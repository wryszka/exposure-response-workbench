# T5 `frontend` — notes (overnight build, 2026-10-15)

Owner of `app/` only. Branch `frontend` (worktree `erw-wt-frontend`). Deployed from the staging copy
`/Workspace/Users/laurence.ryszka@databricks.com/erw-staging/frontend-app` (Contract E) — the final consolidated
bundle deploy should re-point the app at the bundle path.

## What changed

**Navigation.** "Workbench" keeps the three live screens (Exposure — alerts · Live event · Event Radar). A new
"Explore" group holds the depth: Data sources & quality · AI agents · Ask the book (Genie) · Governance & provenance ·
Learn. The landing (alert board) is unchanged apart from the "Also watching" list.

**Alert board.** Events with ≥1 insured home in the path keep a full alert card (Var wildfire pinned first). Events
with 0 homes in our EU book (e.g. the UK EA flood `EVT_EA_SAMPLE_UK`) go in a compact "Also watching" list, tagged
"Active zone published to underwriting" when the event appears in `mv_active_zones` (Contract A), with one line on
how the underwriting workbench uses it at bind.

**Maps (Contract D).** The in-app SVG maps are gone (code removed). The event view embeds that event's AI/BI
dashboard page from `ref_dashboard_pages` (lookup: exact event_id → any `source='EA'` event → UK page → else the `'*'`
overview; iframe = `embed_url`, new-tab = `page_url`). A clean "Map loading" placeholder shows if no page exists.
The Live event tab embeds the existing **Live Fire Tracker** dashboard (it carries the live-ingestion tiles) and
reloads it after each Start / Next update / Reset cursor write.

**Data sources & quality (Contract B).** `/api/sources` → `fn_source_status()`, `fn_dq_recent(7)`,
`ref_dq_threshold`. Screen: rejected-batch banner (first REJECTED batch: source, time, plain reason, "quarantined,
previous data kept"), KPI strip (sources / live · batches 7d · accepted % · rejected), one card per source (mode
chip, cadence, last ingested "N min ago", records, last decision, licence), batch log table (rejected rows
tinted) and a collapsible thresholds table. Degrades to "being deployed" if the functions are missing.

**AI agents (Contract C).** `/api/agents/roster` → `ref_agent_roster` + serving-endpoint state for
`exposure-response-agent` (env `AGENT_ENDPOINT`, default that name). `/api/agents/ask` → served endpoint
(Responses format `{"input":[…]}`; parses `output[].content[].text` + `custom_outputs.specialist / route_reason /
tools`), cached in `cache_agent_responses` (yellow toggle = live). If the endpoint is missing / not ready / errors →
falls back to the in-app agent (`app/server/agent.py`), labelled "In-app agent (fallback)". Screen: chat first
(who answered + why + tools called, Markdown rendered escape-first), supervisor card (+ link to the serving endpoint),
specialist cards (role, responsibilities, skills = UC functions, data read, may / may not, clickable example
questions). If `ref_agent_roster` is empty, the six specialists from the build contract are shown with an honest note.

**Learn (now a full screen, Solvency II pattern).** Sticky contents + 7 panels with persona voice and "see it live"
buttons: 1 How it works (the original 9 steps, updated for the gate, zones and agent team) · 2 Architecture (inline
SVG: sources → ingest jobs + quality gate + quarantine → Unity Catalog tables → governed functions → app / dashboards /
alerts / agents / Genie / underwriting) · 3 Data flow (6-stage SVG strip + per-source table: carries / how it
arrives / lands in) · 4 Cat-modelling basics · 5 Solvency II nat-cat & reinsurance (Directive 2009/138/EC Art. 101
99.5% VaR, Art. 45 ORSA; Delegated Regulation (EU) 2015/35 nat-cat + man-made incl. the 200 m fire-concentration test,
cited at regulation level) · 6 Exposure-management practice (Lloyd's RDS, accumulation control, event response,
coverholder oversight) · 7 Data, licensing & climate (Copernicus, NASA FIRMS, MeteoAlarm, GDACS, Environment Agency
OGL v3.0; GDPR; EIOPA ORSA climate scenarios; CSRD (EU) 2022/2464 / ESRS E1). The old Learn overlay is removed.

**Ask screen** is now Genie-only ("Ask the book (Genie)") with three starter questions; the agent chat moved to the
AI agents screen.

## Backend endpoints added (`app/app.py`, bound params / no string interpolation of user input)
- `GET /api/sources` · `GET /api/agents/roster` · `POST /api/agents/ask` · `GET /api/maps`
- `GET /api/events` now also returns `zones` (event ids in `mv_active_zones`, or null if the view is missing).
- Removed dead `GET /api/live/tick/{i}` (the SVG live map was its only caller).

## Fixes made on the way
- Promoting a radar signal refreshed a removed event picker (would throw) → now refreshes the alert board.
- Removed the dead event-picker code (`setFilter` / `renderPicker`) and the dead "What am I seeing?" `#whatis` lookup.

## For the main session (docs I don't own)
- `docs/DEMO_DOC.md` pre-flight step "Ask → click all 5 starter prompts": the starters are now on **AI agents**
  (one example per specialist); the Ask screen is Genie-only.
- Learn's architecture/data-flow text names `fn_zone_check`, `mv_active_zones`, `gov_feed_dq_log`,
  `1_feed_quarantine` — matches the contracts.
- The app SP needs **CAN_QUERY on `exposure-response-agent`** for the served path (T3) — without it the chat
  falls back (labelled).
- Browser render-check of the embedded dashboard iframes NOT done — the local chrome-devtools profile was locked by
  another session. HTTP checks only.

## Verification (see report for final state)
Deploys: `01f1c87ea9f91ae6adf4bbd600321a3b`, then `01f1c87ef35d1131bb256364a52a42ab` (RUNNING).
