# Databricks notebook source
# MAGIC %md
# MAGIC # 11b · Wildfire adapter — Copernicus EFFIS Burnt Areas (perimeters)
# MAGIC
# MAGIC Complements the FIRMS adapter (`11_feed_wildfire`). Where **FIRMS** gives near-real-time active-fire
# MAGIC *hotspots* (points, clustered into a rough footprint), **EFFIS Burnt Areas** gives the authoritative
# MAGIC *burnt-area perimeters* (polygons) — this is the dataset Hiscox's exposure team actually uses. Both land
# MAGIC in the same `2_event_footprint` shape (WKT, EPSG:4326) and flow through the same governed functions, so an
# MAGIC EFFIS burnt-area event overlays the portfolio exactly like any other event.
# MAGIC
# MAGIC **Source:** Copernicus EFFIS / GWIS WFS (keyless), current burnt-areas layer, requested as GeoJSON via the
# MAGIC shared backoff/retry fetch. If the endpoint is unreachable/blocked/empty the adapter falls back to a
# MAGIC **frozen real-shape burnt-area perimeter** over the SE-France / Var belt (`is_live=false`) so the job never
# MAGIC crashes. **Attribution:** *Copernicus Emergency Management Service — EFFIS / GWIS*. No credential required.

# COMMAND ----------

# MAGIC %run ./10_feeds_common

# COMMAND ----------

import datetime

today = datetime.date.today()

# European fire belt bbox (lon/lat) — FR/IT/AT/ES/DE + Mediterranean
EU_BBOX = (-10.0, 35.0, 20.0, 55.0)  # west, south, east, north
MAX_EVENTS = 15          # cap ingested perimeters so the layer never sprawls
MAX_WKT_CHARS = 60000    # skip pathologically complex perimeters (keeps the demo snappy)

# EFFIS/GWIS WFS is a MapServer — try a few base/layer/format combinations and take the
# first that returns a GeoJSON FeatureCollection with polygon features.
_BASES = [
    "https://maps.effis.emergency.copernicus.eu/gwis",
    "https://maps.effis.emergency.copernicus.eu/effis",
]
_LAYERS = ["ms:modis.ba", "modis.ba", "ms:effis.ba.poly", "ms:ba.poly"]
_FORMATS = ["application/json", "geojson"]


def _wfs_url(base, layer, fmt):
    return (f"{base}?service=WFS&version=2.0.0&request=GetFeature"
            f"&typeNames={layer}&outputFormat={fmt}&count=500&srsName=EPSG:4326")


def _centroid(geom):
    """Rough centroid (mean of exterior-ring vertices) for bbox / country classification."""
    t, c = geom["type"], geom["coordinates"]
    ring = c[0] if t == "Polygon" else c[0][0]  # first ring of first polygon
    xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


# crude centroid→country classifier (bbox, best-effort) for the five-country book
_CC = [
    ("ES", -9.5, 35.9, 3.4, 43.8), ("FR", -5.2, 42.3, 8.3, 51.1),
    ("DE", 5.8, 47.2, 15.1, 55.1), ("AT", 9.5, 46.3, 17.2, 49.1),
    ("IT", 6.6, 36.6, 18.6, 47.1),
]
def _country(lon, lat):
    for cc, w, s, e, n in _CC:
        if w <= lon <= e and s <= lat <= n:
            return cc
    return "FR"


def _feature_date(props):
    for k in ("FIREDATE", "firedate", "LASTUPDATE", "lastupdate", "DATE", "date", "initialdate", "INITIALDATE"):
        v = props.get(k)
        if v:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y"):
                try:
                    return datetime.datetime.strptime(str(v)[:19], fmt).date()
                except ValueError:
                    continue
    return None


def _area_ha(props):
    for k in ("AREA_HA", "area_ha", "AREA", "area", "hectares"):
        v = props.get(k)
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None

# COMMAND ----------

rows = []
is_live = False
tried = []

fc = None
for base in _BASES:
    for layer in _LAYERS:
        for fmt in _FORMATS:
            url = _wfs_url(base, layer, fmt)
            tried.append(f"{base}|{layer}|{fmt}")
            g = robust_get(url, timeout=60, retries=2, expect="json")
            if isinstance(g, dict) and g.get("type") == "FeatureCollection" and g.get("features"):
                fc = g
                land_raw("EFFIS", url, len(g["features"]), json.dumps(g.get("features", [])[:1])[:2000])
                print(f"EFFIS live: {len(g['features'])} features from {layer} @ {base}")
                break
        if fc:
            break
    if fc:
        break

if fc:
    w, s, e, n = EU_BBOX
    cand = []
    for feat in fc["features"]:
        geom = feat.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            wkt = geojson_to_wkt(geom)
        except Exception:
            continue
        if len(wkt) > MAX_WKT_CHARS:
            continue
        lon, lat = _centroid(geom)
        if not (w <= lon <= e and s <= lat <= n):
            continue
        props = feat.get("properties") or {}
        fdate = _feature_date(props) or today
        cand.append((fdate, _area_ha(props), wkt, lon, lat, props, feat.get("id")))
    # most-recent first, then largest; cap
    cand.sort(key=lambda r: (r[0], r[1] or 0.0), reverse=True)
    for i, (fdate, area, wkt, lon, lat, props, fid) in enumerate(cand[:MAX_EVENTS]):
        cc = (props.get("COUNTRY") or props.get("country") or _country(lon, lat))[:2].upper()
        place = props.get("COMMUNE") or props.get("PROVINCE") or props.get("NAME") or props.get("place") or ""
        area_txt = f"{area:,.0f} ha" if area else "area n/a"
        rows.append({
            "event_id": f"EVT_EFFIS_{(str(fid).split('.')[-1] if fid else i)}",
            "peril_code": "FIRE",
            "event_name": f"EFFIS burnt area{(' — ' + str(place)) if place else ''} ({area_txt})",
            "event_date": fdate,
            "footprint_wkt": wkt,
            "country_code": cc if cc in ("FR", "IT", "AT", "ES", "DE") else _country(lon, lat),
            "source_detail": f"Copernicus EFFIS burnt-area perimeter; {area_txt}; fire date {fdate}",
        })
    is_live = bool(rows)
    print(f"EFFIS: {len(cand)} EU polygons in window, kept {len(rows)} (cap {MAX_EVENTS})")
else:
    print(f"EFFIS live pull failed on all {len(tried)} endpoint combinations — falling back to frozen sample")

# COMMAND ----------

# MAGIC %md ## Fallback: frozen real-shape burnt-area perimeter over the Var belt (is_live=false)

# COMMAND ----------

if not rows:
    # An irregular burnt-area *perimeter* (distinct from the FIRMS bbox footprint) over the SE-France / Var belt.
    frozen = ("POLYGON((6.62 43.57, 6.71 43.55, 6.83 43.58, 6.90 43.63, 6.88 43.70, "
              "6.79 43.73, 6.69 43.71, 6.63 43.66, 6.62 43.57))")
    # Dated with FIRMS (1 day back): burnt areas are mapped AFTER the fire, so the frozen news item
    # (FIRMS date - 14 h) stays ahead of every structured feed and keeps beats_feed TRUE after a reset.
    rows = [{"event_id": "EVT_EFFIS_SAMPLE_FR", "peril_code": "FIRE",
             "event_name": "EFFIS burnt area — Var, SE France (frozen sample, ~4,200 ha)",
             "event_date": today - datetime.timedelta(days=1), "footprint_wkt": frozen, "country_code": "FR",
             "source_detail": "Frozen EFFIS-shaped burnt-area perimeter over the Var belt; live EFFIS/GWIS WFS "
                              "was unreachable this run (Copernicus EMS — EFFIS/GWIS)"}]
    land_raw("EFFIS", "frozen-sample", len(rows), f"tried={len(tried)} endpoints")

n = replace_events_from_source("EFFIS", rows, is_live=is_live)
print(f"EFFIS burnt-area adapter: {n} row(s), live={is_live}")
