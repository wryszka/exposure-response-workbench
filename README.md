# Exposure & Event Response Workbench — Bricksurance SE

A catastrophe **event-response** workbench for a European property insurer. When a wildfire, flood or
windstorm is developing, it overlays the live hazard footprint onto the insured property book and answers, in
one governed place: **which properties are threatened right now, what is the exposure (gross and net of treaty),
and who needs to know** — including the claims and underwriting stakeholders who never log into a data platform.

Part of the Bricksurance demo family. Built and reviewed against the [Bricksurance standard](https://github.com/wryszka/bricksurance-playbook) (see `STANDARDS.md`). Promotes the `exposure-management` ("Exposure & Event Response") roadmap tile in the actuarial-workbench hub to live.

## The canonical question
> *"A wildfire is burning in southern France. Which of our insured properties sit in or near the footprint, what's the exposure gross and net, and who needs to know — right now?"*

## One spine, three perils
Wildfire · Flood · Windstorm — built on one event-response spine (footprint model → geospatial intersection →
exposure view → gross→net → alert), with three thin peril adapters. See `CONVENTIONS.md`.

## Data feeds
- **Wildfire:** NASA FIRMS + EFFIS · **Windstorm:** MeteoAlarm + NOAA NHC / IBTrACS · **Flood:** GloFAS/EFAS + Copernicus EMS.
- Live where the feed exists, a frozen snapshot for a reproducible room, synthetic fill where live data is thin.
- **MeteoAlarm windstorm ingestion is live now** (keyless) — real Météo-France warnings → NUTS3 boundaries →
  the governed exposure function. Wildfire (FIRMS) and flood (GloFAS/EFAS) go fully live once two free keys are
  set — see [`docs/FEEDS.md`](docs/FEEDS.md) for the exact signup URLs and secret locations. Until then they run
  on frozen real-shape samples.
- Refresh with the `exposure_10_refresh_feeds` job (serverless; daily, paused in dev; run on demand).
- Property book is synthetic Bricksurance SE (no PII).

## Repo structure
```
databricks.yml            # DAB bundle — catalog is the one portability var
resources/                # setup_job.yml · functions_job.yml · app.yml (ingest/ai/reset to follow)
notebooks/
  00_setup.py             # schema + reference code-sets (mirror data-core)
  01_property_foundation.py  # European property book + frozen event footprints + band smoke test
  02_exposure_functions.py   # governed UC exposure functions (DDL orchestrated onto the warehouse)
app/                      # thin FastAPI + self-contained SPA (inline-SVG map); calls the UC functions
frontend/tokens.css       # canonical house design tokens
docs/                     # DEMO_RUNSHEET.md, DEMO_QA.md
CONVENTIONS.md · DECISIONS.md · STANDARDS.md
```

## Governed compute (the exposure functions)
- `fn_exposure_in_footprint(event_id, buffer_m)` — threatened insured objects within the buffer, with true metres + distance band; multi-segment events unioned.
- `fn_event_exposure_summary(event_id, buffer_m)` — the same exposure rolled up by band and by country (the cross-border view).
- `fn_events(buffer_m)` — active events with a live threatened-count, for the picker.

All exposure maths lives in these functions, never in the app. **`ST_*` runs on the SQL warehouse, not the serverless notebook engine** — so notebook 02 orchestrates the DDL onto the warehouse via the Statement Execution API.

## Quick start (dev)
```bash
databricks bundle deploy -t dev -p DEV
databricks bundle run exposure_00_setup    -t dev -p DEV   # schema + property foundation
databricks bundle run exposure_02_functions -t dev -p DEV  # governed exposure functions
# app: databricks apps start/deploy exposure-response-workbench --source-code-path <ws files>/app
```
Geospatial (`ST_`) needs a serverless / DBR 17.1+ warehouse. Default warehouse: `a3b61648ea4809e3`.

## Status
**P0 + P1 complete.** The European property foundation, the governed exposure functions, and the **live exposure
view** all deploy and run on dev. App: `https://exposure-response-workbench-7474656169654171.aws.databricksapps.com`
— event picker, CSP-safe SVG map (footprint + properties by band), band KPIs, cross-border country breakdown,
threatened-property table, all off the governed functions. Verified: Var Wildfire **59 / €164m** (bands 50/1/8);
Alpine Flood cross-border **IT 69 / AT 53**. Next: **P2** — live hazard-feed ingestion.
