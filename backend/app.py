"""MilTrack — FastAPI application entry point.

Serves the tracker API and the built React frontend as static files.
"""

import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(Path(__file__).parent.parent / ".env")
from fastapi.staticfiles import StaticFiles

from .tracker import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="MilTrack", description="Live military aircraft tracker & conflict monitor")
app.include_router(router, prefix="/api")

STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    logger.info("Serving frontend from %s", STATIC_DIR)
else:
    logger.warning("No frontend build at %s — run: cd frontend && bun run build", STATIC_DIR)

    @app.get("/")
    async def root():
        return {"status": "running", "hint": "Frontend not built yet. Run: cd frontend && bun run build"}
