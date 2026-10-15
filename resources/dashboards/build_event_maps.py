"""Generate resources/dashboards/event_maps.lvdash.json — the "Exposure — Event maps" AI/BI dashboard.

One page per hero event, each a large point map (event footprint drawn as fill points + insured homes
coloured in-the-path vs clear) with KPI counters that match the app. Datasets read the map views built by
notebooks/62_map_views.py. Run: python3 resources/dashboards/build_event_maps.py
"""
import json
import pathlib

CAT, SCH = "lr_dev_aws_us_catalog", "exposure_response"
RED, PURPLE, SLATE = "#DC2626", "#7C3AED", "#475569"
FIRE_FP, FLOOD_FP, STORM_FP = "#FDBA74", "#7DD3FC", "#C4B5FD"
EUR = {"type": "number-currency", "currencyCode": "EUR", "abbreviation": "compact",
       "decimalPlaces": {"type": "max", "places": 1}}
GBP = dict(EUR, currencyCode="GBP")
INT = {"type": "number", "abbreviation": "compact", "decimalPlaces": {"type": "max", "places": 0}}


def ds(name, display, lines):
    return {"name": name, "displayName": display, "catalog": CAT, "schema": SCH,
            "queryLines": [l if l.endswith((" ", "\n")) else l + "\n" for l in lines]}


def static_event_sql(event_id, fp_layer, by_country=False):
    home_layer = ("CASE WHEN NOT in_path THEN 'Home clear' WHEN country_code='IT' THEN 'Home in the path · Italy' "
                  "WHEN country_code='AT' THEN 'Home in the path · Austria' ELSE 'Home in the path' END"
                  if by_country else "CASE WHEN in_path THEN 'Home in the path' ELSE 'Home clear' END")
    return [
        f"SELECT '{fp_layer}' AS layer, latitude, longitude, 1 AS marker, 0 AS at_risk, CAST(0 AS DOUBLE) AS at_risk_si,",
        "       0 AS at_risk_it, 0 AS at_risk_at",
        f"FROM mv_map_footprint_points WHERE event_id = '{event_id}'",
        "UNION ALL",
        f"SELECT {home_layer} AS layer, latitude, longitude,",
        "       CASE WHEN in_path THEN 6 ELSE 2 END AS marker, CASE WHEN in_path THEN 1 ELSE 0 END AS at_risk,",
        "       CAST(CASE WHEN in_path THEN sum_insured ELSE 0 END AS DOUBLE) AS at_risk_si,",
        "       CASE WHEN in_path AND country_code='IT' THEN 1 ELSE 0 END AS at_risk_it,",
        "       CASE WHEN in_path AND country_code='AT' THEN 1 ELSE 0 END AS at_risk_at",
        f"FROM mv_map_event_homes WHERE event_id = '{event_id}'",
        "ORDER BY marker",
    ]


LIVE_SQL = [
    "SELECT 'Fire footprint' AS layer, latitude, longitude, 1 AS marker, 0 AS at_risk, CAST(0 AS DOUBLE) AS at_risk_si,",
    "       t_index + 1 AS update_no",
    "FROM mv_map_live_footprint_points",
    "UNION ALL",
    "SELECT CASE WHEN in_path THEN 'Home in the path' ELSE 'Home clear' END AS layer, latitude, longitude,",
    "       CASE WHEN in_path THEN 6 ELSE 2 END AS marker, CASE WHEN in_path THEN 1 ELSE 0 END AS at_risk,",
    "       CAST(CASE WHEN in_path THEN sum_insured ELSE 0 END AS DOUBLE) AS at_risk_si, t_index + 1 AS update_no",
    "FROM live_fire_state",
    "ORDER BY marker",
]

UK_SQL = [
    "SELECT 'Flood warning area' AS layer, latitude, longitude, 1 AS marker, event_id AS zone_event,",
    "       0 AS district_in_zone, 0 AS subs_in_zone, CAST(0 AS DOUBLE) AS si_in_zone",
    "FROM mv_map_footprint_points WHERE source = 'EA'",
    "UNION ALL",
    "SELECT CASE WHEN in_zone THEN 'Underwriting risks in a flood zone' ELSE 'Underwriting risks clear' END AS layer,",
    "       latitude, longitude, CASE WHEN in_zone THEN 7 ELSE 3 END AS marker, CAST(NULL AS STRING) AS zone_event,",
    "       CASE WHEN in_zone THEN 1 ELSE 0 END AS district_in_zone,",
    "       CASE WHEN in_zone THEN n_submissions ELSE 0 END AS subs_in_zone,",
    "       CAST(CASE WHEN in_zone THEN total_property_si ELSE 0 END AS DOUBLE) AS si_in_zone",
    "FROM mv_map_uk_uw_risks",
    "ORDER BY marker",
]

OVERVIEW_SQL = [
    "SELECT CASE peril_code WHEN 'FIRE' THEN 'Wildfire footprint' WHEN 'FLOOD' THEN 'Flood footprint'",
    "            ELSE 'Storm footprint' END AS layer, latitude, longitude, 1 AS marker, event_id,",
    "       0 AS at_risk, CAST(0 AS DOUBLE) AS at_risk_si",
    "FROM mv_map_footprint_points",
    "UNION ALL",
    "SELECT CASE WHEN in_path THEN 'Home in the path' ELSE 'Home clear' END AS layer, latitude, longitude,",
    "       CASE WHEN in_path THEN 5 ELSE 1 END AS marker, CAST(NULL AS STRING) AS event_id,",
    "       CASE WHEN in_path THEN 1 ELSE 0 END AS at_risk,",
    "       CAST(CASE WHEN in_path THEN sum_insured ELSE 0 END AS DOUBLE) AS at_risk_si",
    "FROM mv_map_book_homes",
    "ORDER BY marker",
]


def text(name, md, y, h=1):
    return {"widget": {"name": name, "multilineTextboxSpec": {"lines": [md]}},
            "position": {"x": 0, "y": y, "width": 12, "height": h}}


def counter(name, dataset, expr_name, expr, title, fmt, x, y, w):
    return {"widget": {
        "name": name,
        "queries": [{"name": "main_query", "query": {
            "datasetName": dataset, "fields": [{"name": expr_name, "expression": expr}], "disaggregated": False}}],
        "spec": {"version": 2, "widgetType": "counter",
                 "encodings": {"value": {"fieldName": expr_name, "displayName": title, "format": fmt}},
                 "frame": {"showTitle": True, "title": title}}},
        "position": {"x": x, "y": y, "width": w, "height": 3}}


def point_map(name, dataset, title, mappings, y, h=10):
    return {"widget": {
        "name": name,
        "queries": [{"name": "main_query", "query": {
            "datasetName": dataset,
            "fields": [{"name": "latitude", "expression": "`latitude`"},
                       {"name": "longitude", "expression": "`longitude`"},
                       {"name": "layer", "expression": "`layer`"},
                       {"name": "marker", "expression": "`marker`"}],
            "disaggregated": True}}],
        "spec": {"version": 2, "widgetType": "symbol-map",
                 "encodings": {
                     "coordinates": {"latitude": {"fieldName": "latitude"}, "longitude": {"fieldName": "longitude"}},
                     "color": {"fieldName": "layer", "displayName": "What you see",
                               "scale": {"type": "categorical",
                                         "mappings": [{"value": v, "color": c} for v, c in mappings]}},
                     "size": {"fieldName": "marker", "scale": {"type": "quantitative"}}},
                 "mark": {"opacity": 0.85},
                 "frame": {"showTitle": True, "title": title}}},
        "position": {"x": 0, "y": y, "width": 12, "height": h}}


def page(name, display, layout):
    return {"name": name, "displayName": display, "pageType": "PAGE_TYPE_CANVAS",
            "layoutVersion": "GRID_V1", "layout": layout}


HOMES = [("Home in the path", RED), ("Home clear", SLATE)]

datasets = [
    ds("ds_live", "Var wildfire — live follow", LIVE_SQL),
    ds("ds_var", "Var wildfire", static_event_sql("EVT_FIRE_VAR", "Fire footprint")),
    ds("ds_alps", "Alpine flood", static_event_sql("EVT_FLOOD_ALPS", "Flood footprint", by_country=True)),
    ds("ds_celine", "Windstorm Celine", static_event_sql("EVT_STORM_CELINE", "Storm footprint")),
    ds("ds_uk", "UK flood warnings vs underwriting risks", UK_SQL),
    ds("ds_overview", "All active events", OVERVIEW_SQL),
]

pages = [
    page("live", "Var wildfire — live", [
        text("live_title", "## Var wildfire — following it live", 0),
        text("live_sub", "Every dot is a home we insure. Red = in the fire's path (within 200 m). The orange area is the fire, redrawn at each update.", 1),
        counter("live_homes", "ds_live", "sum(at_risk)", "SUM(`at_risk`)", "Homes in the fire's path", INT, 0, 2, 4),
        counter("live_si", "ds_live", "sum(at_risk_si)", "SUM(`at_risk_si`)", "Sum insured at risk", EUR, 4, 2, 4),
        counter("live_update", "ds_live", "max(update_no)", "MAX(`update_no`)", "Update (of 4)", INT, 8, 2, 4),
        point_map("live_map", "ds_live", "The fire and the homes in its path", [("Fire footprint", FIRE_FP)] + HOMES, 5),
    ]),
    page("var", "Var wildfire", [
        text("var_title", "## Var wildfire — southern France", 0),
        text("var_sub", "Red = homes we insure within 200 m of the fire. Grey = homes nearby, clear for now.", 1),
        counter("var_homes", "ds_var", "sum(at_risk)", "SUM(`at_risk`)", "Homes in the fire's path", INT, 0, 2, 6),
        counter("var_si", "ds_var", "sum(at_risk_si)", "SUM(`at_risk_si`)", "Sum insured at risk", EUR, 6, 2, 6),
        point_map("var_map", "ds_var", "The fire and the homes in its path", [("Fire footprint", FIRE_FP)] + HOMES, 5),
    ]),
    page("alps", "Alpine flood", [
        text("alps_title", "## Alpine flood — one event, two countries", 0),
        text("alps_sub", "One flood across the Italian–Austrian border. Red = homes in the path in Italy, purple = in Austria. One alert, not two.", 1),
        counter("alps_homes", "ds_alps", "sum(at_risk)", "SUM(`at_risk`)", "Homes in the path", INT, 0, 2, 3),
        counter("alps_si", "ds_alps", "sum(at_risk_si)", "SUM(`at_risk_si`)", "Sum insured at risk", EUR, 3, 2, 3),
        counter("alps_it", "ds_alps", "sum(at_risk_it)", "SUM(`at_risk_it`)", "In Italy", INT, 6, 2, 3),
        counter("alps_at", "ds_alps", "sum(at_risk_at)", "SUM(`at_risk_at`)", "In Austria", INT, 9, 2, 3),
        point_map("alps_map", "ds_alps", "The flood and the homes in its path, by country",
                  [("Flood footprint", FLOOD_FP), ("Home in the path · Italy", RED),
                   ("Home in the path · Austria", PURPLE), ("Home clear", SLATE)], 5),
    ]),
    page("celine", "Windstorm Celine", [
        text("celine_title", "## Windstorm Celine — a severe storm", 0),
        text("celine_sub", "The storm's swath (violet) and every home we insure inside it (red).", 1),
        counter("celine_homes", "ds_celine", "sum(at_risk)", "SUM(`at_risk`)", "Homes in the path", INT, 0, 2, 6),
        counter("celine_si", "ds_celine", "sum(at_risk_si)", "SUM(`at_risk_si`)", "Sum insured at risk", EUR, 6, 2, 6),
        point_map("celine_map", "ds_celine", "The storm and the homes in its path", [("Storm footprint", STORM_FP)] + HOMES, 5),
    ]),
    page("uk", "UK flood warnings", [
        text("uk_title", "## UK flood — Environment Agency warnings", 0),
        text("uk_sub", "Live flood warning areas (blue) published as active zones. Red = underwriting risks inside a warning area: new cover there is referred.", 1),
        counter("uk_zones", "ds_uk", "countdistinct(zone_event)", "COUNT(DISTINCT `zone_event`)", "Active flood warning areas", INT, 0, 2, 4),
        counter("uk_subs", "ds_uk", "sum(subs_in_zone)", "SUM(`subs_in_zone`)", "Underwriting submissions in a zone", INT, 4, 2, 4),
        counter("uk_si", "ds_uk", "sum(si_in_zone)", "SUM(`si_in_zone`)", "Property sum insured in a zone", GBP, 8, 2, 4),
        point_map("uk_map", "ds_uk", "Flood warning areas and underwriting risks",
                  [("Flood warning area", FLOOD_FP), ("Underwriting risks in a flood zone", RED),
                   ("Underwriting risks clear", SLATE)], 5),
    ]),
    page("overview", "All active events", [
        text("ov_title", "## All active events across the book", 0),
        text("ov_sub", "Every active event footprint, and every home we insure — red where an event is within 200 m.", 1),
        counter("ov_events", "ds_overview", "countdistinct(event_id)", "COUNT(DISTINCT `event_id`)", "Active events", INT, 0, 2, 4),
        counter("ov_homes", "ds_overview", "sum(at_risk)", "SUM(`at_risk`)", "Homes in an event's path", INT, 4, 2, 4),
        counter("ov_si", "ds_overview", "sum(at_risk_si)", "SUM(`at_risk_si`)", "Sum insured at risk", EUR, 8, 2, 4),
        point_map("ov_map", "ds_overview", "Events and the book",
                  [("Wildfire footprint", FIRE_FP), ("Flood footprint", FLOOD_FP), ("Storm footprint", STORM_FP),
                   ("Home in the path", RED), ("Home clear", "#94A3B8")], 5),
    ]),
]

dashboard = {
    "datasets": datasets,
    "pages": pages,
    "uiSettings": {"theme": {
        "canvasBackgroundColor": {"light": "#FCFCFC", "dark": "#1F272D"},
        "widgetBackgroundColor": {"light": "#FFFFFF", "dark": "#11171C"},
        "fontColor": {"light": "#11171C", "dark": "#E8ECF0"},
        "selectionColor": {"light": "#2563EB", "dark": "#8ACAFF"},
        "visualizationColors": [RED, "#F97316", "#0EA5E9", PURPLE, SLATE, "#10B981"],
        "widgetHeaderAlignment": "LEFT"}},
}

out = pathlib.Path(__file__).with_name("event_maps.lvdash.json")
out.write_text(json.dumps(dashboard, indent=1, ensure_ascii=False) + "\n")
print(f"wrote {out} — {len(datasets)} datasets, {len(pages)} pages")
