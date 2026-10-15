#!/usr/bin/env python3
"""Build (idempotently) the Databricks SQL alert that emails the stakeholder digest — no SMTP needed.

Creates / updates:
  1. view  `mv_alert_email`      — the latest alert sweep's breached events, one row per event
  2. table `gov_alert_delivery`  — append-only record of each real send (written by notebooks/31_alert_email.py)
  3. saved query "Exposure — alert email (latest sweep)"
  4. SQL alert   "Bricksurance exposure alert" — fires when the query returns rows; custom subject + HTML body

The alert is evaluated by the `send_alert_email` task of the `exposure_30_alerts` job (sql_task.alert), which
subscribes the recipients, so Databricks sends the email itself. Prints the alert id for databricks.yml
(`alert_email_id`).

Usage:  python3 resources/alerts/build_alert_email.py [--profile DEV]
"""
import json, subprocess, sys

PROFILE = sys.argv[sys.argv.index("--profile") + 1] if "--profile" in sys.argv else "DEV"
WAREHOUSE = "a3b61648ea4809e3"
FQN = "lr_dev_aws_us_catalog.exposure_response"
APP_URL = "https://exposure-response-workbench-7474656169654171.aws.databricksapps.com"
QUERY_NAME = "Exposure — alert email (latest sweep)"
ALERT_NAME = "Bricksurance exposure alert"
PARENT = "/Workspace/Users/laurence.ryszka@databricks.com/exposure_response_alerts"


def api(method, path, body=None):
    cmd = ["databricks", "api", method, path, "-p", PROFILE]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"{method} {path}: {out.stderr.strip() or out.stdout.strip()}")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def sql(statement):
    r = api("post", "/api/2.0/sql/statements",
            {"warehouse_id": WAREHOUSE, "statement": statement, "wait_timeout": "50s"})
    st = r.get("status", {})
    if st.get("state") != "SUCCEEDED":
        raise RuntimeError(f"SQL failed: {st}")
    return r


# 1) the view the email reports on: the latest sweep's breached events
sql(f"""
CREATE OR REPLACE VIEW {FQN}.mv_alert_email
COMMENT 'Latest alert sweep: events that crossed their exposure threshold, one row per event (cross-border in one row). Drives the Databricks SQL alert that emails the stakeholder digest.'
AS
WITH latest AS (SELECT max(run_id) AS run_id FROM {FQN}.gov_alert_dispatch)
SELECT
  count(*) OVER ()                                   AS events_above_threshold,
  d.event_name                                       AS event,
  CASE d.peril_code WHEN 'FIRE' THEN 'Wildfire' WHEN 'FLOOD' THEN 'Flood' WHEN 'STORM' THEN 'Windstorm'
       ELSE d.peril_code END                         AS peril,
  d.threatened_count                                 AS homes_in_path,
  round(d.sum_insured_eur / 1e6, 1)                  AS sum_insured_eur_m,
  round(d.net_eur / 1e6, 1)                          AS net_retained_eur_m,
  d.countries                                        AS countries,
  d.new_count                                        AS newly_threatened,
  date_format(d.sent_at, 'yyyy-MM-dd HH:mm')         AS sweep_utc,
  d.run_id                                           AS run_id
FROM {FQN}.gov_alert_dispatch d JOIN latest l ON d.run_id = l.run_id
WHERE d.breached
ORDER BY d.gross_eur DESC
""")

# 2) the append-only delivery log (what actually went out, by which channel, to whom)
sql(f"""
CREATE TABLE IF NOT EXISTS {FQN}.gov_alert_delivery (
  run_id STRING COMMENT 'alert sweep run the email reported on (joins gov_alert_dispatch.run_id)',
  alert_id STRING, channel STRING, recipients STRING, n_events INT,
  alert_state STRING, delivered_at TIMESTAMP
) COMMENT 'Append-only record of each real stakeholder send: which sweep, which channel (Databricks SQL alert email), to whom, when. The dispatch log records the decision; this records the delivery.'
""")
for grantee in ["account users", "fc3ac835-8393-4ea6-8d2b-180096836d7b"]:
    for obj, priv in [(f"{FQN}.mv_alert_email", "SELECT"), (f"{FQN}.gov_alert_delivery", "SELECT")]:
        sql(f"GRANT {priv} ON TABLE {obj} TO `{grantee}`")

# 3) the saved query (idempotent by display name)
subprocess.run(["databricks", "workspace", "mkdirs", PARENT, "-p", PROFILE], capture_output=True)
query_text = (f"SELECT event AS `Event`, peril AS `Peril`, homes_in_path AS `Homes in path`, "
              f"sum_insured_eur_m AS `Sum insured (EUR m)`, net_retained_eur_m AS `We keep after reinsurance (EUR m)`, "
              f"countries AS `Countries`, newly_threatened AS `New since last sweep`, sweep_utc AS `Sweep (UTC)` "
              f"FROM {FQN}.mv_alert_email")
queries = api("get", "/api/2.0/sql/queries?page_size=100").get("results", [])
q = next((x for x in queries if x.get("display_name") == QUERY_NAME), None)
qbody = {"display_name": QUERY_NAME, "warehouse_id": WAREHOUSE, "query_text": query_text,
         "description": "Latest alert sweep — events above their exposure threshold. Feeds the stakeholder email alert."}
if q:
    q = api("patch", f"/api/2.0/sql/queries/{q['id']}",
            {"update_mask": "display_name,warehouse_id,query_text,description", "query": qbody})
else:
    q = api("post", "/api/2.0/sql/queries", {"query": {**qbody, "parent_path": PARENT}})
query_id = q["id"]

# 4) the alert: fires when the latest sweep has any breached event; Databricks emails the subscribers
subject = "Bricksurance exposure alert — events above their exposure threshold"
body = (
    "<p><b>Bricksurance SE — Exposure &amp; Event Response</b></p>"
    "<p>These events have crossed their exposure threshold. One alert per event, across borders — "
    "the whole picture in one place, before anyone opens a laptop.</p>"
    "{{QUERY_RESULT_TABLE}}"
    f"<p><a href=\"{APP_URL}\">Open the Exposure &amp; Event Response workbench</a> to track each event live.</p>"
    "<p style=\"color:#64748b;font-size:12px\">Sent by a Databricks SQL alert on the governed alert sweep "
    "(gov_alert_dispatch). Demonstration on synthetic data — Bricksurance SE is a fictional insurer.</p>"
)
alert_body = {
    "display_name": ALERT_NAME,
    "query_id": query_id,
    "condition": {"op": "GREATER_THAN",
                  "operand": {"column": {"name": "Homes in path"}},   # first row = the largest event
                  "threshold": {"value": {"double_value": 0}},
                  "empty_result_state": "OK"},
    "custom_subject": subject,
    "custom_body": body,
    "notify_on_ok": False,
    "seconds_to_retrigger": 60,   # re-arm after a minute so each demo sweep can email again
}
alerts = api("get", "/api/2.0/sql/alerts?page_size=100").get("results", [])
a = next((x for x in alerts if x.get("display_name") == ALERT_NAME), None)
if a:
    a = api("patch", f"/api/2.0/sql/alerts/{a['id']}",
            {"update_mask": "display_name,query_id,condition,custom_subject,custom_body,notify_on_ok,seconds_to_retrigger",
             "alert": alert_body})
else:
    a = api("post", "/api/2.0/sql/alerts", {"alert": {**alert_body, "parent_path": PARENT}})
print(json.dumps({"query_id": query_id, "alert_id": a["id"], "alert_state": a.get("state")}))
