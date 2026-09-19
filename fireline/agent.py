"""Agent layer: seven tools + edge-case triage loop (CONTRACTS "Agent tools", PLAN 6.6).

Deterministic engine for the common case, agent for the edge cases, human decides (PLAN 3). The
agent only ever acts through the tools below; it may move an asset in the pessimistic direction on
its own (`add_override`) and must `escalate` anything optimistic as one question with options and a
default. Every number in its final message must come from a tool result of the same loop
(post-check); otherwise the message is replaced and the failure logged.

Works offline with `llm.FakeLLM` (default when `llm=None`).
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from .exposure import OptimisticMoveError, TIER_RANK, apply_override

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REGISTERS = ROOT / "fixtures" / "registers.json"
DATA_REGISTERS_DIR = ROOT / "data" / "registers"

POSTCHECK_FAILED_TEXT = ("Recommendation, not an order. Agent message withheld: number post-check failed "
                         "(the draft quoted a number that is not in any tool result). See the coordinator "
                         "queue and overrides for what was recorded.")
STEP_CAP_TEXT = ("Recommendation, not an order. Agent stopped at the step cap for this asset; the pessimistic "
                 "defaults already applied stand until a coordinator answers.")


# ---------------------------------------------------------------------------
# Scenario store
# ---------------------------------------------------------------------------
class ScenarioStore:
    """scenario_id -> Scenario. Tools address scenarios by id, so the store is the tools' only state."""

    def __init__(self):
        self._by_id: dict[str, object] = {}

    def register(self, scenario) -> str:
        self._by_id[scenario.id] = scenario
        return scenario.id

    def get(self, scenario_id: str):
        try:
            return self._by_id[scenario_id]
        except KeyError:
            raise KeyError(f"unknown scenario_id {scenario_id!r}; known: {sorted(self._by_id)}") from None

    def __contains__(self, scenario_id: str) -> bool:
        return scenario_id in self._by_id

    def ids(self) -> list[str]:
        return sorted(self._by_id)


STORE = ScenarioStore()


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def jsonable(obj):
    """Recursively convert to JSON-serialisable values: inf -> "inf", nan -> None, numpy -> python."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, float):
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
        if math.isnan(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if hasattr(obj, "item"):  # numpy scalar
        return jsonable(obj.item())
    if hasattr(obj, "tolist"):  # numpy array
        return jsonable(obj.tolist())
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _round(v, nd=0):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
            return v
        return round(v, nd) if nd else int(round(v))
    return v


_ASSET_FIELDS = ("asset_id", "name", "asset_class", "municipality", "occupancy", "occupancy_source",
                 "shelter_viable", "tier", "needs_review", "notes", "lead_time_min")
_DECISION_FIELDS = ("decision", "staged", "exit_window_min", "latest_departure_min", "reception_centre",
                    "medical_destination", "checks", "issuer_note")
_ROUTE_FIELDS = ("asset_id", "destination_id", "destination_name", "destination_kind", "travel_min",
                 "first_cut_road", "first_cut_min", "via_track")


def trim_asset(row: dict) -> dict:
    """The fields the agent needs, numbers rounded so they are easy to quote verbatim."""
    out = {k: row.get(k) for k in _ASSET_FIELDS}
    out["burn_prob"] = _round(row.get("burn_prob"), 2)
    for k in ("arrival_p10_min", "arrival_p50_min", "lead_adjusted_p10_min"):
        out[k] = _round(row.get(k))
    out["n_overrides"] = len(row.get("overrides") or [])
    dec = row.get("decision") or {}
    out["decision"] = dec.get("decision")
    out["exit_window_min"] = _round(dec.get("exit_window_min"))
    return jsonable(out)


def trim_decision(dec: dict) -> dict:
    out = {k: dec.get(k) for k in _DECISION_FIELDS}
    out["asset_id"] = dec.get("asset_id")
    for k in ("exit_window_min", "latest_departure_min"):
        out[k] = _round(out.get(k))
    route = dec.get("route")
    out["route"] = trim_route(route) if route else None
    return jsonable(out)


def trim_route(route: dict) -> dict:
    out = {k: route.get(k) for k in _ROUTE_FIELDS}
    out["travel_min"] = _round(out.get("travel_min"))
    out["first_cut_min"] = _round(out.get("first_cut_min"))
    out["n_points"] = len(route.get("path_lonlat") or [])
    return jsonable(out)


# ---------------------------------------------------------------------------
# Registers (lookup_facility)
# ---------------------------------------------------------------------------
_NAME_KEYS = ("name", "nom", "r_tol", "denominaci_completa", "alies")
_MUNI_KEYS = ("municipality", "municipi", "poblacio", "nom_municipi")
_CAPACITY_KEYS = ("capacitat", "total_places", "alumnes", "capacity")
_STOPWORDS = {"la", "el", "els", "les", "de", "del", "dels", "d", "l", "i", "s", "n", "s/n", "a", "en", "the", "of"}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.casefold().strip())


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", _fold(s)) if t and t not in _STOPWORDS}


def _first(row: dict, keys) -> str | None:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


def _capacity(row: dict) -> int | None:
    v = _first(row, _CAPACITY_KEYS)
    if v is None:
        return None
    try:
        return int(float(str(v).replace(",", ".")))
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _load_registers() -> list[tuple[str, dict]]:
    """(register_name, raw_row) for every row in fixtures/registers.json and data/registers/*.json."""
    rows: list[tuple[str, dict]] = []
    if FIXTURE_REGISTERS.exists():
        d = json.loads(FIXTURE_REGISTERS.read_text())
        for reg, items in d.items():
            if reg.startswith("_") or not isinstance(items, list):
                continue
            rows.extend((f"fixture:{reg}", r) for r in items if isinstance(r, dict))
    if DATA_REGISTERS_DIR.is_dir():
        for p in sorted(DATA_REGISTERS_DIR.glob("*.json")):
            try:
                items = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(items, dict):
                items = items.get("rows") or items.get("data") or []
            rows.extend((f"gencat:{p.stem}", r) for r in items if isinstance(r, dict))
    return rows


def _score(query: str, name: str) -> int:
    q, n = _fold(query), _fold(name)
    if not q or not n:
        return 0
    if q == n:
        return 100
    if q in n or n in q:
        return 50
    return len(_tokens(query) & _tokens(name))


# ---------------------------------------------------------------------------
# The seven tools
# ---------------------------------------------------------------------------
def get_assets(scenario_id: str, tier: str | None = None, needs_review: bool | None = None,
               store: ScenarioStore | None = None) -> list[dict]:
    sc = (store or STORE).get(scenario_id)
    rows = []
    for a in sc.assets:
        if tier and a["tier"] != tier:
            continue
        if needs_review is True and not a.get("needs_review"):
            continue
        if needs_review is False and a.get("needs_review"):
            continue
        rows.append(trim_asset(a))
    return rows


def get_decision(scenario_id: str, asset_id: str, store: ScenarioStore | None = None) -> dict:
    sc = (store or STORE).get(scenario_id)
    a = sc.asset(asset_id)
    dec = a.get("decision")
    if not dec:
        return {"asset_id": asset_id, "decision": None, "error": "no decision attached"}
    out = trim_decision(dec)
    out["tier"] = a["tier"]
    out["occupancy"] = a.get("occupancy")
    return out


def get_route(scenario_id: str, asset_id: str, store: ScenarioStore | None = None) -> dict | None:
    sc = (store or STORE).get(scenario_id)
    route = sc.asset(asset_id).get("route")
    return trim_route(route) if route else None


def lookup_facility(query: str, limit: int = 20) -> list[dict]:
    """Candidate register rows matching `query` by name (case/accent-insensitive substring or token
    overlap). Each candidate: register, name, municipality, capacity (int|None), score, fields."""
    q = (query or "").strip()
    if not q:
        return []
    out = []
    for reg, row in _load_registers():
        name = _first(row, _NAME_KEYS)
        if not name:
            continue
        s = _score(q, str(name))
        if s <= 0:
            continue
        out.append({
            "register": reg,
            "name": str(name),
            "municipality": str(_first(row, _MUNI_KEYS) or ""),
            "capacity": _capacity(row),
            "score": s,
            "fields": jsonable(row),
        })
    out.sort(key=lambda c: (-c["score"], c["capacity"] is None, c["register"], c["name"]))
    return out[:limit]


def sample_raster(scenario_id: str, layer: str, lon: float, lat: float,
                  store: ScenarioStore | None = None) -> float | str | None:
    sc = (store or STORE).get(scenario_id)
    if layer not in ("arrival_p10", "arrival_p50", "burn_prob"):
        raise ValueError(f"unknown layer {layer!r}")
    if sc.arrival is None:
        raise ValueError("scenario has no arrival raster")
    v = float(sc.arrival.sample(layer, float(lon), float(lat)))
    if math.isnan(v):
        return None
    if math.isinf(v):
        return "inf"
    return round(v, 2) if layer == "burn_prob" else round(v)


def add_override(scenario_id: str, asset_id: str, field: str, value, source: str, quoted_snippet: str,
                 confidence: str = "low", store: ScenarioStore | None = None) -> dict:
    """Pessimistic-only override with evidence. Raises OptimisticMoveError on an optimistic move."""
    sc = (store or STORE).get(scenario_id)
    asset = sc.asset(asset_id)
    before = {"tier": asset["tier"], "decision": (asset.get("decision") or {}).get("decision"),
              "value": asset.get(field)}
    apply_override(asset, {"field": field, "value": value, "source": source,
                           "quoted_snippet": quoted_snippet, "confidence": confidence})
    record = asset["overrides"][-1]  # direction "pessimistic" or "same", set by apply_override
    if hasattr(sc, "_redecide"):
        sc._redecide(asset, f"agent override {field}={value} ({source})")
    sc.change_log.append(f"{asset_id}: agent override {field} {before['value']} -> {asset.get(field)} "
                         f"from {source} ({confidence})")
    return {
        "ok": True,
        "asset_id": asset_id,
        "field": field,
        "value": asset.get(field),
        "previous": before["value"],
        "direction": record["direction"],
        "tier": asset["tier"],
        "decision": (asset.get("decision") or {}).get("decision"),
        "tier_before": before["tier"],
        "decision_before": before["decision"],
        "needs_review": list(asset.get("needs_review", [])),
    }


def escalate(scenario_id: str, asset_id: str, question: str, options: list[str], default: str,
             field: str | None = None, default_value=None, store: ScenarioStore | None = None) -> dict:
    """One question for the coordinator with a pessimistic default applied now (scenario.add_escalation)."""
    sc = (store or STORE).get(scenario_id)
    esc = sc.add_escalation(asset_id, question, list(options), default, field=field, default_value=default_value)
    asset = sc.asset(asset_id)
    return {
        "ok": True,
        "escalation_id": esc["escalation_id"],
        "asset_id": asset_id,
        "question": question,
        "options": list(options),
        "default": default,
        "status": esc["status"],
        "field": field,
        "default_value": default_value,
        "tier": asset["tier"],
        "decision": (asset.get("decision") or {}).get("decision"),
    }


TOOL_FUNCTIONS = {
    "get_assets": get_assets,
    "get_decision": get_decision,
    "get_route": get_route,
    "lookup_facility": lookup_facility,
    "sample_raster": sample_raster,
    "add_override": add_override,
    "escalate": escalate,
}

_SID = {"type": "string", "description": "Scenario id (given in the task)."}
_AID = {"type": "string", "description": "Asset id, e.g. 'fixture:can_xic'."}

TOOLS: list[dict] = [
    {
        "name": "get_assets",
        "description": "List assets of the scenario (trimmed rows), optionally filtered by tier and by "
                       "whether they need review. Numbers here are the engine's; quote them verbatim.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario_id": _SID,
                "tier": {"type": ["string", "null"], "enum": ["act_now", "prepare", "monitor", None]},
                "needs_review": {"type": ["boolean", "null"]},
            },
            "required": ["scenario_id"],
        },
    },
    {
        "name": "get_decision",
        "description": "The confine/evacuate decision for one asset with its checks, exit window, "
                       "latest departure, reception centre, medical destination and route summary.",
        "input_schema": {
            "type": "object",
            "properties": {"scenario_id": _SID, "asset_id": _AID},
            "required": ["scenario_id", "asset_id"],
        },
    },
    {
        "name": "get_route",
        "description": "Best exit route for one asset (destination, travel minutes, first road cut and "
                       "when), or null when no route exists.",
        "input_schema": {
            "type": "object",
            "properties": {"scenario_id": _SID, "asset_id": _AID},
            "required": ["scenario_id", "asset_id"],
        },
    },
    {
        "name": "lookup_facility",
        "description": "Search the facility registers (Equipaments, care homes, campsites, schools) by "
                       "name. Returns candidate rows with register, name, municipality, capacity (or "
                       "null) and all raw fields. Check municipality before trusting a match.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Facility name or part of it."}},
            "required": ["query"],
        },
    },
    {
        "name": "sample_raster",
        "description": "Sample the fire forecast at a point: minutes to arrival (p10 or p50; 'inf' if "
                       "never) or burn probability 0..1. Null outside the modelled grid.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario_id": _SID,
                "layer": {"type": "string", "enum": ["arrival_p10", "arrival_p50", "burn_prob"]},
                "lon": {"type": "number"},
                "lat": {"type": "number"},
            },
            "required": ["scenario_id", "layer", "lon", "lat"],
        },
    },
    {
        "name": "add_override",
        "description": "Record a pessimistic-only override with evidence: occupancy may only go up, tier "
                       "only towards act_now, shelter_viable only to false. Anything optimistic is refused; "
                       "use escalate for that. quoted_snippet must quote the source verbatim.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario_id": _SID,
                "asset_id": _AID,
                "field": {"type": "string", "enum": ["occupancy", "tier", "shelter_viable"]},
                "value": {"type": ["integer", "string", "boolean"],
                          "description": "New value: integer occupancy, tier name, or false."},
                "source": {"type": "string", "description": "Where the evidence comes from (register name, page)."},
                "quoted_snippet": {"type": "string", "description": "Verbatim snippet from the source."},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["scenario_id", "asset_id", "field", "value", "source", "quoted_snippet", "confidence"],
        },
    },
    {
        "name": "escalate",
        "description": "Put one question in the coordinator queue with options and a pessimistic default "
                       "that is applied now. Optional field/default_value apply the default to the asset "
                       "(pessimistic direction only) until the coordinator answers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario_id": _SID,
                "asset_id": _AID,
                "question": {"type": "string", "description": "One specific question the coordinator can answer in one click."},
                "options": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                "default": {"type": "string", "description": "The pessimistic option, must be one of options."},
                "field": {"type": ["string", "null"], "enum": ["occupancy", "tier", "shelter_viable", None]},
                "default_value": {"type": ["integer", "string", "boolean", "null"]},
            },
            "required": ["scenario_id", "asset_id", "question", "options", "default"],
        },
    },
]


def dispatch(name: str, tool_input: dict, store: ScenarioStore | None = None):
    """Run a tool by name and return a JSON-serialisable result. Errors come back as
    {"error": ..., "hint": ...} so the model can recover; optimistic moves point at `escalate`."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}", "hint": f"available: {sorted(TOOL_FUNCTIONS)}"}
    kwargs = dict(tool_input or {})
    if name != "lookup_facility":
        kwargs["store"] = store or STORE
    try:
        return jsonable(fn(**kwargs))
    except OptimisticMoveError as e:
        return {"error": f"optimistic move refused: {e}",
                "hint": "The agent may only move pessimistically. Use `escalate` with a question, options "
                        "and the pessimistic default; a coordinator can apply the optimistic answer."}
    except KeyError as e:
        return {"error": f"not found: {e}", "hint": "check scenario_id and asset_id"}
    except (TypeError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {e}", "hint": "check the tool's input schema"}


# ---------------------------------------------------------------------------
# Number post-check
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def number_tokens(text: str) -> list[str]:
    return _NUM_RE.findall(text or "")


def postcheck_numbers(final_text: str, tool_results: list[str], asset_id: str = "") -> tuple[bool, list[str]]:
    """True when every number token in `final_text` (ignoring those in `asset_id`) appears in at least
    one tool result string. Returns (ok, offending_tokens)."""
    allowed = set()
    for r in tool_results:
        allowed.update(number_tokens(r))
    ignore = set(number_tokens(asset_id))
    joined = "\n".join(tool_results)
    bad = []
    for tok in number_tokens(final_text):
        if tok in ignore or tok in allowed:
            continue
        # tolerate a plain-integer rendering of a float in a result (e.g. "48" for "48.0") and vice versa
        core = tok.rstrip("0").rstrip(".,") if "." in tok or "," in tok else tok
        if core and (core in allowed or f"{core}.0" in allowed or core in joined):
            continue
        bad.append(tok)
    return (not bad), bad


# ---------------------------------------------------------------------------
# Triage loop
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are the edge-case triage agent of FireLine, a wildfire values-at-risk decision layer for the INFOCAT director and the municipal coordinator. A deterministic engine has already tiered every asset and decided confine/evacuate. You handle only the assets it flagged as needs_review, one at a time, with the tools provided.

Rules (binding):
1. Tool results only. Every number you state must come verbatim from a tool result in this conversation. Call get_decision before quoting anything. Never estimate, round differently, or recall a figure from memory. If you have no tool result for a number, do not state it.
2. Autonomy is asymmetric. You may move an asset only in the pessimistic direction on your own with add_override: occupancy up, tier towards act_now, shelter_viable to false. Every override carries a source and a verbatim quoted_snippet. Any optimistic move (lower occupancy, "not in session", "track is passable", "bungalows so shelter is viable") must go through escalate: one specific question the coordinator can answer in one click, with options and the pessimistic default, which is applied until answered.
3. Never issue an order. Start your final message with "Recommendation, not an order." Keep it to a few lines: what you found, what you overrode with what evidence, what you escalated with which default.
4. Stay within the step budget. Do the minimum investigation that resolves or escalates the reason codes, then stop.

Reason-code playbook:
- occupancy_unknown: lookup_facility(name). If a register row matches by name AND municipality and has a capacity, add_override occupancy = that capacity, source = the register, quoted_snippet = the row's name, municipality and capacity. If no capacity is found anywhere, escalate "occupied today?" default yes (fold into the seasonal question if occupancy_seasonal is also present). Do not invent a capacity.
- occupancy_seasonal (camp, school, second homes): escalate "in session / people on site today?" options yes/no, default yes. If a register or page states a capacity, add_override occupancy to it first (pessimistic).
- class_ambiguous (campsite tents vs bungalows): escalate with the two readings, default the pessimistic one (tents: shelter not viable; field shelter_viable, default_value false).
- no_exit: escalate "only exit is a track, passable by car?" options yes/no, default no. If the asset is a masia or campsite, add_override shelter_viable = false (source: routing) so the decision is confine and request protection.
"""


def _asset_ids_in_tier_order(scenario) -> list[str]:
    rows = [a for a in scenario.assets if a.get("needs_review")]
    rows.sort(key=lambda a: (TIER_RANK.get(a["tier"], 99), a.get("lead_adjusted_p10_min", math.inf), a["asset_id"]))
    return [a["asset_id"] for a in rows]


def _block_to_dict(block) -> dict:
    if isinstance(block, dict):
        return block
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input or {})}
    return {"type": block.type}


def _first_message(scenario, asset: dict) -> str:
    payload = {"scenario_id": scenario.id, "reason_codes": list(asset["needs_review"]), "asset": trim_asset(asset)}
    return ("Triage this needs_review asset. Reason codes and the engine's row follow as JSON. "
            "Resolve pessimistically with evidence or escalate, then give your short final message.\n"
            + json.dumps(payload, ensure_ascii=False))


def triage_asset(scenario, asset_id: str, llm, max_steps: int = 6, store: ScenarioStore | None = None) -> dict:
    """Run one investigate-resolve-or-escalate loop for a single asset."""
    store = store or STORE
    asset = scenario.asset(asset_id)
    reason_codes = list(asset["needs_review"])
    n_over0 = len(asset.get("overrides") or [])
    n_esc0 = sum(1 for e in scenario.queue if e["asset_id"] == asset_id)

    messages: list[dict] = [{"role": "user", "content": _first_message(scenario, asset)}]
    tool_result_strings: list[str] = []
    tool_calls: list[dict] = []
    final_text = ""
    steps = 0
    hit_cap = False

    while True:
        if steps >= max_steps:
            hit_cap = True
            break
        response = llm.create(system=SYSTEM_PROMPT, messages=messages, tools=TOOLS)
        steps += 1
        blocks = [_block_to_dict(b) for b in response.content]
        messages.append({"role": "assistant", "content": blocks})
        uses = [b for b in blocks if b["type"] == "tool_use"]
        texts = [b["text"] for b in blocks if b["type"] == "text"]
        if not uses:
            final_text = "\n".join(t for t in texts if t).strip()
            break
        results = []
        for u in uses:
            result = dispatch(u["name"], u["input"], store=store)
            result_str = json.dumps(result, ensure_ascii=False)
            tool_result_strings.append(result_str)
            tool_calls.append({"name": u["name"], "input": u["input"], "result": result})
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": result_str,
                            "is_error": isinstance(result, dict) and "error" in result})
        messages.append({"role": "user", "content": results})

    if hit_cap and not final_text:
        final_text = STEP_CAP_TEXT
        scenario.change_log.append(f"{asset_id}: step cap ({max_steps}) hit, loop stopped")

    ok, bad = postcheck_numbers(final_text, tool_result_strings, asset_id)
    if not ok:
        scenario.change_log.append(
            f"{asset_id}: agent message withheld, number post-check failed on {bad}: {final_text[:160]!r}")
        final_text = POSTCHECK_FAILED_TEXT
    if final_text and not final_text.lower().startswith("recommendation, not an order"):
        final_text = "Recommendation, not an order. " + final_text

    asset = scenario.asset(asset_id)  # re-fetch: overrides may have re-sorted the table
    overrides_added = (asset.get("overrides") or [])[n_over0:]
    escalations_added = [e for e in scenario.queue if e["asset_id"] == asset_id][n_esc0:]
    record = {
        "asset_id": asset_id,
        "name": asset["name"],
        "reason_codes": reason_codes,
        "steps": steps,
        "tool_calls": tool_calls,
        "final_text": final_text,
        "overrides_added": overrides_added,
        "escalations_added": escalations_added,
        "postcheck_ok": ok,
        "tier": asset["tier"],
        "decision": (asset.get("decision") or {}).get("decision"),
    }
    scenario.change_log.append(
        f"{asset_id}: triage {','.join(reason_codes)} in {steps} steps: {len(overrides_added)} override(s), "
        f"{len(escalations_added)} escalation(s), postcheck {'ok' if ok else 'FAILED'}; tier {asset['tier']}, "
        f"decision {record['decision']}")
    return record


def triage(scenario, llm=None, max_steps_per_asset: int = 6, store: ScenarioStore | None = None) -> list[dict]:
    """Edge-case triage over every needs_review asset, in tier order (act_now, prepare, monitor).
    Returns one record per asset; appends a one-line summary per asset to scenario.change_log."""
    if llm is None:
        from .llm import FakeLLM

        llm = FakeLLM()
    store = store or STORE
    store.register(scenario)
    records = []
    for asset_id in _asset_ids_in_tier_order(scenario):
        records.append(triage_asset(scenario, asset_id, llm, max_steps=max_steps_per_asset, store=store))
    return records
