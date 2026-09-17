# Databricks notebook source
# MAGIC %md
# MAGIC # 30 · Stakeholder alerts — the people who never open Databricks (Bricksurance SE)
# MAGIC
# MAGIC The exposure view answers *which properties are threatened, net of treaty, by coverholder*. But the Head of
# MAGIC Exposure Management said the hard part out loud: **claims handlers, underwriters and seniors won't be logged
# MAGIC in when an event develops** — they need to be *told*. And the miss that hurt was a cross-border one: an event
# MAGIC that spanned three countries, seen by three country-siloed teams as three small things instead of one big one.
# MAGIC
# MAGIC This notebook is the alert path, and it runs **with nobody logged in** (a scheduled serverless job):
# MAGIC 1. **Snapshot** the live exposure for every active event (`gov_exposure_snapshot`, append-only).
# MAGIC 2. **Delta** vs the previous snapshot — newly-threatened / still-threatened / no-longer-threatened
# MAGIC    (`fn_event_delta`): new fires, progression, extinguished.
# MAGIC 3. **Threshold** per peril (`ref_alert_threshold`) decides whether an event warrants an alert.
# MAGIC 4. **Digest, grouped BY EVENT** — one alert carries the whole cross-border picture (every country the event
# MAGIC    touches, gross → net, top delegated authorities, what's new since last time).
# MAGIC 5. **Dispatch + audit** (`gov_alert_dispatch`, append-only): who was told, when, on what channel, and the exact
# MAGIC    digest that went out. A **real send** if a channel secret is configured (SMTP or Slack), else the digest is
# MAGIC    still rendered and logged (the in-app preview is the proof) — never a crash for a missing credential.
# MAGIC
# MAGIC **GOTCHA (as in notebooks 02 / 20):** anything reading `fn_exposure_in_footprint` uses `ST_*`, rejected on the
# MAGIC serverless notebook (Spark Connect) — so those statements run on the **SQL warehouse** via the Statement
# MAGIC Execution API (`run_wh`). Plain governed tables (`gov_*`, `ref_*`) are written with Spark. `CREATE OR REPLACE
# MAGIC FUNCTION` revokes EXECUTE — the re-grant cell must run.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "exposure_response")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("app_sp", "")
dbutils.widgets.text("buffer_m", "200")
dbutils.widgets.text("app_url", "https://exposure-response-workbench-7474656169654171.aws.databricksapps.com")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
warehouse_id = dbutils.widgets.get("warehouse_id")
app_sp = dbutils.widgets.get("app_sp").strip()
BUFFER_M = int(dbutils.widgets.get("buffer_m"))
APP_URL = dbutils.widgets.get("app_url").rstrip("/")
fqn = f"{catalog}.{schema}"
print(f"target = {fqn}  warehouse={warehouse_id}  buffer={BUFFER_M}m  app_sp={app_sp or '(none yet)'}")

from datetime import datetime, timezone
import html as _html

from databricks.sdk import WorkspaceClient
_w = WorkspaceClient()

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def run_wh(stmt: str, label: str = ""):
    """Execute one statement on the SQL warehouse (where ST_* is supported and the app also runs)."""
    r = _w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=warehouse_id, catalog=catalog, schema=schema, wait_timeout="50s")
    state = r.status.state.value
    if state != "SUCCEEDED":
        raise RuntimeError(f"{label}: {state} — {r.status.error.message if r.status.error else '?'}")
    return r


def wh_rows(stmt: str, label: str = ""):
    """Run on the warehouse, return list[dict] (all values are strings from the API)."""
    r = run_wh(stmt, label)
    if r.result is None or r.result.data_array is None:
        return []
    cols = [c.name for c in r.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in r.result.data_array]

# COMMAND ----------

# MAGIC %md ## ref_alert_threshold — per-peril trigger (visible, governed, tunable)
# MAGIC An event alerts when its modelled gross loss clears the peril's floor **or** enough properties are newly
# MAGIC threatened since last look. Kept as a table so the trigger is auditable, not buried in code.

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {fqn}.ref_alert_threshold (
    peril_code STRING COMMENT 'FIRE / FLOOD / STORM',
    min_gross_eur DOUBLE COMMENT 'alert if modelled gross loss (fn_gross_to_net) at/above this',
    min_new_count BIGINT COMMENT 'alert if this many properties newly threatened since the previous snapshot'
) COMMENT 'Governed, tunable per-peril alert trigger. An event fires an alert when gross modelled loss >= min_gross_eur OR newly-threatened count >= min_new_count.'
""")
spark.sql(f"DELETE FROM {fqn}.ref_alert_threshold")
spark.createDataFrame(
    [("FIRE", 10_000_000.0, 15), ("FLOOD", 15_000_000.0, 20), ("STORM", 25_000_000.0, 40)],
    "peril_code string, min_gross_eur double, min_new_count bigint",
).write.mode("append").saveAsTable(f"{fqn}.ref_alert_threshold")
print("ref_alert_threshold seeded")

# COMMAND ----------

# MAGIC %md ## gov_exposure_snapshot — append-only, so we can diff run-to-run
# MAGIC One row per threatened property per event per run. The previous run is the baseline the delta compares against.

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {fqn}.gov_exposure_snapshot (
    run_id STRING COMMENT 'yyyyMMddHHmmss of the sweep; lexically sortable so max() = latest',
    event_id STRING, buffer_m INT, insured_object_id STRING, band STRING, sum_insured DOUBLE,
    as_of TIMESTAMP
) COMMENT 'Append-only exposure snapshot. Each alert sweep records the threatened property set per event so the next sweep can compute new / still / no-longer threatened (fire progression). Never overwritten — the event-response audit trail.'
""")

# COMMAND ----------

# MAGIC %md ## gov_alert_dispatch — append-only audit of every alert evaluated and sent

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {fqn}.gov_alert_dispatch (
    dispatch_id STRING, run_id STRING, event_id STRING, peril_code STRING, event_name STRING, event_date STRING,
    breached BOOLEAN COMMENT 'did the event clear its peril threshold', threshold_rule STRING,
    threatened_count BIGINT, sum_insured_eur DOUBLE, gross_eur DOUBLE, ceded_eur DOUBLE, net_eur DOUBLE,
    countries STRING COMMENT 'every country the ONE event touches — cross-border in a single alert',
    n_countries INT, top_coverholder STRING,
    new_count BIGINT, still_count BIGINT, gone_count BIGINT,
    channel STRING, recipients STRING,
    status STRING COMMENT 'SENT / RENDERED_NOT_SENT (breached, no channel secret) / SUPPRESSED (below threshold)',
    digest_html STRING, digest_text STRING, sent_at TIMESTAMP
) COMMENT 'Append-only dispatch log. One row per event per sweep: the threshold decision, the governed numbers that went into the alert, the cross-border country list, the delta since last time, the channel + recipients, and the exact digest sent. The record of who was told what, when — reproducible.'
""")

# COMMAND ----------

# MAGIC %md ## fn_event_delta — current live exposure vs the previous snapshot
# MAGIC New fires, progression, extinguished — computed against the last recorded snapshot for this event/buffer.
# MAGIC (Reads `fn_exposure_in_footprint` → `ST_*` → must be created on the warehouse.)

# COMMAND ----------

run_wh(f"""
CREATE OR REPLACE FUNCTION {fqn}.fn_event_delta(
    p_event_id STRING COMMENT 'event_id from 2_event_footprint',
    p_buffer_m INT     COMMENT 'buffer distance in metres'
)
RETURNS TABLE(status STRING, n_objects BIGINT, sum_insured_eur DOUBLE)
COMMENT 'Change in threatened properties for event p_event_id within p_buffer_m metres, comparing the CURRENT live exposure (fn_exposure_in_footprint) against the most recent gov_exposure_snapshot for the same event/buffer. Rows: newly_threatened (new since last look — new fires / progression), still_threatened (unchanged), no_longer_threatened (dropped out — extinguished / receded). If there is no prior snapshot, everything is newly_threatened. This is the new/progressing/extinguished signal the alert leads with.'
RETURN
  WITH cur AS (
    SELECT insured_object_id, sum_insured FROM {fqn}.fn_exposure_in_footprint(p_event_id, p_buffer_m)
  ),
  last_run AS (
    SELECT max(run_id) AS rid FROM {fqn}.gov_exposure_snapshot
    WHERE event_id = p_event_id AND buffer_m = p_buffer_m
  ),
  prev AS (
    SELECT s.insured_object_id, s.sum_insured
    FROM {fqn}.gov_exposure_snapshot s JOIN last_run ON s.run_id = last_run.rid
    WHERE s.event_id = p_event_id AND s.buffer_m = p_buffer_m
  )
  SELECT 'newly_threatened' AS status, count(*) AS n_objects, CAST(COALESCE(sum(sum_insured),0) AS DOUBLE) AS sum_insured_eur
    FROM cur WHERE insured_object_id NOT IN (SELECT insured_object_id FROM prev)
  UNION ALL
  SELECT 'still_threatened', count(*), CAST(COALESCE(sum(sum_insured),0) AS DOUBLE)
    FROM cur WHERE insured_object_id IN (SELECT insured_object_id FROM prev)
  UNION ALL
  SELECT 'no_longer_threatened', count(*), CAST(COALESCE(sum(sum_insured),0) AS DOUBLE)
    FROM prev WHERE insured_object_id NOT IN (SELECT insured_object_id FROM cur)
""", "fn_event_delta")
print("fn_event_delta created")

# COMMAND ----------

# MAGIC %md ## Sweep — evaluate every active event, build the event-grouped digest, dispatch + audit

# COMMAND ----------

def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d

def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d

def eurm(v):
    v = _f(v) / 1e6
    return "€" + (f"{v:.0f}" if abs(v) >= 100 else f"{v:.1f}") + "m"


def get_secret(key):
    try:
        v = dbutils.secrets.get("exposure_response", key)
        return v if v else None
    except Exception:
        return None


def send_digest(subject, html_body, text_body):
    """Pluggable real send. SMTP if configured, else Slack webhook, else no channel.
    Returns (channel, recipients, sent_ok). Never raises."""
    # 1) SMTP
    host = get_secret("SMTP_HOST")
    if host:
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            port = _i(get_secret("SMTP_PORT") or 587, 587)
            user = get_secret("SMTP_USER")
            pw = get_secret("SMTP_PASS")
            sender = get_secret("SMTP_FROM") or user
            to = get_secret("SMTP_TO") or "claims@bricksurance.example"
            msg = MIMEMultipart("alternative")
            msg["Subject"], msg["From"], msg["To"] = subject, sender, to
            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls()
                if user and pw:
                    s.login(user, pw)
                s.sendmail(sender, [a.strip() for a in to.split(",")], msg.as_string())
            return ("email", to, True)
        except Exception as e:
            print(f"  SMTP send failed: {e}")
    # 2) Slack webhook
    hook = get_secret("SLACK_WEBHOOK")
    if hook:
        try:
            import json
            import urllib.request
            req = urllib.request.Request(
                hook, data=json.dumps({"text": f"*{subject}*\n{text_body}"}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20).read()
            return ("slack", "#exposure-alerts", True)
        except Exception as e:
            print(f"  Slack send failed: {e}")
    return (None, None, False)


# The standing distribution list for cat event response (synthetic — no real people).
RECIPIENTS = "claims@bricksurance.example, underwriting@bricksurance.example, exposure-mgmt@bricksurance.example"

PERIL_LABEL = {"FIRE": "Wildfire", "FLOOD": "Flood", "STORM": "Windstorm"}
PERIL_COLOR = {"FIRE": "#dc2626", "FLOOD": "#2563eb", "STORM": "#7c3aed"}


def build_digest(ev, headline, countries, coverholders, delta):
    """Compose the event-grouped digest (HTML + text). ONE event = ONE alert, whole cross-border picture."""
    peril = ev["peril_code"]
    plabel = PERIL_LABEL.get(peril, peril)
    pcol = PERIL_COLOR.get(peril, "#334155")
    ename = ev["event_name"]
    edate = ev.get("event_date", "")
    n = _i(headline["threatened_count"])
    si = _f(headline["sum_insured_eur"])
    gross, ceded, net = _f(headline["gross_eur"]), _f(headline["ceded_eur"]), _f(headline["net_eur"])
    new = _i(delta.get("newly_threatened", 0))
    gone = _i(delta.get("no_longer_threatened", 0))
    clist = ", ".join(f"{c['k']} ({_i(c['n'])})" for c in countries)
    xborder = len(countries) > 1
    top_ch = coverholders[0] if coverholders else None
    link = f"{APP_URL}/?event={ev['event_id']}"

    e = _html.escape
    ctry_rows = "".join(
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #eee'>{e(c['k'])}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_i(c['n']):,}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{eurm(c['si'])}</td></tr>"
        for c in countries)
    ch_rows = "".join(
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #eee'>{e(c['name'])}"
        f"{' <span style=\"color:#3730a3;font-size:11px\">delegated</span>' if c['del'] else ' <span style=\"color:#64748b;font-size:11px\">direct</span>'}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_i(c['n']):,}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{eurm(c['si'])}</td></tr>"
        for c in coverholders[:5])

    xborder_banner = (
        f"<div style='margin:14px 0;padding:10px 12px;background:#fff7ed;border-left:4px solid #ea580c;"
        f"border-radius:6px;font-size:13px;color:#7c2d12'><b>Cross-border event.</b> This single event touches "
        f"<b>{len(countries)} countries</b> ({e(clist)}). Country-siloed teams would each see only their slice — "
        f"this alert is the whole event.</div>" if xborder else "")

    html_body = f"""<!doctype html><html><body style="margin:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif">
<div style="max-width:640px;margin:0 auto;background:#fff">
  <div style="background:{pcol};color:#fff;padding:18px 22px">
    <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.9">Bricksurance SE · Event Response Alert</div>
    <div style="font-size:20px;font-weight:700;margin-top:4px">{e(plabel)}: {e(ename)}</div>
    <div style="font-size:13px;opacity:.9;margin-top:2px">{e(edate)} · buffer {BUFFER_M} m</div>
  </div>
  <div style="padding:20px 22px">
    <table style="width:100%;border-collapse:collapse;margin-bottom:6px"><tr>
      <td style="padding:8px;text-align:center"><div style="font-size:22px;font-weight:700">{n:,}</div><div style="font-size:11px;color:#64748b">properties threatened</div></td>
      <td style="padding:8px;text-align:center"><div style="font-size:22px;font-weight:700">{eurm(si)}</div><div style="font-size:11px;color:#64748b">sum insured at risk</div></td>
      <td style="padding:8px;text-align:center"><div style="font-size:22px;font-weight:700;color:#16a34a">{eurm(net)}</div><div style="font-size:11px;color:#64748b">net retained</div></td>
    </tr></table>
    <div style="font-size:13px;color:#334155;margin:6px 0 2px"><b style="color:#b45309">▲ {new:,} newly threatened</b> since the last check{f" · <span style='color:#64748b'>▼ {gone:,} no longer threatened</span>" if gone else ""}.</div>
    {xborder_banner}
    <div style="font-size:13px;font-weight:700;color:#334155;margin:16px 0 4px">Gross → net of treaty</div>
    <div style="font-size:13px;color:#475569">Modelled gross loss <b>{eurm(gross)}</b> → ceded <b>{eurm(ceded)}</b> → <b style="color:#16a34a">net retained {eurm(net)}</b> (Property Cat XL 2026).</div>
    <div style="font-size:13px;font-weight:700;color:#334155;margin:16px 0 4px">Exposure by country{'  ·  cross-border' if xborder else ''}</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr>
      <th style="text-align:left;padding:6px 10px;color:#64748b;font-size:11px">Country</th>
      <th style="text-align:right;padding:6px 10px;color:#64748b;font-size:11px">Properties</th>
      <th style="text-align:right;padding:6px 10px;color:#64748b;font-size:11px">Sum insured</th></tr></thead><tbody>{ctry_rows}</tbody></table>
    <div style="font-size:13px;font-weight:700;color:#334155;margin:16px 0 4px">Top delegated authorities / coverholders</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr>
      <th style="text-align:left;padding:6px 10px;color:#64748b;font-size:11px">Coverholder</th>
      <th style="text-align:right;padding:6px 10px;color:#64748b;font-size:11px">Properties</th>
      <th style="text-align:right;padding:6px 10px;color:#64748b;font-size:11px">Sum insured</th></tr></thead><tbody>{ch_rows}</tbody></table>
    <div style="margin:20px 0 4px"><a href="{e(link)}" style="display:inline-block;background:{pcol};color:#fff;text-decoration:none;padding:10px 18px;border-radius:6px;font-size:13px;font-weight:700">Open the live exposure view →</a></div>
    <div style="font-size:11px;color:#94a3b8;margin-top:14px">Automated by the Exposure &amp; Event Response Workbench. Numbers come from governed Unity Catalog functions. Illustrative demo data — synthetic Bricksurance SE.</div>
  </div>
</div></body></html>"""

    top_ch_txt = f"{top_ch['name']} ({_i(top_ch['n'])} / {eurm(top_ch['si'])})" if top_ch else "—"
    text_body = (
        f"BRICKSURANCE SE — EVENT RESPONSE ALERT\n{plabel}: {ename} ({edate})\n\n"
        f"{n:,} properties threatened within {BUFFER_M} m · {eurm(si)} sum insured at risk.\n"
        f"Gross modelled loss {eurm(gross)} -> ceded {eurm(ceded)} -> net retained {eurm(net)}.\n"
        f"NEW since last check: {new:,}" + (f"  ·  no longer threatened: {gone:,}" if gone else "") + "\n"
        + (f"CROSS-BORDER: {len(countries)} countries — {clist}\n" if xborder else f"Countries: {clist}\n")
        + f"Top delegated authority: {top_ch_txt}\n"
        f"Live view: {link}\n")
    return html_body, text_body


# ---- pull active events, evaluate each ----
events = wh_rows(
    f"SELECT DISTINCT event_id, peril_code, event_name, CAST(event_date AS STRING) AS event_date "
    f"FROM {fqn}.`2_event_footprint` ORDER BY event_id", "active events")
thresholds = {r["peril_code"]: r for r in wh_rows(f"SELECT * FROM {fqn}.ref_alert_threshold", "thresholds")}
print(f"evaluating {len(events)} events at {BUFFER_M} m")

dispatch_rows = []
snapshot_stmts = []
now = datetime.now(timezone.utc)

for ev in events:
    eid = ev["event_id"]
    # governed numbers (all on the warehouse — ST_ path)
    summ = wh_rows(f"SELECT dim, dim_key, n_objects, sum_insured_eur FROM {fqn}.fn_event_exposure_summary('{eid}', {BUFFER_M})", f"summary {eid}")
    wf = wh_rows(f"SELECT kind, running_net_eur, ceded_eur FROM {fqn}.fn_gross_to_net('{eid}', {BUFFER_M})", f"waterfall {eid}")
    chs = wh_rows(f"SELECT coverholder_name, is_delegated, n_objects, sum_insured_eur FROM {fqn}.fn_exposure_by_coverholder('{eid}', {BUFFER_M})", f"coverholders {eid}")
    dl = wh_rows(f"SELECT status, n_objects, sum_insured_eur FROM {fqn}.fn_event_delta('{eid}', {BUFFER_M})", f"delta {eid}")

    countries = sorted(
        [{"k": r["dim_key"], "n": _i(r["n_objects"]), "si": _f(r["sum_insured_eur"])} for r in summ if r["dim"] == "country"],
        key=lambda c: -c["n"])
    total_n = sum(c["n"] for c in countries)
    total_si = sum(c["si"] for c in countries)
    gross = _f(next((r["running_net_eur"] for r in wf if r["kind"] == "gross"), 0))
    net = _f(next((r["running_net_eur"] for r in wf if r["kind"] == "net"), 0))
    ceded = max(gross - net, 0.0)
    coverholders = [{"name": r["coverholder_name"], "del": str(r["is_delegated"]).lower() == "true",
                     "n": _i(r["n_objects"]), "si": _f(r["sum_insured_eur"])} for r in chs]
    delta = {r["status"]: _i(r["n_objects"]) for r in dl}
    new_count = delta.get("newly_threatened", 0)

    th = thresholds.get(ev["peril_code"], {})
    min_gross = _f(th.get("min_gross_eur"), 1e18)
    min_new = _i(th.get("min_new_count"), 1 << 30)
    breached = (gross >= min_gross) or (new_count >= min_new)
    rule = f"gross ≥ {eurm(min_gross)} OR new ≥ {min_new:,}"

    headline = {"threatened_count": total_n, "sum_insured_eur": total_si,
                "gross_eur": gross, "ceded_eur": ceded, "net_eur": net}
    html_body, text_body = build_digest(ev, headline, countries, coverholders, delta)

    channel, recipients, status = None, None, "SUPPRESSED"
    if breached:
        subject = f"[Bricksurance SE] {PERIL_LABEL.get(ev['peril_code'], ev['peril_code'])} — {ev['event_name']}: {total_n:,} properties, {eurm(net)} net at risk"
        channel, recipients, sent_ok = send_digest(subject, html_body, text_body)
        status = "SENT" if sent_ok else "RENDERED_NOT_SENT"
        if not sent_ok:
            recipients = RECIPIENTS
    print(f"  {eid}: gross={eurm(gross)} new={new_count} breached={breached} -> {status}")

    dispatch_rows.append((
        f"{RUN_ID}_{eid}", RUN_ID, eid, ev["peril_code"], ev["event_name"], ev.get("event_date", ""),
        breached, rule, total_n, total_si, gross, ceded, net,
        ", ".join(c["k"] for c in countries), len(countries),
        coverholders[0]["name"] if coverholders else None,
        new_count, delta.get("still_threatened", 0), delta.get("no_longer_threatened", 0),
        channel, recipients, status, html_body, text_body, now))
    # snapshot the CURRENT exposure AFTER computing the delta (becomes next run's baseline)
    snapshot_stmts.append(
        f"INSERT INTO {fqn}.gov_exposure_snapshot "
        f"SELECT '{RUN_ID}','{eid}',{BUFFER_M}, insured_object_id, band, sum_insured, current_timestamp() "
        f"FROM {fqn}.fn_exposure_in_footprint('{eid}', {BUFFER_M})")

# COMMAND ----------

# MAGIC %md ## Persist — dispatch audit (Spark append) + snapshot the current sets (warehouse, ST_ path)

# COMMAND ----------

disp_schema = ("dispatch_id string, run_id string, event_id string, peril_code string, event_name string, "
               "event_date string, breached boolean, threshold_rule string, threatened_count bigint, "
               "sum_insured_eur double, gross_eur double, ceded_eur double, net_eur double, countries string, "
               "n_countries int, top_coverholder string, new_count bigint, still_count bigint, gone_count bigint, "
               "channel string, recipients string, status string, digest_html string, digest_text string, sent_at timestamp")
spark.createDataFrame(dispatch_rows, disp_schema).write.mode("append").saveAsTable(f"{fqn}.gov_alert_dispatch")
print(f"gov_alert_dispatch += {len(dispatch_rows)} rows (run {RUN_ID})")

for stmt in snapshot_stmts:
    run_wh(stmt, "snapshot")
print(f"gov_exposure_snapshot updated for {len(snapshot_stmts)} events (run {RUN_ID})")

# COMMAND ----------

# MAGIC %md ## GRANTs — re-grant EXECUTE (fn) + SELECT (gov/ref tables) to app SP + account users

# COMMAND ----------

tables = ["gov_alert_dispatch", "gov_exposure_snapshot", "ref_alert_threshold"]
fns = ["fn_event_delta"]
grantees = ["`account users`"] + ([f"`{app_sp}`"] if app_sp else [])
for g in grantees:
    for t in tables:
        run_wh(f"GRANT SELECT ON TABLE {fqn}.{t} TO {g}", f"grant SELECT {t} -> {g}")
    for fn in fns:
        run_wh(f"GRANT EXECUTE ON FUNCTION {fqn}.{fn} TO {g}", f"grant EXECUTE {fn} -> {g}")
print("grants applied")

# COMMAND ----------

# MAGIC %md ## Smoke — the dispatch log for this sweep (cross-border proof for the Alpine flood)

# COMMAND ----------

for row in wh_rows(
    f"SELECT event_name, status, breached, threatened_count, n_countries, countries, new_count, "
    f"round(gross_eur/1e6,1) AS gross_m, round(net_eur/1e6,1) AS net_m, channel "
    f"FROM {fqn}.gov_alert_dispatch WHERE run_id='{RUN_ID}' ORDER BY gross_eur DESC", "dispatch log"):
    print("  ", row)
