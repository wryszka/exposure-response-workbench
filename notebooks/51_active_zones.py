# Databricks notebook source
# MAGIC %md
# MAGIC # 51 · Active zones — the surface other workbenches read
# MAGIC
# MAGIC This workbench **publishes** where catastrophes are happening right now, so other workbenches can act on it.
# MAGIC The underwriting workbench calls `fn_zone_check` at quote/bind: if a property sits in an active zone, cover is
# MAGIC referred instead of bound.
# MAGIC
# MAGIC - **`mv_active_zones`** — one row per active event segment, every peril and source (EU synthetic + live
# MAGIC   MeteoAlarm / FIRMS / EFFIS / Copernicus EMS, and the UK Environment Agency flood warnings).
# MAGIC - **`fn_zone_check(p_lat, p_lon, p_buffer_m)`** — the nearest active zone to a point and whether the point is
# MAGIC   within `p_buffer_m` metres of it. Always returns exactly one row (`in_zone=false` with nulls when nothing is
# MAGIC   active).
# MAGIC
# MAGIC Distances are true metres in **EPSG:3035** (ETRS89 LAEA Europe), the same metric CRS as the rest of the
# MAGIC workbench. It covers Great Britain too; linear-scale distortion at UK latitudes is around 1%, which is
# MAGIC immaterial for a "within N metres of a warning area" test. `ST_*` DDL runs on the SQL warehouse via the
# MAGIC Statement Execution API (it cannot run through serverless Spark Connect).

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "fc3ac835-8393-4ea6-8d2b-180096836d7b")
dbutils.widgets.text("extra_grantees", "")  # comma-separated SP application ids (e.g. the underwriting app SP)

import time
from databricks.sdk import WorkspaceClient

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
extra = [x.strip() for x in dbutils.widgets.get("extra_grantees").split(",") if x.strip()]
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
# MAGIC %md ### The published surface

# COMMAND ----------

MV_ACTIVE_ZONES = """CREATE OR REPLACE VIEW mv_active_zones
COMMENT 'Published active catastrophe zones: one row per active event segment, all perils and sources (synthetic EU events, live MeteoAlarm/FIRMS/EFFIS/Copernicus EMS, UK Environment Agency flood warnings). Read by other workbenches (e.g. underwriting at bind) via fn_zone_check.'
AS SELECT
  concat(event_id, '#', substr(sha2(footprint_wkt, 256), 1, 8)) AS zone_id,
  event_id, peril_code, country_code, source, is_live,
  CASE WHEN source = 'EA' THEN coalesce(nullif(regexp_extract(source_detail, '^EA ([A-Za-z ]+?) \\\\(severity', 1), ''), 'Flood Warning')
       WHEN source = 'METEOALARM' THEN 'Weather warning'
       ELSE 'Active event' END AS severity,
  coalesce(detected_at, CAST(event_date AS TIMESTAMP)) AS active_from,
  footprint_wkt, ingested_at, event_name, source_detail
FROM `2_event_footprint`
WHERE footprint_wkt IS NOT NULL"""

FN_ZONE_CHECK = """CREATE OR REPLACE FUNCTION fn_zone_check(p_lat DOUBLE, p_lon DOUBLE, p_buffer_m INT)
RETURNS TABLE(in_zone BOOLEAN, zone_id STRING, event_id STRING, peril_code STRING, source STRING, severity STRING, distance_m DOUBLE, active_from TIMESTAMP)
COMMENT 'Disaster-zone check for any workbench: the nearest active catastrophe zone to a point (lat/lon, WGS84) and whether the point is within p_buffer_m metres of it (true metres, EPSG:3035). Always returns one row; in_zone=false with nulls when no zone is active. Used by underwriting at quote/bind to refer cover inside an active zone.'
RETURN WITH pt AS (SELECT ST_Transform(ST_SetSRID(ST_Point(p_lon, p_lat), 4326), 3035) AS g),
best AS (
  SELECT z.zone_id, z.event_id, z.peril_code, z.source, z.severity, z.active_from,
         ST_Distance(pt.g, ST_Transform(ST_SetSRID(ST_GeomFromText(z.footprint_wkt), 4326), 3035)) AS d
  FROM mv_active_zones z CROSS JOIN pt
  ORDER BY d ASC LIMIT 1)
SELECT coalesce(best.d <= p_buffer_m, false) AS in_zone, best.zone_id, best.event_id, best.peril_code,
       best.source, best.severity, best.d AS distance_m, best.active_from
FROM (SELECT 1 AS one) o LEFT JOIN best ON true"""

run_wh(MV_ACTIVE_ZONES, "mv_active_zones")
run_wh(FN_ZONE_CHECK, "fn_zone_check")
print("mv_active_zones + fn_zone_check ready")

# COMMAND ----------
# MAGIC %md ### Grants — UC SQL functions run with invoker rights, so callers need SELECT on what they read

# COMMAND ----------

grantees = ["`account users`"]
if app_sp:
    grantees.append(f"`{app_sp}`")
grantees += [f"`{x}`" for x in extra]
for who in grantees:
    for stmt in (
        f"GRANT USE SCHEMA ON SCHEMA {catalog}.{schema} TO {who}",
        f"GRANT SELECT ON VIEW mv_active_zones TO {who}",
        f"GRANT SELECT ON TABLE `2_event_footprint` TO {who}",
        f"GRANT EXECUTE ON FUNCTION fn_zone_check TO {who}",
    ):
        run_wh(stmt, f"grant {who}")
print("grants applied:", grantees)

# COMMAND ----------
# MAGIC %md ### Verify

# COMMAND ----------

r = run_wh("SELECT source, count(*) n FROM mv_active_zones GROUP BY source ORDER BY source", "zones by source")
for row in r.result.data_array:
    print("zones", row)
r = run_wh("SELECT * FROM fn_zone_check(43.586962, 6.742300, 200)", "zone check Var")
print("Var hero point:", r.result.data_array)
