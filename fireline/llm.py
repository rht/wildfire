"""LLM back-ends for the investigation agent (CONTRACTS section 6, readme 8).

Two classes with the same `.create(system, messages, tools)` interface, both returning an object
with `.content` (blocks with `.type` in {"text", "tool_use"}) and `.stop_reason`:

- `AnthropicLLM` wraps `anthropic.Anthropic().messages.create` (network, needs credentials).
- `FakeLLM` is deterministic and offline: it reads the asset's review reasons from the first user
  message and scripts the same get_asset -> lookup_facility -> propose_update / escalate steps the
  system prompt asks of the real model, quoting only numbers that appeared in tool results.

`agent.investigate` treats the two identically.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Real back-end
# ---------------------------------------------------------------------------
class AnthropicLLM:
    """Thin wrapper over the Anthropic Messages API. Model from env FIRELINE_MODEL."""

    def __init__(self, model: str | None = None, client=None, max_tokens: int = MAX_TOKENS):
        self.model = model or os.environ.get("FIRELINE_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic  # imported lazily so tests never need credentials

            self._client = anthropic.Anthropic()
        return self._client

    def create(self, system: str, messages: list[dict], tools: list[dict]):
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )


# ---------------------------------------------------------------------------
# Fake back-end (offline, deterministic)
# ---------------------------------------------------------------------------
@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"


_HEADCOUNT_KEYS = ("headcount", "estimated_occupancy", "people_present")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _loads(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


class FakeLLM:
    """Scripted stand-in for the model. One instance can serve many asset loops; the script is
    derived from the conversation alone (first user message + completed tool results).

    Script per review reason (everything is a proposal or a question, nothing is applied):
    - always: get_asset(asset_id) first.
    - occupancy_unknown: lookup_facility(name, municipality); if a candidate matches by name and
      municipality and has a capacity, propose_update(capacity) quoting its snippet (never
      estimated_occupancy from a capacity); propose estimated_occupancy only when the candidate carries
      a headcount field; then escalate "how many people are present today?" unless headcount evidence
      exists (folded into the seasonal question when occupancy_seasonal is also present).
    - occupancy_seasonal: escalate "in session today?" default yes.
    - class_ambiguous / value_unknown: propose asset_type when a matching candidate states one, else
      escalate with two readings.
    - location_unknown: lookup, then escalate "confirm address/coordinates" quoting any address found.
    - exposure_unknown: escalate.
    Final text quotes only numbers present in tool results of the loop.
    """

    def __init__(self):
        self.calls = 0
        self._counter = 0

    # -- helpers --------------------------------------------------------
    def _next_id(self) -> str:
        self._counter += 1
        return f"fake_tool_{self._counter}"

    @staticmethod
    def _first_user_payload(messages: list[dict]) -> dict:
        """The asset id, name and review reasons that investigate puts in the first user message."""
        first = messages[0]
        content = first["content"]
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        m = re.search(r"\{.*\}", content, re.S)
        return _loads(m.group(0)) or {} if m else {}

    @staticmethod
    def _tool_results(messages: list[dict]) -> list[tuple[str, dict, str]]:
        """(tool_name, tool_input, result_text) for every completed tool call in the conversation."""
        pending: dict[str, tuple[str, dict]] = {}
        out = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if msg["role"] == "assistant":
                    btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                    if btype == "tool_use":
                        bid = getattr(block, "id", None) or block.get("id")
                        name = getattr(block, "name", None) or block.get("name")
                        inp = getattr(block, "input", None) or block.get("input")
                        pending[bid] = (name, inp)
                elif msg["role"] == "user" and isinstance(block, dict) and block.get("type") == "tool_result":
                    name, inp = pending.get(block["tool_use_id"], ("?", {}))
                    res = block.get("content")
                    if isinstance(res, list):
                        res = " ".join(b.get("text", "") for b in res if isinstance(b, dict))
                    out.append((name, inp, str(res)))
        return out

    @staticmethod
    def _match(cands, name: str, muni: str) -> dict | None:
        """First candidate whose name matches (score >= 50) and whose municipality matches."""
        if not isinstance(cands, list):
            return None
        for c in cands:
            if not isinstance(c, dict) or c.get("score", 0) < 50:
                continue
            cm = _norm(c.get("municipality", ""))
            if muni and cm and cm != muni and muni not in cm and cm not in muni:
                continue
            if muni and not cm:
                continue
            return c
        return None

    @staticmethod
    def _headcount(c: dict | None) -> int | None:
        if not c:
            return None
        fields = c.get("fields") or {}
        for k in _HEADCOUNT_KEYS:
            v = fields.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
        return None

    @staticmethod
    def _address(c: dict | None) -> str | None:
        if not c:
            return None
        fields = c.get("fields") or {}
        for k in ("address", "adreca", "adre_a"):
            v = fields.get(k)
            if v:
                return str(v)
        return None

    @staticmethod
    def _source(c: dict) -> str:
        return c.get("source") or c.get("register") or c.get("evidence_id") or "cached facility page"

    def _propose(self, aid: str, c: dict, field_name: str, value, confidence: str = "medium") -> ToolUseBlock:
        return ToolUseBlock(self._next_id(), "propose_update", {
            "asset_id": aid, "field": field_name, "value": value, "source": self._source(c),
            "quoted_snippet": c.get("snippet") or c.get("name", ""), "confidence": confidence,
            "url": c.get("url"), "observed_at": c.get("observed_at")})

    def _escalate(self, aid: str, question: str, options: list[str], default: str) -> ToolUseBlock:
        return ToolUseBlock(self._next_id(), "escalate", {
            "asset_id": aid, "question": question, "options": options, "default": default})

    # -- scripted plan --------------------------------------------------
    def _plan(self, payload: dict, done: list[tuple[str, dict, str]]) -> list[ToolUseBlock] | TextBlock:
        aid = payload.get("asset_id")
        codes = list(payload.get("review_reasons", []))
        name = payload.get("name") or ""
        muni = _norm(payload.get("municipality") or "")
        atype = payload.get("asset_type") or "unknown"
        done_names = [d[0] for d in done]
        asked = [d[1].get("question", "").lower() for d in done if d[0] == "escalate"]
        proposed = [d[1].get("field") for d in done if d[0] == "propose_update"]

        def asked_about(word: str) -> bool:
            return any(word in q for q in asked)

        # Step 1: always ground the numbers with get_asset.
        if "get_asset" not in done_names:
            return [ToolUseBlock(self._next_id(), "get_asset", {"asset_id": aid})]
        asset = _loads(next(d[2] for d in done if d[0] == "get_asset")) or {}
        if isinstance(asset, dict) and not asset.get("error"):
            name = asset.get("name") or name
            muni = _norm(asset.get("municipality") or "") or muni
            atype = asset.get("asset_type") or atype

        needs_lookup = any(c in codes for c in ("occupancy_unknown", "class_ambiguous", "value_unknown",
                                                 "location_unknown"))
        if needs_lookup and "lookup_facility" not in done_names:
            inp = {"query": name}
            if muni:
                inp["municipality"] = payload.get("municipality") or asset.get("municipality")
            return [ToolUseBlock(self._next_id(), "lookup_facility", inp)]
        cands = _loads(next((d[2] for d in done if d[0] == "lookup_facility"), "null"))
        match = self._match(cands, name, muni) if needs_lookup else None
        headcount = self._headcount(match)

        # Step 2: occupancy_unknown -> propose capacity (never occupancy from capacity), then ask headcount.
        if "occupancy_unknown" in codes:
            if match and match.get("capacity") is not None and "capacity" not in proposed:
                return [self._propose(aid, match, "capacity", int(match["capacity"]))]
            if headcount is not None and "estimated_occupancy" not in proposed:
                return [self._propose(aid, match, "estimated_occupancy", int(headcount))]
            if headcount is None and not asked_about("present") and "occupancy_seasonal" not in codes:
                if match:
                    q = (f"{name}: the evidence gives a capacity but no headcount. How many people are "
                         f"present today?")
                    return [self._escalate(aid, q, ["headcount confirmed with the facility",
                                                    "unknown, treat capacity as an upper bound",
                                                    "nobody on site today"],
                                           "unknown, treat capacity as an upper bound")]
                q = f"{name}: no evidence found for this facility. Is it occupied today, and how many people are present?"
                return [self._escalate(aid, q, ["occupied, headcount to confirm", "not occupied today"],
                                       "occupied, headcount to confirm")]

        # Step 3: occupancy_seasonal -> escalate in-session question (carries the headcount question).
        if "occupancy_seasonal" in codes and not asked_about("session"):
            q = f"{name}: in session today (people on site)? If yes, how many are present?"
            return [self._escalate(aid, q, ["yes", "no"], "yes")]

        # Step 4: class_ambiguous / value_unknown -> propose asset_type from evidence or escalate readings.
        if "class_ambiguous" in codes or "value_unknown" in codes:
            stated = (match or {}).get("asset_type")
            if stated and "asset_type" not in proposed:
                return [self._propose(aid, match, "asset_type", stated)]
            if not stated and not asked_about("class"):
                other = "camp" if atype == "campsite" else ("campsite" if atype == "camp" else "unknown")
                q = f"{name}: which class applies? The register does not say (current reading: {atype})."
                return [self._escalate(aid, q, [atype, other], atype)]

        # Step 5: location_unknown -> escalate address confirmation, quoting any evidence address.
        if "location_unknown" in codes and not asked_about("address"):
            addr = self._address(match)
            if addr:
                q = f"{name}: confirm address / coordinates. Evidence address: {addr} ({match.get('municipality')})."
            else:
                q = f"{name}: no address found in the evidence. Confirm address / coordinates with the municipality."
            return [self._escalate(aid, q, ["address confirmed, geocode it", "wrong facility", "still unknown"],
                                   "still unknown")]

        # Step 6: exposure_unknown -> escalate (nothing the agent can supply).
        if "exposure_unknown" in codes and "location_unknown" not in codes and not asked_about("exposure"):
            q = f"{name}: exposure unknown (no fire geometry or location). Keep in the review queue?"
            return [self._escalate(aid, q, ["keep in review", "resolved manually"], "keep in review")]

        # Done: final text quoting only numbers from tool results.
        return TextBlock(self._final_text(name, done))

    @staticmethod
    def _final_text(name: str, done: list[tuple[str, dict, str]]) -> str:
        parts = []
        for tool, inp, res in done:
            r = _loads(res)
            if tool == "get_asset" and isinstance(r, dict) and not r.get("error"):
                bits = []
                if isinstance(r.get("distance_to_fire_m"), (int, float)):
                    bits.append(f"{r['distance_to_fire_m']} m from the fire")
                if isinstance(r.get("priority_score"), (int, float)):
                    bits.append(f"priority {r['priority_score']}")
                if r.get("queue"):
                    bits.append(f"queue {r['queue']}")
                if bits:
                    parts.append(", ".join(bits))
            elif tool == "propose_update":
                if isinstance(r, dict) and r.get("proposal_id"):
                    parts.append(f"proposed {r['field']}={r['value']} from {inp.get('source')} "
                                 f"(pending analyst confirmation)")
                else:
                    parts.append(f"proposal of {inp.get('field')} refused")
            elif tool == "escalate":
                parts.append(f"escalated '{inp.get('question')}' default '{inp.get('default')}'")
        return f"Recommendation, not an order. {name}: " + "; ".join(parts) + "."

    # -- interface ------------------------------------------------------
    def create(self, system: str, messages: list[dict], tools: list[dict]) -> FakeResponse:
        self.calls += 1
        payload = self._first_user_payload(messages)
        done = self._tool_results(messages)
        plan = self._plan(payload, done)
        if isinstance(plan, TextBlock):
            return FakeResponse(content=[plan], stop_reason="end_turn")
        return FakeResponse(content=list(plan), stop_reason="tool_use")
