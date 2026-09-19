"""NebiusLLM: the Anthropic <-> OpenAI translation and the back-end picked for a given key.

Offline: the HTTP session is a stub, so nothing here needs NEBIUS_API_KEY or a network. The point
is that `agent.investigate` can drive the Nebius back-end without knowing it is not Anthropic.
"""

import json

import pytest

from fireline import agent, env
from fireline.agent import TOOLS, Workbench, investigate
from fireline.llm import (DEFAULT_NEBIUS_MODEL, AnthropicLLM, FakeLLM, NebiusLLM, from_openai_message,
                          live_llm, to_openai_messages, to_openai_tools)
from tests.test_agent import POU, _asset


# ---------------------------------------------------------------------------
# stub transport
# ---------------------------------------------------------------------------
class StubResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class StubSession:
    """Returns the queued assistant messages in order and records every request body."""

    def __init__(self, messages, status_code=200):
        self._messages = list(messages)
        self.status_code = status_code
        self.requests = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "body": json, "headers": headers, "timeout": timeout})
        if self.status_code != 200:
            return StubResponse({"error": "nope"}, self.status_code)
        msg = self._messages[min(len(self.requests) - 1, len(self._messages) - 1)]
        return StubResponse({"choices": [{"message": msg, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


def _call(name, args, cid="call_1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def wb() -> Workbench:
    from tests.test_agent import CAMPING, FORECAST_ONLY, NOWHERE, PLAIN, RESI

    return Workbench.from_scored([_asset(**PLAIN), _asset(**CAMPING), _asset(**POU), _asset(**RESI),
                                  _asset(**NOWHERE), _asset(**FORECAST_ONLY)])


# ---------------------------------------------------------------------------
# request translation
# ---------------------------------------------------------------------------
def test_tools_become_openai_functions_with_the_same_schema():
    fns = to_openai_tools(TOOLS)
    assert [f["function"]["name"] for f in fns] == [t["name"] for t in TOOLS]
    assert all(f["type"] == "function" for f in fns)
    assert fns[0]["function"]["parameters"] == TOOLS[0]["input_schema"]
    assert fns[0]["function"]["description"] == TOOLS[0]["description"]


def test_messages_become_system_user_assistant_tool_calls_and_tool_results():
    messages = [
        {"role": "user", "content": "Investigate this flagged asset. {\"asset_id\": \"a1\"}"},
        {"role": "assistant", "content": [{"type": "text", "text": "looking"},
                                          {"type": "tool_use", "id": "t1", "name": "get_asset",
                                           "input": {"asset_id": "a1"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": '{"capacity": 120}'}]},
    ]
    out = to_openai_messages("SYSTEM", messages)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[0]["content"] == "SYSTEM"
    assert out[2]["content"] == "looking"
    assert out[2]["tool_calls"] == [_call("get_asset", {"asset_id": "a1"}, "t1")]
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": '{"capacity": 120}'}


def test_assistant_message_without_text_carries_no_content_but_keeps_its_calls():
    messages = [{"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "escalate",
                                                   "input": {}}]}]
    out = to_openai_messages("S", messages)
    assert out[1]["content"] is None and len(out[1]["tool_calls"]) == 1


def test_non_ascii_evidence_survives_the_round_trip():
    messages = [{"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "propose_update",
                                                   "input": {"quoted_snippet": "Capacitat 120 places"}}]}]
    args = json.loads(to_openai_messages("S", messages)[1]["tool_calls"][0]["function"]["arguments"])
    assert args["quoted_snippet"] == "Capacitat 120 places"


# ---------------------------------------------------------------------------
# response translation
# ---------------------------------------------------------------------------
def test_tool_calls_become_tool_use_blocks():
    resp = from_openai_message({"content": "thinking", "tool_calls": [_call("get_asset", {"asset_id": "a1"})]})
    assert resp.stop_reason == "tool_use"
    assert [b.type for b in resp.content] == ["text", "tool_use"]
    assert resp.content[1].id == "call_1" and resp.content[1].input == {"asset_id": "a1"}


def test_text_only_message_ends_the_turn():
    resp = from_openai_message({"content": "Recommendation, not an order. Nothing found."})
    assert resp.stop_reason == "end_turn" and resp.content[0].text.startswith("Recommendation")


def test_unparseable_arguments_become_an_empty_input_rather_than_a_guess(wb):
    bad = {"id": "c1", "type": "function", "function": {"name": "get_asset", "arguments": "{oops"}}
    resp = from_openai_message({"tool_calls": [bad]})
    assert resp.content[0].input == {}
    # the tool layer then reports a missing argument instead of acting
    assert "error" in agent.dispatch("get_asset", {}, workbench=wb)


# ---------------------------------------------------------------------------
# the back-end itself
# ---------------------------------------------------------------------------
def test_create_posts_the_model_tools_and_key_then_returns_blocks():
    session = StubSession([{"content": None, "tool_calls": [_call("get_asset", {"asset_id": "a1"})]}])
    llm = NebiusLLM(api_key="secret", session=session)
    resp = llm.create("SYSTEM", [{"role": "user", "content": "go"}], TOOLS)
    req = session.requests[0]
    assert req["url"].endswith("/chat/completions")
    assert req["headers"]["Authorization"] == "Bearer secret"
    assert req["body"]["model"] == DEFAULT_NEBIUS_MODEL and req["body"]["temperature"] == 0.0
    assert len(req["body"]["tools"]) == len(TOOLS)
    assert resp.content[0].name == "get_asset"


def test_model_comes_from_fireline_model_env(monkeypatch):
    monkeypatch.setenv("FIRELINE_MODEL", "moonshotai/Kimi-K3")
    assert NebiusLLM(api_key="k").model == "moonshotai/Kimi-K3"
    assert NebiusLLM("zai-org/GLM-5.3", api_key="k").model == "zai-org/GLM-5.3"


def test_http_error_is_raised_with_the_model_and_status():
    llm = NebiusLLM(api_key="k", session=StubSession([{}], status_code=429))
    with pytest.raises(RuntimeError, match="429"):
        llm.create("S", [{"role": "user", "content": "go"}], TOOLS)


def test_empty_choices_is_an_error_not_a_silent_end_turn():
    class NoChoices(StubSession):
        def post(self, url, json=None, headers=None, timeout=None):
            self.requests.append(json)
            return StubResponse({"choices": []})

    with pytest.raises(RuntimeError, match="no choices"):
        NebiusLLM(api_key="k", session=NoChoices([{}])).create("S", [{"role": "user", "content": "g"}], TOOLS)


def test_missing_key_is_reported_by_name(monkeypatch):
    monkeypatch.setattr(env, "_loaded", ["already-loaded"])
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        _ = NebiusLLM().api_key


# ---------------------------------------------------------------------------
# driving the real loop
# ---------------------------------------------------------------------------
def test_investigate_runs_the_loop_over_the_nebius_back_end(wb):
    """get_asset -> lookup_facility -> propose_update -> final text, all through the translation."""
    scripted = [
        {"content": None, "tool_calls": [_call("get_asset", {"asset_id": POU["asset_id"]}, "c1")]},
        {"content": None, "tool_calls": [_call("lookup_facility", {"query": "Pou del Glaç",
                                                                   "municipality": "la Bisbal d'Empordà"}, "c2")]},
        {"content": None, "tool_calls": [_call("propose_update", {
            "asset_id": POU["asset_id"], "field": "capacity", "value": 120,
            "source": "gencat", "quoted_snippet": "Capacitat 120 places", "confidence": "medium"}, "c3")]},
        {"content": "Recommendation, not an order. Proposed capacity 120, pending confirmation."},
    ]
    session = StubSession(scripted)
    record = investigate(wb, POU["asset_id"], llm=NebiusLLM(api_key="k", session=session))
    assert record["llm_mode"] == "live"
    assert [c["name"] for c in record["tool_calls"]] == ["get_asset", "lookup_facility", "propose_update"]
    assert [(p["field"], p["value"]) for p in record["proposals_added"]] == [("capacity", 120)]
    assert record["postcheck_ok"] and record["final_text"].startswith("Recommendation, not an order.")
    # the whole conversation, tool results included, is resent each step
    last = session.requests[-1]["body"]["messages"]
    assert [m["role"] for m in last][:2] == ["system", "user"]
    assert sum(1 for m in last if m["role"] == "tool") == 3


def test_a_number_the_tools_never_returned_is_still_withheld(wb):
    scripted = [
        {"content": None, "tool_calls": [_call("get_asset", {"asset_id": POU["asset_id"]}, "c1")]},
        {"content": "Recommendation, not an order. There are 150 children on site."},
    ]
    record = investigate(wb, POU["asset_id"], llm=NebiusLLM(api_key="k", session=StubSession(scripted)))
    assert record["postcheck_ok"] is False
    assert record["final_text"] == agent.POSTCHECK_FAILED_TEXT


# ---------------------------------------------------------------------------
# choosing a back-end
# ---------------------------------------------------------------------------
def test_live_llm_prefers_nebius_then_anthropic_then_nothing(monkeypatch):
    monkeypatch.setattr(env, "_loaded", ["already-loaded"])
    monkeypatch.setenv("NEBIUS_API_KEY", "n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert env.live_llm_provider() == "nebius" and isinstance(live_llm(), NebiusLLM)
    monkeypatch.delenv("NEBIUS_API_KEY")
    assert env.live_llm_provider() == "anthropic" and isinstance(live_llm(), AnthropicLLM)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert env.live_llm_provider() is None and live_llm() is None


def test_llm_mode_calls_every_provider_back_end_live():
    assert agent.llm_mode(NebiusLLM(api_key="k")) == "live"
    assert agent.llm_mode(AnthropicLLM(client=object())) == "live"
    assert agent.llm_mode(FakeLLM()) == "fake"
