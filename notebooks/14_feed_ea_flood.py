# Databricks notebook source
# MAGIC %md
# MAGIC # 14 · UK flood adapter — **Environment Agency flood warnings (LIVE, keyless)**
# MAGIC
# MAGIC The Environment Agency publishes every current flood warning in England as open data — no key. We pull the
# MAGIC current warnings, fetch each warning's real **flood-area polygon**, and land them in `2_event_footprint`
# MAGIC (`source='EA'`, peril FLOOD, country GB). From there they flow into the **published active zones**
# MAGIC (`mv_active_zones` / `fn_zone_check`, notebook 51), which the **underwriting workbench reads at quote/bind**:
# MAGIC a property inside an active warning area is referred instead of bound.
# MAGIC
# MAGIC | EA severity | Meaning | Ingested |
# MAGIC |---|---|---|
# MAGIC | 1 | Severe Flood Warning — danger to life | yes |
# MAGIC | 2 | Flood Warning — flooding expected | yes |
# MAGIC | 3 | Flood Alert — flooding possible | yes |
# MAGIC | 4 | Warning no longer in force | no (not an active zone) |
# MAGIC
# MAGIC **Frozen hero (always present, `is_live=false`):** `EVT_EA_SAMPLE_UK` — a Flood Warning on EA flood area
# MAGIC `123FWF312`, *River Calder and Hebden Water at Hebden Bridge*, using that area's **real EA polygon**. It
# MAGIC contains the postcode-district centroid of the underwriting workbench's hero `sub:900002` (Calder Valley
# MAGIC Fine Foods, Hebden Bridge Mill, HX7), so the referral story works whatever the weather is on the day. If EA
# MAGIC issues a real warning for the same area, the live one replaces the sample.
# MAGIC
# MAGIC Large alert-area polygons (thousands of vertices) are thinned to <=400 vertices per ring for display and
# MAGIC the zone test — immaterial at warning-area scale. Contains Environment Agency data © Crown copyright and
# MAGIC database right, licensed under the Open Government Licence v3.0.

# COMMAND ----------

# MAGIC %run ./10_feeds_common

# COMMAND ----------

dbutils.widgets.text("max_warnings", "15")
MAX_WARNINGS = int(dbutils.widgets.get("max_warnings"))

# 10_feeds_common's replace helper only accepts known sources; register EA here (T1 may also add it there).


EA_BASE = "https://environment.data.gov.uk/flood-monitoring"
ATTRIB = "Contains Environment Agency data © Crown copyright, Open Government Licence v3.0"
ACTIVE_LEVELS = {1: "Severe Flood Warning", 2: "Flood Warning", 3: "Flood Alert"}

HERO_AREA_ID = "123FWF312"
HERO_AREA = "River Calder and Hebden Water at Hebden Bridge"
HERO_WKT = "POLYGON((-2.01384 53.74267, -2.01412 53.74245, -2.01391 53.74226, -2.01394 53.74207, -2.0147 53.74173, -2.01426 53.74152, -2.01431 53.74141, -2.01431 53.74135, -2.01465 53.74122, -2.01491 53.74107, -2.01532 53.7408, -2.0154 53.74069, -2.01534 53.74061, -2.01517 53.74054, -2.01477 53.74026, -2.01432 53.74025, -2.014 53.74026, -2.01282 53.74022, -2.01168 53.74018, -2.01079 53.74013, -2.00944 53.73978, -2.00904 53.73884, -2.00921 53.73866, -2.00886 53.73854, -2.00826 53.73816, -2.00774 53.73798, -2.00706 53.73773, -2.00645 53.73814, -2.00683 53.7383, -2.00756 53.73872, -2.00866 53.73946, -2.00897 53.73974, -2.00886 53.73981, -2.00909 53.73995, -2.00898 53.74002, -2.00941 53.7403, -2.00965 53.74034, -2.00991 53.74034, -2.01011 53.74031, -2.01032 53.74065, -2.01042 53.74091, -2.01058 53.74112, -2.01079 53.74129, -2.01117 53.74152, -2.01146 53.74177, -2.01165 53.74193, -2.01171 53.74218, -2.0117 53.74243, -2.0117 53.74269, -2.01171 53.74283, -2.0117 53.743, -2.01177 53.7429, -2.01202 53.74271, -2.01208 53.74257, -2.01223 53.74246, -2.01237 53.74252, -2.0125 53.74246, -2.01368 53.74285, -2.01384 53.74267))"


def _thin(ring, cap=400):
    """Keep every k-th vertex so a ring has <= cap vertices; keep the ring closed."""
    if len(ring) <= cap:
        return ring
    k = -(-len(ring) // cap)
    out = ring[::k]
    if out[-1] != ring[-1]:
        out.append(ring[-1])
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def _thin_geom(g):
    if g["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [_thin(r) for r in g["coordinates"]]}
    if g["type"] == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": [[_thin(r) for r in p] for p in g["coordinates"]]}
    raise ValueError(g["type"])

# COMMAND ----------
# MAGIC %md ## Pull current warnings (bronze) → real flood-area polygons

# COMMAND ----------

now = datetime.datetime.utcnow()
url = f"{EA_BASE}/id/floods"
data = robust_get(url, expect="json")
items = (data or {}).get("items", []) if isinstance(data, dict) else []
land_raw("EA", url, len(items), json.dumps(items[:5])[:4000] if items else "no response / no items")

active = [i for i in items if int(i.get("severityLevel") or 9) in ACTIVE_LEVELS]
active.sort(key=lambda i: str(i.get("timeRaised") or ""), reverse=True)   # newest first...
active.sort(key=lambda i: int(i.get("severityLevel") or 9))                 # ...then most severe first (stable)
active = active[:MAX_WARNINGS]
print(f"EA: {len(items)} warnings published, {len(active)} active (severity 1-3) taken")

records = []
for i in active:
    fa = i.get("floodArea") or {}
    area_id = i.get("floodAreaID") or fa.get("notation")
    poly_url = fa.get("polygon") or f"{EA_BASE}/id/floodAreas/{area_id}/polygon"
    gj = robust_get(poly_url.replace("http://", "https://"), expect="json", retries=3)
    feats = (gj or {}).get("features") or []
    if not feats:
        print(f"    no polygon for {area_id} — skipped")
        continue
    try:
        wkt = geojson_to_wkt(_thin_geom(feats[0]["geometry"]))
    except Exception as e:  # noqa: BLE001 — one bad geometry must not stop the pull
        print(f"    bad geometry {area_id}: {e}")
        continue
    level = int(i.get("severityLevel"))
    sev = ACTIVE_LEVELS[level]
    raised = str(i.get("timeRaised") or i.get("timeSeverityChanged") or now.isoformat())[:19]
    try:
        raised_ts = datetime.datetime.strptime(raised, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raised_ts = now
    area = (i.get("description") or fa.get("label") or area_id).strip()
    records.append({
        "event_id": f"EVT_EA_{area_id}", "peril_code": "FLOOD",
        "event_name": f"EA {sev} — {area}", "event_date": raised_ts.date(), "footprint_wkt": wkt,
        "country_code": "GB", "detected_at": raised_ts, "is_live": True,
        "source_detail": f"EA {sev} (severity {level}) · area: {area} · {area_id} · "
                         f"{i.get('eaAreaName') or ''} · issued {raised_ts:%Y-%m-%d %H:%M} UTC · {ATTRIB}",
        "lat": None, "lon": None,
    })

# COMMAND ----------
# MAGIC %md ## Data-quality gate (if available) + the frozen hero

# COMMAND ----------

live_rejected = False
if "dq_gate" in globals():
    records, decision = dq_gate("EA", records)
    live_rejected = decision.get("decision") == "REJECTED"
    print("dq_gate:", decision)
else:
    print("dq_gate not defined yet — passing EA records through")

live_area_ids = {r["event_id"] for r in records}
if f"EVT_EA_{HERO_AREA_ID}" not in live_area_ids:
    issued = now - datetime.timedelta(hours=2)
    hero = [{
        "event_id": "EVT_EA_SAMPLE_UK", "peril_code": "FLOOD",
        "event_name": f"EA Flood Warning — {HERO_AREA} (frozen sample)", "event_date": issued.date(),
        "footprint_wkt": HERO_WKT, "country_code": "GB", "detected_at": issued, "is_live": False,
        "source_detail": f"EA Flood Warning (severity 2) · area: {HERO_AREA} · {HERO_AREA_ID} · Yorkshire · "
                         f"issued {issued:%Y-%m-%d %H:%M} UTC · frozen sample on the real EA flood-area polygon · {ATTRIB}",
    }]
    if "dq_gate" in globals():   # the frozen sample is gated + logged too (FALLBACK_FROZEN)
        hero, _ = dq_gate("EA", hero, frozen=True)
    records.extend(hero)
    print("frozen hero EVT_EA_SAMPLE_UK added (Hebden Bridge)")
else:
    print("live warning already covers the hero area — no frozen sample needed")

# COMMAND ----------
# MAGIC %md ## Idempotent replace of all EA rows (live + frozen together)

# COMMAND ----------

ensure_footprint_columns()
if live_rejected:
    # A rejected live batch never replaces good data: keep the previous EA rows exactly as they are.
    dbutils.notebook.exit("EA live batch REJECTED by the data-quality gate — previous EA data kept")
spark.sql(f"DELETE FROM {fqn}.`2_event_footprint` WHERE source = 'EA'")
recs = [(r["event_id"], r["peril_code"], r["event_name"], r["event_date"], r["footprint_wkt"], r["country_code"],
         "EA", r["detected_at"], bool(r["is_live"]), r["source_detail"], now) for r in records]
if recs:
    df = spark.createDataFrame(recs, ", ".join(
        ["event_id string", "peril_code string", "event_name string", "event_date date",
         "footprint_wkt string", "country_code string", "source string", "detected_at timestamp",
         "is_live boolean", "source_detail string", "ingested_at timestamp"]))
    df.write.mode("append").option("mergeSchema", "true").saveAsTable(f"{fqn}.`2_event_footprint`")
n_live = sum(1 for r in records if r["is_live"])
print(f"EA landed: {len(records)} rows ({n_live} live, {len(records) - n_live} frozen)")
display(spark.sql(f"SELECT event_id, event_name, is_live, detected_at FROM {fqn}.`2_event_footprint` "
                  f"WHERE source='EA' ORDER BY is_live DESC, detected_at DESC"))
