"""Agent layer: four tools + one bounded investigation loop (CONTRACTS section 6, readme 8).

Deterministic ranking by remaining evacuation window for the common case, agent for the flagged
cases, analyst decides. The agent only ever acts through the four tools below and never changes an
asset itself:

- `get_asset`        read the ranked record (numbers rounded so they can be quoted verbatim)
- `lookup_facility`  search the cached evidence (facility pages, register rows) by name
- `propose_update`   record a sourced field update as a *pending* proposal
- `escalate`         record one concrete question for the analyst

Every proposal needs analyst confirmation (`confirm_proposal`), which persists it through
`tasks.TaskStore.confirm_override` when a store is attached, updates the in-memory asset and
re-ranks the workbench's snapshot with `priority.rank_snapshot` (readme 8 step 4). Every number in the agent's final message must come from a tool result of the
same loop (post-check); otherwise the message is replaced and the failure logged. The loop is capped
at `max_steps` model calls. Works offline with `llm.FakeLLM` (default when `llm=None`).
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from . import config, priority

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REGISTERS = ROOT / "fixtures" / "registers.json"
FIXTURE_EVIDENCE = ROOT / "fixtures" / "evidence.json"
DATA_REGISTERS_DIR = ROOT / "data" / "registers"

PROPOSAL_FIELDS = ("estimated_occupancy", "capacity", "asset_type", "evacuation_min")
OCCUPANCY_FIELDS = ("estimated_occupancy", "capacity")
CONFIDENCE_LEVELS = ("low", "medium", "high")
REVIEW_REASONS = ("location_unknown", "occupancy_unknown", "occupancy_seasonal", "class_ambiguous",
                  "value_unknown", "exposure_unknown", "forecast_unavailable", "evacuation_unknown")

POSTCHECK_FAILED_TEXT = ("Recommendation, not an order. Agent message withheld: number post-check failed "
                         "(the draft quoted a number that is not in any tool result). See the proposals "
                         "and questions recorded for this asset.")
STEP_CAP_TEXT = ("Recommendation, not an order. Agent stopped at the step cap for this asset; the proposals "
                 "and questions recorded so far await the analyst, nothing was applied.")

# Words that mean "capacity" (a ceiling, not a headcount) and words that mean an actual headcount.
_CAPACITY_WORDS = ("capacity", "capacitat", "capacidad", "places", "plazas", "total_places", "beds", "llits")
_HEADCOUNT_WORDS = ("headcount", "head count", "present", "presents", "on site", "occupied", "occupancy",
                    "ocupació", "ocupacio", "ocupats", "ocupades", "ocupación", "persones avui", "people today",
                    "today", "avui", "hoy", "residents actuals", "actual", "counted", "recompte")


class CapacityAsOccupancyError(ValueError):
    """Raised when capacity evidence is proposed as an actual-occupancy claim (readme 11)."""


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Workbench: the tools' only state
# ---------------------------------------------------------------------------
@dataclass
class Workbench:
    """Ranked assets plus the agent's pending proposals and open questions.

    `assets` maps asset_id -> ranked asset record (CONTRACTS 2.2 + 4). `tasks` is a
    `tasks.TaskStore` or None (then confirmations are kept in `overrides` in memory). `snapshot` is
    the snapshot the assets came from; when set, a confirmation re-ranks it with
    `priority.rank_snapshot`. `change_log` is a list of one-line strings."""

    assets: dict = field(default_factory=dict)
    tasks: object | None = None
    proposals: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    change_log: list = field(default_factory=list)
    snapshot: dict | None = None
    overrides: list = field(default_factory=list)      # in-memory confirmations when no store is attached

    @classmethod
    def from_scored(cls, scored_assets, tasks=None, snapshot=None) -> "Workbench":
        """Build from a list of ranked assets or the dict `priority.rank_snapshot` returns."""
        if isinstance(scored_assets, dict) and "all" in scored_assets:
            scored_assets = scored_assets["all"]
        assets = {a["asset_id"]: a for a in scored_assets}
        return cls(assets=assets, tasks=tasks, snapshot=snapshot)

    def confirmed_overrides(self) -> list[dict]:
        """The store's confirmed overrides when attached, else the in-memory ones."""
        if self.tasks is not None and hasattr(self.tasks, "overrides"):
            return list(self.tasks.overrides())
        return list(self.overrides)

    def asset(self, asset_id: str) -> dict:
        try:
            return self.assets[asset_id]
        except KeyError:
            raise KeyError(f"unknown asset_id {asset_id!r}") from None

    def proposal(self, proposal_id: str) -> dict:
        for p in self.proposals:
            if p["proposal_id"] == proposal_id:
                return p
        raise KeyError(f"unknown proposal_id {proposal_id!r}")

    def question(self, question_id: str) -> dict:
        for q in self.questions:
            if q["question_id"] == question_id:
                return q
        raise KeyError(f"unknown question_id {question_id!r}")


# ---------------------------------------------------------------------------
# Evidence cache and registers (lookup_facility)
# ---------------------------------------------------------------------------
_NAME_KEYS = ("name", "nom", "r_tol", "denominaci_completa", "alies")
_MUNI_KEYS = ("municipality", "municipi", "poblacio", "nom_municipi")
_CAPACITY_KEYS = ("capacity", "capacitat", "total_places", "alumnes")
_ADDRESS_KEYS = ("address", "adreca", "adre_a")
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


def _as_int(v) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(float(str(v).replace(",", ".")))
    except ValueError:
        return None


def _capacity(row: dict) -> int | None:
    return _as_int(_first(row, _CAPACITY_KEYS))


def _file_time(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return None


@lru_cache(maxsize=1)
def _load_registers() -> list[dict]:
    """One candidate skeleton per row in fixtures/registers.json and data/registers/*.json."""
    out: list[dict] = []

    def add(register: str, items, fetched_at):
        for i, row in enumerate(items):
            if not isinstance(row, dict):
                continue
            name = _first(row, _NAME_KEYS)
            if not name:
                continue
            muni = str(_first(row, _MUNI_KEYS) or "")
            cap = _capacity(row)
            cap_text = f"capacity {cap}" if cap is not None else "capacity unknown"
            out.append({
                "evidence_id": f"reg:{register}:{i}",
                "name": str(name),
                "municipality": muni,
                "register": register,
                "url": None,
                "source": register,
                "capacity": cap,
                "asset_type": None,
                "snippet": f"{name}, {muni}, {cap_text}",
                "observed_at": None,
                "fetched_at": fetched_at,
                "fields": jsonable(row),
            })

    if FIXTURE_REGISTERS.exists():
        d = json.loads(FIXTURE_REGISTERS.read_text(encoding="utf-8"))
        for reg, items in d.items():
            if reg.startswith("_") or not isinstance(items, list):
                continue
            add(f"fixture:{reg}", items, None)
    if DATA_REGISTERS_DIR.is_dir():
        for p in sorted(DATA_REGISTERS_DIR.glob("*.json")):
            try:
                items = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(items, dict):
                items = items.get("rows") or items.get("data") or []
            add(f"gencat:{p.stem}", items, _file_time(p))
    return out


@lru_cache(maxsize=1)
def _load_evidence() -> list[dict]:
    """Candidates from fixtures/evidence.json (cached facility pages / register rows, readme 4)."""
    if not FIXTURE_EVIDENCE.exists():
        return []
    try:
        entries = json.loads(FIXTURE_EVIDENCE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(entries, dict):
        entries = entries.get("entries") or []
    out = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not e.get("name"):
            continue
        source = e.get("source") or ""
        out.append({
            "evidence_id": e.get("evidence_id") or f"ev:{i}",
            "name": str(e["name"]),
            "municipality": str(e.get("municipality") or ""),
            "register": e.get("register") or (source if source and not e.get("url") else None),
            "url": e.get("url"),
            "source": source or None,
            "capacity": _as_int(e.get("capacity")),
            "asset_type": e.get("asset_type"),
            "snippet": e.get("snippet") or "",
            "observed_at": e.get("observed_at"),
            "fetched_at": e.get("fetched_at"),
            "fields": jsonable(e),
        })
    return out


def _score(query: str, name: str) -> int:
    q, n = _fold(query), _fold(name)
    if not q or not n:
        return 0
    if q == n:
        return 100
    if q in n or n in q:
        return 50
    return len(_tokens(query) & _tokens(name))


def _muni_matches(wanted: str, actual: str) -> bool:
    w, a = _fold(wanted), _fold(actual)
    if not w:
        return True
    if not a:
        return False
    return w == a or w in a or a in w


# ---------------------------------------------------------------------------
# The four tools
# ---------------------------------------------------------------------------
_ASSET_FIELDS = ("asset_id", "name", "asset_type", "municipality", "latitude", "longitude", "capacity",
                 "estimated_occupancy", "occupancy_basis", "value_score", "intersects_fire", "queue",
                 "review_reasons", "fire_arrival_at", "fire_arrival_basis", "forecast_source",
                 "evacuation_min", "evacuation_source")


def get_asset(asset_id: str, workbench: Workbench) -> dict:
    """Trimmed ranked record for one asset (numbers rounded): timing fields, remaining window
    (`slack_min`), status, rank and queue, plus open tasks, confirmed overrides and the count of
    proposals still pending."""
    a = workbench.asset(asset_id)
    out = {k: a.get(k) for k in _ASSET_FIELDS}
    out["review_reasons"] = list(a.get("review_reasons") or [])
    out["distance_to_fire_m"] = _round(a.get("distance_to_fire_m"))
    out["evacuation_min"] = _round(a.get("evacuation_min"))
    out["slack_min"] = _round(a.get("slack_min"))
    out["priority_status"] = a.get("priority_status")
    out["priority_rank"] = a.get("priority_rank")
    out["open_tasks"] = []
    out["confirmed_overrides"] = []
    tasks = workbench.tasks
    if tasks is not None:
        try:
            for t in tasks.tasks(asset_id=asset_id):
                if t.get("status") != "done":
                    out["open_tasks"].append({"task_id": t.get("task_id"), "action": t.get("action"),
                                              "status": t.get("status")})
        except AttributeError:
            pass
        try:
            for o in tasks.overrides(asset_id=asset_id):
                out["confirmed_overrides"].append({"field": o.get("field"), "value": o.get("value"),
                                                   "source": o.get("source")})
        except AttributeError:
            pass
    out["pending_proposals"] = sum(1 for p in workbench.proposals
                                   if p["asset_id"] == asset_id and p["status"] == "pending")
    return jsonable(out)


def lookup_facility(query: str, municipality: str | None = None, limit: int = 20) -> list[dict]:
    """Candidates matching `query` by name (case/accent-insensitive substring or token overlap) from
    fixtures/evidence.json, fixtures/registers.json and data/registers/*.json. Optional municipality
    filter. Each candidate: evidence_id, name, municipality, register|url, capacity, asset_type,
    snippet, observed_at, fetched_at, source, score, fields."""
    q = (query or "").strip()
    if not q:
        return []
    out = []
    for cand in _load_evidence() + _load_registers():
        s = _score(q, cand["name"])
        if s <= 0:
            continue
        if municipality and not _muni_matches(municipality, cand["municipality"]):
            continue
        c = dict(cand)
        c["score"] = s
        out.append(c)
    out.sort(key=lambda c: (-c["score"], c["capacity"] is None, c["url"] is None, c["evidence_id"]))
    return out[:limit]


def _mentions(text: str, words) -> bool:
    t = _fold(text)
    return any(_fold(w) in t for w in words)


def _check_value(field_name: str, value):
    """Validate and normalise a proposed value. Raises ValueError."""
    if field_name not in PROPOSAL_FIELDS:
        raise ValueError(f"field must be one of {list(PROPOSAL_FIELDS)}, got {field_name!r}")
    if field_name in OCCUPANCY_FIELDS:
        if isinstance(value, bool) or value is None:
            raise ValueError(f"{field_name} must be an integer >= 0, got {value!r}")
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(f"{field_name} must be an integer >= 0, got {value!r}")
            value = int(value)
        if isinstance(value, str):
            v = _as_int(value)
            if v is None or str(v) != value.strip():
                raise ValueError(f"{field_name} must be an integer >= 0, got {value!r}")
            value = v
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{field_name} must be an integer >= 0, got {value!r}")
        return value
    if field_name == "evacuation_min":
        if isinstance(value, bool) or value is None:
            raise ValueError(f"evacuation_min must be a number of minutes >= 0, got {value!r}")
        if isinstance(value, str):
            try:
                value = float(value.strip().replace(",", "."))
            except ValueError:
                raise ValueError(f"evacuation_min must be a number of minutes >= 0, got {value!r}") from None
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"evacuation_min must be a number of minutes >= 0, got {value!r}")
        return float(value)
    allowed = list(config.VALUE_POLICY["by_type"]) + ["unknown"]
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"asset_type must be one of {allowed}, got {value!r}")
    return value


def propose_update(asset_id: str, field: str, value, source: str, quoted_snippet: str, confidence: str,
                   url: str | None = None, observed_at: str | None = None,
                   workbench: Workbench | None = None) -> dict:
    """Record a sourced field update as a pending proposal. Nothing is applied to the asset."""
    if workbench is None:
        raise TypeError("propose_update needs a workbench")
    asset = workbench.asset(asset_id)
    value = _check_value(field, value)
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {list(CONFIDENCE_LEVELS)}, got {confidence!r}")
    if not (quoted_snippet or "").strip():
        raise ValueError("quoted_snippet must quote the evidence verbatim (it is empty)")
    if not (source or "").strip():
        raise ValueError("source must name the register or page the snippet comes from")
    if field == "estimated_occupancy":
        text = f"{quoted_snippet} {source}"
        if _mentions(text, _CAPACITY_WORDS) and not _mentions(text, _HEADCOUNT_WORDS):
            raise CapacityAsOccupancyError(
                "the evidence states a capacity (places), not a headcount; capacity must not become a "
                "claim about actual occupancy. Propose field='capacity' with this snippet instead, and "
                "escalate the question of how many people are present today.")
    proposal = {
        "proposal_id": f"prop-{len(workbench.proposals) + 1:03d}-{asset_id.split(':')[-1]}",
        "asset_id": asset_id,
        "field": field,
        "value": value,
        "previous": asset.get(field),
        "source": source,
        "quoted_snippet": quoted_snippet,
        "url": url,
        "observed_at": observed_at,
        "confidence": confidence,
        "status": "pending",
        "created_at": _now(),
    }
    workbench.proposals.append(proposal)
    workbench.change_log.append(f"{asset_id}: proposal {proposal['proposal_id']} {field} "
                                f"{proposal['previous']!r} -> {value!r} from {source} ({confidence}), pending")
    return dict(proposal)


def escalate(asset_id: str, question: str, options: list[str], default: str,
             workbench: Workbench | None = None) -> dict:
    """Record one concrete question for the analyst with options and a default. Nothing is applied."""
    if workbench is None:
        raise TypeError("escalate needs a workbench")
    workbench.asset(asset_id)
    options = [str(o) for o in (options or [])]
    if len(options) < 2:
        raise ValueError("options must list at least two answers")
    if default not in options:
        raise ValueError(f"default {default!r} must be one of options {options}")
    if not (question or "").strip():
        raise ValueError("question is empty")
    record = {
        "question_id": f"q-{len(workbench.questions) + 1:03d}-{asset_id.split(':')[-1]}",
        "asset_id": asset_id,
        "question": question,
        "options": options,
        "default": default,
        "status": "open",
        "answer": None,
        "created_at": _now(),
    }
    workbench.questions.append(record)
    workbench.change_log.append(f"{asset_id}: question {record['question_id']} open: {question!r} "
                                f"options {options} default {default!r}")
    return dict(record)


TOOL_FUNCTIONS = {
    "get_asset": get_asset,
    "lookup_facility": lookup_facility,
    "propose_update": propose_update,
    "escalate": escalate,
}

_AID = {"type": "string", "description": "Asset id, e.g. 'fixture:pou_del_glac'."}

TOOLS: list[dict] = [
    {
        "name": "get_asset",
        "description": "The ranked record of one asset: type, municipality, capacity, estimated occupancy "
                       "and its basis, distance to fire, forecast fire arrival and its basis, evacuation "
                       "duration and its source, remaining evacuation window (slack_min), status, rank and "
                       "queue, review reasons, open tasks, confirmed overrides and pending proposals. Numbers "
                       "here are the engine's; quote them verbatim.",
        "input_schema": {
            "type": "object",
            "properties": {"asset_id": _AID},
            "required": ["asset_id"],
        },
    },
    {
        "name": "lookup_facility",
        "description": "Search the cached evidence (facility pages with URL, snippet and dates; Gencat "
                       "register rows) by facility name, optionally restricted to a municipality. Returns "
                       "candidates with evidence_id, name, municipality, register or url, capacity, "
                       "asset_type when stated, snippet, observed_at, fetched_at and score. Check the "
                       "municipality before trusting a match; a capacity is a ceiling, not a headcount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Facility name or part of it."},
                "municipality": {"type": ["string", "null"],
                                 "description": "Restrict candidates to this municipality."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "propose_update",
        "description": "Propose a sourced update of one field for the analyst to confirm. Nothing is "
                       "applied until confirmed. Fields: capacity (an integer from evidence stating "
                       "places/capacity), estimated_occupancy (an integer ONLY from evidence stating an "
                       "actual headcount today), asset_type (a class the evidence states), evacuation_min "
                       "(total evacuation duration in minutes ONLY from evidence stating how long a full "
                       "evacuation of this facility takes, e.g. its evacuation plan or the facility itself). "
                       "quoted_snippet must quote the evidence verbatim; pass its url and observed_at when known.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": _AID,
                "field": {"type": "string", "enum": list(PROPOSAL_FIELDS)},
                "value": {"type": ["integer", "number", "string"],
                          "description": "Integer >= 0 for capacity/estimated_occupancy; class name for asset_type; "
                                         "minutes >= 0 for evacuation_min."},
                "source": {"type": "string", "description": "Register name or page the snippet comes from."},
                "quoted_snippet": {"type": "string", "description": "Verbatim snippet from the evidence."},
                "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
                "url": {"type": ["string", "null"]},
                "observed_at": {"type": ["string", "null"], "description": "When the evidence was observed (ISO date)."},
            },
            "required": ["asset_id", "field", "value", "source", "quoted_snippet", "confidence"],
        },
    },
    {
        "name": "escalate",
        "description": "Put one concrete question for the analyst on record, with options and a default. "
                       "Nothing is applied; the question stays open until the analyst answers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": _AID,
                "question": {"type": "string", "description": "One specific question an analyst can answer in one click."},
                "options": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                "default": {"type": "string", "description": "The cautious option; must be one of options."},
            },
            "required": ["asset_id", "question", "options", "default"],
        },
    },
]


def dispatch(name: str, tool_input: dict, workbench: Workbench | None = None):
    """Run a tool by name and return a JSON-serialisable result. Errors come back as
    {"error": ..., "hint": ...} so the model can recover."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}", "hint": f"available: {sorted(TOOL_FUNCTIONS)}"}
    kwargs = dict(tool_input or {})
    if name != "lookup_facility":
        kwargs["workbench"] = workbench
    try:
        return jsonable(fn(**kwargs))
    except CapacityAsOccupancyError as e:
        return {"error": f"refused: {e}",
                "hint": "Call propose_update again with field='capacity' and the same snippet, then "
                        "escalate 'how many people are present today?' if no headcount evidence exists."}
    except KeyError as e:
        return {"error": f"not found: {e}", "hint": "check the asset_id given in the task"}
    except (TypeError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {e}", "hint": "check the tool's input schema"}


# ---------------------------------------------------------------------------
# Analyst side: confirm / reject / answer
# ---------------------------------------------------------------------------
def rerank(workbench: Workbench) -> dict | None:
    """Re-rank the workbench's snapshot with the confirmed overrides (`priority.rank_snapshot`) and
    replace its asset records; None (nothing done) when the workbench has no snapshot."""
    if workbench.snapshot is None:
        return None
    scored = priority.rank_snapshot(workbench.snapshot, overrides=workbench.confirmed_overrides())
    workbench.assets = {a["asset_id"]: a for a in scored["all"]}
    return scored


def _refresh_in_memory(asset: dict, override: dict) -> None:
    """Without a snapshot to re-rank: apply the override to this record and recompute its window keys
    (rank number unchanged; the queue order is only rebuilt by rerank / rank_snapshot)."""
    updated = priority.apply_overrides([asset], [override])[0]
    now_at = (asset.get("window_components") or {}).get("now_at")
    if now_at:
        ranked = priority.rank_asset(updated, now_at)
        ranked["priority_rank"] = asset.get("priority_rank") if ranked["queue"] == "ranked" else None
        updated = ranked
    asset.clear()
    asset.update(updated)


def confirm_proposal(workbench: Workbench, proposal_id: str, rescore=None) -> dict:
    """Analyst confirmation: persist through `workbench.tasks.confirm_override` when a store is attached
    (else keep the override in `workbench.overrides`), mark the proposal confirmed, log it and
    recalculate the window: the asset record is refreshed in place, then `rescore(workbench)` when
    given, else `rerank(workbench)` when the workbench holds its snapshot."""
    p = workbench.proposal(proposal_id)
    if p["status"] != "pending":
        raise ValueError(f"proposal {proposal_id} is {p['status']}, not pending")
    asset = workbench.asset(p["asset_id"])
    previous = asset.get(p["field"])
    override = None
    if workbench.tasks is not None:
        override = workbench.tasks.confirm_override(
            p["asset_id"], p["field"], p["value"], source=p["source"], snippet=p["quoted_snippet"],
            url=p.get("url"), observed_at=p.get("observed_at"), confidence=p["confidence"],
            proposal_id=proposal_id)
    record = dict(override) if isinstance(override, dict) else {}
    record.setdefault("override_id", None)
    record.update({"asset_id": p["asset_id"], "field": p["field"], "value": p["value"], "source": p["source"],
                   "snippet": p["quoted_snippet"], "url": p.get("url"), "observed_at": p.get("observed_at"),
                   "confidence": p["confidence"], "proposal_id": proposal_id})
    record.setdefault("previous", previous)
    record.setdefault("confirmed_at", _now())
    if workbench.tasks is None:
        workbench.overrides.append(record)
    p["status"] = "confirmed"
    p["confirmed_at"] = record["confirmed_at"]
    if override is not None:
        p["override_id"] = record.get("override_id")
    workbench.change_log.append(f"{p['asset_id']}: proposal {proposal_id} confirmed: {p['field']} "
                                f"{previous!r} -> {p['value']!r} from {p['source']}"
                                f"{' (persisted)' if override is not None else ' (in-memory only)'}")
    _refresh_in_memory(asset, record)          # the record is current even before / without a re-rank
    if rescore is not None:
        rescore(workbench)
    else:
        rerank(workbench)
    if p["field"] == "evacuation_min" or (asset.get("slack_min") is not None):
        a = workbench.asset(p["asset_id"])
        workbench.change_log.append(f"{p['asset_id']}: window recalculated: status {a.get('priority_status')}, "
                                    f"remaining window {a.get('slack_min')} min, queue {a.get('queue')}")
    return dict(p)


def reject_proposal(workbench: Workbench, proposal_id: str, note: str = "") -> dict:
    p = workbench.proposal(proposal_id)
    if p["status"] != "pending":
        raise ValueError(f"proposal {proposal_id} is {p['status']}, not pending")
    p["status"] = "rejected"
    p["rejected_at"] = _now()
    p["note"] = note
    workbench.change_log.append(f"{p['asset_id']}: proposal {proposal_id} rejected"
                                f"{': ' + note if note else ''}")
    return dict(p)


def answer_question(workbench: Workbench, question_id: str, answer: str) -> dict:
    q = workbench.question(question_id)
    q["answer"] = answer
    q["status"] = "answered"
    q["answered_at"] = _now()
    workbench.change_log.append(f"{q['asset_id']}: question {question_id} answered {answer!r}")
    return dict(q)


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
# Investigation loop
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are the investigation agent of FireLine, a wildfire values-at-risk coordination layer for the analyst on duty. Code has already ranked every asset by its remaining evacuation window (forecast fire arrival minus the total evacuation duration and a buffer, relative to the snapshot time); assets without a forecast or an evacuation estimate sit in the review queue. You handle one flagged asset at a time with four tools: get_asset, lookup_facility, propose_update, escalate.

Rules (binding):
1. Tool results only. Every number you state must come verbatim from a tool result in this conversation. Call get_asset first. Never estimate, round differently, or recall a figure from memory. If you have no tool result for a number, do not state it.
2. You change nothing. Every field update is a proposal (propose_update) that the analyst confirms or rejects; every open point is a question (escalate) with options and a cautious default. You never assign teams, never change the ranking policy, never supply a forecast arrival and never claim an update is applied.
3. Capacity is not occupancy. A register or page stating places, capacity, capacitat or total_places is evidence for the field `capacity`. It is never evidence for `estimated_occupancy`, which needs a source stating how many people are actually present (a headcount). If you only have a capacity, propose capacity and escalate the headcount question.
4. Match before you trust. Use lookup_facility with the asset's name and municipality; accept a candidate only when name and municipality both match. Quote the evidence verbatim in quoted_snippet and pass its url and observed_at when the candidate has them.
5. Never issue an order. Start your final message with "Recommendation, not an order." Keep it to a few lines: what you found, what you proposed with what evidence (pending confirmation), what you escalated with which default.
6. Stay within the step budget. Do the minimum that resolves or escalates each review reason, then stop. Leave what you cannot support unresolved rather than guessing.

Review-reason playbook:
- occupancy_unknown: lookup_facility(name, municipality). If a candidate matches by name and municipality and states a capacity, propose_update field=capacity with the snippet (not estimated_occupancy). Then escalate "how many people are present today?" unless the evidence states a headcount, in which case propose estimated_occupancy from that headcount. If nothing matches, escalate whether the site is occupied today; do not invent a capacity.
- occupancy_seasonal (camp, school, second homes): escalate "in session / people on site today?" options yes/no, default yes. Fold the headcount question into it when both reasons are present.
- class_ambiguous: if the evidence states the class (e.g. the page says the places are bungalows, or the register lists the type), propose_update field=asset_type with the snippet; otherwise escalate with the two readings as options and the more cautious one as default.
- location_unknown: lookup_facility; escalate "confirm address / coordinates" quoting the address found in the evidence if any. Do not propose coordinates.
- value_unknown: propose asset_type when the evidence resolves the class; otherwise escalate which class applies.
- exposure_unknown: escalate; there is no fire geometry or location to compute exposure from and you cannot supply one.
- evacuation_unknown: the total evacuation duration (mobilisation, preparation/loading, movement to a receiving location) is unknown, so the asset cannot be ranked. Propose_update field=evacuation_min ONLY when the evidence states how long a full evacuation of this facility takes (an evacuation plan, the facility itself); never derive it from headcount, distance or class. Otherwise escalate "confirm the total evacuation duration with the facility" with options such as "confirmed with the facility" / "use the class default, labelled as an assumption", default "confirmed with the facility".
- forecast_unavailable: no spread forecast covers this location; the window cannot be computed. You cannot supply a forecast arrival (do not propose one). Escalate whether the analyst wants the location kept in the review queue pending a forecast, default "keep in review".
"""


def _block_to_dict(block) -> dict:
    if isinstance(block, dict):
        return block
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input or {})}
    return {"type": block.type}


def _first_message(asset: dict) -> str:
    payload = {"asset_id": asset["asset_id"], "name": asset.get("name"), "municipality": asset.get("municipality"),
               "asset_type": asset.get("asset_type"), "review_reasons": list(asset.get("review_reasons") or [])}
    return ("Investigate this flagged asset. Its id, name and review reasons follow as JSON. Call get_asset "
            "first, look up evidence, then propose sourced updates or escalate questions, and give your short "
            "final message.\n" + json.dumps(payload, ensure_ascii=False))


def llm_mode(llm) -> str:
    """'fake' for FakeLLM (or None), 'live' for AnthropicLLM, else 'custom'."""
    from .llm import AnthropicLLM, FakeLLM

    if llm is None or isinstance(llm, FakeLLM):
        return "fake"
    if isinstance(llm, AnthropicLLM):
        return "live"
    return "custom"


def investigate(workbench: Workbench, asset_id: str, llm=None, max_steps: int = 6) -> dict:
    """One bounded investigate-propose-or-escalate loop for a single asset. Returns the record
    described in CONTRACTS 6: tool_calls, final_text, proposals_added, questions_added, postcheck_ok,
    llm_mode (+ asset_id, name, review_reasons, steps)."""
    if llm is None:
        from .llm import FakeLLM

        llm = FakeLLM()
    mode = llm_mode(llm)
    asset = workbench.asset(asset_id)
    review_reasons = list(asset.get("review_reasons") or [])
    n_prop0 = len(workbench.proposals)
    n_q0 = len(workbench.questions)

    messages: list[dict] = [{"role": "user", "content": _first_message(asset)}]
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
            result = dispatch(u["name"], u["input"], workbench=workbench)
            result_str = json.dumps(result, ensure_ascii=False)
            tool_result_strings.append(result_str)
            tool_calls.append({"name": u["name"], "input": u["input"], "result": result})
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": result_str,
                            "is_error": isinstance(result, dict) and "error" in result})
        messages.append({"role": "user", "content": results})

    if hit_cap and not final_text:
        final_text = STEP_CAP_TEXT
        workbench.change_log.append(f"{asset_id}: step cap ({max_steps}) hit, loop stopped")

    ok, bad = postcheck_numbers(final_text, tool_result_strings, asset_id)
    if not ok:
        workbench.change_log.append(
            f"{asset_id}: agent message withheld, number post-check failed on {bad}: {final_text[:160]!r}")
        final_text = POSTCHECK_FAILED_TEXT
    if final_text and not final_text.lower().startswith("recommendation, not an order"):
        final_text = "Recommendation, not an order. " + final_text

    proposals_added = [dict(p) for p in workbench.proposals[n_prop0:] if p["asset_id"] == asset_id]
    questions_added = [dict(q) for q in workbench.questions[n_q0:] if q["asset_id"] == asset_id]
    record = {
        "asset_id": asset_id,
        "name": asset.get("name"),
        "review_reasons": review_reasons,
        "steps": steps,
        "tool_calls": tool_calls,
        "final_text": final_text,
        "proposals_added": proposals_added,
        "questions_added": questions_added,
        "postcheck_ok": ok,
        "llm_mode": mode,
    }
    workbench.change_log.append(
        f"{asset_id}: investigation ({mode}) {','.join(review_reasons)} in {steps} steps: "
        f"{len(proposals_added)} proposal(s) pending, {len(questions_added)} question(s) open, "
        f"postcheck {'ok' if ok else 'FAILED'}")
    return record


PRODUCER_ONLY_REASONS = ("forecast_unavailable",)   # nothing the agent can look up or propose


def flagged_asset_ids(workbench: Workbench) -> list[str]:
    """Assets with review reasons the agent can act on, in queue order: needs_review queue first (in
    the order given, which is the priority module's), then flagged ranked assets by priority rank,
    then remaining window. Assets whose only reason is a producer gap (`forecast_unavailable`) are
    left to the review queue."""
    flagged = [a for a in workbench.assets.values()
               if any(r not in PRODUCER_ONLY_REASONS for r in a.get("review_reasons") or [])]
    review = [a for a in flagged if a.get("queue") == "needs_review" or a.get("slack_min") is None]
    ranked = [a for a in flagged if a not in review]
    ranked.sort(key=lambda a: (a.get("priority_rank") if a.get("priority_rank") is not None else math.inf,
                               a.get("slack_min") if a.get("slack_min") is not None else math.inf, a["asset_id"]))
    return [a["asset_id"] for a in review + ranked]


review_order = flagged_asset_ids


def investigate_all(workbench: Workbench, llm=None, max_steps: int = 6) -> list[dict]:
    """Investigate every asset with review reasons, needs_review queue first, then flagged ranked
    assets by priority. One record per asset."""
    if llm is None:
        from .llm import FakeLLM

        llm = FakeLLM()
    return [investigate(workbench, aid, llm=llm, max_steps=max_steps) for aid in flagged_asset_ids(workbench)]
