# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Property foundation + frozen event footprints — Bricksurance SE (European book)
# MAGIC
# MAGIC Deterministic (seed=42) synthetic **European property book** — the anchor for exposure and event
# MAGIC response. ~5,000 insured objects across FR/IT/AT/ES/DE, geocoded (lat/long), clustered around real
# MAGIC cities, plus a dense **wildfire exposure cluster** (SE France / Var) so live-event beats have threatened
# MAGIC properties, and a **cross-border alpine cluster** (N Italy ↔ Austria) so one event crossing a border is real.
# MAGIC
# MAGIC Columns mirror `bricksurance-data-core` `policy.insured_object` (insured_object_id, policy_id,
# MAGIC insured_object_type_code, description, country_code, postcode, latitude/longitude decimal(9,6),
# MAGIC location_key) + property-book extensions (sum_insured, coverage_type, line_of_business, construction, occupancy).
# MAGIC
# MAGIC Also seeds `2_event_footprint`: 2–3 **frozen synthetic** hazard footprints (WKT polygons) for wildfire,
# MAGIC flood (two segments under one event_id — the cross-border seed) and windstorm. These are the frozen
# MAGIC fallback; P2 layers the live FIRMS/EFFIS/MeteoAlarm/GloFAS feeds over the same table shape.
# MAGIC
# MAGIC Geospatial note: coordinates are WKT `POINT(lon lat)` / `POLYGON((lon lat, ...))` in EPSG:4326. Distance
# MAGIC math reprojects to **EPSG:3035** (LAEA Europe) so `ST_DWithin(...50/100/200)` bands are true metres.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("seed", "42")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
SEED = int(dbutils.widgets.get("seed"))
fqn = f"{catalog}.{schema}"

import random, datetime, math
from pyspark.sql import functions as F

rng = random.Random(SEED)
TODAY = datetime.date.today()
print(f"target = {fqn}  seed={SEED}  as_at={TODAY}")

# COMMAND ----------

# MAGIC %md ## Property clusters
# MAGIC Each cluster: (city, country, centre_lat, centre_lon, n_properties, scatter_deg). The Var wildfire cluster
# MAGIC and the two cross-border clusters are deliberately placed and tightly scattered so events intersect the book.

# COMMAND ----------

# city, country, lat, lon, n, scatter(deg ~ 0.01 ≈ 1.1km lat)
CLUSTERS = [
    # France
    ("Paris",      "FR", 48.8566,  2.3522, 900, 0.10),
    ("Lyon",       "FR", 45.7640,  4.8357, 350, 0.06),
    ("Nice",       "FR", 43.7000,  7.2650, 300, 0.05),
    ("Marseille",  "FR", 43.2965,  5.3698, 300, 0.06),
    ("Var (wildfire zone)", "FR", 43.6000, 6.7525, 90, 0.006),  # DENSE — sits on the wildfire footprint
    # Italy
    ("Milan",      "IT", 45.4642,  9.1900, 450, 0.07),
    ("Rome",       "IT", 41.9028, 12.4964, 400, 0.08),
    ("Turin",      "IT", 45.0703,  7.6869, 250, 0.05),
    ("Friuli (border)", "IT", 46.5000, 13.0000, 70, 0.03),   # cross-border seed (IT side)
    # Austria
    ("Vienna",     "AT", 48.2082, 16.3738, 300, 0.06),
    ("Innsbruck",  "AT", 47.2692, 11.3933, 180, 0.04),
    ("Carinthia (border)", "AT", 46.6200, 13.4000, 70, 0.05), # cross-border seed (AT side)
    # Spain
    ("Madrid",     "ES", 40.4168, -3.7038, 450, 0.08),
    ("Barcelona",  "ES", 41.3874,  2.1686, 350, 0.06),
    # Germany
    ("Munich",     "DE", 48.1351, 11.5820, 350, 0.06),
    ("Berlin",     "DE", 52.5200, 13.4050, 350, 0.08),
    ("Hamburg",    "DE", 53.5511,  9.9937, 260, 0.06),  # under the N-German windstorm swath
]

LOB = ["COMMERCIAL_PROPERTY", "HOMEOWNERS", "HIGH_VALUE_HOME"]
LOB_W = [0.45, 0.40, 0.15]
COV = ["PROPERTY_DAMAGE", "CONTENTS", "BUSINESS_INTERRUPTION"]
OBJ = ["BUILDING", "CONTENTS", "BUSINESS_INTERRUPTION"]
CONSTRUCTION = ["MASONRY", "TIMBER_FRAME", "REINFORCED_CONCRETE", "STEEL_FRAME"]
OCCUPANCY = ["RESIDENTIAL", "OFFICE", "RETAIL", "HOTEL", "LIGHT_INDUSTRIAL"]
# indicative sum-insured ranges (EUR) by line of business
SI_RANGE = {
    "COMMERCIAL_PROPERTY": (400_000, 8_000_000),
    "HOMEOWNERS":          (120_000, 900_000),
    "HIGH_VALUE_HOME":     (900_000, 6_000_000),
}

def postcode(country, r):
    if country in ("FR", "IT", "ES", "DE"):
        return f"{r.randint(10000, 98999):05d}"
    return f"{r.randint(1000, 9999):04d}"  # AT 4-digit

rows = []
oid = 0
for city, country, clat, clon, n, scatter in CLUSTERS:
    for _ in range(n):
        oid += 1
        # gaussian scatter; longitude scatter widened by 1/cos(lat) so degrees ≈ isotropic metres
        lat = clat + rng.gauss(0, scatter)
        lon = clon + rng.gauss(0, scatter / max(0.3, math.cos(math.radians(clat))))
        lob = rng.choices(LOB, weights=LOB_W)[0]
        lo, hi = SI_RANGE[lob]
        si = round(rng.uniform(lo, hi), 2)
        pc = postcode(country, rng)
        obj_type = rng.choices(OBJ, weights=[0.7, 0.2, 0.1])[0]
        rows.append((
            f"IO{oid:07d}",
            f"POL{country}{oid:07d}",
            obj_type,
            f"{rng.choice(OCCUPANCY).title()} property, {city}",
            country,
            pc,
            round(lat, 6),
            round(lon, 6),
            f"{country}_{pc}",
            si,
            rng.choices(COV, weights=[0.6, 0.2, 0.2])[0],
            lob,
            rng.choice(CONSTRUCTION),
            rng.choice(OCCUPANCY),
            city,
        ))

schema_str = (
    "insured_object_id string, policy_id string, insured_object_type_code string, description string, "
    "country_code string, postcode string, latitude double, longitude double, location_key string, "
    "sum_insured double, coverage_type_code string, line_of_business_code string, "
    "construction_code string, occupancy string, city string"
)
prop = (
    spark.createDataFrame(rows, schema_str)
    .withColumn("latitude", F.col("latitude").cast("decimal(9,6)"))
    .withColumn("longitude", F.col("longitude").cast("decimal(9,6)"))
    .withColumn("sum_insured", F.col("sum_insured").cast("decimal(15,2)"))
    .withColumn("as_at_date", F.lit(TODAY))
)
prop.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.3_property")
print(f"3_property: {prop.count()} rows")
spark.sql(f"COMMENT ON TABLE {fqn}.3_property IS "
          f"'Bricksurance SE synthetic European property book (FR/IT/AT/ES/DE), geocoded. Anchor for exposure and event response.'")

# COMMAND ----------

# MAGIC %md ## Frozen event footprints (synthetic) — the fallback the live feeds later replace

# COMMAND ----------

def poly(coords):
    # coords: list of (lon, lat); auto-close the ring
    ring = coords + [coords[0]]
    return "POLYGON((" + ", ".join(f"{lon} {lat}" for lon, lat in ring) + "))"

# FIRE — Var wildfire, SE France. Box sits over the western half of the dense Var cluster so the bands populate.
fire_wkt = poly([(6.745, 43.593), (6.760, 43.593), (6.760, 43.607), (6.745, 43.607)])
# FLOOD — one event, TWO segments crossing the IT↔AT border (the cross-border seed).
flood_it_wkt = poly([(12.90, 46.44), (13.10, 46.44), (13.10, 46.56), (12.90, 46.56)])
flood_at_wkt = poly([(13.28, 46.55), (13.52, 46.55), (13.52, 46.69), (13.28, 46.69)])
# STORM — broad windstorm swath over northern Germany (Hamburg under it).
storm_wkt = poly([(8.6, 53.2), (11.2, 53.2), (11.2, 53.9), (8.6, 53.9)])
# FIRE (small, UNDER the retention) — a localised brush fire near Esterel, SE France. Modelled loss (~EUR 14m)
# stays inside the EUR 25m retention, so the treaty does not attach and net = gross. Shows the waterfall's low end.
esterel_wkt = poly([(6.736, 43.585), (6.749, 43.585), (6.749, 43.597), (6.736, 43.597)])
# STORM (severe, PIERCES the tower) — a major windstorm swath across the Paris-Lyon corridor. Modelled loss
# (~EUR 567m) exceeds the EUR 400m programme top, so net rises well above the retention. Shows the waterfall's high end.
celine_wkt = poly([(1.75, 45.50), (5.10, 45.50), (5.10, 49.22), (1.75, 49.22)])

def dsub(days):
    return TODAY - datetime.timedelta(days=days)

footprints = [
    # event_id, peril_code, event_name, event_date, footprint_wkt, country_code, source, detected_at
    ("EVT_FIRE_VAR",     "FIRE",  "Var Wildfire",       dsub(3),  fire_wkt,     "FR", "SYNTHETIC"),
    ("EVT_FLOOD_ALPS",   "FLOOD", "Alpine Flood",       dsub(6),  flood_it_wkt, "IT", "SYNTHETIC"),
    ("EVT_FLOOD_ALPS",   "FLOOD", "Alpine Flood",       dsub(6),  flood_at_wkt, "AT", "SYNTHETIC"),
    ("EVT_STORM_NORD",   "STORM", "Windstorm Nord",     dsub(12), storm_wkt,    "DE", "SYNTHETIC"),
    # Two scenarios that make the gross->net waterfall visibly vary across the tower (see REQUIREMENTS.md / DEMO_QA Q10):
    ("EVT_FIRE_ESTEREL", "FIRE",  "Esterel Brush Fire", dsub(2),  esterel_wkt,  "FR", "SYNTHETIC"),  # under retention -> net = gross
    ("EVT_STORM_CELINE", "STORM", "Windstorm Celine",   dsub(9),  celine_wkt,   "FR", "SYNTHETIC"),  # pierces tower -> net > retention
]
fp = (
    spark.createDataFrame(
        footprints,
        "event_id string, peril_code string, event_name string, event_date date, "
        "footprint_wkt string, country_code string, source string",
    )
    .withColumn("detected_at", F.current_timestamp())
)
fp.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.2_event_footprint")
print(f"2_event_footprint: {fp.count()} rows across {fp.select('event_id').distinct().count()} events")
spark.sql(f"COMMENT ON TABLE {fqn}.2_event_footprint IS "
          f"'Frozen synthetic hazard footprints (WKT, EPSG:4326) for wildfire/flood/windstorm. Live FIRMS/EFFIS/GloFAS/MeteoAlarm feeds land in this shape in P2. Alpine Flood spans two segments under one event_id (cross-border).'")

# COMMAND ----------

# MAGIC %md ## Smoke — properties within 50/100/200m of the Var wildfire footprint (the hero band logic)

# COMMAND ----------

smoke = spark.sql(f"""
WITH fire AS (
  SELECT ST_Transform(ST_GeomFromText(footprint_wkt, 4326), 3035) AS g
  FROM {fqn}.2_event_footprint WHERE event_id = 'EVT_FIRE_VAR'
),
props AS (
  SELECT insured_object_id, sum_insured,
         ST_Transform(ST_GeomFromText(concat('POINT(', longitude, ' ', latitude, ')'), 4326), 3035) AS g
  FROM {fqn}.3_property
)
SELECT
  count_if(ST_DWithin(props.g, fire.g, 50))  AS within_50m,
  count_if(ST_DWithin(props.g, fire.g, 100)) AS within_100m,
  count_if(ST_DWithin(props.g, fire.g, 200)) AS within_200m,
  round(sum(CASE WHEN ST_DWithin(props.g, fire.g, 200) THEN sum_insured END)/1e6, 1) AS sum_insured_within_200m_eur_m
FROM props CROSS JOIN fire
""")
smoke.show(truncate=False)
