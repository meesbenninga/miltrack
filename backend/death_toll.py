"""Death toll per country — UCDP (verified) + GDELT aggregation.

Data sources:
- UCDP GED (gedevents): verified battle-related deaths, requires UCDP_ACCESS_TOKEN
- GDELT: AI-enriched conflict events with inferred fatalities (secondary)
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter()

_CONFLICT_CONTEXT: dict[str, str] = {
    "Israel": "Gaza conflict",
    "Palestine": "Gaza conflict",
    "Lebanon": "Israel-Lebanon / Hezbollah conflict",
    "Yemen": "Houthi / Red Sea crisis",
    "Iran": "Iran-Israel tensions",
    "Syria": "Civil war / regional spillover",
    "Iraq": "Militia activity / regional spillover",
}

# Middle East bounding box: SW (lat,lon), NE (lat,lon)
# Format: "lat0 lon0,lat1 lon1" per UCDP Geography filter
_ME_GEOGRAPHY = "12 25,42 63"

# Gleditsch-Ward country codes for Middle East (for UCDP)
_ME_GW_CODES = {
    630: "Iran",
    645: "Iraq",
    666: "Israel",
    652: "Syria",
    660: "Lebanon",
    663: "Jordan",
    678: "Yemen",
    680: "Yemen (South)",
    640: "Turkey",
    670: "Saudi Arabia",
    651: "Egypt",
    690: "Kuwait",
    696: "UAE",
    692: "Bahrain",
    694: "Qatar",
    698: "Oman",
    667: "Palestine",
}

UCDP_API_BASE = "https://ucdpapi.pcr.uu.se/api"
UCDP_GED_VERSION = "25.1"


async def _fetch_ucdp_ged(geography: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch UCDP GED events for a region and date range. Requires UCDP_ACCESS_TOKEN."""
    token = os.getenv("UCDP_ACCESS_TOKEN", "").strip()
    if not token:
        return []

    url = f"{UCDP_API_BASE}/gedevents/{UCDP_GED_VERSION}"
    params = {
        "pagesize": 500,
        "page": 1,
        "Geography": geography,
        "StartDate": start_date,
        "EndDate": end_date,
    }
    headers = {"x-ucdp-access-token": token}

    all_events: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("Result", [])
                all_events.extend(results)

                next_url = data.get("NextPageUrl")
                if not next_url or not results:
                    break
                # Parse next page from URL
                if "page=" in next_url:
                    page = int(next_url.split("page=")[-1].split("&")[0])
                    params["page"] = page
                else:
                    break
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            logger.warning("UCDP: Invalid or missing token")
        else:
            logger.warning("UCDP API error: %s", e)
    except Exception as e:
        logger.warning("UCDP fetch failed: %s", e)

    return all_events


def _aggregate_ucdp_by_country(
    events: list[dict], start_date: str | None = None, end_date: str | None = None,
) -> dict[str, dict]:
    """Aggregate UCDP GED events by country. Uses 'best' estimate for deaths.
    Client-side date filter as safety net (API should already filter)."""
    by_country: dict[str, dict] = defaultdict(lambda: {"ucdp_best": 0, "ucdp_low": 0, "ucdp_high": 0})
    for ev in events:
        if start_date or end_date:
            d = (ev.get("date_start") or ev.get("date_end") or "")[:10]
            if d:
                if start_date and d < start_date:
                    continue
                if end_date and d > end_date:
                    continue
        country = ev.get("country") or ev.get("location") or "Unknown"
        best = ev.get("best") or 0
        low = ev.get("low") or 0
        high = ev.get("high") or 0
        if isinstance(best, (int, float)):
            by_country[country]["ucdp_best"] += int(best)
        if isinstance(low, (int, float)):
            by_country[country]["ucdp_low"] += int(low)
        if isinstance(high, (int, float)):
            by_country[country]["ucdp_high"] += int(high)
    return dict(by_country)


_US_PATTERN = re.compile(
    r"\b(us|u\.s\.?|u\.s\.a\.?|usa|united states|american)\b",
    re.IGNORECASE,
)


def _has_us(s: str | None) -> bool:
    """Check if string indicates United States (for casualty attribution)."""
    if not s or not isinstance(s, str):
        return False
    return bool(_US_PATTERN.search(s))


def _parse_event_date(ev) -> str | None:
    """Extract YYYY-MM-DD from a GDELT StrikeEvent or dict."""
    raw = getattr(ev, "event_date", None) or (ev.get("event_date") if isinstance(ev, dict) else None)
    if not raw or not isinstance(raw, str):
        return None
    return raw[:10]  # "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS" → "YYYY-MM-DD"


def _aggregate_gdelt_by_country(
    events: list, start_date: str | None = None, end_date: str | None = None,
) -> dict[str, int]:
    """Aggregate GDELT strike events by country from fatalities field.
    Only includes events whose event_date falls within [start_date, end_date]."""
    by_country: dict[str, int] = defaultdict(int)

    for ev in events:
        if start_date or end_date:
            d = _parse_event_date(ev)
            if d:
                if start_date and d < start_date:
                    continue
                if end_date and d > end_date:
                    continue

        fatalities = getattr(ev, "fatalities", None) or (ev.get("fatalities") if isinstance(ev, dict) else None)
        if fatalities is None or not isinstance(fatalities, (int, float)):
            continue
        n = int(fatalities)

        actor2 = getattr(ev, "actor2", None) or (ev.get("actor2") if isinstance(ev, dict) else None)
        country = getattr(ev, "country", None) or (ev.get("country") if isinstance(ev, dict) else None)

        if _has_us(actor2):
            by_country["United States"] += n
        elif country:
            by_country[country] += n
    return dict(by_country)


def _normalize_country_name(name: str) -> str:
    """Normalize country names for merging UCDP and GDELT."""
    if not name:
        return ""
    # UCDP uses "Yemen (North Yemen)" etc.
    if "Yemen" in name:
        return "Yemen"
    if "Palestine" in name or "Gaza" in name or "West Bank" in name:
        return "Palestine"
    return name.strip()


def _resolve_dates(preset: str, custom_start: str | None, custom_end: str | None) -> tuple[str, str]:
    """Resolve preset name or custom dates into (start_date, end_date)."""
    now = datetime.now(timezone.utc)
    end = custom_end or now.strftime("%Y-%m-%d")

    if custom_start:
        return custom_start, end

    presets = {
        "30d": (now - timedelta(days=30)).strftime("%Y-%m-%d"),
        "90d": (now - timedelta(days=90)).strftime("%Y-%m-%d"),
        "ytd": f"{now.year}-01-01",
        "2024": "2024-01-01",
        "all": "2023-10-07",
    }
    start = presets.get(preset, presets["all"])
    if preset == "2024":
        end = "2024-12-31"
    return start, end


async def get_death_toll(
    gdelt_events: list | None = None,
    preset: str = "all",
    custom_start: str | None = None,
    custom_end: str | None = None,
) -> dict:
    """Return death toll per country from UCDP (verified) and GDELT (secondary)."""
    start_date, end_date = _resolve_dates(preset, custom_start, custom_end)

    ucdp_by_country: dict[str, dict] = {}
    ucdp_events = await _fetch_ucdp_ged(_ME_GEOGRAPHY, start_date, end_date)
    if ucdp_events:
        ucdp_by_country = _aggregate_ucdp_by_country(ucdp_events, start_date, end_date)

    gdelt_by_country: dict[str, int] = {}
    if gdelt_events:
        gdelt_by_country = _aggregate_gdelt_by_country(gdelt_events, start_date, end_date)

    # Merge by normalized country name
    merged: dict[str, dict] = {}
    for c, data in ucdp_by_country.items():
        norm = _normalize_country_name(c) or c
        if norm not in merged:
            merged[norm] = {"ucdp_best": 0, "ucdp_low": 0, "ucdp_high": 0, "gdelt_total": 0}
        merged[norm]["ucdp_best"] += data.get("ucdp_best", 0)
        merged[norm]["ucdp_low"] += data.get("ucdp_low", 0)
        merged[norm]["ucdp_high"] += data.get("ucdp_high", 0)
    for c, total in gdelt_by_country.items():
        norm = _normalize_country_name(c) or c
        if norm not in merged:
            merged[norm] = {"ucdp_best": 0, "ucdp_low": 0, "ucdp_high": 0, "gdelt_total": 0}
        merged[norm]["gdelt_total"] += total

    by_country = []
    for country, data in merged.items():
        ucdp_best = data["ucdp_best"]
        ucdp_low = data["ucdp_low"]
        ucdp_high = data["ucdp_high"]
        gdelt_total = data["gdelt_total"]
        if ucdp_best == 0 and gdelt_total == 0:
            continue
        source = []
        if ucdp_best > 0:
            source.append("UCDP")
        if gdelt_total > 0:
            source.append("GDELT")
        by_country.append({
            "country": country,
            "ucdp_best": ucdp_best or None,
            "ucdp_low": ucdp_low or None,
            "ucdp_high": ucdp_high or None,
            "gdelt_total": gdelt_total or None,
            "source": " + ".join(source),
            "conflict_context": _CONFLICT_CONTEXT.get(country),
        })

    by_country.sort(key=lambda x: ((x["ucdp_best"] or 0), (x["gdelt_total"] or 0)), reverse=True)

    return {
        "by_country": by_country,
        "ucdp_available": bool(ucdp_events),
        "period": f"{start_date} to {end_date}",
        "preset": preset,
    }


@router.get("/death-toll")
async def death_toll_endpoint(
    preset: str = Query("all", regex="^(30d|90d|ytd|2024|all)$"),
    start_date: str | None = Query(None, alias="start"),
    end_date: str | None = Query(None, alias="end"),
):
    """Death toll per country — UCDP (verified) + GDELT aggregation."""
    from .tracker import _get_cached

    CACHE_TTL = 3600
    cached = _get_cached("strikes:90", CACHE_TTL) or _get_cached("strikes:180", CACHE_TTL)
    gdelt_events = list(cached) if cached else []
    return await get_death_toll(gdelt_events, preset=preset, custom_start=start_date, custom_end=end_date)
