# Databricks notebook source
# MAGIC %md
# MAGIC # 40 · Event Radar — the unstructured news sensor ("we knew before the satellite")
# MAGIC
# MAGIC The structured feeds (FIRMS, MeteoAlarm, GloFAS) are authoritative but *lagging* — a wildfire is often in
# MAGIC the press hours before it is in the satellite feed. This chapter adds the **unstructured** sensor:
# MAGIC
# MAGIC 1. **Scan** a real keyless disaster/news source (**GDACS RSS**, live) + a **frozen synthetic press item**
# MAGIC    (deterministic for the room) → bronze `1_news_raw`.
# MAGIC 2. **Extract** peril / place / severity / summary with **AI Functions** (`ai_classify`, `ai_query`) and
# MAGIC    geocode the place against a small governed gazetteer → `2_news_signal`.
# MAGIC 3. **Verify** (the crux, disconfirmation-first): does the signal *geospatially touch the book*, and has a
# MAGIC    *structured feed caught it yet*? → `fn_news_radar` returns threatened count/SI, a confidence, an
# MAGIC    evidence trail, and a **beats_feed** flag (touches the book AND no structured feed had it at the time).
# MAGIC 4. **Decide — human-gated.** The app surfaces verified signals; a person clicks **Promote to event & alert**.
# MAGIC    Promotion inserts a provisional `source='NEWS'` event into `2_event_footprint`, so it flows through the
# MAGIC    *same* exposure view + alert path. Every detection and decision is audited in `gov_news_decision`.
# MAGIC    Nothing consequential runs autonomously.
# MAGIC
# MAGIC Same split as the other notebooks: `ST_*` / `ai_*` run on the **SQL warehouse** via the Statement Execution
# MAGIC API (`run_wh`); plain governed tables are written with Spark.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "")
dbutils.widgets.text("fm_endpoint", "databricks-claude-sonnet-4-6")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
FM = dbutils.widgets.get("fm_endpoint").strip()
fqn = f"{catalog}.{schema}"
print(f"target = {fqn}  warehouse={warehouse_id}  fm={FM}  app_sp={app_sp or '(none yet)'}")

import json, time, urllib.request, urllib.error, datetime, re
import xml.etree.ElementTree as ET
from pyspark.sql import functions as F, types as T
from databricks.sdk import WorkspaceClient
_w = WorkspaceClient()


def run_wh(stmt, label="", max_wait=600):
    """Execute on the SQL warehouse; poll to completion (AI-function statements can exceed the 50s wait)."""
    r = _w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=warehouse_id, catalog=catalog, schema=schema, wait_timeout="50s")
    waited = 0
    while r.status.state.value in ("PENDING", "RUNNING") and waited < max_wait:
        time.sleep(4); waited += 4
        r = _w.statement_execution.get_statement(r.statement_id)
    st = r.status.state.value
    if st != "SUCCEEDED":
        raise RuntimeError(f"{label}: {st} — {r.status.error.message if r.status.error else '?'}")
    return r


def wh_rows(stmt, label=""):
    r = run_wh(stmt, label)
    if r.result is None or r.result.data_array is None:
        return []
    cols = [c.name for c in r.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in r.result.data_array]

# COMMAND ----------

# MAGIC %md ## ref_gazetteer — a small governed place → coordinate table
# MAGIC News reports a *place*, not a lat/long. This is the deterministic geocoder for the demo geography. In
# MAGIC production this is a real gazetteer / geocoding service; the join pattern is identical.

# COMMAND ----------

gaz = [
    # place_name, lat, lon   (name is matched case-insensitively as a substring of the news text;
    # longer names win, so "southern france" doesn't beat "Var")
    ("Var", 43.610, 6.720),            # SE-France insured cluster (the Hiscox wildfire story)
    ("Vidauban", 43.432, 6.434),
    ("Le Luc", 43.393, 6.312),
    ("Fréjus", 43.433, 6.737),
    ("Nice", 43.710, 7.262),
    ("Marseille", 43.296, 5.370),
    ("Provence", 43.530, 5.450),
    ("Turin", 45.070, 7.687),
    ("Milan", 45.464, 9.190),
    ("Aosta Valley", 45.735, 7.313),   # FR/IT/AT alpine cross-border
    ("Tyrol", 47.260, 11.395),
    ("Vienna", 48.209, 16.373),
    ("Madrid", 40.417, -3.703),
    ("Barcelona", 41.385, 2.173),
    ("Munich", 48.135, 11.582),
    ("Berlin", 52.520, 13.405),
    ("Hamburg", 53.551, 9.993),
]
gaz_schema = T.StructType([
    T.StructField("place_name", T.StringType()),
    T.StructField("lat", T.DoubleType()),
    T.StructField("lon", T.DoubleType()),
])
spark.createDataFrame(gaz, gaz_schema).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_gazetteer")
run_wh(f"COMMENT ON TABLE {fqn}.ref_gazetteer IS 'Governed place-to-coordinate gazetteer used to geocode the place named in an unstructured news signal. Longer names win a substring match. Deterministic stand-in for a production geocoder.'", "comment gaz")
print(f"ref_gazetteer = {len(gaz)} places")

# COMMAND ----------

# MAGIC %md ## 1_news_raw — bronze landing (frozen synthetic item + live GDACS)

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {fqn}.`1_news_raw` (
  signal_id STRING, source STRING, title STRING, body STRING, url STRING,
  published_at TIMESTAMP, ingested_at TIMESTAMP, is_live BOOLEAN
) USING DELTA
COMMENT 'Bronze landing for the unstructured event sensor — one row per news/disaster item (frozen synthetic press wire + live GDACS RSS).'
""")

# --- Frozen synthetic press item: a wildfire in the Var, published BEFORE the FIRMS sample event.
#     Dated relative to EVT_FIRMS_SAMPLE_FR so the "news beats the feed" gap always holds after a reset.
run_wh(f"""
MERGE INTO {fqn}.`1_news_raw` t
USING (
  SELECT
    'NEWS_FROZEN_VAR_FIRE' AS signal_id,
    'Wire (synthetic)' AS source,
    'Fast-moving wildfire spreads through hills in the Var, southern France; evacuations ordered' AS title,
    'Emergency services battled a fast-moving wildfire in the Var department of southern France overnight, with flames threatening residential areas as hot, dry winds pushed the fire front toward the coast. Local authorities ordered evacuations and warned that several hamlets were at risk.' AS body,
    'https://example.invalid/wire/var-wildfire' AS url,
    timestampadd(HOUR, -14, CAST((SELECT event_date FROM {fqn}.`2_event_footprint` WHERE event_id='EVT_FIRMS_SAMPLE_FR' LIMIT 1) AS TIMESTAMP)) AS published_at,
    current_timestamp() AS ingested_at,
    false AS is_live
) s
ON t.signal_id = s.signal_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""", "frozen news item")
print("frozen press item upserted")

# COMMAND ----------

# MAGIC %md ### Live GDACS RSS — keyless, real (robust fetch; never breaks the run)

# COMMAND ----------

UA = {"User-Agent": "bricksurance-exposure-demo/1.0 (contact laurence.ryszka@databricks.com)"}


def robust_get(url, timeout=30, retries=3, backoff=2.0):
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 — resilience is the point
            last = e
            print(f"  [gdacs] attempt {a+1}/{retries} failed ({e})")
            time.sleep(backoff ** a)
    print(f"  [gdacs] gave up: {last}")
    return None


def _txt(el, tag, ns=None):
    f = el.find(tag, ns) if ns else el.find(tag)
    return (f.text or "").strip() if f is not None and f.text else ""


gdacs_rows = []
xml = robust_get("https://www.gdacs.org/xml/rss.xml")
if xml:
    try:
        root = ET.fromstring(xml)
        ns = {"geo": "http://www.w3.org/2003/01/geo/wgs84_pos#", "gdacs": "http://www.gdacs.org"}
        for it in root.iter("item"):
            title = _txt(it, "title")
            desc = re.sub("<[^>]+>", " ", _txt(it, "description"))[:1200]
            link = _txt(it, "link")
            pub = _txt(it, "pubDate")
            try:
                pub_ts = datetime.datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S")
            except Exception:
                pub_ts = datetime.datetime.utcnow()
            sid = "NEWS_GDACS_" + (re.sub(r"[^A-Za-z0-9]", "", link)[-16:] or str(len(gdacs_rows)))
            gdacs_rows.append((sid, "GDACS", title, desc, link, pub_ts, datetime.datetime.utcnow(), True))
        gdacs_rows = gdacs_rows[:12]  # cap AI-extraction cost/latency
        print(f"  [gdacs] parsed {len(gdacs_rows)} live items (capped at 12)")
    except Exception as e:  # noqa: BLE001
        print(f"  [gdacs] parse failed ({e}) — continuing with frozen item only")

if gdacs_rows:
    raw_schema = T.StructType([
        T.StructField("signal_id", T.StringType()), T.StructField("source", T.StringType()),
        T.StructField("title", T.StringType()), T.StructField("body", T.StringType()),
        T.StructField("url", T.StringType()), T.StructField("published_at", T.TimestampType()),
        T.StructField("ingested_at", T.TimestampType()), T.StructField("is_live", T.BooleanType()),
    ])
    df = spark.createDataFrame(gdacs_rows, raw_schema).dropDuplicates(["signal_id"])
    # delete-then-append so the live GDACS set stays bounded to the cap on every run (the feed is large)
    spark.sql(f"DELETE FROM {fqn}.`1_news_raw` WHERE source = 'GDACS'")
    df.write.mode("append").saveAsTable(f"{fqn}.`1_news_raw`")
    print(f"  [gdacs] refreshed {df.count()} live rows in 1_news_raw")

print("1_news_raw total:", spark.table(f"{fqn}.`1_news_raw`").count())

# COMMAND ----------

# MAGIC %md ## 2_news_signal — AI extraction (peril / severity / summary) + gazetteer geocode
# MAGIC Real AI Functions on the warehouse: `ai_classify` for peril + severity, `ai_query` (Claude) for a factual
# MAGIC one-line summary. Location comes from the governed gazetteer (longest-name substring match).

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE TABLE {fqn}.`2_news_signal` AS
SELECT
  r.signal_id, r.source, r.is_live, r.title, r.url, r.published_at,
  ai_classify(concat(r.title, '. ', r.body), array('FIRE','FLOOD','STORM','OTHER')) AS peril_code,
  ai_classify(concat(r.title, '. ', r.body), array('LOW','MEDIUM','HIGH'))          AS severity,
  ai_query('{FM}', concat('In one short factual sentence, summarise this hazard report for an insurance exposure team: ', r.title, '. ', r.body)) AS summary,
  g.place_name,
  CAST(g.lat AS DOUBLE) AS latitude,
  CAST(g.lon AS DOUBLE) AS longitude
FROM {fqn}.`1_news_raw` r
LEFT JOIN LATERAL (
  SELECT gg.place_name, gg.lat, gg.lon
  FROM {fqn}.ref_gazetteer gg
  WHERE concat(lower(r.title), ' ', lower(r.body)) LIKE concat('%', lower(gg.place_name), '%')
  ORDER BY length(gg.place_name) DESC
  LIMIT 1
) g
""", "2_news_signal (ai extract + geocode)")
run_wh(f"COMMENT ON TABLE {fqn}.`2_news_signal` IS 'Structured news signals: peril + severity via ai_classify, one-line summary via ai_query (Claude), location geocoded against ref_gazetteer. One row per 1_news_raw item.'", "comment signal")
print("2_news_signal:", spark.table(f"{fqn}.`2_news_signal`").count())

# COMMAND ----------

# MAGIC %md ## fn_news_radar — governed verification (touches the book? beaten the feed?)
# MAGIC The crux, disconfirmation-first. For each signal: (a) does it intersect the book (properties within
# MAGIC `p_buffer_m` of the geocoded point, on the metric ST_ path)? (b) has a *structured* feed of the same peril
# MAGIC already caught it *at or before* the signal time & nearby? `beats_feed` = touches the book AND not yet
# MAGIC corroborated. Confidence and status are derived and auditable; the app reads only this function.

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_news_radar()
RETURNS TABLE (
  signal_id STRING, source STRING, is_live BOOLEAN, peril_code STRING, severity STRING,
  place_name STRING, latitude DOUBLE, longitude DOUBLE, published_at TIMESTAMP,
  title STRING, summary STRING, threatened_count BIGINT, sum_insured DOUBLE,
  corroborated BOOLEAN, beats_feed BOOLEAN, confidence DOUBLE, status STRING, evidence STRING
)
COMMENT 'Event Radar verification. For each unstructured news signal, returns how many insured properties sit within 3000 m of the geocoded location (the ST_ metric path), whether a structured feed of the same peril already caught it at or before the signal time nearby (corroborated), a beats_feed flag (touches the book AND not yet in a structured feed = an early warning), plus a derived confidence, status and human-readable evidence trail. Disconfirmation-first: a signal that does not touch the book scores low. The app and agent read exposure decisions from here; they never invent them.'
RETURN
SELECT
  s.signal_id, s.source, s.is_live, s.peril_code, s.severity, s.place_name,
  s.latitude, s.longitude, s.published_at, s.title, s.summary,
  coalesce(tc.threatened_count, 0)            AS threatened_count,
  coalesce(tc.sum_insured, 0.0)               AS sum_insured,
  coalesce(cor.corroborated, false)           AS corroborated,
  (coalesce(tc.threatened_count,0) > 0 AND NOT coalesce(cor.corroborated,false)) AS beats_feed,
  CASE
    WHEN s.latitude IS NULL OR coalesce(tc.threatened_count,0) = 0 THEN 0.15
    ELSE least(1.0, 0.5
      + CASE s.severity WHEN 'HIGH' THEN 0.3 WHEN 'MEDIUM' THEN 0.15 ELSE 0.0 END
      + CASE WHEN tc.threatened_count >= 20 THEN 0.2 WHEN tc.threatened_count >= 5 THEN 0.1 ELSE 0.0 END)
  END                                         AS confidence,
  CASE
    WHEN s.latitude IS NULL OR coalesce(tc.threatened_count,0) = 0 THEN 'dismissed'
    WHEN (0.5 + CASE s.severity WHEN 'HIGH' THEN 0.3 WHEN 'MEDIUM' THEN 0.15 ELSE 0.0 END
              + CASE WHEN tc.threatened_count >= 20 THEN 0.2 WHEN tc.threatened_count >= 5 THEN 0.1 ELSE 0.0 END) >= 0.6
      THEN 'verified'
    ELSE 'candidate'
  END                                         AS status,
  concat_ws(' · ',
    CASE WHEN s.latitude IS NULL THEN 'No geocode — cannot locate against book'
         ELSE concat('Geocoded to ', s.place_name) END,
    CASE WHEN coalesce(tc.threatened_count,0) > 0
         THEN concat('Touches book: ', cast(tc.threatened_count as string), ' insured properties within 3000m')
         ELSE 'Does not touch the book' END,
    CASE WHEN coalesce(cor.corroborated,false) THEN 'Already in a structured feed'
         ELSE 'Not yet in any structured feed (early)' END,
    concat('Severity ', coalesce(s.severity,'?'))
  )                                           AS evidence
FROM {fqn}.`2_news_signal` s
LEFT JOIN LATERAL (
  SELECT count(DISTINCT p.insured_object_id) AS threatened_count,
         cast(coalesce(sum(p.sum_insured), 0.0) AS DOUBLE) AS sum_insured
  FROM {fqn}.`3_property` p
  WHERE s.latitude IS NOT NULL AND p.latitude IS NOT NULL
    AND ST_DWithin(
          ST_Transform(ST_SetSRID(ST_Point(CAST(p.longitude AS DOUBLE), CAST(p.latitude AS DOUBLE)), 4326), 3035),
          ST_Transform(ST_SetSRID(ST_Point(s.longitude, s.latitude), 4326), 3035),
          3000)
) tc
LEFT JOIN LATERAL (
  SELECT count(*) > 0 AS corroborated
  FROM {fqn}.`2_event_footprint` e
  -- corroboration counts only real structured FEEDS (satellite / official warning), never SYNTHETIC
  -- illustrative footprints or prior NEWS promotions — "has a feed caught it yet?"
  WHERE e.source IN ('FIRMS','EFFIS','METEOALARM','GLOFAS','EMS') AND e.peril_code = s.peril_code AND s.latitude IS NOT NULL
    AND e.event_date <= CAST(s.published_at AS DATE)
    AND ST_Distance(
          ST_Transform(ST_Centroid(ST_GeomFromText(e.footprint_wkt, 4326)), 3035),
          ST_Transform(ST_SetSRID(ST_Point(s.longitude, s.latitude), 4326), 3035)
        ) < 40000
) cor
""", "fn_news_radar")
print("fn_news_radar created")

# COMMAND ----------

# MAGIC %md ## gov_news_decision — append-only audit of every human decision (promote / dismiss)

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {fqn}.gov_news_decision (
  signal_id STRING, action STRING, event_id STRING, decided_by STRING,
  decided_at TIMESTAMP, confidence DOUBLE, evidence STRING
) USING DELTA
COMMENT 'Append-only audit of Event Radar decisions. Auto-detect / human-decide: promoting a news signal to a provisional event, or dismissing it, is a human action recorded here (who, when, on what confidence & evidence).'
""")
print("gov_news_decision ready")

# COMMAND ----------

# MAGIC %md ## Grants — the app SP reads the radar and (on human click) promotes + audits

# COMMAND ----------

if app_sp:
    for stmt in [
        f"GRANT SELECT ON TABLE {fqn}.`1_news_raw` TO `{app_sp}`",
        f"GRANT SELECT ON TABLE {fqn}.`2_news_signal` TO `{app_sp}`",
        f"GRANT SELECT ON TABLE {fqn}.ref_gazetteer TO `{app_sp}`",
        f"GRANT SELECT, MODIFY ON TABLE {fqn}.gov_news_decision TO `{app_sp}`",
        f"GRANT SELECT, MODIFY ON TABLE {fqn}.`2_event_footprint` TO `{app_sp}`",
        f"GRANT EXECUTE ON FUNCTION {fqn}.fn_news_radar TO `{app_sp}`",
    ]:
        run_wh(stmt, "grant app_sp")
    print(f"granted app SP {app_sp}")
# account users can read too (demo transparency)
for obj in ["`1_news_raw`", "`2_news_signal`", "ref_gazetteer", "gov_news_decision"]:
    run_wh(f"GRANT SELECT ON TABLE {fqn}.{obj} TO `account users`", "grant users")
run_wh(f"GRANT EXECUTE ON FUNCTION {fqn}.fn_news_radar TO `account users`", "grant users fn")
print("grants applied")

# COMMAND ----------

# MAGIC %md ## Verify — the hero: the frozen wildfire beats the FIRMS feed

# COMMAND ----------

rows = wh_rows(f"SELECT signal_id, peril_code, severity, place_name, threatened_count, round(sum_insured/1e6,1) AS si_m, corroborated, beats_feed, round(confidence,2) AS conf, status, is_live FROM {fqn}.fn_news_radar() ORDER BY beats_feed DESC, confidence DESC", "verify radar")
for r in rows:
    print(r)

fire = [r for r in rows if r["signal_id"] == "NEWS_FROZEN_VAR_FIRE"]
if fire:
    f0 = fire[0]
    print("\nHERO — frozen Var wildfire:",
          f"peril={f0['peril_code']} touches={f0['threatened_count']} props €{f0['si_m']}m",
          f"beats_feed={f0['beats_feed']} confidence={f0['conf']} status={f0['status']}")
