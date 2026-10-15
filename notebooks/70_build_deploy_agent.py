# Databricks notebook source
# MAGIC %md
# MAGIC # 70 · Build + deploy the Exposure & Event Response agent team
# MAGIC
# MAGIC Logs the co-located `70_exposure_agent.py` (a Mosaic AI Agent-Framework **ResponsesAgent**: a supervisor +
# MAGIC six specialists) to Unity Catalog, registers `@prod`, and deploys it to the Model Serving endpoint
# MAGIC **`exposure-response-agent`** (Agent Framework / UC AI Gateway). Also writes `ref_agent_roster` — the team card
# MAGIC the app shows (responsibilities, skills, data read, limits).
# MAGIC
# MAGIC Tool specs are read from Unity Catalog at build time (function comments + parameters), so re-running this
# MAGIC notebook picks up any new governed function a specialist is allowed to use. Submit as a serverless job —
# MAGIC the deploy + wait takes ~15–25 min.

# COMMAND ----------

# MAGIC %pip install -q databricks-agents openai httpx
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "fc3ac835-8393-4ea6-8d2b-180096836d7b")
dbutils.widgets.text("endpoint", "exposure-response-agent")
dbutils.widgets.text("llm_endpoint", "databricks-claude-sonnet-5-5")
dbutils.widgets.text("router_endpoint", "databricks-claude-haiku-5-5")
dbutils.widgets.text("uw_schema", "underwriting_workbench")
dbutils.widgets.text("deploy", "true")

import importlib.util, json, time
import mlflow
from databricks.sdk import WorkspaceClient

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
ENDPOINT = dbutils.widgets.get("endpoint")
LLM = dbutils.widgets.get("llm_endpoint")
ROUTER = dbutils.widgets.get("router_endpoint")
uw_schema = dbutils.widgets.get("uw_schema").strip()
DEPLOY = dbutils.widgets.get("deploy").lower() == "true"
fqn = f"{catalog}.{schema}"
FULL_NAME = f"{fqn}.exposure_response_agent"
w = WorkspaceClient()
print("mlflow", mlflow.__version__)

ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
nb_dir = ctx.notebookPath().get().rsplit("/", 1)[0]
agent_file = "/Workspace" + nb_dir + "/70_exposure_agent.py"
spec = importlib.util.spec_from_file_location("exposure_agent", agent_file)
team = importlib.util.module_from_spec(spec)
spec.loader.exec_module(team)
SPECIALISTS = team.SPECIALISTS
print("agent file:", agent_file, "| specialists:", list(SPECIALISTS))

# COMMAND ----------

# MAGIC %md ## Tool specs from Unity Catalog (only functions that exist are given to the team)

# COMMAND ----------

wanted = sorted({t for sp in SPECIALISTS.values() for t in sp["tools"]})
tool_specs, missing = [], []
for name in wanted:
    try:
        f = w.functions.get(f"{fqn}.{name}")
    except Exception:
        missing.append(name)
        continue
    params = []
    for p in (f.input_params.parameters if f.input_params and f.input_params.parameters else []):
        params.append({"name": p.name, "type": p.type_text or p.type_name.value,
                       "default": p.parameter_default, "comment": p.comment or ""})
    tool_specs.append({"name": name, "description": (f.comment or name), "params": params})
print("tools available:", [t["name"] for t in tool_specs])
print("tools not yet in this workspace:", missing)

config = {**team.DEFAULT_CONFIG, "llm_endpoint": LLM, "router_endpoint": ROUTER, "warehouse_id": warehouse_id,
          "catalog": catalog, "schema": schema, "tools": tool_specs}

# COMMAND ----------

# MAGIC %md ## Smoke test in the notebook (same code the endpoint runs)

# COMMAND ----------

from mlflow.types.responses import ResponsesAgentRequest

local = team.ExposureAgentTeam(config)
for q in ["What's my exposure to the Var wildfire, gross and net?",
          "Can we bind cover in Munich right now?"]:
    r = local.predict(ResponsesAgentRequest(input=[{"role": "user", "content": q}]))
    print("Q:", q, "\n  →", r.custom_outputs["specialist"], "|", r.custom_outputs["route_reason"])
    print("  tools:", [t["name"] for t in r.custom_outputs["tools"]])
    print("  ", r.output[0].content[0]["text"][:600] if hasattr(r.output[0], "content") else r.output[0])

# COMMAND ----------

# MAGIC %md ## Log + register (passthrough resources = LLMs, warehouse, functions, tables)

# COMMAND ----------

from mlflow.models.resources import (DatabricksFunction, DatabricksServingEndpoint, DatabricksSQLWarehouse,
                                     DatabricksTable)
from mlflow.tracking import MlflowClient

mlflow.set_registry_uri("databricks-uc")
exp_dir = "/Workspace/Users/laurence.ryszka@databricks.com/exposure_response_agent"
try:
    w.workspace.mkdirs(exp_dir)
except Exception as e:
    print("mkdirs:", e)
mlflow.set_experiment(exp_dir + "/experiment")

tables = [f"{fqn}.{t.name}" for t in w.tables.list(catalog_name=catalog, schema_name=schema)]
uw_centroids = f"{catalog}.{uw_schema}.ref_postcode_centroid"
try:
    w.tables.get(uw_centroids); tables.append(uw_centroids)
except Exception:
    pass
resources = [DatabricksServingEndpoint(endpoint_name=LLM), DatabricksServingEndpoint(endpoint_name=ROUTER),
             DatabricksSQLWarehouse(warehouse_id=warehouse_id),
             *[DatabricksFunction(function_name=f"{fqn}.{t['name']}") for t in tool_specs],
             *[DatabricksTable(table_name=t) for t in tables]]
print(len(resources), "resources")

from importlib.metadata import version as _v
def _pin(pkg):
    try:
        return f"{pkg}=={_v(pkg)}"
    except Exception:
        return None
pip_reqs = [p for p in (_pin(x) for x in ["mlflow", "databricks-sdk", "openai", "httpx", "pydantic"]) if p]
print("pip:", pip_reqs)

with mlflow.start_run(run_name="log-exposure-agent"):
    info = mlflow.pyfunc.log_model(
        name="agent",
        python_model=agent_file,
        model_config=config,
        resources=resources,
        input_example={"input": [{"role": "user", "content": "What's my exposure to the Var wildfire, gross and net?"}]},
        pip_requirements=pip_reqs,
        registered_model_name=FULL_NAME,
    )
client = MlflowClient(registry_uri="databricks-uc")
version = max(client.search_model_versions(f"name='{FULL_NAME}'"), key=lambda x: int(x.version)).version
client.set_registered_model_alias(FULL_NAME, "prod", version)
print("registered", FULL_NAME, "version", version)

# COMMAND ----------

# MAGIC %md ## The team card — `ref_agent_roster`

# COMMAND ----------

roster = [{
    "agent_key": "supervisor", "display_order": 0, "kind": "supervisor", "name": "Supervisor",
    "role": "Reads each question and hands it to the right specialist (or two, in order), saying why.",
    "responsibilities": ["Route every question to one specialist, or chain two when needed",
                         "Record the reason for the routing so it can be shown to the user",
                         "Never answers a question itself"],
    "skills": [], "reads": [],
    "may_do": ["Route questions", "Chain two specialists"],
    "may_not_do": ["Answer questions or quote numbers itself"],
    "example_questions": ["Who is in the path of the Alpine flood, and what will it cost us?"],
    "endpoint": ENDPOINT,
}]
for i, (k, sp) in enumerate(SPECIALISTS.items(), start=1):
    roster.append({"agent_key": k, "display_order": i, "kind": "specialist", "name": sp["name"], "role": sp["role"],
                   "responsibilities": sp["responsibilities"], "skills": sp["tools"], "reads": sp["reads"],
                   "may_do": sp["may_do"], "may_not_do": sp["may_not_do"], "example_questions": sp["examples"],
                   "endpoint": ENDPOINT})
(spark.createDataFrame(roster)
 .select("agent_key", "display_order", "kind", "name", "role", "responsibilities", "skills", "reads",
         "may_do", "may_not_do", "example_questions", "endpoint")
 .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_agent_roster"))
spark.sql(f"COMMENT ON TABLE {fqn}.ref_agent_roster IS 'The Exposure and Event Response agent team: supervisor plus six specialists, with responsibilities, skills (governed UC functions), data read and limits. Served as one endpoint.'")
for who in [p for p in ("account users", app_sp) if p]:
    spark.sql(f"GRANT SELECT ON TABLE {fqn}.ref_agent_roster TO `{who}`")
print("ref_agent_roster written:", len(roster), "rows")

# COMMAND ----------

# MAGIC %md ## Deploy to Model Serving (Agent Framework) and grant the app CAN_QUERY

# COMMAND ----------

result = {"endpoint": ENDPOINT, "model": FULL_NAME, "version": version, "tools": [t["name"] for t in tool_specs],
          "missing_tools": missing}
if DEPLOY:
    from databricks import agents
    dep = agents.deploy(FULL_NAME, version, endpoint_name=ENDPOINT, scale_to_zero=True,
                        tags={"demo": "exposure-response-workbench"})
    print("deploy started:", getattr(dep, "endpoint_name", ENDPOINT))
    t0 = time.time()
    while time.time() - t0 < 45 * 60:
        ep = w.serving_endpoints.get(ENDPOINT)
        ready = ep.state.ready.value if ep.state and ep.state.ready else "?"
        upd = ep.state.config_update.value if ep.state and ep.state.config_update else "?"
        print(f"{int(time.time() - t0)}s ready={ready} config_update={upd}")
        if ready == "READY" and upd in ("NOT_UPDATING", "?"):
            break
        if upd == "UPDATE_FAILED":
            raise RuntimeError("endpoint update failed — see the serving endpoint events / build logs")
        time.sleep(30)
    from databricks.sdk.service.serving import (ServingEndpointAccessControlRequest,
                                                ServingEndpointPermissionLevel)
    if app_sp:
        w.serving_endpoints.update_permissions(
            serving_endpoint_id=ep.id,
            access_control_list=[ServingEndpointAccessControlRequest(
                service_principal_name=app_sp, permission_level=ServingEndpointPermissionLevel.CAN_QUERY)])
        print("granted CAN_QUERY to", app_sp)
    result.update({"ready": ready, "config_update": upd})
dbutils.notebook.exit(json.dumps(result))
