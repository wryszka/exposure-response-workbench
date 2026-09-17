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

## P1 (2026-09-17) — governed exposure functions + live exposure view — newest first
- **D14 — App live + verified.** App `exposure-response-workbench` deployed to dev, RUNNING. URL `https://exposure-response-workbench-7474656169654171.aws.databricksapps.com`. Verified over HTTP (auth token): `/api/config`, `/api/events` (59/122/260 threatened), `/api/event/EVT_FIRE_VAR?buffer=200` (59 props, bands 50/1/8, €164.3m), `/api/event/EVT_FLOOD_ALPS` (cross-border IT 69 / AT 53), root SPA 200 with `Cache-Control: no-store`.
- **D13 — App SP grants.** App SP `fc3ac835-8393-4ea6-8d2b-180096836d7b` (client id) granted USE CATALOG/SCHEMA, SELECT on `3_property` + `2_event_footprint` (UC SQL functions run **invoker's rights** → the SP needs table SELECT, not just EXECUTE), and EXECUTE on the three functions. Warehouse CAN_USE comes from the `resources/app.yml` binding. SP recorded in `databricks.yml` dev target.
- **D12 — `ST_*` is warehouse-only; notebooks can't create these functions.** `spark.sql('CREATE FUNCTION … ST_…')` fails on serverless notebook (Spark Connect) with `SparkConnectUdfRejectionScope`. Fix: notebook `02_exposure_functions.py` orchestrates the DDL onto the **SQL warehouse** via the Statement Execution API (`WorkspaceClient().statement_execution`). Portable (catalog/schema/warehouse from widgets), re-runnable, and the job (`exposure_02_functions`) runs green.
- **D11 — Governed functions (the compute path).** `fn_exposure_in_footprint(p_event_id, p_buffer_m)` (threatened objects + true metres + band; multi-segment events unioned via `ST_Union_Agg`), `fn_event_exposure_summary(…)` (rollup by band and by country), `fn_events(p_buffer_m DEFAULT 200)` (picker with live threatened-count). Rich `COMMENT` on each so agents/Genie can route later. All math lives here, never in the app.
- **D10 — Map is CSP-safe inline SVG.** No external tile/CDN (strict self-contained HTML). The app returns footprint WKT + property lat/long; the browser parses the `POLYGON((…))`, projects lon/lat into a viewBox (cos-latitude x-correction) and draws the footprint polygon + property points coloured by band. Reused because the sibling reinsurance app renders geo as tables, not a plotted map.

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
