"""Bricksurance SE — Exposure & Event Response Workbench (thin app).

Presentation only. Every panel calls a real governed UC function (fn_events / fn_exposure_in_footprint /
fn_event_exposure_summary) on the SQL warehouse and renders. No business logic, no geospatial maths, no scoring
here — the app recomputes by *calling* the functions, so the numbers on screen are the governed numbers.
"""
import os
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

from server import config, sql, agent, genie

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


def _try(statement: str, params: dict = None):
    """(rows, error) — for panels built on objects another workstream deploys; a missing object degrades
    the panel to an honest 'not available yet' instead of a 500."""
    try:
        return sql.query(statement, params), None
    except Exception as e:
        return [], str(e)[:200]


def _arr(v):
    """Statement Execution returns ARRAY columns as JSON text — parse to a list (tolerant of plain strings)."""
    import json as _json
    if v is None:
        return []
    if isinstance(v, list):
        return v
    try:
        out = _json.loads(v)
        return out if isinstance(out, list) else [out]
    except Exception:
        return [s.strip() for s in str(v).split(",") if s.strip()]


# ─────────────────────────── config ───────────────────────────
@app.get("/api/config")
def api_config():
    return {"catalog": config.CATALOG, "schema": config.SCHEMA, "entity": config.ENTITY,
            "workspace_host": config.workspace_host(), "genie_space_id": config.GENIE_SPACE_ID,
            "dashboard_id": config.DASHBOARD_ID, "workspace_id": config.workspace_id()}


# ─────────────────────────── events (picker) ───────────────────────────
@app.get("/api/events")
def api_events():
    """Active events + which of them are published as active zones (Contract A: mv_active_zones), so the
    board can truthfully tag 'active zone published to underwriting'. zones=None when the view isn't there."""
    zones, err = _try(f"SELECT DISTINCT event_id FROM {config.fqn('mv_active_zones')}")
    return {"events": _events(), "zones": None if err else [z["event_id"] for z in zones]}


# ─────────────────────────── one event: exposure view ───────────────────────────
@app.get("/api/event/{eid}")
def api_event(eid: str, buffer: int = 200):
    try:
        buf = int(buffer)
    except (TypeError, ValueError):
        buf = 200
    if buf not in (50, 100, 200):
        buf = 200
    fq = config.fqn
    pe = {"eid": eid, "buf": buf}  # bound params — eid comes from the URL path (untrusted)
    out = sql.query_many({
        "summary": (f"SELECT dim, dim_key, n_objects, sum_insured_eur FROM {fq('fn_event_exposure_summary')}(:eid, :buf) ORDER BY dim, dim_key", pe),
        "properties": (
            f"SELECT insured_object_id, policy_id, country_code, city, postcode, latitude, longitude, "
            f"round(distance_m,1) AS distance_m, band, sum_insured, coverage_type_code, line_of_business_code "
            f"FROM {fq('fn_exposure_in_footprint')}(:eid, :buf) ORDER BY distance_m", pe),
        # footprint geometry for the map — the raw synthetic WKT segments for this event
        "footprint": (f"SELECT event_id, peril_code, event_name, event_date, footprint_wkt FROM {fq('`2_event_footprint`')} WHERE event_id = :eid", {"eid": eid}),
        # gross → net waterfall (modelled loss ceded through the Property Cat XL tower)
        "waterfall": (
            f"SELECT seq, step_label, layer_name, attachment_eur, limit_eur, placement_pct, "
            f"ceded_eur, running_net_eur, kind FROM {fq('fn_gross_to_net')}(:eid, :buf) ORDER BY seq", pe),
        # threatened exposure split by coverholder / delegated authority
        "coverholders": (
            f"SELECT coverholder_id, coverholder_name, binder_ref, country_scope, is_delegated, "
            f"n_objects, sum_insured_eur FROM {fq('fn_exposure_by_coverholder')}(:eid, :buf)", pe),
        # what changed since the last alert sweep — new / still / no-longer threatened
        "delta": (f"SELECT status, n_objects, sum_insured_eur FROM {fq('fn_event_delta')}(:eid, :buf)", pe),
    })
    return {"event_id": eid, "buffer_m": buf, "alert": _latest_alert(eid), **out}


# ─────────────────────────── alerts ───────────────────────────
def _latest_alert(eid: str):
    """The most recent dispatch row for one event — status, the governed numbers, and the exact digest sent."""
    return sql.query_one(
        f"SELECT run_id, status, breached, threshold_rule, threatened_count, gross_eur, ceded_eur, net_eur, "
        f"countries, n_countries, new_count, still_count, gone_count, channel, recipients, "
        f"CAST(sent_at AS STRING) AS sent_at, digest_html "
        f"FROM {config.fqn('gov_alert_dispatch')} WHERE event_id = :eid ORDER BY run_id DESC LIMIT 1", {"eid": eid})


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


# ─────────────────────────── reset demo (roll every date to today) ───────────────────────────
def _reset_job_id():
    jid = os.environ.get("RESET_JOB_ID", "").strip()
    return int(jid) if jid.isdigit() else None


def _reset_run_summary(w, run):
    tasks = run.tasks or []
    done = sum(1 for t in tasks if t.state and t.state.result_state is not None)
    st = run.state
    return {"run_id": run.run_id,
            "state": (st.life_cycle_state.value if st and st.life_cycle_state else None),
            "result": (st.result_state.value if st and st.result_state else None),
            "started_ms": run.start_time, "ended_ms": run.end_time or None,
            "steps_done": done, "steps_total": len(tasks)}


@app.get("/api/demo/reset")
def api_demo_reset_status():
    """Latest run of the reset job: whether one is running, progress, and when the data was last reset."""
    jid = _reset_job_id()
    if not jid:
        return {"available": False, "reason": "reset job not bound to this app"}
    try:
        w = config.get_workspace_client()
        runs = list(w.jobs.list_runs(job_id=jid, limit=5))
        if not runs:
            return {"available": True, "latest": None, "last_success_ms": None}
        latest = w.jobs.get_run(runs[0].run_id)
        last_ok = next((r.end_time for r in runs if r.state and r.state.result_state
                        and r.state.result_state.value == "SUCCESS"), None)
        return {"available": True, "latest": _reset_run_summary(w, latest), "last_success_ms": last_ok}
    except Exception as e:
        return {"available": False, "reason": str(e)[:200]}


@app.post("/api/demo/reset")
def api_demo_reset_start():
    """Start the reset job (dates roll to today, fire back to the start, demo clicks cleared). One at a time."""
    jid = _reset_job_id()
    if not jid:
        return {"ok": False, "reason": "reset job not bound to this app"}
    try:
        w = config.get_workspace_client()
        active = list(w.jobs.list_runs(job_id=jid, active_only=True, limit=1))
        if active:
            return {"ok": False, "reason": "a reset is already running", "run_id": active[0].run_id}
        run = w.jobs.run_now(job_id=jid)
        return {"ok": True, "run_id": run.run_id}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


# ─────────────────────────── event response agent (Claude via FMAPI, governed tools) ───────────────────────────
@app.get("/api/agent/starters")
def api_agent_starters():
    return {"starters": agent.STARTERS, "endpoint": config.FM_ENDPOINT}


@app.post("/api/agent")
async def api_agent(request: Request):
    """Ask the Event Response agent. It answers only by calling the governed fn_* tools (proof returned).
    `live=true` bypasses the cache (the yellow live/cached toggle)."""
    body = await request.json()
    q = (body.get("question") or "").strip()
    if not q:
        return {"text": "Ask about an event's exposure, gross/net, coverholder split, or what's changed.", "tools_called": [], "cache": "n/a"}
    use_cache = not bool(body.get("live", False))
    return agent.ask(q, use_cache=use_cache)


# ─────────────────────────── Genie — ask the exposure book ───────────────────────────
@app.post("/api/genie")
async def api_genie(request: Request):
    body = await request.json()
    return genie.ask((body.get("question") or "").strip())


# ─────────────────────────── governance / version transparency ───────────────────────────
@app.get("/api/governance")
def api_governance():
    """Provenance (which feed, live/frozen, when ingested), exposure history (what moved across snapshots),
    and the alert audit — the governed, append-only story behind every number."""
    fq = config.fqn
    return sql.query_many({
        "provenance": (f"SELECT event_id, event_name, peril_code, source, source_detail, is_live, n_segments, "
                       f"countries, CAST(event_date AS STRING) event_date, CAST(ingested_at AS STRING) ingested_at "
                       f"FROM {fq('gov_data_provenance')} ORDER BY is_live DESC, ingested_at DESC"),
        "history": (f"SELECT event_id, CAST(as_of AS STRING) as_of, buffer_m, threatened_count, sum_insured_eur "
                    f"FROM {fq('gov_exposure_history')} ORDER BY event_id, as_of"),
        "audit": (f"SELECT event_id, event_name, status, breached, threatened_count, gross_eur, net_eur, "
                  f"n_countries, recipients, CAST(sent_at AS STRING) sent_at "
                  f"FROM {fq('gov_alert_audit')} ORDER BY sent_at DESC LIMIT 50"),
    })


# ─────────────────────────── data sources & quality (Contract B) ───────────────────────────
@app.get("/api/sources")
def api_sources():
    """Every feed we ingest (what it carries, real-time/batch/frozen, cadence, licence, last ingested, last
    decision) + the recent batch log from the quality gate (ACCEPTED / REJECTED / FALLBACK_FROZEN, with the
    plain reason). Both come from governed functions; missing objects degrade honestly."""
    fq = config.fqn
    sources, e1 = _try(
        f"SELECT source_id, source_name, peril_code, provider, carries, mode, cadence, licence, "
        f"CAST(last_ingested_at AS STRING) last_ingested_at, last_records, last_decision, is_live "
        f"FROM {fq('fn_source_status')}()")
    batches, e2 = _try(
        f"SELECT run_id, source_id, CAST(pulled_at AS STRING) pulled_at, n_records, decision, reason, failed_checks "
        f"FROM {fq('fn_dq_recent')}(:d) ORDER BY pulled_at DESC LIMIT 60", {"d": 7})
    for b in batches:
        b["failed_checks"] = _arr(b.get("failed_checks"))
    thresholds, _ = _try(
        f"SELECT source_id, check_name, threshold, comparator, description FROM {fq('ref_dq_threshold')} "
        f"ORDER BY source_id, check_name")
    return {"sources": sources, "batches": batches, "thresholds": thresholds,
            "available": {"sources": e1 is None, "batches": e2 is None}, "now_utc": _now_utc()}


@app.get("/api/sources/batch")
def api_sources_batch(run_id: str = ""):
    """Every quality check run on one feed batch (from fn_dq_batch_checks): the rule, the observed value,
    pass or fail — the detail behind an accepted or rejected batch."""
    if not run_id:
        return {"checks": [], "available": False}
    checks, err = _try(
        f"SELECT check_name, description, comparator, threshold, observed, passed, decision "
        f"FROM {config.fqn('fn_dq_batch_checks')}(:r)", {"r": run_id})
    return {"checks": checks, "available": err is None}


def _now_utc():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────── AI agent team (Contract C) ───────────────────────────
AGENT_ENDPOINT = os.getenv("AGENT_ENDPOINT", "exposure-response-agent")


def _endpoint_state():
    """Is the served supervisor endpoint up? → ('READY'|'NOT_READY'|'MISSING', detail)."""
    try:
        ep = config.get_workspace_client().serving_endpoints.get(AGENT_ENDPOINT)
        st = ep.state
        ready = getattr(getattr(st, "ready", None), "value", str(getattr(st, "ready", "")))
        return ("READY" if str(ready).upper().endswith("READY") and "NOT" not in str(ready).upper() else "NOT_READY",
                str(ready))
    except Exception as e:
        return ("MISSING", str(e)[:160])


@app.get("/api/agents/roster")
def api_agents_roster():
    """The agent team from ref_agent_roster (role, responsibilities, skills = UC functions, data read, may /
    may-not, example questions) + whether the served supervisor endpoint is ready."""
    rows, err = _try(
        f"SELECT agent_key, name, role, responsibilities, skills, reads, may_do, may_not_do, example_questions, endpoint "
        f"FROM {config.fqn('ref_agent_roster')}")
    for r in rows:
        for k in ("responsibilities", "skills", "reads", "may_do", "may_not_do", "example_questions"):
            r[k] = _arr(r.get(k))
    state, detail = _endpoint_state()
    return {"roster": rows, "roster_available": err is None, "endpoint": AGENT_ENDPOINT,
            "endpoint_state": state, "endpoint_detail": detail, "workspace_host": config.workspace_host()}


def _parse_agent_response(d: dict):
    """Pull text + routing out of an Agent-Framework (Responses) reply; tolerant of chat-completions shape."""
    text_parts = []
    for item in d.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    text_parts.append(c["text"])
    if not text_parts and d.get("choices"):
        text_parts.append(((d["choices"][0] or {}).get("message") or {}).get("content") or "")
    co = d.get("custom_outputs") or {}
    tools = co.get("tools") or []
    if not tools:  # derive from function_call items if the agent didn't summarise them
        for item in d.get("output") or []:
            if item.get("type") == "function_call":
                tools.append({"name": item.get("name"), "args": item.get("arguments")})
    return {"text": "\n\n".join(t for t in text_parts if t).strip(), "specialist": co.get("specialist"),
            "route_reason": co.get("route_reason"), "tools": tools}


@app.post("/api/agents/ask")
async def api_agents_ask(request: Request):
    """Ask the agent team. Goes to the served supervisor endpoint (Agent Framework) which routes to a
    specialist; the reply carries which specialist answered, why, and the tools it called. Cached (LLM turn
    only, `live=true` bypasses). If the endpoint isn't available, falls back to the in-app agent — labelled."""
    import hashlib, json as _json, requests
    body = await request.json()
    q = (body.get("question") or "").strip()
    if not q:
        return {"text": "Ask the team about an event, its cost, who was told, a bind decision, or a data source.",
                "mode": "n/a", "tools": []}
    live = bool(body.get("live", False))
    key = hashlib.sha256(_json.dumps({"q": q, "m": AGENT_ENDPOINT}, sort_keys=True).encode()).hexdigest()[:32]
    if not live:
        try:
            agent._ensure_cache()
            hit = agent._read(key)
            if hit is not None:
                return {**_json.loads(hit), "cache": "hit"}
        except Exception:
            pass
    state, _ = _endpoint_state()
    if state == "READY":
        try:
            w = config.get_workspace_client()
            host = config.workspace_host()
            if host and not host.startswith("http"):
                host = "https://" + host
            r = requests.post(f"{host}/serving-endpoints/{AGENT_ENDPOINT}/invocations",
                              headers={**w.config._header_factory(), "Content-Type": "application/json"},
                              json={"input": [{"role": "user", "content": q}]}, timeout=180)
            r.raise_for_status()
            out = {**_parse_agent_response(r.json()), "mode": "served", "endpoint": AGENT_ENDPOINT}
            try:
                agent._ensure_cache()
                agent._write(key, q, _json.dumps(out))
            except Exception:
                pass
            return {**out, "cache": "live" if live else "miss"}
        except Exception as e:
            fallback_reason = f"served endpoint call failed: {str(e)[:120]}"
    else:
        fallback_reason = f"served endpoint {AGENT_ENDPOINT} is {state.lower().replace('_', ' ')}"
    fb = agent.ask(q, use_cache=not live)
    return {"text": fb.get("text", ""), "specialist": None, "route_reason": None,
            "tools": [{"name": t.get("tool"), "args": t.get("args"), "rows": t.get("rows")} for t in fb.get("tools_called", [])],
            "mode": "fallback", "fallback_reason": fallback_reason, "endpoint": config.FM_ENDPOINT,
            "cache": fb.get("cache")}


# ─────────────────────────── event maps (Contract D) ───────────────────────────
@app.get("/api/maps")
def api_maps():
    """Which AI/BI dashboard page shows the map for each event (ref_dashboard_pages)."""
    rows, err = _try(f"SELECT * FROM {config.fqn('ref_dashboard_pages')}")  # incl. page_url / map_widget if present
    return {"pages": rows, "available": err is None}


# ─────────────────────────── event radar (unstructured news sensor) ───────────────────────────
@app.get("/api/radar")
def api_radar():
    """Verified news signals from fn_news_radar — peril, geocoded location, threatened exposure, a confidence
    and evidence trail, and the beats_feed flag (touches the book AND no structured feed had it yet)."""
    rows = sql.query(
        f"SELECT signal_id, source, is_live, peril_code, severity, place_name, published_at, title, summary, "
        f"threatened_count, sum_insured, corroborated, beats_feed, confidence, status, evidence "
        f"FROM {config.fqn('fn_news_radar')}() ORDER BY beats_feed DESC, confidence DESC")
    return {"signals": rows}


@app.post("/api/radar/promote")
async def api_radar_promote(request: Request):
    """HUMAN-GATED. Promote a verified signal to a provisional NEWS event so it flows through the exposure view
    and the alert path. Idempotent; every promotion is audited to gov_news_decision. Never runs autonomously."""
    body = await request.json()
    sid = (body.get("signal_id") or "").strip()
    if not sid:
        return {"ok": False, "reason": "signal_id required"}
    eid = f"EVT_NEWS_{sid}"
    fq = config.fqn
    p = {"eid": eid, "sid": sid}  # bound params — signal_id comes from the request body (untrusted)
    box = ("concat('POLYGON((', "
           "cast(s.longitude-0.03 as string),' ',cast(s.latitude-0.03 as string),', ', "
           "cast(s.longitude+0.03 as string),' ',cast(s.latitude-0.03 as string),', ', "
           "cast(s.longitude+0.03 as string),' ',cast(s.latitude+0.03 as string),', ', "
           "cast(s.longitude-0.03 as string),' ',cast(s.latitude+0.03 as string),', ', "
           "cast(s.longitude-0.03 as string),' ',cast(s.latitude-0.03 as string),'))')")
    try:
        sql.query(
            f"INSERT INTO {fq('`2_event_footprint`')} "
            f"(event_id, peril_code, event_name, event_date, footprint_wkt, source, is_live, source_detail, ingested_at) "
            f"SELECT :eid, s.peril_code, concat('(News) ', coalesce(s.place_name,'?'), ' ', s.peril_code), "
            f"current_date(), {box}, 'NEWS', true, concat('Promoted from news signal ', :sid, ' - human-approved'), current_timestamp() "
            f"FROM {fq('`2_news_signal`')} s "
            f"WHERE s.signal_id = :sid AND s.latitude IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {fq('`2_event_footprint`')} WHERE event_id = :eid)", p)
        sql.query(
            f"INSERT INTO {fq('gov_news_decision')} "
            f"(signal_id, action, event_id, decided_by, decided_at, confidence, evidence) "
            f"SELECT signal_id, 'PROMOTE', :eid, 'exposure-team (demo)', current_timestamp(), confidence, evidence "
            f"FROM {fq('fn_news_radar')}() WHERE signal_id = :sid", p)
        return {"ok": True, "event_id": eid}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


# ─────────────────────────── live event: follow a developing fire + anti-selection vignette ───────────────────────────
# Two synthetic applicants for the simulated buy journey (deterministic, no payment, no PII):
#   var    — a home in the Var whose address the growing fire reaches → DECLINED at bind (active zone)
#   munich — a home far from any active event → APPROVED (cover bound)
APPLICANTS = {
    "var": {"object_ref": "IO0001856", "applicant": "Prospective policyholder — villa, Var",
            "lat": 43.586962, "lon": 6.742300, "city": "Var (SE France)", "postcode": "13823",
            "sum_insured": 7575254.27, "coverage": "BUILDINGS + CONTENTS"},
    "munich": {"object_ref": "IO0004465", "applicant": "Prospective policyholder — apartment, Munich",
               "lat": 48.112706, "lon": 11.618368, "city": "Munich (DE)", "postcode": "81667",
               "sum_insured": 7993646.77, "coverage": "BUILDINGS + CONTENTS"},
}
def _applicant(which): return APPLICANTS.get((which or "var").strip().lower(), APPLICANTS["var"])
HERO = APPLICANTS["var"]  # back-compat default
LIVE_EID = "EVT_LIVE_VAR_FIRE"


@app.get("/api/live/ticks")
def api_live_ticks():
    """The developing-fire progression (news T0 → satellite corroboration → growing perimeter). The browser
    'follows' it, stepping ticks; each tick's threatened count/SI climbs. mv_progression_exposure (governed)."""
    rows = sql.query(
        f"SELECT t_index, label, as_of, corroborated, feed_source, threatened_count, sum_insured_eur "
        f"FROM {config.fqn('mv_progression_exposure')} ORDER BY t_index")
    return {"event_id": LIVE_EID, "event_name": "Var wildfire (developing)", "hero": HERO, "ticks": rows}


@app.post("/api/live/cursor")
async def api_live_cursor(request: Request):
    """Point the Live Fire Tracker dashboard at the tick the presenter is on. The dashboard reads
    live_cursor, so following / Next update / Reset in the app moves the embedded dashboard in step."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        ti = max(0, min(int(body.get("t_index", 0)), 50))
    except (TypeError, ValueError):
        ti = 0
    try:
        sql.query(f"DELETE FROM {config.fqn('live_cursor')}")
        sql.query(f"INSERT INTO {config.fqn('live_cursor')} VALUES (:eid, CAST(:ti AS INT))", {"eid": LIVE_EID, "ti": ti})
        return {"ok": True, "t_index": ti}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:200]}


@app.post("/api/bind/quote")
async def api_bind_quote(request: Request):
    """Issue an indicative quote for the chosen applicant (?which=var|munich). Quoting always succeeds — governance is at BIND."""
    import uuid
    a = _applicant(request.query_params.get("which"))
    return {"quote_id": "Q-" + uuid.uuid4().hex[:8].upper(), "which": request.query_params.get("which") or "var", **a,
            "premium_eur": round(a["sum_insured"] * 0.0042)}


def _bind_narration(reason: str, decision: str, dist: float, chk: dict, override: bool) -> str:
    """The underwriting agent (Claude/FMAPI) explains the governed bind decision — grounded only in the facts."""
    facts = (f"Decision={decision}. Applicant address distance to the active fire footprint = {int(dist)} m "
             f"(0 = inside the footprint). Active event={chk.get('active_event')}, as of {chk.get('as_of')}, "
             f"source: {chk.get('feed_source')}.")
    sysmsg = ("You are the underwriting agent for a European property insurer, enforcing a governed bind-time rule: "
              "cover cannot be bound on a property inside an active catastrophe (wildfire) zone — that would be "
              "adverse selection. Explain the decision to the applicant in TWO concise, plain, polite sentences, "
              "grounded ONLY in the facts given; never invent numbers. If DECLINED, say cover can't be bound right "
              "now because the address is in an active fire zone, and refer them to an underwriter. If BOUND, confirm "
              "cover is bound and that the property is currently clear of the fire. If OVERRIDDEN, note an underwriter "
              "has overridden the automatic block and accepted the risk.")
    try:
        m = agent._fm_chat([{"role": "system", "content": sysmsg}, {"role": "user", "content": facts}])
        return (m.get("content") or "").strip() or reason
    except Exception:
        return reason


@app.post("/api/bind/buy")
async def api_bind_buy(request: Request):
    """Bind-time governance (anti-selection). Runs the governed fn_bind_check against the CURRENT fire footprint;
    declines if the address is in the active zone. Human override supported. Every decision audited (gov_bind_decision)."""
    body = await request.json()
    qid = (body.get("quote_id") or "Q-DEMO").strip()  # bound as :qid in the audit INSERT below
    try:
        ti = int(body.get("t_index", 0))
    except (TypeError, ValueError):
        ti = 0
    override = bool(body.get("override", False))
    a = _applicant(body.get("which"))
    fq = config.fqn
    chk = sql.query_one(
        f"SELECT in_zone, round(distance_m,1) AS distance_m, active_event, as_of, feed_source "
        f"FROM {fq('fn_bind_check')}({a['lon']}, {a['lat']}, {ti})") or {}
    in_zone = str(chk.get("in_zone")).lower() == "true"
    dist = float(chk.get("distance_m") or 0)
    if in_zone and not override:
        decision, reason = "DECLINED", f"Address is in the active fire zone as of {chk.get('as_of')} ({chk.get('feed_source')}) — cannot bind."
    elif in_zone and override:
        decision, reason = "OVERRIDDEN", "Underwriter override — risk accepted against the active-zone block."
    else:
        decision, reason = "BOUND", f"Clear of the active fire zone ({int(dist)} m away) at bind time — cover bound."
    agent_text = _bind_narration(reason, decision, dist, chk, override)
    try:
        sql.query(
            f"INSERT INTO {fq('gov_bind_decision')} (quote_id, applicant, object_ref, latitude, longitude, t_index, "
            f"decision, reason, active_event_id, distance_m, as_of, decided_at, decided_by, overridden) "
            f"VALUES (:qid, :app, :obj, {a['lat']}, {a['lon']}, {ti}, :dec, :rsn, :evt, {dist}, :aso, "
            f"current_timestamp(), :by, {str(override).lower()})",
            {"qid": qid, "app": a["applicant"], "obj": a["object_ref"], "dec": decision, "rsn": reason,
             "evt": chk.get("active_event") or LIVE_EID, "aso": chk.get("as_of") or "",
             "by": "underwriter (demo)" if override else "bind-time rule"})
    except Exception:
        pass
    return {"decision": decision, "reason": reason, "agent_text": agent_text, "distance_m": dist,
            "in_zone": in_zone, "as_of": chk.get("as_of"), "feed_source": chk.get("feed_source"),
            "quote_id": qid, "overridden": override}


@app.get("/api/bind/audit")
def api_bind_audit():
    """The append-only bind-decision log — every quote→buy outcome, its reason, and the active event at the time."""
    rows = sql.query(
        f"SELECT quote_id, object_ref, t_index, decision, reason, round(distance_m) AS distance_m, "
        f"CAST(decided_at AS STRING) AS decided_at, decided_by, overridden "
        f"FROM {config.fqn('gov_bind_decision')} ORDER BY decided_at DESC LIMIT 20")
    return {"decisions": rows}


# ─────────────────────────── static SPA ───────────────────────────
@app.get("/")
def index():
    return FileResponse(os.path.join(DIST, "index.html"))


@app.get("/healthz")
def healthz():
    return {"ok": True}
