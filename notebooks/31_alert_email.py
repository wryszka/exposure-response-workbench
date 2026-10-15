# Databricks notebook source
# MAGIC %md
# MAGIC # 31 · Record the stakeholder email (after the Databricks SQL alert fires)
# MAGIC The `exposure_30_alerts` job runs the sweep (`30_alerts`), then evaluates the Databricks SQL alert
# MAGIC **"Bricksurance exposure alert"** (`send_alert_email` task, `sql_task.alert`). If the latest sweep has events
# MAGIC above threshold the alert triggers and **Databricks emails the subscribers itself** — no SMTP server.
# MAGIC This notebook then checks the alert really triggered and appends one row to `gov_alert_delivery`
# MAGIC (append-only: which sweep, channel, recipients, when). The app shows the sweep as **SENT** from that row.
# MAGIC The alert definition is built by `resources/alerts/build_alert_email.py`.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("alert_id", "")
dbutils.widgets.text("recipients", "laurence.ryszka@databricks.com")
catalog, schema = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
alert_id, recipients = dbutils.widgets.get("alert_id").strip(), dbutils.widgets.get("recipients").strip()
fqn = f"`{catalog}`.`{schema}`"

# COMMAND ----------

import datetime
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
alert = w.alerts.get(alert_id)
state = alert.state.value if alert.state else "UNKNOWN"
trig = alert.trigger_time   # ISO string, set when the alert last triggered
print(f"alert {alert.display_name}: state={state} trigger_time={trig}")

latest = spark.sql(f"SELECT max(run_id) AS r FROM {fqn}.gov_alert_dispatch").collect()[0]["r"]
n_events = spark.sql(f"SELECT count(*) AS n FROM {fqn}.mv_alert_email").collect()[0]["n"]

recent = False
if trig:
    t = datetime.datetime.fromisoformat(trig.replace("Z", "+00:00"))
    recent = (datetime.datetime.now(datetime.timezone.utc) - t) <= datetime.timedelta(minutes=20)

if state == "TRIGGERED" and recent and n_events > 0:
    spark.sql(f"""
      INSERT INTO {fqn}.gov_alert_delivery
      SELECT '{latest}', '{alert_id}', 'DATABRICKS_ALERT_EMAIL', '{recipients}', {int(n_events)},
             '{state}', current_timestamp()""")
    print(f"recorded delivery: run {latest}, {n_events} events → {recipients}")
    dbutils.notebook.exit(f"SENT {n_events} events to {recipients}")
else:
    if n_events > 0:
        # Thresholds were breached but the email did not go out — record it, so the governance queue raises an
        # operational incident (fn_reportable rule 8). Readers of delivered sends filter alert_state = 'TRIGGERED'.
        st = state if state != "TRIGGERED" else "TRIGGERED_STALE"
        spark.sql(f"""
          INSERT INTO {fqn}.gov_alert_delivery
          SELECT '{latest}', '{alert_id}', 'DATABRICKS_ALERT_EMAIL', '{recipients}', {int(n_events)},
                 '{st}', current_timestamp()""")
        print(f"breached but NOT delivered: run {latest}, {n_events} events, state={st} — recorded for review")
    else:
        print("nothing above threshold in this run — nothing to send")
    dbutils.notebook.exit(f"NOT SENT (state={state}, recent={recent}, events={n_events})")
