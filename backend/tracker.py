"""Live military aircraft tracker & conflict event endpoints.

Data sources:
- adsb.lol /v2/mil — free, unfiltered military ADS-B data (primary)
- airplanes.live /v2/mil — free ADS-B data (fallback for adsb.lol)
- OpenSky Network — free ADS-B data (supplements primary source)
- GDELT Project — free, real-time conflict event data (no auth needed)
- ACLED — curated conflict event data (free registration required)
- OpenStreetMap / Overpass — military airbase locations (static layer)
- GDELT DOC 2.0 — real-time global news articles (no auth needed)
- X (Twitter) API v2 — tweets (paid, requires X_API_BEARER_TOKEN)
"""

from __future__ import annotations

import asyncio
import csv
import datetime
import io
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from email.utils import parsedate_to_datetime
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
    # LLM-enriched fields
    title: str | None = None
    summary: str | None = None
    severity: int | None = None       # 1-10
    confidence: float | None = None   # 0.0-1.0 (is this a real military event?)
    attack_direction: str | None = None  # "to_iran" | "from_iran" | "internal" | "other"
    hours_ago: float | None = None    # computed at serve time
    source_url: str | None = None     # link to original news article


class StrikesResponse(BaseModel):
    events: list[StrikeEvent]
    total: int
    cached: bool = False
    enriched: bool = False
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


# Registration prefix → ISO 3166-1 alpha-2 (ICAO Annex 7 nationality marks)
# Check 2-char first, then 1-char. Prefer registration over hex when both available (registration is on the aircraft; hex can miscode).
_REG_PREFIX_TO_COUNTRY: dict[str, str] = {
    # 2-char prefixes (check before 1-char)
    "4X": "IL", "OE": "AT", "OM": "SK", "EP": "IR", "HA": "HU", "EI": "IE", "LX": "LU",
    "HB": "CH", "OO": "BE", "PH": "NL", "LN": "NO", "OH": "FI", "SE": "SE", "OY": "DK", "TC": "TR",
    "SP": "PL", "CS": "PT", "YR": "RO", "RA": "RU", "HZ": "SA", "A6": "AE", "A7": "QA",
    "A9": "BH", "9K": "KW", "4L": "GE", "EX": "KG", "YL": "LV", "ER": "MD", "EW": "BY",
    "UR": "UA", "UK": "UA", "9A": "HR", "S5": "SI", "LZ": "SK", "OK": "CZ", "9H": "MT",
    "9M": "MY", "PK": "ID", "AP": "PK", "VH": "AU", "ZK": "NZ", "JA": "JP", "HL": "KR",
    "VT": "IN", "AP": "PK", "HS": "TH", "VN": "VN", "RP": "PH", "PP": "BR", "PR": "BR",
    "CC": "CL", "HK": "CO", "XA": "MX", "XB": "MX", "XC": "MX", "TI": "CR", "HP": "PA",
    "TG": "GT", "HR": "HN", "YN": "NI", "CP": "BO", "OB": "PE", "CC": "CL", "CX": "UY",
    "ZP": "PY", "PT": "BR", "PS": "BR", "SU": "EG", "7O": "YE", "5A": "LY", "5R": "MG",
    "3B": "MU", "3D": "SZ", "ZS": "ZA", "ZR": "ZW", "5Y": "KE", "5X": "UG", "ET": "ET",
    "ST": "SD", "7T": "DZ", "CN": "MA", "TS": "TN", "3V": "BJ", "5V": "TG", "5U": "NE",
    "TT": "BF", "TU": "CI", "TR": "GA", "TL": "CF", "TY": "BJ", "3X": "GN", "9L": "SL",
    "EL": "LR", "5N": "NG", "9G": "GH", "9U": "BI", "9R": "RW", "5H": "TZ", "5Y": "KE",
}
# 1-char prefixes (used when 2-char not found)
_REG_PREFIX_1CHAR: dict[str, str] = {
    "N": "US", "G": "GB", "F": "FR", "D": "DE", "I": "IT", "E": "ES", "P": "PL",
    "C": "CA", "B": "CN",
}


def _registration_to_country(reg: str | None) -> str | None:
    """Derive country from registration prefix (ICAO Annex 7). Returns ISO 3166-1 alpha-2 or None."""
    if not reg or not isinstance(reg, str):
        return None
    s = reg.strip().upper()
    if not s:
        return None
    # Extract prefix: before hyphen if present, else first 2 then 1 chars
    if "-" in s:
        prefix = s.split("-")[0].strip()
    else:
        prefix = s[:2] if len(s) >= 2 else s[:1]
    if not prefix:
        return None
    # Try 2-char first (for prefixes like 4X, OM)
    if len(prefix) >= 2:
        two = prefix[:2]
        if two in _REG_PREFIX_TO_COUNTRY:
            return _REG_PREFIX_TO_COUNTRY[two]
    # Try 1-char
    one = prefix[0]
    return _REG_PREFIX_1CHAR.get(one)


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


def _resolve_country(registration: str | None, hex_code: str | None) -> str | None:
    """Resolve country: prefer registration (on aircraft) over hex (can miscode)."""
    reg_cc = _registration_to_country(registration)
    if reg_cc:
        return reg_cc
    return _icao_hex_to_country(hex_code)


def _parse_aircraft(ac: dict) -> AircraftPosition:
    hex_code = ac.get("hex")
    registration = ac.get("r")
    return AircraftPosition(
        hex=hex_code,
        flight=(ac.get("flight") or "").strip() or None,
        registration=registration,
        aircraft_type=ac.get("t"),
        description=ac.get("desc"),
        country_code=_resolve_country(registration, hex_code),
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


class FlightAwareResponse(BaseModel):
    ident: str
    positions: list[TrailPoint]
    total: int
    fa_flight_id: str | None = None
    origin: str | None = None
    destination: str | None = None
    aircraft_type: str | None = None
    route_distance: str | None = None
    owner: str | None = None
    operator: str | None = None
    operator_icao: str | None = None
    status: str | None = None
    blocked: bool = False
    available: bool = True
    message: str | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    estimated_arrival: str | None = None
    filed_ete: int | None = None
    progress_percent: int | None = None
    filed_altitude: int | None = None
    filed_airspeed: int | None = None
    filed_route: str | None = None
    registration: str | None = None


FLIGHTAWARE_BASE = "https://aeroapi.flightaware.com/aeroapi"


async def _fa_get(path: str) -> tuple[dict | None, int, str]:
    """FlightAware request returning (data, status_code, error_detail)."""
    api_key = os.getenv("FLIGHTAWARE_API_KEY", "").strip()
    if not api_key or api_key in ("your-flightaware-key", "placeholder"):
        return None, 0, "API key not configured"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{FLIGHTAWARE_BASE}{path}",
                headers={"x-apikey": api_key},
            )
            if resp.status_code == 200:
                return resp.json(), 200, ""
            detail = ""
            try:
                body = resp.json()
                detail = body.get("detail", body.get("title", ""))
            except Exception:
                detail = resp.text[:200]
            logger.warning("FlightAware %s → %d: %s", path, resp.status_code, detail)
            return None, resp.status_code, detail
    except Exception as e:
        logger.warning("FlightAware request error: %s", e)
        return None, 0, str(e)


def _parse_fa_positions(track_data: dict) -> list[TrailPoint]:
    """Parse FlightAware track positions into TrailPoint list."""
    from datetime import datetime as _dt
    positions: list[TrailPoint] = []
    for pos in track_data.get("positions", []):
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is None or lon is None:
            continue
        alt_hundreds = pos.get("altitude")
        alt_ft = alt_hundreds * 100 if isinstance(alt_hundreds, (int, float)) else None
        ts_str = pos.get("timestamp", "")
        try:
            ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = 0.0
        positions.append(TrailPoint(ts=ts, lat=lat, lon=lon, alt=alt_ft))
    return positions


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


@router.get("/aircraft/flightaware/{ident}")
async def get_flightaware_route(ident: str, registration: str | None = Query(None)):
    """Fetch full flight route from FlightAware AeroAPI.
    Tries callsign first, then registration as fallback. Handles blocked military aircraft."""
    try:
        return await _do_flightaware_route(ident, registration)
    except Exception as e:
        logger.exception("FlightAware endpoint crashed for %s: %s", ident, e)
        return FlightAwareResponse(
            ident=ident, positions=[], total=0,
            message=f"Internal error — {type(e).__name__}: {e}",
        )


_fa_rate_limited_until: float = 0.0


async def _do_flightaware_route(ident: str, registration: str | None) -> FlightAwareResponse:
    global _fa_rate_limited_until
    api_key = os.getenv("FLIGHTAWARE_API_KEY", "").strip()
    if not api_key or api_key in ("your-flightaware-key", "placeholder"):
        return FlightAwareResponse(
            ident=ident, positions=[], total=0, available=False,
            message="FlightAware API key not configured",
        )

    cache_key = f"fa_route:{ident.upper()}:{(registration or '').upper()}"
    cached = _get_cached(cache_key, 600)
    if cached is not None:
        return cached

    if time.time() < _fa_rate_limited_until:
        wait = int(_fa_rate_limited_until - time.time())
        return FlightAwareResponse(
            ident=ident, positions=[], total=0,
            message=f"FlightAware rate limit — try again in ~{wait}s",
        )

    # Try callsign first, then registration without hyphens (max 2 calls)
    idents_to_try = [ident]
    if registration:
        clean_reg = registration.replace("-", "").strip()
        if clean_reg.upper() != ident.upper():
            idents_to_try.append(clean_reg)

    flights_data = None
    used_ident = ident
    last_error = ""
    for try_ident in idents_to_try:
        data, status, err = await _fa_get(f"/flights/{try_ident}")
        if status == 429:
            _fa_rate_limited_until = time.time() + 60
            return FlightAwareResponse(
                ident=ident, positions=[], total=0,
                message="FlightAware rate limit reached — wait ~60s before retrying",
            )
        if data and data.get("flights"):
            flights_data = data
            used_ident = try_ident
            break
        if status == 400 and "blocked" in err.lower():
            result = FlightAwareResponse(
                ident=ident, positions=[], total=0, blocked=True,
                message=f"Aircraft '{try_ident}' is blocked on FlightAware (common for military)",
            )
            _set_cache(cache_key, result)
            return result
        if status == 200:
            last_error = f"No active flights for '{try_ident}'"
        elif status == 404:
            last_error = f"'{try_ident}' not found"
        else:
            last_error = err or f"FlightAware error {status}"

    if not flights_data or not flights_data.get("flights"):
        tried = " / ".join(idents_to_try)
        result = FlightAwareResponse(
            ident=ident, positions=[], total=0,
            message=last_error or f"No flights found ({tried}). Military flights are often not tracked.",
        )
        _set_cache(cache_key, result)
        return result

    flight = flights_data["flights"][0]
    fa_id = flight.get("fa_flight_id")
    origin = flight.get("origin") or {}
    dest = flight.get("destination") or {}
    origin_code = origin.get("code_iata") or origin.get("code_icao") or origin.get("code")
    origin_name = origin.get("name")
    dest_code = dest.get("code_iata") or dest.get("code_icao") or dest.get("code")
    dest_name = dest.get("name")
    origin_str = f"{origin_code} ({origin_name})" if origin_name and origin_code else origin_code
    dest_str = f"{dest_code} ({dest_name})" if dest_name and dest_code else dest_code
    flight_reg = registration or flight.get("registration")

    # Extract owner from the flights response itself (no extra API call)
    owner_str = None
    raw_operator = flight.get("operator")
    if isinstance(raw_operator, dict):
        owner_str = raw_operator.get("name")
    elif isinstance(raw_operator, str):
        owner_str = raw_operator

    common_kwargs = dict(
        origin=origin_str, destination=dest_str,
        aircraft_type=flight.get("aircraft_type"),
        route_distance=f"{flight.get('route_distance')} nm" if flight.get("route_distance") else None,
        operator=owner_str, operator_icao=flight.get("operator_icao"),
        status=flight.get("status"), owner=None,
        departure_time=flight.get("actual_out") or flight.get("scheduled_out"),
        arrival_time=flight.get("actual_in"),
        estimated_arrival=flight.get("estimated_in") or flight.get("scheduled_in"),
        filed_ete=_safe_int(flight.get("filed_ete")),
        progress_percent=_safe_int(flight.get("progress_percent")),
        filed_altitude=_safe_int(flight.get("filed_altitude")),
        filed_airspeed=_safe_int(flight.get("filed_airspeed")),
        filed_route=flight.get("route"),
        registration=flight_reg,
    )

    if not fa_id:
        result = FlightAwareResponse(
            ident=used_ident, positions=[], total=0, **common_kwargs,
            message="Flight found but no track ID — may be scheduled or blocked",
        )
        _set_cache(cache_key, result)
        return result

    # Fetch track (2nd API call)
    track_data, track_status, track_err = await _fa_get(f"/flights/{fa_id}/track")
    if track_status == 429:
        _fa_rate_limited_until = time.time() + 60
        return FlightAwareResponse(
            ident=ident, positions=[], total=0,
            message="FlightAware rate limit reached — wait ~60s before retrying",
        )
    positions = _parse_fa_positions(track_data) if track_data else []

    result = FlightAwareResponse(
        ident=used_ident, positions=positions, total=len(positions),
        fa_flight_id=fa_id, **common_kwargs,
        message=track_err if not positions else None,
    )
    _set_cache(cache_key, result)
    return result


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


_ADSB_SOURCES = [
    ("adsb.lol", "https://api.adsb.lol/v2/mil"),
    ("airplanes.live", "https://api.airplanes.live/v2/mil"),
]


async def _fetch_adsblol() -> list[AircraftPosition]:
    """Fetch military aircraft from adsb.lol with airplanes.live as fallback."""
    for name, url in _ADSB_SOURCES:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            ac_list = data.get("ac", [])
            result = [_parse_aircraft(ac) for ac in ac_list if ac.get("lat") and ac.get("lon")]
            if result:
                logger.info("%s: %d military aircraft", name, len(result))
                return result
        except Exception as e:
            logger.warning("%s fetch failed: %s", name, e)
    logger.error("All ADS-B sources failed")
    return []


# Genuine military ICAO24 hex ranges per country
# Source: https://www.ads-b.nl/overview.php (ICAO allocation table)
# National blocks from ICAO Annex 10 (military shares with civil when no dedicated block)
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
    (0x894000, 0x897FFF),  # Pakistan military
    (0x700000, 0x70FFFF),  # Saudi Arabia
    (0x738000, 0x73FFFF),  # Turkey military
    # Nordics
    (0x458000, 0x45BFFF),  # Denmark
    (0x460000, 0x463FFF),  # Finland
    (0x480000, 0x487FFF),  # Norway
    (0x4A8000, 0x4AFFFF),  # Sweden
    # Gulf states
    (0x880000, 0x887FFF),  # UAE
    (0x888000, 0x88FFFF),  # Bahrain
    (0x890000, 0x893FFF),  # Qatar
    (0x748000, 0x74FFFF),  # Kuwait
    (0x898000, 0x8FFFFF),  # Oman
    # NATO allies
    (0x448000, 0x44FFFF),  # Belgium
    (0x488000, 0x48FFFF),  # Poland
    (0x490000, 0x497FFF),  # Portugal
    (0x470000, 0x477FFF),  # Greece
    (0x498000, 0x49FFFF),  # Czech Republic
    (0x4A0000, 0x4A7FFF),  # Romania
    (0x450000, 0x457FFF),  # Bulgaria
    # Conflict actors (often transponder-off, but when broadcasting we show them)
    (0x100000, 0x1FFFFF),  # Russia
    (0x720000, 0x727FFF),  # Iran
    (0x728000, 0x72FFFF),  # Iraq
    (0x518000, 0x51FFFF),  # Syria
    (0x510000, 0x517FFF),  # Lebanon
    (0x508000, 0x50FFFF),  # Jordan
    (0x740000, 0x747FFF),  # Jordan (main civil block)
    (0x010000, 0x017FFF),  # Egypt
    (0x018000, 0x01FFFF),  # Libya
    (0x680000, 0x6FFFFF),  # China
    (0x600000, 0x6003FF),  # Afghanistan
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


# --- Strike / conflict event endpoints (GDELT → LLM enrichment) ---

# CAMEO root codes for violent events
_CAMEO_VIOLENT = {"18", "19", "20"}  # 18=Assault, 19=Fight, 20=Use unconventional violence/force

# Middle East country FIPS codes (used in GDELT Actor1/2CountryCode)
_ME_FIPS = {"IR", "IZ", "SY", "IS", "LE", "YM", "GZ", "WE", "JO", "TU", "SA", "EG", "MU", "AE", "QA", "KU", "BA"}

# GDELT v2 export TSV column indices (61 columns total)
_COL_GLOBALEVENTID = 0
_COL_DAY = 1
_COL_ACTOR1NAME = 6
_COL_ACTOR1COUNTRY = 7
_COL_ACTOR2NAME = 16
_COL_ACTOR2COUNTRY = 17
_COL_EVENTROOTCODE = 28
_COL_EVENTCODE = 26
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
_COL_DATEADDED = 59
_COL_SOURCEURL = 60


@router.get("/strikes", response_model=StrikesResponse)
async def get_strike_events(
    days: int = Query(7, ge=1, le=180),
    limit: int = Query(500, ge=1, le=5000),
):
    """Fetch conflict events from GDELT, optionally enriched by LLM."""
    cache_key = f"strikes:{days}"
    cached = _get_cached(cache_key, CACHE_TTL_STRIKES)
    if cached is not None:
        events = _compute_hours_ago(cached)
        is_enriched = any(e.title is not None for e in events)
        return StrikesResponse(events=events, total=len(events), cached=True, enriched=is_enriched)

    gdelt_events = await _fetch_gdelt_event_files(days, limit)

    all_events = gdelt_events[:limit]
    all_events.sort(key=lambda e: e.event_date or "", reverse=True)
    all_events = _compute_hours_ago(all_events)

    _set_cache(cache_key, all_events)
    return StrikesResponse(
        events=all_events, total=len(all_events), cached=False, enriched=False,
        hint="Conflict events are being processed by AI. Enriched data will appear shortly.",
    )


def _compute_hours_ago(events: list[StrikeEvent]) -> list[StrikeEvent]:
    """Compute hours_ago for each event based on event_date."""
    now = datetime.datetime.now(datetime.timezone.utc)
    for ev in events:
        if ev.event_date:
            try:
                if "T" in ev.event_date:
                    dt = datetime.datetime.strptime(ev.event_date, "%Y-%m-%dT%H:%M:%S")
                else:
                    dt = datetime.datetime.strptime(ev.event_date, "%Y-%m-%d")
                dt = dt.replace(tzinfo=datetime.timezone.utc)
                delta = now - dt
                ev.hours_ago = round(delta.total_seconds() / 3600, 1)
            except ValueError:
                pass
    return events


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast haversine distance in km."""
    from math import radians, sin, cos, sqrt, atan2
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# --- GDELT event files ---


async def _fetch_gdelt_event_files(days: int, limit: int) -> list[StrikeEvent]:
    """Download recent GDELT v2 event export files and filter for ME military events."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get("http://data.gdeltproject.org/gdeltv2/lastupdate.txt")
            resp.raise_for_status()
            lines = resp.text.strip().split("\n")
    except Exception as e:
        logger.error("Failed to get GDELT file list: %s", e)
        return []

    export_urls: list[str] = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 3 and "export.CSV" in parts[2]:
            export_urls.append(parts[2])

    if not export_urls:
        logger.warning("No GDELT export URLs found")
        return []

    if days > 1:
        extra_urls = await _get_gdelt_historical_urls(days)
        export_urls = extra_urls + export_urls

    # Fetch more files per day for better coverage (was 4, now 8)
    max_files = min(len(export_urls), days * 8)
    events: list[StrikeEvent] = []
    seen_ids: set[str] = set()

    for url in export_urls[:max_files]:
        try:
            batch = await _download_parse_gdelt_export(url, seen_ids)
            events.extend(batch)
            if len(events) >= limit:
                break
        except Exception as e:
            logger.error("Failed to process GDELT file %s: %s", url, e)

    events = events[:limit]
    logger.info("Fetched %d GDELT conflict events from %d files", len(events), max_files)
    return events


async def _get_gdelt_historical_urls(days: int) -> list[str]:
    """Get GDELT export file URLs for past N days."""
    urls: list[str] = []
    now = datetime.datetime.now(datetime.timezone.utc)

    # Sample every 3 hours for better coverage (was every 6)
    for d in range(1, min(days + 1, 30)):
        for h in range(0, 24, 3):
            ts = now - datetime.timedelta(days=d) + datetime.timedelta(hours=h)
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
                    # Use DATEADDED (YYYYMMDDHHmmSS) for precise timestamps
                    date_added = row[_COL_DATEADDED] if len(row) > _COL_DATEADDED else ""
                    if len(date_added) >= 14:
                        event_date = f"{date_added[:4]}-{date_added[4:6]}-{date_added[6:8]}T{date_added[8:10]}:{date_added[10:12]}:{date_added[12:14]}"
                    elif len(day_str) >= 8:
                        event_date = f"{day_str[:4]}-{day_str[4:6]}-{day_str[6:8]}"
                    else:
                        event_date = None

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
                        event_id=f"gdelt-{event_id}",
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
                        source=f"GDELT · {source_url}" if source_url else "GDELT",
                        source_url=source_url,
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


# --- Military news feed (RSS + GDELT DOC 2.0) ---


class NewsItem(BaseModel):
    title: str
    link: str | None = None
    published: str | None = None
    source: str | None = None
    summary: str | None = None
    relevance: float = 0.0


class NewsResponse(BaseModel):
    items: list[NewsItem]
    total: int
    cached: bool = False
    sources_ok: int = 0
    sources_failed: int = 0


CACHE_TTL_NEWS = 300  # 5 minutes

_RSS_FEEDS: list[tuple[str, str]] = [
    # Military / defense specialist
    ("https://www.thedrive.com/the-war-zone/feed", "The War Zone"),
    ("https://breakingdefense.com/feed/", "Breaking Defense"),
    ("https://feeds.feedburner.com/defense-news/home", "Defense News"),
    ("https://theaviationist.com/feed/", "The Aviationist"),
    # Middle East focused
    ("https://www.israelhayom.com/feed/", "Israel Hayom"),
    ("https://www.timesofisrael.com/feed/", "Times of Israel"),
    ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml", "NY Times ME"),
    ("https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "BBC Middle East"),
    ("https://www.middleeasteye.net/rss", "Middle East Eye"),
    # US & UK news
    ("https://moxie.foxnews.com/google-publisher/world.xml", "Fox News"),
    ("https://rss.cnn.com/rss/cnn_world.rss", "CNN"),
    ("https://www.theguardian.com/world/rss", "The Guardian"),
    ("https://www.thetimes.co.uk/tto/news/rss/", "The Times"),
]

# Tiered keyword scoring — high-value terms score more
_KW_HIGH: set[str] = {
    "iran", "tehran", "isfahan", "natanz", "irgc", "quds force", "khamenei",
    "israel", "idf", "netanyahu", "gaza", "hezbollah", "hamas",
    "airstrike", "missile strike", "air campaign", "military strike",
    "centcom", "eucom", "strait of hormuz", "persian gulf", "red sea",
    "f-35", "f-15", "b-2", "b-52", "kc-135", "kc-46", "rc-135",
    "carrier strike group", "no-fly zone", "air defense",
    "houthi", "ansar allah", "proxy war",
}

_KW_MED: set[str] = {
    "military", "air force", "navy", "defense", "defence",
    "fighter jet", "drone", "uav", "tanker", "refueling",
    "deployment", "conflict", "war", "attack", "bombing",
    "missile", "strike", "pentagon", "nato", "coalition",
    "reconnaissance", "surveillance", "sortie",
    "syria", "iraq", "yemen", "lebanon", "saudi",
    "sanctions", "nuclear", "ballistic",
}

_KW_LOW: set[str] = {
    "middle east", "security", "tension", "escalation", "ceasefire",
    "weapons", "arms", "airspace", "interception",
    "egypt", "turkey", "qatar", "bahrain", "uae", "kuwait",
    "operation", "patrol", "base",
}


def _score_relevance(title: str, summary: str) -> float:
    """Score article relevance 0.0–1.0 based on tiered keyword matches."""
    text = (title + " " + summary).lower()
    high_hits = sum(1 for kw in _KW_HIGH if kw in text)
    med_hits = sum(1 for kw in _KW_MED if kw in text)
    low_hits = sum(1 for kw in _KW_LOW if kw in text)
    score = min(1.0, high_hits * 0.3 + med_hits * 0.12 + low_hits * 0.05)
    title_low = title.lower()
    if any(kw in title_low for kw in _KW_HIGH):
        score = min(1.0, score + 0.2)
    return round(score, 2)


def _normalize_date(date_str: str | None) -> str | None:
    """Normalize various date formats to ISO 8601."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(date_str.strip(), fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return date_str


def _dedup_news(items: list[NewsItem]) -> list[NewsItem]:
    """Remove near-duplicate articles by normalized title prefix."""
    seen: dict[str, NewsItem] = {}
    for item in items:
        key = re.sub(r"[^a-z0-9\s]", "", item.title.lower())
        key = re.sub(r"\s+", " ", key).strip()[:60]
        if key in seen:
            if item.relevance > seen[key].relevance:
                seen[key] = item
        else:
            seen[key] = item
    return list(seen.values())


async def _fetch_single_rss(
    client: httpx.AsyncClient, feed_url: str, source_name: str,
) -> tuple[list[NewsItem], bool]:
    """Fetch and parse a single RSS feed. Returns (items, success)."""
    try:
        resp = await client.get(
            feed_url,
            headers={"User-Agent": "MilTrack/1.0 (military-aviation-tracker)"},
        )
        if resp.status_code != 200:
            logger.warning("RSS %s returned %d", source_name, resp.status_code)
            return [], False

        root = ET.fromstring(resp.content)

        items_el = root.findall(".//item")
        if not items_el:
            atom_ns = "{http://www.w3.org/2005/Atom}"
            items_el = root.findall(f".//{atom_ns}entry")

        results: list[NewsItem] = []
        for el in items_el:
            title = ""
            for tag in ["title", "{http://www.w3.org/2005/Atom}title"]:
                t = el.find(tag)
                if t is not None:
                    title = "".join(t.itertext()).strip()
                    if title:
                        break
            if not title:
                continue

            link = ""
            link_el = el.find("link")
            if link_el is not None:
                link = (link_el.text or "").strip() or link_el.get("href", "")
            if not link:
                atom_link = el.find("{http://www.w3.org/2005/Atom}link")
                if atom_link is not None:
                    link = atom_link.get("href", "")

            published = None
            for tag in [
                "pubDate",
                "{http://www.w3.org/2005/Atom}published",
                "{http://www.w3.org/2005/Atom}updated",
            ]:
                t = el.find(tag)
                if t is not None and t.text:
                    published = _normalize_date(t.text.strip())
                    break

            summary = ""
            for tag in [
                "description",
                "{http://www.w3.org/2005/Atom}summary",
                "{http://www.w3.org/2005/Atom}content",
            ]:
                t = el.find(tag)
                if t is not None:
                    raw = "".join(t.itertext()).strip()
                    if raw:
                        summary = re.sub(r"<[^>]+>", "", raw)[:500]
                        break

            rel = _score_relevance(title, summary)
            if rel < 0.05:
                continue

            results.append(NewsItem(
                title=title,
                link=link or None,
                published=published,
                source=source_name,
                summary=summary[:250] if summary else None,
                relevance=rel,
            ))

        logger.info("RSS %s: %d items, %d relevant", source_name, len(items_el), len(results))
        return results, True
    except Exception as e:
        logger.error("RSS feed %s failed: %s", source_name, e)
        return [], False


_GDELT_NEWS_QUERIES = [
    "military iran israel airstrike",
    "missile strike conflict middle east",
    "houthi drone red sea navy",
]


async def _fetch_gdelt_news() -> list[NewsItem]:
    """Fetch recent military/conflict news from GDELT DOC 2.0 API (free, no auth)."""
    all_items: list[NewsItem] = []
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for q in _GDELT_NEWS_QUERIES:
            try:
                resp = await client.get(
                    "http://api.gdeltproject.org/api/v2/doc/doc",
                    params={
                        "query": q,
                        "mode": "artlist",
                        "maxrecords": "75",
                        "format": "json",
                        "timespan": "48h",
                        "sourcelang": "english",
                    },
                )
                if resp.status_code != 200:
                    logger.warning("GDELT DOC returned %d for '%s'", resp.status_code, q)
                    continue

                data = resp.json()
                articles = data.get("articles", [])

                for art in articles:
                    title = (art.get("title") or "").strip()
                    url = art.get("url", "")
                    if not title or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    seen_date = art.get("seendate", "")
                    published = None
                    if seen_date and len(seen_date) >= 14:
                        try:
                            published = (
                                f"{seen_date[:4]}-{seen_date[4:6]}-{seen_date[6:8]}"
                                f"T{seen_date[9:11]}:{seen_date[11:13]}:00Z"
                            )
                        except (IndexError, ValueError):
                            pass

                    domain = art.get("domain", "")
                    rel = _score_relevance(title, "")
                    if rel < 0.05:
                        continue

                    all_items.append(NewsItem(
                        title=title,
                        link=url or None,
                        published=published,
                        source=f"GDELT · {domain}" if domain else "GDELT",
                        summary=None,
                        relevance=rel,
                    ))

                logger.info("GDELT DOC '%s': %d articles", q, len(articles))

            except Exception as e:
                logger.error("GDELT DOC query '%s' failed: %s: %s", q, type(e).__name__, e)

    return all_items


async def _fetch_x_news() -> list[NewsItem]:
    """Fetch military/conflict tweets from X API v2 (paid, requires X_API_BEARER_TOKEN)."""
    token = os.environ.get("X_API_BEARER_TOKEN", "").strip()
    if not token or token == "your-x-bearer-token":
        return []

    # Query: last 7 days, military/conflict keywords
    query = "(Iran OR Israel OR IDF OR Hezbollah OR Gaza) (airstrike OR missile OR military OR attack)"
    url = "https://api.twitter.com/2/tweets/search/recent"
    params = {
        "query": query,
        "max_results": 50,
        "tweet.fields": "created_at,author_id,public_metrics",
        "expansions": "author_id",
        "user.fields": "username",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "MilTrack/1.0 (military-aviation-tracker)",
                },
            )
            if resp.status_code == 429:
                logger.warning("X API rate limited (429)")
                return []
            if resp.status_code == 401:
                logger.warning("X API unauthorized (401) — check bearer token")
                return []
            if resp.status_code == 403:
                logger.warning("X API forbidden (403) — tier may not include search")
                return []
            if resp.status_code != 200:
                logger.warning("X API returned %d", resp.status_code)
                return []

            data = resp.json()
    except Exception as e:
        logger.debug("X API request failed: %s", e)
        return []

    users: dict[str, str] = {}
    for u in data.get("includes", {}).get("users", []):
        uid = u.get("id")
        uname = u.get("username", "")
        if uid:
            users[uid] = f"@{uname}" if uname else "X"

    items: list[NewsItem] = []
    for t in data.get("data", []):
        text = (t.get("text") or "").strip()
        if not text or len(text) < 20:
            continue
        tid = t.get("id", "")
        created = t.get("created_at", "")
        author_id = t.get("author_id", "")
        source = users.get(author_id, "X")
        link = f"https://x.com/i/status/{tid}" if tid else None
        rel = _score_relevance(text[:200], "")
        if rel < 0.05:
            continue
        items.append(NewsItem(
            title=text[:120] + ("…" if len(text) > 120 else ""),
            link=link,
            published=created[:19] + "Z" if created and len(created) >= 19 else None,
            source=source,
            summary=text[:300] if len(text) > 120 else None,
            relevance=rel,
        ))

    if items:
        logger.info("X API: %d tweets", len(items))
    return items


_BRAVE_X_QUERIES = [
    "Iran Israel military site:x.com",
    "IDF Hezbollah airstrike site:x.com",
    "Gaza missile strike site:x.com",
]


async def _fetch_brave_x_news() -> list[NewsItem]:
    """Fetch X/tweet content via Brave web search (uses BRAVE_SEARCH_API_KEY, no X API needed)."""
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key or api_key == "your-brave-api-key":
        return []

    items: list[NewsItem] = []
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(timeout=15.0) as client:
        for q in _BRAVE_X_QUERIES:
            try:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={
                        "q": q,
                        "count": "15",
                        "freshness": "pw",
                        "text_decorations": "false",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": api_key,
                    },
                )
                if resp.status_code == 401:
                    logger.warning("Brave Search 401 — check BRAVE_SEARCH_API_KEY")
                    return []
                if resp.status_code == 429:
                    logger.warning("Brave Search rate limited (429)")
                    return []
                if resp.status_code != 200:
                    continue

                data = resp.json()
                results = data.get("web", {}).get("results", [])

                for r in results:
                    url = (r.get("url") or "").strip()
                    if not url or "x.com" not in url and "twitter.com" not in url:
                        continue
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    title = (r.get("title") or "").strip()
                    desc = (r.get("description") or "").strip()
                    text = f"{title} {desc}".strip()
                    if len(text) < 30:
                        continue

                    rel = _score_relevance(text[:200], "")
                    if rel < 0.05:
                        continue

                    age = r.get("age", "")
                    published = age[:19] + "Z" if age and len(age) >= 19 else None

                    items.append(NewsItem(
                        title=title or text[:80] + ("…" if len(text) > 80 else ""),
                        link=url,
                        published=published,
                        source="X (Brave)",
                        summary=desc or None,
                        relevance=rel,
                    ))

            except Exception as e:
                logger.debug("Brave X search '%s' failed: %s", q[:30], e)

    if items:
        logger.info("Brave X search: %d items", len(items))
    return items


@router.get("/news", response_model=NewsResponse)
async def get_military_news(limit: int = Query(75, ge=1, le=500)):
    """Fetch latest military/conflict news from RSS feeds + GDELT DOC API."""
    cache_key = "mil_news"
    cached = _get_cached(cache_key, CACHE_TTL_NEWS)
    if cached is not None:
        items, sources_ok, sources_failed = cached
        return NewsResponse(
            items=items[:limit], total=len(items), cached=True,
            sources_ok=sources_ok, sources_failed=sources_failed,
        )

    items, sources_ok, sources_failed = await _fetch_all_news()
    _set_cache(cache_key, (items, sources_ok, sources_failed))
    return NewsResponse(
        items=items[:limit], total=len(items), cached=False,
        sources_ok=sources_ok, sources_failed=sources_failed,
    )


async def _fetch_all_news() -> tuple[list[NewsItem], int, int]:
    """Fetch all RSS feeds in parallel + GDELT DOC, merge, dedup, rank."""
    sources_ok = 0
    sources_failed = 0

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        tasks = [_fetch_single_rss(client, url, name) for url, name in _RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[NewsItem] = []
    for result in results:
        if isinstance(result, Exception):
            sources_failed += 1
            logger.error("RSS feed raised: %s", result)
        else:
            items, ok = result
            all_items.extend(items)
            if ok:
                sources_ok += 1
            else:
                sources_failed += 1

    try:
        gdelt_items = await _fetch_gdelt_news()
        all_items.extend(gdelt_items)
        if gdelt_items:
            sources_ok += 1
    except Exception as e:
        logger.error("GDELT DOC failed: %s", e)
        sources_failed += 1

    try:
        x_items = await _fetch_x_news()
        all_items.extend(x_items)
        if x_items:
            sources_ok += 1
    except Exception as e:
        logger.error("X API failed: %s", e)
        sources_failed += 1

    try:
        brave_x_items = await _fetch_brave_x_news()
        all_items.extend(brave_x_items)
        if brave_x_items:
            sources_ok += 1
    except Exception as e:
        logger.error("Brave X search failed: %s", e)
        sources_failed += 1

    all_items = _dedup_news(all_items)
    all_items.sort(key=lambda x: (x.relevance, x.published or ""), reverse=True)

    logger.info(
        "News total: %d items from %d sources (%d failed)",
        len(all_items), sources_ok, sources_failed,
    )
    return all_items, sources_ok, sources_failed


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
    "C27J": "Alenia_C-27J_Spartan",
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
    "A139": "AgustaWestland_AW139",
    "A169": "AgustaWestland_AW169",
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
    "PC21": "Pilatus_PC-21",
    "P180": "Piaggio_P.180_Avanti",
    "FA7X": "Dassault_Falcon_7X",
    "EC45": "Airbus_H145",
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
    query = f"{type_code} aircraft"
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

        # Skip generic list pages (e.g. "List of active United States military aircraft")
        for r in results:
            title = r["title"]
            if title.lower().startswith("list of ") or title.lower().startswith("lists of "):
                continue
            return await _fetch_wiki_summary(type_code, title.replace(" ", "_"))
        return AircraftInfo(type_code=type_code)
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
