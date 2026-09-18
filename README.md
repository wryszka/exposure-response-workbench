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
- **Wildfire:** NASA FIRMS + EFFIS · **Windstorm:** MeteoAlarm + NOAA NHC / IBTrACS · **Flood:** GloFAS/EFAS + Copernicus EMS · **Unstructured:** GDACS RSS + press wires (Event Radar).
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
  40_news_radar.py        # Event Radar — GDACS + press → AI extract + geocode + fn_news_radar verify
app/                      # thin FastAPI + self-contained SPA (inline-SVG map); calls the UC functions
frontend/tokens.css       # canonical house design tokens
docs/                     # DEMO_RUNSHEET.md, DEMO_QA.md
CONVENTIONS.md · DECISIONS.md · STANDARDS.md
```

## Governed compute (the exposure functions)
- `fn_exposure_in_footprint(event_id, buffer_m)` — threatened insured objects within the buffer, with true metres + distance band; multi-segment events unioned.
- `fn_event_exposure_summary(event_id, buffer_m)` — the same exposure rolled up by band and by country (the cross-border view).
- `fn_events(buffer_m)` — active events with a live threatened-count, for the picker.
- `fn_gross_to_net(event_id, buffer_m)` — modelled gross loss (`ref_damage_factor`) ceded through the Property Cat XL tower (`3_treaty` / `3_treaty_layer`) to net retained; one row per waterfall step.
- `fn_exposure_by_coverholder(event_id, buffer_m)` — threatened exposure split by coverholder / delegated authority (`3_coverholder` via `3_property_coverholder`).
- `fn_event_delta(event_id, buffer_m)` — newly / still / no-longer threatened vs the previous snapshot (`gov_exposure_snapshot`) — the new/progressing/extinguished signal behind alerts.
- `fn_news_radar()` — Event Radar verification: each news/GDACS signal with threatened count/SI, corroboration, a confidence, an evidence trail and the `beats_feed` early-warning flag.

All exposure maths lives in these functions, never in the app. **`ST_*` runs on the SQL warehouse, not the serverless notebook engine** — so notebook 02 orchestrates the DDL onto the warehouse via the Statement Execution API.

## Quick start (dev)
```bash
databricks bundle deploy -t dev -p DEV
databricks bundle run exposure_00_setup    -t dev -p DEV   # schema + property foundation
databricks bundle run exposure_02_functions -t dev -p DEV  # governed exposure functions
# app: databricks apps start/deploy exposure-response-workbench --source-code-path <ws files>/app
```
Geospatial (`ST_`) needs a serverless / DBR 17.1+ warehouse. Default warehouse: `a3b61648ea4809e3`.

## Stakeholder alerts (for the people who never open Databricks)
`fn_event_delta` + a per-peril threshold (`ref_alert_threshold`) drive an **event-grouped** digest — one alert
carries the whole cross-border picture — audited in `gov_alert_dispatch` and dispatched by the serverless job
`exposure_30_alerts` (runs with nobody logged in). Real send via SMTP or Slack — add one free secret and it emails
for real; without it the digest is still built, audited and previewable in-app. See `docs/ALERTS.md`.

## Ask in plain English — agent + Genie
The **Event Response agent** is Claude on the Databricks Foundation Model API (`databricks-claude-sonnet-4-6`),
in-app (`app/server/agent.py`) with a **governed-function tool surface** — it answers only by calling the
`fn_*` functions, so every number is a real tool call (shown under each answer), never invented. A response
cache + a visible **live/cached toggle** keep demo beats snappy. **Genie** ("Ask the Exposure Book", created
programmatically over the `mv_*` views) answers ad-hoc questions in-app and shows the SQL it wrote.

## Event Radar — the unstructured news sensor ("we knew before the satellite")
The structured feeds are authoritative but lagging. **Event Radar** (`notebooks/40_news_radar.py`, tab in-app)
scans **GDACS RSS** (live, keyless) + press wires, extracts peril/place/severity with **AI Functions**
(`ai_classify`, `ai_query`), geocodes against a governed gazetteer, and **verifies each signal against the book**
via `fn_news_radar`: threatened count/SI on the `ST_` path, a confidence, an evidence trail, and a **`beats_feed`**
flag (touches the book *and* no structured feed has caught it yet = an early warning). **Auto-detect, human-decide:**
promoting a signal to a `NEWS` event (which then flows through the same exposure + alert path) is a human click,
audited append-only in `gov_news_decision`. Hero: a Var wildfire press item threatens **36 props / €103.6m**,
`beats_feed=TRUE`, **14 hours ahead** of the FIRMS satellite sample.

## Governance & provenance
`gov_data_provenance` (feed source, live vs frozen, ingest time per event), `gov_exposure_history` (what moved
on the book across snapshots), `gov_alert_audit` (append-only dispatch record) and `gov_news_decision` (every
Event Radar detection + human promote/dismiss) back the **Governance** and **Event Radar** tabs.

## Status
**P0–P7 complete.** Property foundation, governed exposure functions, live exposure view, **live hazard-feed
ingestion** (MeteoAlarm + GDACS live; FIRMS/Copernicus frozen pending free keys), the **gross → net → coverholder**
waterfall, the **stakeholder alert path**, the **event-response agent + Genie + governance**, and the **Event Radar
news sensor** all deploy and run on dev. App: `https://exposure-response-workbench-7474656169654171.aws.databricksapps.com`.
Verified: Var Wildfire **59 / €164m** (agent returns net **€25m** via governed tools); Alpine Flood cross-border
**IT 69 / AT 53** dispatched as **one alert**; live MeteoAlarm storm **195 / €499m**; Event Radar hero **36 / €103.6m,
beats FIRMS by 14h**; Genie answers over the book.
Next: flip the `exposure-management` hub tile roadmap→live.
