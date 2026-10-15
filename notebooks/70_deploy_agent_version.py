# Databricks notebook source
# MAGIC %md
# MAGIC # 70 · Deploy a registered agent version (no rebuild)
# MAGIC Points `exposure-response-agent` at an already-registered version of
# MAGIC `exposure_response.exposure_response_agent` (default: the `@prod` alias), waits for READY and re-grants the
# MAGIC app CAN_QUERY. Use it when a build registered a version but the endpoint was busy updating.
# MAGIC
# MAGIC **Also run it at the END of every `exposure_99_reset`.** The endpoint authenticates to its tools with
# MAGIC automatic credential passthrough, scoped to the functions and tables resolved at deploy time. The reset
# MAGIC drops and re-creates several of them (new objects), so until the endpoint is redeployed those tool calls fail
# MAGIC with "insufficient privileges" even though the grants look right. If the version is already being served,
# MAGIC this notebook re-pushes the same served entity, which forces a fresh deployment (~3–5 min).

# COMMAND ----------

# MAGIC %pip install -q databricks-agents
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("model", "lr_dev_aws_us_catalog.exposure_response.exposure_response_agent")
dbutils.widgets.text("version", "")   # blank → @prod
dbutils.widgets.text("endpoint", "exposure-response-agent")
dbutils.widgets.text("app_sp", "fc3ac835-8393-4ea6-8d2b-180096836d7b")

import json, time
from databricks.sdk import WorkspaceClient
from mlflow.tracking import MlflowClient

MODEL = dbutils.widgets.get("model")
ENDPOINT = dbutils.widgets.get("endpoint")
app_sp = dbutils.widgets.get("app_sp").strip()
w = WorkspaceClient()
client = MlflowClient(registry_uri="databricks-uc")
version = dbutils.widgets.get("version").strip() or client.get_model_version_by_alias(MODEL, "prod").version
client.set_registered_model_alias(MODEL, "prod", version)
print("deploying", MODEL, "version", version)

from databricks import agents
try:
    agents.deploy(MODEL, version, endpoint_name=ENDPOINT, scale_to_zero=True,
                  tags={"demo": "exposure-response-workbench"})
except ValueError as e:
    if "already serves" not in str(e):
        raise
    # Same version already served → force a fresh deployment of it (re-resolves the passthrough scope).
    from databricks.sdk.service.serving import ServedEntityInput, TrafficConfig, Route
    cur = w.serving_endpoints.get(ENDPOINT)
    ent = [x for x in cur.config.served_entities if str(x.entity_version) == str(version)][0]
    print("version", version, "already served — refreshing", ent.name)
    w.serving_endpoints.update_config(
        name=ENDPOINT,
        served_entities=[ServedEntityInput(name=ent.name, entity_name=ent.entity_name, entity_version=ent.entity_version,
                                           workload_size=ent.workload_size, scale_to_zero_enabled=ent.scale_to_zero_enabled,
                                           environment_vars=ent.environment_vars)],
        traffic_config=TrafficConfig(routes=[Route(served_model_name=ent.name, traffic_percentage=100)]))
t0 = time.time()
while time.time() - t0 < 45 * 60:
    ep = w.serving_endpoints.get(ENDPOINT)
    ready = ep.state.ready.value if ep.state and ep.state.ready else "?"
    upd = ep.state.config_update.value if ep.state and ep.state.config_update else "?"
    print(f"{int(time.time() - t0)}s ready={ready} config_update={upd}")
    if ready == "READY" and upd == "NOT_UPDATING":
        break
    if upd == "UPDATE_FAILED":
        raise RuntimeError("endpoint update failed — see the serving endpoint events / build logs")
    time.sleep(30)

from databricks.sdk.service.serving import ServingEndpointAccessControlRequest, ServingEndpointPermissionLevel
if app_sp:
    w.serving_endpoints.update_permissions(
        serving_endpoint_id=ep.id,
        access_control_list=[ServingEndpointAccessControlRequest(
            service_principal_name=app_sp, permission_level=ServingEndpointPermissionLevel.CAN_QUERY)])
served = [e.entity_version for e in (ep.config.served_entities or [])] if ep.config else []
dbutils.notebook.exit(json.dumps({"endpoint": ENDPOINT, "version": version, "ready": ready,
                                  "config_update": upd, "served_versions": served}))
