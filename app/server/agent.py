"""Event Response agent — Claude (Foundation Model API) with a governed-function tool surface.

The model NARRATES; the governed UC functions DECIDE. Every number in an answer comes from a real
tool call that runs an fn_* on the SQL warehouse — the model is never asked to compute or recall a figure.
Tool-calling loop uses the OpenAI-compatible chat schema the Databricks FM endpoints expose.

An answer (question → text + the tools actually called) is cached in a Delta table so a demo beat never
stalls on the model; the visible live/cached toggle (the "yellow button") switches modes. Cache wraps the
LLM turn only — the tools always hit live governed functions.
"""
import hashlib, json
from . import config, sql

CACHE_TABLE = f"{config.CATALOG}.{config.SCHEMA}.cache_agent_responses"

SYSTEM = (
    "You are the Event Response analyst for {entity}, a European property insurer. A catastrophe event "
    "(wildfire, flood or windstorm) is developing and the exposure team needs fast, defensible answers about "
    "the insured property book. You answer ONLY from the governed tools provided — never invent, estimate or "
    "recall a number. Call the tools, then narrate what they return in plain, senior-ready language. "
    "Money is EUR; report large sums in millions (e.g. €164.3m). Distances are true metres (50/100/200 m bands; "
    "inside the footprint is 0 m). One event can cross borders — always surface the whole cross-border picture, "
    "never a single country's slice. When asked 'how sure am I', explain the exposure is computed by governed "
    "Unity Catalog functions over the current footprint and book, and name the footprint's source and whether it "
    "is a live feed or a frozen illustrative footprint. Be concise: lead with the headline number, then the split."
).format(entity=config.ENTITY)

# ── tool surface: each maps to a governed fn_* on the warehouse ──
TOOLS = [
    {"type": "function", "function": {
        "name": "list_events",
        "description": "List the active catastrophe events on the book (id, peril, name, date, live/frozen feed source, and threatened-property count within 200 m). Call this first if the user names an event so you can resolve it to an event_id.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "exposure_summary",
        "description": "Threatened exposure for one event within a distance buffer, rolled up by distance band and by country. Returns counts and sum insured (EUR). Use for 'what is my exposure' and cross-border questions.",
        "parameters": {"type": "object", "properties": {
            "event_id": {"type": "string"}, "buffer_m": {"type": "integer", "description": "50, 100 or 200", "default": 200}},
            "required": ["event_id"]}}},
    {"type": "function", "function": {
        "name": "gross_to_net",
        "description": "The gross modelled loss for an event ceded through the Property Cat XL treaty tower to a net retained figure (waterfall by layer). Use for gross/net-of-treaty questions.",
        "parameters": {"type": "object", "properties": {
            "event_id": {"type": "string"}, "buffer_m": {"type": "integer", "default": 200}}, "required": ["event_id"]}}},
    {"type": "function", "function": {
        "name": "exposure_by_coverholder",
        "description": "Threatened exposure for an event split by coverholder / delegated authority (binder). Use for 'which delegated authority / MGA is most exposed'.",
        "parameters": {"type": "object", "properties": {
            "event_id": {"type": "string"}, "buffer_m": {"type": "integer", "default": 200}}, "required": ["event_id"]}}},
    {"type": "function", "function": {
        "name": "event_delta",
        "description": "What changed for an event since the last snapshot: newly-threatened, still-threatened, no-longer-threatened counts and sum insured. Use for 'what changed / what is new'.",
        "parameters": {"type": "object", "properties": {
            "event_id": {"type": "string"}, "buffer_m": {"type": "integer", "default": 200}}, "required": ["event_id"]}}},
]


def _buf(a):
    try:
        b = int(a.get("buffer_m", 200))
    except (TypeError, ValueError):
        b = 200
    return b if b in (50, 100, 200) else 200


def _run_tool(name: str, args: dict):
    """Execute a tool by calling the matching governed function on the warehouse. Returns list[dict] rows."""
    fq = config.fqn
    eid = sql.esc(str(args.get("event_id", "")))
    b = _buf(args)
    if name == "list_events":
        return sql.query(f"SELECT event_id, peril_code, event_name, CAST(event_date AS STRING) event_date, "
                         f"source, is_live, threatened_count FROM {fq('fn_events')}(200) ORDER BY threatened_count DESC")
    if name == "exposure_summary":
        return sql.query(f"SELECT dim, dim_key, n_objects, sum_insured_eur FROM {fq('fn_event_exposure_summary')}('{eid}', {b}) ORDER BY dim, dim_key")
    if name == "gross_to_net":
        return sql.query(f"SELECT step_label, layer_name, ceded_eur, running_net_eur, kind FROM {fq('fn_gross_to_net')}('{eid}', {b}) ORDER BY seq")
    if name == "exposure_by_coverholder":
        return sql.query(f"SELECT coverholder_name, binder_ref, is_delegated, n_objects, sum_insured_eur FROM {fq('fn_exposure_by_coverholder')}('{eid}', {b}) ORDER BY sum_insured_eur DESC")
    if name == "event_delta":
        return sql.query(f"SELECT status, n_objects, sum_insured_eur FROM {fq('fn_event_delta')}('{eid}', {b})")
    return [{"error": f"unknown tool {name}"}]


# ── cache (LLM turn only) ──
def _key(question: str) -> str:
    return hashlib.sha256(json.dumps({"q": question, "m": config.FM_ENDPOINT}, sort_keys=True).encode()).hexdigest()[:32]


def _ensure_cache():
    sql.query(f"CREATE TABLE IF NOT EXISTS {CACHE_TABLE} (cache_key STRING, question STRING, response STRING, created_ts TIMESTAMP) USING DELTA")


def _read(key: str):
    # key is a 32-char hex digest (literal-safe), but bind it anyway for consistency.
    row = sql.query_one(f"SELECT response FROM {CACHE_TABLE} WHERE cache_key = :key LIMIT 1", {"key": key})
    return row["response"] if row else None


def _write(key: str, question: str, response: str):
    # Bind response/question as parameters — they carry JSON (backslashes, quotes, newlines) that would
    # corrupt a SQL string literal (Databricks interprets backslash escapes inside literals).
    sql.query(
        f"MERGE INTO {CACHE_TABLE} t USING (SELECT :key AS k) s ON t.cache_key = s.k "
        f"WHEN MATCHED THEN UPDATE SET response = :resp, question = :q, created_ts = current_timestamp() "
        f"WHEN NOT MATCHED THEN INSERT (cache_key, question, response, created_ts) "
        f"VALUES (:key, :q, :resp, current_timestamp())",
        {"key": key, "q": question, "resp": response})


def _fm_chat(messages, tools=None):
    import requests
    w = config.get_workspace_client()
    host = config.workspace_host()
    if host and not host.startswith("http"):
        host = "https://" + host
    hdr = w.config._header_factory()
    body = {"messages": messages, "max_tokens": 1024, "temperature": 0}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    r = requests.post(f"{host}/serving-endpoints/{config.FM_ENDPOINT}/invocations",
                      headers={**hdr, "Content-Type": "application/json"}, json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def ask(question: str, use_cache: bool = None) -> dict:
    """Answer a question via the tool-calling loop. Returns {text, tools_called, cache, endpoint}."""
    if use_cache is None:
        use_cache = config.USE_CACHE
    key = _key(question)
    if use_cache:
        try:
            _ensure_cache()
            hit = _read(key)
            if hit is not None:
                d = json.loads(hit)
                return {"text": d.get("text", ""), "tools_called": d.get("tools_called", []), "cache": "hit", "endpoint": config.FM_ENDPOINT}
        except Exception:
            pass
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    tools_called = []
    try:
        for _ in range(6):
            msg = _fm_chat(messages, TOOLS)
            calls = msg.get("tool_calls") or []
            if not calls:
                text = msg.get("content") or ""
                break
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            for c in calls:
                fn = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except Exception:
                    args = {}
                rows = _run_tool(fn, args)
                tools_called.append({"tool": fn, "args": args, "rows": len(rows)})
                messages.append({"role": "tool", "tool_call_id": c.get("id"),
                                 "content": json.dumps(rows, default=str)[:6000]})
        else:
            text = "[the agent took too many steps — please narrow the question]"
    except Exception as e:
        return {"text": f"[agent unavailable: {str(e)[:180]}]", "tools_called": tools_called, "cache": "error", "endpoint": config.FM_ENDPOINT}
    try:
        _ensure_cache()
        _write(key, question, json.dumps({"text": text, "tools_called": tools_called}))
    except Exception:
        pass
    return {"text": text, "tools_called": tools_called, "cache": ("miss" if use_cache else "live"), "endpoint": config.FM_ENDPOINT}


STARTERS = [
    "What's my exposure to the Var wildfire, gross and net, and how sure am I?",
    "Which delegated authority is most exposed to the Alpine flood?",
    "Show me the cross-border exposure for the Alpine flood by country.",
    "What's the biggest live event on the book right now?",
]
