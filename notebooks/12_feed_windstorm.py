# Databricks notebook source
# MAGIC %md
# MAGIC # 12 · Windstorm / severe-weather adapter — **MeteoAlarm (LIVE, keyless)**
# MAGIC
# MAGIC The live differentiator. MeteoAlarm aggregates the official warnings of 38 European national met services
# MAGIC (Météo-France, DWD, ZAMG, AEMET, …) as CAP-JSON — **no API key**. We pull the current warnings, map each
# MAGIC warned region to a real boundary polygon, and land them into `2_event_footprint` as live events that flow
# MAGIC through the **same** `fn_exposure_in_footprint` as the frozen demo events.
# MAGIC
# MAGIC **France** warns by **NUTS3** code → joined to Eurostat GISCO NUTS3 (2013 vintage) boundaries: fully real
# MAGIC footprints. Other countries warn by **EMMA_ID** (MeteoAlarm's own regions) — those raw pulls are landed to
# MAGIC bronze; wiring the EMMA→boundary lookup is the documented next step (see `docs/FEEDS.md`).
# MAGIC
# MAGIC Peril mapping (CAP `awareness_type`): 1 Wind / 3 Thunderstorm / 7 Coastal → STORM · 8 Forest-fire → FIRE ·
# MAGIC 10 Rain / 11 Flood / 12 Rain-Flood → FLOOD.

# COMMAND ----------

# MAGIC %run ./10_feeds_common

# COMMAND ----------

import datetime

FEEDS = {  # MeteoAlarm keyless country feeds
    "france": "https://feeds.meteoalarm.org/api/v1/warnings/feeds-france",
    "italy": "https://feeds.meteoalarm.org/api/v1/warnings/feeds-italy",
    "austria": "https://feeds.meteoalarm.org/api/v1/warnings/feeds-austria",
    "spain": "https://feeds.meteoalarm.org/api/v1/warnings/feeds-spain",
    "germany": "https://feeds.meteoalarm.org/api/v1/warnings/feeds-germany",
}

def peril_of(atype):
    n = str(atype).split(";")[0].strip()
    return {"1": "STORM", "3": "STORM", "7": "STORM", "8": "FIRE",
            "10": "FLOOD", "11": "FLOOD", "12": "FLOOD"}.get(n, "STORM")

# COMMAND ----------

# MAGIC %md ## Pull all five feeds (bronze) + build live FR footprints from NUTS3 boundaries

# COMMAND ----------

nuts_fr = load_nuts3_wkt(year=2013, res="20M", country="FR")
print(f"NUTS3-FR boundaries: {len(nuts_fr)} regions")

today = datetime.date.today()
groups = {}  # peril -> {codes:set, level:int, label:str}
total_warnings = 0

for country, url in FEEDS.items():
    d = robust_get(url, timeout=30, expect="json")
    if not d or "warnings" not in d:
        print(f"  {country}: no data this pull"); continue
    ws = d["warnings"]; total_warnings += len(ws)
    land_raw("METEOALARM", url, len(ws), None)  # audit every pull
    if country != "france":
        continue  # EMMA_ID regions — landed to bronze; boundary lookup is the documented next step
    for w in ws:
        for info in w.get("alert", {}).get("info", []):
            params = {p["valueName"]: p["value"] for p in info.get("parameter", [])}
            peril = peril_of(params.get("awareness_type", "1"))
            try:
                lvl = int(params.get("awareness_level", "1;").split(";")[0] or 1)
            except ValueError:
                lvl = 1
            codes = [g["value"] for a in info.get("area", []) for g in a.get("geocode", [])
                     if g["valueName"] == "NUTS3" and g["value"] in nuts_fr]
            if not codes:
                continue
            grp = groups.setdefault(peril, {"codes": set(), "level": 0, "label": info.get("event", "")})
            grp["codes"].update(codes)
            if lvl > grp["level"]:
                grp["level"] = lvl; grp["label"] = info.get("event", "")

print(f"pulled {total_warnings} warnings across {len(FEEDS)} countries; FR live perils: "
      f"{ {p: len(g['codes']) for p, g in groups.items()} }")

# COMMAND ----------

# MAGIC %md ## Normalise to footprints (one row per warned region, shared event_id) + idempotent replace

# COMMAND ----------

rows = []
ymd = today.strftime("%Y%m%d")
for peril, grp in groups.items():
    eid = f"EVT_LIVE_MA_FR_{peril}_{ymd}"
    ename = f"MeteoAlarm FR {peril} ({grp['label']})"
    for code in sorted(grp["codes"]):
        rows.append({
            "event_id": eid, "peril_code": peril, "event_name": ename, "event_date": today,
            "footprint_wkt": nuts_fr[code], "country_code": "FR",
            "source_detail": f"Meteo-France via MeteoAlarm (keyless); region {code}; boundary GISCO NUTS3 2013 20M",
        })

n = replace_events_from_source("METEOALARM", rows)
print(f"MeteoAlarm live ingest complete: {n} footprint rows landed at {datetime.datetime.utcnow().isoformat()}Z")
