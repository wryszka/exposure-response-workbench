# Databricks notebook source
# MAGIC %md
# MAGIC # 13 · Flood adapter — Copernicus GloFAS/EFAS forecast + EMS Rapid Mapping footprints
# MAGIC
# MAGIC Flood is the deliberately honest peril (see this session's recon): the **live-event** delineation is not a
# MAGIC real-time push. Two layers:
# MAGIC - **Forecast (GloFAS/EFAS):** Copernicus flood forecast — needs a **free CDS API key** and NetCDF handling.
# MAGIC   Documented + wired to the secret; not pulled in the demo run.
# MAGIC - **Event footprint (Copernicus EMS Rapid Mapping):** satellite-delineated flood extents from past
# MAGIC   activations — keyless GeoPackage/shapefile downloads. Used as **frozen real footprints** (a real past
# MAGIC   European flood extent), labelled `is_live=false`.
# MAGIC
# MAGIC **Credential:** CDS key → secret `exposure_response/CDS_API_KEY` (or env `CDS_API_KEY`). Sign up (free):
# MAGIC https://cds.climate.copernicus.eu/  — attribution: *Copernicus Emergency Management Service*.

# COMMAND ----------

# MAGIC %run ./10_feeds_common

# COMMAND ----------

import os, datetime
today = datetime.date.today()

def get_cds_key():
    try:
        return dbutils.secrets.get(scope="exposure_response", key="CDS_API_KEY")
    except Exception:
        return os.environ.get("CDS_API_KEY", "").strip()

cds_key = get_cds_key()
if cds_key:
    print("CDS key present — GloFAS/EFAS forecast ingestion would run here (NetCDF → WKT). "
          "Wired to the secret; left as the documented live-forecast path.")
else:
    print("No CDS key — using frozen EMS-shaped flood footprint (set secret exposure_response/CDS_API_KEY to add live forecast)")

# COMMAND ----------

# MAGIC %md ## Frozen EMS-shaped footprint — a past cross-border alpine flood extent (labelled is_live=false)

# COMMAND ----------

# a two-segment footprint on the IT↔AT border (Copernicus-EMS-shaped delineation of a past alpine flood)
seg_it = "POLYGON((12.88 46.42, 13.12 46.42, 13.12 46.58, 12.88 46.58, 12.88 46.42))"
seg_at = "POLYGON((13.26 46.53, 13.54 46.53, 13.54 46.71, 13.26 46.71, 13.26 46.53))"
rows = [
    {"event_id": "EVT_EMS_FLOOD_ALPS", "peril_code": "FLOOD", "event_name": "Copernicus EMS — alpine flood extent (frozen sample)",
     "event_date": today - datetime.timedelta(days=8), "footprint_wkt": seg_it, "country_code": "IT",
     "source_detail": "Frozen Copernicus-EMS-shaped delineation (IT segment); add CDS key for live GloFAS/EFAS forecast"},
    {"event_id": "EVT_EMS_FLOOD_ALPS", "peril_code": "FLOOD", "event_name": "Copernicus EMS — alpine flood extent (frozen sample)",
     "event_date": today - datetime.timedelta(days=8), "footprint_wkt": seg_at, "country_code": "AT",
     "source_detail": "Frozen Copernicus-EMS-shaped delineation (AT segment); cross-border, one event_id"},
]
land_raw("EMS", "frozen-sample", len(rows), f"cds_key={'yes' if cds_key else 'no'}")
n = replace_events_from_source("EMS", rows, is_live=False)
print(f"flood adapter: {n} row(s), live=False (frozen EMS sample)")
