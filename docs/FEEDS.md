# Hazard feeds — sources, access, status

The workbench overlays **live** hazard footprints on the insured book, with a **frozen** layer so a demo room is
reproducible. Live events land in `2_event_footprint` alongside the frozen synthetic seeds, tagged `source` and
`is_live`, and flow through the **same** governed `fn_exposure_in_footprint`. Adapters use one robust fetch
(timeout + exponential backoff + retry) — this is the answer to the customer's real pain: their analyst hit
API timeouts/blocking pulling these by hand.

Refresh job: `exposure_10_refresh_feeds` (serverless; daily, paused in dev; run on demand). Notebooks
`10_feeds_common` (shared helpers) → `11_feed_wildfire`, `12_feed_windstorm`, `13_feed_flood`.

| Peril | Source | Access | Auth | Format | Cadence | Europe | Licence / attribution | Status |
|---|---|---|---|---|---|---|---|---|
| Windstorm / severe | **MeteoAlarm** (38 EU met services) | `feeds.meteoalarm.org/api/v1/warnings/feeds-{country}` | **none** | CAP-JSON | ~hourly | ✅ all | Free; © national met services | **LIVE now** |
| — region → polygon | Eurostat **GISCO** NUTS3 | `gisco-services.ec.europa.eu/.../NUTS_RG_20M_2013_4326_LEVL_3.geojson` | none | GeoJSON | static | ✅ | © EuroGeographics | **LIVE** (cached to Volume) |
| Wildfire | **NASA FIRMS** active fire (VIIRS 375 m) | `firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/...` | **free MAP_KEY** | CSV | NRT ~3 h | ✅ global | NASA EOSDIS/LANCE (attribute) | adapter live; **needs key** → frozen sample |
| Wildfire | **EFFIS** (Copernicus) perimeters | EFFIS portal / WFS | free | GeoJSON/GPKG | daily | ✅ | Copernicus | documented; not wired |
| Flood (forecast) | **GloFAS / EFAS** (Copernicus) | CDS API | **free CDS key** | NetCDF | 6–12 h | ✅ | Copernicus (attribute) | adapter wired to secret; **needs key** |
| Flood (event extent) | **Copernicus EMS** Rapid Mapping | activation downloads | none (past) | GPKG/SHP | event | ✅ | Copernicus (CC-BY) | frozen real-shape sample |

## Credentials to obtain (free — light up the remaining live feeds, no code change)

1. **NASA FIRMS MAP_KEY** — instant, free: <https://firms.modaps.eosdis.nasa.gov/api/map_key/>
   Put it in secret scope `exposure_response`, key `MAP_KEY` (or env `FIRMS_MAP_KEY`). `11_feed_wildfire` then
   ingests live VIIRS hotspots for the Mediterranean fire belt and clusters them into a footprint.
2. **Copernicus CDS API key** — free account: <https://cds.climate.copernicus.eu/>
   Put it in secret scope `exposure_response`, key `CDS_API_KEY` (or env `CDS_API_KEY`). Enables the GloFAS/EFAS
   live flood-forecast path in `13_feed_flood`.

Create the scope + keys:
```bash
databricks secrets create-scope exposure_response -p DEV        # once
databricks secrets put-secret exposure_response MAP_KEY -p DEV
databricks secrets put-secret exposure_response CDS_API_KEY -p DEV
```

## Known next step
Non-France MeteoAlarm feeds warn by **EMMA_ID** (MeteoAlarm's own regions), not NUTS3, so those pulls are
landed to bronze (`1_hazard_feed_raw`) but not yet turned into footprints. Wiring the EMMA_ID → boundary lookup
extends the live windstorm layer to IT/AT/ES/DE. France warns by **NUTS3** → fully live today.
