# Exposure & Event Response — Demo Doc

> Source of truth for the live demo. Each `#` section below is a **tab** in the Google Doc.
> The Google Doc link is **stable** — it is updated in place, never re-created.
> Audience: Hiscox Europe exposure-management team (Dawn, Miguel ×2). Review: 16 Oct 2026.

---

# Simple Talk Track

**The whole demo in four beats. One idea, one number, one screen per beat. Plain words — no platform jargon. ~4 minutes.**

Open the app → **Live event** tab. Make sure it's reset to the clean pre-trigger state (see *Setup & Reset*).

**Beat 1 — The fire, and our homes in its path.**
- DO: Click **Follow this story** on the news item. The map draws the fire; insured homes inside it light up. Let it advance a step or two.
- SAY: *"A wildfire's just broken out in southern France — on real live hazard feeds. Normally we're blind for hours. Here are the homes we insure in its path — right now."*
- NUMBER: **73 homes** exposed, climbing as the fire spreads.
- *(Names the risk of inaction + signals real live data, upfront — per review.)*

**Beat 2 — What it could cost us.**
- DO: Point to the exposure figure, then the net.
- SAY: *"That's €183m of cover exposed. After our reinsurance, we'd keep €25m."*
- NUMBERS: gross **€183m** → net **€25m**.
- BRIDGE (30 sec, if a mixed/ops room): *"The €183m is the cover at risk; a visible damage curve turns it into a modelled loss, and our reinsurance absorbs the rest down to what we keep. The full step-by-step is one click away in Explore."*

**Beat 3 — Who was told, automatically.**
- DO: Show the stakeholder-alert card / digest (present tense — the alert that goes out).
- SAY: *"The moment this crosses threshold, here's the alert that goes out — claims, underwriting, exposure — one event, both countries, before anyone opens a laptop."*
- IF ASKED "did it actually email?": *"The alert is built and audited; turning on real send is one free credential (SMTP or Slack) — no code change."* (Wire it and this becomes a live send — see Setup.)

**Beat 4 — The one that lands: buying cover as the fire arrives.**
- DO: In the quote→buy panel, request a quote for the hero address (issues fine), then click **Buy** once the fire has reached it.
- SAY: *"Someone tried to buy cover with the fire at their door. The system said no — active zone, can't insure."*
- NUMBER: quote **€31,816/yr** — bound when 5.7 km clear, **declined** once in the zone.

**Close (one line):** *"Sense the event, see the exposure, tell the right people, and stop the bad risk — in minutes, on live data."*

> Keep it here on the main track. Everything else is **Explore** — only if they ask.

---

# Setup & Reset

**Pre-flight (do ~10 min before the room — tested):**
1. **Warm the warehouse:** open the app (it lands on **Live event**), click into **Exposure** → pick the Var wildfire → wait for the map (~first query is the slow one). Avoids a cold-start stall on the live follow.
2. **Pre-cache the agent:** go to **Ask** → click all 5 starter prompts once (each caches instantly) → so the first live question is instant.
3. **Confirm reset:** Live event back to pre-trigger (no promoted `EVT_NEWS_*`, no bind rows); dates roll to today.
4. One browser tab, full screen, zoom so one KPI reads from the back of the room.

**Live-follow fallback:** the fire auto-advances every ~30s; if it ever stalls (slow query), use the manual **"Next update ▸"** button to step through — don't wait on the timer.
- App URL: https://exposure-response-workbench-7474656169654171.aws.databricksapps.com
- Warehouse `a3b61648ea4809e3` must be running.
- Fallback if you skip the live follow: the **Exposure** tab on the Var wildfire tells beats 1–3 statically.

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
