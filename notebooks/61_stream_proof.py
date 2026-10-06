# Databricks notebook source
# MAGIC %md
# MAGIC # 61 · Live ingestion proof — a real streaming feed into the Live Fire Tracker
# MAGIC
# MAGIC Proves the dashboard is fed by a **genuinely ingesting** stream, not a table being re-read.
# MAGIC Every `interval_s` seconds a small hazard-feed record (a simulated fire-front heartbeat) lands as a
# MAGIC JSON file in a UC Volume, and an **Auto Loader Structured Streaming** query (checkpointed,
# MAGIC exactly-once, `trigger(availableNow=True)`) ingests every new file into `stream_proof`.
# MAGIC The dashboard's **Live ingestion** tiles read `stream_proof` (records ingested + seconds since the last one).
# MAGIC
# MAGIC **Bounded by design:** the job runs for `minutes` (default 45) and then stops on its own — it cannot run up
# MAGIC cost indefinitely. Start it ~5 min before the room; `reset=true` clears the table + checkpoint for a clean count.
# MAGIC Serverless note: serverless jobs support `availableNow` (not `processingTime`) triggers, hence the
# MAGIC drop-then-ingest loop — each cycle is a real incremental streaming micro-batch over new files only.

# COMMAND ----------
import json, time, uuid
from datetime import datetime, timezone
from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("minutes", "45")
dbutils.widgets.text("interval_s", "20")
dbutils.widgets.dropdown("reset", "false", ["false", "true"])
CATALOG = dbutils.widgets.get("catalog"); SCHEMA = dbutils.widgets.get("schema")
MINUTES = float(dbutils.widgets.get("minutes")); INTERVAL = int(dbutils.widgets.get("interval_s"))
RESET = dbutils.widgets.get("reset") == "true"
S = f"{CATALOG}.{SCHEMA}"
VOL = f"/Volumes/{CATALOG}/{SCHEMA}/stream_landing"
LANDING, CKPT = f"{VOL}/feed", f"{VOL}/_checkpoint/stream_proof"

spark.sql(f"CREATE VOLUME IF NOT EXISTS {S}.stream_landing")
if RESET:
    spark.sql(f"DROP TABLE IF EXISTS {S}.stream_proof")
    dbutils.fs.rm(LANDING, True); dbutils.fs.rm(CKPT, True)
dbutils.fs.mkdirs(LANDING)
spark.sql(f"""CREATE TABLE IF NOT EXISTS {S}.stream_proof (
  seq BIGINT, feed STRING, event_id STRING, fire_front_km DOUBLE, emitted_at TIMESTAMP,
  ingested_at TIMESTAMP, source_file STRING)
  COMMENT 'Live ingestion proof: hazard-feed heartbeat records ingested by an Auto Loader Structured Streaming query (notebook 61). Read by the Live Fire Tracker dashboard Live ingestion tiles.'""")
for who in ("`account users`", "`fc3ac835-8393-4ea6-8d2b-180096836d7b`"):
    spark.sql(f"GRANT SELECT ON TABLE {S}.stream_proof TO {who}")

# COMMAND ----------
SCHEMA_DDL = "seq BIGINT, feed STRING, event_id STRING, fire_front_km DOUBLE, emitted_at TIMESTAMP"

def ingest_new_files():
    """One incremental Structured Streaming run: Auto Loader picks up only files not yet seen (checkpoint)."""
    q = (spark.readStream.format("cloudFiles")
         .option("cloudFiles.format", "json").schema(SCHEMA_DDL).load(LANDING)
         .withColumn("ingested_at", F.current_timestamp())
         .withColumn("source_file", F.col("_metadata.file_path"))
         .writeStream.option("checkpointLocation", CKPT)
         .trigger(availableNow=True).toTable(f"{S}.stream_proof"))
    q.awaitTermination()

start = time.time(); seq = spark.table(f"{S}.stream_proof").count()
print(f"streaming proof: {MINUTES} min, one record every {INTERVAL}s (starting at seq {seq})")
while time.time() - start < MINUTES * 60:
    seq += 1
    rec = {"seq": seq, "feed": "Simulated fire-front feed (Var)", "event_id": "EVT_LIVE_VAR_FIRE",
           "fire_front_km": round(2.0 + 0.05 * seq, 2), "emitted_at": datetime.now(timezone.utc).isoformat()}
    dbutils.fs.put(f"{LANDING}/rec_{seq:06d}_{uuid.uuid4().hex[:6]}.json", json.dumps(rec), overwrite=True)
    ingest_new_files()
    time.sleep(INTERVAL)
print(f"done — stopped on its own after {MINUTES} min; stream_proof rows = {spark.table(f'{S}.stream_proof').count()}")
