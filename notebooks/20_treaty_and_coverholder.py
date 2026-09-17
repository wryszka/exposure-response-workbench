# Databricks notebook source
# MAGIC %md
# MAGIC # 20 · Treaty programme, coverholders & the gross → net waterfall (Bricksurance SE)
# MAGIC
# MAGIC The exposure view answers *which properties are threatened*. This notebook adds the next two questions the
# MAGIC Head of Exposure Management asks in the same breath — **"net of treaty?"** and **"by coverholder?"** — as
# MAGIC governed Unity Catalog functions, so the answer comes from one audited place.
# MAGIC
# MAGIC **The chain (honest about what each step is):**
# MAGIC 1. `fn_exposure_in_footprint` gives the threatened **sum insured**, by distance band.
# MAGIC 2. `ref_damage_factor` — an **illustrative, governed** damage ratio per peril × band — turns sum insured
# MAGIC    into a **modelled gross loss** (a cat model would supply this; here it is a transparent, tunable table,
# MAGIC    never a number hidden in the app).
# MAGIC 3. `3_treaty` / `3_treaty_layer` — a realistic **property catastrophe XL** programme — cede the modelled
# MAGIC    gross loss layer by layer to a **net retained** figure.
# MAGIC 4. `3_coverholder` / `3_property_coverholder` split the same threatened exposure by **delegated authority**.
# MAGIC
# MAGIC Structure and terminology (attachment / limit / placement / layer) mirror the reinsurance workbench.
# MAGIC
# MAGIC **GOTCHA (same as notebook 02):** the functions call `fn_exposure_in_footprint`, which uses `ST_*` — rejected
# MAGIC on the serverless notebook (Spark Connect), so all DDL runs on the **SQL warehouse** via the Statement
# MAGIC Execution API. `CREATE OR REPLACE FUNCTION` revokes EXECUTE — the re-grant cell must run.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
fqn = f"{catalog}.{schema}"
print(f"target = {fqn}  warehouse={warehouse_id}  app_sp={app_sp or '(none yet)'}")

from databricks.sdk import WorkspaceClient
_w = WorkspaceClient()

def run_wh(stmt: str, label: str = ""):
    """Execute one statement on the SQL warehouse (where ST_* is supported and the app also runs)."""
    r = _w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=warehouse_id, catalog=catalog, schema=schema, wait_timeout="50s")
    state = r.status.state.value
    if state != "SUCCEEDED":
        raise RuntimeError(f"{label}: {state} — {r.status.error.message if r.status.error else '?'}")
    print(f"  {label}: {state}")
    return r

# COMMAND ----------

# MAGIC %md ## ref_damage_factor — illustrative modelled damage ratio per peril × band
# MAGIC The single, visible place the sum-insured-at-risk becomes a modelled loss. Nearer the footprint = higher
# MAGIC damage; fire is the most destructive at the fringe, windstorm the least. Tunable; a cat model would replace it.

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE TABLE {fqn}.ref_damage_factor (
    peril_code STRING COMMENT 'FIRE / FLOOD / STORM',
    band STRING COMMENT 'distance band from fn_exposure_in_footprint: 0-50m / 50-100m / 100-200m',
    damage_ratio DOUBLE COMMENT 'illustrative fraction of sum insured lost at this proximity — modelled, tunable, NOT a hardcoded app number'
) COMMENT 'Illustrative governed damage curve turning sum-insured-at-risk into a modelled gross loss per peril and distance band. Stands in for a catastrophe model output; kept as a table so the assumption is visible and auditable.'
""", "ref_damage_factor table")

run_wh(f"""
INSERT OVERWRITE {fqn}.ref_damage_factor VALUES
  ('FIRE','0-50m',0.60),('FIRE','50-100m',0.25),('FIRE','100-200m',0.08),
  ('FLOOD','0-50m',0.45),('FLOOD','50-100m',0.20),('FLOOD','100-200m',0.06),
  ('STORM','0-50m',0.18),('STORM','50-100m',0.08),('STORM','100-200m',0.03)
""", "ref_damage_factor rows")

# COMMAND ----------

# MAGIC %md ## Coverholders / delegated authorities, and the deterministic property → coverholder assignment

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE TABLE {fqn}.`3_coverholder` (
    coverholder_id STRING, coverholder_name STRING, binder_ref STRING,
    country_scope STRING, is_delegated BOOLEAN
) COMMENT 'Coverholders / delegated authorities writing on behalf of Bricksurance SE, plus the direct book. Exposure is split by these so a delegated-authority view is available the moment an event develops.'
""", "3_coverholder table")

run_wh(f"""
INSERT OVERWRITE {fqn}.`3_coverholder` VALUES
  ('CH_ALP','Alpine Cover Underwriting','B-ALP-2026','AT/IT',true),
  ('CH_MED','Midi Mediterranee MGA','B-MED-2026','FR',true),
  ('CH_IBER','Iberica Risk Partners','B-IBE-2026','ES',true),
  ('CH_DACH','DACH Property Binder','B-DACH-2026','DE/AT',true),
  ('CH_DIRECT','Bricksurance SE (direct)','-','ALL',false)
""", "3_coverholder rows")

# Deterministic assignment from the property book (xxhash64 is stable → reproducible on reset).
run_wh(f"""
CREATE OR REPLACE TABLE {fqn}.`3_property_coverholder`
COMMENT 'Deterministic mapping of every insured object to a coverholder / delegated authority, by country with a stable hash split. Reset-safe: derived from 3_property.'
AS SELECT insured_object_id,
  CASE
    WHEN country_code='ES' THEN 'CH_IBER'
    WHEN country_code='FR' THEN CASE WHEN pmod(xxhash64(insured_object_id),10) < 6 THEN 'CH_MED' ELSE 'CH_DIRECT' END
    WHEN country_code='IT' THEN CASE WHEN pmod(xxhash64(insured_object_id),10) < 7 THEN 'CH_ALP' ELSE 'CH_DIRECT' END
    WHEN country_code='AT' THEN CASE WHEN pmod(xxhash64(insured_object_id),10) < 5 THEN 'CH_ALP' ELSE 'CH_DACH' END
    WHEN country_code='DE' THEN CASE WHEN pmod(xxhash64(insured_object_id),10) < 6 THEN 'CH_DACH' ELSE 'CH_DIRECT' END
    ELSE 'CH_DIRECT' END AS coverholder_id
FROM {fqn}.`3_property`
""", "3_property_coverholder")

# COMMAND ----------

# MAGIC %md ## The property catastrophe XL programme — treaty + layers
# MAGIC A conventional cat tower for a ~€14bn European property book: €25m retention, then four layers to €400m.

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE TABLE {fqn}.`3_treaty` (
    treaty_id STRING, treaty_name STRING, programme STRING, currency STRING,
    uw_year INT, peril_scope STRING, retention_eur DOUBLE
) COMMENT 'Bricksurance SE outwards reinsurance programmes. The property catastrophe XL responds to FIRE/FLOOD/STORM events.'
""", "3_treaty table")

run_wh(f"""
INSERT OVERWRITE {fqn}.`3_treaty` VALUES
  ('TREATY_PROP_CAT_2026','Bricksurance SE Property Cat XL 2026','Group Property Catastrophe','EUR',2026,'FIRE,FLOOD,STORM',25000000)
""", "3_treaty rows")

run_wh(f"""
CREATE OR REPLACE TABLE {fqn}.`3_treaty_layer` (
    treaty_id STRING, layer_no INT, layer_name STRING,
    attachment_eur DOUBLE, limit_eur DOUBLE, placement_pct DOUBLE
) COMMENT 'Cat XL layers: each cedes LEAST(GREATEST(loss-attachment,0),limit)*placement_pct of the modelled gross loss. Attachment/limit/placement mirror the reinsurance workbench.'
""", "3_treaty_layer table")

run_wh(f"""
INSERT OVERWRITE {fqn}.`3_treaty_layer` VALUES
  ('TREATY_PROP_CAT_2026',1,'Layer 1 - EUR 50m xs 25m',   25000000,  50000000, 1.00),
  ('TREATY_PROP_CAT_2026',2,'Layer 2 - EUR 75m xs 75m',   75000000,  75000000, 1.00),
  ('TREATY_PROP_CAT_2026',3,'Layer 3 - EUR 100m xs 150m',150000000, 100000000, 0.90),
  ('TREATY_PROP_CAT_2026',4,'Layer 4 - EUR 150m xs 250m',250000000, 150000000, 0.80)
""", "3_treaty_layer rows")

# COMMAND ----------

# MAGIC %md ## fn_gross_to_net — the waterfall from modelled gross loss to net retained
# MAGIC One row per waterfall step: the gross row, one row per treaty layer (with running net after cession), and the
# MAGIC net-retained row. The app draws it as a step waterfall; the numbers are the governed numbers.

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_gross_to_net(
    p_event_id STRING COMMENT 'event_id from 2_event_footprint',
    p_buffer_m INT     COMMENT 'buffer distance in metres'
)
RETURNS TABLE(
    seq INT, step_label STRING, layer_name STRING,
    attachment_eur DOUBLE, limit_eur DOUBLE, placement_pct DOUBLE,
    ceded_eur DOUBLE, running_net_eur DOUBLE, kind STRING
)
COMMENT 'Gross-to-net waterfall for event p_event_id within p_buffer_m metres. Turns the threatened sum insured into a modelled gross loss via ref_damage_factor (peril x band), then cedes it through the Property Cat XL layers (3_treaty_layer) to a net retained figure. Rows: kind=gross (modelled gross loss), kind=cession (one per layer, with ceded_eur and the running net after that layer), kind=net (final net retained). Answers the canonical question net of treaty; the app and any agent read the same waterfall.'
RETURN
  WITH x AS (
    SELECT peril_code, band, sum_insured FROM {fqn}.fn_exposure_in_footprint(p_event_id, p_buffer_m)
  ),
  gl AS (
    SELECT CAST(COALESCE(SUM(x.sum_insured * df.damage_ratio), 0) AS DOUBLE) AS g
    FROM x JOIN {fqn}.ref_damage_factor df ON df.peril_code = x.peril_code AND df.band = x.band
  ),
  lyr AS (
    SELECT layer_no, layer_name, attachment_eur, limit_eur, placement_pct,
           LEAST(GREATEST((SELECT g FROM gl) - attachment_eur, 0), limit_eur) * placement_pct AS ceded_eur
    FROM {fqn}.`3_treaty_layer`
  ),
  lyr_run AS (
    SELECT *, SUM(ceded_eur) OVER (ORDER BY layer_no ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum
    FROM lyr
  ),
  wf AS (
    SELECT 0 AS seq, 'Gross modelled loss' AS step_label, CAST(NULL AS STRING) AS layer_name,
           CAST(NULL AS DOUBLE) AS attachment_eur, CAST(NULL AS DOUBLE) AS limit_eur, CAST(NULL AS DOUBLE) AS placement_pct,
           CAST(0 AS DOUBLE) AS ceded_eur, (SELECT g FROM gl) AS running_net_eur, 'gross' AS kind
    UNION ALL
    SELECT layer_no, layer_name, layer_name, attachment_eur, limit_eur, placement_pct,
           ceded_eur, (SELECT g FROM gl) - cum, 'cession'
    FROM lyr_run
    UNION ALL
    SELECT 9999, 'Net retained', CAST(NULL AS STRING), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE),
           CAST(0 AS DOUBLE), (SELECT g FROM gl) - (SELECT COALESCE(SUM(ceded_eur), 0) FROM lyr), 'net'
  )
  SELECT * FROM wf ORDER BY seq
""", "fn_gross_to_net")

# COMMAND ----------

# MAGIC %md ## fn_exposure_by_coverholder — threatened exposure split by delegated authority

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_exposure_by_coverholder(
    p_event_id STRING COMMENT 'event_id from 2_event_footprint',
    p_buffer_m INT     COMMENT 'buffer distance in metres'
)
RETURNS TABLE(
    coverholder_id STRING, coverholder_name STRING, binder_ref STRING,
    country_scope STRING, is_delegated BOOLEAN, n_objects BIGINT, sum_insured_eur DOUBLE
)
COMMENT 'Threatened exposure for event p_event_id within p_buffer_m metres, split by coverholder / delegated authority (3_coverholder via the 3_property_coverholder assignment). n_objects is a distinct insured-object count. Gives the delegated-authority view of an event the moment it develops. Largest exposure first.'
RETURN
  WITH x AS (
    SELECT insured_object_id, sum_insured FROM {fqn}.fn_exposure_in_footprint(p_event_id, p_buffer_m)
  )
  SELECT c.coverholder_id, c.coverholder_name, c.binder_ref, c.country_scope, c.is_delegated,
         count(DISTINCT x.insured_object_id) AS n_objects,
         CAST(SUM(x.sum_insured) AS DOUBLE) AS sum_insured_eur
  FROM x
  JOIN {fqn}.`3_property_coverholder` m ON m.insured_object_id = x.insured_object_id
  JOIN {fqn}.`3_coverholder` c ON c.coverholder_id = m.coverholder_id
  GROUP BY c.coverholder_id, c.coverholder_name, c.binder_ref, c.country_scope, c.is_delegated
  ORDER BY sum_insured_eur DESC
""", "fn_exposure_by_coverholder")

# COMMAND ----------

# MAGIC %md ## GRANTs — CREATE OR REPLACE revoked them; re-grant SELECT (tables) + EXECUTE (functions)

# COMMAND ----------

tables = ["ref_damage_factor", "`3_coverholder`", "`3_property_coverholder`", "`3_treaty`", "`3_treaty_layer`"]
fns = ["fn_gross_to_net", "fn_exposure_by_coverholder"]
grantees = ["`account users`"] + ([f"`{app_sp}`"] if app_sp else [])
for g in grantees:
    for t in tables:
        run_wh(f"GRANT SELECT ON TABLE {fqn}.{t} TO {g}", f"grant SELECT {t} -> {g}")
    for fn in fns:
        run_wh(f"GRANT EXECUTE ON FUNCTION {fqn}.{fn} TO {g}", f"grant EXECUTE {fn} -> {g}")

# COMMAND ----------

# MAGIC %md ## Smoke — the waterfall + coverholder split for the three events

# COMMAND ----------

for label, stmt in [
    ("Var wildfire waterfall 200m", f"SELECT seq, step_label, round(ceded_eur/1e6,2) AS ceded_m, round(running_net_eur/1e6,2) AS net_m, kind FROM {fqn}.fn_gross_to_net('EVT_FIRE_VAR', 200) ORDER BY seq"),
    ("Alpine flood waterfall 200m", f"SELECT seq, step_label, round(ceded_eur/1e6,2) AS ceded_m, round(running_net_eur/1e6,2) AS net_m, kind FROM {fqn}.fn_gross_to_net('EVT_FLOOD_ALPS', 200) ORDER BY seq"),
    ("Var wildfire by coverholder 200m", f"SELECT coverholder_name, is_delegated, n_objects, round(sum_insured_eur/1e6,1) AS si_m FROM {fqn}.fn_exposure_by_coverholder('EVT_FIRE_VAR', 200)"),
]:
    print(f"\n{label}:")
    r = run_wh(stmt, label)
    for row in (r.result.data_array or []):
        print("   ", row)
