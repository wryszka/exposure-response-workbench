# Exposure & Event Response — Demo Doc

> Source of truth for the live demo. Each `#` section below is a **tab** in the Google Doc.
> The Google Doc link is **stable** — it is updated in place, never re-created.
> Audience: Hiscox Europe exposure-management team (Dawn, Miguel ×2). Review: 16 Oct 2026.

---

# Opening — "You asked to see"

**Set the scene in about a minute, before you touch the app. Their words, read back to them — every beat after this answers one of them.**

**Say:**

*"On the 16th you walked us through how you track wildfire exposure today. A fire is reported, someone pulls the policies out of SQL, finds the fire outlines, and overlays them by hand. Miguel then sent us the data he uses — the Copernicus burnt-area outlines. You asked to see five things:"*

1. **Where the risk is, right now** — which fires threaten which of your locations, what's new, what's out.
2. **The distances that matter to you** — 50, 100 and 200 metres from the fire.
3. **The right people told automatically** — claims and underwriting, who won't be logging into Databricks.
4. **One picture across borders** — so a flood that hits Italy, France and Austria is one event, not three teams finding out separately.
5. **A process, not just a dashboard** — running on its own, on open data, without the API timeouts.

*"And the dream you mentioned: a view for Justin of what an event could cost us."*

*"What you'll see is a fictional insurer, Bricksurance SE — about 5,400 homes across France, Italy, Austria, Spain and Germany — on real open hazard feeds, including the Copernicus data Miguel uses. No Hiscox data anywhere."*

*"We'll take one fire from the first alert to three questions: who do we tell, what does it cost us, and what do we refuse to write. Then two things you didn't ask for. At the end, how you get from Miguel's notebook to this, one step at a time."*

> Then go straight to the alert board. If short on time, say only the five numbered asks and the last line.

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
- DO: Back in the event view: the cost card, then the alert card → **Run alert sweep now**. Switch to your inbox and open the email that's already waiting from the pre-room sweep (a fresh one lands ~3 minutes after the click).
- SAY: *"That's the cover at risk. After our reinsurance, we keep €25m. And the moment it crossed the line, this email went to claims and underwriting — every event, one row each, both countries together — sent by Databricks, before anyone opened a laptop."*
- NUMBER: we keep **€25m**.
- IF ASKED "is that a real email?": *"Yes — Databricks sent it, from a governed alert on the exposure data. No mail server, no extra tool."*

**Beat 4 — A stranger tries to buy cover with the fire at their door.**
- DO: In the **Get cover** panel pick 🔥 **Villa in the Var** → quote → **Buy cover now** → **Declined**. Then pick 🏡 **Apartment in Munich** → quote → **Buy** → **Bound**.
- SAY: *"This isn't one of our customers. It's someone who never insured with us, watching the fire come up the valley, trying to buy cover in the last hour. That's the risk we must not write — so: no, active zone. Someone in Munich buys the same cover — approved. Same rule, decided by the live data."*
- IF ASKED *"can we refuse a sale?"*: *"This is new business only — existing customers and renewals are untouched. Pausing new business inside an active event zone is normal market practice, often called a moratorium or binding embargo. Here it's a governed rule an underwriter can override, and every decision is logged."*
- NUMBER: Munich quote **€33,573/yr — bound.** Var — **declined.**

**Close (one line):** *"Spot it, track it live, know the cost, tell the right people, and stop the bad risk — in minutes."*

> Keep it here on the main track. Everything else is **Explore** — only if they ask.

---

# Setup & Reset

**Morning of the demo:** click **↻ Reset demo** at the bottom of the app's sidebar → **Reset now** (or Jobs → `exposure_99_reset` → Run now). About **15 minutes**: dates roll to today, the fire and dashboard go back to the start, demo clicks are cleared, and the agent team is refreshed. The sidebar shows progress and, when done, "Dates last rolled to today …". Don't run it during a demo.

**Pre-flight (do ~10 min before the room — tested):**
1. **Warm the warehouse:** open the app (it lands on the **alert board**), click **Track ▸** on the Var wildfire and wait for the map, then open the live dashboard once. First query is the slow one — this avoids a stall in the room.
2. **Start the live-ingestion job** ~5 min before, so the dashboard's ingestion counter is ticking when you get there: in Databricks **Jobs → exposure_61_stream_proof → Run now** (set `reset=true`), or from a terminal `databricks bundle run exposure_61_stream_proof -t dev -p DEV --params reset=true`. It stops by itself after 45 min; cancel the run in Jobs to stop early.
3. **Warm the agent team:** go to **Explore → Ask AI** and click each example question once. The first call wakes the served agent (it sleeps when idle and can take a minute or two to wake); after that answers come back quickly and are cached.
4. **Confirm reset:** fire back to the start (no promoted `EVT_NEWS_*`, no bind rows); dates roll to today.
5. **Send yourself a fresh alert email:** open any event → **Run alert sweep now**, and check it lands in your inbox (~3 min). Keep that email open in a tab for Beat 3.
6. One browser tab for the app, full screen, zoom so one KPI reads from the back of the room.

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
- **Data sources & quality** (Explore): every source, real-time or batch, when it last arrived, and the quality gate. One batch is always on show as **rejected**: 38% of a weather-warning pull fell outside Europe (limit 5%) because the coordinates were swapped — it was quarantined and the previous data kept.
- **Ask AI** (Explore → Ask AI): one place to ask. The supervisor routes each question to one of six specialists — Event Watch, Exposure Analyst, Reinsurance Analyst, Alerts Officer, Underwriting Guard, Data Steward — whose numbers come from the governed calculations; free-form data questions go to **Genie**, which shows the SQL it wrote. Each tile says what that specialist does; click it for its responsibilities and limits. The answer shows who answered and why.
- **UK flood → underwriting**: a live Environment Agency flood-warning feed publishes active zones; the separate **underwriting workbench** checks every quote against them. Calder Valley Fine Foods (Hebden Bridge, HX7) is inside a warning area → **refer**, quote blocked, with the reason. A risk in Cheshire quotes normally.
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
- *"How do we know the feed data is right?"* Every batch passes a quality gate (empty, missing locations, outside Europe, broken shapes, duplicates, staleness). A failing batch is quarantined and the previous good data stays — show the rejected batch on the Data sources & quality screen.
- *"Our customers are insured all year — why would we block a sale?"* It isn't about the existing book. It's the uninsured stranger trying to buy cover as the fire reaches them — adverse selection at the point of sale. New business only; renewals and existing policies are untouched.
- *"Do the agents decide anything?"* No. They explain and look things up through governed functions; the rules decide, and a person approves anything consequential.
- *"Do the alerts actually send?"* Yes. A Databricks SQL alert on the governed exposure data emails the stakeholders after every sweep — show the one in the inbox. Every send is logged; Slack or Teams is one more destination on the same alert.
- *"Is the wildfire feed live?"* MeteoAlarm and GDACS are live; the EFFIS wildfire perimeter is a real Copernicus sample, frozen for a reproducible room — live EFFIS ingestion is a known next step.
- *"Is that net number a real cat-model loss?"* No — it's a transparent, governed first-order damage curve, not a calibrated cat model. Don't read €25m as a signed-off capital figure; a real cat model would feed the curve (roadmap).
- *"Does it really block a bind in our underwriting system?"* The control logic + audit are real; wiring it into the live underwriting workflow is the deferred next phase (scope decision). Today it's a self-contained vignette.
- *"Does it scale to our 50k+ book / is the geocoding accurate?"* The engine is standard warehouse geospatial; the demo book is synthetic. At real scale you'd add a geocoding provider + a scale test — roadmap.

---

# About this demo

Synthetic data only — a fictional European insurer, **Bricksurance SE** (~5,420 properties, ~€14bn insured). Live feeds (MeteoAlarm, GDACS) are real and keyless; wildfire perimeters use EFFIS (frozen sample in the room). Built on Databricks; no Hiscox or any real customer data, names, or systems are used.
