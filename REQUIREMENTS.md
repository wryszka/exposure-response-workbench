# Requirements — Exposure & Event Response workbench

What this demo must satisfy, so it can be scored against the client's actual asks and the
Bricksurance demo standard. Derived from the **Hiscox Europe exposure-management call
(2026-09-16)** and the `exposure-management` hub tile's canonical question:

> *"A named storm makes landfall in 36 hours. What is my exposure — gross, net of treaty,
> by coverholder — right now, and how sure am I?"*

Status key: **MET** (built & verified) · **PARTIAL** (built, with a labelled gap) · **ROADMAP** (deliberately deferred).

## Functional requirements (from the call)

| # | Requirement | Status | Evidence / gap |
|---|-------------|--------|----------------|
| R1 | **Live threat view** — which insured properties are in or near an active event footprint, at the client's **50 / 100 / 200 m** distance bands. | **MET** | `fn_exposure_in_footprint(event, buffer_m)` over `ST_`/EPSG:3035; exposure map + threatened-property table in-app. Var wildfire 50m:50 / 100m:51 / 200m:59. |
| R2 | **Automatic stakeholder alerts** to people who will not log into Databricks (claims, underwriters, seniors), including **new / progressing / extinguished** detection. | **PARTIAL** | Alert engine, `fn_event_delta`, thresholds, event-grouped digest, `gov_alert_dispatch` audit, in-app preview, serverless hourly job — all built & verified. **Gap:** real send is `RENDERED_NOT_SENT` until one free channel credential is set (`exposure_response/SMTP_*` or `SLACK_WEBHOOK`); flips to `SENT` with no code change (`docs/ALERTS.md`). |
| R3 | **Cross-border visibility** — an event spanning countries surfaces as **one event, not siloed** (the Italy + France + Austria miss). | **MET** | Alerts and exposure group **by event, not country**. Alpine Flood dispatches as one alert, `n_countries=2`, IT 69 + AT 53 together. |
| R4 | **Historical analysis** capability. | **PARTIAL** | Append-only `gov_exposure_snapshot` + event history + governance views give the spine (point-in-time, reproducible). **Gap:** a dedicated historical trend/UI (season-to-date, event replay) is **ROADMAP**. |
| R5 | **Gross → net-of-treaty → by-coverholder** exposure (the tile's canonical question). | **MET** | `fn_gross_to_net` (Property Cat XL: €25m retention, 4 layers to €400m) + `fn_exposure_by_coverholder`. Waterfall varies across the tower: Esterel €13.7m→net €13.7m (under); Var/Alpine →net €25m (within); Celine €566.9m→net €231.9m (pierced). |
| R6 | **Robust ingestion of the real open feeds** the client's analyst struggled to pull (FIRMS/NASA fire + flood + storm) — solving the **API-timeout / blocking** pain. | **MET** | Framework with exponential-backoff/retry + Delta cache; **two live keyless feeds proven** — MeteoAlarm windstorm (real Météo-France warning → 195 threatened props) *and* **GDACS RSS** disaster feed (12 live current items landed to `1_news_raw`). FIRMS (fire) + Copernicus EFAS/EMS (flood) adapters built on **frozen real-shape samples**; go live with two free keys (`MAP_KEY`, `CDS_API_KEY`, `docs/FEEDS.md`). Statement-Execution polling handles slow AI-extraction (`docs`/DECISIONS gotcha). |
| R7 | **Stretch: predicted cost impact.** | **PARTIAL** | `ref_damage_factor` (governed peril×band damage curve) turns sum-insured-at-risk into a **modelled gross loss** — a transparent first-order cost model. **Gap:** a fuller predictive severity/cost model is **ROADMAP** (a cat model would supply the curve). |
| R8 | **Early unstructured signal — "beats the feed."** Catch a developing event from **press / news** *before* the structured satellite feed confirms it, verified against the actual book. | **MET** | **Event Radar** (`notebooks/40_news_radar.py`, `fn_news_radar`, app tab): GDACS + press wire → `ai_classify`/`ai_query` extract + gazetteer geocode → geospatial verification vs `3_property` → confidence + evidence + **`beats_feed`** flag. Hero: frozen Var wildfire touches **36 props / €103.6m**, `beats_feed=TRUE`, **14 h ahead** of the FIRMS sample. |
| R9 | **Human-in-the-loop governance — auto-detect, human-decide.** No consequential action (promote-to-event, alert) runs autonomously; every detection + decision is audited. | **MET** | Detection is automatic; **promotion is a human click** (`POST /api/radar/promote`, idempotent) that inserts a `source='NEWS'` event into the *existing* exposure + alert path; every decision appended to `gov_news_decision` (who / when / confidence / evidence). Verified: promote → `fn_exposure_in_footprint` 30 props / €90.6m → audit row. |

## Non-functional requirements (Bricksurance standard)

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| N1 | **Real governed services, nothing faked.** | **MET** | UC functions (all business maths), `ST_` geospatial, Autoloader-style ingestion, embedded Genie, in-app Mosaic AI / FMAPI agent, append-only governance tables. |
| N2 | **Serverless everywhere, scale-to-zero.** | **MET** | Serverless SQL warehouse + serverless jobs; no always-on compute. |
| N3 | **Deterministic + reset closes the loop.** | **MET** | seed=42; dates roll from `current_date()`; setup/feeds/treaty/alerts jobs re-runnable; heroes deterministic conditional on as-of date. |
| N4 | **Built for three audiences** (C-suite / practitioner / SA·AE). | **PARTIAL** | App + Learn panel + agent serve all three; per-audience runsheet beats finalised in the review pass. |
| N5 | **AI narrates, deterministic functions decide** (never invent a number). | **MET** | Event Response agent answers by calling `fn_*` — verified it returns the governed figures (Var net €25.0m), not hallucinations. |
| N6 | **No real customer names or recognizable estates.** | **MET** | Synthetic Bricksurance SE European book; no Hiscox data, names, or estate anywhere. |

## Review

Reviewed against the Bricksurance standard via the 8-agent panel (practitioner · decision-maker ·
Databricks SA · senior dev · security · current-Databricks expert · incumbent champion · UI/UX).
Findings and fixes are tracked in `docs/REVIEW/`. Ship when every P0 passes and every P1 gap is
labelled and roadmapped (the PARTIAL/ROADMAP rows above are that honesty).
