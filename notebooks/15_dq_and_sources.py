# Databricks notebook source
# MAGIC %md
# MAGIC # 15 · Data sources & data quality — the catalogue behind "can we trust this?"
# MAGIC
# MAGIC Every feed batch passes the **data-quality gate** (`dq_gate` in `10_feeds_common`) before it may replace a
# MAGIC source's data. This notebook publishes the governed view of that work:
# MAGIC - `ref_feed_source` — one row per source: what it carries, who provides it, real-time vs batch, cadence, licence.
# MAGIC - `gov_source_catalog` — the source list joined to its latest batch: last ingested, records in use, last
# MAGIC   decision (ACCEPTED / REJECTED / FALLBACK_FROZEN), whether live data is in use right now.
# MAGIC - `fn_source_status()`, `fn_dq_recent(p_days)`, `fn_dq_batch_checks(p_run_id)` — governed functions the app and
# MAGIC   the agents call.
# MAGIC - A **seeded rejected batch** (deterministic, re-created by every reset): a MeteoAlarm pull whose coordinates came
# MAGIC   back swapped — 8 of 21 regions land outside Europe (38% vs a 5% threshold) → REJECTED, quarantined, previous
# MAGIC   warnings kept. So the story "this batch was rejected because quality was under the threshold" is always visible.
# MAGIC - Post-landing quality checks for the two loads that don't go through a footprint adapter: the live GDACS
# MAGIC   disaster feed (landed by `40_news_radar`) and the Bricksurance scenario events (loaded by `01`).

# COMMAND ----------

# MAGIC %run ./10_feeds_common

# COMMAND ----------

dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "fc3ac835-8393-4ea6-8d2b-180096836d7b")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
GRANTEES = [g for g in [app_sp, "account users"] if g]

from databricks.sdk import WorkspaceClient
_w = WorkspaceClient()


def run_wh(stmt, label="", max_wait=600):
    """Execute on the SQL warehouse and poll to completion (views/functions are created there, like nb 02)."""
    r = _w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=warehouse_id, catalog=catalog, schema=schema, wait_timeout="50s")
    waited = 0
    while r.status.state.value in ("PENDING", "RUNNING") and waited < max_wait:
        time.sleep(3); waited += 3
        r = _w.statement_execution.get_statement(r.statement_id)
    if r.status.state.value != "SUCCEEDED":
        raise RuntimeError(f"{label}: {r.status.state.value} — {r.status.error.message if r.status.error else '?'}")
    return r


def wh_rows(stmt, label=""):
    r = run_wh(stmt, label)
    if r.result is None or r.result.data_array is None:
        return []
    cols = [c.name for c in r.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in r.result.data_array]


ensure_dq_tables()
print("DQ tables ready")

# COMMAND ----------

# MAGIC %md ## ref_feed_source — what each source is, in plain words

# COMMAND ----------

SRC_ROWS = [
    # source_id, source_name, peril_code, provider, carries, declared_mode, cadence, licence, url, status_if_missing, sort
    ("METEOALARM", "MeteoAlarm — European weather warnings", "STORM · FLOOD · FIRE",
     "EUMETNET MeteoAlarm (38 national weather services; Météo-France for France)",
     "Official severe-weather warnings (wind, thunderstorm, rain and flood, forest fire) by region; French regions are mapped to Eurostat NUTS3 boundaries.",
     "REAL-TIME", "Hourly — keyless API", "MeteoAlarm terms: free re-use with attribution (Source: MeteoAlarm / EUMETNET)",
     "https://feeds.meteoalarm.org", "NOT YET INGESTED", 1),
    ("EA", "Environment Agency — UK flood warnings", "FLOOD",
     "Environment Agency (England) flood-monitoring API",
     "Live flood alerts and warnings with their flood-area polygons; published as active zones to the underwriting workbench.",
     "REAL-TIME", "Every 15 minutes upstream — keyless API", "Open Government Licence v3.0 (contains public sector information)",
     "https://environment.data.gov.uk/flood-monitoring/doc/reference", "NOT YET INGESTED", 2),
    ("GDACS", "GDACS — global disaster alerts", "FLOOD · STORM · FIRE · QUAKE",
     "EU Joint Research Centre and UN OCHA — Global Disaster Alert and Coordination System",
     "Disaster alerts (floods, cyclones, wildfires, earthquakes) worldwide; Event Radar scans them for early signals near the book.",
     "REAL-TIME", "Every news-radar run — keyless RSS", "GDACS terms of use: free with attribution (European Commission JRC)",
     "https://www.gdacs.org", "NOT YET INGESTED", 3),
    ("FIRMS", "NASA FIRMS — active-fire hotspots", "FIRE",
     "NASA FIRMS / EOSDIS LANCE (VIIRS 375 m satellite)",
     "Satellite active-fire detections, clustered into a fire footprint.",
     "BATCH", "Daily batch — a free MAP_KEY switches on the live pull", "NASA open data: free with attribution (NASA FIRMS)",
     "https://firms.modaps.eosdis.nasa.gov", "NOT YET INGESTED", 4),
    ("EFFIS", "Copernicus EFFIS — burnt areas", "FIRE",
     "Copernicus Emergency Management Service — EFFIS / GWIS",
     "Mapped burnt-area perimeters (polygons) — the wildfire source an exposure team relies on.",
     "BATCH", "Daily — keyless web feature service", "Copernicus data policy: free, full and open, with attribution",
     "https://forest-fire.emergency.copernicus.eu", "NOT YET INGESTED", 5),
    ("EMS", "Copernicus EMS — flood extents", "FLOOD",
     "Copernicus Emergency Management Service — Rapid Mapping",
     "Satellite-mapped flood extents from emergency activations (here a cross-border Alpine flood).",
     "BATCH", "Per activation — hours to days after the event", "Copernicus data policy: free, full and open, with attribution",
     "https://emergency.copernicus.eu", "NOT YET INGESTED", 6),
    ("GLOFAS", "Copernicus GloFAS / EFAS — flood forecast", "FLOOD",
     "Copernicus Emergency Management Service (run by ECMWF)",
     "Probabilistic river-flood forecasts. Wired to a secret; needs a free Copernicus CDS key, so not ingested in the demo.",
     "BATCH", "Daily forecast — needs a free CDS key", "Copernicus data policy: free, full and open, with attribution",
     "https://global-flood.emergency.copernicus.eu", "NOT CONFIGURED", 7),
    ("STREAM", "Live ingestion heartbeat", "—",
     "Bricksurance — streaming ingestion proof (Auto Loader)",
     "A real streaming feed that lands a hazard-feed record every ~20 seconds while the stream job runs, to show continuous ingestion.",
     "REAL-TIME", "Every ~20 seconds while the stream job runs", "Internal",
     "", "NOT RUNNING", 8),
    ("SYNTHETIC", "Bricksurance scenario events", "FIRE · FLOOD · STORM",
     "Bricksurance demo (synthetic, seed 42)",
     "Reproducible demo scenarios — Var wildfire, Esterel brush fire, Alpine flood, windstorms Celine and Nord.",
     "FROZEN SAMPLE", "Reloaded on every reset", "Synthetic — no real data",
     "", "NOT YET INGESTED", 9),
]
spark.createDataFrame(SRC_ROWS, (
    "source_id string, source_name string, peril_code string, provider string, carries string, "
    "declared_mode string, cadence string, licence string, url string, status_if_missing string, sort_order int"
)).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_feed_source")
run_wh(f"COMMENT ON TABLE {fqn}.ref_feed_source IS 'One row per data source: what it carries, the provider, real-time vs batch, cadence and licence. Joined to the DQ log in gov_source_catalog.'", "comment ref_feed_source")
print(f"ref_feed_source: {len(SRC_ROWS)} sources")

# COMMAND ----------

# MAGIC %md ## Seeded rejected batch — a MeteoAlarm pull with swapped coordinates (deterministic, re-seeded by reset)

# COMMAND ----------

for t in ("gov_feed_dq_log", "`1_feed_quarantine`"):
    spark.sql(f"DELETE FROM {fqn}.{t} WHERE run_id LIKE 'SEED-%'")

# 21 French warning regions (small boxes around real department centres). In this corrupted pull, 8 of them came
# back with latitude and longitude reversed, which puts them in the Gulf of Guinea / Indian Ocean.
_CENTRES = [("75", 2.35, 48.86), ("69", 4.84, 45.76), ("13", 5.37, 43.30), ("31", 1.44, 43.60), ("06", 7.26, 43.70),
            ("44", -1.55, 47.22), ("67", 7.75, 48.58), ("34", 3.88, 43.61), ("33", -0.58, 44.84), ("59", 3.06, 50.63),
            ("35", -1.68, 48.11), ("51", 4.03, 49.26), ("83", 5.93, 43.12), ("38", 5.72, 45.19), ("21", 5.04, 47.32),
            ("49", -0.55, 47.47), ("30", 4.36, 43.84), ("63", 3.09, 45.78), ("87", 1.26, 45.83), ("37", 0.69, 47.39),
            ("84", 4.81, 43.95)]
_SWAPPED = {2, 5, 7, 9, 12, 14, 17, 19}   # 8 of 21 = 38%


def _box(x, y, d=0.15):
    return f"POLYGON(({x-d} {y-d}, {x+d} {y-d}, {x+d} {y+d}, {x-d} {y+d}, {x-d} {y-d}))"


seed_now = datetime.datetime.utcnow()
seed_pulled = (seed_now - datetime.timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
seed_rows = []
for i, (dep, lon, lat) in enumerate(_CENTRES):
    x, y = (lat, lon) if i in _SWAPPED else (lon, lat)
    seed_rows.append({"event_id": f"EVT_LIVE_MA_FR_STORM_{seed_pulled:%Y%m%d}", "peril_code": "STORM",
                      "event_name": "MeteoAlarm FR STORM (orange wind warning)", "event_date": seed_pulled.date(),
                      "footprint_wkt": _box(x, y), "country_code": "FR",
                      "source_detail": f"MeteoAlarm warning region, department {dep}"})
_, seed_dec = dq_gate("METEOALARM", seed_rows, run_id=f"SEED-METEOALARM-{seed_pulled:%Y%m%d%H%M}",
                      pulled_at=seed_pulled,
                      reason_detail="the coordinates look swapped (latitude and longitude reversed)")
assert seed_dec["decision"] == "REJECTED", seed_dec
print("seeded rejected batch:", seed_dec["run_id"], "—", seed_dec["reason"])

# COMMAND ----------

# MAGIC %md ## Post-landing quality checks — GDACS (landed by 40_news_radar) and the scenario events (loaded by 01)

# COMMAND ----------

def _logged(run_id):
    return spark.table(f"{fqn}.gov_feed_dq_log").where(F.col("run_id") == run_id).limit(1).count() > 0

if spark.catalog.tableExists(f"{fqn}.`1_news_raw`"):
    g = spark.table(f"{fqn}.`1_news_raw`").where("source = 'GDACS'").collect()
    if g:
        batch_at = max(r.ingested_at for r in g)
        rid = f"GDACS-{batch_at:%Y%m%d%H%M%S}"
        if not _logged(rid):
            dq_gate("GDACS", [{"title": r.title, "url": r.url, "published_at": r.published_at} for r in g],
                    run_id=rid, pulled_at=batch_at,
                    note="live disaster alerts landed by the news radar; capped at 12 items")
        else:
            print("GDACS batch already checked:", rid)
    else:
        print("no GDACS items landed yet")

syn = spark.table(f"{fqn}.`2_event_footprint`").where("source = 'SYNTHETIC'").collect()
if syn:
    loaded = max([r.detected_at for r in syn if r.detected_at] or [datetime.datetime.utcnow()])
    rid = f"SYNTHETIC-{loaded:%Y%m%d%H%M%S}"
    if not _logged(rid):
        dq_gate("SYNTHETIC", [r.asDict() for r in syn], run_id=rid, pulled_at=loaded,
                note="Bricksurance scenario events loaded (deterministic, seed 42)")
    else:
        print("scenario load already checked:", rid)

# COMMAND ----------

# MAGIC %md ## gov_source_catalog — the governed view behind the Data sources screen

# COMMAND ----------

has_news = spark.catalog.tableExists(f"{fqn}.`1_news_raw`")
has_stream = spark.catalog.tableExists(f"{fqn}.stream_proof")
news_cte = (f"SELECT 'GDACS' AS source_id, bool_or(coalesce(is_live, false)) AS live_now, count(*) AS items "
            f"FROM {fqn}.`1_news_raw` WHERE source = 'GDACS'") if has_news else \
           "SELECT 'GDACS' AS source_id, false AS live_now, CAST(0 AS BIGINT) AS items"
stream_cte = (f"SELECT 'STREAM' AS source_id, max(ingested_at) AS ts, count(*) AS n FROM {fqn}.stream_proof") if has_stream else \
             "SELECT 'STREAM' AS source_id, CAST(NULL AS TIMESTAMP) AS ts, CAST(NULL AS BIGINT) AS n"

run_wh(f"""
CREATE OR REPLACE VIEW {fqn}.gov_source_catalog
COMMENT 'Data sources catalogue: one row per source (MeteoAlarm, Environment Agency UK flood, GDACS, NASA FIRMS, Copernicus EFFIS / EMS / GloFAS, the streaming heartbeat, the scenario events) with what it carries, real-time vs batch, cadence, licence, when it was last ingested, records in use, the last data-quality decision (ACCEPTED / REJECTED / FALLBACK_FROZEN) and its reason, and whether live data is in use right now.'
AS
WITH batches AS (
  SELECT run_id, source_id, max(pulled_at) AS pulled_at, max(n_records) AS n_records,
         max(decision) AS decision, max(reason) AS reason,
         concat_ws(', ', collect_list(CASE WHEN NOT passed THEN check_name END)) AS failed_checks
  FROM {fqn}.gov_feed_dq_log GROUP BY run_id, source_id),
latest AS (SELECT *, row_number() OVER (PARTITION BY source_id ORDER BY pulled_at DESC) AS rn FROM batches),
good AS (SELECT source_id, max(pulled_at) AS last_good_at, max_by(n_records, pulled_at) AS good_records
         FROM batches WHERE decision IN ('ACCEPTED', 'FALLBACK_FROZEN') GROUP BY source_id),
inuse AS (SELECT source, bool_or(coalesce(is_live, false)) AS live_now, count(DISTINCT event_id) AS events_in_use
          FROM {fqn}.`2_event_footprint` GROUP BY source),
news_c AS ({news_cte}),
heartbeat_c AS ({stream_cte})
SELECT
  s.source_id, s.source_name, s.peril_code, s.provider, s.carries,
  CASE WHEN l.decision = 'FALLBACK_FROZEN' THEN 'FROZEN SAMPLE'
       WHEN coalesce(u.events_in_use, 0) > 0 AND NOT coalesce(u.live_now, false) THEN 'FROZEN SAMPLE'
       ELSE s.declared_mode END AS mode,
  s.declared_mode, s.cadence, s.licence, s.url,
  coalesce(g.last_good_at, st.ts) AS last_ingested_at,
  CAST(coalesce(g.good_records, st.n) AS BIGINT) AS last_records,
  coalesce(l.decision,
           CASE WHEN st.ts > current_timestamp() - INTERVAL 15 MINUTES THEN 'STREAMING'
                WHEN st.ts IS NOT NULL THEN 'IDLE' END,
           s.status_if_missing) AS last_decision,
  l.reason AS last_reason, l.pulled_at AS last_batch_at, nullif(l.failed_checks, '') AS last_failed_checks,
  CASE WHEN s.source_id = 'STREAM' THEN coalesce(st.ts > current_timestamp() - INTERVAL 15 MINUTES, false)
       WHEN s.source_id = 'GDACS' THEN coalesce(nw.live_now, false)
       ELSE coalesce(u.live_now, false) END AS is_live,
  CAST(CASE WHEN s.source_id = 'GDACS' THEN coalesce(nw.items, 0)
            WHEN s.source_id = 'STREAM' THEN coalesce(st.n, 0)
            ELSE coalesce(u.events_in_use, 0) END AS BIGINT) AS items_in_use,
  s.sort_order
FROM {fqn}.ref_feed_source s
LEFT JOIN latest l ON l.source_id = s.source_id AND l.rn = 1
LEFT JOIN good g ON g.source_id = s.source_id
LEFT JOIN inuse u ON u.source = s.source_id
LEFT JOIN news_c nw ON nw.source_id = s.source_id
LEFT JOIN heartbeat_c st ON st.source_id = s.source_id
""", "gov_source_catalog")
print("gov_source_catalog view ready")

# COMMAND ----------

# MAGIC %md ## Governed functions — what the app and the agents call

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_source_status()
RETURNS TABLE (source_id STRING, source_name STRING, peril_code STRING, provider STRING, carries STRING,
               mode STRING, cadence STRING, licence STRING, last_ingested_at TIMESTAMP, last_records BIGINT,
               last_decision STRING, last_reason STRING, last_batch_at TIMESTAMP, is_live BOOLEAN, items_in_use BIGINT)
COMMENT 'Status of every data source feeding the exposure workbench: what it carries, real-time vs batch vs frozen sample, cadence, licence, when it was last ingested, how many records are in use, the last data-quality decision (ACCEPTED, REJECTED or FALLBACK_FROZEN) with its plain-English reason, and whether live data is in use right now. Use it to answer: which feeds are live, when was X last updated, can I trust the data, which sources are frozen samples.'
RETURN SELECT source_id, source_name, peril_code, provider, carries, mode, cadence, licence, last_ingested_at,
              last_records, last_decision, last_reason, last_batch_at, is_live, items_in_use
       FROM {fqn}.gov_source_catalog ORDER BY sort_order
""", "fn_source_status")

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_dq_recent(p_days INT)
RETURNS TABLE (run_id STRING, source_id STRING, source_name STRING, pulled_at TIMESTAMP, n_records INT,
               decision STRING, reason STRING, failed_checks STRING, checks_run INT, checks_passed INT)
COMMENT 'Recent data-quality decisions, one row per pulled feed batch over the last p_days days, newest first: source, when it was pulled, records, decision (ACCEPTED = loaded; REJECTED = failed a governed threshold, quarantined, previous data kept; FALLBACK_FROZEN = live pull unavailable, frozen sample used), the plain-English reason and which checks failed. Use it to answer: was any batch rejected, why was it rejected, what happened to the bad data, how often do feeds fail.'
RETURN
  SELECT b.run_id, b.source_id, s.source_name, b.pulled_at, b.n_records, b.decision, b.reason,
         b.failed_checks, b.checks_run, b.checks_passed
  FROM (SELECT run_id, source_id, max(pulled_at) AS pulled_at, CAST(max(n_records) AS INT) AS n_records,
               max(decision) AS decision, max(reason) AS reason,
               nullif(concat_ws(', ', collect_list(CASE WHEN NOT passed THEN check_name END)), '') AS failed_checks,
               CAST(count(*) AS INT) AS checks_run, CAST(sum(CASE WHEN passed THEN 1 ELSE 0 END) AS INT) AS checks_passed
        FROM {fqn}.gov_feed_dq_log
        WHERE pulled_at >= current_timestamp() - make_interval(0, 0, 0, p_days)
        GROUP BY run_id, source_id) b
  LEFT JOIN {fqn}.ref_feed_source s ON s.source_id = b.source_id
  ORDER BY b.pulled_at DESC
""", "fn_dq_recent")

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_dq_batch_checks(p_run_id STRING)
RETURNS TABLE (run_id STRING, source_id STRING, check_name STRING, description STRING, comparator STRING,
               threshold DOUBLE, observed DOUBLE, passed BOOLEAN, decision STRING)
COMMENT 'Every data-quality check run on one feed batch (by run_id from fn_dq_recent): the check, what it means, the governed threshold, the observed value and pass or fail. Use it to explain exactly why a batch was accepted or rejected.'
RETURN
  SELECT l.run_id, l.source_id, l.check_name, t.description, t.comparator, l.threshold, l.observed, l.passed, l.decision
  FROM {fqn}.gov_feed_dq_log l
  LEFT JOIN {fqn}.ref_dq_threshold t ON t.source_id = l.source_id AND t.check_name = l.check_name
  WHERE l.run_id = p_run_id
  ORDER BY l.passed, l.check_name
""", "fn_dq_batch_checks")
print("functions ready: fn_source_status, fn_dq_recent, fn_dq_batch_checks")

# COMMAND ----------

# MAGIC %md ## Grants — app service principal + account users (CREATE OR REPLACE drops grants, so re-apply every run)

# COMMAND ----------

for gtee in GRANTEES:
    for obj in ["ref_dq_threshold", "gov_feed_dq_log", "`1_feed_quarantine`", "ref_feed_source", "gov_source_catalog"]:
        run_wh(f"GRANT SELECT ON TABLE {fqn}.{obj} TO `{gtee}`", f"grant {obj}")
    for fn in ["fn_source_status", "fn_dq_recent", "fn_dq_batch_checks"]:
        run_wh(f"GRANT EXECUTE ON FUNCTION {fqn}.{fn} TO `{gtee}`", f"grant {fn}")
print("grants applied to", GRANTEES)

# COMMAND ----------

# MAGIC %md ## Verify

# COMMAND ----------

for r in wh_rows(f"SELECT source_id, mode, last_ingested_at, last_records, last_decision, is_live FROM {fqn}.fn_source_status()"):
    print(r)
print("--- recent batches ---")
for r in wh_rows(f"SELECT source_id, pulled_at, n_records, decision, failed_checks FROM {fqn}.fn_dq_recent(7) LIMIT 15"):
    print(r)
