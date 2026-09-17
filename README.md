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
- Property book is synthetic Bricksurance SE (no PII).

## Repo structure
```
databricks.yml            # DAB bundle — catalog is the one portability var
resources/                # jobs (setup_job.yml; ingest/ai/reset/app to follow)
notebooks/
  00_setup.py             # schema + reference code-sets (mirror data-core)
  01_property_foundation.py  # European property book + frozen event footprints + band smoke test
frontend/tokens.css       # canonical house design tokens
docs/                     # DEMO_RUNSHEET.md, DEMO_QA.md
CONVENTIONS.md · DECISIONS.md · STANDARDS.md
```

## Quick start (dev)
```bash
databricks bundle deploy -t dev -p DEV
databricks bundle run exposure_00_setup -t dev -p DEV
```
Geospatial (`ST_`) needs a serverless / DBR 17.1+ warehouse. Default warehouse: `a3b61648ea4809e3`.

## Status
**P0 complete** — scaffold, DAB, and the European property foundation deploy and populate; the 50/100/200 m
band logic is verified against the frozen wildfire footprint. Next: **P1** — the `fn_exposure_in_footprint`
governed UC function + the live exposure view/map.
