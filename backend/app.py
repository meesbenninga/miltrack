"""MilTrack — FastAPI application entry point.

Serves the tracker API and the built React frontend as static files.
Runs background tasks to keep news and conflict data warm.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_path)

from fastapi.staticfiles import StaticFiles

from .tracker import (
    router as tracker_router,
    _fetch_all_news,
    _set_cache,
    _get_cached as _get_tracker_cached,
    _fetch_gdelt_event_files,
    _compute_hours_ago,
    StrikeEvent,
    CACHE_TTL_STRIKES,
    CACHE_TTL_AIRCRAFT,
)
from .death_toll import router as death_toll_router
from .intel import (
    router as intel_router,
    run_intel_pipeline,
    is_configured as intel_configured,
    _set_cached as _set_intel_cached,
    _get_cached as _get_intel_cached,
    llm_enrich_conflicts,
    _llm_configured,
    generate_sitrep,
    _sitrep_cache,
    CACHE_TTL_INTEL,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEWS_REFRESH_INTERVAL = 300      # 5 min
INTEL_REFRESH_INTERVAL = 7200    # 2 hours (keeps Brave API under free tier)
STRIKES_REFRESH_INTERVAL = 7200  # 2 hours (aligned with intel pipeline)
SITREP_REFRESH_INTERVAL = 7200   # 2 hours


async def _background_news():
    """Refresh RSS news."""
    await asyncio.sleep(5)
    while True:
        try:
            items, ok, fail = await _fetch_all_news()
            _set_cache("mil_news", (items, ok, fail))
            logger.info("Background news: %d items (%d ok, %d failed)", len(items), ok, fail)
        except Exception as e:
            logger.error("Background news failed: %s", e)

        await asyncio.sleep(NEWS_REFRESH_INTERVAL)


async def _background_strikes():
    """Fetch GDELT events and enrich through LLM."""
    await asyncio.sleep(90)  # 90s — run before SITREP, after intel starts
    while True:
        try:
            raw_events = await _fetch_gdelt_event_files(days=90, limit=500)
            logger.info("Background strikes: fetched %d raw GDELT events", len(raw_events))

            # Cache raw immediately so map shows data while enrichment runs
            raw_with_hours = _compute_hours_ago(raw_events)
            _set_cache("strikes:90", raw_with_hours)
            _set_cache("strikes:7", [e for e in raw_with_hours if (e.hours_ago or 999) <= 168])
            _set_cache("strikes:30", [e for e in raw_with_hours if (e.hours_ago or 999) <= 720])
            _set_cache("strikes:180", raw_with_hours)

            if raw_events and _llm_configured():
                raw_dicts = [ev.model_dump() for ev in raw_events]
                enriched = await llm_enrich_conflicts(raw_dicts)

                if enriched:
                    enriched_events: list[StrikeEvent] = []
                    for inc in enriched:
                        conf = inc.get("confidence", 0.5)
                        if conf < 0.5:
                            continue
                        enriched_events.append(StrikeEvent(
                            event_id=f"llm-{len(enriched_events)}",
                            event_date=inc.get("event_date"),
                            event_type=inc.get("event_type"),
                            sub_event_type=None,
                            actor1=inc.get("actor1"),
                            actor2=inc.get("actor2"),
                            country=inc.get("country"),
                            admin1=None,
                            admin2=None,
                            location=inc.get("location"),
                            latitude=inc.get("latitude"),
                            longitude=inc.get("longitude"),
                            fatalities=inc.get("fatalities"),
                            notes=None,
                            source="GDELT → AI verified",
                            title=inc.get("title"),
                            summary=inc.get("summary"),
                            severity=inc.get("severity"),
                            confidence=conf,
                            attack_direction=inc.get("attack_direction"),
                            source_url=inc.get("source_url"),
                        ))

                    enriched_events.sort(key=lambda e: e.event_date or "", reverse=True)
                    enriched_events = _compute_hours_ago(enriched_events)
                    _set_cache("strikes:90", enriched_events)
                    _set_cache("strikes:7", [e for e in enriched_events if (e.hours_ago or 999) <= 168])
                    _set_cache("strikes:30", [e for e in enriched_events if (e.hours_ago or 999) <= 720])
                    _set_cache("strikes:180", enriched_events)
                    logger.info(
                        "Background strikes: LLM enriched %d raw → %d verified incidents",
                        len(raw_events), len(enriched_events),
                    )
                else:
                    logger.info("Background strikes: LLM returned empty, keeping %d raw events", len(raw_events))
            else:
                logger.info("Background strikes: LLM not configured, using raw events")

        except Exception as e:
            logger.error("Background strikes failed: %s", e)

        await asyncio.sleep(STRIKES_REFRESH_INTERVAL)


async def _background_intel():
    """Refresh AI intelligence pipeline (Brave → Jina → Databricks LLM)."""
    await asyncio.sleep(15)  # stagger after news
    if not intel_configured():
        logger.info("Intel pipeline not configured — skipping background refresh")
        return
    while True:
        try:
            articles, status = await run_intel_pipeline()
            if articles:
                _set_intel_cached("intel_feed", (articles, status))
            logger.info("Background intel: %d articles — %s", len(articles), status)
        except Exception as e:
            logger.error("Background intel failed: %s", e)

        await asyncio.sleep(INTEL_REFRESH_INTERVAL)


async def _background_sitrep():
    """Generate AI situation report from all cached data sources."""
    await asyncio.sleep(360)  # wait 6 min — let strikes enrichment complete (batched LLM calls)
    if not _llm_configured():
        logger.info("SITREP: Databricks not configured — skipping")
        return
    while True:
        try:
            ac_data = _get_tracker_cached("mil_aircraft", 300)
            aircraft_dicts = [ac.model_dump() for ac in ac_data] if ac_data else []

            strikes_data = _get_tracker_cached("strikes:90", CACHE_TTL_STRIKES)
            strikes_dicts = [s.model_dump() for s in strikes_data] if strikes_data else []

            intel_data = _get_intel_cached("intel_feed", CACHE_TTL_INTEL)
            if intel_data:
                articles_list, _ = intel_data
                intel_dicts = [a.model_dump() for a in articles_list] if articles_list else []
            else:
                intel_dicts = []

            report = await generate_sitrep(aircraft_dicts, strikes_dicts, intel_dicts)
            if report:
                _sitrep_cache["sitrep"] = (time.time(), report)
                logger.info(
                    "SITREP generated: %s — %d aircraft, %d strikes, %d articles",
                    report.threat_level, len(aircraft_dicts), len(strikes_dicts), len(intel_dicts),
                )
            else:
                logger.warning("SITREP generation returned None")

        except Exception as e:
            logger.error("Background SITREP failed: %s", e)

        await asyncio.sleep(SITREP_REFRESH_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    news_task = asyncio.create_task(_background_news())
    intel_task = asyncio.create_task(_background_intel())
    strikes_task = asyncio.create_task(_background_strikes())
    sitrep_task = asyncio.create_task(_background_sitrep())
    yield
    for t in [news_task, intel_task, strikes_task, sitrep_task]:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="MilTrack",
    description="Live military aircraft tracker & conflict monitor",
    lifespan=lifespan,
)


@app.get("/api/health")
def health():
    """Debug: verify env vars are injected (no secrets exposed)."""
    static_exists = (Path(__file__).parent.parent / "frontend" / "dist").exists()
    return {
        "status": "ok",
        "brave_configured": bool(os.getenv("BRAVE_SEARCH_API_KEY")),
        "llm_configured": bool(os.getenv("DATABRICKS_TOKEN")) and (bool(os.getenv("DATABRICKS_ENDPOINT_URL")) or bool(os.getenv("DATABRICKS_HOST"))),
        "frontend_dist_exists": static_exists,
    }


@app.get("/api/debug/llm-test")
async def debug_llm_test():
    """Diagnostic: make a minimal LLM call and return status/error."""
    import httpx
    host = (os.getenv("DATABRICKS_HOST", "") or "").rstrip("/")
    token = os.getenv("DATABRICKS_TOKEN", "")
    if not host or not token:
        return {"ok": False, "error": "DATABRICKS_HOST or DATABRICKS_TOKEN not set"}
    url = f"{host}/serving-endpoints/databricks-claude-opus-4-6/invocations"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "messages": [{"role": "user", "content": "Reply with one word: OK"}],
                    "max_tokens": 10,
                },
            )
        return {
            "ok": r.status_code == 200,
            "status_code": r.status_code,
            "body_preview": r.text[:500] if r.text else "",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


app.include_router(tracker_router, prefix="/api")
app.include_router(intel_router, prefix="/api")
app.include_router(death_toll_router, prefix="/api")

STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    logger.info("Serving frontend from %s", STATIC_DIR)
else:
    logger.warning("No frontend build at %s — run: cd frontend && bun run build", STATIC_DIR)

    @app.get("/")
    async def root():
        return {"status": "running", "hint": "Frontend not built yet. Run: cd frontend && bun run build"}
