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

## Final verification (2026-10-15, after the consolidated exposure_99_reset + endpoint refresh) — ALL PASS
Endpoint `exposure-response-agent`, model `exposure_response.exposure_response_agent` v4 (@prod), 100% traffic, READY, scale-to-zero. App SP fc3ac835-… has CAN_QUERY.

| Question | Specialist (supervisor route) | Tools | Key numbers |
|---|---|---|---|
| Var wildfire, gross and net? | Exposure Analyst → Reinsurance Analyst (chained) | fn_event_exposure_summary, fn_gross_to_net | 59 homes / EUR 164.3m SI; modelled gross 87.2m, ceded 62.2m, net 25.0m |
| Alpine flood homes by country? | Exposure Analyst | fn_event_exposure_summary, fn_exposure_in_footprint | 122 homes / EUR 331.4m; IT 69 (185.6m), AT 53 (145.8m) |
| Any feed batch rejected today and why? | Data Steward | fn_dq_recent, fn_dq_batch_checks | MeteoAlarm 07:00 batch REJECTED: 8 of 21 (38%) outside Europe/UK vs 5% limit, coords swapped, quarantined; other checks passed |
| Bind cover at UK postcode HX7? | Underwriting Guard | fn_place_lookup, fn_zone_check, fn_bind_check | REFER — inside active EA flood warning EVT_EA_SAMPLE_UK (0 m); Var fire 1,270 km away |
| Who was alerted about the Alpine flood? | Alerts Officer | fn_alert_log | built, not sent (no channel); 3 recipients; IT+AT one alert; threshold gross >= EUR 15m OR new >= 20 |
| Radar signal that beats the satellite? | Event Watch | fn_news_radar | Var wire report: 36 homes / EUR 103.6m, beats_feed, confidence 1.0; GDACS items dismissed at 0.15 |

Same six via the app's POST /api/agents/ask (live:true): all `mode=served`, same specialists, real supervisor route_reason, same tools and numbers.

## ⚠️ ACTION FOR MAIN SESSION — reset breaks the agent until it is refreshed
The endpoint authenticates to its tools with automatic credential passthrough, scoped to the functions/tables resolved AT DEPLOY TIME. `exposure_99_reset` drops and re-creates functions (fn_gross_to_net, fn_news_radar, fn_bind_check, fn_zone_check, fn_dq_*, fn_source_status) and tables, so after a reset those tool calls fail with "insufficient privileges" even though `account users` + app-SP grants look correct (verified — the agents correctly said "permissions error" and invented nothing).
**Fix (tested):** run `notebooks/70_deploy_agent_version` (no params → @prod) as the LAST task of `exposure_99_reset`. When the version is already served it re-pushes the same served entity → fresh deployment → passthrough re-resolved (~3–5 min, verified SUCCESS + tools work). Add it to resources/reset_job.yml after 07 governance (and 70_agent_tools after 30 alerts). Then warm the endpoint with one question before the room.
