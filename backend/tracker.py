"""Live military aircraft tracker & conflict event endpoints.

Data sources:
- adsb.lol /v2/mil — free, unfiltered military ADS-B data
- OpenSky Network — free ADS-B data (supplements adsb.lol)
- GDELT Project — free, real-time conflict event data (no auth needed)
- OpenStreetMap / Overpass — military airbase locations (static layer)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# --- In-memory cache ---

_cache: dict[str, tuple[float, object]] = {}
CACHE_TTL_AIRCRAFT = 12
CACHE_TTL_STRIKES = 3600


def _get_cached(key: str, ttl: float) -> object | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < ttl:
            return data
    return None


def _set_cache(key: str, data: object) -> None:
    _cache[key] = (time.time(), data)


# --- Response models ---


class AircraftPosition(BaseModel):
    hex: str | None = None
    flight: str | None = None
    registration: str | None = None
    aircraft_type: str | None = None
    description: str | None = None
    country_code: str | None = None  # ISO 3166-1 alpha-2 (derived from ICAO hex)
    lat: float | None = None
    lon: float | None = None
    alt_baro: float | int | str | None = None
    alt_geom: float | int | None = None
    ground_speed: float | None = None
    track: float | None = None
    squawk: str | None = None
    category: str | None = None
    nav_heading: float | None = None
    seen: float | None = None
    rssi: float | None = None
    emergency: str | None = None
    db_flags: int | None = None


class AircraftResponse(BaseModel):
    aircraft: list[AircraftPosition]
    total: int
    cached: bool = False


class StrikeEvent(BaseModel):
    event_id: str | None = None
    event_date: str | None = None
    event_type: str | None = None
    sub_event_type: str | None = None
    actor1: str | None = None
    actor2: str | None = None
    country: str | None = None
    admin1: str | None = None
    admin2: str | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    fatalities: int | None = None
    notes: str | None = None
    source: str | None = None


class StrikesResponse(BaseModel):
    events: list[StrikeEvent]
    total: int
    cached: bool = False
    hint: str | None = None


# --- Bounding box ---

ME_LAT_MIN = 12.0
ME_LAT_MAX = 42.0
ME_LON_MIN = 25.0
ME_LON_MAX = 63.0


def _in_bounds(lat: float | None, lon: float | None, lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> bool:
    if lat is None or lon is None:
        return False
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


# ICAO hex range → ISO 3166-1 alpha-2 country code
# Comprehensive table covering most military and civil allocations
_ICAO_COUNTRY_RANGES: list[tuple[int, int, str]] = [
    (0x000000, 0x003FFF, "ZZ"),  # ICAO special
    (0x004000, 0x0043FF, "ZW"),  # Zimbabwe
    (0x006000, 0x006FFF, "MZ"),  # Mozambique
    (0x008000, 0x00FFFF, "ZA"),  # South Africa
    (0x010000, 0x017FFF, "EG"),  # Egypt
    (0x018000, 0x01FFFF, "LY"),  # Libya
    (0x020000, 0x027FFF, "MA"),  # Morocco
    (0x028000, 0x02FFFF, "TN"),  # Tunisia
    (0x030000, 0x0303FF, "BW"),  # Botswana
    (0x032000, 0x032FFF, "BI"),  # Burundi
    (0x034000, 0x034FFF, "CM"),  # Cameroon
    (0x038000, 0x038FFF, "CD"),  # Congo DR
    (0x03E000, 0x03EFFF, "ET"),  # Ethiopia
    (0x040000, 0x040FFF, "GQ"),  # Equatorial Guinea
    (0x042000, 0x042FFF, "GH"),  # Ghana
    (0x044000, 0x044FFF, "GN"),  # Guinea
    (0x048000, 0x048FFF, "KE"),  # Kenya
    (0x050000, 0x050FFF, "NG"),  # Nigeria
    (0x054000, 0x054FFF, "SN"),  # Senegal
    (0x060000, 0x060FFF, "TZ"),  # Tanzania
    (0x068000, 0x068FFF, "UG"),  # Uganda
    (0x070000, 0x070FFF, "SD"),  # Sudan
    (0x0C0000, 0x0C3FFF, "CA"),  # Canada (partial)
    (0x0D0000, 0x0D7FFF, "MX"),  # Mexico
    (0x100000, 0x1FFFFF, "RU"),  # Russia
    (0x200000, 0x27FFFF, "JP"),  # Japan
    (0x300000, 0x33FFFF, "IT"),  # Italy
    (0x340000, 0x37FFFF, "ES"),  # Spain
    (0x380000, 0x3BFFFF, "FR"),  # France
    (0x3C0000, 0x3FFFFF, "DE"),  # Germany
    (0x400000, 0x43FFFF, "GB"),  # United Kingdom
    (0x440000, 0x447FFF, "AT"),  # Austria
    (0x448000, 0x44FFFF, "BE"),  # Belgium
    (0x450000, 0x457FFF, "BG"),  # Bulgaria
    (0x458000, 0x45FFFF, "DK"),  # Denmark
    (0x460000, 0x467FFF, "FI"),  # Finland
    (0x468000, 0x46FFFF, "NL"),  # Netherlands
    (0x470000, 0x477FFF, "GR"),  # Greece
    (0x478000, 0x47FFFF, "HU"),  # Hungary
    (0x480000, 0x487FFF, "NO"),  # Norway
    (0x488000, 0x48FFFF, "PL"),  # Poland
    (0x490000, 0x497FFF, "PT"),  # Portugal
    (0x498000, 0x49FFFF, "CZ"),  # Czech Republic
    (0x4A0000, 0x4A7FFF, "RO"),  # Romania
    (0x4A8000, 0x4AFFFF, "SE"),  # Sweden
    (0x4B0000, 0x4B7FFF, "CH"),  # Switzerland
    (0x4B8000, 0x4BFFFF, "TR"),  # Turkey
    (0x4C0000, 0x4C7FFF, "RS"),  # Serbia
    (0x500000, 0x507FFF, "IL"),  # Israel
    (0x508000, 0x50FFFF, "JO"),  # Jordan
    (0x510000, 0x517FFF, "LB"),  # Lebanon
    (0x518000, 0x51FFFF, "SY"),  # Syria
    (0x600000, 0x6003FF, "AF"),  # Afghanistan
    (0x608000, 0x60FFFF, "BD"),  # Bangladesh
    (0x680000, 0x6FFFFF, "CN"),  # China
    (0x700000, 0x70FFFF, "SA"),  # Saudi Arabia
    (0x710000, 0x717FFF, "AU"),  # Australia
    (0x718000, 0x71FFFF, "ID"),  # Indonesia
    (0x720000, 0x727FFF, "IR"),  # Iran
    (0x728000, 0x72FFFF, "IQ"),  # Iraq
    (0x730000, 0x737FFF, "KR"),  # South Korea
    (0x738000, 0x73FFFF, "TR"),  # Turkey (extended)
    (0x740000, 0x747FFF, "JO"),  # Jordan (main civil block, RJA = Royal Jordanian)
    (0x748000, 0x74FFFF, "KW"),  # Kuwait
    (0x750000, 0x757FFF, "MY"),  # Malaysia
    (0x758000, 0x75FFFF, "NP"),  # Nepal
    (0x760000, 0x767FFF, "NZ"),  # New Zealand
    (0x768000, 0x76FFFF, "PK"),  # Pakistan
    (0x770000, 0x777FFF, "PH"),  # Philippines
    (0x778000, 0x77FFFF, "SG"),  # Singapore
    (0x780000, 0x787FFF, "LK"),  # Sri Lanka
    (0x788000, 0x78FFFF, "TW"),  # Taiwan
    (0x790000, 0x797FFF, "TH"),  # Thailand
    (0x798000, 0x79FFFF, "VN"),  # Vietnam
    (0x7C0000, 0x7FFFFF, "AU"),  # Australia (extended civil)
    (0x800000, 0x83FFFF, "IN"),  # India
    (0x840000, 0x87FFFF, "BR"),  # Brazil
    (0x880000, 0x887FFF, "AE"),  # United Arab Emirates
    (0x888000, 0x88FFFF, "BH"),  # Bahrain
    (0x890000, 0x893FFF, "QA"),  # Qatar
    (0x894000, 0x897FFF, "PK"),  # Pakistan (military)
    (0x898000, 0x8FFFFF, "OM"),  # Oman
    (0x900000, 0x9FFFFF, "AR"),  # Argentina
    (0xA00000, 0xAFFFFF, "US"),  # United States
    (0xC00000, 0xC3FFFF, "CA"),  # Canada
    (0xC80000, 0xCBFFFF, "KP"),  # North Korea
    (0xE00000, 0xE3FFFF, "AR"),  # Argentina (extended)
    (0xE40000, 0xE7FFFF, "BR"),  # Brazil (extended)
]


def _icao_hex_to_country(hex_code: str | None) -> str | None:
    """Convert ICAO hex code to ISO 3166-1 alpha-2 country code."""
    if not hex_code or len(hex_code) < 2:
        return None
    try:
        val = int(hex_code, 16)
    except ValueError:
        return None
    for lo, hi, cc in _ICAO_COUNTRY_RANGES:
        if lo <= val <= hi:
            return cc
    return None


def _parse_aircraft(ac: dict) -> AircraftPosition:
    hex_code = ac.get("hex")
    return AircraftPosition(
        hex=hex_code,
        flight=(ac.get("flight") or "").strip() or None,
        registration=ac.get("r"),
        aircraft_type=ac.get("t"),
        description=ac.get("desc"),
        country_code=_icao_hex_to_country(hex_code),
        lat=ac.get("lat"),
        lon=ac.get("lon"),
        alt_baro=ac.get("alt_baro"),
        alt_geom=ac.get("alt_geom"),
        ground_speed=ac.get("gs"),
        track=ac.get("track"),
        squawk=ac.get("squawk"),
        category=ac.get("category"),
        nav_heading=ac.get("nav_heading"),
        seen=ac.get("seen"),
        rssi=ac.get("rssi"),
        emergency=ac.get("emergency"),
        db_flags=ac.get("dbFlags"),
    )


# --- Health ---


@router.get("/health")
async def health():
    os_configured = bool(os.getenv("OPENSKY_CLIENT_ID") and os.getenv("OPENSKY_CLIENT_SECRET"))
    return {
        "status": "ok",
        "service": "miltrack",
        "opensky_oauth2": os_configured,
        "gdelt_available": True,
    }


# --- Aircraft trail storage (in-memory, last 60 minutes) ---

TRAIL_MAX_AGE = 3600  # seconds
_trails: dict[str, list[tuple[float, float, float, float | None]]] = {}  # hex -> [(ts, lat, lon, alt), ...]


def _record_trails(aircraft: list[AircraftPosition]):
    """Append current positions to trail history."""
    now = time.time()
    cutoff = now - TRAIL_MAX_AGE
    for ac in aircraft:
        if not ac.hex or ac.lat is None or ac.lon is None:
            continue
        alt = ac.alt_baro if isinstance(ac.alt_baro, (int, float)) else None
        trail = _trails.setdefault(ac.hex, [])
        # Deduplicate: skip if same position as last point
        if trail and abs(trail[-1][1] - ac.lat) < 0.0001 and abs(trail[-1][2] - ac.lon) < 0.0001:
            continue
        trail.append((now, ac.lat, ac.lon, alt))

    # Prune old points and stale aircraft
    stale_keys = []
    for hex_code, trail in _trails.items():
        _trails[hex_code] = [p for p in trail if p[0] > cutoff]
        if not _trails[hex_code]:
            stale_keys.append(hex_code)
    for k in stale_keys:
        del _trails[k]


class TrailPoint(BaseModel):
    ts: float
    lat: float
    lon: float
    alt: float | None = None


class TrailResponse(BaseModel):
    hex: str
    points: list[TrailPoint]
    total: int


# --- Aircraft endpoints ---


@router.get("/aircraft", response_model=AircraftResponse)
async def get_live_military_aircraft(
    lat_min: float = Query(ME_LAT_MIN),
    lat_max: float = Query(ME_LAT_MAX),
    lon_min: float = Query(ME_LON_MIN),
    lon_max: float = Query(ME_LON_MAX),
    global_view: bool = Query(False, description="If true, return all aircraft worldwide"),
):
    """Fetch live military aircraft from multiple sources (adsb.lol + OpenSky)."""
    cache_key = "mil_aircraft"
    cached = _get_cached(cache_key, CACHE_TTL_AIRCRAFT)
    if cached is not None:
        all_ac = cached
        from_cache = True
    else:
        all_ac = await _fetch_merged_military_aircraft()
        _set_cache(cache_key, all_ac)
        _record_trails(all_ac)
        from_cache = False

    if global_view:
        return AircraftResponse(aircraft=all_ac, total=len(all_ac), cached=from_cache)

    filtered = [ac for ac in all_ac if _in_bounds(ac.lat, ac.lon, lat_min, lat_max, lon_min, lon_max)]
    return AircraftResponse(aircraft=filtered, total=len(filtered), cached=from_cache)


@router.get("/aircraft/trail/{hex_code}", response_model=TrailResponse)
async def get_aircraft_trail(hex_code: str):
    """Return flight trail: tries OpenSky full track (since takeoff), falls back to in-memory."""
    code = hex_code.lower()

    # Try OpenSky full track first (provides path since takeoff)
    opensky_points = await _fetch_opensky_track(code)
    if opensky_points:
        return TrailResponse(hex=code, points=opensky_points, total=len(opensky_points))

    # Fallback to in-memory trail
    trail = _trails.get(code, [])
    points = [TrailPoint(ts=p[0], lat=p[1], lon=p[2], alt=p[3]) for p in trail]
    return TrailResponse(hex=code, points=points, total=len(points))


async def _fetch_opensky_track(icao24: str) -> list[TrailPoint]:
    """Fetch full flight track from OpenSky Network (path since last takeoff)."""
    cache_key = f"opensky_track:{icao24}"
    cached = _get_cached(cache_key, 60)  # cache for 60s to avoid rate limits
    if cached is not None:
        return cached

    url = "https://opensky-network.org/api/tracks/all"
    params = {"icao24": icao24, "time": 0}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                logger.debug("OpenSky track rate-limited for %s", icao24)
                return []
            if resp.status_code != 200:
                return []
            data = resp.json()

        path = data.get("path", [])
        if not path:
            return []

        # path entries: [time, lat, lon, baro_altitude, true_track, on_ground]
        points = []
        for wp in path:
            if len(wp) < 4 or wp[1] is None or wp[2] is None:
                continue
            alt_m = wp[3]
            alt_ft = round(alt_m * 3.28084) if alt_m is not None else None
            points.append(TrailPoint(ts=float(wp[0]), lat=wp[1], lon=wp[2], alt=alt_ft))

        _set_cache(cache_key, points)
        return points
    except Exception as e:
        logger.debug("OpenSky track fetch failed for %s: %s", icao24, e)
        return []


CACHE_TTL_OPENSKY = 90  # cache OpenSky separately (4000 credits/day budget)

async def _fetch_merged_military_aircraft() -> list[AircraftPosition]:
    """Fetch from adsb.lol and OpenSky in parallel, merge by ICAO hex (freshest wins)."""
    # Use separate cache for OpenSky to reduce API calls
    opensky_cache_key = "opensky_mil_states"
    cached_opensky = _get_cached(opensky_cache_key, CACHE_TTL_OPENSKY)

    adsblo_result = await _fetch_adsblol()

    if cached_opensky is not None:
        opensky_result = cached_opensky
    else:
        opensky_result = await _fetch_opensky_mil()
        if isinstance(opensky_result, list) and opensky_result:
            _set_cache(opensky_cache_key, opensky_result)

    merged: dict[str, AircraftPosition] = {}

    # adsb.lol is primary
    if isinstance(adsblo_result, list):
        for ac in adsblo_result:
            if ac.hex:
                merged[ac.hex] = ac
        logger.info("adsb.lol: %d aircraft", len(adsblo_result))
    else:
        logger.error("adsb.lol failed: %s", adsblo_result)

    # OpenSky supplements — only add aircraft not already seen
    if isinstance(opensky_result, list):
        new_count = 0
        for ac in opensky_result:
            if ac.hex and ac.hex not in merged:
                merged[ac.hex] = ac
                new_count += 1
        logger.info("OpenSky: %d aircraft (%d new after merge)", len(opensky_result), new_count)
    else:
        logger.error("OpenSky failed: %s", opensky_result)

    result = list(merged.values())
    _record_trails(result)
    return result


async def _fetch_adsblol() -> list[AircraftPosition]:
    """Fetch military aircraft from adsb.lol."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://api.adsb.lol/v2/mil")
            resp.raise_for_status()
            data = resp.json()
        ac_list = data.get("ac", [])
        return [_parse_aircraft(ac) for ac in ac_list if ac.get("lat") and ac.get("lon")]
    except Exception as e:
        logger.error("adsb.lol fetch failed: %s", e)
        return []


# Genuine military ICAO24 hex ranges per country
# Source: https://www.ads-b.nl/overview.php (ICAO allocation table)
_MIL_HEX_RANGES: list[tuple[int, int]] = [
    (0xADF7C0, 0xAFFFFF),  # USA military
    (0x3F0000, 0x3FFFFF),  # Germany military
    (0x43C000, 0x43CFFF),  # UK military
    (0x3A8000, 0x3AFFFF),  # France military
    (0x33FF00, 0x33FFFF),  # Italy military
    (0x350000, 0x37FFFF),  # Spain military (partial)
    (0x468000, 0x468FFF),  # Netherlands military
    (0x710000, 0x71FFFF),  # Australia military
    (0xC20000, 0xC3FFFF),  # Canada military
    (0x7C0000, 0x7FFFFF),  # Australia (some military)
    (0x800000, 0x83FFFF),  # India military
    (0x200000, 0x20FFFF),  # Japan military (JASDF)
    (0x500000, 0x507FFF),  # Israel (all aviation, small country)
    (0x894000, 0x894FFF),  # Pakistan military
    (0x700000, 0x700FFF),  # Saudi Arabia (partial mil)
    (0x738000, 0x738FFF),  # Turkey military
]


def _is_opensky_military(icao24: str) -> bool:
    """Check if an ICAO24 hex falls in a known military allocation block."""
    if not icao24 or len(icao24) < 4:
        return False
    try:
        val = int(icao24, 16)
    except ValueError:
        return False
    return any(lo <= val <= hi for lo, hi in _MIL_HEX_RANGES)


# --- OpenSky OAuth2 token management ---

_opensky_token: str | None = None
_opensky_token_expires: float = 0.0
_opensky_backoff_until: float = 0.0
_opensky_consecutive_429: int = 0


async def _get_opensky_token() -> str | None:
    """Obtain an OAuth2 bearer token using client credentials flow."""
    global _opensky_token, _opensky_token_expires

    if _opensky_token and time.time() < _opensky_token_expires - 60:
        return _opensky_token

    client_id = os.getenv("OPENSKY_CLIENT_ID", "")
    client_secret = os.getenv("OPENSKY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None

    token_url = (
        "https://auth.opensky-network.org/auth/realms/opensky-network"
        "/protocol/openid-connect/token"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
        _opensky_token = data["access_token"]
        _opensky_token_expires = time.time() + data.get("expires_in", 1800)
        logger.info("OpenSky OAuth2 token acquired (expires in %ds)", data.get("expires_in", 0))
        return _opensky_token
    except Exception as e:
        logger.error("OpenSky OAuth2 token request failed: %s", e)
        return None


async def _fetch_opensky_mil() -> list[AircraftPosition]:
    """Fetch state vectors from OpenSky Network and filter to likely military aircraft."""
    global _opensky_backoff_until, _opensky_consecutive_429
    if time.time() < _opensky_backoff_until:
        return []

    # Build auth header — prefer OAuth2 token, fall back to basic auth for legacy
    headers: dict[str, str] = {}
    token = await _get_opensky_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        os_user = os.getenv("OPENSKY_USERNAME", "")
        os_pass = os.getenv("OPENSKY_PASSWORD", "")
        if not os_user:
            return []

    auth = None
    if not token:
        os_user = os.getenv("OPENSKY_USERNAME", "")
        os_pass = os.getenv("OPENSKY_PASSWORD", "")
        if os_user and os_pass:
            auth = (os_user, os_pass)

    try:
        async with httpx.AsyncClient(timeout=20.0, auth=auth) as client:
            resp = await client.get(
                "https://opensky-network.org/api/states/all",
                headers=headers,
            )
            if resp.status_code == 429:
                remaining = resp.headers.get("X-Rate-Limit-Remaining", "?")
                retry_after = resp.headers.get("X-Rate-Limit-Retry-After-Seconds", "?")
                _opensky_consecutive_429 += 1
                backoff = min(60 * (2 ** _opensky_consecutive_429), 3600)
                _opensky_backoff_until = time.time() + backoff
                logger.warning(
                    "OpenSky 429 — remaining=%s, retry_after=%ss, backoff=%ds",
                    remaining, retry_after, backoff,
                )
                return []
            if resp.status_code == 401:
                logger.error("OpenSky 401 Unauthorized — check OPENSKY_CLIENT_ID/SECRET")
                return []
            _opensky_consecutive_429 = 0
            resp.raise_for_status()
            data = resp.json()

        states = data.get("states", [])
        if not states:
            return []

        result = []
        for s in states:
            icao24 = s[0] if s[0] else ""
            if not _is_opensky_military(icao24):
                continue
            lat = s[6]
            lon = s[5]
            if lat is None or lon is None:
                continue
            result.append(AircraftPosition(
                hex=icao24.lower(),
                flight=(s[1] or "").strip() or None,
                registration=None,
                aircraft_type=None,
                description=None,
                country_code=_icao_hex_to_country(icao24),
                lat=lat,
                lon=lon,
                alt_baro=s[7],       # baro_altitude (meters → keep as meters for now)
                alt_geom=s[13],      # geo_altitude
                ground_speed=_ms_to_knots(s[9]),  # velocity m/s → knots
                track=s[10],         # true_track degrees
                squawk=s[14],        # squawk
                category=None,
                nav_heading=None,
                seen=None,
                rssi=None,
                emergency=None,
                db_flags=None,
            ))
        return result
    except Exception as e:
        logger.error("OpenSky fetch failed: %s", e)
        return []


def _ms_to_knots(ms: float | None) -> float | None:
    if ms is None:
        return None
    return round(ms * 1.94384, 1)


# --- Strike / conflict event endpoints (GDELT Event Files) ---

import csv
import io
import zipfile

# CAMEO root codes for violent events
_CAMEO_VIOLENT = {"18", "19", "20"}  # 18=Assault, 19=Fight, 20=Use unconventional violence/force

# Middle East country FIPS codes (used in GDELT Actor1/2CountryCode)
_ME_FIPS = {"IR", "IZ", "SY", "IS", "LE", "YM", "GZ", "WE", "JO", "TU", "SA", "EG", "MU", "AE", "QA", "KU", "BA"}

# GDELT v2 export TSV column indices (61 columns total)
_COL_GLOBALEVENTID = 0
_COL_DAY = 1
_COL_ACTOR1NAME = 6      # Actor1Name (5=Actor1Code)
_COL_ACTOR1COUNTRY = 7   # Actor1CountryCode
_COL_ACTOR2NAME = 16     # Actor2Name (15=Actor2Code)
_COL_ACTOR2COUNTRY = 17  # Actor2CountryCode
_COL_EVENTROOTCODE = 28  # EventRootCode (26=EventCode, 27=EventBaseCode)
_COL_EVENTCODE = 26      # EventCode
_COL_GOLDSTEIN = 30
_COL_NUMMENTIONS = 31
_COL_NUMSOURCES = 32
_COL_AVGTONE = 34
_COL_ACTOR1GEO_LAT = 40
_COL_ACTOR1GEO_LONG = 41
_COL_ACTOR2GEO_LAT = 48
_COL_ACTOR2GEO_LONG = 49
_COL_ACTIONGEO_FULLNAME = 52
_COL_ACTIONGEO_COUNTRYCODE = 53
_COL_ACTIONGEO_LAT = 56
_COL_ACTIONGEO_LONG = 57
_COL_SOURCEURL = 60


@router.get("/strikes", response_model=StrikesResponse)
async def get_strike_events(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(500, ge=1, le=2000),
):
    """Fetch conflict events from GDELT Event files (free, no auth required)."""
    cache_key = f"gdelt_events:{days}"
    cached = _get_cached(cache_key, CACHE_TTL_STRIKES)
    if cached is not None:
        return StrikesResponse(events=cached, total=len(cached), cached=True)

    events = await _fetch_gdelt_event_files(days, limit)
    _set_cache(cache_key, events)
    return StrikesResponse(events=events, total=len(events), cached=False)


async def _fetch_gdelt_event_files(days: int, limit: int) -> list[StrikeEvent]:
    """Download recent GDELT v2 event export files and filter for ME military events."""
    # Get list of recent update files
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get("http://data.gdeltproject.org/gdeltv2/lastupdate.txt")
            resp.raise_for_status()
            lines = resp.text.strip().split("\n")
    except Exception as e:
        logger.error("Failed to get GDELT file list: %s", e)
        return []

    # Extract the export CSV URL (first line)
    export_urls: list[str] = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 3 and "export.CSV" in parts[2]:
            export_urls.append(parts[2])

    if not export_urls:
        logger.warning("No GDELT export URLs found")
        return []

    # For more history, also fetch the master file list for the past N days
    if days > 1:
        extra_urls = await _get_gdelt_historical_urls(days)
        export_urls = extra_urls + export_urls

    events: list[StrikeEvent] = []
    seen_ids: set[str] = set()

    for url in export_urls[:min(len(export_urls), days * 4)]:  # ~4 files per day is enough
        try:
            batch = await _download_parse_gdelt_export(url, seen_ids)
            events.extend(batch)
            if len(events) >= limit:
                break
        except Exception as e:
            logger.error("Failed to process GDELT file %s: %s", url, e)

    events = events[:limit]
    logger.info("Fetched %d GDELT conflict events from %d files", len(events), len(export_urls))
    return events


async def _get_gdelt_historical_urls(days: int) -> list[str]:
    """Get GDELT export file URLs for past N days via masterfilelist-translation."""
    urls: list[str] = []
    now = datetime.datetime.now(datetime.timezone.utc)

    # Sample a few timestamps per day (every 6 hours) to get representative files
    for d in range(1, min(days + 1, 15)):
        for h in [0, 6, 12, 18]:
            ts = now - datetime.timedelta(days=d, hours=-h)
            # Round to 15-minute GDELT interval
            minute = (ts.minute // 15) * 15
            stamp = ts.strftime(f"%Y%m%d%H{minute:02d}00")
            url = f"http://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"
            urls.append(url)

    return urls


async def _download_parse_gdelt_export(url: str, seen_ids: set[str]) -> list[StrikeEvent]:
    """Download and parse a single GDELT v2 export ZIP file."""
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t")
                events: list[StrikeEvent] = []

                for row in reader:
                    if len(row) < 61:
                        continue

                    root_code = row[_COL_EVENTROOTCODE]
                    if root_code not in _CAMEO_VIOLENT:
                        continue

                    country_code = row[_COL_ACTIONGEO_COUNTRYCODE]
                    actor1_cc = row[_COL_ACTOR1COUNTRY]
                    actor2_cc = row[_COL_ACTOR2COUNTRY]

                    if not (country_code in _ME_FIPS or actor1_cc in _ME_FIPS or actor2_cc in _ME_FIPS):
                        continue

                    lat = _safe_float(row[_COL_ACTIONGEO_LAT])
                    lon = _safe_float(row[_COL_ACTIONGEO_LONG])
                    if lat is None or lon is None:
                        continue

                    event_id = row[_COL_GLOBALEVENTID]
                    if event_id in seen_ids:
                        continue
                    seen_ids.add(event_id)

                    day_str = row[_COL_DAY]
                    event_date = f"{day_str[:4]}-{day_str[4:6]}-{day_str[6:8]}" if len(day_str) >= 8 else None

                    event_code = row[_COL_EVENTCODE]
                    event_type = {
                        "18": "Assault", "181": "Abduction", "182": "Physical assault",
                        "183": "Armed assault", "19": "Fight", "190": "Use military force",
                        "191": "Impose blockade", "192": "Occupy territory",
                        "193": "Fight with small arms", "194": "Fight with artillery",
                        "195": "Use aerial weapons", "196": "Violate ceasefire",
                        "20": "Use unconventional force", "201": "Engage in mass expulsion",
                        "202": "Engage in ethnic cleansing", "203": "Use WMD",
                        "204": "Use conventional military force",
                    }.get(event_code, f"CAMEO {event_code}")

                    location = row[_COL_ACTIONGEO_FULLNAME] or None
                    source_url = row[_COL_SOURCEURL] or None

                    goldstein = _safe_float(row[_COL_GOLDSTEIN])
                    mentions = _safe_int(row[_COL_NUMMENTIONS])

                    events.append(StrikeEvent(
                        event_id=event_id,
                        event_date=event_date,
                        event_type=event_type,
                        sub_event_type=None,
                        actor1=row[_COL_ACTOR1NAME] if len(row) > _COL_ACTOR1NAME else None,
                        actor2=row[_COL_ACTOR2NAME] if len(row) > _COL_ACTOR2NAME else None,
                        country=location.split(",")[-1].strip() if location and "," in location else None,
                        admin1=None,
                        admin2=None,
                        location=location,
                        latitude=lat,
                        longitude=lon,
                        fatalities=None,
                        notes=f"Goldstein: {goldstein}, Mentions: {mentions}" if goldstein else None,
                        source=source_url,
                    ))

                return events
    except Exception as e:
        logger.error("Failed to parse GDELT export %s: %s", url, e)
        return []


# --- Military Airbase static layer (OpenStreetMap) ---


class MilitaryBase(BaseModel):
    id: int
    name: str | None = None
    lat: float
    lon: float
    base_type: str = "airbase"  # airbase | naval_base | base
    operator: str | None = None
    country: str | None = None


class BasesResponse(BaseModel):
    bases: list[MilitaryBase]
    total: int
    cached: bool = False


CACHE_TTL_BASES = 86400  # 24 hours — static data


@router.get("/bases", response_model=BasesResponse)
async def get_military_bases(
    lat_min: float = Query(ME_LAT_MIN - 5),
    lat_max: float = Query(ME_LAT_MAX + 5),
    lon_min: float = Query(ME_LON_MIN - 10),
    lon_max: float = Query(ME_LON_MAX + 5),
    global_view: bool = Query(False),
):
    """Fetch military airbase locations from OpenStreetMap via Overpass API."""
    cache_key = "mil_bases" if global_view else f"mil_bases:{lat_min}:{lat_max}:{lon_min}:{lon_max}"
    cached = _get_cached(cache_key, CACHE_TTL_BASES)
    if cached is not None:
        return BasesResponse(bases=cached, total=len(cached), cached=True)

    bases = await _fetch_overpass_bases(lat_min, lat_max, lon_min, lon_max, global_view)
    _set_cache(cache_key, bases)
    return BasesResponse(bases=bases, total=len(bases), cached=False)


async def _fetch_overpass_bases(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float,
    global_view: bool,
) -> list[MilitaryBase]:
    """Query Overpass API for military=airfield and aeroway=aerodrome + military nodes."""
    bbox = "" if global_view else f"({lat_min},{lon_min},{lat_max},{lon_max})"

    # Find military airfields/bases — using [out:json] for structured data
    query = f"""
[out:json][timeout:60];
(
  node["military"="airfield"]{bbox};
  way["military"="airfield"]{bbox};
  node["aeroway"="aerodrome"]["military"]{bbox};
  way["aeroway"="aerodrome"]["military"]{bbox};
  node["military"="naval_base"]{bbox};
  way["military"="naval_base"]{bbox};
  node["military"="base"]{bbox};
  way["military"="base"]{bbox};
);
out center;
"""

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query},
            )
            resp.raise_for_status()
            data = resp.json()

        elements = data.get("elements", [])
        bases: list[MilitaryBase] = []
        seen_names: set[str] = set()

        for el in elements:
            tags = el.get("tags", {})
            lat = el.get("lat") or el.get("center", {}).get("lat")
            lon = el.get("lon") or el.get("center", {}).get("lon")
            if not lat or not lon:
                continue

            name = tags.get("name") or tags.get("name:en") or tags.get("official_name")
            mil_type = tags.get("military", "")
            base_type = "airbase"
            if mil_type == "naval_base":
                base_type = "naval_base"
            elif mil_type == "base":
                base_type = "base"

            dedup_key = f"{name or ''}-{lat:.2f}-{lon:.2f}"
            if dedup_key in seen_names:
                continue
            seen_names.add(dedup_key)

            bases.append(MilitaryBase(
                id=el.get("id", 0),
                name=name,
                lat=lat,
                lon=lon,
                base_type=base_type,
                operator=tags.get("operator") or tags.get("operator:en"),
                country=None,
            ))

        logger.info("Fetched %d military bases from Overpass", len(bases))
        return bases
    except Exception as e:
        logger.error("Failed to fetch military bases: %s", e)
        return []


# --- Military news feed (RSS) ---


class NewsItem(BaseModel):
    title: str
    link: str | None = None
    published: str | None = None
    source: str | None = None
    summary: str | None = None


class NewsResponse(BaseModel):
    items: list[NewsItem]
    total: int
    cached: bool = False


CACHE_TTL_NEWS = 900  # 15 minutes

_RSS_FEEDS: list[tuple[str, str]] = [
    ("https://www.jpost.com/rss/rssfeedsmilitaryanddefense", "Jerusalem Post"),
    ("https://www.timesofisrael.com/feed/", "Times of Israel"),
    ("https://feeds.feedburner.com/defense-news/home", "Defense News"),
    ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml", "NY Times ME"),
    ("https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "BBC Middle East"),
]

_IRAN_KEYWORDS = {
    "iran", "tehran", "isfahan", "irgc", "quds", "khamenei",
    "israel", "idf", "netanyahu", "tel aviv", "gaza", "hezbollah",
    "strike", "airstrike", "bombing", "missile", "military",
    "conflict", "war", "attack", "defense", "air force",
    "fighter", "jet", "drone", "uav", "tanker", "refueling",
    "carrier", "deployment", "operation", "centcom",
    "f-35", "f-15", "b-2", "b-52", "kc-135", "kc-46",
    "strait of hormuz", "persian gulf", "red sea", "houthi", "yemen",
}


@router.get("/news", response_model=NewsResponse)
async def get_military_news(limit: int = Query(50, ge=1, le=200)):
    """Fetch latest military/Iran conflict news from RSS feeds."""
    cache_key = "mil_news"
    cached = _get_cached(cache_key, CACHE_TTL_NEWS)
    if cached is not None:
        return NewsResponse(items=cached[:limit], total=len(cached), cached=True)

    items = await _fetch_rss_news()
    _set_cache(cache_key, items)
    return NewsResponse(items=items[:limit], total=len(items), cached=False)


async def _fetch_rss_news() -> list[NewsItem]:
    """Fetch and parse RSS feeds, filter for Iran/military conflict relevance."""
    import re
    import xml.etree.ElementTree as ET

    all_items: list[NewsItem] = []

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for feed_url, source_name in _RSS_FEEDS:
            try:
                resp = await client.get(feed_url, headers={"User-Agent": "MilTrack/1.0"})
                if resp.status_code != 200:
                    continue

                # Parse XML from bytes to handle encoding properly
                root = ET.fromstring(resp.content)

                # Handle both RSS 2.0 (<item>) and Atom (<entry>) formats
                items = root.findall(".//item")
                if not items:
                    atom_ns = "{http://www.w3.org/2005/Atom}"
                    items = root.findall(f".//{atom_ns}entry")

                feed_count = 0
                for item in items:
                    # Extract title — try multiple approaches for CDATA handling
                    title = ""
                    for tag in ["title", "{http://www.w3.org/2005/Atom}title"]:
                        el = item.find(tag)
                        if el is not None:
                            title = "".join(el.itertext()).strip()
                            if title:
                                break

                    if not title:
                        continue

                    # Extract link
                    link = ""
                    link_el = item.find("link")
                    if link_el is not None:
                        link = (link_el.text or "").strip() or link_el.get("href", "")
                    if not link:
                        atom_link = item.find("{http://www.w3.org/2005/Atom}link")
                        if atom_link is not None:
                            link = atom_link.get("href", "")

                    # Extract published date
                    published = None
                    for tag in ["pubDate", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"]:
                        el = item.find(tag)
                        if el is not None and el.text:
                            published = el.text.strip()
                            break

                    # Extract summary/description
                    summary = ""
                    for tag in ["description", "{http://www.w3.org/2005/Atom}summary"]:
                        el = item.find(tag)
                        if el is not None:
                            raw = "".join(el.itertext()).strip()
                            if raw:
                                summary = re.sub(r"<[^>]+>", "", raw)[:300]
                                break

                    # Relevance filter
                    text = (title + " " + summary).lower()
                    if not any(kw in text for kw in _IRAN_KEYWORDS):
                        continue

                    all_items.append(NewsItem(
                        title=title,
                        link=link,
                        published=published,
                        source=source_name,
                        summary=summary[:200] if summary else None,
                    ))
                    feed_count += 1

                logger.info("RSS %s: %d items (%d relevant)", source_name, len(items), feed_count)

            except Exception as e:
                logger.error("RSS feed %s failed: %s", source_name, e)

    all_items.sort(key=lambda x: x.published or "", reverse=True)
    logger.info("Fetched %d relevant news items from RSS total", len(all_items))
    return all_items


# --- Aircraft info (Wikipedia) ---

# ICAO type code → Wikipedia article title
_WIKI_MAP: dict[str, str] = {
    "A400": "Airbus_A400M_Atlas",
    "B350": "Beechcraft_King_Air",
    "B462": "BAe_146",
    "B752": "Boeing_757",
    "C130": "Lockheed_C-130_Hercules",
    "C160": "Transall_C-160",
    "C17":  "Boeing_C-17_Globemaster_III",
    "C2":   "Kawasaki_C-2_(aircraft)",
    "C30J": "Lockheed_Martin_C-130J_Super_Hercules",
    "C5":   "Lockheed_C-5_Galaxy",
    "C5M":  "Lockheed_C-5_Galaxy",
    "CL60": "Bombardier_Challenger_600_series",
    "E2C":  "Northrop_Grumman_E-2_Hawkeye",
    "E2D":  "Northrop_Grumman_E-2_Hawkeye",
    "E3CF": "Boeing_E-3_Sentry",
    "E3TF": "Boeing_E-3_Sentry",
    "E6B":  "Boeing_E-6_Mercury",
    "E737": "Boeing_737_AEW%26C",
    "E767": "Boeing_E-767",
    "EP3":  "Lockheed_EP-3",
    "F16":  "General_Dynamics_F-16_Fighting_Falcon",
    "F15":  "McDonnell_Douglas_F-15_Eagle",
    "F18":  "McDonnell_Douglas_F/A-18_Hornet",
    "F35":  "Lockheed_Martin_F-35_Lightning_II",
    "GLF5": "Gulfstream_V",
    "GLF6": "Gulfstream_G650",
    "GLEX": "Bombardier_Global_Express",
    "H47":  "Boeing_CH-47_Chinook",
    "H60":  "Sikorsky_UH-60_Black_Hawk",
    "K35R": "Boeing_KC-135_Stratotanker",
    "K35T": "Boeing_KC-135_Stratotanker",
    "K35E": "Boeing_KC-135_Stratotanker",
    "KC10": "McDonnell_Douglas_KC-10_Extender",
    "KC2T": "Kawasaki_KC-767",
    "KC30": "Airbus_A330_MRTT",
    "KC46": "Boeing_KC-46_Pegasus",
    "MQ4C": "Northrop_Grumman_MQ-4C_Triton",
    "MQ9":  "General_Atomics_MQ-9_Reaper",
    "MRTT": "Airbus_A330_MRTT",
    "P8":   "Boeing_P-8_Poseidon",
    "PC12": "Pilatus_PC-12",
    "R135": "Boeing_RC-135",
    "RC135":"Boeing_RC-135",
    "RQ4":  "Northrop_Grumman_RQ-4_Global_Hawk",
    "U2":   "Lockheed_U-2",
    "V22":  "Bell_Boeing_V-22_Osprey",
    "A300": "Airbus_A300",
    "A310": "Airbus_A310",
    "A320": "Airbus_A320_family",
    "A330": "Airbus_A330",
    "B737": "Boeing_737",
    "B738": "Boeing_737",
    "B742": "Boeing_747",
    "B744": "Boeing_747",
    "B748": "Boeing_747",
    "B763": "Boeing_767",
    "B764": "Boeing_767",
    "B772": "Boeing_777",
    "B77L": "Boeing_777",
    "C208": "Cessna_208_Caravan",
    "C56X": "Cessna_Citation_V",
    "CRJ7": "Bombardier_CRJ700_series",
    "DHC6": "De_Havilland_Canada_DHC-6_Twin_Otter",
    "E135": "Embraer_ERJ_145_family",
    "E145": "Embraer_ERJ_145_family",
    "LJ35": "Learjet_35",
    "P3":   "Lockheed_P-3_Orion",
}

CACHE_TTL_WIKI = 86400  # 24 hours


class AircraftInfo(BaseModel):
    type_code: str
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    wiki_url: str | None = None


@router.get("/aircraft/info/{type_code}", response_model=AircraftInfo)
async def get_aircraft_info(type_code: str):
    """Look up aircraft type info + image from Wikipedia."""
    code = type_code.upper().replace("-", "")

    cache_key = f"wiki:{code}"
    cached = _get_cached(cache_key, CACHE_TTL_WIKI)
    if cached is not None:
        return cached

    article = _WIKI_MAP.get(code)
    info: AircraftInfo

    if article:
        info = await _fetch_wiki_summary(code, article)
    else:
        info = await _search_wiki_aircraft(code)

    if info.name:
        _set_cache(cache_key, info)
    return info


_WIKI_UA = "MilTrack/1.0 (https://github.com/miltrack; miltrack-app@example.com)"


async def _fetch_wiki_summary(type_code: str, article_title: str) -> AircraftInfo:
    """Fetch summary + thumbnail from Wikipedia REST API."""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{article_title}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _WIKI_UA, "Api-User-Agent": _WIKI_UA})
            if resp.status_code == 404:
                return AircraftInfo(type_code=type_code)
            resp.raise_for_status()
            data = resp.json()

        extract = data.get("extract", "")
        # Take only the first 1-2 sentences
        sentences = extract.split(". ")
        short = ". ".join(sentences[:2]).strip()
        if short and not short.endswith("."):
            short += "."

        return AircraftInfo(
            type_code=type_code,
            name=data.get("title", "").replace("_", " "),
            description=short,
            image_url=data.get("thumbnail", {}).get("source"),
            wiki_url=data.get("content_urls", {}).get("desktop", {}).get("page"),
        )
    except Exception as e:
        logger.error("Wikipedia lookup failed for %s: %s", article_title, e)
        return AircraftInfo(type_code=type_code)


async def _search_wiki_aircraft(type_code: str) -> AircraftInfo:
    """Fall back to Wikipedia search when type code isn't in the mapping."""
    if len(type_code) < 2:
        return AircraftInfo(type_code=type_code)
    query = f"{type_code} military aircraft"
    search_url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": "0",
        "srlimit": "1",
        "format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(search_url, params=params, headers={"User-Agent": _WIKI_UA, "Api-User-Agent": _WIKI_UA})
            resp.raise_for_status()
            data = resp.json()

        results = data.get("query", {}).get("search", [])
        if not results:
            return AircraftInfo(type_code=type_code)

        title = results[0]["title"].replace(" ", "_")
        return await _fetch_wiki_summary(type_code, title)
    except Exception as e:
        logger.error("Wikipedia search failed for %s: %s", type_code, e)
        return AircraftInfo(type_code=type_code)


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
