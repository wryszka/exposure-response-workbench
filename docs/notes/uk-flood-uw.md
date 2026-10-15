# T2 `uk-flood-uw` — UK EA flood feed, active zones (Contract A), underwriting link

## Contract A (live in `lr_dev_aws_us_catalog.exposure_response`, created first)
- **`mv_active_zones`** — `zone_id, event_id, peril_code, country_code, source, is_live, severity, active_from, footprint_wkt, ingested_at` (+ `event_name, source_detail`). One row per active event segment, all sources (SYNTHETIC, METEOALARM, FIRMS, EFFIS, EMS, EA). `zone_id = event_id#sha8(wkt)`. Severity: EA → parsed from `source_detail` ("Flood Warning", "Severe Flood Warning", "Flood Alert"); METEOALARM → "Weather warning"; else "Active event".
- **`fn_zone_check(p_lat DOUBLE, p_lon DOUBLE, p_buffer_m INT)`** → `TABLE(in_zone BOOLEAN, zone_id, event_id, peril_code, source, severity, distance_m DOUBLE, active_from TIMESTAMP)` — nearest active zone; always exactly ONE row (in_zone=false + nulls when nothing is active). True metres in **EPSG:3035** (LAEA Europe covers GB; ~1% scale distortion at UK latitudes, immaterial for a within-N-metres test — not 27700, so one CRS everywhere).
- Granted to `account users`, the exposure app SP, and the underwriting app SP `81589066-5ed3-49c3-8093-f723a711cc2d` (USE SCHEMA, SELECT `mv_active_zones` + `2_event_footprint`, EXECUTE `fn_zone_check`) — invoker rights.
- Notebook **`notebooks/51_active_zones.py`** (re)creates both + grants; widget `extra_grantees` for other SPs.

## UK Environment Agency flood feed — `notebooks/14_feed_ea_flood.py`
- Keyless EA flood-monitoring API: `/id/floods` (current warnings) → each warning's `floodArea` polygon (`/id/floodAreas/{id}/polygon`, GeoJSON). Uses `robust_get`, `geojson_to_wkt`, `land_raw`, `ensure_footprint_columns` from `10_feeds_common` (NOT edited).
- Takes severity 1–3 only (1 Severe Flood Warning, 2 Flood Warning, 3 Flood Alert; 4 "no longer in force" excluded), most severe then newest first, cap `max_warnings` (default 15). Large polygons thinned to ≤400 vertices/ring.
- Lands into `2_event_footprint` with `source='EA'`, FLOOD, GB, `detected_at` = EA timeRaised, `source_detail` = "EA <severity> (severity n) · area: <description> · <floodAreaID> · <EA area> · issued … UTC · Contains Environment Agency data © Crown copyright, Open Government Licence v3.0". Own idempotent replace (delete source='EA' then append live + frozen together — the shared helper writes a single is_live per call).
- Calls `dq_gate("EA", records)` if defined, else passes through.
- **For T1:** add `"EA"` to `SOURCES` in `10_feeds_common.py` (the adapter appends it at runtime meanwhile) and wire tasks into feeds/reset jobs: `14_feed_ea_flood` (needs `catalog`, `schema`) → then `51_active_zones` (needs `catalog`, `schema`, `warehouse_id`, `app_sp`, optional `extra_grantees=81589066-5ed3-49c3-8093-f723a711cc2d`).
- **Live right now (2026-10-15 evening): 0 active EA warnings** (the API listed 1 record, severity 4 "no longer in force"). The frozen hero carries the room; real warnings appear automatically whenever EA issues them.

## Frozen hero — always present
- `EVT_EA_SAMPLE_UK`, `is_live=false`, "EA Flood Warning — River Calder and Hebden Water at Hebden Bridge (frozen sample)", the **real EA polygon of flood area `123FWF312`**, issued now−2h (rolls on every run). Skipped automatically if a live warning exists for 123FWF312.
- **Hero UW risk:** `sub:900002` Calder Valley Fine Foods Ltd — Hebden Bridge Mill (production, £3.4m) + Mytholmroyd Works (£1.6m), postcode district **HX7** (centroid 53.742, −2.012, flood band High, River Calder) → `fn_zone_check` in_zone TRUE at 0 m.
- **Clear risk:** `sub:900001` Fenwick & Moss Homewares Ltd — WA14 (Altrincham, flood band Low) → in_zone FALSE, 45.2 km.

## Underwriting workbench (repo `underwriting-workbench`, branch `disaster-zone-check`, commit 1286dc2 — NOT merged to main)
- `notebooks/05f_disaster_zone_check.py`: **`underwriting_workbench.fn_disaster_zone_check(p_postcode_district)`** → `postcode_district, lat, lon, in_zone, decision (REFER|CLEAR|UNKNOWN), reason, zone_id, event_id, area_name, peril_code, source, severity, is_live, distance_m, active_from`. Same rule as `fn_zone_check` applied over the published `mv_active_zones` (a SQL TVF can't pass parameter-correlated values into another TVF — both `LATERAL` and scalar-subquery args are rejected); the notebook asserts parity with `fn_zone_check` for HX7 and WA14. Registers Referral Control rule **`CAT_ACTIVE_ZONE`** ("Property in an active catastrophe zone", risk_selection, refer, compliance-locked, workflow scope, source_check fn_disaster_zone_check) via MERGE **after** 00e/00f/07d, so the RNG-driven telemetry and hero outcomes are untouched. Grants to account users + UW app SP.
- App: `app/app.py` — `_zone_checks()` (per distinct district, `query_many`), `zone_check` added to `/api/submission/{sid}/panels`, new `GET /api/zone-check?district=`, and `/api/decision` returns `{"blocked": true, rule_id, reasons}` for **Quote** while any location is in an active zone (Refer/Decline unaffected; `zone_override:true` records an audited internal note). `app/dist/index.html` — "Disaster zone (live)" check card next to Accumulation, a red banner above the recommendation, "⛔ Quote blocked … → Refer instead / Override & record".
- Jobs: `05f` added to `underwriting_99_reset` (after `referral_detection`) and setup (after `referral_governance`).
- Deployed: bundle + app deployment `01f1c87f07cd1ea89abb093b57c18f51` (from the branch working tree).

## Verification (HTTP against the deployed UW app, 2026-10-15)
- `sub:900002` panels: HX7 **REFER** — "Property is in an active Environment Agency Flood Warning area — River Calder and Hebden Water at Hebden Bridge, issued 15 Oct 2026 07:54 UTC (frozen demo sample); refer to underwriter. Cover cannot be bound inside an active warning area." HD1/HX6/PR1/WS2 CLEAR. Recommendation still **refer** (unchanged).
- `sub:900001` panels: WA14 **CLEAR** (45.2 km); recommendation still **quote** (zero-touch unaffected).
- `POST /api/decision {sid: sub:900002, action: quote}` → `blocked: true, rule_id CAT_ACTIVE_ZONE, districts [HX7]`, nothing recorded.
- Referral Control rulebook shows CAT_ACTIVE_ZONE; findings endpoint OK; smoke invariants: 24 current rules (20–30), one current version each.
- Exposure: `mv_active_zones` EA row present; `fn_zone_check(53.742,−2.012,250)` → TRUE 0 m; `(53.387,−2.349,250)` → FALSE 45,200 m. `fn_events` heroes unchanged (Var 59, Alpine 122).

## Gotchas
- SQL table functions: no correlated `LATERAL` into another TVF, and no parameter-correlated scalar subqueries as TVF arguments (`UNSUPPORTED_CORRELATED_SCALAR_SUBQUERY`) — apply the rule over the published view instead.
- `replace_events_from_source` asserts the source is in `SOURCES` and writes one is_live per call — EA does its own replace.
- The frozen Hebden Bridge warning is permanent in the UW demo while this workbench is deployed (labelled "frozen demo sample" in every reason). The UW supervisor agent doesn't yet have the zone check as a tool (follow-up).
