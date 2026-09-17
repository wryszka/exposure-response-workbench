# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Exposure functions — the governed compute path (Bricksurance SE)
# MAGIC
# MAGIC All the event-response business math lives here, in **governed Unity Catalog functions** — never in the
# MAGIC app. The app (and, later, the agents and Genie) recompute by *calling* these functions, so there is one
# MAGIC place the exposure numbers come from and one place they are audited.
# MAGIC
# MAGIC The compute is the geospatial intersection confirmed on the SQL warehouse:
# MAGIC `ST_Transform(ST_GeomFromText(wkt, 4326), 3035)` for the event footprint (multi-segment events unioned with
# MAGIC `ST_Union_Agg`), `ST_Transform(ST_SetSRID(ST_Point(lon, lat), 4326), 3035)` for each insured property, then
# MAGIC `ST_Distance` / `ST_DWithin` in **EPSG:3035 (LAEA Europe)** so the 50 / 100 / 200 m bands are true metres.
# MAGIC `ST_Distance` to a polygon is 0 inside it and the nearest-edge distance outside, so "within N m of the
# MAGIC event" reads correctly whether a property is inside the footprint or just near it.
# MAGIC
# MAGIC **GOTCHA (why this notebook talks to the warehouse, not `spark.sql`):** the `ST_*` geospatial functions run
# MAGIC on the **SQL warehouse** but are rejected on the serverless notebook (Spark Connect) engine
# MAGIC (`SparkConnectUdfRejectionScope`). So the DDL is executed against the warehouse via the Statement Execution
# MAGIC API — the notebook only orchestrates. The functions themselves run entirely on the warehouse, where the app
# MAGIC also calls them. Portable: catalog / schema / warehouse come from widgets.
# MAGIC
# MAGIC **GOTCHA:** `CREATE OR REPLACE FUNCTION` revokes EXECUTE grants — the re-grant cell must run, or the app 500s.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "")  # app service principal id — EXECUTE granted after app create; blank is fine on first run

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
fqn = f"{catalog}.{schema}"
print(f"target = {fqn}  warehouse={warehouse_id}  app_sp={app_sp or '(none yet)'}")

from databricks.sdk import WorkspaceClient
_w = WorkspaceClient()

def run_wh(stmt: str, label: str = ""):
    """Execute one statement on the SQL warehouse (where ST_* is supported)."""
    r = _w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=warehouse_id, catalog=catalog, schema=schema, wait_timeout="50s")
    state = r.status.state.value
    if state != "SUCCEEDED":
        raise RuntimeError(f"{label}: {state} — {r.status.error.message if r.status.error else '?'}")
    print(f"  {label}: {state}")
    return r

# COMMAND ----------

# MAGIC %md ## fn_exposure_in_footprint — the threatened properties for an event within a buffer
# MAGIC One row per insured object within `buffer_m` of the event footprint, with true distance in metres and the
# MAGIC distance band. The function the exposure view, the alert job, and the agent all call.

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_exposure_in_footprint(
    p_event_id STRING COMMENT 'event_id from 2_event_footprint, e.g. EVT_FIRE_VAR',
    p_buffer_m INT     COMMENT 'buffer distance in metres — the demo uses 50, 100 or 200'
)
RETURNS TABLE(
    event_id STRING, peril_code STRING, event_name STRING,
    insured_object_id STRING, policy_id STRING, country_code STRING, city STRING, postcode STRING,
    latitude DECIMAL(9,6), longitude DECIMAL(9,6),
    distance_m DOUBLE, band STRING,
    sum_insured DECIMAL(15,2), coverage_type_code STRING, line_of_business_code STRING
)
COMMENT 'Insured properties within p_buffer_m metres of catastrophe event p_event_id, with true metric distance (EPSG:3035) and distance band (0-50m / 50-100m / 100-200m). Multi-segment events (e.g. a flood spanning a border) are unioned into one footprint, so a single event returns its whole cross-border exposure. The governed source of the threatened-property list for the exposure view, the alert job and the exposure agent.'
RETURN
  WITH ev AS (
    SELECT event_id, peril_code, event_name,
           ST_Transform(ST_Union_Agg(ST_GeomFromText(footprint_wkt, 4326)), 3035) AS g
    FROM {fqn}.`2_event_footprint`
    WHERE event_id = p_event_id
    GROUP BY event_id, peril_code, event_name
  ),
  props AS (
    SELECT insured_object_id, policy_id, country_code, city, postcode, latitude, longitude,
           sum_insured, coverage_type_code, line_of_business_code,
           ST_Transform(ST_SetSRID(ST_Point(longitude, latitude), 4326), 3035) AS pg
    FROM {fqn}.`3_property`
  )
  SELECT ev.event_id, ev.peril_code, ev.event_name,
         props.insured_object_id, props.policy_id, props.country_code, props.city, props.postcode,
         props.latitude, props.longitude,
         ST_Distance(props.pg, ev.g) AS distance_m,
         CASE WHEN ST_Distance(props.pg, ev.g) <= 50  THEN '0-50m'
              WHEN ST_Distance(props.pg, ev.g) <= 100 THEN '50-100m'
              WHEN ST_Distance(props.pg, ev.g) <= 200 THEN '100-200m'
              ELSE '>200m' END AS band,
         props.sum_insured, props.coverage_type_code, props.line_of_business_code
  FROM props JOIN ev ON ST_DWithin(props.pg, ev.g, p_buffer_m)
""", "fn_exposure_in_footprint")

# COMMAND ----------

# MAGIC %md ## fn_event_exposure_summary — exposure rolled up by band and by country
# MAGIC Long-format: `dim` = 'band' or 'country'. The by-country rows carry the cross-border story (one event, IT
# MAGIC and AT side by side). Distinct-object counts.

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_event_exposure_summary(
    p_event_id STRING COMMENT 'event_id from 2_event_footprint',
    p_buffer_m INT     COMMENT 'buffer distance in metres'
)
RETURNS TABLE(dim STRING, dim_key STRING, n_objects BIGINT, sum_insured_eur DECIMAL(20,2))
COMMENT 'Exposure for event p_event_id within p_buffer_m metres, rolled up two ways: dim=band (0-50m/50-100m/100-200m) and dim=country (country_code). n_objects is a distinct insured-object count; sum_insured_eur totals the sum insured. Drives the exposure-view KPIs and the cross-border country breakdown; the alert job reads the same rollup so the dashboard and the alert can never disagree.'
RETURN
  WITH x AS (
    SELECT insured_object_id, country_code, band, sum_insured
    FROM {fqn}.fn_exposure_in_footprint(p_event_id, p_buffer_m)
  )
  SELECT 'band' AS dim, band AS dim_key,
         count(DISTINCT insured_object_id) AS n_objects,
         CAST(sum(sum_insured) AS DECIMAL(20,2)) AS sum_insured_eur
  FROM x GROUP BY band
  UNION ALL
  SELECT 'country' AS dim, country_code AS dim_key,
         count(DISTINCT insured_object_id) AS n_objects,
         CAST(sum(sum_insured) AS DECIMAL(20,2)) AS sum_insured_eur
  FROM x GROUP BY country_code
""", "fn_event_exposure_summary")

# COMMAND ----------

# MAGIC %md ## fn_events — active events with a live threatened-count, for the picker

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_events(
    p_buffer_m INT DEFAULT 200 COMMENT 'buffer distance in metres for the headline threatened-count'
)
RETURNS TABLE(
    event_id STRING, peril_code STRING, event_name STRING, event_date DATE,
    n_segments BIGINT, threatened_count BIGINT, source STRING, is_live BOOLEAN
)
COMMENT 'Active catastrophe events (wildfire / flood / windstorm) with a live count of insured properties within p_buffer_m metres of each footprint. source is the feed the event came from (SYNTHETIC frozen seed, or a live feed: METEOALARM / FIRMS / EMS / GLOFAS / EFFIS) and is_live flags a real live pull vs a frozen sample. Drives the event picker and the live/frozen badge in the exposure view. Newest event first.'
RETURN
  WITH ev AS (
    SELECT event_id, peril_code, event_name, event_date,
           count(*) AS n_segments,
           max(source) AS source, max(coalesce(is_live, false)) AS is_live,
           ST_Transform(ST_Union_Agg(ST_GeomFromText(footprint_wkt, 4326)), 3035) AS g
    FROM {fqn}.`2_event_footprint`
    GROUP BY event_id, peril_code, event_name, event_date
  ),
  props AS (
    SELECT ST_Transform(ST_SetSRID(ST_Point(longitude, latitude), 4326), 3035) AS pg
    FROM {fqn}.`3_property`
  )
  SELECT ev.event_id, ev.peril_code, ev.event_name, ev.event_date, ev.n_segments,
         count(props.pg) AS threatened_count, ev.source, ev.is_live
  FROM ev LEFT JOIN props ON ST_DWithin(props.pg, ev.g, p_buffer_m)
  GROUP BY ev.event_id, ev.peril_code, ev.event_name, ev.event_date, ev.n_segments, ev.source, ev.is_live
  ORDER BY ev.is_live DESC, ev.event_date DESC
""", "fn_events")

# COMMAND ----------

# MAGIC %md ## GRANT EXECUTE — CREATE OR REPLACE revoked the old grants
# MAGIC Re-grant to `account users` and, once the app service principal exists, to it explicitly. Runs after every recreate.

# COMMAND ----------

fns = ["fn_exposure_in_footprint", "fn_event_exposure_summary", "fn_events"]
grantees = ["`account users`"] + ([f"`{app_sp}`"] if app_sp else [])
for fn in fns:
    for g in grantees:
        run_wh(f"GRANT EXECUTE ON FUNCTION {fqn}.{fn} TO {g}", f"grant {fn} -> {g}")

# COMMAND ----------

# MAGIC %md ## Smoke — prove the functions return the expected exposure

# COMMAND ----------

for label, stmt in [
    ("fn_events(200)", f"SELECT * FROM {fqn}.fn_events(200)"),
    ("Var wildfire summary 200m", f"SELECT * FROM {fqn}.fn_event_exposure_summary('EVT_FIRE_VAR', 200) ORDER BY dim, dim_key"),
    ("Alpine Flood by country 200m", f"SELECT * FROM {fqn}.fn_event_exposure_summary('EVT_FLOOD_ALPS', 200) WHERE dim='country' ORDER BY n_objects DESC"),
]:
    print(f"\n{label}:")
    r = run_wh(stmt, label)
    for row in (r.result.data_array or []):
        print("   ", row)
