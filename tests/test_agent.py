"""Agent tools + triage loop on the Gavarres fixtures with the offline FakeLLM (no network, no key)."""

import json
from pathlib import Path

import pytest

from fireline import agent, config
from fireline.agent import (POSTCHECK_FAILED_TEXT, STORE, TOOLS, ScenarioStore, dispatch, postcheck_numbers,
                            triage)
from fireline.llm import FakeLLM, FakeResponse, TextBlock, ToolUseBlock
from fireline.routing import RoadGraph
from fireline.scenario import Scenario
from tests.test_exposure import make_arrival
from tests.test_scenario import fire_state

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def build(arrival, closures=None) -> Scenario:
    assets = json.load(open(FIX / "assets.json"))
    dests = json.load(open(FIX / "destinations.json"))
    rg = RoadGraph.from_fixture(FIX / "roads.json")
    return Scenario.run(fire_state(), arrival, assets, dests, rg, config, closures=closures)


@pytest.fixture(scope="module")
def arrival():
    return make_arrival()


@pytest.fixture
def sc(arrival):
    # Camí de Can Xic closed so Can Xic is a real no_exit case.
    return build(arrival, closures=["Camí de Can Xic"])


@pytest.fixture
def triaged(sc):
    records = triage(sc, llm=FakeLLM())
    return sc, records


def rec(records, aid):
    return next(r for r in records if r["asset_id"] == aid)


# ---------------------------------------------------------------------------
# triage end-to-end
# ---------------------------------------------------------------------------
def test_triage_covers_needs_review_in_tier_order(triaged):
    sc, records = triaged
    ids = [r["asset_id"] for r in records]
    assert set(ids) == {"fixture:pou_del_glac", "fixture:can_xic", "fixture:camping_gavarres",
                        "fixture:residencia_la_bisbal", "fixture:escola_cruilles", "fixture:mas_pla"}
    # tiers recorded after triage may have moved pessimistically; the visit order was by initial tier
    assert ids[0] in ("fixture:pou_del_glac", "fixture:can_xic")
    for r in records:
        assert r["postcheck_ok"] is True
        assert r["steps"] <= 6
        assert r["final_text"].startswith("Recommendation, not an order")
        assert set(r) >= {"asset_id", "reason_codes", "steps", "final_text", "overrides_added",
                          "escalations_added", "postcheck_ok"}
    assert sum(1 for line in sc.change_log if ": triage " in line) == len(records)
    assert sc.id in STORE


def test_residencia_gets_register_occupancy(triaged):
    sc, records = triaged
    r = rec(records, "fixture:residencia_la_bisbal")
    assert r["reason_codes"] == ["occupancy_unknown"]
    a = sc.asset("fixture:residencia_la_bisbal")
    assert a["occupancy"] == 48 and a["occupancy_source"] == "override"
    assert "occupancy_unknown" not in a["needs_review"]
    assert len(r["overrides_added"]) == 1
    ov = r["overrides_added"][0]
    assert ov["field"] == "occupancy" and ov["value"] == 48 and ov["direction"] == "pessimistic"
    assert "Residència Geriàtrica de la Bisbal" in ov["quoted_snippet"]
    assert "care_homes" in ov["source"]
    assert r["escalations_added"] == []
    assert "48" in r["final_text"]


def test_pou_del_glac_escalated_default_yes(triaged):
    sc, records = triaged
    r = rec(records, "fixture:pou_del_glac")
    assert {"occupancy_unknown", "occupancy_seasonal"} <= set(r["reason_codes"])
    assert r["overrides_added"] == []          # no register gives a capacity: nothing invented
    assert len(r["escalations_added"]) == 1
    esc = r["escalations_added"][0]
    assert esc["default"] == "yes" and esc["options"] == ["yes", "no"] and esc["status"] == "open"
    assert esc in sc.queue
    assert sc.asset("fixture:pou_del_glac")["occupancy"] is None


def test_can_xic_no_exit(triaged):
    sc, records = triaged
    r = rec(records, "fixture:can_xic")
    assert r["reason_codes"] == ["no_exit"]
    escs = r["escalations_added"]
    assert len(escs) == 1 and escs[0]["default"] == "no" and "passable" in escs[0]["question"].lower()
    a = sc.asset("fixture:can_xic")
    assert a["shelter_viable"] is False
    assert any(o["field"] == "shelter_viable" and o["value"] is False for o in r["overrides_added"])
    assert a["decision"]["decision"] == "confine_request_protection"


def test_camping_class_ambiguous(triaged):
    sc, records = triaged
    r = rec(records, "fixture:camping_gavarres")
    escs = r["escalations_added"]
    assert len(escs) == 1 and escs[0]["default"] == "tents" and escs[0]["field"] == "shelter_viable"
    assert sc.asset("fixture:camping_gavarres")["shelter_viable"] is False


def test_triage_default_llm_is_fake(arrival):
    sc = build(arrival)
    records = triage(sc)
    assert records and all(r["postcheck_ok"] for r in records)


# ---------------------------------------------------------------------------
# tools and dispatch
# ---------------------------------------------------------------------------
def test_tools_schema_names():
    names = [t["name"] for t in TOOLS]
    assert names == ["get_assets", "get_decision", "get_route", "lookup_facility", "sample_raster",
                     "add_override", "escalate"]
    for t in TOOLS:
        assert t["input_schema"]["type"] == "object" and "required" in t["input_schema"]


def test_dispatch_outputs_are_json_serialisable(sc):
    store = ScenarioStore()
    store.register(sc)
    sid = sc.id
    calls = [
        ("get_assets", {"scenario_id": sid}),
        ("get_assets", {"scenario_id": sid, "tier": "act_now", "needs_review": True}),
        ("get_decision", {"scenario_id": sid, "asset_id": "fixture:vall_repos"}),
        ("get_route", {"scenario_id": sid, "asset_id": "fixture:vall_repos"}),
        ("get_route", {"scenario_id": sid, "asset_id": "fixture:can_xic"}),          # None
        ("lookup_facility", {"query": "residència"}),
        ("sample_raster", {"scenario_id": sid, "layer": "arrival_p10", "lon": 3.04, "lat": 41.94}),
        ("sample_raster", {"scenario_id": sid, "layer": "arrival_p10", "lon": 2.86, "lat": 41.81}),  # inf
        ("sample_raster", {"scenario_id": sid, "layer": "burn_prob", "lon": 0.0, "lat": 0.0}),       # outside
        ("add_override", {"scenario_id": sid, "asset_id": "fixture:sant_pol", "field": "occupancy", "value": 90,
                          "source": "test", "quoted_snippet": "90", "confidence": "low"}),
        ("escalate", {"scenario_id": sid, "asset_id": "fixture:sant_pol", "question": "q?",
                      "options": ["yes", "no"], "default": "yes"}),
        ("nope", {}),
        ("get_decision", {"scenario_id": "zzz", "asset_id": "x"}),
    ]
    for name, inp in calls:
        out = dispatch(name, inp, store=store)
        json.dumps(out)  # must not raise
    assert dispatch("get_route", {"scenario_id": sid, "asset_id": "fixture:can_xic"}, store=store) is None
    assert dispatch("sample_raster", {"scenario_id": sid, "layer": "arrival_p10", "lon": 2.86, "lat": 41.81},
                    store=store) == "inf"
    assert dispatch("sample_raster", {"scenario_id": sid, "layer": "burn_prob", "lon": 0.0, "lat": 0.0},
                    store=store) is None
    assert "error" in dispatch("nope", {}, store=store)
    assert "error" in dispatch("get_decision", {"scenario_id": "zzz", "asset_id": "x"}, store=store)
    rows = dispatch("get_assets", {"scenario_id": sid}, store=store)
    assert len(rows) == 12 and "lon" not in rows[0] and "overrides" not in rows[0]
    # inf survives as the string "inf" in trimmed rows
    monitor = dispatch("get_assets", {"scenario_id": sid, "tier": "monitor"}, store=store)
    assert any(r["arrival_p10_min"] == "inf" for r in monitor)


def test_add_override_rejects_optimistic_move(sc):
    store = ScenarioStore()
    store.register(sc)
    out = dispatch("add_override", {"scenario_id": sc.id, "asset_id": "fixture:vall_repos", "field": "occupancy",
                                    "value": 10, "source": "t", "quoted_snippet": "10", "confidence": "high"},
                   store=store)
    assert "error" in out and "escalate" in out["hint"]
    assert sc.asset("fixture:vall_repos")["occupancy"] == 70
    with pytest.raises(agent.OptimisticMoveError):
        agent.add_override(sc.id, "fixture:vall_repos", "occupancy", 10, "t", "10", "high", store=store)
    # pessimistic move goes through and re-decides
    out = dispatch("add_override", {"scenario_id": sc.id, "asset_id": "fixture:vall_repos", "field": "occupancy",
                                    "value": 75, "source": "t", "quoted_snippet": "75", "confidence": "high"},
                   store=store)
    assert out["ok"] and out["value"] == 75 and out["direction"] == "pessimistic"
    assert sc.asset("fixture:vall_repos")["overrides"][-1]["direction"] == "pessimistic"


def test_escalate_tool_records_queue_and_default(sc):
    store = ScenarioStore()
    store.register(sc)
    out = dispatch("escalate", {"scenario_id": sc.id, "asset_id": "fixture:camping_gavarres",
                                "question": "tents or bungalows?", "options": ["tents", "bungalows"],
                                "default": "tents", "field": "shelter_viable", "default_value": False}, store=store)
    assert out["ok"] and sc.queue[-1]["escalation_id"] == out["escalation_id"]
    assert sc.asset("fixture:camping_gavarres")["shelter_viable"] is False
    bad = dispatch("escalate", {"scenario_id": sc.id, "asset_id": "fixture:camping_gavarres",
                                "question": "?", "options": ["a", "b"], "default": "c"}, store=store)
    assert "error" in bad


def test_lookup_facility():
    cands = agent.lookup_facility("Residència la Bisbal")
    names = [c["name"] for c in cands]
    assert names[0] == "Residència la Bisbal"
    assert "Residència Geriàtrica de la Bisbal" in names
    geri = next(c for c in cands if c["name"] == "Residència Geriàtrica de la Bisbal")
    assert geri["capacity"] == 48 and geri["register"] == "fixture:care_homes"
    assert geri["fields"]["capacitat"] == 48
    assert agent.lookup_facility("") == []
    assert agent.lookup_facility("pou del glac")           # accent-insensitive
    top = agent.lookup_facility("Càmping Gavarres")[0]
    assert top["name"] == "Càmping Gavarres" and top["capacity"] == 200


# ---------------------------------------------------------------------------
# number post-check
# ---------------------------------------------------------------------------
def test_postcheck_numbers():
    results = ['{"occupancy": 48, "exit_window_min": 123}']
    assert postcheck_numbers("Recommendation: 48 people, window 123 min", results) == (True, [])
    ok, bad = postcheck_numbers("Recommendation: 150 children", results)
    assert not ok and bad == ["150"]
    assert postcheck_numbers("asset x:12 has 48", results, asset_id="fixture:x12")[0]
    assert postcheck_numbers("no numbers here", [])[0]


class LyingLLM:
    """Emits one tool call then a final text with a number that no tool result contains."""

    def create(self, system, messages, tools):
        payload = json.loads(messages[0]["content"][messages[0]["content"].index("{"):])
        if len(messages) == 1:
            return FakeResponse([ToolUseBlock("t1", "get_decision", {"scenario_id": payload["scenario_id"],
                                                                     "asset_id": payload["asset"]["asset_id"]})],
                                "tool_use")
        return FakeResponse([TextBlock("There are 150 children on site.")], "end_turn")


def test_postcheck_replaces_invented_number(sc):
    records = triage(sc, llm=LyingLLM())
    assert records and all(r["postcheck_ok"] is False for r in records)
    assert all(r["final_text"] == POSTCHECK_FAILED_TEXT for r in records)
    assert any("post-check failed" in line and "150" in line for line in sc.change_log)


class LoopingLLM:
    def create(self, system, messages, tools):
        payload = json.loads(messages[0]["content"][messages[0]["content"].index("{"):])
        return FakeResponse([ToolUseBlock(f"t{len(messages)}", "get_route",
                                          {"scenario_id": payload["scenario_id"],
                                           "asset_id": payload["asset"]["asset_id"]})], "tool_use")


def test_step_cap(sc):
    records = triage(sc, llm=LoopingLLM(), max_steps_per_asset=3)
    assert all(r["steps"] == 3 for r in records)
    assert all(r["final_text"] == agent.STEP_CAP_TEXT for r in records)
    assert any("step cap" in line for line in sc.change_log)
