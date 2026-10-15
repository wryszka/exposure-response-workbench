# Databricks notebook source
# MAGIC %md
# MAGIC # 70 · Agent tools — two governed functions the agent team needs
# MAGIC
# MAGIC The Event Response agent team (notebook `70_build_deploy_agent`) answers ONLY by calling governed
# MAGIC Unity Catalog functions. Most already exist (exposure, gross→net, alerts delta, news radar, bind check,
# MAGIC zone check). Two gaps are closed here:
# MAGIC - **`fn_alert_log(p_event_id)`** — who was alerted, when, on which threshold, with what headline numbers
# MAGIC   (read from the append-only `gov_alert_dispatch`). Used by the *Alerts Officer*.
# MAGIC - **`fn_place_lookup(p_place)`** — resolves a place name (demo gazetteer) or a UK postcode district
# MAGIC   (underwriting workbench centroids) to coordinates, so the *Underwriting Guard* never invents a lat/long.
# MAGIC
# MAGIC Plain SQL (no `ST_`), created on the SQL warehouse via the Statement Execution API like the other notebooks.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "fc3ac835-8393-4ea6-8d2b-180096836d7b")
dbutils.widgets.text("uw_schema", "underwriting_workbench")

import time
from databricks.sdk import WorkspaceClient

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
uw_schema = dbutils.widgets.get("uw_schema").strip()
fqn = f"{catalog}.{schema}"
_w = WorkspaceClient()


def run_wh(stmt, label="", max_wait=300):
    r = _w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=warehouse_id, catalog=catalog, schema=schema, wait_timeout="50s")
    waited = 0
    while r.status.state.value in ("PENDING", "RUNNING") and waited < max_wait:
        time.sleep(3); waited += 3
        r = _w.statement_execution.get_statement(r.statement_id)
    if r.status.state.value != "SUCCEEDED":
        raise RuntimeError(f"{label}: {r.status.state.value} — {r.status.error.message if r.status.error else '?'}")
    return r

# COMMAND ----------

# MAGIC %md ## `fn_alert_log` — the stakeholder-alert record, per event

# COMMAND ----------

# The real send is recorded by the alert-sweep job (Databricks SQL alert → email) in gov_alert_delivery, one row
# per sweep that was delivered. Make sure it exists so this function can always be created.
run_wh(f"""
CREATE TABLE IF NOT EXISTS {fqn}.gov_alert_delivery (
  run_id STRING, alert_id STRING, channel STRING, recipients STRING, n_events INT, alert_state STRING,
  delivered_at TIMESTAMP)
""", "gov_alert_delivery")

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_alert_log(p_event_id STRING DEFAULT NULL)
RETURNS TABLE (
  event_id STRING, event_name STRING, peril_code STRING, status STRING, breached BOOLEAN,
  threshold_rule STRING, threatened_count BIGINT, sum_insured_eur DOUBLE, gross_eur DOUBLE, net_eur DOUBLE,
  countries STRING, n_countries INT, top_coverholder STRING, new_count BIGINT, still_count BIGINT,
  gone_count BIGINT, channel STRING, recipients STRING, sent_at TIMESTAMP)
COMMENT 'Stakeholder alert record (append-only gov_alert_dispatch): for each event, the LATEST alert decision — whether it breached its threshold (threshold_rule), the headline numbers in the digest (threatened homes, sum insured, modelled gross/net), the countries covered (one alert per event even across borders), new/still/no-longer threatened counts vs the previous snapshot, the recipients (claims, underwriting, exposure management) and when it was dispatched. status: SENT = the alert email was delivered by a Databricks SQL alert (channel DATABRICKS_ALERT_EMAIL; recipients shows who it went to; sent_at = delivery time); RENDERED_NOT_SENT = built and audited but not delivered for that sweep (e.g. the silent morning reset); SUPPRESSED = below threshold. Pass p_event_id (e.g. EVT_FLOOD_ALPS) or NULL for all events. Used by the Alerts Officer agent.'
RETURN
  SELECT x.event_id, x.event_name, x.peril_code,
         CASE WHEN x.status = 'RENDERED_NOT_SENT' AND dl.run_id IS NOT NULL THEN 'SENT' ELSE x.status END AS status,
         x.breached, x.threshold_rule,
         x.threatened_count, x.sum_insured_eur, x.gross_eur, x.net_eur, x.countries, x.n_countries, x.top_coverholder,
         x.new_count, x.still_count, x.gone_count,
         CASE WHEN dl.run_id IS NOT NULL AND x.breached THEN dl.channel ELSE x.channel END AS channel,
         CASE WHEN dl.run_id IS NOT NULL AND x.breached
              THEN concat(coalesce(x.recipients, 'stakeholders'), ' — delivered to: ', dl.recipients)
              ELSE x.recipients END AS recipients,
         CASE WHEN dl.run_id IS NOT NULL AND x.breached THEN dl.delivered_at ELSE x.sent_at END AS sent_at
  FROM (
    SELECT d.*, row_number() OVER (PARTITION BY d.event_id ORDER BY d.sent_at DESC) AS rn
    FROM {fqn}.gov_alert_dispatch d
    WHERE p_event_id IS NULL OR d.event_id = p_event_id
  ) x
  LEFT JOIN (SELECT run_id, max(channel) AS channel, max(recipients) AS recipients, max(delivered_at) AS delivered_at
             FROM {fqn}.gov_alert_delivery GROUP BY run_id) dl
    ON dl.run_id = x.run_id
  WHERE x.rn = 1
  ORDER BY sent_at DESC
""", "fn_alert_log")
print("fn_alert_log created")

# COMMAND ----------

# MAGIC %md ## `fn_place_lookup` — place name or UK postcode district → coordinates

# COMMAND ----------

# The UK postcode-district centroids live in the underwriting workbench (shared catalog). Only reference them if
# that table is present, so this notebook still runs in a workspace without the underwriting workbench.
def _table_exists(full):
    try:
        _w.tables.get(full)
        return True
    except Exception:
        return False

uw_centroids = f"{catalog}.{uw_schema}.ref_postcode_centroid"
has_uw = _table_exists(uw_centroids)
uk_branch = f"""
  UNION ALL
  SELECT postcode_district AS place, 'UK postcode district' AS kind, lat, lon,
         'underwriting workbench postcode centroids' AS source
  FROM {uw_centroids}
  WHERE upper(replace(postcode_district, ' ', '')) = upper(replace(trim(p_place), ' ', ''))""" if has_uw else ""

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_place_lookup(p_place STRING)
RETURNS TABLE (place STRING, kind STRING, lat DOUBLE, lon DOUBLE, source STRING)
COMMENT 'Resolve a place to coordinates for bind / zone checks — never guess a latitude/longitude. Matches the demo gazetteer (European towns and regions, e.g. Var, Nice, Munich, Turin, Tyrol) by name (case-insensitive, partial match) and UK postcode districts (e.g. YO1, CA3) from the underwriting workbench centroids. Returns up to 5 candidates with lat (WGS84), lon, the kind of match and its source. Feed lat/lon into fn_zone_check (any active zone, incl. UK flood warnings) or fn_bind_check (the developing Var fire). Used by the Underwriting Guard agent.'
RETURN
  SELECT place, kind, lat, lon, source FROM (
    SELECT place_name AS place, 'gazetteer place' AS kind, lat, lon, 'demo gazetteer (ref_gazetteer)' AS source
    FROM {fqn}.ref_gazetteer
    WHERE lower(place_name) LIKE concat('%', lower(trim(p_place)), '%')
    {uk_branch}
  ) LIMIT 5
""", "fn_place_lookup")
print("fn_place_lookup created (UK postcode branch:", has_uw, ")")

# COMMAND ----------

# MAGIC %md ## Grants (CREATE OR REPLACE drops them)

# COMMAND ----------

for fn in ("fn_alert_log", "fn_place_lookup"):
    for who in [p for p in ("account users", app_sp) if p]:
        run_wh(f"GRANT EXECUTE ON FUNCTION {fqn}.{fn} TO `{who}`", f"grant {fn} {who}")
print("grants applied")

# COMMAND ----------

# MAGIC %md ## Smoke test

# COMMAND ----------

def q(sql):
    r = run_wh(sql, sql[:60])
    return r.result.data_array if r.result else []

print(q(f"SELECT event_id, status, n_countries, countries FROM {fqn}.fn_alert_log('EVT_FLOOD_ALPS')"))
print(q(f"SELECT * FROM {fqn}.fn_place_lookup('Munich')"))
if has_uw:
    print(q(f"SELECT postcode_district FROM {uw_centroids} LIMIT 1"))
