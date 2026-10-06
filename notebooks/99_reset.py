# Databricks notebook source
# MAGIC %md
# MAGIC # 99 · Reset & roll forward — bookends of the `exposure_99_reset` job
# MAGIC
# MAGIC The reset job re-runs the existing notebooks (00 → 01 → feeds → 20 → 40 → 50 → 60 → 30 → 07) so every
# MAGIC demo date rolls forward to "as of today" while the heroes stay deterministic (seed 42, fixed footprints).
# MAGIC This notebook runs twice, as the first and last task:
# MAGIC - **`mode=snapshot`** — records every direct grant held by the app service principal and `account users`
# MAGIC   on this schema's tables, views and functions. Rebuilding tables (overwrite / CREATE OR REPLACE) can drop
# MAGIC   table grants; this makes the reset safe without hand-maintaining a grant list.
# MAGIC - **`mode=finish`** — clears demo-click rows (bind decisions, news promotions, any promoted NEWS event) so
# MAGIC   the room starts clean, re-applies the snapshot grants, and grants the app write access to `live_cursor`
# MAGIC   (the app moves the embedded dashboard's tick as the presenter follows the fire).
# MAGIC
# MAGIC System history (`gov_exposure_snapshot`, `gov_alert_dispatch`, `1_hazard_feed_raw`) is append-only and kept.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("app_sp", "fc3ac835-8393-4ea6-8d2b-180096836d7b")
dbutils.widgets.dropdown("mode", "snapshot", ["snapshot", "finish"])
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
APP_SP = dbutils.widgets.get("app_sp").strip()
MODE = dbutils.widgets.get("mode")
FQ = f"`{CATALOG}`.`{SCHEMA}`"
SNAP = f"{FQ}.ops_reset_grants"
GRANTEES = [g for g in [APP_SP, "account users"] if g]
print(f"{MODE} · {CATALOG}.{SCHEMA} · grantees={GRANTEES}")

# COMMAND ----------

def snapshot_grants():
    inlist = ", ".join(f"'{g}'" for g in GRANTEES)
    df = spark.sql(f"""
      SELECT 'TABLE' AS object_kind, table_name AS object_name, grantee, privilege_type
      FROM `{CATALOG}`.information_schema.table_privileges
      WHERE table_schema = '{SCHEMA}' AND grantee IN ({inlist}) AND inherited_from = 'NONE'
      UNION ALL
      SELECT 'FUNCTION', routine_name, grantee, privilege_type
      FROM `{CATALOG}`.information_schema.routine_privileges
      WHERE routine_schema = '{SCHEMA}' AND grantee IN ({inlist}) AND inherited_from = 'NONE'
    """)
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(SNAP)
    print(f"snapshot: {df.count()} direct grants saved to ops_reset_grants")


def clear_demo_clicks():
    for stmt in [f"DELETE FROM {FQ}.gov_bind_decision",
                 f"DELETE FROM {FQ}.gov_news_decision",
                 f"DELETE FROM {FQ}.`2_event_footprint` WHERE source = 'NEWS'"]:
        try:
            spark.sql(stmt); print("ok:", stmt)
        except Exception as e:
            print("skip:", stmt, "->", str(e)[:120])


def restore_grants():
    rows = spark.table(SNAP).collect()
    ok = fail = 0
    for r in rows:
        on = "FUNCTION" if r.object_kind == "FUNCTION" else "TABLE"
        try:
            spark.sql(f"GRANT {r.privilege_type} ON {on} {FQ}.`{r.object_name}` TO `{r.grantee}`"); ok += 1
        except Exception as e:
            fail += 1; print("  grant failed:", r.object_name, r.privilege_type, r.grantee, "->", str(e)[:120])
    print(f"restored {ok} grants ({fail} failed)")
    if APP_SP:  # the app writes the dashboard cursor as the presenter follows the fire
        spark.sql(f"GRANT SELECT, MODIFY ON TABLE {FQ}.live_cursor TO `{APP_SP}`")
        print("granted SELECT, MODIFY on live_cursor to app SP")

# COMMAND ----------

if MODE == "snapshot":
    snapshot_grants()
else:
    clear_demo_clicks()
    restore_grants()
    ev = spark.sql(f"SELECT event_id, min(event_date) d, max(source) src FROM {FQ}.`2_event_footprint` "
                   f"GROUP BY event_id ORDER BY d DESC").collect()
    print("events now dated:", [(e.event_id, str(e.d), e.src) for e in ev])
