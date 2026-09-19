"""LLM back-ends for the triage agent (PLAN 6.6).

Two classes with the same `.create(system, messages, tools)` interface, both returning an object
with `.content` (blocks with `.type` in {"text", "tool_use"}) and `.stop_reason`:

- `AnthropicLLM` wraps `anthropic.Anthropic().messages.create` (network, needs credentials).
- `FakeLLM` is deterministic and offline: it reads the asset's reason codes from the first user
  message and scripts the same investigate-resolve-or-escalate steps the prompt asks of the real
  model, quoting only numbers that appeared in tool results.

`agent.triage` treats the two identically.
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


_CAPACITY_KEYS = ("capacitat", "total_places", "alumnes")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


class FakeLLM:
    """Scripted stand-in for the model. One instance can serve many asset loops; state is keyed by
    the asset_id found in the first user message of each conversation.

    Script per reason code (pessimistic-only autonomy, PLAN 6.6):
    - occupancy_unknown: lookup_facility(name); if a candidate with a capacity field matches the
      asset's municipality, add_override(occupancy=capacity) citing the register; else escalate
      (folded into the seasonal question when occupancy_seasonal is also present).
    - occupancy_seasonal: escalate "in session today?" default yes.
    - class_ambiguous: escalate tents/bungalows, default tents, shelter_viable False.
    - no_exit: escalate "track passable by car?" default no, and add_override shelter_viable False
      for masia/campsite.
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
        """The asset row + reason codes that triage puts in the first user message (JSON block)."""
        first = messages[0]
        content = first["content"]
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        m = re.search(r"\{.*\}", content, re.S)
        return json.loads(m.group(0)) if m else {}

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

    # -- scripted plan --------------------------------------------------
    def _plan(self, payload: dict, done: list[tuple[str, dict, str]]) -> list[ToolUseBlock] | TextBlock:
        asset = payload.get("asset", {})
        codes = list(payload.get("reason_codes", []))
        sid = payload.get("scenario_id")
        aid = asset.get("asset_id")
        name = asset.get("name", "")
        cls = asset.get("asset_class", "")
        muni = _norm(asset.get("municipality", ""))
        done_names = [d[0] for d in done]

        # Step 1: always ground the numbers with get_decision.
        if "get_decision" not in done_names:
            return [ToolUseBlock(self._next_id(), "get_decision", {"scenario_id": sid, "asset_id": aid})]

        # Step 2: occupancy_unknown -> lookup, then override or escalate.
        if "occupancy_unknown" in codes:
            if "lookup_facility" not in done_names:
                return [ToolUseBlock(self._next_id(), "lookup_facility", {"query": name})]
            if not any(d[0] == "add_override" and d[1].get("field") == "occupancy" for d in done) and not any(
                d[0] == "escalate" and "occupied today" in d[1].get("question", "").lower() for d in done
            ):
                lookup = next(d for d in done if d[0] == "lookup_facility")
                try:
                    cands = json.loads(lookup[2])
                except json.JSONDecodeError:
                    cands = []
                if isinstance(cands, dict):
                    cands = cands.get("candidates", [])
                match = None
                for c in cands:
                    if c.get("capacity") is None:
                        continue
                    if muni and _norm(c.get("municipality", "")) != muni:
                        continue
                    match = c
                    break
                if match is not None:
                    snippet = (f"{match['register']}: '{match['name']}' ({match['municipality']}) "
                               f"capacity {match['capacity']}; joined to asset '{name}' by name and municipality")
                    return [ToolUseBlock(self._next_id(), "add_override", {
                        "scenario_id": sid, "asset_id": aid, "field": "occupancy",
                        "value": int(match["capacity"]), "source": f"register {match['register']}",
                        "quoted_snippet": snippet, "confidence": "medium"})]
                if "occupancy_seasonal" not in codes:
                    return [ToolUseBlock(self._next_id(), "escalate", {
                        "scenario_id": sid, "asset_id": aid,
                        "question": f"No register gives a capacity for {name}. Is it occupied today?",
                        "options": ["yes", "no"], "default": "yes"})]
                # seasonal present: the seasonal escalation below carries the question.

        # Step 3: occupancy_seasonal -> escalate, default yes.
        if "occupancy_seasonal" in codes and not any(
            d[0] == "escalate" and "session" in d[1].get("question", "").lower() for d in done
        ):
            return [ToolUseBlock(self._next_id(), "escalate", {
                "scenario_id": sid, "asset_id": aid,
                "question": f"{name}: in session today (people on site)?",
                "options": ["yes", "no"], "default": "yes"})]

        # Step 4: class_ambiguous -> escalate tents/bungalows, pessimistic default.
        if "class_ambiguous" in codes and not any(
            d[0] == "escalate" and "bungalow" in d[1].get("question", "").lower() for d in done
        ):
            return [ToolUseBlock(self._next_id(), "escalate", {
                "scenario_id": sid, "asset_id": aid,
                "question": f"{name}: places are tents or bungalows? (tents: shelter not viable)",
                "options": ["tents", "bungalows"], "default": "tents",
                "field": "shelter_viable", "default_value": False})]

        # Step 5: no_exit -> escalate passability, default no; shelter_viable False for masia/campsite.
        if "no_exit" in codes:
            if not any(d[0] == "escalate" and "passable" in d[1].get("question", "").lower() for d in done):
                return [ToolUseBlock(self._next_id(), "escalate", {
                    "scenario_id": sid, "asset_id": aid,
                    "question": f"{name}: only exit is a track. Passable by car?",
                    "options": ["yes", "no"], "default": "no"})]
            if cls in ("masia", "campsite") and not any(
                d[0] == "add_override" and d[1].get("field") == "shelter_viable" for d in done
            ):
                return [ToolUseBlock(self._next_id(), "add_override", {
                    "scenario_id": sid, "asset_id": aid, "field": "shelter_viable", "value": False,
                    "source": "routing", "quoted_snippet": "no exit route found; pessimistic default",
                    "confidence": "medium"})]

        # Done: final text quoting only numbers from tool results.
        return TextBlock(self._final_text(name, done))

    @staticmethod
    def _final_text(name: str, done: list[tuple[str, dict, str]]) -> str:
        parts = [f"Recommendation, not an order. {name}:"]
        for tool, inp, res in done:
            if tool == "get_decision":
                try:
                    d = json.loads(res)
                except json.JSONDecodeError:
                    d = {}
                if isinstance(d, dict) and d.get("decision"):
                    parts.append(f"decision {d['decision']}")
                    if isinstance(d.get("exit_window_min"), (int, float)):
                        parts.append(f"exit window {d['exit_window_min']} min")
            elif tool == "add_override":
                try:
                    r = json.loads(res)
                except json.JSONDecodeError:
                    r = {}
                if isinstance(r, dict) and r.get("ok"):
                    parts.append(f"override {r['field']}={r['value']} recorded from {inp.get('source')}")
                else:
                    parts.append("override refused")
            elif tool == "escalate":
                parts.append(f"escalated '{inp.get('question')}' default '{inp.get('default')}'")
        return "; ".join(parts) + "."

    # -- interface ------------------------------------------------------
    def create(self, system: str, messages: list[dict], tools: list[dict]) -> FakeResponse:
        self.calls += 1
        payload = self._first_user_payload(messages)
        done = self._tool_results(messages)
        plan = self._plan(payload, done)
        if isinstance(plan, TextBlock):
            return FakeResponse(content=[plan], stop_reason="end_turn")
        return FakeResponse(content=list(plan), stop_reason="tool_use")
