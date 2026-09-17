# Databricks notebook source
# MAGIC %md
# MAGIC # 10 · Feed ingestion — shared helpers (the "we fix your API-timeout pain" layer)
# MAGIC
# MAGIC The customer's analyst hit **timeouts / blocking** pulling hazard feeds by hand. This layer is the answer:
# MAGIC one robust fetch (timeout + exponential backoff + retry), one geometry-normalisation step
# MAGIC (points / regions → WKT polygon in the `2_event_footprint` shape), a bronze landing of every raw pull
# MAGIC (`1_hazard_feed_raw`), and one idempotent replace into `2_event_footprint` tagged by `source` + `is_live`.
# MAGIC
# MAGIC The peril adapters (`11_feed_wildfire`, `12_feed_windstorm`, `13_feed_flood`) `%run` this notebook, then
# MAGIC call these helpers. Live where a feed is keyless (MeteoAlarm), frozen-real-sample where a credential is
# MAGIC required (FIRMS MAP_KEY, Copernicus CDS key) — see `docs/FEEDS.md`. Nothing here needs `ST_*` (footprints
# MAGIC are stored as WKT text; the governed functions do the geospatial maths on the warehouse).

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
fqn = f"{catalog}.{schema}"

import json, time, urllib.request, urllib.error, datetime
from pyspark.sql import functions as F, types as T

UA = {"User-Agent": "bricksurance-exposure-demo/1.0 (contact laurence.ryszka@databricks.com)"}
SOURCES = ("FIRMS", "EFFIS", "METEOALARM", "GLOFAS", "EMS", "SYNTHETIC")

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

print("feeds_common helpers ready:", fqn)
