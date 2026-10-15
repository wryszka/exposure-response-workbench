# Databricks notebook source
# MAGIC %md
# MAGIC # 10 · Feed ingestion — shared helpers (the "we fix your API-timeout pain" layer)
# MAGIC
# MAGIC The customer's analyst hit **timeouts / blocking** pulling hazard feeds by hand. This layer is the answer:
# MAGIC one robust fetch (timeout + exponential backoff + retry), one geometry-normalisation step
# MAGIC (points / regions → WKT polygon in the `2_event_footprint` shape), a bronze landing of every raw pull
# MAGIC (`1_hazard_feed_raw`), and one idempotent replace into `2_event_footprint` tagged by `source` + `is_live`.
# MAGIC
# MAGIC The peril adapters (`11_feed_wildfire`, `11b_feed_effis`, `12_feed_windstorm`, `13_feed_flood`, `14_feed_ea_flood`)
# MAGIC `%run` this notebook, then call these helpers. **Every batch passes the data-quality gate (`dq_gate`) before
# MAGIC it may replace a source's footprints** — a batch that fails a governed threshold (`ref_dq_threshold`) is
# MAGIC REJECTED, quarantined (`1_feed_quarantine`) and the previous accepted data stays in place; every check of
# MAGIC every batch is logged to `gov_feed_dq_log`. Live where a feed is keyless (MeteoAlarm), frozen-real-sample where a credential is
# MAGIC required (FIRMS MAP_KEY, Copernicus CDS key) — see `docs/FEEDS.md`. Nothing here needs `ST_*` (footprints
# MAGIC are stored as WKT text; the governed functions do the geospatial maths on the warehouse).

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
fqn = f"{catalog}.{schema}"

import json, time, re, uuid, urllib.request, urllib.error, datetime
from pyspark.sql import functions as F, types as T

UA = {"User-Agent": "bricksurance-exposure-demo/1.0 (contact laurence.ryszka@databricks.com)"}
SOURCES = ("FIRMS", "EFFIS", "METEOALARM", "GLOFAS", "EMS", "EA", "GDACS", "SYNTHETIC")

# COMMAND ----------

# MAGIC %md ## robust_get — timeout + exponential backoff + retry (solves the customer's manual-pull pain)

# COMMAND ----------

def robust_get(url, headers=None, timeout=30, retries=4, backoff=2.0, expect="text"):
    """GET with exponential backoff. Returns text/bytes/json, or None after exhausting retries (never raises —
    the adapter then falls back to its frozen sample and logs it, so a flaky feed never breaks the job)."""
    hdr = dict(UA); hdr.update(headers or {})
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            if expect == "bytes": return raw
            txt = raw.decode("utf-8", "replace")
            return json.loads(txt) if expect == "json" else txt
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            wait = backoff ** attempt
            print(f"    [robust_get] attempt {attempt+1}/{retries} failed ({e}); retry in {wait:.1f}s")
            time.sleep(wait)
    print(f"    [robust_get] GAVE UP on {url}: {last}")
    return None

# COMMAND ----------

# MAGIC %md ## GeoJSON geometry → WKT (pure Python — no shapely/geopandas dependency)

# COMMAND ----------

def _ring(cs):
    return "(" + ", ".join(f"{x} {y}" for x, y in cs) + ")"

def geojson_to_wkt(geom):
    """GeoJSON Polygon/MultiPolygon → WKT in EPSG:4326 (the 2_event_footprint convention)."""
    t, c = geom["type"], geom["coordinates"]
    if t == "Polygon":
        return "POLYGON(" + ", ".join(_ring(r) for r in c) + ")"
    if t == "MultiPolygon":
        return "MULTIPOLYGON(" + ", ".join("(" + ", ".join(_ring(r) for r in poly) + ")" for poly in c) + ")"
    raise ValueError(f"unsupported geometry {t}")

def points_to_footprint_wkt(points, buffer_deg=0.02):
    """Cluster of (lon,lat) points → a single POLYGON footprint (axis-aligned buffered bbox). Used for
    FIRMS active-fire hotspots, which arrive as points, not perimeters. buffer_deg ~0.02° ≈ 2 km."""
    if not points: return None
    lons = [p[0] for p in points]; lats = [p[1] for p in points]
    x0, x1 = min(lons) - buffer_deg, max(lons) + buffer_deg
    y0, y1 = min(lats) - buffer_deg, max(lats) + buffer_deg
    return f"POLYGON(({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}))"

# COMMAND ----------

# MAGIC %md ## GISCO NUTS3 boundaries — cached to a Volume (MeteoAlarm warns by region, not polygon)

# COMMAND ----------

def load_nuts3_wkt(year=2013, res="20M", country=None):
    """Eurostat GISCO NUTS3 boundaries (keyless) → {NUTS_ID: wkt}. Cached to a UC Volume so repeated feed
    runs don't re-download. year=2013 matches the NUTS vintage MeteoAlarm's FR NUTS3 codes use."""
    vol_dir = f"/Volumes/{catalog}/{schema}/reference"
    try:
        spark.sql(f"CREATE VOLUME IF NOT EXISTS {fqn}.reference")
    except Exception as e:
        print(f"    volume create note: {e}")
    fname = f"nuts3_{res}_{year}.geojson"
    path = f"{vol_dir}/{fname}"
    try:
        with open(path, "r") as f:
            g = json.load(f)
        print(f"    NUTS3 from cache: {path}")
    except Exception:
        url = (f"https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
               f"NUTS_RG_{res}_{year}_4326_LEVL_3.geojson")
        txt = robust_get(url, timeout=60, expect="text")
        if txt is None:
            print("    NUTS3 download failed — region→polygon mapping unavailable this run")
            return {}
        g = json.loads(txt)
        try:
            with open(path, "w") as f:
                f.write(txt)
            print(f"    NUTS3 cached to {path}")
        except Exception as e:
            print(f"    NUTS3 cache-write note: {e}")
    out = {}
    for feat in g["features"]:
        p = feat["properties"]
        if country and p.get("CNTR_CODE") != country:
            continue
        try:
            out[p["NUTS_ID"]] = geojson_to_wkt(feat["geometry"])
        except Exception:
            pass
    return out

# COMMAND ----------

# MAGIC %md ## Data-quality gate — every batch is checked before it may replace a source's data
# MAGIC
# MAGIC `dq_gate(source_id, records)` runs governed checks on one pulled batch and returns
# MAGIC `(accepted_records, decision)`. Thresholds live in `ref_dq_threshold` (seeded here, editable — a MERGE never
# MAGIC overwrites an edited threshold). Decisions:
# MAGIC - **ACCEPTED** — every check passed → the adapter replaces the source's footprints with this batch.
# MAGIC - **REJECTED** — at least one check failed → the batch goes to `1_feed_quarantine` and **the previous accepted
# MAGIC   data is kept** (a bad pull never overwrites good data).
# MAGIC - **FALLBACK_FROZEN** — the live pull was unavailable and a frozen real-shape sample was used (still checked).
# MAGIC
# MAGIC One row per check per batch is appended to `gov_feed_dq_log` — the audit trail behind the Data sources screen.

# COMMAND ----------

EUROPE_UK_BBOX = (-25.0, 34.0, 45.0, 72.0)  # west, south, east, north — continental Europe + UK/IE (the book's area)

# check_name -> (comparator, default threshold, plain-English description)
_DQ_DEFAULTS = {
    "record_count":         (">=", 1.0,  "The batch carries at least this many records (warning feeds allow 0 = 'no active warnings')"),
    "pct_null_geometry":    ("<=", 2.0,  "% of records with no footprint or coordinates"),
    "pct_invalid_geometry": ("<=", 1.0,  "% of records with malformed geometry (unclosed ring, under 4 vertices, coordinates out of range)"),
    "pct_outside_bbox":     ("<=", 5.0,  "% of records located outside the Europe + UK area of the book (catches swapped lat/lon or a wrong-region pull)"),
    "pct_duplicate":        ("<=", 10.0, "% of records duplicating another record in the same batch"),
    "freshness_hours":      ("<=", 72.0, "Age in hours of the newest record, against the feed's expected cadence"),
}
# per-source overrides: (check_name -> threshold). Warning feeds may legitimately be empty.
_DQ_SOURCE_OVERRIDES = {
    "METEOALARM": {"record_count": 0.0, "freshness_hours": 24.0},
    "EA":         {"record_count": 0.0, "freshness_hours": 24.0},
    "GDACS":      {"freshness_hours": 72.0},
    "FIRMS":      {"freshness_hours": 72.0},
    "EFFIS":      {"freshness_hours": 240.0},   # burnt areas are mapped days after the fire
    "EMS":        {"freshness_hours": 720.0},
    "GLOFAS":     {"freshness_hours": 48.0},
    "SYNTHETIC":  {},
}
_NO_FRESHNESS = {"SYNTHETIC"}  # scenario events are not a feed: no freshness expectation
_SRC_NAME = {"METEOALARM": "MeteoAlarm", "EA": "Environment Agency", "GDACS": "GDACS", "FIRMS": "NASA FIRMS",
             "EFFIS": "Copernicus EFFIS", "EMS": "Copernicus EMS", "GLOFAS": "Copernicus GloFAS",
             "SYNTHETIC": "scenario"}

_dq_ready = False

def ensure_dq_tables():
    """Create the DQ tables (idempotent) and seed any missing thresholds."""
    global _dq_ready
    if _dq_ready:
        return
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {fqn}.ref_dq_threshold (
        source_id STRING, check_name STRING, threshold DOUBLE, comparator STRING, description STRING)
      USING DELTA COMMENT 'Governed data-quality thresholds per feed and check. dq_gate (10_feeds_common) reads these for every batch; edit a threshold here to tune the gate.'""")
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {fqn}.gov_feed_dq_log (
        run_id STRING, source_id STRING, pulled_at TIMESTAMP, n_records INT, check_name STRING,
        observed DOUBLE, threshold DOUBLE, passed BOOLEAN, decision STRING, reason STRING)
      USING DELTA COMMENT 'Append-only data-quality log: one row per check per pulled batch, with the batch decision (ACCEPTED / REJECTED / FALLBACK_FROZEN) and a plain-English reason.'""")
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {fqn}.`1_feed_quarantine` (
        run_id STRING, source_id STRING, pulled_at TIMESTAMP, n_records INT, reason STRING, payload_excerpt STRING)
      USING DELTA COMMENT 'Quarantine for REJECTED feed batches: the batch is held here and never written to 2_event_footprint; the previous accepted data stays in use.'""")
    seed = []
    for src in SOURCES:
        ov = _DQ_SOURCE_OVERRIDES.get(src, {})
        for ck, (comp, thr, desc) in _DQ_DEFAULTS.items():
            if ck == "freshness_hours" and src in _NO_FRESHNESS:
                continue
            seed.append((src, ck, float(ov.get(ck, thr)), comp, desc))
    (spark.createDataFrame(seed, "source_id string, check_name string, threshold double, comparator string, description string")
          .createOrReplaceTempView("_dq_threshold_seed"))
    spark.sql(f"""MERGE INTO {fqn}.ref_dq_threshold t USING _dq_threshold_seed s
                  ON t.source_id = s.source_id AND t.check_name = s.check_name
                  WHEN NOT MATCHED THEN INSERT *""")
    _dq_ready = True


_DQ_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

def _dq_parse_geom(rec):
    """Return (kind, rings) for a record: kind in point/polygon/none/bad. Pure Python, no ST_ needed."""
    wkt = rec.get("footprint_wkt")
    if wkt:
        s = str(wkt).strip().upper()
        if s.startswith("POINT"):
            v = _DQ_NUM.findall(s)
            return ("point", [[(float(v[0]), float(v[1]))]]) if len(v) >= 2 else ("bad", None)
        if not (s.startswith("POLYGON") or s.startswith("MULTIPOLYGON")):
            return ("bad", None)
        rings = []
        for body in re.findall(r"\(([^()]+)\)", s):
            pts = []
            for pair in body.split(","):
                v = _DQ_NUM.findall(pair)
                if len(v) < 2:
                    return ("bad", None)
                pts.append((float(v[0]), float(v[1])))
            rings.append(pts)
        return ("polygon", rings) if rings else ("bad", None)
    lat, lon = rec.get("latitude"), rec.get("longitude")
    if lat is not None and lon is not None:
        try:
            return ("point", [[(float(lon), float(lat))]])
        except (TypeError, ValueError):
            return ("bad", None)
    return ("none", None)


def _dq_geom_valid(kind, rings):
    if kind not in ("point", "polygon") or not rings:
        return False
    for ring in rings:
        if any(abs(x) > 180 or abs(y) > 90 for x, y in ring):
            return False
    if kind == "point":
        return True
    for ring in rings:
        if len(ring) < 4:
            return False
        (x0, y0), (x1, y1) = ring[0], ring[-1]
        if abs(x0 - x1) > 1e-9 or abs(y0 - y1) > 1e-9:
            return False
    return True


def _dq_centroid(rings):
    ring = rings[0][:-1] if len(rings[0]) > 1 else rings[0]
    xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _dq_rec_time(rec):
    for k in ("observed_at", "published_at", "event_date", "detected_at"):
        v = rec.get(k)
        if v is None:
            continue
        if isinstance(v, datetime.datetime):
            return v.replace(tzinfo=None)
        if isinstance(v, datetime.date):
            return datetime.datetime(v.year, v.month, v.day)
        try:
            return datetime.datetime.fromisoformat(str(v)[:19])
        except ValueError:
            continue
    return None


_DQ_FAIL_TEXT = {
    "record_count":         lambda o, t, n: f"the pull returned {int(o)} records (at least {int(t)} expected)",
    "pct_null_geometry":    lambda o, t, n: f"{o:.0f}% of records ({round(o * n / 100)} of {n}) have no location (limit {t:g}%)",
    "pct_invalid_geometry": lambda o, t, n: f"{o:.0f}% of records ({round(o * n / 100)} of {n}) have malformed geometry (limit {t:g}%)",
    "pct_outside_bbox":     lambda o, t, n: f"{o:.0f}% of records ({round(o * n / 100)} of {n}) fall outside Europe and the UK (limit {t:g}%)",
    "pct_duplicate":        lambda o, t, n: f"{o:.0f}% of records are duplicates (limit {t:g}%)",
    "freshness_hours":      lambda o, t, n: f"the newest record is {o:.0f} hours old (expected within {t:g} hours)",
    "feed_reachable":       lambda o, t, n: "the feed could not be reached after retries",
}


def _dq_log_batch(run_id, source_id, pulled_at, n, results, decision, reason, quarantine_excerpt=None):
    rows = [(run_id, source_id, pulled_at, int(n), ck, None if ob is None else float(ob),
             None if th is None else float(th), bool(ok), decision, reason) for ck, ob, th, ok in results]
    (spark.createDataFrame(rows, "run_id string, source_id string, pulled_at timestamp, n_records int, check_name string, "
                                 "observed double, threshold double, passed boolean, decision string, reason string")
          .write.mode("append").saveAsTable(f"{fqn}.gov_feed_dq_log"))
    if decision == "REJECTED":
        (spark.createDataFrame([(run_id, source_id, pulled_at, int(n), reason, (quarantine_excerpt or "")[:4000])],
                               "run_id string, source_id string, pulled_at timestamp, n_records int, reason string, payload_excerpt string")
              .write.mode("append").saveAsTable(f"{fqn}.`1_feed_quarantine`"))


def dq_gate(source_id, records, checks=None, *, frozen=False, pulled_at=None, run_id=None,
            note=None, reason_detail=None):
    """Data-quality gate for one pulled batch. Returns (accepted_records, decision_dict).

    records: list of dicts in the 2_event_footprint shape (footprint_wkt, event_id, event_date, ...) or point
    records (latitude/longitude) or text records (title/published_at — geometry checks then don't apply).
    frozen=True marks a frozen-sample fallback (decision FALLBACK_FROZEN if the sample passes; freshness skipped).
    REJECTED → returns [] and quarantines the batch: the caller must keep the previous accepted data."""
    ensure_dq_tables()
    pulled_at = pulled_at or datetime.datetime.utcnow()
    run_id = run_id or f"{source_id}-{pulled_at:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    n = len(records)
    thr = {r.check_name: (r.comparator, r.threshold) for r in
           spark.table(f"{fqn}.ref_dq_threshold").where(F.col("source_id") == source_id).collect()}

    parsed = [_dq_parse_geom(r) for r in records]
    has_geom = any(k != "none" for k, _ in parsed) or any("footprint_wkt" in r for r in records)
    obs = {"record_count": float(n)}
    if has_geom and n:
        null = sum(1 for k, _ in parsed if k == "none")
        bad = sum(1 for k, g in parsed if k != "none" and not _dq_geom_valid(k, g))
        w, so, e, no = EUROPE_UK_BBOX
        outside = 0
        for k, g in parsed:
            if k in ("point", "polygon") and _dq_geom_valid(k, g):
                cx, cy = _dq_centroid(g)
                if not (w <= cx <= e and so <= cy <= no):
                    outside += 1
        obs["pct_null_geometry"] = 100.0 * null / n
        obs["pct_invalid_geometry"] = 100.0 * bad / n
        obs["pct_outside_bbox"] = 100.0 * outside / n
    if n:
        keys = [(r.get("event_id"), r.get("footprint_wkt"), r.get("title"), r.get("url")) for r in records]
        obs["pct_duplicate"] = 100.0 * (n - len(set(keys))) / n
    if n and not frozen and source_id not in _NO_FRESHNESS:
        ts = [t for t in (_dq_rec_time(r) for r in records) if t is not None]
        if ts:
            obs["freshness_hours"] = max(0.0, (pulled_at.replace(tzinfo=None) - max(ts)).total_seconds() / 3600.0)

    results = []
    for ck in (checks or list(_DQ_DEFAULTS)):
        if ck not in obs:
            continue  # not applicable to this batch (e.g. no geometry on a news batch, freshness on a frozen sample)
        comp, th = thr.get(ck, _DQ_DEFAULTS[ck][:2])
        ok = obs[ck] >= th if comp == ">=" else obs[ck] <= th
        results.append((ck, obs[ck], th, ok))
    failed = [(ck, ob, th) for ck, ob, th, ok in results if not ok]

    if failed:
        decision = "REJECTED"
        why = "; ".join(_DQ_FAIL_TEXT[ck](ob, th, n) for ck, ob, th in failed)
        reason = (f"Rejected: {why}" + (f" — {reason_detail}" if reason_detail else "") +
                  f". Batch quarantined; the previous accepted {_SRC_NAME.get(source_id, source_id)} data is still in use.")
    elif frozen:
        decision = "FALLBACK_FROZEN"
        reason = (f"Live pull unavailable{(' (' + note + ')') if note else ''} — frozen sample used; "
                  f"it passed all {len(results)} checks ({n} records).")
    elif n == 0:
        decision = "ACCEPTED"
        reason = ("Accepted: no active warnings in this pull — an empty batch is valid for this feed"
                  + (f" ({note})" if note else "") + ".")
    else:
        decision = "ACCEPTED"
        reason = (f"Accepted: all {len(results)} checks passed ({n} records)" + (f" — {note}" if note else "") + ".")
    excerpt = json.dumps([{k: str(v)[:200] for k, v in r.items()} for r in records[:5]], default=str)
    _dq_log_batch(run_id, source_id, pulled_at, n, results, decision, reason, excerpt)
    print(f"    [dq_gate] {source_id} run={run_id}: {decision} — {reason}")
    return (records if decision != "REJECTED" else []), {
        "run_id": run_id, "source_id": source_id, "decision": decision, "reason": reason,
        "n_records": n, "pulled_at": pulled_at, "failed_checks": [ck for ck, _, _ in failed]}


def dq_unreachable(source_id, note="feed unreachable after retries"):
    """Log a batch for a feed that could not be reached and has no frozen fallback: REJECTED, previous data kept."""
    ensure_dq_tables()
    pulled_at = datetime.datetime.utcnow()
    run_id = f"{source_id}-{pulled_at:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    reason = f"Rejected: {note}. Nothing was replaced; the previous accepted {_SRC_NAME.get(source_id, source_id)} data is still in use."
    _dq_log_batch(run_id, source_id, pulled_at, 0, [("feed_reachable", 0.0, 1.0, False)], "REJECTED", reason, note)
    print(f"    [dq_gate] {source_id} run={run_id}: REJECTED — {reason}")
    return {"run_id": run_id, "source_id": source_id, "decision": "REJECTED", "reason": reason}

# COMMAND ----------

# MAGIC %md ## Bronze landing + idempotent replace into 2_event_footprint

# COMMAND ----------

_RAW_SCHEMA = T.StructType([
    T.StructField("feed", T.StringType()), T.StructField("pulled_at", T.TimestampType()),
    T.StructField("url", T.StringType()), T.StructField("n_records", T.IntegerType()),
    T.StructField("payload_excerpt", T.StringType()),
])

def land_raw(feed, url, n_records, payload_excerpt):
    """Append one raw-pull audit row to 1_hazard_feed_raw (append-only bronze)."""
    row = [(feed, datetime.datetime.utcnow(), url, int(n_records), (payload_excerpt or "")[:4000])]
    (spark.createDataFrame(row, _RAW_SCHEMA)
        .write.mode("append").option("mergeSchema", "true").saveAsTable(f"{fqn}.1_hazard_feed_raw"))
    print(f"    landed raw: {feed} ({n_records} records)")

_FP_COLS = ["event_id", "peril_code", "event_name", "event_date", "footprint_wkt",
            "country_code", "source", "detected_at", "is_live", "source_detail", "ingested_at"]

def ensure_footprint_columns():
    """P1 created 2_event_footprint without live-tracking columns; add them idempotently and backfill."""
    try:
        spark.sql(f"ALTER TABLE {fqn}.`2_event_footprint` ADD COLUMNS "
                  f"(is_live BOOLEAN, source_detail STRING, ingested_at TIMESTAMP)")
    except Exception as e:
        if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
            print(f"    add-columns note: {e}")
    spark.sql(f"UPDATE {fqn}.`2_event_footprint` SET is_live = false WHERE is_live IS NULL")

def replace_events_from_source(source, rows, is_live=True):
    """Idempotently replace all footprints from a given `source` with the freshly pulled `rows`.
    rows: list of dicts with keys event_id, peril_code, event_name, event_date(date), footprint_wkt,
    country_code, source_detail. detected_at/ingested_at set to now; source stamped. `is_live` marks a
    true live pull (True) vs a frozen-real-sample fallback used when a credential is missing (False)."""
    assert source in SOURCES
    ensure_footprint_columns()
    spark.sql(f"DELETE FROM {fqn}.`2_event_footprint` WHERE source = '{source}'")
    if not rows:
        print(f"    {source}: no rows this pull (nothing georeferenced)")
        return 0
    now = datetime.datetime.utcnow()
    recs = [(r["event_id"], r["peril_code"], r["event_name"], r["event_date"], r["footprint_wkt"],
             r["country_code"], source, now, bool(is_live), r.get("source_detail", ""), now) for r in rows]
    df = spark.createDataFrame(recs, ", ".join(
        ["event_id string", "peril_code string", "event_name string", "event_date date",
         "footprint_wkt string", "country_code string", "source string", "detected_at timestamp",
         "is_live boolean", "source_detail string", "ingested_at timestamp"]))
    df.write.mode("append").option("mergeSchema", "true").saveAsTable(f"{fqn}.`2_event_footprint`")
    n_events = len({r["event_id"] for r in rows})
    print(f"    {source}: landed {len(rows)} footprint rows across {n_events} live event(s)")
    return len(rows)

print("feeds_common helpers ready (incl. dq_gate):", fqn)
