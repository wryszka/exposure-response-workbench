"""
Exposure & Event Response — agent team (Mosaic AI Agent Framework ResponsesAgent, MLflow 3).

One served endpoint, `exposure-response-agent`, holds a SUPERVISOR and SIX SPECIALISTS:

    question ──► Supervisor (fast Claude, routing only, no tools)
                   │  picks 1 specialist (or chains 2) + records why
                   ▼
    Event Watch · Exposure Analyst · Reinsurance Analyst · Alerts Officer · Underwriting Guard · Data Steward
                   │  each: its own responsibilities, limits and a RESTRICTED subset of governed tools
                   ▼
    Governed Unity Catalog functions, executed on the SQL warehouse (Statement Execution API)

Advise-only, deterministic engines decide: every number in an answer comes from a governed UC function call —
specialists never invent, estimate or recall a figure. The functions are executed on the SQL warehouse (not
Spark Connect) because the exposure maths uses ST_ geospatial functions, which only run on a SQL warehouse.

Configuration (LLMs, warehouse, tool specs) is logged with the model via `model_config`, so redeploying picks up
new governed functions without editing this file.
"""
import json
import re
import time
import uuid
from typing import Any, Generator

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

# ── The team ─────────────────────────────────────────────────────────────────────────────────────────────
# Single source of truth for routing, prompts AND the ref_agent_roster table the app shows.
SPECIALISTS: dict[str, dict[str, Any]] = {
    "event_watch": {
        "name": "Event Watch",
        "role": "Watches the world for events that could touch the book.",
        "responsibilities": [
            "List the active catastrophe events and whether each came from a live feed or a frozen sample",
            "Explain where each event came from (MeteoAlarm, NASA FIRMS, Copernicus EFFIS/EMS, Environment Agency, GDACS, press)",
            "Flag early signals on the Event Radar that touch the book before the satellite feed confirms them",
            "Describe how a developing event is progressing tick by tick",
        ],
        "tools": ["fn_events", "fn_news_radar", "fn_source_status", "fn_progression_props"],
        "reads": ["2_event_footprint", "2_news_signal", "1_news_raw", "gov_source_catalog", "2_event_progression"],
        "may_do": ["Report events, sources and early-warning signals", "Point the user to the right specialist"],
        "may_not_do": ["Promote a news signal to an event (a person clicks Promote)", "Change alert thresholds or feed settings"],
        "examples": [
            "What events are active right now, and which are live?",
            "Is anything on the radar that beats the satellite?",
            "How is the Var fire developing?",
        ],
    },
    "exposure_analyst": {
        "name": "Exposure Analyst",
        "role": "Answers who is in the path — homes, distance bands, countries, sum insured.",
        "responsibilities": [
            "Count the insured homes inside or near an event footprint at 50 / 100 / 200 m",
            "Split threatened exposure by country — one event across borders stays one event",
            "Report sum insured at risk and list the most exposed properties",
            "Follow the developing Var fire tick by tick (homes in the path as it grows)",
        ],
        "tools": ["fn_events", "fn_exposure_in_footprint", "fn_event_exposure_summary", "fn_progression_props"],
        "reads": ["3_property", "2_event_footprint", "2_event_progression"],
        "may_do": ["Report threatened homes and sum insured from the governed functions"],
        "may_not_do": ["Estimate a loss or a net figure (that is the Reinsurance Analyst)", "Decide whether cover can be bound"],
        "examples": [
            "Which homes are in the path of the Alpine flood, by country?",
            "How many homes are within 50 m of the Var wildfire?",
            "How many homes are in the fire's path at the latest update?",
        ],
    },
    "reinsurance_analyst": {
        "name": "Reinsurance Analyst",
        "role": "Turns exposure into money — modelled gross loss, what reinsurance pays, what we keep.",
        "responsibilities": [
            "Run the gross → ceded → net waterfall through the Property Cat XL programme (EUR 25m retention, four layers to EUR 400m)",
            "Explain which layers attach and why net differs between small, mid and severe events",
            "Split threatened exposure by coverholder / delegated authority",
        ],
        "tools": ["fn_events", "fn_gross_to_net", "fn_exposure_by_coverholder", "fn_event_exposure_summary"],
        "reads": ["3_treaty", "3_treaty_layer", "ref_damage_factor", "3_coverholder", "3_property_coverholder"],
        "may_do": ["Report modelled gross, ceded and net figures and the coverholder split"],
        "may_not_do": ["Present the modelled net as a signed-off capital or reserving figure", "Change treaty terms or damage factors"],
        "examples": [
            "What's my exposure to the Var wildfire, gross and net?",
            "Which coverholder is most exposed to the Alpine flood?",
            "Why does Windstorm Celine leave us with more than the retention?",
        ],
    },
    "alerts_officer": {
        "name": "Alerts Officer",
        "role": "Knows who was told, when, and why.",
        "responsibilities": [
            "Report the stakeholder alert for an event: threshold breached, recipients, countries, time",
            "Explain what changed since the last snapshot (newly, still and no-longer threatened homes)",
            "Explain alert status — sent, built-but-not-sent (no delivery channel yet), or suppressed below threshold",
        ],
        "tools": ["fn_events", "fn_alert_log", "fn_event_delta"],
        "reads": ["gov_alert_dispatch", "gov_exposure_snapshot", "ref_alert_threshold"],
        "may_do": ["Report the alert record and the deltas"],
        "may_not_do": ["Send an alert (a governed scheduled job does)", "Change recipients or thresholds"],
        "examples": [
            "Who was alerted about the Alpine flood?",
            "What changed for the Var wildfire since the last snapshot?",
            "Did the alert for the Var fire actually send?",
        ],
    },
    "underwriting_guard": {
        "name": "Underwriting Guard",
        "role": "Checks whether new cover can be bound at an address while an event is active.",
        "responsibilities": [
            "Resolve the address, town or UK postcode district to coordinates (never guess them)",
            "Check it against every active disaster zone — including UK Environment Agency flood warnings",
            "Check it against the developing Var fire's footprint at the latest update",
            "Explain, in plain words, why cover would be referred or declined",
        ],
        "tools": ["fn_place_lookup", "fn_zone_check", "fn_bind_check", "fn_events"],
        "reads": ["mv_active_zones", "2_event_progression", "ref_gazetteer", "underwriting_workbench.ref_postcode_centroid", "gov_bind_decision"],
        "may_do": ["Explain a bind / zone decision and the zone that triggered it"],
        "may_not_do": ["Override a decline or approve cover (only an underwriter can)", "Invent coordinates — always look the place up first"],
        "examples": [
            "Can we bind cover in the Var right now?",
            "Is Munich inside any active disaster zone?",
            "Can we bind cover at UK postcode district YO1?",
        ],
    },
    "data_steward": {
        "name": "Data Steward",
        "role": "Vouches for the data — sources, freshness, quality checks, rejected batches.",
        "responsibilities": [
            "List the data sources: what each carries, real-time or batch, and when it last arrived",
            "Report the data-quality checks on each batch and their thresholds",
            "Explain any batch that was rejected or quarantined, and why",
        ],
        "tools": ["fn_source_status", "fn_dq_recent", "fn_events"],
        "reads": ["gov_source_catalog", "gov_feed_dq_log", "1_feed_quarantine", "ref_dq_threshold", "1_hazard_feed_raw"],
        "may_do": ["Report source status and data-quality decisions"],
        "may_not_do": ["Accept a rejected batch or lower a quality threshold", "Edit or delete feed data"],
        "examples": [
            "Was any feed batch rejected today, and why?",
            "When did each data source last arrive?",
            "Which sources are live and which are frozen samples?",
        ],
    },
}

SHARED_RULES = """You are part of the Bricksurance SE Exposure & Event Response agent team. Bricksurance SE is a fictional
European insurer; all data is synthetic demo data (no real customer data).

HARD RULES
- Answer ONLY from the results of your tools. Never invent, estimate, recall or extrapolate a number, a date, a
  place or a coordinate. If a tool returns nothing, say so plainly. If you lack a tool for the question, say which
  colleague can answer it.
- Quote figures exactly as the tools return them. Money: write as EUR, rounded to EUR 0.1m (e.g. EUR 164.3m).
  Losses from the waterfall are MODELLED figures — say "modelled".
- Plain language for a busy, non-technical exposure manager. Lead with the answer in one or two sentences, then at
  most a few short bullets. No jargon (no function names in the body, no SQL).
- End with one short line: "Source: <the governed functions you used>".

HOW TO USE THE DATA
- Event ids: if unsure, call fn_events first. Known ids: EVT_FIRE_VAR = Var wildfire (France); EVT_FLOOD_ALPS =
  Alpine flood (Italy + Austria, one event across the border); EVT_STORM_CELINE = Windstorm Celine; EVT_STORM_NORD =
  Windstorm Nord; EVT_FIRE_ESTEREL = Esterel brush fire. Ids starting EVT_LIVE_ are real live warnings from today.
  UK Environment Agency flood warnings have source EA.
- p_buffer_m is metres from the event footprint; the standard distance bands are 50 / 100 / 200 m; default 200.
- The developing Var fire (live follow) is EVT_LIVE_VAR_FIRE; its ticks are 0..3 (0 = just started, 3 = latest).
  Use fn_progression_props(p_t_index) for homes in its path, and p_t_index = 3 in fn_bind_check unless told otherwise.
"""

ROUTER_PROMPT = """You are the SUPERVISOR of the Bricksurance Exposure & Event Response agent team. You do not answer
questions yourself — you route each question to the right specialist.

Specialists:
{roster}

Reply with JSON ONLY, no prose, in exactly this shape:
{{"specialists": ["<key>"], "route_reason": "<one short sentence: why this specialist>"}}
Use one specialist normally. Use two (in order) only when the question clearly needs both, e.g.
["exposure_analyst", "reinsurance_analyst"] for "who is in the path and what will it cost us".
Valid keys: {keys}."""

DEFAULT_CONFIG = {
    "llm_endpoint": "databricks-claude-sonnet-5-5",
    "router_endpoint": "databricks-claude-haiku-5-5",
    "warehouse_id": "a3b61648ea4809e3",
    "catalog": "lr_dev_aws_us_catalog",
    "schema": "exposure_response",
    "max_tool_rounds": 6,
    "tools": [],   # filled by the build notebook: [{"name","description","params":[{"name","type","default","comment"}]}]
}

_KEYWORDS = [
    ("data_steward", r"\b(reject|quarantin|quality|dq|threshold.*batch|batch|source|feed.*(arriv|fresh|last)|ingest)"),
    ("underwriting_guard", r"\b(bind|cover at|buy cover|can we (insure|write|sell)|postcode|quote|zone check|in.*zone)"),
    ("alerts_officer", r"\b(alert|notif|who was told|email|dispatch|since the last|what changed)"),
    ("reinsurance_analyst", r"\b(gross|net|reinsur|treaty|retention|layer|ceded|coverholder|delegated|cost)"),
    ("event_watch", r"\b(radar|satellite|news|beats? the feed|live events?|what events|active events|progress)"),
    ("exposure_analyst", r"\b(homes?|propert|in the path|exposure|band|country|countries|sum insured)"),
]


def _sql_type(t: str) -> str:
    t = (t or "STRING").upper()
    if t.startswith(("INT", "BIGINT", "SMALLINT", "TINYINT", "LONG")):
        return "INT"
    if t.startswith(("DOUBLE", "FLOAT", "DECIMAL")):
        return "DOUBLE"
    if t.startswith("BOOLEAN"):
        return "BOOLEAN"
    return "STRING"


def _json_type(t: str) -> str:
    return {"INT": "integer", "DOUBLE": "number", "BOOLEAN": "boolean"}.get(_sql_type(t), "string")


class ExposureAgentTeam(ResponsesAgent):
    """Supervisor + six specialists over governed UC functions."""

    def __init__(self, config: dict | None = None):
        cfg = dict(DEFAULT_CONFIG)
        if config:
            cfg.update(config)
        self.cfg = cfg
        self.fqn = f"{cfg['catalog']}.{cfg['schema']}"
        self.tool_specs = {t["name"]: t for t in cfg.get("tools", [])}
        self._w = None
        self._oa = None

    # ── clients (lazy: created inside the serving container with passthrough credentials) ──
    @property
    def w(self):
        if self._w is None:
            from databricks.sdk import WorkspaceClient
            self._w = WorkspaceClient()
        return self._w

    @property
    def llm(self):
        if self._oa is None:
            self._oa = self.w.serving_endpoints.get_open_ai_client()
        return self._oa

    # ── tools ────────────────────────────────────────────────────────────────────────────────────────────
    def _openai_tools(self, names: list[str]) -> list[dict]:
        out = []
        for n in names:
            spec = self.tool_specs.get(n)
            if not spec:
                continue  # function not deployed in this workspace yet — the specialist will say so
            props, required = {}, []
            for p in spec.get("params", []):
                props[p["name"]] = {"type": _json_type(p["type"]), "description": (p.get("comment") or p["name"])[:300]}
                if p.get("default") in (None, "", "None"):
                    required.append(p["name"])
            out.append({"type": "function", "function": {
                "name": n,
                "description": (spec.get("description") or n)[:1500],
                "parameters": {"type": "object", "properties": props, "required": required},
            }})
        return out

    @mlflow.trace(span_type="TOOL")
    def run_tool(self, name: str, args: dict) -> str:
        spec = self.tool_specs.get(name)
        if not spec:
            return json.dumps({"error": f"tool {name} is not available"})
        from databricks.sdk.service.sql import StatementParameterListItem
        call_args, params = [], []
        for p in spec.get("params", []):
            if p["name"] in args and args[p["name"]] is not None:
                call_args.append(f"{p['name']} => :{p['name']}")
                params.append(StatementParameterListItem(name=p["name"], value=str(args[p["name"]]), type=_sql_type(p["type"])))
        stmt = f"SELECT * FROM {self.fqn}.{name}({', '.join(call_args)})"
        try:
            r = self.w.statement_execution.execute_statement(
                statement=stmt, warehouse_id=self.cfg["warehouse_id"], parameters=params or None, wait_timeout="30s")
            waited = 0
            while r.status.state.value in ("PENDING", "RUNNING") and waited < 90:
                time.sleep(2); waited += 2
                r = self.w.statement_execution.get_statement(r.statement_id)
            if r.status.state.value != "SUCCEEDED":
                msg = r.status.error.message if r.status.error else r.status.state.value
                return json.dumps({"error": msg[:500]})
            cols = [c.name for c in r.manifest.schema.columns]
            rows = (r.result.data_array or []) if r.result else []
            data = [dict(zip(cols, row)) for row in rows[:60]]
            out = {"rows": data, "row_count": len(rows)}
            if len(rows) > 60:
                out["note"] = f"showing first 60 of {len(rows)} rows"
            s = json.dumps(out, default=str)
            return s if len(s) <= 9000 else s[:9000] + '..."(truncated)"'
        except Exception as e:  # surfaced to the model, which must then say it could not get the figure
            return json.dumps({"error": str(e)[:500]})

    # ── supervisor ───────────────────────────────────────────────────────────────────────────────────────
    @mlflow.trace(span_type="CHAIN")
    def route(self, question: str) -> tuple[list[str], str]:
        roster = "\n".join(f"- {k}: {v['name']} — {v['role']} Handles: {'; '.join(v['responsibilities'][:3])}."
                           for k, v in SPECIALISTS.items())
        prompt = ROUTER_PROMPT.format(roster=roster, keys=", ".join(SPECIALISTS))
        try:
            resp = self.llm.chat.completions.create(
                model=self.cfg["router_endpoint"], max_tokens=200,
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": question}])
            text = resp.choices[0].message.content or ""
            m = re.search(r"\{.*\}", text, re.S)
            d = json.loads(m.group(0)) if m else {}
            keys = [k for k in d.get("specialists", []) if k in SPECIALISTS][:2]
            if keys:
                return keys, str(d.get("route_reason", "")).strip() or "routed by the supervisor"
        except Exception:
            pass
        q = question.lower()
        for key, pat in _KEYWORDS:  # deterministic fallback if the router reply can't be parsed
            if re.search(pat, q):
                return [key], "keyword routing (supervisor reply unavailable)"
        return ["exposure_analyst"], "default routing"

    # ── specialist loop ──────────────────────────────────────────────────────────────────────────────────
    @mlflow.trace(span_type="AGENT")
    def run_specialist(self, key: str, history: list[dict], colleague_note: str | None, tool_log: list) -> str:
        sp = SPECIALISTS[key]
        available = [t for t in sp["tools"] if t in self.tool_specs]
        missing = [t for t in sp["tools"] if t not in self.tool_specs]
        system = (f"You are {sp['name']}. {sp['role']}\n\nYour responsibilities:\n- " + "\n- ".join(sp["responsibilities"])
                  + "\n\nYou may: " + "; ".join(sp["may_do"]) + ".\nYou must NOT: " + "; ".join(sp["may_not_do"]) + ".\n\n"
                  + SHARED_RULES)
        if missing:
            system += f"\nNot yet available to you in this workspace: {', '.join(missing)}. If the question needs them, say that data isn't connected yet.\n"
        msgs = [{"role": "system", "content": system}] + history
        if colleague_note:
            msgs.append({"role": "user", "content": f"(Note from your colleague, for context — reuse their figures, don't repeat their work)\n{colleague_note}"})
        tools = self._openai_tools(available)
        for _ in range(int(self.cfg["max_tool_rounds"])):
            kwargs = {"model": self.cfg["llm_endpoint"], "messages": msgs, "max_tokens": 1200}
            if tools:
                kwargs["tools"] = tools
            resp = self.llm.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            if not calls:
                return (msg.content or "").strip()
            msgs.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                                        for c in calls]})
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except Exception:
                    args = {}
                tool_log.append({"name": c.function.name, "args": args, "specialist": sp["name"]})
                msgs.append({"role": "tool", "tool_call_id": c.id, "content": self.run_tool(c.function.name, args)})
        return "I couldn't complete that within my tool budget — please ask a narrower question."

    # ── ResponsesAgent interface ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _history(req: ResponsesAgentRequest) -> list[dict]:
        hist = []
        for item in req.input:
            d = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            role, content = d.get("role"), d.get("content")
            if role not in ("user", "assistant") or content is None:
                continue
            if isinstance(content, list):
                content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
            hist.append({"role": role, "content": str(content)})
        return hist[-8:]

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        self._oa = None  # fresh Foundation Model client per request (short-lived OAuth tokens in serving)
        history = self._history(request)
        question = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        keys, reason = self.route(question)
        tool_log: list = []
        answer, note = "", None
        for key in keys:
            answer = self.run_specialist(key, history, note, tool_log)
            note = f"{SPECIALISTS[key]['name']} answered:\n{answer}"
        names = [SPECIALISTS[k]["name"] for k in keys]
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=answer, id=f"msg_{uuid.uuid4().hex[:12]}")],
            custom_outputs={
                "specialist": " → ".join(names),
                "specialist_keys": keys,
                "route_reason": reason,
                "tools": [{"name": t["name"], "args": t["args"]} for t in tool_log],
                "tool_calls": tool_log,
            },
        )

    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[ResponsesAgentStreamEvent, None, None]:
        resp = self.predict(request)
        for item in resp.output:
            yield ResponsesAgentStreamEvent(type="response.output_item.done", item=item)


def _load_config() -> dict:
    """The logged model_config when served; DEFAULT_CONFIG when run in a notebook."""
    try:
        mc = mlflow.models.ModelConfig(development_config=DEFAULT_CONFIG)
    except Exception:
        return dict(DEFAULT_CONFIG)
    cfg = {}
    for k, v in DEFAULT_CONFIG.items():
        try:
            cfg[k] = mc.get(k)
        except Exception:
            cfg[k] = v
    return cfg


mlflow.openai.autolog()
AGENT = ExposureAgentTeam(_load_config())
mlflow.models.set_model(AGENT)
