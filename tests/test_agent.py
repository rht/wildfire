"""Four agent tools, proposals pending confirmation and the investigate loop with the offline FakeLLM
(CONTRACTS section 6). No network, no key."""

import json

import pytest

from fireline import agent, config
from fireline.agent import (POSTCHECK_FAILED_TEXT, STEP_CAP_TEXT, TOOLS, Workbench, confirm_proposal, dispatch,
                            investigate, investigate_all, postcheck_numbers, reject_proposal)
from fireline.llm import AnthropicLLM, FakeLLM, FakeResponse, TextBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# helpers (local so this file does not depend on tests/helpers.py)
# ---------------------------------------------------------------------------
def _asset(**overrides) -> dict:
    asset_type = overrides.get("asset_type", "school")
    review_reasons = list(overrides.get("review_reasons", []))
    record = {
        "asset_id": "fixture:test_asset", "name": "Test asset", "asset_type": asset_type,
        "latitude": 41.95, "longitude": 3.05, "geometry": None, "area_m2": None,
        "capacity": 200, "estimated_occupancy": None, "occupancy_basis": "register capacity",
        "value_score": config.VALUE_POLICY["by_type"].get(asset_type), "value_basis": config.VALUE_POLICY["version"],
        "distance_to_fire_m": 2000.4, "intersects_fire": False, "burn_probability": None,
        "arrival_p10_at": None, "arrival_p50_at": None, "forecast_horizon_at": "2026-07-03T20:00:00+00:00",
        "forecast_source": "fixture:test-spread (synthetic)",
        "fire_arrival_at": "2026-07-03T11:00:00+00:00", "fire_arrival_basis": "p10",
        "evacuation_min": 90.0, "evacuation_source": config.EVACUATION_POLICY["version"],
        "needs_review": bool(review_reasons), "review_reasons": review_reasons, "sources": [],
        "municipality": None,
        # ranked keys (CONTRACTS 4): arrival +180 min, evacuation 90, buffer 30 -> window 60 at as_of 08:00
        "priority_rank": 1, "queue": "ranked", "priority_status": "window_open", "time_to_impact_min": 180.0,
        "latest_start_min": 60.0, "slack_min": 60.0,
        "window_components": {"fire_arrival_at": "2026-07-03T11:00:00+00:00", "fire_arrival_basis": "p10",
                              "forecast_source": "fixture:test-spread (synthetic)",
                              "forecast_horizon_at": "2026-07-03T20:00:00+00:00", "now_at": "2026-07-03T08:00:00+00:00",
                              "evacuation_min": 90.0, "evacuation_source": config.EVACUATION_POLICY["version"],
                              "buffer_min": 30.0, "distance_to_fire_m": 2000.4},
        "priority_policy_version": config.CONTACT_POLICY["version"], "priority_reasons": [],
    }
    record.update(overrides)
    if "needs_review" not in overrides:
        record["needs_review"] = bool(record["review_reasons"])
    return record


UNRANKED = dict(fire_arrival_at=None, fire_arrival_basis=None, forecast_source=None, forecast_horizon_at=None,
                slack_min=None, latest_start_min=None, time_to_impact_min=None, priority_status="needs_review",
                priority_rank=None, queue="needs_review")
POU = dict(asset_id="fixture:pou_del_glac", name="Pou del Glaç", asset_type="camp", municipality="la Bisbal d'Empordà",
           capacity=None, occupancy_basis=None,
           review_reasons=["occupancy_unknown", "occupancy_seasonal", "forecast_unavailable"], **UNRANKED)
CAMPING = dict(asset_id="fixture:camping_gavarres", name="Càmping Gavarres", asset_type="campsite",
               municipality="Calonge i Sant Antoni", capacity=200, review_reasons=["class_ambiguous"],
               slack_min=105.5, latest_start_min=105.5, priority_rank=2, queue="ranked")
RESI = dict(asset_id="fixture:residencia_sense_coordenades", name="Residència sense coordenades",
            asset_type="care_home", municipality="Cruïlles, Monells i Sant Sadurní de l'Heura", latitude=None,
            longitude=None, capacity=None, distance_to_fire_m=None, intersects_fire=None,
            review_reasons=["location_unknown", "exposure_unknown", "occupancy_unknown", "forecast_unavailable"],
            **UNRANKED)
NOWHERE = dict(asset_id="fixture:mas_nou", name="Mas Nou de Ningú", asset_type="masia", municipality="Cruïlles",
               capacity=None, review_reasons=["occupancy_unknown", "forecast_unavailable"], **UNRANKED)
PLAIN = dict(asset_id="fixture:sant_pol", name="Sant Pol", asset_type="nucleus", capacity=85, review_reasons=[],
             slack_min=60.0, priority_rank=1)
FORECAST_ONLY = dict(asset_id="fixture:no_forecast", name="No forecast", asset_type="school", capacity=50,
                     review_reasons=["forecast_unavailable"], **UNRANKED)


@pytest.fixture
def wb() -> Workbench:
    return Workbench.from_scored([_asset(**PLAIN), _asset(**CAMPING), _asset(**POU), _asset(**RESI), _asset(**NOWHERE),
                                 _asset(**FORECAST_ONLY)])


class StubTasks:
    """Tiny stand-in for tasks.TaskStore: records confirm_override calls."""

    def __init__(self):
        self.calls = []
        self._tasks = [{"task_id": "t1", "asset_id": "fixture:pou_del_glac", "action": "confirm_occupancy",
                        "status": "open"},
                       {"task_id": "t2", "asset_id": "fixture:pou_del_glac", "action": "contact_facility",
                        "status": "done"}]

    def confirm_override(self, asset_id, field, value, *, source, snippet, url=None, observed_at=None,
                         confidence, proposal_id=None):
        rec = {"override_id": f"ov-{len(self.calls) + 1}", "asset_id": asset_id, "field": field, "value": value,
               "source": source, "snippet": snippet, "url": url, "observed_at": observed_at,
               "confidence": confidence, "proposal_id": proposal_id}
        self.calls.append(rec)
        return rec

    def overrides(self, asset_id=None):
        return [c for c in self.calls if asset_id is None or c["asset_id"] == asset_id]

    def tasks(self, asset_id=None, status=None):
        return [t for t in self._tasks if (asset_id is None or t["asset_id"] == asset_id)
                and (status is None or t["status"] == status)]


# ---------------------------------------------------------------------------
# tool schemas and dispatch
# ---------------------------------------------------------------------------
def test_tools_schema_names():
    assert [t["name"] for t in TOOLS] == ["get_asset", "lookup_facility", "lookup_notability",
                                          "lookup_valuation_reference", "propose_update", "escalate"]
    for t in TOOLS:
        assert t["input_schema"]["type"] == "object" and "required" in t["input_schema"]
    assert set(agent.TOOL_FUNCTIONS) == {t["name"] for t in TOOLS}


def test_dispatch_get_asset(wb):
    out = dispatch("get_asset", {"asset_id": "fixture:camping_gavarres"}, workbench=wb)
    json.dumps(out)
    assert out["asset_id"] == "fixture:camping_gavarres" and out["asset_type"] == "campsite"
    assert out["distance_to_fire_m"] == 2000 and out["slack_min"] == 106 and out["priority_rank"] == 2
    assert out["priority_status"] == "window_open" and out["queue"] == "ranked"
    assert out["fire_arrival_at"] == "2026-07-03T11:00:00+00:00" and out["fire_arrival_basis"] == "p10"
    assert out["evacuation_min"] == 90 and out["evacuation_source"] == config.EVACUATION_POLICY["version"]
    assert out["forecast_source"] and out["review_reasons"] == ["class_ambiguous"]
    assert out["open_tasks"] == [] and out["confirmed_overrides"] == [] and out["pending_proposals"] == 0
    assert "geometry" not in out and "window_components" not in out and "priority_score" not in out
    unranked = dispatch("get_asset", {"asset_id": "fixture:pou_del_glac"}, workbench=wb)
    assert unranked["slack_min"] is None and unranked["priority_status"] == "needs_review" and unranked["fire_arrival_at"] is None
    assert "error" in dispatch("get_asset", {"asset_id": "fixture:nope"}, workbench=wb)
    assert "error" in dispatch("nope", {}, workbench=wb)


def test_get_asset_shows_tasks_and_overrides(wb):
    wb.tasks = StubTasks()
    wb.tasks.confirm_override("fixture:pou_del_glac", "capacity", 120, source="page", snippet="s", confidence="high")
    dispatch("propose_update", {"asset_id": "fixture:pou_del_glac", "field": "capacity", "value": 130,
                                "source": "x", "quoted_snippet": "capacitat 130", "confidence": "low"}, workbench=wb)
    out = dispatch("get_asset", {"asset_id": "fixture:pou_del_glac"}, workbench=wb)
    assert out["open_tasks"] == [{"task_id": "t1", "action": "confirm_occupancy", "status": "open"}]  # done one hidden
    assert out["confirmed_overrides"] == [{"field": "capacity", "value": 120, "source": "page"}]
    assert out["pending_proposals"] == 1


def test_dispatch_lookup_facility(wb):
    out = dispatch("lookup_facility", {"query": "Pou del Glaç"}, workbench=wb)
    json.dumps(out)
    assert out and out[0]["name"] == "Pou del Glaç"
    assert {"evidence_id", "name", "municipality", "register", "url", "capacity", "snippet", "observed_at",
            "fetched_at", "score", "fields"} <= set(out[0])


def test_dispatch_propose_update_is_pending_and_does_not_mutate(wb):
    before = dict(wb.asset("fixture:pou_del_glac"))
    out = dispatch("propose_update", {"asset_id": "fixture:pou_del_glac", "field": "capacity", "value": 120,
                                      "source": "manual enrichment", "quoted_snippet": "Capacitat 120 places",
                                      "confidence": "medium", "url": "https://example.invalid/p",
                                      "observed_at": "2026-06-15"}, workbench=wb)
    json.dumps(out)
    assert out["proposal_id"] == "prop-001-pou_del_glac" and out["status"] == "pending"
    assert out["field"] == "capacity" and out["value"] == 120 and out["previous"] is None
    assert out["url"] == "https://example.invalid/p" and out["observed_at"] == "2026-06-15"
    assert out["created_at"]
    assert wb.asset("fixture:pou_del_glac") == before          # nothing applied
    assert wb.proposals[0]["status"] == "pending" and len(wb.proposals) == 1
    assert any("pending" in line for line in wb.change_log)


def test_propose_update_refuses_capacity_as_occupancy(wb):
    out = dispatch("propose_update", {"asset_id": "fixture:pou_del_glac", "field": "estimated_occupancy",
                                      "value": 120, "source": "facility page",
                                      "quoted_snippet": "Capacitat 120 places", "confidence": "medium"},
                   workbench=wb)
    assert "error" in out and "capacity" in out["hint"]
    assert wb.proposals == [] and wb.asset("fixture:pou_del_glac")["estimated_occupancy"] is None
    with pytest.raises(agent.CapacityAsOccupancyError):
        agent.propose_update("fixture:pou_del_glac", "estimated_occupancy", 120, "register total_places",
                             "total_places 120", "high", workbench=wb)
    # a headcount statement is accepted as estimated_occupancy
    ok = dispatch("propose_update", {"asset_id": "fixture:pou_del_glac", "field": "estimated_occupancy",
                                     "value": 42, "source": "facility phone call",
                                     "quoted_snippet": "42 people present today", "confidence": "high"},
                  workbench=wb)
    assert ok["status"] == "pending" and ok["field"] == "estimated_occupancy"


def test_propose_update_validates_values(wb):
    aid = "fixture:camping_gavarres"
    base = {"asset_id": aid, "source": "s", "quoted_snippet": "snippet", "confidence": "low"}
    assert "error" in dispatch("propose_update", {**base, "field": "asset_type", "value": "hotel"}, workbench=wb)
    assert "error" in dispatch("propose_update", {**base, "field": "capacity", "value": -1}, workbench=wb)
    assert "error" in dispatch("propose_update", {**base, "field": "capacity", "value": "many"}, workbench=wb)
    assert "error" in dispatch("propose_update", {**base, "field": "capacity", "value": 1.5}, workbench=wb)
    assert "error" in dispatch("propose_update", {**base, "field": "latitude", "value": 41.0}, workbench=wb)
    assert "error" in dispatch("propose_update", {**base, "field": "capacity", "value": 1, "confidence": "sure"},
                               workbench=wb)
    assert wb.proposals == []
    for value in list(config.VALUE_POLICY["by_type"]) + ["unknown"]:
        out = dispatch("propose_update", {**base, "field": "asset_type", "value": value}, workbench=wb)
        assert out["status"] == "pending" and out["value"] == value
    out = dispatch("propose_update", {**base, "field": "capacity", "value": "150"}, workbench=wb)
    assert out["value"] == 150
    assert wb.asset(aid)["asset_type"] == "campsite" and wb.asset(aid)["capacity"] == 200


def test_propose_update_evacuation_min_needs_nonnegative_number_and_source(wb):
    aid = "fixture:camping_gavarres"
    base = {"asset_id": aid, "field": "evacuation_min", "quoted_snippet": "evacuation plan: 2 h to clear the site",
            "confidence": "medium", "source": "facility evacuation plan"}
    assert "error" in dispatch("propose_update", {**base, "value": -1}, workbench=wb)
    assert "error" in dispatch("propose_update", {**base, "value": "two hours"}, workbench=wb)
    assert "error" in dispatch("propose_update", {**base, "value": True}, workbench=wb)
    assert "error" in dispatch("propose_update", {**base, "value": 120, "source": " "}, workbench=wb)
    assert wb.proposals == []
    out = dispatch("propose_update", {**base, "value": 120}, workbench=wb)
    assert out["status"] == "pending" and out["value"] == 120.0 and out["previous"] == 90.0
    assert dispatch("propose_update", {**base, "value": "37.5"}, workbench=wb)["value"] == 37.5
    assert wb.asset(aid)["evacuation_min"] == 90.0                                   # nothing applied
    propose = next(t for t in TOOLS if t["name"] == "propose_update")
    assert "evacuation_min" in propose["input_schema"]["properties"]["field"]["enum"]
    assert "fire_arrival_at" not in propose["input_schema"]["properties"]["field"]["enum"]


def test_dispatch_escalate_applies_nothing(wb):
    before = dict(wb.asset("fixture:camping_gavarres"))
    out = dispatch("escalate", {"asset_id": "fixture:camping_gavarres", "question": "tents or bungalows?",
                                "options": ["tents", "bungalows"], "default": "tents"}, workbench=wb)
    json.dumps(out)
    assert out["question_id"] == "q-001-camping_gavarres" and out["status"] == "open" and out["answer"] is None
    assert out["options"] == ["tents", "bungalows"] and out["default"] == "tents"
    assert wb.asset("fixture:camping_gavarres") == before and wb.proposals == []
    assert wb.questions[0] is not out and wb.questions[0]["question_id"] == out["question_id"]
    bad = dispatch("escalate", {"asset_id": "fixture:camping_gavarres", "question": "?", "options": ["a", "b"],
                                "default": "c"}, workbench=wb)
    assert "error" in bad
    answered = agent.answer_question(wb, out["question_id"], "bungalows")
    assert answered["status"] == "answered" and answered["answer"] == "bungalows"


# ---------------------------------------------------------------------------
# analyst confirmation
# ---------------------------------------------------------------------------
def test_confirm_proposal_persists_and_applies(wb):
    tasks = StubTasks()
    wb.tasks = tasks
    p = agent.propose_update("fixture:pou_del_glac", "capacity", 120, "manual enrichment", "Capacitat 120 places",
                             "medium", url="https://example.invalid/p", observed_at="2026-06-15", workbench=wb)
    rescored = []
    out = confirm_proposal(wb, p["proposal_id"], rescore=lambda w: rescored.append(w))
    assert out["status"] == "confirmed" and out["confirmed_at"] and out["override_id"] == "ov-1"
    assert rescored == [wb]
    call = tasks.calls[0]
    assert call["asset_id"] == "fixture:pou_del_glac" and call["field"] == "capacity" and call["value"] == 120
    assert call["source"] == "manual enrichment" and call["snippet"] == "Capacitat 120 places"
    assert call["url"] == "https://example.invalid/p" and call["observed_at"] == "2026-06-15"
    assert call["confidence"] == "medium" and call["proposal_id"] == p["proposal_id"]
    a = wb.asset("fixture:pou_del_glac")
    assert a["capacity"] == 120 and a["occupancy_basis"] == "analyst override"
    assert any("confirmed" in line and "persisted" in line for line in wb.change_log)
    with pytest.raises(ValueError):
        confirm_proposal(wb, p["proposal_id"])


def test_confirm_proposal_without_tasks_updates_in_memory(wb):
    p = agent.propose_update("fixture:camping_gavarres", "asset_type", "camp", "page", "bungalows", "high", workbench=wb)
    confirm_proposal(wb, p["proposal_id"])
    a = wb.asset("fixture:camping_gavarres")
    assert a["asset_type"] == "camp" and a["value_score"] == config.VALUE_POLICY["by_type"]["camp"]
    assert wb.proposals[0]["status"] == "confirmed" and wb.overrides[0]["field"] == "asset_type"
    assert a["slack_min"] == 60.0 and a["priority_rank"] == 2          # window recomputed from its own now_at, rank kept
    assert a["sources"][-1]["source"] == "analyst override: page"


def test_confirm_evacuation_proposal_recalculates_window_in_memory(wb):
    p = agent.propose_update("fixture:camping_gavarres", "evacuation_min", 120, "facility evacuation plan",
                             "the site is cleared in two hours", "medium", workbench=wb)
    confirm_proposal(wb, p["proposal_id"])
    a = wb.asset("fixture:camping_gavarres")
    assert a["evacuation_min"] == 120.0 and a["evacuation_source"] == "analyst override: facility evacuation plan"
    assert a["slack_min"] == 180.0 - 120.0 - 30.0 and a["priority_status"] == "window_open"
    assert any("window recalculated" in line for line in wb.change_log)


def test_confirm_proposal_reranks_the_workbench_snapshot():
    from fireline import priority
    from tests.helpers import make_asset, make_snapshot

    def timed(asset_id, evacuation_min, **kw):
        return make_asset(asset_id=asset_id, fire_arrival_at="2026-07-03T11:00:00+00:00", fire_arrival_basis="p10",
                          forecast_source="fixture:test-spread (synthetic)", evacuation_min=evacuation_min,
                          evacuation_source=None if evacuation_min is None else config.EVACUATION_POLICY["version"], **kw)

    snap = make_snapshot([timed("fixture:a", 90.0, name="A"),
                          timed("fixture:b", None, name="B", review_reasons=["evacuation_unknown"])])
    scored = priority.rank_snapshot(snap)
    wb = Workbench.from_scored(scored, snapshot=snap)
    assert [a["asset_id"] for a in scored["ranked"]] == ["fixture:a"] and wb.asset("fixture:b")["queue"] == "needs_review"
    p = agent.propose_update("fixture:b", "evacuation_min", 150, "phone call with the director",
                             "about two and a half hours to evacuate everyone", "high", workbench=wb)
    confirm_proposal(wb, p["proposal_id"])
    b = wb.asset("fixture:b")
    assert b["queue"] == "ranked" and b["slack_min"] == 180.0 - 150.0 - 30.0 and b["priority_rank"] == 1
    assert wb.asset("fixture:a")["priority_rank"] == 2 and b["review_reasons"] == []
    assert wb.confirmed_overrides()[0]["field"] == "evacuation_min"
    assert agent.rerank(Workbench()) is None


def test_reject_proposal(wb):
    p = agent.propose_update("fixture:pou_del_glac", "capacity", 120, "page", "Capacitat 120 places", "low",
                             workbench=wb)
    out = reject_proposal(wb, p["proposal_id"], note="wrong facility")
    assert out["status"] == "rejected" and out["note"] == "wrong facility"
    assert wb.asset("fixture:pou_del_glac")["capacity"] is None
    with pytest.raises(ValueError):
        confirm_proposal(wb, p["proposal_id"])
    with pytest.raises(KeyError):
        reject_proposal(wb, "prop-999-x")


# ---------------------------------------------------------------------------
# lookup_facility
# ---------------------------------------------------------------------------
def test_lookup_facility_evidence_and_registers():
    cands = agent.lookup_facility("Pou del Glaç")
    ids = [c["evidence_id"] for c in cands]
    assert ids[0] == "ev:pou_del_glac:page"                     # evidence page first (has capacity + url)
    page = cands[0]
    assert page["capacity"] == 120 and page["url"] and page["observed_at"] == "2026-06-15"
    assert "capacitat 120" in page["snippet"].lower()
    assert "reg:fixture:schools:0" in ids                        # register rows carry deterministic ids
    reg = next(c for c in cands if c["evidence_id"] == "reg:fixture:schools:0")
    assert reg["register"] == "fixture:schools" and reg["url"] is None and reg["capacity"] is None
    assert reg["snippet"] == "Pou del Glaç, la Bisbal d'Empordà, capacity unknown"
    geri = agent.lookup_facility("Residència Geriàtrica de la Bisbal")[0]
    assert geri["register"] == "fixture:care_homes" and geri["capacity"] == 48 and geri["fields"]["capacitat"] == 48
    assert geri["snippet"] == "Residència Geriàtrica de la Bisbal, la Bisbal d'Empordà, capacity 48"
    assert agent.lookup_facility("") == []
    assert agent.lookup_facility("pou del glac")                # accent-insensitive
    real = agent.lookup_facility("Residència Calonge")
    assert real and real[0]["evidence_id"] == "ev:gencat:care_homes:S07567" and real[0]["capacity"] == 35


def test_lookup_facility_municipality_filter_excludes_distractor():
    all_hits = agent.lookup_facility("Residència sense coordenades")
    munis = {c["municipality"] for c in all_hits}
    assert {"Cruïlles, Monells i Sant Sadurní de l'Heura", "Palafrugell"} <= munis
    filtered = agent.lookup_facility("Residència sense coordenades", municipality="Cruïlles, Monells i Sant Sadurní de l'Heura")
    assert filtered and all(c["municipality"].startswith("Cruïlles") for c in filtered)
    assert filtered[0]["capacity"] == 40 and "Carrer de la Font" in filtered[0]["snippet"]
    assert filtered[0]["evidence_id"] == "ev:residencia_sense_coordenades:register"
    # the distractor (same name, Palafrugell) is excluded; a municipality with no such facility returns only
    # weak token matches, never the fixture entries
    elsewhere = agent.lookup_facility("Residència sense coordenades", municipality="Girona")
    assert not any(c["name"] == "Residència sense coordenades" for c in elsewhere)
    assert not any(c["evidence_id"].startswith("ev:residencia_sense_coordenades") for c in elsewhere)


# ---------------------------------------------------------------------------
# investigate with FakeLLM
# ---------------------------------------------------------------------------
def test_investigate_occupancy_unknown_proposes_capacity_not_occupancy(wb):
    r = investigate(wb, "fixture:pou_del_glac", llm=FakeLLM())
    assert r["llm_mode"] == "fake" and r["postcheck_ok"] is True and r["steps"] <= 6
    assert [c["name"] for c in r["tool_calls"]][:2] == ["get_asset", "lookup_facility"]
    assert r["tool_calls"][1]["input"]["municipality"] == "la Bisbal d'Empordà"
    assert len(r["proposals_added"]) == 1
    p = r["proposals_added"][0]
    assert p["field"] == "capacity" and p["value"] == 120 and p["status"] == "pending"
    assert "120" in p["quoted_snippet"] and p["url"] and p["observed_at"] == "2026-06-15"
    assert not any(p["field"] == "estimated_occupancy" for p in r["proposals_added"])
    a = wb.asset("fixture:pou_del_glac")
    assert a["capacity"] is None and a["estimated_occupancy"] is None      # nothing applied
    assert len(r["questions_added"]) == 1 and r["questions_added"][0]["default"] == "yes"
    assert "session" in r["questions_added"][0]["question"]
    assert r["final_text"].startswith("Recommendation, not an order") and "120" in r["final_text"]
    assert "pending" in r["final_text"]
    assert set(r) >= {"asset_id", "name", "review_reasons", "steps", "tool_calls", "final_text", "proposals_added",
                      "questions_added", "postcheck_ok", "llm_mode"}


def test_investigate_class_ambiguous(wb):
    r = investigate(wb, "fixture:camping_gavarres", llm=FakeLLM())
    assert r["postcheck_ok"] and (r["proposals_added"] or r["questions_added"])
    if r["proposals_added"]:
        assert r["proposals_added"][0]["field"] == "asset_type"
    assert wb.asset("fixture:camping_gavarres")["asset_type"] == "campsite"


def test_investigate_location_unknown_escalates_with_address(wb):
    r = investigate(wb, "fixture:residencia_sense_coordenades", llm=FakeLLM())
    assert r["postcheck_ok"]
    qs = [q["question"] for q in r["questions_added"]]
    assert any("address" in q and "Carrer de la Font" in q for q in qs)
    assert all(p["field"] == "capacity" for p in r["proposals_added"])
    assert wb.asset("fixture:residencia_sense_coordenades")["latitude"] is None


def test_investigate_no_evidence_escalates_only(wb):
    r = investigate(wb, "fixture:mas_nou", llm=FakeLLM())
    assert r["postcheck_ok"] and r["proposals_added"] == []
    assert len(r["questions_added"]) == 1 and r["questions_added"][0]["status"] == "open"
    assert wb.asset("fixture:mas_nou")["capacity"] is None


def test_investigate_default_llm_is_fake(wb):
    r = investigate(wb, "fixture:mas_nou")
    assert r["llm_mode"] == "fake" and r["postcheck_ok"]


def test_investigate_all_order_and_coverage(wb):
    records = investigate_all(wb, llm=FakeLLM())
    ids = [r["asset_id"] for r in records]
    assert "fixture:sant_pol" not in ids                                         # no review reasons
    assert "fixture:no_forecast" not in ids                                      # producer gap only: review queue
    assert agent.review_order(wb) == ids
    assert set(ids) == {"fixture:pou_del_glac", "fixture:residencia_sense_coordenades", "fixture:mas_nou",
                        "fixture:camping_gavarres"}
    assert ids[-1] == "fixture:camping_gavarres"                                 # ranked after needs_review
    assert ids[:3] == ["fixture:pou_del_glac", "fixture:residencia_sense_coordenades", "fixture:mas_nou"]
    assert all(r["postcheck_ok"] for r in records)
    assert sum(1 for line in wb.change_log if ": investigation (fake)" in line) == len(records)


# ---------------------------------------------------------------------------
# post-check, step cap, llm_mode
# ---------------------------------------------------------------------------
def test_postcheck_numbers():
    results = ['{"capacity": 48, "distance_to_fire_m": 2135}']
    assert postcheck_numbers("48 places, 2135 m", results) == (True, [])
    ok, bad = postcheck_numbers("150 children", results)
    assert not ok and bad == ["150"]
    assert postcheck_numbers("asset x:12 has 48", results, asset_id="fixture:x12")[0]
    assert postcheck_numbers("no numbers here", [])[0]


class LyingLLM:
    """One get_asset call then a final text with a number that no tool result contains."""

    def create(self, system, messages, tools):
        payload = json.loads(messages[0]["content"][messages[0]["content"].index("{"):])
        if len(messages) == 1:
            return FakeResponse([ToolUseBlock("t1", "get_asset", {"asset_id": payload["asset_id"]})], "tool_use")
        return FakeResponse([TextBlock("There are 150 children on site.")], "end_turn")


def test_postcheck_withholds_invented_number(wb):
    r = investigate(wb, "fixture:pou_del_glac", llm=LyingLLM())
    assert r["postcheck_ok"] is False and r["final_text"] == POSTCHECK_FAILED_TEXT
    assert r["llm_mode"] == "custom"
    assert any("post-check failed" in line and "150" in line for line in wb.change_log)


class LoopingLLM:
    def create(self, system, messages, tools):
        payload = json.loads(messages[0]["content"][messages[0]["content"].index("{"):])
        return FakeResponse([ToolUseBlock(f"t{len(messages)}", "get_asset", {"asset_id": payload["asset_id"]})],
                            "tool_use")


def test_step_cap(wb):
    r = investigate(wb, "fixture:pou_del_glac", llm=LoopingLLM(), max_steps=3)
    assert r["steps"] == 3 and len(r["tool_calls"]) == 3
    assert r["final_text"] == STEP_CAP_TEXT
    assert any("step cap" in line for line in wb.change_log)


def test_llm_mode_labelling():
    assert agent.llm_mode(None) == "fake"
    assert agent.llm_mode(FakeLLM()) == "fake"
    assert agent.llm_mode(AnthropicLLM(client=object())) == "live"   # client injected: no credentials needed
    assert agent.llm_mode(LyingLLM()) == "custom"


def test_system_prompt_mentions_four_tools_and_confirmation():
    for name in ("get_asset", "lookup_facility", "propose_update", "escalate"):
        assert name in agent.SYSTEM_PROMPT
    assert "confirm" in agent.SYSTEM_PROMPT and "Capacity is not occupancy" in agent.SYSTEM_PROMPT
    for reason in agent.REVIEW_REASONS:
        assert reason in agent.SYSTEM_PROMPT
    assert {"forecast_unavailable", "evacuation_unknown"} <= set(agent.REVIEW_REASONS)
    assert "evacuation window" in agent.SYSTEM_PROMPT and "priority score" not in agent.SYSTEM_PROMPT
    assert "never supply a forecast arrival" in agent.SYSTEM_PROMPT
