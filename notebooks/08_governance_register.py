# Databricks notebook source
# MAGIC %md
# MAGIC # 08 · Governance register — what we record, and what may need reporting
# MAGIC
# MAGIC Two governed surfaces over the append-only spine:
# MAGIC - **`ref_record_register`** + **`fn_record_register()`** — every record the workbench keeps: what is recorded, how it
# MAGIC   is kept (append-only Delta table, Delta history, MLflow tracing, Jobs run history, Unity Catalog audit and lineage),
# MAGIC   where it lives, who can see it, how long it is kept, and which rules it evidences — with a **live** count and
# MAGIC   "last written" time for every record that is a table.
# MAGIC - **`fn_reportable(p_days)`** — the queue of things that **may need reporting or review**, derived by deterministic
# MAGIC   rules from those records (risk appetite and the reinsurance programme, refused new business and overrides,
# MAGIC   rejected data, ICT incidents, undelivered alerts, cross-border events, events declared by a person). Each item
# MAGIC   says what happened, why it matters, who to tell and the rule behind it. A person acknowledges or marks it
# MAGIC   reported; that is appended to **`gov_report_action`** — nothing is overwritten.
# MAGIC
# MAGIC Plain SQL (no `ST_`), created on the SQL warehouse like the other governed functions. Re-run by the reset.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "fc3ac835-8393-4ea6-8d2b-180096836d7b")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
fqn = f"{catalog}.{schema}"
GRANTEES = [g for g in [app_sp, "account users"] if g]

import time
from databricks.sdk import WorkspaceClient
_w = WorkspaceClient()


def run_wh(stmt, label="", max_wait=600):
    """Execute on the SQL warehouse and poll to completion."""
    r = _w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=warehouse_id, catalog=catalog, schema=schema, wait_timeout="50s")
    waited = 0
    while r.status.state.value in ("PENDING", "RUNNING") and waited < max_wait:
        time.sleep(3); waited += 3
        r = _w.statement_execution.get_statement(r.statement_id)
    if r.status.state.value != "SUCCEEDED":
        raise RuntimeError(f"{label}: {r.status.state.value} — {r.status.error.message if r.status.error else '?'}")
    return r


def wh_rows(stmt, label=""):
    r = run_wh(stmt, label)
    if r.result is None or r.result.data_array is None:
        return []
    cols = [c.name for c in r.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in r.result.data_array]


print("Target:", fqn)

# COMMAND ----------
# MAGIC %md ## The tables the rules read — make sure they exist (a fresh workspace may not have every log yet)

# COMMAND ----------

for ddl in [
    f"""CREATE TABLE IF NOT EXISTS {fqn}.gov_report_action (
          item_id STRING, action STRING, note STRING, actor STRING, acted_at TIMESTAMP)
        COMMENT 'Append-only: a person acknowledged a reportable item or marked it reported (governance screen).'""",
    f"""CREATE TABLE IF NOT EXISTS {fqn}.gov_bind_decision (
          quote_id STRING, applicant STRING, object_ref STRING, latitude DOUBLE, longitude DOUBLE, t_index INT,
          decision STRING, reason STRING, active_event_id STRING, distance_m DOUBLE, as_of STRING,
          decided_at TIMESTAMP, decided_by STRING, overridden BOOLEAN)""",
    f"""CREATE TABLE IF NOT EXISTS {fqn}.gov_news_decision (
          signal_id STRING, action STRING, event_id STRING, decided_by STRING, decided_at TIMESTAMP,
          confidence DOUBLE, evidence STRING)""",
    f"""CREATE TABLE IF NOT EXISTS {fqn}.gov_alert_delivery (
          run_id STRING, alert_id STRING, channel STRING, recipients STRING, n_events INT, alert_state STRING,
          delivered_at TIMESTAMP)""",
    f"""CREATE TABLE IF NOT EXISTS {fqn}.cache_agent_responses (
          cache_key STRING, question STRING, response STRING, created_ts TIMESTAMP)""",
]:
    run_wh(ddl, "ensure table")
print("log tables present")

# COMMAND ----------
# MAGIC %md ## Risk appetite — the per-event limit the escalation rule uses (governed, visible, tunable)

# COMMAND ----------

spark.createDataFrame(
    [("per_event_modelled_gross_loss", 100_000_000.0,
      "Maximum modelled gross loss from a single catastrophe event before it must be escalated to the risk "
      "function (illustrative Bricksurance SE appetite; set by the board in a real firm).")],
    "metric STRING, limit_eur DOUBLE, description STRING",
).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_risk_appetite")
print("ref_risk_appetite ✓")

# COMMAND ----------
# MAGIC %md ## The register — every record the workbench keeps

# COMMAND ----------

LAW_SII = "Solvency II (governance, risk management, ORSA, data quality)"
LAW_DORA = "DORA (ICT incidents and resilience)"
LAW_GDPR = "GDPR (location data)"
LAW_IDD = "IDD (product oversight, fair treatment)"
LAW_AI = "EU AI Act (human oversight, logging)"
REG = [
    # record_key, sort, name, what_is_recorded, how_kept, table_or_source, who_can_see, retention_note, supports
    ("feed_pulls", 1, "Feed pulls",
     "Every pull from every hazard feed: which feed, when, from where, how many records, a sample of the payload.",
     "Append-only Delta table", "1_hazard_feed_raw",
     "Exposure and data team; auditors (read-only Unity Catalog grant)",
     "Kept indefinitely; Delta history keeps earlier versions for time travel.", f"{LAW_SII}; {LAW_DORA}"),
    ("dq_decisions", 2, "Data-quality decisions",
     "For every batch, every quality check: what was measured, the threshold, pass or fail, and the decision (accepted, rejected, frozen sample) with the reason.",
     "Append-only Delta table", "gov_feed_dq_log",
     "Data owner, exposure team, auditors", "Kept indefinitely (one row per check per batch).", LAW_SII),
    ("quarantine", 3, "Quarantined batches",
     "Batches that failed the gate, kept untouched for investigation — they never reach the book.",
     "Append-only Delta table", "1_feed_quarantine",
     "Data owner, auditors", "Kept until investigated; never edited.", f"{LAW_SII}; {LAW_DORA}"),
    ("event_footprints", 4, "Event footprints and provenance",
     "Every event footprint in use, which feed it came from, whether it is live or a frozen sample, and when it arrived.",
     "Delta table + provenance view; Unity Catalog lineage", "2_event_footprint · gov_data_provenance",
     "Exposure team, underwriting (published active zones), auditors",
     "Current state in the table; every earlier state in Delta history.", LAW_SII),
    ("exposure_snapshots", 5, "Exposure snapshots",
     "Which insured homes were in each event footprint at each sweep, by distance band and sum insured — what we knew, when.",
     "Append-only Delta table", "gov_exposure_snapshot · gov_exposure_history",
     "Exposure team, risk function, auditors", "Kept indefinitely; one snapshot per event per sweep.",
     f"{LAW_SII}; Lloyd’s exposure-management practice"),
    ("alert_decisions", 6, "Alert decisions",
     "For every event at every sweep: did it cross its threshold, the numbers in the alert, the countries, the intended recipients and the exact message.",
     "Append-only Delta table", "gov_alert_dispatch · gov_alert_audit",
     "Exposure team, claims, underwriting, auditors", "Kept indefinitely, including the full message text.",
     f"{LAW_SII}; {LAW_DORA}"),
    ("alert_deliveries", 7, "Alert email deliveries",
     "Proof the alert email went out: which sweep, which Databricks alert, to whom, when — and any sweep whose email did not go out.",
     "Append-only Delta table (+ Databricks SQL alert history)", "gov_alert_delivery",
     "Exposure team, IT, auditors", "Kept indefinitely.", f"{LAW_DORA}; internal alerting policy"),
    ("news_decisions", 8, "Human decisions on news signals",
     "When a person promoted a press or disaster-feed signal to an event: who, when, the confidence and the evidence they saw.",
     "Append-only Delta table", "gov_news_decision",
     "Exposure team, auditors", "Kept indefinitely; the demo reset clears demo clicks only.", f"{LAW_AI}; {LAW_SII}"),
    ("bind_decisions", 9, "Cover decisions and overrides",
     "Every new-business decision checked against active zones: the applicant, the address, bound / refused / referred, the reason, the active event, and whether an underwriter overrode it.",
     "Append-only Delta table", "gov_bind_decision",
     "Underwriting, conduct and compliance, auditors", "Kept indefinitely; the demo reset clears demo clicks only.",
     f"{LAW_IDD}; UK Consumer Duty (UK risks); {LAW_GDPR}"),
    ("agent_conversations", 10, "AI agent conversations",
     "Each question to the agent team, which specialist answered, why it was routed there, the governed functions it called and the answer.",
     "MLflow tracing on the served agent + response cache table", "exposure-response-agent (MLflow traces) · cache_agent_responses",
     "Model owner, risk function, auditors", "Traces kept in the MLflow experiment; cache kept until cleared.",
     LAW_AI),
    ("job_runs", 11, "Scheduled runs and resets",
     "Every run of every job — feeds, alert sweeps, the reset — who started it, when, how long, and whether it succeeded.",
     "Databricks Jobs run history", "Jobs: exposure_10_refresh_feeds · exposure_30_alerts · exposure_99_reset",
     "Platform and data team", "Kept by the Jobs service (run history, typically 60 days).", LAW_DORA),
    ("access_audit", 12, "Who accessed what",
     "Every read and change to these tables and functions, every grant and permission change, by person or service.",
     "Unity Catalog audit log (system tables)", "system.access.audit · Unity Catalog lineage",
     "Security and platform team, auditors", "Kept in the platform audit log per the account’s system-table retention.",
     f"{LAW_GDPR}; {LAW_DORA}; {LAW_SII}"),
    ("report_actions", 13, "Reporting actions",
     "When a person acknowledged an item on the reporting queue or marked it reported, with their note.",
     "Append-only Delta table", "gov_report_action",
     "Risk function, compliance, auditors", "Kept indefinitely; the demo reset clears demo clicks only.",
     f"{LAW_SII}; {LAW_DORA}"),
]
spark.createDataFrame(
    REG, "record_key STRING, sort_order INT, name STRING, what_is_recorded STRING, how_kept STRING, "
         "table_or_source STRING, who_can_see STRING, retention_note STRING, supports STRING",
).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{fqn}.ref_record_register")
print(f"ref_record_register ✓ ({len(REG)} records)")

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_record_register()
RETURNS TABLE (record_key STRING, sort_order INT, name STRING, what_is_recorded STRING, how_kept STRING,
               table_or_source STRING, who_can_see STRING, retention_note STRING, supports STRING,
               n_records BIGINT, last_written TIMESTAMP, live_note STRING)
COMMENT 'The governance register: every record the Exposure & Event Response workbench keeps — what is recorded, how it is kept (append-only Delta table, Delta history, MLflow tracing, Jobs run history, Unity Catalog audit), where it lives, who can see it, retention, and which rules it evidences — with a live record count and last-written time for every record that is a table. Records kept outside a table (MLflow traces, Jobs run history, the platform audit log) say where to find them in live_note.'
RETURN
  WITH stats AS (
    SELECT 'feed_pulls' AS k, count(*) AS n, max(pulled_at) AS ts FROM {fqn}.`1_hazard_feed_raw`
    UNION ALL SELECT 'dq_decisions', count(DISTINCT run_id), max(pulled_at) FROM {fqn}.gov_feed_dq_log
    UNION ALL SELECT 'quarantine', count(*), max(pulled_at) FROM {fqn}.`1_feed_quarantine`
    UNION ALL SELECT 'event_footprints', count(DISTINCT event_id), max(ingested_at) FROM {fqn}.`2_event_footprint`
    UNION ALL SELECT 'exposure_snapshots', count(DISTINCT concat(run_id, '|', event_id)), max(as_of) FROM {fqn}.gov_exposure_snapshot
    UNION ALL SELECT 'alert_decisions', count(*), max(sent_at) FROM {fqn}.gov_alert_dispatch
    UNION ALL SELECT 'alert_deliveries', count(*), max(delivered_at) FROM {fqn}.gov_alert_delivery
    UNION ALL SELECT 'news_decisions', count(*), max(decided_at) FROM {fqn}.gov_news_decision
    UNION ALL SELECT 'bind_decisions', count(*), max(decided_at) FROM {fqn}.gov_bind_decision
    UNION ALL SELECT 'agent_conversations', count(*), max(created_ts) FROM {fqn}.cache_agent_responses
    UNION ALL SELECT 'report_actions', count(*), max(acted_at) FROM {fqn}.gov_report_action
  )
  SELECT r.record_key, r.sort_order, r.name, r.what_is_recorded, r.how_kept, r.table_or_source, r.who_can_see,
         r.retention_note, r.supports, s.n AS n_records, s.ts AS last_written,
         CASE r.record_key
           WHEN 'agent_conversations' THEN 'Count = cached answers; every live call is also traced in MLflow'
           WHEN 'job_runs' THEN 'In the Databricks Jobs run history'
           WHEN 'access_audit' THEN 'In the platform audit log (system.access.audit)'
           ELSE NULL END AS live_note
  FROM {fqn}.ref_record_register r
  LEFT JOIN stats s ON s.k = r.record_key
  ORDER BY r.sort_order
""", "fn_record_register")
print("fn_record_register ✓")

# COMMAND ----------
# MAGIC %md ## The reporting queue — deterministic rules over the records
# MAGIC Rule set (each item: what happened, why it matters, who to tell, rule basis — law vs practice labelled):
# MAGIC 1. **Event above the reinsurance programme** (modelled gross loss > top of the Cat XL tower) — HIGH.
# MAGIC 2. **Event above risk appetite** (modelled gross loss ≥ the per-event limit in `ref_risk_appetite`) — MEDIUM.
# MAGIC 3. **Reinsurance recovery expected** (a layer attaches, below appetite) — LOW: send event loss advice.
# MAGIC 4. **One event across borders** (2+ countries) — LOW: group exposure management + country leads.
# MAGIC 5. **Rejected data batch** — MEDIUM data-quality incident.
# MAGIC 6. **Live data service unreachable** (frozen sample used) — MEDIUM ICT incident (classified minor unless it persists).
# MAGIC 7. **Live feed not switched on** (credential missing) — LOW configuration gap.
# MAGIC 8. **Alert breached but email not delivered** (an alert sweep whose Databricks alert did not send) — HIGH.
# MAGIC 9. **New business refused in an active zone** — MEDIUM conduct record; **underwriter override** — HIGH.
# MAGIC 10. **Event declared by a person from a press report** — LOW.
# MAGIC Frozen corroborating feed samples (FIRMS / EFFIS / EMS copies of a scenario event) don't raise their own
# MAGIC catastrophe items — the scenario or live event does. The silent morning reset never raises rule 8 (it
# MAGIC records no delivery attempt).

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_reportable(p_days INT)
RETURNS TABLE (item_id STRING, detected_at TIMESTAMP, severity STRING, category STRING, title STRING,
               what_happened STRING, why_it_matters STRING, who_to_tell STRING, rule_basis STRING,
               source_record STRING, status STRING, status_note STRING, status_by STRING, status_at TIMESTAMP)
COMMENT 'Things that may need reporting or review, derived by deterministic rules from the append-only records: events above the reinsurance programme or the risk appetite, expected reinsurance recoveries (loss advice), cross-border events, rejected data batches, unreachable live feeds (ICT incidents), alerts that breached but were not delivered, new business refused in an active zone and underwriter overrides, and events declared by a person from a press report. Each item: severity (HIGH / MEDIUM / LOW), what happened, why it matters, who to tell, the rule basis (regulation, contract or practice — labelled), the source record, and its status from gov_report_action (OPEN / ACKNOWLEDGED / REPORTED). p_days limits log-based items to the last p_days days.'
RETURN
  WITH latest AS (SELECT max(run_id) AS rid FROM {fqn}.gov_alert_dispatch),
  tower AS (SELECT max(attachment_eur + limit_eur) AS top_eur, min(attachment_eur) AS retention_eur
            FROM {fqn}.`3_treaty_layer`),
  appetite AS (SELECT max(limit_eur) AS lim FROM {fqn}.ref_risk_appetite
               WHERE metric = 'per_event_modelled_gross_loss'),
  ev AS (SELECT event_id, max(source) AS source, bool_or(is_live) AS is_live
         FROM {fqn}.`2_event_footprint` GROUP BY event_id),
  sweep AS (
    SELECT d.* FROM {fqn}.gov_alert_dispatch d
    JOIN latest l ON d.run_id = l.rid
    JOIN ev e ON e.event_id = d.event_id
    WHERE NOT (e.source IN ('FIRMS', 'EFFIS', 'EMS') AND NOT e.is_live)
  ),
  src AS (SELECT source_id, source_name FROM {fqn}.ref_feed_source),
  batches AS (
    SELECT run_id, source_id, max(pulled_at) AS pulled_at, max(decision) AS decision, max(reason) AS reason,
           max(CASE WHEN check_name = 'feed_reachable' AND NOT passed THEN 1 ELSE 0 END) AS unreachable
    FROM {fqn}.gov_feed_dq_log
    WHERE pulled_at >= current_timestamp() - make_interval(0, 0, 0, p_days)
    GROUP BY run_id, source_id
  ),
  last_batch AS (
    SELECT * FROM (SELECT b.*, row_number() OVER (PARTITION BY source_id ORDER BY pulled_at DESC) AS rn
                   FROM batches b) WHERE rn = 1
  ),
  items AS (
    -- 1–3 · catastrophe events from the latest sweep: one item per event, at its highest severity
    SELECT concat('CAT|', s.event_id) AS k, s.sent_at AS detected_at,
           CASE WHEN s.gross_eur > t.top_eur THEN 'HIGH' WHEN s.gross_eur >= a.lim THEN 'MEDIUM' ELSE 'LOW' END AS severity,
           CASE WHEN s.gross_eur > t.top_eur THEN 'Reinsurance programme exceeded'
                WHEN s.gross_eur >= a.lim THEN 'Above risk appetite'
                ELSE 'Reinsurance recovery expected' END AS category,
           concat(s.event_name, CASE WHEN s.gross_eur > t.top_eur THEN ' — modelled loss above the reinsurance programme'
                                     WHEN s.gross_eur >= a.lim THEN ' — above our per-event risk appetite'
                                     ELSE ' — our reinsurance will pay out' END) AS title,
           concat('Modelled gross loss EUR ', format_number(s.gross_eur / 1e6, 1), 'm on ', s.threatened_count,
                  ' insured homes. Reinsurance recovers EUR ', format_number(s.ceded_eur / 1e6, 1), 'm; we keep EUR ',
                  format_number(s.net_eur / 1e6, 1), 'm.',
                  CASE WHEN s.gross_eur > t.top_eur THEN concat(' The loss is above the top of the programme (EUR ',
                       format_number(t.top_eur / 1e6, 1), 'm), so everything above it stays with us.')
                       WHEN s.gross_eur >= a.lim THEN concat(' The per-event appetite limit is EUR ',
                       format_number(a.lim / 1e6, 1), 'm.') ELSE '' END) AS what_happened,
           CASE WHEN s.gross_eur > t.top_eur THEN 'What we keep is far above our retention — a material change to the risk profile the risk committee should see now, not at the next quarterly review. Reinsurers on every layer need notice.'
                WHEN s.gross_eur >= a.lim THEN 'An event above the per-event appetite has to be escalated to the risk function and recorded against the appetite framework.'
                ELSE 'Reinsurers expect early notice of events likely to reach their layer, so recoveries are not delayed.' END AS why_it_matters,
           CASE WHEN s.gross_eur > t.top_eur THEN 'Chief Risk Officer and the risk committee (for the ORSA); reinsurance team, who send the event loss advice to every layer'
                WHEN s.gross_eur >= a.lim THEN 'Chief Risk Officer / risk function; reinsurance team (event loss advice)'
                ELSE 'Reinsurance team — send event loss advice to the reinsurers on the layer' END AS who_to_tell,
           CASE WHEN s.gross_eur >= a.lim THEN 'Solvency II risk management and ORSA (Directive 2009/138/EC); the firm’s risk appetite framework (internal policy); the treaty’s loss-notification clause (contract)'
                ELSE 'The treaty’s loss-notification clause (a contract term, not regulation)' END AS rule_basis,
           concat('gov_alert_dispatch · ', s.dispatch_id) AS source_record
    FROM sweep s CROSS JOIN tower t CROSS JOIN appetite a
    WHERE s.ceded_eur > 0 OR s.gross_eur >= a.lim
    UNION ALL
    -- 4 · one event across borders
    SELECT concat('XB|', s.event_id), s.sent_at, 'LOW', 'One event across borders',
           concat(s.event_name, ' — one event across ', s.countries),
           concat(s.threatened_count, ' insured homes in ', s.n_countries, ' countries (', s.countries,
                  ') are in the same event.'),
           'Country teams each see their own part. Group exposure has to see it as one event, or the accumulation is understated.',
           'Group exposure management and the exposure lead in each country',
           'Group-wide accumulation control (market practice, e.g. Lloyd’s exposure management)',
           concat('gov_alert_dispatch · ', s.dispatch_id)
    FROM sweep s WHERE s.n_countries >= 2 AND s.threatened_count > 0
    UNION ALL
    -- 5 · rejected data batch (quality)
    SELECT concat('DQ|', b.run_id), b.pulled_at, 'MEDIUM', 'Data-quality incident',
           concat('Feed batch rejected — ', coalesce(sr.source_name, b.source_id)),
           b.reason,
           'The batch never reached the book: decisions in that window used the previous accepted data. The data owner should confirm the cause and that nothing downstream used the bad batch.',
           'Data owner for the feed; head of exposure management',
           'Solvency II data-quality requirements — data must be appropriate, complete and accurate (Directive 2009/138/EC; Delegated Regulation (EU) 2015/35)',
           concat('gov_feed_dq_log · ', b.run_id)
    FROM batches b LEFT JOIN src sr ON sr.source_id = b.source_id
    WHERE b.decision = 'REJECTED' AND b.unreachable = 0
    UNION ALL
    -- 6 · live data service unreachable (ICT incident)
    SELECT concat('ICT|', lb.source_id), lb.pulled_at, 'MEDIUM', 'ICT incident',
           concat('Live feed unreachable — ', coalesce(sr.source_name, lb.source_id)),
           lb.reason,
           'A live data service was down, so a frozen sample was used and labelled. Every ICT-related incident is logged and classified; this one is minor (no customer service affected) unless it persists or spreads.',
           'IT / ICT risk function; the data owner',
           'DORA — ICT-related incident management and classification (Regulation (EU) 2022/2554)',
           concat('gov_feed_dq_log · ', lb.run_id)
    FROM last_batch lb LEFT JOIN src sr ON sr.source_id = lb.source_id
    WHERE (lb.decision = 'FALLBACK_FROZEN' AND lb.reason LIKE '%unreachable%') OR lb.unreachable = 1
    UNION ALL
    -- 7 · live feed not switched on (credential missing) — a configuration gap, not an incident
    SELECT concat('CFG|', lb.source_id), lb.pulled_at, 'LOW', 'Data source configuration',
           concat('Live feed not switched on — ', coalesce(sr.source_name, lb.source_id)),
           lb.reason,
           'The live feed needs a credential, so a frozen real sample is in use. Not an incident, but the gap belongs on the data owner’s list.',
           'Data owner',
           'Internal data governance (no regulatory trigger)',
           concat('gov_feed_dq_log · ', lb.run_id)
    FROM last_batch lb LEFT JOIN src sr ON sr.source_id = lb.source_id
    WHERE lb.decision = 'FALLBACK_FROZEN' AND (lb.reason LIKE '%no MAP_KEY%' OR lb.reason LIKE '%no CDS key%')
    UNION ALL
    -- 8 · an alert sweep breached thresholds but the email did not go out
    SELECT concat('ALERT|', dl.run_id), dl.delivered_at, 'HIGH', 'Operational incident',
           'Alert email not delivered',
           concat(dl.n_events, ' events crossed their threshold in sweep ', dl.run_id,
                  ' but the Databricks alert did not send (state ', dl.alert_state, ').'),
           'The people who act on alerts — claims and underwriting — were not told. Fix the delivery and resend.',
           'Head of exposure management; IT',
           'Operational resilience — DORA incident management where the cause is ICT (Regulation (EU) 2022/2554); the firm’s alerting policy',
           concat('gov_alert_delivery · ', dl.run_id)
    FROM {fqn}.gov_alert_delivery dl
    WHERE dl.alert_state <> 'TRIGGERED' AND dl.delivered_at >= current_timestamp() - make_interval(0, 0, 0, p_days)
    UNION ALL
    -- 9 · new business refused in an active zone, and underwriter overrides
    SELECT concat('BIND|', bd.quote_id, '|', CAST(bd.decided_at AS STRING)), bd.decided_at,
           CASE WHEN bd.overridden THEN 'HIGH' ELSE 'MEDIUM' END,
           CASE WHEN bd.overridden THEN 'Underwriter override' ELSE 'Conduct record' END,
           CASE WHEN bd.overridden THEN concat('Underwriter overrode the active-zone rule — ', bd.applicant)
                ELSE concat('New business refused in an active zone — ', bd.applicant) END,
           concat(bd.applicant, ' (', bd.object_ref, '): ', bd.decision, ' — ', bd.reason),
           CASE WHEN bd.overridden THEN 'An exception to the rule needs a second pair of eyes and a recorded rationale.'
                ELSE 'Refusing cover must be consistent and explainable: the reason is kept with the decision, and the same rule applies to everyone. New business only — existing customers and renewals are untouched.' END,
           CASE WHEN bd.overridden THEN 'Underwriting manager (second review); compliance'
                ELSE 'Underwriting manager; conduct and compliance' END,
           concat('IDD — product oversight and governance, fair treatment (Directive (EU) 2016/97)',
                  CASE WHEN bd.latitude BETWEEN 49.8 AND 60.9 AND bd.longitude BETWEEN -8.7 AND 1.8
                       THEN '; UK FCA Consumer Duty (UK risk)' ELSE '' END),
           concat('gov_bind_decision · ', bd.quote_id)
    FROM {fqn}.gov_bind_decision bd
    WHERE (bd.decision IN ('DECLINED', 'REFER', 'REFERRED') OR bd.overridden)
      AND bd.decided_at >= current_timestamp() - make_interval(0, 0, 0, p_days)
    UNION ALL
    -- 10 · an event declared by a person from a press report
    SELECT concat('NEWS|', nd.signal_id), nd.decided_at, 'LOW', 'Human decision',
           concat('Event declared from a press report — ', coalesce(nd.event_id, nd.signal_id)),
           concat('A person promoted a press / disaster-feed signal to an event (confidence ',
                  format_number(coalesce(nd.confidence, 0) * 100, 0), '%). ', coalesce(nd.evidence, '')),
           'The event was raised from an unverified source by a person. Confirm it when the satellite or official feed catches up, or stand it down.',
           'Head of exposure management',
           'Human oversight and logging of AI-assisted decisions — good practice (EU AI Act, Regulation (EU) 2024/1689; property cat monitoring is not a listed high-risk use)',
           concat('gov_news_decision · ', nd.signal_id)
    FROM {fqn}.gov_news_decision nd
    WHERE nd.action = 'PROMOTE' AND nd.decided_at >= current_timestamp() - make_interval(0, 0, 0, p_days)
  ),
  act AS (
    SELECT * FROM (
      SELECT item_id, action, note, actor, acted_at,
             row_number() OVER (PARTITION BY item_id ORDER BY acted_at DESC) AS rn
      FROM {fqn}.gov_report_action) WHERE rn = 1
  )
  SELECT substr(sha2(i.k, 256), 1, 16) AS item_id, i.detected_at, i.severity, i.category, i.title,
         i.what_happened, i.why_it_matters, i.who_to_tell, i.rule_basis, i.source_record,
         coalesce(a.action, 'OPEN') AS status, a.note AS status_note, a.actor AS status_by, a.acted_at AS status_at
  FROM items i
  LEFT JOIN act a ON a.item_id = substr(sha2(i.k, 256), 1, 16)
  ORDER BY CASE coalesce(a.action, 'OPEN') WHEN 'OPEN' THEN 0 WHEN 'ACKNOWLEDGED' THEN 1 ELSE 2 END,
           CASE i.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
           i.detected_at DESC
""", "fn_reportable")
print("fn_reportable ✓")

# COMMAND ----------
# Plain-named view for Genie (table names starting with a digit break Genie's SQL)
run_wh(f"CREATE OR REPLACE VIEW {fqn}.mv_reportable AS SELECT * FROM {fqn}.fn_reportable(30)", "mv_reportable")
print("mv_reportable ✓")

# COMMAND ----------
# MAGIC %md ## Grants (UC SQL functions run with invoker rights — the app needs SELECT on what they read)

# COMMAND ----------

READS = ["`1_hazard_feed_raw`", "gov_feed_dq_log", "`1_feed_quarantine`", "`2_event_footprint`",
         "gov_exposure_snapshot", "gov_alert_dispatch", "gov_alert_delivery", "gov_news_decision",
         "gov_bind_decision", "cache_agent_responses", "gov_report_action", "ref_record_register",
         "ref_risk_appetite", "`3_treaty_layer`", "ref_feed_source"]
for g in GRANTEES:
    for t in READS:
        try:
            run_wh(f"GRANT SELECT ON TABLE {fqn}.{t} TO `{g}`", f"grant {t}")
        except Exception as e:
            print("grant warn", t, g, str(e)[:120])
    for f in ["fn_record_register", "fn_reportable"]:
        run_wh(f"GRANT EXECUTE ON FUNCTION {fqn}.{f} TO `{g}`", f"grant {f}")
    run_wh(f"GRANT SELECT ON VIEW {fqn}.mv_reportable TO `{g}`", "grant mv_reportable")
if app_sp:  # the governance screen appends acknowledge / reported actions
    run_wh(f"GRANT SELECT, MODIFY ON TABLE {fqn}.gov_report_action TO `{app_sp}`", "grant report_action")
print("grants applied to", GRANTEES)

# COMMAND ----------
# MAGIC %md ## Check

# COMMAND ----------

for r in wh_rows(f"SELECT name, how_kept, n_records, CAST(last_written AS STRING) AS lw FROM {fqn}.fn_record_register()"):
    print(f"  {r['name']:<34} {r['how_kept'][:40]:<40} n={r['n_records']}  last={r['lw']}")
items = wh_rows(f"SELECT severity, category, title, status FROM {fqn}.fn_reportable(7)")
print(f"\nreportable items (last 7 days): {len(items)}")
for r in items:
    print(f"  [{r['severity']}] {r['category']}: {r['title']} — {r['status']}")
