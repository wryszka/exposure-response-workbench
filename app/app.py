"""Bricksurance SE — Exposure & Event Response Workbench (thin app).

Presentation only. Every panel calls a real governed UC function (fn_events / fn_exposure_in_footprint /
fn_event_exposure_summary) on the SQL warehouse and renders. No business logic, no geospatial maths, no scoring
here — the app recomputes by *calling* the functions, so the numbers on screen are the governed numbers.
"""
import os
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

from server import config, sql

app = FastAPI(title="Exposure & Event Response Workbench")
DIST = os.path.join(os.path.dirname(__file__), "dist")


@app.middleware("http")
async def no_store_html(request: Request, call_next):
    """Databricks Apps can serve stale HTML — force HTML never to cache (GOTCHA in DECISIONS.md)."""
    resp = await call_next(request)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


def _events():
    return sql.query(f"SELECT * FROM {config.fqn('fn_events')}(200)")


# ─────────────────────────── config ───────────────────────────
@app.get("/api/config")
def api_config():
    return {"catalog": config.CATALOG, "schema": config.SCHEMA, "entity": config.ENTITY,
            "workspace_host": config.workspace_host(), "genie_space_id": config.GENIE_SPACE_ID}


# ─────────────────────────── events (picker) ───────────────────────────
@app.get("/api/events")
def api_events():
    return {"events": _events()}


# ─────────────────────────── one event: exposure view ───────────────────────────
@app.get("/api/event/{eid}")
def api_event(eid: str, buffer: int = 200):
    eid = sql.esc(eid)
    try:
        buf = int(buffer)
    except (TypeError, ValueError):
        buf = 200
    fq = config.fqn
    out = sql.query_many({
        "summary": f"SELECT dim, dim_key, n_objects, sum_insured_eur FROM {fq('fn_event_exposure_summary')}('{eid}', {buf}) ORDER BY dim, dim_key",
        "properties": (
            f"SELECT insured_object_id, policy_id, country_code, city, postcode, latitude, longitude, "
            f"round(distance_m,1) AS distance_m, band, sum_insured, coverage_type_code, line_of_business_code "
            f"FROM {fq('fn_exposure_in_footprint')}('{eid}', {buf}) ORDER BY distance_m"),
        # footprint geometry for the map — the raw synthetic WKT segments for this event
        "footprint": f"SELECT event_id, peril_code, event_name, event_date, footprint_wkt FROM {fq('`2_event_footprint`')} WHERE event_id = '{eid}'",
        # gross → net waterfall (modelled loss ceded through the Property Cat XL tower)
        "waterfall": (
            f"SELECT seq, step_label, layer_name, attachment_eur, limit_eur, placement_pct, "
            f"ceded_eur, running_net_eur, kind FROM {fq('fn_gross_to_net')}('{eid}', {buf}) ORDER BY seq"),
        # threatened exposure split by coverholder / delegated authority
        "coverholders": (
            f"SELECT coverholder_id, coverholder_name, binder_ref, country_scope, is_delegated, "
            f"n_objects, sum_insured_eur FROM {fq('fn_exposure_by_coverholder')}('{eid}', {buf})"),
        # what changed since the last alert sweep — new / still / no-longer threatened
        "delta": f"SELECT status, n_objects, sum_insured_eur FROM {fq('fn_event_delta')}('{eid}', {buf})",
    })
    return {"event_id": eid, "buffer_m": buf, "alert": _latest_alert(eid), **out}


# ─────────────────────────── alerts ───────────────────────────
def _latest_alert(eid: str):
    """The most recent dispatch row for one event — status, the governed numbers, and the exact digest sent."""
    return sql.query_one(
        f"SELECT run_id, status, breached, threshold_rule, threatened_count, gross_eur, ceded_eur, net_eur, "
        f"countries, n_countries, new_count, still_count, gone_count, channel, recipients, "
        f"CAST(sent_at AS STRING) AS sent_at, digest_html "
        f"FROM {config.fqn('gov_alert_dispatch')} WHERE event_id = '{sql.esc(eid)}' ORDER BY run_id DESC LIMIT 1")


@app.get("/api/alerts")
def api_alerts():
    """The dispatch log for the latest sweep — the record of who was told what, when (grouped by event)."""
    rows = sql.query(
        f"SELECT event_id, event_name, peril_code, status, breached, threatened_count, n_countries, countries, "
        f"new_count, gross_eur, net_eur, channel, recipients, CAST(sent_at AS STRING) AS sent_at "
        f"FROM {config.fqn('gov_alert_dispatch')} "
        f"WHERE run_id = (SELECT max(run_id) FROM {config.fqn('gov_alert_dispatch')}) "
        f"ORDER BY gross_eur DESC")
    return {"dispatch": rows}


@app.post("/api/alerts/run")
def api_alerts_run():
    """Fire the alert sweep on demand (the same serverless job the schedule runs). Degrades gracefully."""
    try:
        w = config.get_workspace_client()
        jobs = list(w.jobs.list(name="exposure_30_alerts"))
        if not jobs:
            return {"ok": False, "reason": "alert sweep job not found in this workspace"}
        run = w.jobs.run_now(job_id=jobs[0].job_id)
        return {"ok": True, "run_id": run.run_id}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


# ─────────────────────────── static SPA ───────────────────────────
@app.get("/")
def index():
    return FileResponse(os.path.join(DIST, "index.html"))


@app.get("/healthz")
def healthz():
    return {"ok": True}
