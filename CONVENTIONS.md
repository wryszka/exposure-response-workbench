# Conventions — Exposure & Event Response Workbench

> The hard build rules for this demo. Mirrors the Bricksurance standard (see `STANDARDS.md` → bricksurance-playbook `BUILD_AND_REVIEW.md` + `DESIGN_LANGUAGE.md`). Mirror, don't invent.

## What this is
A catastrophe **event-response** workbench for **Bricksurance SE**'s European property book: overlay live hazard
footprints (wildfire / flood / windstorm) onto insured property locations, show which properties are threatened
now (gross, and net of treaty), and alert the stakeholders who never log into a data platform. Promotes the
existing `exposure-management` roadmap tile in the actuarial-workbench hub to live.

## One spine, three peril adapters
Build the event-response mechanics **once** — one footprint model, one geospatial-intersection engine, one
exposure view, one alert path — and plug in **three adapters** that differ only in footprint source + distance
semantics. **Wildfire first and deepest.** Never fork the spine per peril.

| Peril | Live feed (P2) | Frozen/synthetic fallback |
|---|---|---|
| Wildfire | NASA FIRMS (near-real-time) + EFFIS (EU perimeters) | `2_event_footprint` WKT |
| Windstorm | MeteoAlarm (CAP, 38 EU met services) + NOAA NHC + IBTrACS | `2_event_footprint` WKT |
| Flood | GloFAS/EFAS forecast + Copernicus EMS event footprints | `2_event_footprint` WKT + synthetic fill |

Marketplace geo/property (CARTO, Precisely, MBI) is enrichment/basemap context — **verify listings in-workspace before banking on them**. The property book stays synthetic (no PII).

## Geospatial (the compute path) — CONFIRMED on warehouse a3b61648ea4809e3
- `ST_` functions require a **serverless / DBR 17.1+** warehouse (not SQL Classic). Use **GEOMETRY**, not GEOGRAPHY.
- Store hazard footprints + property points as WKT in **EPSG:4326** (`POINT(lon lat)` / `POLYGON((lon lat, ...))`).
- For true-metre distance bands, reproject to **EPSG:3035** (LAEA Europe):
  `ST_DWithin(ST_Transform(ST_GeomFromText(wkt,4326),3035), ST_Transform(ST_GeomFromText(pt,4326),3035), <metres>)`.
- Bands the customer specified: **50 / 100 / 200 m**. SRID/CRS mismatch silently returns wrong distances — always transform to 3035 before distance.

## Schema & tables
- **One schema:** `exposure_response`. Medallion by **table prefix**, not schema.
- `1_` raw/landing feeds · `2_` bronze (event footprints) · `3_` gold (property, exposure) · `gov_` governed/append-only (event snapshots, who-was-alerted) · `ref_` reference code-sets · `mv_` consumption views · `cache_` agent cache.
- Reference code-sets mirror `bricksurance-data-core` (`cause_of_loss`, `country`, `insured_object_type`, `coverage_type`, `line_of_business`) — seeded locally so the demo stands alone.
- **Portable catalog:** single `${var.catalog}` bundle var; no hardcoded catalog anywhere.

## UC functions
- Business math lives in governed `fn_*` UC functions (P1+): `fn_exposure_in_footprint(event_id, buffer_m)` is the hero. The app renders and recomputes by calling functions — never embeds the math.
- Rich `COMMENT` on every function (agents route off them). Watch the EXECUTE-grant gotcha on `CREATE OR REPLACE` (see DECISIONS.md).

## Real services only
Serverless everywhere, scale-to-zero. Autoloader for feed ingest (P2). Genie embedded + created via API. Agent on Mosaic AI / FMAPI (`${var.fm_endpoint}`), Claude. Working alert via a Databricks job (P4). Deterministic engines decide; AI narrates.

## Reset / reseed
One idempotent path: regenerate seeded synthetic data → re-land feeds (or frozen snapshot) → recompute → cache clear. Deterministic **conditional on as-of date**; event dates roll from `current_date()` so the book never reads stale.

## Naming & safety
No real customer names or recognizable estates anywhere. Public repo `wryszka/exposure-response-workbench`. Ships the doc trio (RUNSHEET, QA, DECISIONS) + Learn section + README in the same PR as the feature. 8-agent panel review before the room.
