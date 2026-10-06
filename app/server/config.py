"""Config — all portability via env vars (set in app.yaml). No hardcoded catalog/schema/IDs in logic."""
import os
from functools import lru_cache
from databricks.sdk import WorkspaceClient


def _flag(name, default=True):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


CATALOG = os.getenv("CATALOG_NAME", "lr_dev_aws_us_catalog")
SCHEMA = os.getenv("SCHEMA_NAME", "exposure_response")
WAREHOUSE_ID = os.getenv("WAREHOUSE_ID", "a3b61648ea4809e3")
USE_CACHE = _flag("USE_CACHE", True)
GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID", "")
DASHBOARD_ID = os.getenv("DASHBOARD_ID", "01f1c169c0e0157496036dc765800f30")  # AI/BI Live Fire Tracker
FM_ENDPOINT = os.getenv("FM_ENDPOINT", "databricks-claude-sonnet-4-6")
ENTITY = os.getenv("ENTITY_NAME", "Bricksurance SE")


def fqn(table: str) -> str:
    return f"{CATALOG}.{SCHEMA}.{table}"


@lru_cache(maxsize=1)
def get_workspace_client() -> WorkspaceClient:
    return WorkspaceClient()


def workspace_host() -> str:
    h = os.getenv("DATABRICKS_HOST", "")
    if h:
        return h.rstrip("/")
    try:
        return get_workspace_client().config.host.rstrip("/")
    except Exception:
        return ""


@lru_cache(maxsize=1)
def workspace_id() -> str:
    """Org/workspace id for the dashboard embed URL (?o=). Apps inject DATABRICKS_WORKSPACE_ID; else ask the SDK."""
    wid = os.getenv("DATABRICKS_WORKSPACE_ID", "")
    if wid:
        return wid
    try:
        return str(get_workspace_client().get_workspace_id())
    except Exception:
        return ""
