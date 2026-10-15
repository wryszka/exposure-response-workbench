# Build contracts — overnight build 2026-10-15 (for the 16 Oct review)

Five parallel workstreams. Each works in its OWN git worktree/branch and owns only the files listed. These
contracts are the interfaces between them — build to them exactly. All objects live in
`lr_dev_aws_us_catalog.exposure_response` unless stated. Grant every new table/view/function to the app SP
`fc3ac835-8393-4ea6-8d2b-180096836d7b` and `account users` (SELECT / EXECUTE).

## Ground rules (all tracks)
- Worktree: `git -C /Users/laurence.ryszka/vibe/exposure-response-workbench worktree add /Users/laurence.ryszka/vibe/erw-wt-<track> -b <track> main`. Work, commit and push your branch there. **Do not merge to main** — the main session merges.
- **Never run `databricks bundle deploy` or `bundle run`** from a track (it would delete other tracks' jobs). Run notebooks as one-off serverless runs (`databricks workspace import-dir` your files to `/Workspace/Users/laurence.ryszka@databricks.com/erw-staging/<track>/` then `databricks jobs submit` with a serverless notebook task), or SQL via the Statement Execution API. The main session does one consolidated bundle deploy at the end.
- **Do not edit** `DECISIONS.md`, `README.md`, `docs/REVIEW/*`, `docs/DEMO_DOC.md`, `docs/STORY_CARDS.md`. Write your notes to `docs/notes/<track>.md` (what you built, object names, how to run, gotchas, verification results). The main session consolidates.
- Keep existing hero numbers intact (Var 59, Alpine 122 = IT 69 / AT 53, ramp 0/73/90/105, Var DECLINED / Munich BOUND).

## Contract A — Active zones (owner: T2 `uk-flood-uw`)
- View `mv_active_zones`: `zone_id STRING, event_id STRING, peril_code STRING, country_code STRING, source STRING, is_live BOOLEAN, severity STRING, active_from TIMESTAMP, footprint_wkt STRING (EPSG:4326), ingested_at TIMESTAMP` — one row per active event segment (all perils/sources, incl. the new UK EA flood warnings).
- Function `fn_zone_check(p_lat DOUBLE, p_lon DOUBLE, p_buffer_m INT)` → `TABLE(in_zone BOOLEAN, zone_id STRING, event_id STRING, peril_code STRING, source STRING, severity STRING, distance_m DOUBLE, active_from TIMESTAMP)` — nearest active zone; `in_zone` = within p_buffer_m. Governed, deterministic, ST_/EPSG:3035 (use the UK-appropriate metric CRS EPSG:27700 for GB points if more accurate, document it).
- T2 creates both **first** (within its first steps) against the existing `2_event_footprint`, so the agent track can see them; UK rows flow in once the EA adapter runs.
- New notebooks owned by T2 in this repo: `notebooks/14_feed_ea_flood.py`, `notebooks/51_active_zones.py`. UK events land in `2_event_footprint` with `source='EA'` (frozen fallback `EVT_EA_SAMPLE_UK`, is_live=false).

## Contract B — Sources & data quality (owner: T1 `ingest-dq`)
- Table `ref_dq_threshold` (source_id, check_name, threshold, comparator, description).
- Append-only table `gov_feed_dq_log`: `run_id STRING, source_id STRING, pulled_at TIMESTAMP, n_records INT, check_name STRING, observed DOUBLE, threshold DOUBLE, passed BOOLEAN, decision STRING ('ACCEPTED'|'REJECTED'|'FALLBACK_FROZEN'), reason STRING` (one row per check per batch).
- Table `1_feed_quarantine`: rejected batches (run_id, source_id, pulled_at, reason, payload_excerpt).
- View `gov_source_catalog`: `source_id, source_name, peril_code, provider, carries STRING, mode STRING ('REAL-TIME'|'BATCH'|'FROZEN SAMPLE'), cadence STRING, licence STRING, last_ingested_at TIMESTAMP, last_records INT, last_decision STRING, is_live BOOLEAN` — one row per source incl. EA (T1 adds the EA row by contract even before T2 lands; `source_id='EA'`).
- Functions `fn_source_status()` → the catalog rows; `fn_dq_recent(p_days INT)` → recent batch decisions (one row per batch: run_id, source_id, pulled_at, n_records, decision, reason, failed_checks).
- Python helper in `notebooks/10_feeds_common.py`: `dq_gate(source_id, records, checks=None) -> (accepted_records, decision: dict)` — writes `gov_feed_dq_log` (+ quarantine on reject). Adapters call it before writing `2_event_footprint`. T2's EA adapter must call `dq_gate` if defined (`if 'dq_gate' in globals()`), else pass through.
- A deterministic **seeded rejected batch** (e.g. a corrupted feed pull with ~38% of records outside the Europe/UK bounding box vs a 5% threshold) re-seeded by the reset, so the story "this batch was rejected" is always visible.
- T1 owns `notebooks/10_feeds_common.py`, adapters `11_*`, `11b_*`, `12_*`, `13_*`, new `notebooks/15_dq_and_sources.py`, and `resources/feeds_job.yml` + `resources/reset_job.yml` (add tasks for T2's `14_feed_ea_flood` and `51_active_zones` in the right order).

## Contract C — Agents (owner: T3 `agents`)
- ONE served Agent-Framework endpoint **`exposure-response-agent`** (supervisor + specialists inside one ResponsesAgent), served via Model Serving / UC AI Gateway, tools from the **Databricks-managed UC-functions MCP server** for this schema (so new functions from T1/T2 are picked up), each specialist restricted to its tool subset.
- Specialists: **Event Watch** (sources, live feeds, news radar), **Exposure Analyst** (who's in the path, bands, countries), **Reinsurance Analyst** (gross → net, coverholders), **Alerts Officer** (who's told, deltas, dispatch log), **Underwriting Guard** (bind / zone checks), **Data Steward** (source status, DQ decisions, rejected batches).
- Response: OpenAI-Responses-compatible; `custom_outputs = {"specialist": str, "route_reason": str, "tools": [{"name": str, "args": dict}]}`.
- Table `ref_agent_roster`: `agent_key, name, role, responsibilities ARRAY<STRING>, skills ARRAY<STRING>` (UC function names), `reads ARRAY<STRING>` (tables), `may_do ARRAY<STRING>, may_not_do ARRAY<STRING>, example_questions ARRAY<STRING>, endpoint STRING`.
- T3 owns new `notebooks/70_*.py` files, `docs/AGENTS.md`. The existing in-app agent (`app/server/agent.py`) stays as the fallback — T3 does not edit `app/`.

## Contract D — Maps (owner: T4 `maps`)
- A published Lakeview dashboard (embed_credentials=true) with event maps for the hero events: Var wildfire (live-follow, reuse `live_fire_state`), Alpine flood (cross-border), Windstorm Celine, and the UK EA flood. Footprints rendered as fill points / cells; insured homes as coloured points (in path vs clear). Legible from the back of a room.
- Table `ref_dashboard_pages`: `event_id STRING, dashboard_id STRING, page_id STRING, embed_url STRING, title STRING` (one row per event map; `event_id='*'` for a book-wide overview if built).
- T4 owns new `resources/dashboards/event_maps.lvdash.json` and `notebooks/62_map_views.py`. Keep the existing Live Fire Tracker working.

## Contract E — Front end (owner: T5 `frontend`)
- Owns `app/` only. Builds: Learn (architecture, data flow, theory & regulation), Data sources & quality screen (Contract B), Agents screen (Contract C roster + chat via `exposure-response-agent` showing which specialist answered and the tools used; fall back to the in-app agent if the endpoint is unavailable), maps → embedded dashboards (Contract D; remove the in-app SVG maps from the main flow), and UK flood display (events with 0 homes in our EU book go in a compact "also watching" list tagged "active zone published to underwriting").
- Deploys the app ONLY from a staging copy: `databricks workspace import-dir <worktree>/app /Workspace/Users/laurence.ryszka@databricks.com/erw-staging/frontend-app --overwrite` then `databricks apps deploy exposure-response-workbench --source-code-path <that path>`.

## T2 (cont.) — Underwriting workbench (separate repo, T2 owns)
- `/Users/laurence.ryszka/vibe/underwriting-workbench`: add a governed disaster-zone check at quote/bind (postcode district centroid from `ref_postcode_centroid` → `exposure_response.fn_zone_check`), surface it in the app (REFER/DECLINE with the reason: "property is in an active Environment Agency flood warning area — <name>, issued <time>"), and add it to Referral Control as a rule. Grant the UW app SP USE SCHEMA + EXECUTE + SELECT on what `fn_zone_check` needs (UC SQL functions run with invoker rights). Its own bundle deploy is fine (separate repo).
