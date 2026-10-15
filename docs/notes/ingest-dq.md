# T1 `ingest-dq` — data-quality gate + sources catalogue (Contract B)

Branch `ingest-dq`. Verified 2026-10-15 on DEV (`lr_dev_aws_us_catalog.exposure_response`) with one-off serverless runs from
`/Workspace/Users/laurence.ryszka@databricks.com/erw-staging/ingest-dq/` (no bundle deploy).

## What was built
- **`dq_gate(source_id, records, checks=None, *, frozen, pulled_at, run_id, note, reason_detail)`** in
  `notebooks/10_feeds_common.py` → `(accepted_records, decision)`. Also `dq_unreachable(source_id, note)` for a feed with
  no frozen fallback that can't be reached, and `ensure_dq_tables()`. Helper names are `_dq_*`-prefixed (EFFIS defines its
  own `_centroid`, which shadowed the first version through `%run`).
- `SOURCES` now includes `EA` and `GDACS`.
- Every adapter routes through the gate before writing `2_event_footprint`:
  - `12` MeteoAlarm: gate → replace only if ACCEPTED. If all five feeds are unreachable → `dq_unreachable`, previous
    warnings kept. Overseas French departments (FR9*) are excluded. An empty batch ("no active warnings") is ACCEPTED.
  - `11` FIRMS, `11b` EFFIS: a live batch is gated (REJECTED → previous kept). The frozen fallback is gated with
    `frozen=True` → FALLBACK_FROZEN.
  - `13` EMS: frozen footprint gated → FALLBACK_FROZEN.
- **`notebooks/15_dq_and_sources.py`:**
  - Builds `ref_feed_source` (9 sources: METEOALARM, EA, GDACS, FIRMS, EFFIS, EMS, GLOFAS, STREAM heartbeat, SYNTHETIC
    scenarios).
  - Re-seeds the rejected batch.
  - Runs post-landing checks on GDACS (landed by `40`) and on the scenario events (loaded by `01`). Both are idempotent per
    batch, keyed on the batch timestamp.
  - Creates the view `gov_source_catalog` and the functions `fn_source_status()`, `fn_dq_recent(p_days INT)` and
    `fn_dq_batch_checks(p_run_id STRING)`. The last one is extra, for "why was it rejected" drill-down.
  - Grants SELECT / EXECUTE to the app SP and `account users`.

## Objects (Contract B)
- `ref_dq_threshold` (source_id, check_name, threshold, comparator, description). Seeded by MERGE; never overwrites an
  edited threshold.
- `gov_feed_dq_log`: one row per check per batch.
- `1_feed_quarantine`: rejected batches.
- `ref_feed_source`
- `gov_source_catalog`: contract columns plus `declared_mode`, `url`, `last_reason`, `last_batch_at`,
  `last_failed_checks`, `items_in_use`, `sort_order`.
  - `mode` = FROZEN SAMPLE when the last decision is FALLBACK_FROZEN or only non-live rows are in use.
  - `last_decision` falls back to NOT YET INGESTED / NOT CONFIGURED / STREAMING / IDLE.

## Checks and thresholds (defaults → per-source overrides)

| check | rule | default | overrides |
|---|---|---|---|
| record_count | ≥ | 1 | METEOALARM, EA = 0 (empty = no active warnings) |
| pct_null_geometry | ≤ | 2% | — |
| pct_invalid_geometry | ≤ | 1% | — |
| pct_outside_bbox (Europe + UK: lon −25…45, lat 34…72) | ≤ | 5% | — |
| pct_duplicate | ≤ | 10% | — |
| freshness_hours (newest record) | ≤ | 72 | METEOALARM / EA 24, EFFIS 240, EMS 720, GLOFAS 48; skipped for frozen samples and SYNTHETIC |

Geometry checks only apply when records carry geometry (not on GDACS text items).

## Seeded rejected batch (re-created by every run of 15, so every reset)
- **Run** `SEED-METEOALARM-<yyyymmddHHMM>`, pulled at the top of the hour 3 hours before the run.
- **Batch:** 21 French warning regions; 8 of them have latitude and longitude reversed.
- **Result:** `pct_outside_bbox` = 38.1% against a 5% limit → **REJECTED** and quarantined. The other 5 checks pass.
- **Reason (verbatim):** "Rejected: 38% of records (8 of 21) fall outside Europe and the UK (limit 5%) — the coordinates
  look swapped (latitude and longitude reversed). Batch quarantined; the previous accepted MeteoAlarm data is still in use."
- The gate never writes footprints itself, so the seed can't touch the book.

## Verification (2026-10-15 ~10:10 UTC)
- **`fn_source_status()`:**
  - METEOALARM: REAL-TIME, ACCEPTED, 0 records (no French warnings tonight).
  - EA: FROZEN SAMPLE, ACCEPTED, 0 live warnings, 1 frozen event in use.
  - GDACS: REAL-TIME, ACCEPTED, 12 items, live.
  - FIRMS, EFFIS, EMS: FROZEN SAMPLE, FALLBACK_FROZEN.
  - GLOFAS: NOT CONFIGURED.
  - STREAM: IDLE (9 records; the stream job wasn't running).
  - SYNTHETIC: ACCEPTED, 6 rows across 5 events.
- **`fn_dq_recent(30)`:** METEOALARM 1 ACCEPTED and 1 REJECTED; FIRMS, EFFIS and EMS FALLBACK_FROZEN; EA, GDACS and
  SYNTHETIC ACCEPTED.
- **Quarantine:** 1 row (the seed).
- **`fn_dq_batch_checks(seed)`:** pct_outside_bbox 38.1 > 5 FAIL; the other checks pass.
- **Hero numbers unchanged** (`fn_events(200)`):

  | Event | Homes |
  |---|---|
  | Var | 59 |
  | Alpine | 122 (IT 69 / AT 53) |
  | Esterel | 11 |
  | Celine | 1,250 |
  | Nord | 260 |
  | FIRMS | 90 |
  | EFFIS | 90 |
  | EMS | 129 |
  | EA (EU book) | 0 |
- **Integration test with T2:** T2's `14_feed_ea_flood.py`, taken from branch `uk-flood-uw` and *not committed here*, ran
  against this gate. Its `if 'dq_gate' in globals()` hook logged an EA batch.

## Job wiring (owned here)
- **`feeds_job.yml`:** 12 MeteoAlarm → 11 FIRMS → 11b EFFIS → 13 EMS → **14 EA** → **51 active_zones** →
  **15 dq_and_sources**.
- **`reset_job.yml`:** snapshot → 00 → 01 → 12 → 11 → 11b → 13 → **14 EA** → **51 active_zones** → 20 → 40 →
  **15 dq_and_sources** → 50 → 60 → 30 → 07 → finish.
  - 15 runs after 40, so it checks the fresh GDACS batch, and it re-seeds the rejection.
- Task 51 gets `extra_grantees: 81589066-5ed3-49c3-8093-f723a711cc2d` (the underwriting app SP) in both jobs.
- 14 and 51 get catalog / schema / warehouse_id / app_sp.

## For the merge (main session)
- **T2's EA adapter:**
  1. It adds the frozen Hebden Bridge sample *after* the gate. Gate it as well, e.g.
     `dq_gate("EA", [hero], frozen=True, note="no live EA warning covers the hero area")`, so EA logs FALLBACK_FROZEN.
     The catalogue already shows `mode=FROZEN SAMPLE` for it.
  2. It deletes all EA rows before writing. If the gate returns REJECTED, skip the delete so the previous data is kept.
  3. Its stopgap `SOURCES += ("EA",)` becomes a no-op once this branch merges.
- **Shared-table side effects of the verification runs:**
  - The live MeteoAlarm run removed the 6 Oct MeteoAlarm events (no French warnings today).
  - FIRMS, EFFIS and EMS frozen samples are now dated relative to 15 Oct, so they no longer line up with the 6 Oct news
    item.
  - The morning reset (`exposure_99_reset`) re-aligns everything.
- **CTE name gotcha:** `stream` is a reserved word in Databricks SQL. `LEFT JOIN stream st` parsed as a streaming read
  of a table named `st`, so the CTEs are now `news_c` / `heartbeat_c`.
