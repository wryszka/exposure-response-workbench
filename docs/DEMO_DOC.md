# Exposure & Event Response — Demo Doc

> Source of truth for the live demo. Each `#` section below is a **tab** in the Google Doc.
> The Google Doc link is **stable** — it is updated in place, never re-created.
> Audience: Hiscox Europe exposure-management team (Dawn, Miguel ×2). Review: 16 Oct 2026.

---

# Simple Talk Track

**The whole demo in four beats. One idea, one number, one screen per beat. Plain words — no platform jargon. ~4 minutes.**

The app opens on the **alert board** — one card per active event. Make sure it's reset (see *Setup & Reset*).

**Beat 1 — An alert: a fire near homes we insure.**
- DO: Point at the 🔥 **Wildfire · Var, southern France** card. Nothing else on the screen.
- SAY: *"Normally we'd hear about this hours later. Here it's an alert the moment it touches our book — on real live hazard feeds."*
- NUMBER: **59 homes in the path · €164m at risk.**

**Beat 2 — Track it, live.**
- DO: Click **Track ▸**, then **Track live on the dashboard**. Let the fire spread a step or two (it advances on its own; **Next update ▸** if you want to pace it).
- SAY: *"This is the fire spreading, minute by minute. Every dot is a home we insure — red means it's in the path. And underneath, the data's arriving live."*
- NUMBERS: homes in the path climbing as it grows (0 → 73 → 90 → 105 homes; €287.5m at full size — the app and the dashboard show the same numbers), and the **live-ingestion** counter ticking.

**Beat 3 — What it costs us, and who's told.**
- DO: Back in the event view: the cost card, then the alert.
- SAY: *"That's the cover at risk. After our reinsurance, we keep €25m. And the moment it crossed the line, this alert went to claims and underwriting — one event, both countries — before anyone opened a laptop."*
- NUMBER: we keep **€25m**.
- IF ASKED "did it actually email?": *"The alert is built and audited; turning on the send is one free credential — no code change."*

**Beat 4 — Someone tries to buy cover with the fire at their door.**
- DO: In the **Get cover** panel pick 🔥 **Villa in the Var** → quote → **Buy cover now** → **Declined**. Then pick 🏡 **Apartment in Munich** → quote → **Buy** → **Bound**.
- SAY: *"Someone in the fire's path tries to buy — we say no, active zone. Someone in Munich buys the same cover — approved. Same rule, decided by the live data."*
- NUMBER: Munich quote **€33,573/yr — bound.** Var — **declined.**

**Close (one line):** *"Spot it, track it live, know the cost, tell the right people, and stop the bad risk — in minutes."*

> Keep it here on the main track. Everything else is **Explore** — only if they ask.

---

# Setup & Reset

**Pre-flight (do ~10 min before the room — tested):**
1. **Warm the warehouse:** open the app (it lands on the **alert board**), click **Track ▸** on the Var wildfire and wait for the map, then open the live dashboard once. First query is the slow one — this avoids a stall in the room.
2. **Start the live-ingestion job** ~5 min before, so the dashboard's ingestion counter is ticking when you get there: in Databricks **Jobs → exposure_61_stream_proof → Run now** (set `reset=true`), or from a terminal `databricks bundle run exposure_61_stream_proof -t dev -p DEV --params reset=true`. It stops by itself after 45 min; cancel the run in Jobs to stop early.
3. **Pre-cache the agent:** go to **Ask** → click all 5 starter prompts once (each caches instantly) → so the first live question is instant.
4. **Confirm reset:** fire back to the start (no promoted `EVT_NEWS_*`, no bind rows); dates roll to today.
5. One browser tab, full screen, zoom so one KPI reads from the back of the room.

**Fallbacks:** the fire auto-advances; if it stalls, use **Next update ▸**. If the dashboard won't show inside the app, use its **open in new tab** link. If the dashboard is down entirely, the event view's own map tells the same story.
- App URL: https://exposure-response-workbench-7474656169654171.aws.databricksapps.com
- Warehouse `a3b61648ea4809e3` must be running.

**If asked "is this real data?":** yes — MeteoAlarm (windstorm) and GDACS (disaster news) are live keyless feeds; wildfire perimeters are EFFIS Burnt Areas (the source Hiscox uses), on a frozen sample in the room for reproducibility. The property book is synthetic — no customer data.

**Disclaimer (say once, early):** *"This is a demonstration on synthetic data — a fictional insurer, Bricksurance SE. No Hiscox data is used."*

---

# Explore — going deeper (pull only)

Open these **only when asked** — they are the answer to "can it also…", never the opening.

- **Gross → net → by coverholder** (Exposure tab): the full reinsurance waterfall. It varies honestly — a small fire stays under the €25m retention (net = gross €13.7m); a severe one pierces the €400m tower (net €231.9m). By delegated authority: the Alpine flood sits mostly under one binder (Alpine Cover Underwriting, 78 cross-border homes).
- **Cross-border** (Exposure tab): the Alpine flood is **one event** spanning Italy (69) + Austria (53) — not two country silos.
- **We knew before the satellite** (Event Radar tab): a wildfire in the press, verified against our book — **36 homes / €103.6m, 14 hours before** the satellite feed caught it. Promotion to a live event is a human click, audited.
- **Ask the agent** (Ask tab): plain-language questions; it answers with the governed numbers, not guesses.
- **Ask Genie** (Ask tab): free-form questions over the book.
- **Governance**: every detection, alert, and decision is audited and reproducible.
- **Three perils, one engine**: wildfire, flood, windstorm all run the same way.

---

# The Ladder — "how you get there from where you are" (Act 2)

**Only after they want it.** This answers the "that's years away for us" fear by turning the gap into steps.

- *Today:* an analyst hand-pulls fire outlines and overlays policies in a spreadsheet (exactly what Miguel described).
- *Step 1:* the same overlay, once, in a notebook — no more manual export.
- *Step 2:* it runs on a schedule and emails the result — the analyst stops babysitting it.
- *Step 3:* a simple dashboard anyone can open.
- *Step 4:* the app you've just seen — same logic, now a product.

*"You don't rebuild everything. You start exactly where you are and climb one rung at a time — Miguel's notebook is already rung one."*

---

# Q&A (by persona)

**Exposure manager / underwriter (Dawn, Miguel):**
- *How close counts as "threatened"?* 50 / 100 / 200 m bands — the distances you specified.
- *Where do the fire outlines come from?* EFFIS Burnt Areas — your source. FIRMS hotspots too, as an earlier signal.
- *Does one event really cover both countries?* Yes — grouped by event, not country; the Alpine flood is one alert, IT + AT together.

**Decision-maker:**
- *What's the point in one line?* Know our exposure to a live event, tell the right people, and stop writing risk we shouldn't — in minutes.
- *Is it expensive to run?* Serverless, scale-to-zero — nothing runs when idle.

**Sceptic / "we could never have this":** see **The Ladder** — it starts at Miguel's current notebook.

**Technical / SA:** all business maths lives in governed functions; AI narrates but never invents a number; everything's audited and reproducible. Happy to open the hood in Explore.

**Tough questions — arm these (from the review panel); answer honestly, don't dodge:**
- *"Do the alerts actually send?"* The engine builds + audits the alert; real send is one free credential (SMTP/Slack) away, no code change. *(If we've wired it: "yes — here's the one that just sent.")*
- *"Is the wildfire feed live?"* MeteoAlarm and GDACS are live; the EFFIS wildfire perimeter is a real Copernicus sample, frozen for a reproducible room — live EFFIS ingestion is a known next step.
- *"Is that net number a real cat-model loss?"* No — it's a transparent, governed first-order damage curve, not a calibrated cat model. Don't read €25m as a signed-off capital figure; a real cat model would feed the curve (roadmap).
- *"Does it really block a bind in our underwriting system?"* The control logic + audit are real; wiring it into the live underwriting workflow is the deferred next phase (scope decision). Today it's a self-contained vignette.
- *"Does it scale to our 50k+ book / is the geocoding accurate?"* The engine is standard warehouse geospatial; the demo book is synthetic. At real scale you'd add a geocoding provider + a scale test — roadmap.

---

# About this demo

Synthetic data only — a fictional European insurer, **Bricksurance SE** (~5,420 properties, ~€14bn insured). Live feeds (MeteoAlarm, GDACS) are real and keyless; wildfire perimeters use EFFIS (frozen sample in the room). Built on Databricks; no Hiscox or any real customer data, names, or systems are used.
