"""Split the "Exposure — Event maps" dashboard into one-widget map dashboards, one per event page.

Why: the single-widget embed (`fullscreenWidget=<page>~<widget>`) loaded the frame but the map never drew in the
app (16 Oct dry run). The plain embed of a whole dashboard (`/embed/dashboardsv3/<id>?o=<wsid>`) renders, so each
event's map becomes its own small dashboard holding just that map.

Usage: python resources/dashboards/split_event_maps.py   (DEV profile; creates + publishes NEW dashboards each run)
Then put the printed ids into MAP_DASH in notebooks/62_map_views.py and re-run it (rewrites ref_dashboard_pages).
"""
import json
import pathlib
import subprocess

PROFILE, WAREHOUSE = "DEV", "a3b61648ea4809e3"
PARENT = "/Users/laurence.ryszka@databricks.com/exposure_response"
src = json.loads((pathlib.Path(__file__).parent / "event_maps.lvdash.json").read_text())
datasets = {d["name"]: d for d in src["datasets"]}


def api(method, path, body):
    r = subprocess.run(["databricks", "api", method, path, "-p", PROFILE, "--json", json.dumps(body)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout or "{}")


ids = {}
for page in src["pages"]:
    map_w = next(w for w in page["layout"] if (w["widget"].get("spec") or {}).get("widgetType") == "symbol-map")
    w = json.loads(json.dumps(map_w))
    w["position"] = {"x": 0, "y": 0, "width": 12, "height": 11}
    one = {"datasets": [datasets[w["widget"]["queries"][0]["query"]["datasetName"]]],
           "pages": [{"name": page["name"], "displayName": page["displayName"], "layout": [w]}]}
    did = api("post", "/api/2.0/lakeview/dashboards",
              {"display_name": f"Exposure — Map · {page['displayName']}", "parent_path": PARENT,
               "warehouse_id": WAREHOUSE, "serialized_dashboard": json.dumps(one)})["dashboard_id"]
    api("post", f"/api/2.0/lakeview/dashboards/{did}/published", {"embed_credentials": True, "warehouse_id": WAREHOUSE})
    ids[page["name"]] = did
    print(page["name"], did)
print(json.dumps(ids, indent=1))
