# Demo Q&A — Exposure & Event Response (Bricksurance SE)

> Tab 2 of the run. Every question the demo can't answer live — incl. the incumbent champion's — documented and answered, sourced from live data, cross-referenced from the run-sheet beats. Populated as the build lands.

## Story & process
1. **How is this done today?** Reactive and manual: when a fire is reported someone hand-extracts policies from the source system, pulls the fire outline separately, and eyeballs the overlay. No standing process, no automatic alert, easy to miss a cross-border event.

## Data & feeds
2. **Is the hazard data real?** Yes where a free live feed exists — NASA FIRMS/EFFIS (fire), MeteoAlarm/NOAA (wind), GloFAS/EFAS/Copernicus EMS (flood). A frozen snapshot backs the room for reproducibility; synthetic fill covers gaps (mainly the flood live-event layer). The property book is synthetic — no customer PII.
3. **Why not just call the fire API from a dashboard?** Those feeds rate-limit and time out under ad-hoc polling. The platform ingests them robustly (scheduled + Autoloader + backoff + cache), so the map is always there when an event breaks.

## Platform (how it works)
4. **How is "within 200 m" computed?** Governed geospatial functions (`ST_DWithin`) on the property coordinates vs the event footprint, reprojected to a metric CRS (EPSG:3035) so the 50/100/200 m bands are true metres.
5. **Is the maths in the app?** No — it's in governed UC functions the app calls; the same functions are callable by agents and notebooks.

## Incumbent champion (the skeptic)
6. **Our cat model already tells us this.** This works *alongside* the cat models — it brings their view together with the live event and the actual book, refreshed in hours, and shows who to tell. It's the operational layer, not a replacement model.
