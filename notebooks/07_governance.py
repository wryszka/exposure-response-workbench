# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · Governance & version transparency
# MAGIC Consumption views over the append-only spine — data provenance (which feed, live or frozen, when
# MAGIC ingested), exposure history (what moved on the book across snapshots), and the alert audit
# MAGIC (who was told what, when). No `ST_` here (plain SQL), so these run on the serverless notebook.

# COMMAND ----------
dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
FQ = f"{CATALOG}.{SCHEMA}"
print("Target:", FQ)

# COMMAND ----------
# Data provenance — for every event on the book, where the footprint came from, whether it is a live feed
# or a frozen illustrative footprint, and when it was ingested. This is the "trust the picture" surface.
spark.sql(f"""
CREATE OR REPLACE VIEW {FQ}.gov_data_provenance AS
SELECT event_id,
       max(event_name)                              AS event_name,
       max(peril_code)                              AS peril_code,
       max(source)                                  AS source,
       max(source_detail)                           AS source_detail,
       bool_or(is_live)                             AS is_live,
       count(*)                                     AS n_segments,
       max(event_date)                              AS event_date,
       max(detected_at)                             AS detected_at,
       max(ingested_at)                             AS ingested_at,
       array_join(array_sort(collect_set(country_code)), ', ') AS countries
FROM {FQ}.`2_event_footprint`
GROUP BY event_id
""")
print("gov_data_provenance ✓")

# COMMAND ----------
# Exposure history — threatened count and sum insured per event at each snapshot (as_of). Successive sweeps
# make the movement visible: when a footprint updates, you see what moved on the book and when.
spark.sql(f"""
CREATE OR REPLACE VIEW {FQ}.gov_exposure_history AS
SELECT event_id,
       run_id,
       as_of,
       buffer_m,
       count(DISTINCT insured_object_id)            AS threatened_count,
       round(sum(sum_insured), 0)                   AS sum_insured_eur
FROM {FQ}.gov_exposure_snapshot
GROUP BY event_id, run_id, as_of, buffer_m
""")
print("gov_exposure_history ✓")

# COMMAND ----------
# Alert audit — the append-only record of who was told what, when, and whether it breached threshold.
spark.sql(f"""
CREATE OR REPLACE VIEW {FQ}.gov_alert_audit AS
SELECT dispatch_id, run_id, event_id, event_name, peril_code, status, breached, threshold_rule,
       threatened_count, gross_eur, net_eur, n_countries, countries, new_count,
       channel, recipients, sent_at
FROM {FQ}.gov_alert_dispatch
""")
print("gov_alert_audit ✓")

# COMMAND ----------
# Plain-named consumption views (mv_*) for Genie — table names starting with a digit break Genie's
# generated SQL, so the Genie space sits on these instead of the numbered base tables.
dbutils.widgets.text("app_sp", "fc3ac835-8393-4ea6-8d2b-180096836d7b")
APP_SP = dbutils.widgets.get("app_sp")
MV = {
    "mv_property": "SELECT * FROM {FQ}.`3_property`",
    "mv_event": ("SELECT event_id, peril_code, event_name, event_date, country_code, source, is_live, "
                 "source_detail, ingested_at FROM {FQ}.`2_event_footprint`"),
    "mv_treaty": "SELECT * FROM {FQ}.`3_treaty`",
    "mv_treaty_layer": "SELECT * FROM {FQ}.`3_treaty_layer`",
    "mv_coverholder": "SELECT * FROM {FQ}.`3_coverholder`",
    "mv_property_coverholder": "SELECT * FROM {FQ}.`3_property_coverholder`",
}
for name, body in MV.items():
    spark.sql(f"CREATE OR REPLACE VIEW {FQ}.{name} AS " + body.format(FQ=FQ))
print("mv_ views ✓")

# COMMAND ----------
# Agent response cache — the app writes cached LLM narration here (the "yellow button" live/cached toggle).
# Pre-created so the app SP never needs CREATE TABLE at runtime; SP gets SELECT + MODIFY below.
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {FQ}.cache_agent_responses (
  cache_key STRING, question STRING, response STRING, created_ts TIMESTAMP
) USING DELTA
""")
print("cache_agent_responses ✓")

# COMMAND ----------
# Grants. Genie's SQL runs under the app SP, and the view ownership chain is NOT sufficient — the SP needs
# direct SELECT on the base tables the mv_ views read, plus SELECT on all views (invoker-rights safe).
GRANT_VIEWS = ["gov_data_provenance", "gov_exposure_history", "gov_alert_audit",
               "mv_property", "mv_event", "mv_treaty", "mv_treaty_layer", "mv_coverholder", "mv_property_coverholder"]
GRANT_TABLES = ["`3_property`", "`2_event_footprint`", "`3_treaty`", "`3_treaty_layer`",
                "`3_coverholder`", "`3_property_coverholder`", "ref_country", "ref_cause_of_loss"]
for p in [f"`{APP_SP}`", "`account users`"]:
    for v in GRANT_VIEWS:
        try: spark.sql(f"GRANT SELECT ON VIEW {FQ}.{v} TO {p}")
        except Exception as e: print("grant warn", v, p, str(e)[:80])
    for t in GRANT_TABLES:
        try: spark.sql(f"GRANT SELECT ON TABLE {FQ}.{t} TO {p}")
        except Exception as e: print("grant warn", t, p, str(e)[:80])
# The app SP reads AND writes the response cache.
for pr in ["SELECT", "MODIFY"]:
    try: spark.sql(f"GRANT {pr} ON TABLE {FQ}.cache_agent_responses TO `{APP_SP}`")
    except Exception as e: print("cache grant warn", pr, str(e)[:80])
print("grants ✓")
