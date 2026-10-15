# Track T3 — agents (notes for the main session)

Status: endpoint `exposure-response-agent` READY on model version 4 (100% traffic; v1/v2 at 0%). Final verification appended below.

## What was built
- `notebooks/70_agent_tools.py` — two governed functions: `fn_alert_log(p_event_id DEFAULT NULL)` (latest alert per event from `gov_alert_dispatch`) and `fn_place_lookup(p_place)` (gazetteer place or UK postcode district → lat/lon; the UK branch reads `underwriting_workbench.ref_postcode_centroid`). Grants to account users + app SP. **Already run on DEV.**
- `notebooks/70_exposure_agent.py` — the ResponsesAgent (plain Python FILE, not a notebook — import with `--format AUTO`): supervisor (Claude Haiku 5.5, routing JSON + keyword fallback) + six specialists (Claude Sonnet 5.5), each with a restricted tool subset. Tools execute on the SQL warehouse via Statement Execution API with named args (`fn(p => :p)`) — NOT UCFunctionToolkit, because the exposure functions use ST_ which Spark Connect rejects.
- `notebooks/70_build_deploy_agent.py` — reads tool specs from UC, smoke-tests, logs with passthrough resources (2 LLM endpoints, warehouse, functions, every table/view in the schema + UW centroids), registers `exposure_response.exposure_response_agent@prod`, `agents.deploy(... scale_to_zero=True)`, waits for READY, grants app SP CAN_QUERY, writes `ref_agent_roster`.
- `docs/AGENTS.md` — the team, routing, cards, guardrails, serving, warm-up.

- `notebooks/70_deploy_agent_version.py` — deploy an already-registered version (default `@prod`) without rebuilding; waits for READY; re-grants app CAN_QUERY. Use when a build registered a version but the endpoint was busy updating.

## For the reset job / bundle (main session)
- Add `70_agent_tools` as a task AFTER the functions it reads exist (after 30 alerts is fine; it only needs `gov_alert_dispatch` + `ref_gazetteer`). CREATE OR REPLACE drops grants — the notebook re-grants.
- Do NOT put `70_build_deploy_agent` in the reset (it redeploys the endpoint, ~20 min). Run it on demand after adding a governed function.
- Both 70_* files must sit in the same workspace folder (the build notebook loads the co-located `70_exposure_agent.py`).

## Gotchas
- Claude on FMAPI can return `message.content` as a LIST of blocks inside the serving container (it came back as a string from the CLI). v1 crashed (`'list' object has no attribute 'strip'` → HTTP 400 → the app fell back to the in-app agent) and the supervisor's JSON parse failed → keyword routing. Fixed with `_as_text()` everywhere (v4).
- A model version has a cap on declared dependencies — declaring every table in the schema failed registration. The build now declares only the tables each function body reads (derived from information_schema.routines).
- `agents.deploy` raises "Endpoint ... is currently updating" if a previous rollout is in flight — wait, then use `70_deploy_agent_version`.
- New versions take 100% of traffic; previous versions stay attached at 0% (harmless with scale-to-zero).
- The reset recreates functions/tables (drops grants). The endpoint's identity relies on grants restored to `account users`; if tools fail with permission errors after a reset, re-run `70_deploy_agent_version` (re-applies the endpoint's resource grants).
- `w.serving_endpoints.get_open_ai_client()` needs `httpx` — not in the serverless base image; pinned in pip reqs.
- Specialists only get functions that exist at BUILD time — re-run the build after T1 (`fn_source_status`, `fn_dq_recent`) lands.
