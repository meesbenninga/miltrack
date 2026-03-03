"""AI-powered intelligence pipeline for military news analysis.

Pipeline stages:
  1. Brave Search API — find recent articles
  2. Jina Reader API — extract full article text (free, no auth)
  3. Databricks Foundation Model API — score, classify, summarize

Graceful degradation:
  - No BRAVE_SEARCH_API_KEY → pipeline disabled, frontend falls back to RSS
  - No DATABRICKS_HOST → search + extract work, but articles returned unanalyzed
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Databricks auth — SDK unified auth (supports Databricks Apps + local dev)
# ---------------------------------------------------------------------------

_databricks_config = None
_databricks_host: str | None = None


def _init_databricks_auth():
    """Set up Databricks auth using SDK unified auth.

    In Databricks Apps: auto-detects service principal credentials (M2M OAuth)
    Locally: uses DATABRICKS_TOKEN and DATABRICKS_HOST from .env
    Falls back to raw env vars if SDK is unavailable.
    """
    global _databricks_config, _databricks_host
    try:
        from databricks.sdk.core import Config
        _databricks_config = Config()
        _databricks_host = _databricks_config.host
        if _databricks_config.host:
            os.environ.setdefault("DATABRICKS_HOST", _databricks_config.host)
        logger.info("Databricks SDK auth initialized (host=%s)", _databricks_config.host)
    except Exception as e:
        logger.info("Databricks SDK not available, using env vars: %s", e)


_init_databricks_auth()


def _get_auth_headers() -> dict[str, str]:
    """Get Databricks Authorization headers.

    Prefers an explicit DATABRICKS_TOKEN (PAT) because the app's service
    principal currently cannot call Foundation Model API system endpoints
    (permissions are being migrated to Unity Catalog).
    Falls back to SDK unified auth (M2M OAuth) when no PAT is set.
    """
    token = os.getenv("DATABRICKS_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    if _databricks_config:
        try:
            result = _databricks_config.authenticate()
            if callable(result):
                return result()
            if isinstance(result, dict) and result:
                return result
        except Exception as e:
            logger.warning("SDK auth failed: %s", e)
    return {}

router = APIRouter()

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, object]] = {}


def _get_cached(key: str, ttl: float):
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < ttl:
            return data
    return None


def _get_cached_with_ts(key: str, ttl: float) -> tuple[float, object] | None:
    """Return (timestamp, data) if cached and valid, else None."""
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < ttl:
            return (ts, data)
    return None


def _set_cached(key: str, data: object):
    _cache[key] = (time.time(), data)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def _parse_age_to_hours(age: str | None) -> float:
    """Parse Brave 'age' string (e.g. '2h', '1d') to hours ago. Higher = older."""
    if not age:
        return 999.0
    age = age.strip().lower()
    m = re.match(r"^(\d+)\s*(h|hr|hrs|hour|hours)?$", age)
    if m:
        return float(m.group(1))
    m = re.match(r"^(\d+)\s*(d|day|days)?$", age)
    if m:
        return float(m.group(1)) * 24
    m = re.match(r"^(\d+)\s*(w|week|weeks)?$", age)
    if m:
        return float(m.group(1)) * 24 * 7
    return 999.0


class IntelArticle(BaseModel):
    title: str
    url: str | None = None
    published: str | None = None
    source_domain: str | None = None
    relevance_score: int = 0
    category: str | None = None
    entities: dict | None = None
    summary: str | None = None
    map_connection: str | None = None
    hours_ago: float | None = None  # parsed from published/age for sorting


class IntelResponse(BaseModel):
    articles: list[IntelArticle]
    total: int
    cached: bool = False
    pipeline_status: dict | None = None
    updated_at: float | None = None  # unix timestamp for "New" badge


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_TTL_INTEL = 1800  # 30 min
JINA_MAX_ARTICLES = 12
JINA_CONCURRENCY = 2

_SEARCH_QUERIES = [
    "Iran Israel military conflict latest",
    "Middle East airstrike missile strike today",
    "US military deployment Persian Gulf CENTCOM",
    "Houthi Red Sea attack navy drone",
]


def is_configured() -> bool:
    return bool(os.getenv("BRAVE_SEARCH_API_KEY", ""))


# ---------------------------------------------------------------------------
# Stage 1 — Brave Search
# ---------------------------------------------------------------------------


async def brave_search(query: str, count: int = 10) -> list[dict]:
    """Search for recent news articles via Brave Search API."""
    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "")
    if not api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/news/search",
                params={
                    "q": query,
                    "count": str(count),
                    "freshness": "pd",
                    "text_decorations": "false",
                },
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
            )
            if resp.status_code == 401:
                logger.error("Brave Search 401 — check BRAVE_SEARCH_API_KEY")
                return []
            if resp.status_code == 429:
                logger.warning("Brave Search rate limited")
                return []
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "description": item.get("description", ""),
                "published": item.get("age", ""),
                "domain": (item.get("meta_url") or {}).get("hostname", ""),
            })

        logger.info("Brave Search '%s': %d results", query[:40], len(results))
        return results

    except Exception as e:
        logger.error("Brave Search failed for '%s': %s: %s", query[:40], type(e).__name__, e)
        return []


# ---------------------------------------------------------------------------
# Stage 2 — Jina Reader (full article extraction)
# ---------------------------------------------------------------------------


async def jina_extract(url: str, semaphore: asyncio.Semaphore) -> dict | None:
    """Extract full article text via Jina Reader API (free, no auth)."""
    async with semaphore:
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                for attempt in range(3):
                    resp = await client.get(
                        f"https://r.jina.ai/{url}",
                        headers={
                            "Accept": "text/plain",
                            "User-Agent": "MilTrack/1.0 (military-aviation-tracker)",
                        },
                    )
                    if resp.status_code == 429:
                        wait = 5 * (attempt + 1)
                        logger.debug("Jina 429 for %s — retrying in %ds", url[:60], wait)
                        await asyncio.sleep(wait)
                        continue
                    break

                if resp.status_code != 200:
                    logger.debug("Jina Reader returned %d for %s", resp.status_code, url[:60])
                    return None

                text = resp.text.strip()
                if len(text) > 4000:
                    text = text[:4000] + "\n[truncated]"

                return {"url": url, "text": text}

        except Exception as e:
            logger.debug("Jina Reader failed for %s: %s", url[:60], e)
            return None


# ---------------------------------------------------------------------------
# Stage 3 — Databricks LLM analysis
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a military intelligence analyst specializing in Middle East conflicts.
You work for MilTrack, a military aviation tracking platform.

MilTrack tracks support aircraft via ADS-B: tankers (KC-135, KC-46), AWACS (E-3),
transports (C-17, C-130), reconnaissance (RC-135, P-8), and drones (MQ-9, RQ-4).
Fighter jets (F-35, F-15, F-16) are NOT visible because they fly transponders-off.

Analyze the articles below. Return a JSON array where each element has:
{
  "title": "original title",
  "url": "original URL",
  "relevance_score": <0-100>,
  "category": "<airstrike|deployment|naval|intelligence|diplomatic|force_posture|humanitarian|other>",
  "entities": {
    "countries": ["US","IL",...],
    "weapons_platforms": ["KC-135","F-35",...],
    "actors": ["IDF","IRGC","CENTCOM",...],
    "locations": ["Strait of Hormuz",...]
  },
  "summary": "2-3 sentence intelligence summary focusing on operational significance",
  "map_connection": "What observable support aircraft activity this explains, or null"
}

Scoring guide:
  80-100 = active military operations (strikes, engagements, force movements)
  50-79  = force posture changes, exercises, credible threats
  25-49  = diplomatic/political with military implications
  0-24   = tangentially related (exclude these)

Return ONLY the JSON array. No markdown fences, no commentary."""


def _llm_configured() -> bool:
    """Check if Databricks LLM credentials are available.

    True when SDK auth is initialized OR a PAT is set, and we have
    either an AI Gateway endpoint URL or a workspace host.
    """
    has_auth = bool(_databricks_config) or bool(os.getenv("DATABRICKS_TOKEN"))
    endpoint_url = os.getenv("DATABRICKS_ENDPOINT_URL", "")
    host = os.getenv("DATABRICKS_HOST", "") or (_databricks_host or "")
    return bool(has_auth and (endpoint_url or host))


def _get_models() -> list[str]:
    """Parse comma-separated model list from env. First model is primary."""
    raw = os.getenv("DATABRICKS_LLM_MODEL", "databricks-claude-opus-4-6")
    return [m.strip() for m in raw.split(",") if m.strip()]


async def _call_llm(system_prompt: str, user_msg: str, max_tokens: int = 4096) -> list[dict]:
    """Send a chat completion to Databricks with automatic model fallback.

    Tries each model in DATABRICKS_LLM_MODEL (comma-separated) in order.
    On 429 rate limit, immediately falls back to the next model instead of waiting.
    Auth: uses Databricks SDK unified auth (service principal in Apps, PAT locally).
    """
    endpoint_url = os.getenv("DATABRICKS_ENDPOINT_URL", "")
    host = (os.getenv("DATABRICKS_HOST", "") or _databricks_host or "").rstrip("/")
    models = _get_models()

    using_gateway = bool(endpoint_url)

    content = ""
    try:
        auth_headers = _get_auth_headers()
        async with httpx.AsyncClient(timeout=120.0) as client:
            for i, model in enumerate(models):
                url = endpoint_url or f"{host}/serving-endpoints/{model}/invocations"

                resp = await client.post(
                    url,
                    headers={**auth_headers, "Content-Type": "application/json"},
                    json={
                        **({"model": model} if using_gateway else {}),
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_msg},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.1,
                    },
                )

                if resp.status_code == 429:
                    remaining = [m for m in models[i + 1:]]
                    if remaining:
                        logger.warning(
                            "Model '%s' rate limited (429) — falling back to '%s'",
                            model, remaining[0],
                        )
                        continue
                    logger.warning("Model '%s' rate limited and no fallbacks left — waiting 30s", model)
                    await asyncio.sleep(30)
                    auth_headers = _get_auth_headers()
                    resp = await client.post(
                        url,
                        headers={**auth_headers, "Content-Type": "application/json"},
                        json={
                            **({"model": model} if using_gateway else {}),
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_msg},
                            ],
                            "max_tokens": max_tokens,
                            "temperature": 0.1,
                        },
                    )
                    if resp.status_code == 429:
                        logger.error("All models exhausted + retry failed")
                        return []

                if resp.status_code != 200:
                    body = resp.text[:500]
                    logger.error("Databricks %d from %s (model=%s) — %s", resp.status_code, url[:80], model, body)
                    return []

                logger.info("LLM response from model '%s'", model)
                data = resp.json()
                break
            else:
                logger.error("No models available")
                return []

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        parsed = json.loads(content)
        if not isinstance(parsed, list):
            parsed = [parsed]
        return parsed

    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON: %s — raw: %.200s", e, content)
        return []
    except Exception as e:
        logger.error("Databricks LLM call failed: %s: %s", type(e).__name__, e)
        return []


async def llm_analyze(articles: list[dict]) -> list[dict]:
    """Analyze news articles using Databricks AI Gateway."""
    if not _llm_configured():
        logger.info("Databricks not configured — returning unanalyzed articles")
        return [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "relevance_score": 50,
                "category": "other",
                "summary": (a.get("description") or a.get("text", ""))[:250],
            }
            for a in articles
        ]

    parts: list[str] = []
    for i, art in enumerate(articles, 1):
        body = art.get("text") or art.get("description") or ""
        parts.append(
            f"--- Article {i} ---\n"
            f"Title: {art.get('title', '?')}\n"
            f"URL: {art.get('url', '')}\n"
            f"{body[:2500]}\n"
        )
    user_msg = f"Analyze these {len(articles)} articles:\n\n" + "\n".join(parts)

    result = await _call_llm(_SYSTEM_PROMPT, user_msg, max_tokens=8192)
    logger.info("LLM analyzed %d articles → %d results", len(articles), len(result))
    return result


# ---------------------------------------------------------------------------
# Stage 4 — LLM conflict event enrichment (GDELT → Claude)
# ---------------------------------------------------------------------------

_CONFLICT_SYSTEM_PROMPT = """\
You are a military intelligence analyst. You receive raw GDELT conflict event data \
from the Middle East. These events are machine-coded from news articles and contain \
many duplicates, false positives, and vague descriptions.

Your task: deduplicate and enrich these into verified military incidents.

For each batch of raw events, group duplicates that describe the same real-world incident \
(same date, nearby coordinates, similar actors/descriptions). Then for each unique incident, return:

{
  "title": "Concise human-readable incident title (e.g. 'IDF airstrike on southern Beirut suburb')",
  "event_date": "YYYY-MM-DDTHH:MM:SS (preserve the original timestamp precision)",
  "latitude": <best lat from the group>,
  "longitude": <best lon from the group>,
  "country": "country name",
  "location": "specific location name",
  "event_type": "<airstrike|shelling|ground_battle|missile_attack|drone_strike|naval_engagement|ied_vbied|armed_clash|other>",
  "actor1": "attacking party",
  "actor2": "target/defending party or null (use 'United States' or 'US' when US forces are the target or US casualties occur)",
  "severity": <1-10>,
  "confidence": <0.0-1.0>,
  "attack_direction": "<to_iran|from_iran|internal|other>",
  "fatalities": <estimated number or null>,
  "summary": "2-3 sentence description of what happened and its significance",
  "source_url": "the most relevant source_url from the grouped raw events, or null"
}

Attack direction guide:
  "to_iran"   = strikes/attacks TARGETING Iran or Iranian forces (by US, Israel, coalition)
  "from_iran" = strikes/attacks BY Iran or Iranian proxies (Hezbollah, Houthis, IRGC) against others
  "internal"  = internal unrest, protests, or clashes within a single country
  "other"     = does not clearly fit the Iran axis (e.g. Israel-Palestine, unrelated regional events)

Severity guide:
  9-10 = major military operation (large-scale strikes, major battle, 20+ casualties)
  7-8  = significant attack (targeted strike, multiple casualties)
  5-6  = moderate incident (smaller skirmish, few casualties)
  3-4  = minor incident (small arms fire, harassment, no confirmed casualties)
  1-2  = unverified/uncertain report

Confidence guide:
  0.9-1.0 = confirmed by multiple sources, clearly military
  0.7-0.8 = likely real, reported by credible source
  0.5-0.6 = plausible but only single source or vague details
  0.0-0.4 = probably noise or misclassified (EXCLUDE these — do not return them)

Rules:
- EXCLUDE events with confidence < 0.5 (noise, diplomatic events, etc.)
- Group duplicates aggressively — 10 reports about the same strike become 1 incident
- Pick the most precise coordinates from the group
- Infer fatalities from mentions/descriptions when possible, otherwise null
- When US military/forces are attacked or US casualties occur, set actor2 to "United States" or "US"
- Return ONLY the JSON array. No markdown fences, no commentary."""


async def _call_llm_text(system_prompt: str, user_msg: str, max_tokens: int = 4096) -> str:
    """Like _call_llm but returns the raw text response instead of parsed JSON."""
    endpoint_url = os.getenv("DATABRICKS_ENDPOINT_URL", "")
    host = (os.getenv("DATABRICKS_HOST", "") or _databricks_host or "").rstrip("/")
    models = _get_models()
    using_gateway = bool(endpoint_url)

    try:
        auth_headers = _get_auth_headers()
        async with httpx.AsyncClient(timeout=120.0) as client:
            for i, model in enumerate(models):
                url = endpoint_url or f"{host}/serving-endpoints/{model}/invocations"
                resp = await client.post(
                    url,
                    headers={**auth_headers, "Content-Type": "application/json"},
                    json={
                        **({"model": model} if using_gateway else {}),
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_msg},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.2,
                    },
                )
                if resp.status_code == 429:
                    remaining = [m for m in models[i + 1:]]
                    if remaining:
                        logger.warning("SITREP model '%s' rate limited — falling back to '%s'", model, remaining[0])
                        continue
                    logger.warning("SITREP model '%s' rate limited, no fallbacks — waiting 30s", model)
                    await asyncio.sleep(30)
                    auth_headers = _get_auth_headers()
                    resp = await client.post(
                        url,
                        headers={**auth_headers, "Content-Type": "application/json"},
                        json={
                            **({"model": model} if using_gateway else {}),
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_msg},
                            ],
                            "max_tokens": max_tokens,
                            "temperature": 0.2,
                        },
                    )
                    if resp.status_code == 429:
                        logger.error("SITREP: all models exhausted + retry failed")
                        return ""
                if resp.status_code != 200:
                    logger.error("SITREP LLM %d from %s — %s", resp.status_code, url[:80], resp.text[:300])
                    return ""
                data = resp.json()
                break
            else:
                return ""

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        return content

    except Exception as e:
        logger.error("SITREP LLM call failed: %s: %s", type(e).__name__, e)
        return ""


# ---------------------------------------------------------------------------
# Stage 5 — AI Situation Report (SITREP)
# ---------------------------------------------------------------------------

_SITREP_SYSTEM_PROMPT = """\
You are a senior military intelligence analyst producing a SITUATION REPORT (SITREP) \
for a live military aviation tracking platform called MilTrack.

You will receive three data feeds:
1. AIRCRAFT — currently tracked military support aircraft (tankers, AWACS, transports, \
recon, drones). Fighter jets are NOT visible (transponders off).
2. STRIKES — recent verified conflict events in the Middle East (AI-verified from GDELT).
3. NEWS — AI-curated intelligence articles from the last 24 hours.

Produce a structured SITREP in this exact JSON format:
{
  "threat_level": "<CRITICAL|HIGH|MODERATE|LOW>",
  "executive_summary": "2-3 sentence overview of the current situation",
  "aircraft_situation": "2-4 sentences about notable aircraft activity, patterns, \
what their presence/absence suggests about operations",
  "conflict_situation": "2-4 sentences about recent strikes, escalation/de-escalation \
trends, geographic patterns",
  "key_developments": "2-3 bullet points from the latest news (use • for bullets)",
  "assessment": "2-3 sentences forward-looking analysis — what to watch for, \
likely next developments",
  "connections": "1-2 sentences linking aircraft activity to strikes/news if possible, \
or null if no clear link"
}

Threat level guide:
  CRITICAL = active large-scale military operations, major escalation
  HIGH     = significant military activity, elevated tensions, recent strikes
  MODERATE = routine military presence, localized incidents, stable tensions
  LOW      = minimal activity, de-escalation signals

Rules:
- Be specific: reference actual aircraft types, locations, event details from the data
- Don't speculate wildly — base analysis on the data provided
- If aircraft data is sparse, note that and adjust assessment accordingly
- If no strikes in 24h, note the calm and what it might mean
- If a feed is empty (e.g. "None currently tracked", "No strikes", "No intelligence articles"), \
write a brief factual note like "Data pending" or "Awaiting refresh" — never say "sources return null" \
or "null" or similar technical terms
- Write in professional military intelligence style, concise and direct
- Return ONLY the JSON object. No markdown fences, no commentary."""


class SitrepResponse(BaseModel):
    threat_level: str = "UNKNOWN"
    executive_summary: str = ""
    aircraft_situation: str = ""
    conflict_situation: str = ""
    key_developments: str = ""
    assessment: str = ""
    connections: str | None = None
    generated_at: str = ""
    generated_at_ts: float | None = None  # unix timestamp for "New" badge
    cached: bool = False


_sitrep_cache: dict[str, tuple[float, SitrepResponse]] = {}
CACHE_TTL_SITREP = 7200  # 2 hours


async def generate_sitrep(
    aircraft: list[dict],
    strikes: list[dict],
    intel_articles: list[dict],
) -> SitrepResponse | None:
    """Generate an AI situation report from all available data sources."""
    if not _llm_configured():
        logger.info("SITREP: Databricks not configured")
        return None

    # Build aircraft summary (all aircraft, compressed)
    ac_lines: list[str] = []
    for ac in aircraft:
        flight = ac.get("flight") or "?"
        ac_type = ac.get("aircraft_type") or "?"
        cc = ac.get("country_code") or "?"
        lat = ac.get("lat")
        lon = ac.get("lon")
        alt = ac.get("alt_baro") or "?"
        spd = ac.get("ground_speed") or "?"
        desc = ac.get("description") or ""
        pos = f"lat={lat:.1f} lon={lon:.1f}" if lat and lon else "pos=unknown"
        ac_lines.append(f"  {flight} | {ac_type} ({desc}) | {cc} | {pos} | alt={alt} spd={spd}")

    ac_block = f"AIRCRAFT ({len(aircraft)} tracked):\n" + ("\n".join(ac_lines) if ac_lines else "  None currently tracked")

    # Build strikes summary (last 24h, latest first)
    recent_strikes = [s for s in strikes if (s.get("hours_ago") or 999) <= 24]
    recent_strikes.sort(key=lambda s: s.get("hours_ago") or 999)  # ascending = most recent first
    st_lines: list[str] = []
    for s in recent_strikes[:25]:
        title = s.get("title") or s.get("event_type") or "?"
        loc = s.get("location") or s.get("country") or "?"
        sev = s.get("severity") or "?"
        direction = s.get("attack_direction") or "?"
        hours = s.get("hours_ago")
        ago = f"{hours:.0f}h ago" if hours is not None else "?"
        actors = f"{s.get('actor1', '?')} vs {s.get('actor2', '?')}"
        st_lines.append(f"  [{sev}/10] {title} — {loc} — {actors} — {direction} — {ago}")

    st_block = f"STRIKES (last 24h: {len(recent_strikes)} events):\n" + ("\n".join(st_lines) if st_lines else "  No strikes in the last 24 hours")

    # Build news summary (top articles)
    news_lines: list[str] = []
    for art in intel_articles[:10]:
        title = art.get("title") or "?"
        score = art.get("relevance_score") or art.get("relevance") or 0
        cat = art.get("category") or "?"
        summary = art.get("summary") or ""
        news_lines.append(f"  [{score}] [{cat}] {title}\n    {summary[:200]}")

    news_block = f"NEWS ({len(intel_articles)} articles):\n" + ("\n".join(news_lines) if news_lines else "  No intelligence articles available")

    user_msg = f"{ac_block}\n\n{st_block}\n\n{news_block}"

    raw = await _call_llm_text(_SITREP_SYSTEM_PROMPT, user_msg, max_tokens=2048)
    if not raw:
        return None

    # Parse JSON
    try:
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

        parsed = json.loads(raw)

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        ts = now.timestamp()
        return SitrepResponse(
            threat_level=parsed.get("threat_level", "UNKNOWN"),
            executive_summary=parsed.get("executive_summary", ""),
            aircraft_situation=parsed.get("aircraft_situation", ""),
            conflict_situation=parsed.get("conflict_situation", ""),
            key_developments=parsed.get("key_developments", ""),
            assessment=parsed.get("assessment", ""),
            connections=parsed.get("connections"),
            generated_at=now.strftime("%Y-%m-%d %H:%M UTC"),
            generated_at_ts=ts,
        )
    except json.JSONDecodeError as e:
        logger.error("SITREP: invalid JSON from LLM: %s — raw: %.300s", e, raw)
        return None


@router.get("/sitrep", response_model=SitrepResponse)
async def get_sitrep():
    """Get the latest AI-generated situation report."""
    if "sitrep" in _sitrep_cache:
        ts, report = _sitrep_cache["sitrep"]
        if time.time() - ts < CACHE_TTL_SITREP:
            report.cached = True
            report.generated_at_ts = ts
            return report

    return SitrepResponse(
        threat_level="PENDING",
        executive_summary="Situation report is being generated. Please wait for the next refresh cycle.",
        generated_at="",
    )


CONFLICT_BATCH_SIZE = 80  # smaller batches avoid timeout/rate limits


async def llm_enrich_conflicts(raw_events: list[dict]) -> list[dict]:
    """Deduplicate and enrich raw GDELT events using Claude.

    Processes in batches to avoid timeout and rate limits.
    Returns a list of verified, enriched incident dicts.
    """
    if not _llm_configured():
        logger.info("Databricks not configured — returning raw GDELT events unenriched")
        return []

    if not raw_events:
        return []

    all_enriched: list[dict] = []
    for start in range(0, len(raw_events), CONFLICT_BATCH_SIZE):
        batch = raw_events[start : start + CONFLICT_BATCH_SIZE]
        parts: list[str] = []
        for i, ev in enumerate(batch, 1):
            parts.append(
                f"[{i}] date={ev.get('event_date','?')} "
                f"type={ev.get('event_type','?')} "
                f"actor1={ev.get('actor1','?')} actor2={ev.get('actor2','?')} "
                f"loc={ev.get('location','?')} country={ev.get('country','?')} "
                f"lat={ev.get('latitude','?')} lon={ev.get('longitude','?')} "
                f"source_url={ev.get('source_url','')}"
            )
        user_msg = (
            f"Deduplicate and enrich these {len(batch)} raw GDELT conflict events "
            f"into verified military incidents:\n\n" + "\n".join(parts)
        )
        result = await _call_llm(_CONFLICT_SYSTEM_PROMPT, user_msg, max_tokens=8192)
        if result:
            all_enriched.extend(result)
        await asyncio.sleep(2)  # stagger batches to reduce rate limit pressure

    logger.info("LLM enriched %d raw events → %d verified incidents", len(raw_events), len(all_enriched))
    return all_enriched


# ---------------------------------------------------------------------------
# Full pipeline orchestration
# ---------------------------------------------------------------------------


async def run_intel_pipeline() -> tuple[list[IntelArticle], dict]:
    """Run the full pipeline: Search → Extract → Analyze → Curated feed."""
    status: dict[str, str] = {
        "search": "pending",
        "extract": "pending",
        "analyze": "pending",
    }

    if not is_configured():
        status["search"] = "not configured"
        return [], status

    # Stage 1: Search
    search_tasks = [brave_search(q, count=8) for q in _SEARCH_QUERIES]
    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    all_results: list[dict] = []
    seen_urls: set[str] = set()
    for result in search_results:
        if isinstance(result, list):
            for item in result:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(item)

    status["search"] = f"{len(all_results)} articles found"
    if not all_results:
        status["search"] = "no results"
        return [], status

    # Stage 2: Extract full text (top N articles)
    to_extract = all_results[:JINA_MAX_ARTICLES]
    sem = asyncio.Semaphore(JINA_CONCURRENCY)
    extract_tasks = [jina_extract(a["url"], sem) for a in to_extract]
    extracted = await asyncio.gather(*extract_tasks, return_exceptions=True)

    articles_for_llm: list[dict] = []
    full_text_count = 0
    for i, result in enumerate(extracted):
        article = to_extract[i].copy()
        if isinstance(result, dict) and result.get("text"):
            article["text"] = result["text"]
            full_text_count += 1
        articles_for_llm.append(article)

    status["extract"] = f"{full_text_count}/{len(to_extract)} full text"

    # Stage 3: LLM analysis
    analyzed = await llm_analyze(articles_for_llm)
    status["analyze"] = f"{len(analyzed)} articles scored"

    # Build response models
    intel_articles: list[IntelArticle] = []
    for a in analyzed:
        score = a.get("relevance_score", 0)
        if score < 25:
            continue

        url = a.get("url", "")
        original = next((r for r in all_results if r.get("url") == url), {})

        hours_ago = _parse_age_to_hours(original.get("published"))
        intel_articles.append(IntelArticle(
            title=a.get("title", "Unknown"),
            url=url or None,
            published=original.get("published"),
            source_domain=original.get("domain"),
            relevance_score=score,
            category=a.get("category"),
            entities=a.get("entities"),
            summary=a.get("summary"),
            map_connection=a.get("map_connection"),
            hours_ago=hours_ago,
        ))

    # Sort by latest first (smallest hours_ago = most recent)
    intel_articles.sort(key=lambda x: (x.hours_ago or 999, -x.relevance_score))

    logger.info(
        "Intel pipeline: %d curated (searched %d, extracted %d/%d, analyzed %d)",
        len(intel_articles), len(all_results),
        full_text_count, len(to_extract), len(analyzed),
    )
    return intel_articles, status


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


@router.get("/intel", response_model=IntelResponse)
async def get_intel_feed(limit: int = Query(20, ge=1, le=100)):
    """AI-curated military intelligence feed."""
    if not is_configured():
        return IntelResponse(
            articles=[], total=0, cached=False,
            pipeline_status={"search": "BRAVE_SEARCH_API_KEY not set"},
        )

    cached = _get_cached_with_ts("intel_feed", CACHE_TTL_INTEL)
    if cached is not None:
        ts, (articles, status) = cached
        return IntelResponse(
            articles=articles[:limit], total=len(articles),
            cached=True, pipeline_status=status, updated_at=ts,
        )

    articles, status = await run_intel_pipeline()
    if articles:
        _set_cached("intel_feed", (articles, status))

    return IntelResponse(
        articles=articles[:limit], total=len(articles),
        cached=False, pipeline_status=status, updated_at=time.time(),
    )
