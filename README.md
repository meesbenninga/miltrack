# MilTrack — Military Aviation Intelligence Platform

Real-time military aircraft tracking and conflict analysis for the Middle East theatre, combining multiple open-source intelligence (OSINT) data feeds into a single operational picture.

## What It Does

MilTrack aggregates live ADS-B transponder data, conflict event databases, military base locations, and news feeds into an interactive map with analytical overlays. It focuses on aerial military activity in and around Iran, Israel, and the broader Middle East.

**Core capabilities:**

- **Live military aircraft tracking** — Positions, headings, altitudes, and speeds updated every few seconds from two independent ADS-B networks.
- **Aircraft identification** — Automatic classification by role (tanker, transport, radar/command, reconnaissance, UAV, fighter, helicopter) with type-specific silhouette icons and Wikipedia-sourced descriptions.
- **Conflict involvement scoring** — A 0–100 score per aircraft based on proximity to Iran, operating country, mission-like flight behaviour, and aircraft role. Results are displayed in a sortable analyst table with per-item explanations.
- **Flight trail reconstruction** — Click any aircraft to see its full path since takeoff, altitude profile, and live telemetry in a Flightradar24-style side panel.
- **Strike/conflict events** — GDELT-sourced events plotted on the map with configurable time range (1–90 days).
- **Military base overlay** — Known airbases and military installations from OpenStreetMap.
- **News feed** — Aggregated RSS from defence and regional outlets (Jerusalem Post, Times of Israel, Defense News, Al Jazeera, BBC, NY Times).
- **Country overlays** — Iran and Israel highlighted with distinct GeoJSON borders for spatial context.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   Frontend                       │
│  React 19 · TypeScript · Leaflet · Vite          │
│                                                   │
│  ┌──────────┐ ┌────────────┐ ┌────────────────┐  │
│  │ Live Map │ │ Side Panel │ │ Conflict Table │  │
│  │ (Leaflet)│ │ (FR24-like)│ │ (scored list)  │  │
│  └──────────┘ └────────────┘ └────────────────┘  │
│                       │                           │
│              /api proxy (Vite)                    │
└───────────────────────┬─────────────────────────┘
                        │
┌───────────────────────┴─────────────────────────┐
│                   Backend                        │
│  FastAPI · Python 3.12 · httpx · uvicorn         │
│                                                   │
│  Endpoints:                                       │
│    GET /api/aircraft       live military positions│
│    GET /api/aircraft/trail flight path history    │
│    GET /api/aircraft/info  type info (Wikipedia)  │
│    GET /api/strikes        conflict events (GDELT)│
│    GET /api/bases          military installations │
│    GET /api/news           defence RSS feeds      │
│    GET /api/health         system status           │
│                                                   │
│  In-memory caching · OAuth2 token management      │
│  Exponential backoff · Background polling          │
└───────────────────────┬─────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  ┌──────────┐   ┌──────────┐   ┌──────────────┐
  │ adsb.lol │   │ OpenSky  │   │    GDELT     │
  │ (primary │   │ Network  │   │  (conflict   │
  │  mil ADS │   │ (suppl.  │   │   events)    │
  │  -B feed)│   │  ADS-B)  │   │              │
  └──────────┘   └──────────┘   └──────────────┘
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  ┌──────────┐   ┌──────────┐   ┌──────────────┐
  │ Overpass │   │Wikipedia │   │  RSS Feeds   │
  │  (mil.   │   │ (aircraft│   │ (6 defence & │
  │  bases)  │   │  info)   │   │  news sites) │
  └──────────┘   └──────────┘   └──────────────┘
```

**No database required.** All state is held in-memory with TTL-based caching. The backend acts as a smart aggregation proxy that deduplicates, classifies, and enriches data from upstream sources.

### Data Flow

1. Backend polls adsb.lol (all military) and OpenSky Network (authenticated, filtered) on separate cache timers.
2. Aircraft from both sources are merged by ICAO hex, deduplicated, and enriched with country codes derived from ICAO address allocation blocks.
3. Frontend fetches merged results, applies client-side conflict scoring, and renders on the Leaflet map.
4. GDELT events, military bases, and news are fetched on-demand with longer cache intervals.

## Business Value vs. Individual Data Sources

| Capability | adsb.lol | OpenSky | Flightradar24 | GDELT | **MilTrack** |
|---|---|---|---|---|---|
| Military aircraft positions | Yes | Partial | Yes (paid) | — | **Yes** (merged, free) |
| Aircraft role classification | — | — | Basic | — | **Automatic** (8 categories) |
| Conflict involvement scoring | — | — | — | — | **Yes** (proximity + role) |
| Flight trail since takeoff | — | Yes (rate-limited) | Yes (paid) | — | **Yes** (merged sources) |
| Strike/conflict event overlay | — | — | — | Raw CSV | **Map-integrated** |
| Military base overlay | — | — | — | — | **Yes** (OSM-sourced) |
| Aggregated defence news | — | — | — | — | **Yes** (6 feeds) |
| Unified analyst dashboard | — | — | — | — | **Yes** |
| Country identification + flags | — | Partial | Yes | — | **Yes** (ICAO hex mapping) |
| Cost | Free | Free (limited) | $1,500+/yr | Free | **Free** |

**Key differentiators:**

- **Single pane of glass** — No need to switch between Flightradar24, ADS-B Exchange, GDELT, and news sites. Everything is on one map with one scoring framework.
- **Military-first filtering** — Unlike Flightradar24 which shows all aviation, MilTrack filters for military aircraft using ICAO hex allocation blocks and known military callsign patterns.
- **Conflict scoring** — No competing tool scores individual aircraft by likely involvement in an active conflict. This is unique analytical value.
- **Zero cost** — Built entirely on free/open APIs. Flightradar24 Business costs $1,500+/year and still doesn't offer conflict scoring or event overlays.
- **Extensible** — Designed for a Phase 2 AI agent backend (LLM-powered news analysis via Databricks Model Serving) to generate intelligence-style briefings from combined aircraft and event data.

## Quick Start

### Prerequisites

- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- [Bun](https://bun.sh/) (or Node.js 18+)
- Optional: OpenSky Network API credentials for higher rate limits

### Setup

```bash
# Backend
cd backend
cp ../.env.example ../.env          # add OpenSky credentials if available
uv sync
uv run uvicorn app:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
bun install
bun run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` requests to the backend.

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENSKY_CLIENT_ID` | No | OpenSky OAuth2 client ID (increases rate limits) |
| `OPENSKY_CLIENT_SECRET` | No | OpenSky OAuth2 client secret |

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, httpx, uvicorn |
| Frontend | React 19, TypeScript, Leaflet, Vite |
| Data sources | adsb.lol, OpenSky Network, GDELT, Overpass API, Wikipedia, RSS |
| Package managers | uv (Python), Bun (JS) |
| Deployment target | Databricks Apps (optional) |
