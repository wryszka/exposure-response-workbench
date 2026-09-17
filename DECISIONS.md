# Build decisions log — Exposure & Event Response Workbench

> Dated, reverse-chronological, self-contained entries. The single source of truth for what changed and why.

## Accepted at kickoff (2026-09-17)
- **D1 — Origin & purpose.** Built from the Hiscox Europe exposure-management call (2026-09-16): automated wildfire/cat exposure tracking — overlay live hazard footprints on insured property locations, show threatened properties now, alert stakeholders. Anchor peril wildfire; broadened to flood + windstorm at the user's direction. Review session targeted **Fri 16 Oct 2026**.
- **D2 — Naming.** Repo `wryszka/exposure-response-workbench`; schema `exposure_response`; tile "Exposure & Event Response" (promotes the existing `exposure-management` roadmap tile in the actuarial-workbench hub → live).
- **D3 — All three perils, one spine.** Wildfire + flood + windstorm built on ONE event-response spine (footprint model, intersection engine, exposure view, alert path) + three thin adapters (footprint source differs). Wildfire first and deepest.
- **D4 — Hazard data = live + frozen + Marketplace.** Use free live feeds where they exist (FIRMS/EFFIS fire; MeteoAlarm/NHC/IBTrACS wind; GloFAS/EFAS + Copernicus EMS flood), a frozen snapshot for a reproducible room, synthetic fill where live data is thin (esp. flood live-event layer), and check Databricks Marketplace geo/property listings (CARTO/Precisely/MBI) — verify in-workspace before relying on them. Property book stays synthetic (no PII).
- **D5 — Working alert.** A real Databricks job detects newly-threatened properties over threshold → stakeholder digest to a demo inbox, **grouped by event, not by country** (directly fixes Hiscox's siloed cross-border miss). (P4.)
- **D6 — Entity/geography.** Bricksurance SE, European property book (FR/IT/AT/ES/DE), lat/long geocoded — the cross-border story needs real coordinates, not just UK postcodes.

## P0 (2026-09-17) — scaffold + property foundation — newest first
- **D7 — Geospatial pattern confirmed.** `ST_` functions are live on warehouse `a3b61648ea4809e3` (Serverless Starter). They require **GEOMETRY** (not GEOGRAPHY) and a serverless/DBR-17.1+ warehouse. Verified compute path for the 50/100/200 m bands: `ST_DWithin(ST_Transform(ST_GeomFromText(wkt,4326),3035), ..., metres)` — EPSG:3035 (LAEA Europe) gives true metres (two Paris points 0.0008° apart → 58.7 m; within_100m=true, within_50m=false). **Biggest technical risk retired in P0.**
- **D8 — Data model.** `3_property` (~5,000 synthetic insured objects, columns mirror data-core `insured_object` + property extensions) with a dense **Var wildfire cluster** (SE France) and a **cross-border alpine cluster** (N Italy ↔ Austria). `2_event_footprint` holds frozen WKT footprints for the three perils; the Alpine Flood event has two segments under one `event_id` (the cross-border seed). Live feeds land in the same `2_event_footprint` shape in P2.
- **D9 — Auth/profile.** Deploy on the **DEV** profile (`-p DEV`, host fevm-lr-dev-aws-us). The DEFAULT/serverless-workspace profile token is expired — not used here.

---

## Shared gotchas (inherited across the demo family — verified)
- **`CREATE OR REPLACE FUNCTION` revokes EXECUTE grants** → re-grant the app SP after any function recreate, or the app 500s.
- **Genie space creation:** programmatically via the Databricks API (`databricks api post /api/2.0/genie/spaces --json @file`), never hand-built; grant app SP `CAN_RUN`; keep ≤ ~30 tables.
- **Apps serve stale HTML:** add middleware forcing `Cache-Control: no-store` on `text/html`.
- **Apps config:** `app.yaml` uses `valueFrom` (camelCase); Apps listen on port 8000; app SP needs schema USE/SELECT/EXECUTE, warehouse CAN_USE, Genie CAN_RUN, reset-job CAN_MANAGE_RUN.
- **Bundle auth on macOS:** `databricks bundle deploy` may need an unsandboxed shell (keychain) or it exits 45.
- **Serverless + files:** `%pip` installs; write to `/tmp` then copy; explicit schema on pandas→Delta appends; no `cache()`.
- **Geospatial:** GEOMETRY not GEOGRAPHY; reproject to a metric CRS (EPSG:3035 for Europe) before distance; SRID mismatch silently returns wrong distances.
- **Determinism:** seed the generator (42), math driver-side, sort deterministically; heroes deterministic conditional on the as-of date, which rolls to today on reset.
