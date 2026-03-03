# MilTrack — Executive Overview

**Aviation-focused military intelligence. One dashboard. Trusted sources. AI-validated.**

---

## What It Is

MilTrack is a real-time military aviation intelligence platform for the Middle East theatre. It combines live aircraft tracking, conflict events, and curated news into a single operational picture—so you don’t have to juggle Flightradar24, GDELT, and a dozen news tabs.

**Single point of view.** One map. One scoring framework. One place to see what’s happening.

---

## Why Trust It

### Trusted Data Sources

| Source | Type | Trust Level |
|-------|------|-------------|
| **adsb.lol** | Live ADS-B (military) | Primary — real-time transponder data |
| **OpenSky Network** | ADS-B (supplement) | Academic/research network |
| **GDELT Project** | Conflict events | Machine-coded from news, updated every 15 min |
| **UCDP** | Death tolls | Uppsala Conflict Data Program — peer-reviewed, verified |
| **RSS feeds** | News | Defence, regional, and trusted outlets (BBC, Al Jazeera, Jerusalem Post, Defense News, Times of Israel, NY Times) |

### AI-Curated & Validated

Three AI pipelines (Databricks LLM) run on top of raw data:

1. **AI Intel** — Brave Search finds military news → Jina extracts full text → LLM scores relevance, categorizes, and summarizes. Only articles that pass military relevance are shown.
2. **Conflict enrichment** — Raw GDELT events (often duplicates) → LLM deduplicates, enriches, assigns titles, severity, and fatalities. Low-confidence events are filtered out.
3. **SITREP** — LLM synthesizes aircraft, strikes, and news into a single situation report with threat level and assessment.

**Result:** Raw feeds are filtered, validated, and summarized by AI before they reach you.

---

## Aviation Focus

- **Military aircraft only** — Filtered by ICAO hex, not civilian traffic.
- **Role classification** — Tanker, transport, radar/command, reconnaissance, UAV, fighter, helicopter.
- **Conflict involvement scoring** — 0–100 per aircraft based on proximity to Iran, country, role, and behaviour.
- **Flight trails** — Full path since takeoff, altitude profile, live telemetry.
- **Military bases** — Overlay of known airbases and installations (OpenStreetMap).

---

## For Technical Managers

- **No database** — In-memory caching, TTL-based.
- **API-first** — FastAPI backend; REST endpoints for aircraft, strikes, bases, news, intel, SITREP, death toll.
- **Deployable** — Runs locally or on Databricks Apps.
- **Extensible** — Add new data sources or AI pipelines without changing the core UI.

---

## One-Liner Pitch

> MilTrack = trusted military aviation data + AI curation + single dashboard. One place to see what’s happening in the Middle East theatre.
