# Demo run-sheet — Exposure & Event Response (Bricksurance SE)

> Skeleton — filled beat-by-beat as the build lands (P1+). Every beat is **GO · DO · SAY · IF-ASKED**. SAY lines ≤20 words, **bold** the numbers, **no platform words** (they live in the Q&A). See the standard's BUILD_AND_REVIEW.md §4.

**Audience framing:** present in the exposure manager's process order — an event is developing, so what's my book's exposure and who do I tell? Platform words live in `DEMO_QA.md`.
**Runtime:** ~TBD min demo + Q&A.
**If compressed, cut in this order:** TBD. **NEVER cut** the live-footprint → threatened-properties beat.

## Links — dev (stable)
- **App:** https://exposure-response-workbench-7474656169654171.aws.databricksapps.com (P1 — event picker, map, exposure view)
- **Schema (Catalog Explorer):** `lr_dev_aws_us_catalog.exposure_response`
- **Key assets:** `3_property` · `2_event_footprint` · `fn_exposure_in_footprint` · `fn_event_exposure_summary` · `fn_events` (P1) · Genie (P5) · alert job (P4)

## Pre-flight (before the room)
- **Reset:** in-app button / CLI (regenerates seeded data, rolls event dates to recent past).
- **Verify** embedded map + Genie render on your authenticated session.

---

**0 · The problem (1m)**
- **SAY:** "A wildfire is burning in southern France. Which of our properties are threatened, what's the exposure, and who needs to know — **right now**?"
- **IF ASKED:** how is this done today? → manual: hand-pull policies, eyeball fire outlines. Q&A #1.

**1 · The live footprint on the book (P1)** — the load-bearing beat ✅ built
- **GO:** app → pick **Var Wildfire**.
- **DO:** show the map (footprint + properties coloured by band); read the KPI row; drop the buffer 200 → 100 → 50 m and watch the count tighten.
- **SAY:** "The fire footprint over our book: **59 properties** within **200 m**, **€164 m** sum insured at risk — **50** of them right inside it."
- **IF ASKED:** how is distance measured? → true metres, EPSG:3035; inside the footprint = 0 m. Q&A.

**1b · Cross-border in one view (P1)** ✅ built
- **GO:** pick **Alpine Flood**.
- **DO:** point at the country bars — one event, two countries.
- **SAY:** "One flood, **one event** — **69** Italian and **53** Austrian properties. Country-siloed teams would each see only half."

**1c · This is a REAL live feed (P2)** ✅ built
- **GO:** filter the picker to **● Live** → pick **MeteoAlarm FR STORM** (green LIVE badge, sorted first).
- **DO:** note the badge + the source line; this pulled from Météo-France minutes ago.
- **SAY:** "This isn't seeded — these are **live** Météo-France warnings, pulled straight in. **195 properties** in the warned regions, **€499 m** at risk. The feed your team fought with, running itself."
- **IF ASKED:** how is it live? → keyless MeteoAlarm CAP feed → warned NUTS3 regions → real boundary polygons → the same governed function. FIRMS wildfire + Copernicus flood go live with two free keys (docs/FEEDS.md). Q&A.

**2 · Gross → net of treaty, and by coverholder (P3)** ✅ built
- **GO:** stay on **Alpine Flood** → scroll to the **Gross → net of treaty** card.
- **DO:** read the gross → ceded → net headline; point at the layer bars; then the **by-coverholder** card.
- **SAY:** "Modelled loss **€147 m**. Our Property Cat XL tower absorbs it — we cede **€122 m**, retain just our **€25 m**. And **who wrote it**: Alpine Cover Underwriting, a delegated binder, carries **€215 m** of this event."
- **IF ASKED:** loss vs sum insured? → sum-insured-at-risk × a visible governed damage curve (`ref_damage_factor`) = modelled loss; a cat model would replace that table. Q&A.

**3 · Who needs to know — the alert (P4)** ✅ built
- **GO:** stay on **Alpine Flood** → scroll to the **Stakeholder alert** card.
- **DO:** read the delta chips (▲ newly / ● still / ▼ no longer threatened); note the status pill + "breached: gross ≥ €15m OR new ≥ 20"; click **Preview the alert that goes out**.
- **SAY:** "The moment this crosses threshold, the people who never open Databricks get **this** — claims, underwriting, exposure management. One event, **two countries, one alert** — not two teams each seeing half. It runs on a schedule, with nobody logged in."
- **DO (optional):** click **Run alert sweep now** — the serverless job fires; the dispatch log is audited in `gov_alert_dispatch`.
- **IF ASKED:** does it actually send? → yes — add a free SMTP or Slack secret (`docs/ALERTS.md`) and it emails for real; without it the digest is still built, audited and previewable (what you're looking at). Grouped by event, so cross-border never fragments. Q&A.

**3b · We knew before the satellite — Event Radar (P7)** ✅ built ⭐
- **GO:** left nav → **Event Radar**.
- **DO:** read the top card — the ⚡ **"beats the feed"** badge on the **Var wildfire** press signal; point at **36 threatened properties / €103.6m**, **confidence 100% · verified**, and the **evidence** line (geocoded to Var · touches book · *not yet in any structured feed*). Note the live GDACS signals below it (real disaster feed) sitting as dismissed — they don't touch our book.
- **SAY:** "The press reported this wildfire in the Var at **10:00 on the 15th**. Our satellite feed, FIRMS, didn't confirm it until the **16th** — **14 hours later**. Event Radar read the news, extracted the peril and place with AI, geocoded it, and **verified it against our actual book**: 36 insured properties, €103.6m, and *no structured feed has caught it yet*. That's the early-warning edge."
- **DO:** click **Promote to event & alert** — a **human decision**, audited. The signal becomes a `NEWS` event; flip to **Exposure & event response** and show it now flows through the *same* governed exposure view + alert path.
- **SAY:** "Auto-detect, **human-decide**. The machine finds and verifies; a person promotes. Every detection and decision is logged in `gov_news_decision` — nothing consequential fires on its own."
- **IF ASKED:** false positives? → the gate is **geospatial** — a signal only scores if it touches the book *and* isn't already in a feed; "a fire somewhere in the Var" that isn't near our properties scores 0.15 and is dismissed. Q&A.

**4 · Ask in plain English — the agent (P5)** ✅ built
- **GO:** left nav → **Ask the agent & Genie**.
- **DO:** click the starter "*What's my exposure to the Var wildfire, gross and net, and how sure am I?*"; when it answers, point at the **Called: `list_events` · `exposure_summary` · `gross_to_net`** line under the answer.
- **SAY:** "This is Claude — but it doesn't *guess* numbers. It **calls the same governed functions** the screens use. €164m gross, €25m net — the exact figures, with the tools it called shown as proof. And it tells you the footprint is a frozen illustrative one, so you know how sure to be."
- **DO (optional):** flip the **yellow live/cached toggle**; ask Genie "*total sum insured by country?*" — Genie shows the **SQL it wrote**.
- **IF ASKED:** can it hallucinate a figure? → no — it has no calculator, only the governed tools; the numbers come back from the warehouse. Q&A.

**5 · Trust it — governance (P5)** ✅ built
- **GO:** left nav → **Governance & provenance**.
- **SAY:** "Every event carries its **provenance** — live feed vs frozen footprint, source, ingest time. The book's movement is **auditable across snapshots**, and **every alert dispatch is append-only**. Nothing on these screens is un-sourced."

**N · Finale — the governed loop (1m)**
- **SAY:** "Live event, real book, one governed answer — measured, ceded, dispatched, and explained by an agent that can't make a number up — in minutes, not a scramble."
