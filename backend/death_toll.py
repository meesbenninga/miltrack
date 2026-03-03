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

import httpx
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()

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


def _aggregate_ucdp_by_country(events: list[dict]) -> dict[str, dict]:
    """Aggregate UCDP GED events by country. Uses 'best' estimate for deaths."""
    by_country: dict[str, dict] = defaultdict(lambda: {"ucdp_best": 0, "ucdp_low": 0, "ucdp_high": 0})
    for ev in events:
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


def _aggregate_gdelt_by_country(events: list) -> dict[str, int]:
    """Aggregate GDELT strike events by country from fatalities field.
    Location-based by default. When actor1 or actor2 indicates US (victim), add to United States."""
    by_country: dict[str, int] = defaultdict(int)

    for ev in events:
        fatalities = getattr(ev, "fatalities", None) or (ev.get("fatalities") if isinstance(ev, dict) else None)
        if fatalities is None or not isinstance(fatalities, (int, float)):
            continue
        n = int(fatalities)

        actor2 = getattr(ev, "actor2", None) or (ev.get("actor2") if isinstance(ev, dict) else None)
        country = getattr(ev, "country", None) or (ev.get("country") if isinstance(ev, dict) else None)

        # US casualties: when actor2 (target/victim) indicates US
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


async def get_death_toll(gdelt_events: list | None = None) -> dict:
    """
    Return death toll per country from UCDP (verified) and GDELT (secondary).

    Returns:
        {
            "by_country": [
                {
                    "country": "Iraq",
                    "ucdp_best": 1234,   # verified (null if no UCDP)
                    "ucdp_low": 1000,
                    "ucdp_high": 1500,
                    "gdelt_total": 56,   # from AI-enriched GDELT (unverified)
                    "source": "UCDP + GDELT" | "UCDP" | "GDELT"
                }
            ],
            "ucdp_available": bool,
            "period": "2024-01-01 to 2025-03-02" (or similar)
        }
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    end_date = now.strftime("%Y-%m-%d")
    # Iran war: escalation from Israeli strike on Damascus consulate (Apr 2024)
    start_date = "2024-04-01"

    ucdp_by_country: dict[str, dict] = {}
    ucdp_events = await _fetch_ucdp_ged(_ME_GEOGRAPHY, start_date, end_date)
    if ucdp_events:
        ucdp_by_country = _aggregate_ucdp_by_country(ucdp_events)

    gdelt_by_country: dict[str, int] = {}
    if gdelt_events:
        gdelt_by_country = _aggregate_gdelt_by_country(gdelt_events)

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
        })

    by_country.sort(key=lambda x: ((x["ucdp_best"] or 0), (x["gdelt_total"] or 0)), reverse=True)

    return {
        "by_country": by_country,
        "ucdp_available": bool(ucdp_events),
        "period": f"{start_date} to {end_date}",
    }


@router.get("/death-toll")
async def death_toll_endpoint():
    """Death toll per country — UCDP (verified) + GDELT aggregation."""
    from .tracker import _get_cached

    CACHE_TTL = 3600
    cached = _get_cached("strikes:90", CACHE_TTL) or _get_cached("strikes:180", CACHE_TTL)
    gdelt_events = list(cached) if cached else []
    return await get_death_toll(gdelt_events)
