# Demo Q&A — Exposure & Event Response (Bricksurance SE)

> Tab 2 of the run. Every question the demo can't answer live — incl. the incumbent champion's — documented and answered, sourced from live data, cross-referenced from the run-sheet beats. Populated as the build lands.

## Story & process
1. **How is this done today?** Reactive and manual: when a fire is reported someone hand-extracts policies from the source system, pulls the fire outline separately, and eyeballs the overlay. No standing process, no automatic alert, easy to miss a cross-border event.

## Data & feeds
2. **Is the hazard data real?** Yes where a free live feed exists — NASA FIRMS/EFFIS (fire), MeteoAlarm/NOAA (wind), GloFAS/EFAS/Copernicus EMS (flood). A frozen snapshot backs the room for reproducibility; synthetic fill covers gaps (mainly the flood live-event layer). The property book is synthetic — no customer PII.
3. **Why not just call the fire API from a dashboard?** Those feeds rate-limit and time out under ad-hoc polling. The platform ingests them robustly (scheduled + Autoloader + backoff + cache), so the map is always there when an event breaks.

## Platform (how it works)
4. **How is "within 200 m" computed?** Governed geospatial functions (`ST_DWithin`) on the property coordinates vs the event footprint, reprojected to a metric CRS (EPSG:3035) so the 50/100/200 m bands are true metres. Inside the footprint counts as 0 m.
5. **Is the maths in the app?** No — it's in governed UC functions (`fn_exposure_in_footprint`, `fn_event_exposure_summary`, `fn_events`) the app calls; the same functions are callable by agents and notebooks. The map, KPIs and table are all the one governed result.
6. **A flood is two polygons — how is it one number?** The function unions all segments of an event (`ST_Union_Agg`) into one footprint, so a multi-segment cross-border event returns its whole exposure as a single event — that's why the Alpine Flood shows IT and AT together.
7. **Why does the map have no base map / streets?** It's a strict self-contained page (no external tile servers) — the footprint and properties are drawn as inline SVG from the governed coordinates. A basemap tile layer is a later polish, not load-bearing.

## Incumbent champion (the skeptic)
8. **Our cat model already tells us this.** This works *alongside* the cat models — it brings their view together with the live event and the actual book, refreshed in hours, and shows who to tell. It's the operational layer, not a replacement model.
