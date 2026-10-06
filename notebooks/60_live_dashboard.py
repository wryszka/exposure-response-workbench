# Databricks notebook source
# MAGIC %md
# MAGIC # 60 · Live Fire Tracker — AI/BI dashboard data layer
# MAGIC
# MAGIC Backs the published AI/BI (Lakeview) dashboard **"Exposure — Live Fire Tracker"**
# MAGIC (id `01f1c169c0e0157496036dc765800f30`), which the app's **Track ▸** button opens.
# MAGIC
# MAGIC - `live_cursor` — one row holding the current event + tick the map shows.
# MAGIC - `live_fire_state` — a VIEW: insured homes within 12 km of the current fire tick,
# MAGIC   each classified `In fire zone` / `Near (<=200m)` / `Clear` via `ST_` over EPSG:3035.
# MAGIC   The dashboard reads this flat point set (lat/lon + band + sum_insured).
# MAGIC
# MAGIC **GOTCHA:** `ST_*` DDL/queries do NOT run via `spark.sql` on the serverless notebook
# MAGIC (Spark Connect rejection) — they run on the **SQL warehouse** via the Statement
# MAGIC Execution API. This notebook orchestrates the DDL onto the warehouse, same pattern as
# MAGIC notebook `02`. Deterministic + reproducible; advancing the cursor (t0→t3) is what makes
# MAGIC the dashboard "progress" as the fire spreads (near-real-time via the dashboard's refresh).

# COMMAND ----------
import os, time, requests
dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog"); dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3"); dbutils.widgets.text("t_index", "2")
CATALOG = dbutils.widgets.get("catalog"); SCHEMA = dbutils.widgets.get("schema")
WH = dbutils.widgets.get("warehouse_id"); T = int(dbutils.widgets.get("t_index"))
S = f"{CATALOG}.{SCHEMA}"
HOST = spark.conf.get("spark.databricks.workspaceUrl"); TOKEN = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()

def run_wh(sql: str):
    """Execute one statement on the SQL warehouse (so ST_* resolves); poll to terminal."""
    r = requests.post(f"https://{HOST}/api/2.0/sql/statements",
                      headers={"Authorization": f"Bearer {TOKEN}"},
                      json={"warehouse_id": WH, "statement": sql, "wait_timeout": "30s"}).json()
    sid = r.get("statement_id"); st = r.get("status", {}).get("state")
    while st in ("PENDING", "RUNNING"):
        time.sleep(2); r = requests.get(f"https://{HOST}/api/2.0/sql/statements/{sid}",
                                        headers={"Authorization": f"Bearer {TOKEN}"}).json()
        st = r.get("status", {}).get("state")
    if st != "SUCCEEDED":
        raise RuntimeError(f"{st}: {r.get('status', {}).get('error')}\nSQL: {sql[:200]}")
    return r

# COMMAND ----------
# cursor (current event + tick) — reset/seed
run_wh(f"CREATE TABLE IF NOT EXISTS {S}.live_cursor (event_id STRING, t_index INT)")
run_wh(f"DELETE FROM {S}.live_cursor")
run_wh(f"INSERT INTO {S}.live_cursor VALUES ('EVT_LIVE_VAR_FIRE', {T})")

# live_fire_state view (ST_ classification against the current tick's footprint)
# live_fire_state view — "in the path" uses EXACTLY the app's canonical predicate
# (fn_progression_props / mv_progression_exposure: within 200 m of the tick's footprint, EPSG:3035),
# so the dashboard KPIs and the app always show the same count + sum insured at every tick.
run_wh(f"""
CREATE OR REPLACE VIEW {S}.live_fire_state AS
WITH cur AS (SELECT event_id, t_index FROM {S}.live_cursor LIMIT 1),
f AS (SELECT ST_Transform(ST_SetSRID(ST_GeomFromText(ep.footprint_wkt),4326),3035) g, ep.as_of_ts, ep.t_index
      FROM {S}.`2_event_progression` ep JOIN cur ON ep.event_id=cur.event_id AND ep.t_index=cur.t_index),
d AS (SELECT p.insured_object_id, CAST(p.latitude AS double) latitude, CAST(p.longitude AS double) longitude,
             p.sum_insured, f.as_of_ts, f.t_index,
             ST_DWithin(ST_Transform(ST_SetSRID(ST_Point(p.longitude,p.latitude),4326),3035), f.g, 200) AS in_path,
             ST_Distance(ST_Transform(ST_SetSRID(ST_Point(p.longitude,p.latitude),4326),3035), f.g) AS distance_m
      FROM {S}.`3_property` p CROSS JOIN f)
SELECT insured_object_id, latitude, longitude, sum_insured, in_path, distance_m, t_index,
  CASE WHEN distance_m = 0 THEN 'In fire zone' WHEN in_path THEN 'Near (<=200m)' ELSE 'Clear' END AS band,
  as_of_ts, current_timestamp() AS last_refreshed
FROM d WHERE distance_m <= 12000
""")
run_wh(f"GRANT SELECT ON VIEW {S}.live_fire_state TO `account users`")
run_wh(f"GRANT SELECT ON TABLE {S}.live_cursor TO `account users`")
print(f"live_cursor set to tick {T}; live_fire_state refreshed.")

# COMMAND ----------
# MAGIC %md
# MAGIC **Advance the fire** (demo): re-run with widget `t_index` = 0,1,2,3 (0 = pre-impact,
# MAGIC 3 = fully grown) and the dashboard shows the larger footprint on its next refresh.
# MAGIC Reset for the room: set `t_index=2` (the default threatened state). A tiny scheduled job
# MAGIC could cycle 0→3 for a hands-free "watch it spread"; left manual here (demo-safe).
