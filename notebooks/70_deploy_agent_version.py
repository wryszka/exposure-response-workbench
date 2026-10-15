# Databricks notebook source
# MAGIC %md
# MAGIC # 70 · Deploy a registered agent version (no rebuild)
# MAGIC Points `exposure-response-agent` at an already-registered version of
# MAGIC `exposure_response.exposure_response_agent` (default: the `@prod` alias), waits for READY and re-grants the
# MAGIC app CAN_QUERY. Use it when a build registered a version but the endpoint was busy updating.

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
agents.deploy(MODEL, version, endpoint_name=ENDPOINT, scale_to_zero=True, tags={"demo": "exposure-response-workbench"})
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
