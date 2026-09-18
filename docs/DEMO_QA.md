# Demo Q&A — Exposure & Event Response (Bricksurance SE)

> Tab 2 of the run. Every question the demo can't answer live — incl. the incumbent champion's — documented and answered, sourced from live data, cross-referenced from the run-sheet beats. Populated as the build lands.

## Story & process
1. **How is this done today?** Reactive and manual: when a fire is reported someone hand-extracts policies from the source system, pulls the fire outline separately, and eyeballs the overlay. No standing process, no automatic alert, easy to miss a cross-border event.

## Data & feeds
2. **Is the hazard data real?** Yes where a free live feed exists — NASA FIRMS/EFFIS (fire), MeteoAlarm/NOAA (wind), GloFAS/EFAS/Copernicus EMS (flood). A frozen snapshot backs the room for reproducibility; synthetic fill covers gaps (mainly the flood live-event layer). The property book is synthetic — no customer PII.
3. **Why not just call the fire API from a dashboard?** Those feeds rate-limit and time out under ad-hoc polling. The platform ingests them robustly (scheduled + Autoloader + backoff + cache), so the map is always there when an event breaks.

## Platform (how it works)
4. **How is "within 200 m" computed?** Governed geospatial functions (`ST_DWithin`) on the property coordinates vs the event footprint, reprojected to a metric CRS (EPSG:3035) so the 50/100/200 m bands are true metres. Inside the footprint counts as 0 m.
5. **Is the maths in the app?** No — it's in governed UC functions (`fn_exposure_in_footprint`, `fn_event_exposure_summary`, `fn_events`) the app calls; the same functions are callable by agents and notebooks. The map, KPIs and table are all the one governed result.
6. **A flood is two polygons — how is it one number?** The function unions all segments of an event (`ST_Union_Agg`) into one footprint, so a multi-segment cross-border event returns its whole exposure as a single event — that's why the Alpine Flood shows IT and AT together.
7. **Why does the map have no base map / streets?** It's a strict self-contained page (no external tile servers) — the footprint and properties are drawn as inline SVG from the governed coordinates. A basemap tile layer is a later polish, not load-bearing.

## Gross → net (treaty)
9. **How does gross become net?** The modelled gross loss (a governed peril×band damage curve on the threatened sum insured) is ceded through the Property Cat XL tower — €25m retention then four layers to €400m — layer by layer (`LEAST(GREATEST(loss−attach,0),limit)×placement`) in `fn_gross_to_net`. The waterfall shows each step.
10. **Does net always equal the €25m retention?** No — it varies with severity, and four seeded events show the full range through the *same* `fn_gross_to_net`: **Esterel Brush Fire** (gross €13.7m) sits *under* the retention, the treaty never attaches, so **net = gross = €13.7m**; **Var Wildfire** (€87.2m) and **Alpine Flood** (€147.5m) land inside the fully-placed lower layers, so **net = the €25m retention** — the treaty doing its job; **Windstorm Celine** (gross €566.9m) *pierces* the €400m programme top and picks up the 90%/80% co-participation on the upper layers, so **net rises to €231.9m**. Four events, four different retained figures — proof the waterfall is computed, not pinned.

## Alerts
11. **Do the alerts actually send?** The sweep is a real scheduled serverless job that detects newly-threatened / progressing / extinguished properties, applies a threshold, and composes the digest — grouped **by event, across borders** — audited in `gov_alert_dispatch`. Real delivery flips on with one free credential (SMTP or a Slack webhook); until then the exact digest is rendered in-app.
12. **How does this stop the cross-border miss?** Alerts key on the *event*, not the country. The Alpine Flood dispatches as one alert covering Italy and Austria together — structurally impossible to split by country.

## Agent & Genie (AI)
13. **Does the agent make numbers up?** No. It's Claude (Foundation Model API) with a governed-function tool surface — it *must* call `fn_*` for every figure and narrates what they return; it never computes or recalls a number. Each answer shows the tools it called. The live/cached ("yellow") toggle switches between a cached narration and a fresh model call; the tools always hit live functions.
14. **What is Genie for here?** Free-form questions over the book ("properties by country", "biggest coverholder") — it generates the SQL (shown, so it's inspectable) over governed views. It sits on `mv_*` views because table names starting with a digit break generated SQL.

## Governance
15. **Can I trust the picture / prove what happened?** Every footprint carries its provenance (source, live vs frozen, when ingested); exposure snapshots are append-only so you can see what moved between runs; and every alert dispatch is logged. That's the `gov_*` view set.

## Event Radar — the news sensor (P7)
17. **What does "beats the feed" mean?** The structured satellite/warning feeds (FIRMS, MeteoAlarm, GloFAS) are authoritative but lagging. A news/GDACS signal that (a) geospatially touches our book *and* (b) has **no** structured feed of the same peril near it yet is an **early warning** — `beats_feed=TRUE`. Our hero: a Var wildfire in the press at 10:00 on 15 Sep, threatening 36 properties / €103.6m, **14 hours before** the FIRMS sample event dated 16 Sep.
18. **Is this AI making the decision?** No — same rule as the rest of the workbench. AI (`ai_classify`/`ai_query`) *extracts* peril, severity and a summary from the text; a governed function (`fn_news_radar`) *decides* by intersecting the geocoded location against the book on the `ST_`/EPSG:3035 path. The number is deterministic; the reading is AI.
19. **How do you avoid false positives — "a fire in the Var" ≠ our Var policies?** The gate is geospatial, not keyword. The place is geocoded to a coordinate, buffered 3 km, and intersected against actual property locations; a signal that doesn't touch the book scores 0.15 and is **dismissed**. Confidence and a plain-language **evidence** trail are shown for every signal — disconfirmation-first.
20. **Does it act on its own?** Never. Detection is automatic; **promoting** a signal to an event and alerting is a **human click**, and every decision is appended to `gov_news_decision` (who, when, confidence, evidence). Once promoted, the `NEWS` event flows through the *same* exposure view + alert path as any feed event.
21. **Is the GDACS feed real?** Yes — keyless GDACS RSS, pulled live (12 current disaster items landed). The frozen synthetic press item (a fictional wire, no real names) is there so the "beats the feed" beat is reproducible in the room.

## Incumbent champion (the skeptic)
16. **Our cat model already tells us this.** This works *alongside* the cat models — it brings their view together with the live event and the actual book, refreshed in hours, and shows who to tell. It's the operational layer, not a replacement model.

### Live event / anti-selection
- **Is the fire spread real or scripted?** A deterministic synthetic progression (`2_event_progression`) so the room is reproducible; the intersection each tick is the real governed `fn_progression_props` (ST_/EPSG:3035). Live feeds (MeteoAlarm) and GDACS are real elsewhere.
- **Does the agent decide the decline?** No — the governed `fn_bind_check` decides (geospatial fact); the agent only *explains* it. A human underwriter can override, and every decision is audited.
- **Why block the sale?** Binding cover on a property already in an active fire zone is adverse selection / moral hazard — the loss is near-certain. A human underwriter would refuse; the rule makes it consistent and real-time.
- **Is this wired into the underwriting workbench?** Not yet — this is a self-contained vignette in this workbench. Real cross-workbench integration (underwriting reads a published "active event zones" surface) is the deferred next step.
