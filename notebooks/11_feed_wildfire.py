# Databricks notebook source
# MAGIC %md
# MAGIC # 11 · Wildfire adapter — NASA FIRMS (active fire) + EFFIS (Europe)
# MAGIC
# MAGIC **NASA FIRMS** active-fire detections (VIIRS 375 m, near-real-time) via the area-CSV API. FIRMS needs a
# MAGIC **free MAP_KEY** — this is exactly the feed the customer's analyst was rate-limited on, so the fetch uses the
# MAGIC shared backoff/retry helper and a **daily batch** (well under FIRMS' ~10-req/min limit). Hotspots arrive as
# MAGIC points → clustered into a footprint polygon.
# MAGIC
# MAGIC **Credential:** set secret `MAP_KEY` in scope `exposure_response` (or env `FIRMS_MAP_KEY`). Without it, this
# MAGIC adapter falls back to a small **frozen real-shape sample** over the SE-France / Var wildfire zone (labelled
# MAGIC `is_live=false`) so the pipeline never crashes and the demo still has a fire event. Get a key (instant, free):
# MAGIC https://firms.modaps.eosdis.nasa.gov/api/map_key/  — attribution: *NASA FIRMS / EOSDIS LANCE*.

# COMMAND ----------

# MAGIC %run ./10_feeds_common

# COMMAND ----------

import os, io, csv, datetime

def get_map_key():
    try:
        return dbutils.secrets.get(scope="exposure_response", key="MAP_KEY")
    except Exception:
        return os.environ.get("FIRMS_MAP_KEY", "").strip()

# southern-Europe bbox (west,south,east,north) — France/Italy/Iberia Mediterranean fire belt
BBOX = "-9,36,19,48"
SRC = "VIIRS_SNPP_NRT"
today = datetime.date.today()

# COMMAND ----------

map_key = get_map_key()
rows = []
is_live = False

if map_key:
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{map_key}/{SRC}/{BBOX}/2"
    txt = robust_get(url, timeout=45, expect="text")
    if txt and "latitude" in txt.lower():
        rdr = list(csv.DictReader(io.StringIO(txt)))
        land_raw("FIRMS", url.replace(map_key, "***"), len(rdr), txt[:2000])
        # keep higher-confidence hotspots; cluster all southern-France points into one footprint
        pts = [(float(r["longitude"]), float(r["latitude"])) for r in rdr
               if str(r.get("confidence", "")).lower() in ("n", "h", "nominal", "high") or r.get("confidence", "").isdigit()]
        fr_pts = [(x, y) for (x, y) in pts if 3.0 <= x <= 8.0 and 42.5 <= y <= 45.0]
        wkt = points_to_footprint_wkt(fr_pts, buffer_deg=0.03)
        if wkt:
            rows = [{"event_id": f"EVT_LIVE_FIRMS_FR_{today:%Y%m%d}", "peril_code": "FIRE",
                     "event_name": f"FIRMS active fire (SE France, {len(fr_pts)} hotspots)",
                     "event_date": today, "footprint_wkt": wkt, "country_code": "FR",
                     "source_detail": f"NASA FIRMS {SRC} live; {len(fr_pts)} VIIRS hotspots clustered"}]
            is_live = True
        print(f"FIRMS live: {len(rdr)} detections, {len(fr_pts)} in SE-France cluster")
    else:
        print("FIRMS live pull returned no usable data — falling back to frozen sample")
else:
    print("No FIRMS MAP_KEY — using frozen real-shape sample (set secret exposure_response/MAP_KEY to go live)")

# COMMAND ----------

# MAGIC %md ## Fallback: frozen real-shape sample over the Var wildfire zone (labelled is_live=false)

# COMMAND ----------

if not rows:
    # a compact footprint over the SE-France/Var fire belt (same zone as the synthetic seed but a distinct FIRMS-shaped event)
    frozen = ("POLYGON((6.60 43.55, 6.95 43.55, 6.95 43.72, 6.60 43.72, 6.60 43.55))")
    rows = [{"event_id": f"EVT_FIRMS_SAMPLE_FR", "peril_code": "FIRE",
             "event_name": "FIRMS active fire — SE France (frozen sample)",
             "event_date": today - datetime.timedelta(days=1), "footprint_wkt": frozen, "country_code": "FR",
             "source_detail": "Frozen FIRMS-shaped sample over the Var fire belt; set MAP_KEY to ingest live VIIRS hotspots"}]
    land_raw("FIRMS", "frozen-sample", len(rows), "no MAP_KEY")

n = replace_events_from_source("FIRMS", rows, is_live=is_live)
print(f"wildfire adapter: {n} row(s), live={is_live}")
