"""LLM back-ends for the investigation agent (CONTRACTS section 6, readme 8).

Three classes with the same `.create(system, messages, tools)` interface, all returning an object
with `.content` (blocks with `.type` in {"text", "tool_use"}) and `.stop_reason`:

- `NebiusLLM` calls an open-weight model on Nebius AI Studio over its OpenAI-compatible
  `/chat/completions` endpoint (network, needs `NEBIUS_API_KEY`). It translates the Anthropic-shaped
  tools and message blocks `agent` speaks into OpenAI `tools` / `tool_calls` and back, so the loop
  in `agent.investigate` does not know which provider answered.
- `AnthropicLLM` wraps `anthropic.Anthropic().messages.create` (network, needs `ANTHROPIC_API_KEY`).
- `FakeLLM` is deterministic and offline: it reads the asset's review reasons from the first user
  message and scripts the same get_asset -> lookup_facility -> propose_update / escalate steps the
  system prompt asks of the real model, quoting only numbers that appeared in tool results. It is
  what the tests and `scripts/validate.py` run, so every check stays offline and reproducible.

`live_llm()` returns the live back-end for whichever key is present, or None. `agent.investigate`
treats all three identically.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import requests

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_NEBIUS_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash"
NEBIUS_BASE_URL = "https://api.studio.nebius.com/v1"
# A reasoning model spends this budget on its own thinking before it writes anything, so the cap has
# to clear the thinking, not the answer. Measured over 26 criticality investigations on
# DeepSeek-V4.1-Flash (2026-09-20, the 13 assessed assets of gavarres_real_0002 twice): ordinary
# steps use 9-20 reasoning tokens, the judgement cases 1600-2400, and the largest single turn was
# 2899 completion tokens. At 2048 and again at 4096 the longest-thinking assets ran out mid-thought
# and returned no text and no tool call, which the loop could only read as "the agent had nothing to
# say". 8192 is ~3x the measured maximum; an overflow is now an error, see `finish_reason` below.
MAX_TOKENS = 8192
TIMEOUT_S = 180


# ---------------------------------------------------------------------------
# Response blocks, shared by every back-end
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
    """The shape `agent.investigate` reads: `.content` blocks and `.stop_reason`. Named for the
    offline back-end it was written for; `NebiusLLM` returns one too."""

    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"


Response = FakeResponse   # provider-neutral name for the same shape


# ---------------------------------------------------------------------------
# Nebius back-end (open-weight models over the OpenAI-compatible API)
# ---------------------------------------------------------------------------
def to_openai_tools(tools: list[dict]) -> list[dict]:
    """Anthropic tool definitions -> OpenAI function definitions (same JSON schema inside)."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in tools]


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """The conversation `agent.investigate` builds -> OpenAI chat messages.

    Anthropic keeps tool calls and their results as content blocks inside assistant/user messages;
    OpenAI puts the calls on the assistant message as `tool_calls` and each result in its own
    message with role "tool". Nothing else about the conversation changes.
    """
    out: list[dict] = [{"role": "system", "content": system}]
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            out.append({"role": msg["role"], "content": content})
            continue
        if msg["role"] == "assistant":
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {},
                                                                              ensure_ascii=False)}}
                     for b in content if b.get("type") == "tool_use"]
            out.append({"role": "assistant", "content": text or None, **({"tool_calls": calls} if calls else {})})
            continue
        for b in content:
            if b.get("type") == "tool_result":
                res = b.get("content")
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                            "content": res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)})
            elif b.get("type") == "text":
                out.append({"role": "user", "content": b["text"]})
    return out


def from_openai_message(message: dict, finish_reason: str | None = None) -> FakeResponse:
    """One OpenAI assistant message -> the block response `agent.investigate` reads. A tool call
    whose arguments are not valid JSON becomes an empty input, which the tool layer rejects as a
    missing required argument rather than acting on a guess.

    `finish_reason == "length"` is kept as `stop_reason="max_tokens"`: a truncated turn must stay
    distinguishable from a finished one, whether or not it carried any content."""
    blocks: list = []
    if message.get("content"):
        blocks.append(TextBlock(text=message["content"]))
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        blocks.append(ToolUseBlock(id=call.get("id") or "", name=fn.get("name") or "",
                                   input=args if isinstance(args, dict) else {}))
    if finish_reason == "length":
        stop = "max_tokens"
    else:
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return FakeResponse(content=blocks, stop_reason=stop)


class NebiusLLM:
    """An open-weight model on Nebius AI Studio, over its OpenAI-compatible chat endpoint.

    Model from env `FIRELINE_MODEL`, default `DEFAULT_NEBIUS_MODEL`; key from `NEBIUS_API_KEY`.
    `temperature=0` because the analyst reads these proposals: the same asset and the same evidence
    should give the same investigation as far as the provider allows.
    """

    def __init__(self, model: str | None = None, *, api_key: str | None = None,
                 base_url: str = NEBIUS_BASE_URL, max_tokens: int = MAX_TOKENS,
                 temperature: float = 0.0, session=None):
        self.model = model or os.environ.get("FIRELINE_MODEL") or DEFAULT_NEBIUS_MODEL
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._api_key = api_key
        self._session = session

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            from . import env as _env

            _env.load_env()
            key = os.environ.get("NEBIUS_API_KEY")
            if not key:
                raise RuntimeError("NEBIUS_API_KEY is not set (looked in the environment and .env)")
            self._api_key = key
        return self._api_key

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> FakeResponse:
        body = {"model": self.model, "messages": to_openai_messages(system, messages),
                "temperature": self.temperature, "max_tokens": self.max_tokens}
        if tools:
            body["tools"] = to_openai_tools(tools)
        response = self.session.post(f"{self.base_url}/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {self.api_key}"},
                                     timeout=TIMEOUT_S)
        if response.status_code != 200:
            raise RuntimeError(f"Nebius {self.model} returned {response.status_code}: {response.text[:400]}")
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError(f"Nebius {self.model} returned no choices: {str(payload)[:400]}")
        choice = choices[0]
        response = from_openai_message(choice.get("message") or {}, choice.get("finish_reason"))
        if response.stop_reason == "max_tokens" and not response.content:
            # All of max_tokens went on reasoning, so the turn carries neither text nor a tool call.
            # Raising keeps a truncation from reaching the analyst as a silent "nothing to report".
            usage = payload.get("usage") or {}
            raise RuntimeError(
                f"Nebius {self.model} hit max_tokens ({self.max_tokens}) before writing any text or tool "
                f"call; usage {usage.get('completion_tokens')} completion tokens, "
                f"{usage.get('reasoning_tokens')} of them reasoning. Raise fireline.llm.MAX_TOKENS.")
        return response


# ---------------------------------------------------------------------------
# Anthropic back-end
# ---------------------------------------------------------------------------
class AnthropicLLM:
    """Thin wrapper over the Anthropic Messages API. Model from env FIRELINE_MODEL."""

    def __init__(self, model: str | None = None, client=None, max_tokens: int = MAX_TOKENS):
        self.model = model or os.environ.get("FIRELINE_MODEL", DEFAULT_ANTHROPIC_MODEL)
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
_HEADCOUNT_KEYS = ("headcount", "estimated_occupancy", "people_present")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _loads(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


# Words a notability record may contain and the criticality factor each one evidences. A scripted
# stand-in for the judgement the real model makes; deliberately narrow, so the offline path proposes
# a low tier rather than a flattering one.
_CRIT_FACTOR_WORDS = (
    ("supercomput", "national_research_infrastructure"),
    ("centre de recerca", "national_research_infrastructure"),
    ("centro de investigacion", "national_research_infrastructure"),
    ("research cent", "national_research_infrastructure"),
    ("research institute", "national_research_infrastructure"),
    ("institut de recerca", "national_research_infrastructure"),
    ("csic", "national_research_infrastructure"),
    ("bomber", "emergency_response_capability"),
    ("fire service", "emergency_response_capability"),
    ("fire brigade", "emergency_response_capability"),
    ("oncolog", "sole_regional_service"),
    ("hospital universitari", "sole_regional_service"),
    ("col·leccio", "irreplaceable_holdings"),
    ("colleccio", "irreplaceable_holdings"),
    ("biobanc", "irreplaceable_holdings"),
    ("herbari", "irreplaceable_holdings"),
    ("arxiu", "irreplaceable_holdings"),
)


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

    def _propose_criticality(self, aid: str, rec: dict | None, atype: str) -> ToolUseBlock:
        """A tier from the notability record, or `routine` when there is none.

        Deterministic and evidence-bound like the rest of FakeLLM: the factors come from words that
        are present in the record, and the snippet is a prefix of the record's own summary, so the
        number post-check and the live `_supported` check both pass. It never proposes
        `exceptional` - claiming national infrastructure is not something a scripted stand-in
        should do.
        """
        if rec is None:
            return ToolUseBlock(self._next_id(), "propose_update", {
                "asset_id": aid, "field": "criticality_tier",
                "value": {"tier": "routine", "factors": []},
                "source": f"no notability record; class {atype}",
                "quoted_snippet": f"asset_type {atype}", "confidence": "medium",
                "url": None, "observed_at": None})
        hay = _norm(" ".join([rec.get("summary") or "", rec.get("title") or "",
                              " ".join(rec.get("instance_of") or [])]))
        factors = []
        for word, factor in _CRIT_FACTOR_WORDS:
            if _norm(word) in hay and factor not in factors:
                factors.append(factor)
        tier = "routine" if not factors else ("elevated" if len(factors) == 1 else "high")
        summary = rec.get("summary") or ""
        cut = summary.find(". ")
        snippet = summary[:cut + 1] if cut > 0 else summary[:240]
        return ToolUseBlock(self._next_id(), "propose_update", {
            "asset_id": aid, "field": "criticality_tier",
            "value": {"tier": tier, "factors": factors},
            "source": f"{rec.get('title')} ({rec.get('lang')}.wikipedia.org)",
            "quoted_snippet": snippet, "confidence": "medium",
            "url": rec.get("url"), "observed_at": rec.get("fetched_at")})

    def _propose_valuation(self, aid: str, refs: list, atype: str) -> ToolUseBlock:
        """A bespoke figure replayed from a committed worked example, or `not_valued`.

        Deterministic and evidence-bound like `_propose_criticality`: it never composes a figure of its
        own. A reference that carries `components` with a low/high band IS a worked example, and the
        proposal restates exactly its numbers, quoting its statement verbatim, so both the number
        post-check and the live `_supported` check pass. With no such reference the answer is
        `not_valued`, which is the ordinary answer and records that the corpus was searched.
        """
        worked = next((r for r in refs if isinstance(r, dict) and r.get("components")
                       and r.get("amount_eur_low") is not None and r.get("amount_eur_high") is not None), None)
        if worked is None:
            snippet = next((r.get("statement") for r in refs
                            if isinstance(r, dict) and r.get("statement")), f"asset_type {atype}")
            cut = snippet.find(". ")
            return ToolUseBlock(self._next_id(), "propose_update", {
                "asset_id": aid, "field": "custom_valuation",
                "value": {"method": "not_valued", "components": [], "amount_eur_low": None,
                          "amount_eur_mid": None, "amount_eur_high": None,
                          "note": f"no worked cost reference for class {atype}"},
                "source": f"valuation reference corpus; class {atype}",
                "quoted_snippet": snippet[:cut + 1] if cut > 0 else snippet[:240],
                "confidence": "medium", "url": None, "observed_at": None})
        statement = worked.get("statement") or ""
        cut = statement.find(". ")
        return ToolUseBlock(self._next_id(), "propose_update", {
            "asset_id": aid, "field": "custom_valuation",
            "value": {"method": "component_replacement",
                      "components": [dict(c) for c in worked["components"]],
                      "amount_eur_low": worked["amount_eur_low"],
                      "amount_eur_mid": worked["amount_eur"],
                      "amount_eur_high": worked["amount_eur_high"],
                      "note": f"replayed from reference {worked.get('reference_id')} "
                              f"(basis {worked.get('basis')})"},
            "source": f"{worked.get('title')} [{worked.get('reference_id')}]",
            "quoted_snippet": statement[:cut + 1] if cut > 0 else statement[:240],
            "confidence": "low", "url": worked.get("url"), "observed_at": worked.get("observed_at")})

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

        # Step 6a: criticality_unassessed -> look the institution up, then propose the lowest tier the
        # evidence supports. No record is the ordinary answer and means routine.
        if "criticality_unassessed" in codes and "criticality_tier" not in proposed:
            if "lookup_notability" not in done_names:
                return [ToolUseBlock(self._next_id(), "lookup_notability", {"query": name})]
            notes = _loads(next((d[2] for d in done if d[0] == "lookup_notability"), "null")) or []
            rec = notes[0] if isinstance(notes, list) and notes else None
            return [self._propose_criticality(aid, rec, atype)]

        # Step 6b: valuation_unassessed -> search the cost corpus for this class, then restate the
        # worked example it holds. No worked reference is the ordinary answer and means not_valued.
        if "valuation_unassessed" in codes and "custom_valuation" not in proposed:
            if "lookup_valuation_reference" not in done_names:
                return [ToolUseBlock(self._next_id(), "lookup_valuation_reference",
                                     {"asset_type": atype, "query": name})]
            refs = _loads(next((d[2] for d in done if d[0] == "lookup_valuation_reference"), "null")) or []
            return [self._propose_valuation(aid, refs if isinstance(refs, list) else [], atype)]

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


# ---------------------------------------------------------------------------
# Choosing a back-end
# ---------------------------------------------------------------------------
def live_llm():
    """The live back-end for whichever key is configured, or None when there is none.

    Nebius first: it is the provider the project is set up for (readme 8). Callers fall back to
    `FakeLLM` when this returns None, so the UI, the CLI and the checks all work offline.
    """
    from . import env as _env

    provider = _env.live_llm_provider()
    if provider == "nebius":
        return NebiusLLM()
    if provider == "anthropic":
        return AnthropicLLM()
    return None
