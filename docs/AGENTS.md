# The agent team

**One endpoint, one supervisor, six specialists — every number from a governed function.**

The Exposure & Event Response workbench has an AI agent team served on Databricks as a single Agent-Framework
endpoint, **`exposure-response-agent`** (Model Serving, through the Unity Catalog AI Gateway). It is
*advise-only*: the deterministic governed functions decide; the agents find the right function, call it and
explain the answer in plain words.

```
question ──► Supervisor (fast Claude — routing only, no tools)
               │  picks one specialist (or chains two) and records why
               ▼
Event Watch · Exposure Analyst · Reinsurance Analyst · Alerts Officer · Underwriting Guard · Data Steward
               │  each: own responsibilities, own limits, a RESTRICTED subset of governed tools
               ▼
Governed Unity Catalog functions, executed on the SQL warehouse
```

## How routing works
1. The **Supervisor** (Claude Haiku via the Foundation Model API) reads the question and the team roster and returns
   `{"specialists": [...], "route_reason": "..."}`. It never answers itself. If its reply can't be parsed, a
   deterministic keyword router takes over (so routing never fails silently).
2. The chosen **specialist** (Claude Sonnet) runs a tool loop with *only its own tools*. When two specialists are
   chained (e.g. "who is in the path, and what will it cost us?"), the second receives the first's answer as context.
3. The response is OpenAI-Responses-compatible. `custom_outputs` carries `specialist`, `route_reason` and the
   `tools` called (`name` + `args`) — the app shows these so the audience sees *who* answered and *from what*.

## The specialists

| Specialist | Responsible for | Skills (governed functions) | Must not |
|---|---|---|---|
| **Event Watch** | Active events, live vs frozen feeds, where each came from, early signals that beat the satellite, how a developing event is progressing | `fn_events`, `fn_news_radar`, `fn_source_status`, `fn_progression_props` | Promote a news signal to an event (a person clicks Promote); change thresholds |
| **Exposure Analyst** | Homes in the path at 50 / 100 / 200 m, by country (one event across borders), sum insured at risk, the live fire's homes per update | `fn_events`, `fn_exposure_in_footprint`, `fn_event_exposure_summary`, `fn_progression_props` | Estimate a loss or net; decide on cover |
| **Reinsurance Analyst** | Modelled gross → ceded → net through the Property Cat XL (EUR 25m retention, four layers to EUR 400m); coverholder split | `fn_events`, `fn_gross_to_net`, `fn_exposure_by_coverholder`, `fn_event_exposure_summary` | Present modelled net as a signed-off capital figure; change treaty terms |
| **Alerts Officer** | Who was told, when and why; what changed since the last snapshot; whether an alert was sent or built-but-not-sent | `fn_events`, `fn_alert_log`, `fn_event_delta` | Send alerts (a governed job does); change recipients or thresholds |
| **Underwriting Guard** | Can cover be bound here right now? Looks the place up, checks every active zone (incl. UK flood warnings) and the developing fire, explains refer/decline | `fn_place_lookup`, `fn_zone_check`, `fn_bind_check`, `fn_events` | Override a decline or approve cover (only an underwriter can); invent coordinates |
| **Data Steward** | The data sources, how fresh each is, the quality checks on every batch, and any batch rejected or quarantined — with the reason | `fn_source_status`, `fn_dq_recent`, `fn_events` | Accept a rejected batch or lower a threshold; edit feed data |

The full card for each (responsibilities, skills, tables read, may / may-not, example questions) is the table
**`exposure_response.ref_agent_roster`** — the app's Agents screen reads it.

## Guardrails (all specialists)
- Answer **only** from tool results. Never invent, estimate, recall or extrapolate a number, date, place or coordinate.
- Modelled losses are labelled "modelled". Money rounded to EUR 0.1m.
- Plain language, answer first, then a few bullets, then one "Source:" line naming the governed functions used.
- If a needed function isn't deployed yet, the specialist says that data isn't connected — it does not improvise.
- Every function call runs with the endpoint's own Unity Catalog permissions (automatic authentication
  passthrough over the declared resources), so the agents can see only what they were granted.

## How it's built and served
- Code: `notebooks/70_exposure_agent.py` (the ResponsesAgent — team definition, prompts, routing, tool loop).
- Build/deploy: `notebooks/70_build_deploy_agent.py` — reads each function's signature and comment from Unity
  Catalog, smoke-tests the team in the notebook, logs it with MLflow (tracing on), registers
  `exposure_response.exposure_response_agent@prod`, deploys with `agents.deploy` (scale-to-zero), grants the app
  CAN_QUERY, and writes `ref_agent_roster`. **Re-run it after adding a governed function** — specialists only get
  functions that exist at build time.
- Two supporting functions: `notebooks/70_agent_tools.py` — `fn_alert_log` (the alert record per event) and
  `fn_place_lookup` (place name or UK postcode district → coordinates).
- Functions execute on the SQL warehouse via the Statement Execution API (the exposure maths uses `ST_` geospatial,
  which runs on a SQL warehouse, not on Spark Connect).
- The same governed functions are also reachable from any external agent through the Databricks-managed MCP server
  for this schema (`/api/2.0/mcp/functions/lr_dev_aws_us_catalog/exposure_response`).

## Warming it up before a demo
The endpoint scales to zero when idle, so the **first call after a quiet period takes a few minutes** while it
starts. About **10 minutes before the room**: open the app's Agents screen and ask one starter question (or call the
endpoint once). Leave it — it stays warm while in use. To keep it always-on for a long session, set the endpoint's
scale-to-zero off in Serving, and back on afterwards.
