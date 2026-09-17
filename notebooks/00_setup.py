# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup + reference data — Exposure & Event Response (Bricksurance SE)
# MAGIC
# MAGIC Creates the demo schema and the governed reference code-sets the workbench reads. Code-sets are
# MAGIC **mirrored from bricksurance-data-core** (`reference.cause_of_loss`, `country`, `insured_object_type`,
# MAGIC `coverage_type`, `line_of_business`) but seeded locally so this demo stands alone — it never hard-depends
# MAGIC on the data-core being deployed in the target catalog.
# MAGIC
# MAGIC Module boundary: standalone, re-runnable, parameterised (catalog/schema/seed widgets). The reset job
# MAGIC calls it with the same seed so the world re-anchors to `current_date()` identically.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("seed", "42")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
SEED = int(dbutils.widgets.get("seed"))
fqn = f"{catalog}.{schema}"

from pyspark.sql import functions as F

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {fqn}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {fqn}.landing")
print(f"target = {fqn}  seed={SEED}")

# COMMAND ----------

# MAGIC %md ## ref_cause_of_loss — perils (ACORD LossCauseCd; mirrors data-core cause_of_loss)
# MAGIC The three perils this workbench tracks live are FIRE / FLOOD / STORM; the rest are carried for completeness.

# COMMAND ----------

CAUSE_OF_LOSS = [
    # code, label, description, is_tracked_peril
    ("FIRE",  "Fire",  "Fire, including smoke damage and firefighting damage. Wildfire footprints map here.", True),
    ("FLOOD", "Flood", "Inundation from external water, including river and surface-water flooding.", True),
    ("STORM", "Storm", "Windstorm, hail and associated weather damage.", True),
    ("WATER_DAMAGE", "Water Damage", "Escape of water from internal systems such as burst pipes.", False),
    ("THEFT", "Theft", "Theft, attempted theft and malicious damage in connection with it.", False),
    ("FREEZE", "Freeze", "Freeze, frost and burst-pipe damage from severe cold weather.", False),
    ("LIABILITY_INCIDENT", "Liability Incident", "An event giving rise to third-party injury or property damage.", False),
]
spark.createDataFrame(
    CAUSE_OF_LOSS, "cause_of_loss_code string, label string, description string, is_tracked_peril boolean"
).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_cause_of_loss")

# COMMAND ----------

# MAGIC %md ## ref_country — European book footprint (ISO 3166-1 alpha-2; mirrors data-core country)

# COMMAND ----------

COUNTRY = [
    # code, label, primary_ccy
    ("FR", "France", "EUR"),
    ("IT", "Italy", "EUR"),
    ("AT", "Austria", "EUR"),
    ("ES", "Spain", "EUR"),
    ("DE", "Germany", "EUR"),
]
spark.createDataFrame(
    COUNTRY, "country_code string, label string, currency_code string"
).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_country")

# COMMAND ----------

# MAGIC %md ## ref_insured_object_type + ref_coverage_type + ref_line_of_business

# COMMAND ----------

INSURED_OBJECT_TYPE = [
    ("BUILDING", "Building", "A building or fixed structure at a defined location."),
    ("CONTENTS", "Contents", "Contents and stock within a building at a defined location."),
    ("BUSINESS_INTERRUPTION", "Business Interruption", "Loss of income following damage to insured operations at a location."),
]
spark.createDataFrame(
    INSURED_OBJECT_TYPE, "insured_object_type_code string, label string, description string"
).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_insured_object_type")

COVERAGE_TYPE = [
    ("PROPERTY_DAMAGE", "Property Damage", "Physical loss or damage to the insured object."),
    ("BUSINESS_INTERRUPTION", "Business Interruption", "Consequential loss of income."),
    ("CONTENTS", "Contents", "Contents and stock cover."),
]
spark.createDataFrame(
    COVERAGE_TYPE, "coverage_type_code string, label string, description string"
).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_coverage_type")

LINE_OF_BUSINESS = [
    ("COMMERCIAL_PROPERTY", "Commercial Property", "Commercial buildings, contents and BI."),
    ("HOMEOWNERS", "Homeowners", "Residential property owners."),
    ("HIGH_VALUE_HOME", "High-Value Home", "High-net-worth residential property."),
]
spark.createDataFrame(
    LINE_OF_BUSINESS, "line_of_business_code string, label string, description string"
).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_line_of_business")

# COMMAND ----------

print("reference code-sets written:")
for t in ["ref_cause_of_loss", "ref_country", "ref_insured_object_type", "ref_coverage_type", "ref_line_of_business"]:
    n = spark.table(f"{fqn}.{t}").count()
    print(f"  {t}: {n} rows")
