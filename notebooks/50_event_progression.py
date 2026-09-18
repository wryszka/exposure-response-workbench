# Databricks notebook source
# MAGIC %md
# MAGIC # 50 · Live event progression + anti-selection bind check (the demo centrepiece)
# MAGIC
# MAGIC Turns a *static* event into a **developing** one and adds a governed **bind-time** rule:
# MAGIC
# MAGIC 1. **Progression** (`2_event_progression`) — the Var wildfire as a short time-series of *growing*
# MAGIC    footprints. Early ticks are press-only (`corroborated=false`); a later tick flips `corroborated=true`
# MAGIC    when the satellite (FIRMS) catches up. The app "follows" the story, stepping ticks ~every 30 s, and the
# MAGIC    threatened-property count/SI climb (see `mv_progression_exposure`).
# MAGIC 2. **`fn_progression_props(t_index)`** — properties within 200 m of the footprint at a tick (EPSG:3035),
# MAGIC    for the live-redrawing map.
# MAGIC 3. **`fn_bind_check(lon, lat, t_index)`** — is an applicant address inside/within 200 m of the *active*
# MAGIC    fire footprint right now? The app declines cover in an active zone — **adverse selection at the point of
# MAGIC    sale** — and audits every decision in `gov_bind_decision`. The agent explains; an underwriter can override.
# MAGIC
# MAGIC Same split as the other notebooks: `ST_*` DDL runs on the **SQL warehouse** via the Statement Execution
# MAGIC API (`run_wh`) — it cannot run through the serverless notebook's Spark Connect.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "")

import time
from databricks.sdk import WorkspaceClient

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
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
# MAGIC %md ### Progression footprints — growing boxes around the Var cluster (deterministic)

# COMMAND ----------

C = (6.85, 43.62)  # centre of the developing Var fire (near the existing FIRMS sample)


def box(dlon, dlat):
    lo, la = C
    a, b, c, d = lo - dlon, lo + dlon, la - dlat, la + dlat
    return f"POLYGON(({a} {c}, {b} {c}, {b} {d}, {a} {d}, {a} {c}))"


# (t_index, minutes-from-now, corroborated, feed_source, label, footprint)
TICKS = [
    (0, -90, "false", "Riviera Wire (press)", "First report — smoke seen near the ridge", box(0.037, 0.027)),
    (1, -60, "false", "Press + social posts", "Spreading fast — evacuations begin", box(0.100, 0.072)),
    (2, -30, "true", "Press + FIRMS (satellite confirms)", "Satellite confirms — active fire front", box(0.162, 0.117)),
    (3, 0, "true", "Press + FIRMS + EFFIS", "Major fire — perimeter still growing", box(0.274, 0.198)),
]

run_wh(
    "CREATE OR REPLACE TABLE `2_event_progression` (event_id STRING, t_index INT, label STRING, "
    "as_of_ts TIMESTAMP, corroborated BOOLEAN, feed_source STRING, footprint_wkt STRING) USING DELTA",
    "create progression")
sel = " UNION ALL ".join(
    f"SELECT 'EVT_LIVE_VAR_FIRE', {t}, '{lab}', current_timestamp()+make_dt_interval(0,0,{mins},0), "
    f"{corr}, '{src}', '{wkt}'"
    for (t, mins, corr, src, lab, wkt) in TICKS)
run_wh(f"INSERT INTO `2_event_progression` {sel}", "seed progression")
print("progression seeded — 4 ticks, dates roll from current_timestamp")

# COMMAND ----------
# MAGIC %md ### Governed functions + audit table

# COMMAND ----------

run_wh("""CREATE OR REPLACE FUNCTION fn_progression_props(p_t_index INT)
RETURNS TABLE(insured_object_id STRING, latitude DECIMAL(9,6), longitude DECIMAL(9,6), city STRING, country_code STRING, sum_insured DECIMAL(18,2), distance_m DOUBLE, band STRING)
COMMENT 'Insured properties within 200m of the Var live-fire footprint at progression tick p_t_index (true metres, EPSG:3035). Powers the live-follow map as the fire grows.'
RETURN WITH fp AS (SELECT ST_Transform(ST_SetSRID(ST_GeomFromText(footprint_wkt),4326),3035) g FROM `2_event_progression` WHERE event_id='EVT_LIVE_VAR_FIRE' AND t_index=p_t_index)
SELECT p.insured_object_id,p.latitude,p.longitude,p.city,p.country_code,p.sum_insured,
 ST_Distance(ST_Transform(ST_SetSRID(ST_Point(p.longitude,p.latitude),4326),3035),fp.g) distance_m,
 CASE WHEN ST_Distance(ST_Transform(ST_SetSRID(ST_Point(p.longitude,p.latitude),4326),3035),fp.g)<=50 THEN '0-50m'
      WHEN ST_Distance(ST_Transform(ST_SetSRID(ST_Point(p.longitude,p.latitude),4326),3035),fp.g)<=100 THEN '50-100m' ELSE '100-200m' END band
FROM `3_property` p CROSS JOIN fp
WHERE ST_DWithin(ST_Transform(ST_SetSRID(ST_Point(p.longitude,p.latitude),4326),3035),fp.g,200)""", "fn_progression_props")

run_wh("""CREATE OR REPLACE VIEW mv_progression_exposure AS
SELECT g.t_index,g.label,CAST(g.as_of_ts AS STRING) as_of,g.corroborated,g.feed_source,g.footprint_wkt,
 count(p.insured_object_id) threatened_count, coalesce(sum(p.sum_insured),0) sum_insured_eur
FROM `2_event_progression` g
LEFT JOIN `3_property` p ON ST_DWithin(ST_Transform(ST_SetSRID(ST_Point(p.longitude,p.latitude),4326),3035),ST_Transform(ST_SetSRID(ST_GeomFromText(g.footprint_wkt),4326),3035),200)
WHERE g.event_id='EVT_LIVE_VAR_FIRE'
GROUP BY g.t_index,g.label,g.as_of_ts,g.corroborated,g.feed_source,g.footprint_wkt""", "mv_progression_exposure")

run_wh("""CREATE OR REPLACE FUNCTION fn_bind_check(p_lon DOUBLE, p_lat DOUBLE, p_t_index INT)
RETURNS TABLE(in_zone BOOLEAN, distance_m DOUBLE, active_event STRING, as_of STRING, feed_source STRING)
COMMENT 'Bind-time governance check: is an applicant location inside / within 200m of the active Var fire footprint at progression tick p_t_index? Used to decline cover in an active catastrophe zone (anti-selection).'
RETURN WITH fp AS (SELECT footprint_wkt,as_of_ts,feed_source FROM `2_event_progression` WHERE event_id='EVT_LIVE_VAR_FIRE' AND t_index=p_t_index)
SELECT ST_DWithin(ST_Transform(ST_SetSRID(ST_Point(p_lon,p_lat),4326),3035),ST_Transform(ST_SetSRID(ST_GeomFromText(fp.footprint_wkt),4326),3035),200) in_zone,
 ST_Distance(ST_Transform(ST_SetSRID(ST_Point(p_lon,p_lat),4326),3035),ST_Transform(ST_SetSRID(ST_GeomFromText(fp.footprint_wkt),4326),3035)) distance_m,
 'EVT_LIVE_VAR_FIRE', CAST(fp.as_of_ts AS STRING), fp.feed_source FROM fp""", "fn_bind_check")

run_wh("""CREATE TABLE IF NOT EXISTS gov_bind_decision (quote_id STRING, applicant STRING, object_ref STRING, latitude DOUBLE, longitude DOUBLE, t_index INT, decision STRING, reason STRING, active_event_id STRING, distance_m DOUBLE, as_of STRING, decided_at TIMESTAMP, decided_by STRING, overridden BOOLEAN) USING DELTA""", "gov_bind_decision")
print("functions + audit table ready")

# COMMAND ----------
# MAGIC %md ### Grants — the app SP reads the progression/functions and writes the bind audit

# COMMAND ----------

grantees = [f"`{app_sp}`"] if app_sp else []
grantees.append("`account users`")
for who in grantees:
    for stmt in (
        f"GRANT SELECT ON TABLE `2_event_progression` TO {who}",
        f"GRANT SELECT ON VIEW mv_progression_exposure TO {who}",
        f"GRANT EXECUTE ON FUNCTION fn_progression_props TO {who}",
        f"GRANT EXECUTE ON FUNCTION fn_bind_check TO {who}",
        f"GRANT SELECT ON TABLE gov_bind_decision TO {who}",
        f"GRANT MODIFY ON TABLE gov_bind_decision TO {who}",
    ):
        run_wh(stmt, "grant")
print("grants applied")

# COMMAND ----------
# MAGIC %md ### Verify — the ramp and the bind check

# COMMAND ----------

r = run_wh("SELECT t_index, corroborated, feed_source, threatened_count, round(sum_insured_eur/1e6,1) si_m FROM mv_progression_exposure ORDER BY t_index", "ramp")
for row in r.result.data_array:
    print("tick", row)
r = run_wh("SELECT 0 tick, in_zone, round(distance_m) d FROM fn_bind_check(6.742300,43.586962,0) UNION ALL SELECT 3, in_zone, round(distance_m) FROM fn_bind_check(6.742300,43.586962,3) ORDER BY tick", "bind")
for row in r.result.data_array:
    print("bind hero", row)
