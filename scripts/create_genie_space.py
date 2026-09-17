#!/usr/bin/env python3
"""Create the 'Ask the Exposure Book' Genie space over the exposure-response marts. Reproducible.
Usage: python3 scripts/create_genie_space.py [profile] [warehouse_id] [catalog] [schema]
Prints SPACE_ID on success. Uses the genie-rooms skill's GenieSpaceBuilder."""
import sys, json, subprocess, pathlib

prof = sys.argv[1] if len(sys.argv) > 1 else "DEV"
wh = sys.argv[2] if len(sys.argv) > 2 else "a3b61648ea4809e3"
cat = sys.argv[3] if len(sys.argv) > 3 else "lr_dev_aws_us_catalog"
sch = sys.argv[4] if len(sys.argv) > 4 else "exposure_response"

BUILDER = pathlib.Path.home() / ".vibe/marketplace/plugins/fe-internal-tools/skills/genie-rooms/resources"
sys.path.insert(0, str(BUILDER))
from genie_space_builder import GenieSpaceBuilder  # noqa

fqn = f"{cat}.{sch}"
space = GenieSpaceBuilder(
    title="Ask the Exposure Book — Bricksurance SE",
    description="Natural-language analytics over the European property book and live catastrophe events: exposure by country/peril, sum insured, treaty tower, coverholders/delegated authorities, and event provenance.",
    warehouse_id=wh,
)
space.set_instructions(
    "You answer questions about a European property insurer's exposure book. mv_property is the insured "
    "property book — one row per insured object with country_code (FR/IT/AT/ES/DE), city, postcode, "
    "latitude/longitude, sum_insured (EUR), coverage_type_code, line_of_business_code and coverholder link. "
    "mv_event holds active catastrophe events (peril_code FIRE/FLOOD/STORM, event_name, event_date, "
    "source, is_live = whether it is a live feed or a frozen illustrative footprint). mv_treaty + "
    "mv_treaty_layer are the Property Cat XL reinsurance tower (attachment_eur, limit_eur, placement_pct). "
    "mv_coverholder + mv_property_coverholder map properties to delegated authorities / binders "
    "(is_delegated). gov_data_provenance summarises each event's feed source and live/frozen status. "
    "Money is EUR; report large sums in millions. For geospatial 'near an event' distance questions, tell the "
    "user to use the Exposure view (the governed fn_exposure_in_footprint), as Genie does not compute ST_ distances."
)
for t in ["mv_property", "mv_event", "mv_treaty", "mv_treaty_layer",
          "mv_coverholder", "mv_property_coverholder", "gov_data_provenance",
          "ref_country", "ref_cause_of_loss"]:
    space.add_table(f"{fqn}.{t}")
space.add_sample_questions([
    "What is the total sum insured by country?",
    "How many properties do we insure in Italy and Austria?",
    "Which coverholders are delegated authorities, and how much do they carry?",
    "List the active events and whether each is a live feed or a frozen footprint.",
    "What is the total limit of the Property Cat XL tower?",
]) if hasattr(space, "add_sample_questions") else None
space.validate()

payload = {
    "title": "Ask the Exposure Book — Bricksurance SE",
    "description": "European property exposure analytics: exposure by country/peril, sum insured, treaty tower, coverholders, event provenance.",
    "parent_path": "/Workspace/Users/laurence.ryszka@databricks.com",
    "warehouse_id": wh,
    "serialized_space": space.to_json(),
}
open("/tmp/create_genie_space_exposure.json", "w").write(json.dumps(payload))
out = subprocess.run(["databricks", "api", "post", "/api/2.0/genie/spaces", "--profile", prof,
                      "--json", "@/tmp/create_genie_space_exposure.json"], capture_output=True, text=True)
print(out.stdout[:1000] or out.stderr[:1000])
try:
    print("SPACE_ID:", json.loads(out.stdout)["space_id"])
except Exception:
    pass
