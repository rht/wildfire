import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fireline.incident_runtime import IncidentRuntime, demo_trigger

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 20, 8, tzinfo=UTC)


def settings():
    ops = json.loads((ROOT / "fixtures/end_to_end/operations.json").read_text())
    contacts = [
        {"asset_id": a, "contact_number": f"+1202555012{i}"}
        for i, a in enumerate("ABC")
    ]
    return {
        "input_mode": "synthetic",
        "call_mode": "disabled",
        "search_radius_m": 10000,
        "catalog_file": str(ROOT / "fixtures/end_to_end/catalog.json"),
        "operations": ops,
        "contacts": contacts,
        "approvals": [dict(c, snapshot_id="end-to-end-demo-0001") for c in contacts],
    }


def test_one_fire_discovers_assesses_queues_and_publishes_two_algorithms(tmp_path):
    runtime = IncidentRuntime(tmp_path, settings())
    first = runtime.trigger(demo_trigger(NOW))
    assert [a["asset_id"] for a in first["assets"]] == list("ABC")
    assert all("value_score" in a for a in first["assets"])
    assert [r["asset_id"] for r in first["contacts"]["ranked"]] == list("ABC")
    assert all(c["status"] == "queued" for c in first["calls"])
    assert first["plan"]["response"]["schema_version"] == "multi-response-plan-1"
    assert first["plan"]["response"]["dispatch"] is False
    state = runtime.simulate("A", "no_answer")
    a = next(c for c in state["calls"] if c["asset_id"] == "A")
    assert a["status"] == "no_answer" and a["can_self_evacuate"] is None
    assert any(
        t["asset_id"] == "A" and t["kind"] == "human_callback" for t in state["tasks"]
    )
    restarted = IncidentRuntime(tmp_path, settings())
    assert restarted.state() == state
    assert restarted.trigger(demo_trigger(NOW)) == state
    assert restarted.tick() == state


def test_changed_trigger_identity_and_incident_are_rejected_without_changing_state(
    tmp_path,
):
    runtime = IncidentRuntime(tmp_path, settings())
    trigger = demo_trigger(NOW)
    first = runtime.trigger(trigger)
    changed = deepcopy(trigger)
    changed["fire"]["source"] = "different"
    with pytest.raises(ValueError):
        runtime.trigger(changed)
    changed = deepcopy(trigger)
    changed["trigger_id"] = "new"
    changed["fire"]["incident_id"] = "other"
    with pytest.raises(ValueError):
        runtime.trigger(changed)
    assert runtime.state() == first


def test_missing_contacts_and_forecast_stay_reviewable(tmp_path):
    cfg = settings()
    cfg["contacts"] = []
    cfg["approvals"] = []
    runtime = IncidentRuntime(tmp_path, cfg)
    trigger = demo_trigger(NOW)
    trigger.pop("forecast")
    state = runtime.trigger(trigger)
    assert len(state["contacts"]["review"]) == 3 and not state["calls"]
    assert all(a["fire_arrival_at"] is None for a in state["assets"])
    assert runtime.system()["enqueue"]["blocked"]


def test_later_fire_refresh_preserves_calls_and_does_not_reuse_old_contact_approvals(
    tmp_path,
):
    runtime = IncidentRuntime(tmp_path, settings())
    runtime.trigger(demo_trigger(NOW))
    runtime.simulate("A", "no_answer")
    update = demo_trigger(NOW + timedelta(minutes=2))
    update["trigger_id"] = "update-2"
    state = runtime.trigger(update)
    assert state["snapshot_id"] == "end-to-end-demo-0002"
    assert len(state["calls"]) == 3
    assert (
        next(c for c in state["calls"] if c["asset_id"] == "A")["status"] == "no_answer"
    )
    assert any(e["code"] == "operational_inputs_need_refresh" for e in state["errors"])
    assert not [t for team in state["plan"]["response"]["teams"] for t in team["tasks"]]


def test_synthetic_calls_cannot_be_enabled_as_live(tmp_path):
    cfg = settings()
    cfg["call_mode"] = "live"
    with pytest.raises(ValueError):
        IncidentRuntime(tmp_path, cfg)


def test_approved_destination_and_dangerous_road_enter_exact_call_briefing(tmp_path):
    runtime = IncidentRuntime(tmp_path, settings())
    state = runtime.trigger(demo_trigger(NOW))
    coordinator = runtime._coordinator(runtime._load())
    try:
        row = next(c for c in state["calls"] if c["asset_id"] == "B")
        request = coordinator.voice.get(row["request_id"])["request"]
        brief = request["incident_brief"]
        assert "Demo Reception Hall" in brief and "South Road" in brief
        assert request["road_warnings"][0]["road_name"] == "Forest Road"
    finally:
        coordinator.close()
    b = next(r for r in state["plan"]["locations"] if r["asset_id"] == "B")
    assert b["destination_id"] == "hall"
    assert b["mode"] == "undetermined"  # reservation is not an evacuation confirmation


def test_invalid_planner_input_leaves_no_accepted_fire_or_queued_calls(tmp_path):
    runtime = IncidentRuntime(tmp_path, settings())
    bad = demo_trigger(NOW)
    bad["operations"] = deepcopy(settings()["operations"])
    bad["operations"]["teams"][0]["transport_capacity"] = -1
    with pytest.raises(ValueError):
        runtime.trigger(bad)
    assert runtime.state() is None
    assert runtime._load() is None
    assert not runtime.database.exists()


def test_interrupted_projection_resumes_same_trigger_and_snapshot(
    tmp_path, monkeypatch
):
    runtime = IncidentRuntime(tmp_path, settings())
    original = runtime.refresh
    monkeypatch.setattr(
        runtime, "refresh", lambda: (_ for _ in ()).throw(RuntimeError("interrupted"))
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        runtime.trigger(demo_trigger(NOW))
    monkeypatch.setattr(runtime, "refresh", original)
    state = IncidentRuntime(tmp_path, settings()).trigger(demo_trigger(NOW))
    assert state["snapshot_id"] == "end-to-end-demo-0001"
    assert len(state["calls"]) == 3
    assert len(runtime.system()["events"]) == 1


def test_new_snapshot_cancels_unstarted_calls_and_preserves_allocations_for_review(
    tmp_path,
):
    runtime = IncidentRuntime(tmp_path, settings())
    runtime.trigger(demo_trigger(NOW))
    update = demo_trigger(NOW + timedelta(minutes=2))
    update["trigger_id"] = "fire-update"
    state = runtime.trigger(update)
    coordinator = runtime._coordinator(runtime._load())
    try:
        assert not runtime._queue(coordinator).status()["pending"]
    finally:
        coordinator.close()
    b = next(r for r in state["plan"]["locations"] if r["asset_id"] == "B")
    assert b["destination_id"] == "hall"
    assert b["human_followup"] is True
    assert all(a["instruction_allowed"] is False for a in b["allocations"])


def test_invalid_synthetic_answers_do_not_bind_provider_or_change_state(tmp_path):
    runtime = IncidentRuntime(tmp_path, settings())
    before = runtime.trigger(demo_trigger(NOW))
    with pytest.raises(ValueError):
        runtime.simulate("A", "completed", {"confidence": 5})
    coordinator = runtime._coordinator(runtime._load())
    try:
        row = next(c for c in before["calls"] if c["asset_id"] == "A")
        assert coordinator.voice.get(row["request_id"])["provider_call_id"] is None
    finally:
        coordinator.close()
    assert runtime.state() == before


def test_live_projection_uses_one_clock_instant(tmp_path):
    cfg = settings()
    cfg["input_mode"] = "live"
    cfg["contacts"] = []
    cfg["approvals"] = []
    trigger = demo_trigger(NOW)
    trigger["forecast"]["input_mode"] = "live"
    trigger["fire"]["source"] = "clock-test"
    trigger["forecast"]["forecast_source"] = "clock-test"
    ticks = iter(NOW + timedelta(microseconds=i) for i in range(100))
    runtime = IncidentRuntime(tmp_path, cfg, clock=lambda: next(ticks))
    state = runtime.trigger(trigger)
    assert (
        state["plan"]["response"]["now_min"]
        == (datetime.fromisoformat(state["as_of"]) - NOW).total_seconds() / 60
    )


def test_trigger_cannot_relabel_explicit_input_mode(tmp_path):
    runtime = IncidentRuntime(tmp_path, settings())
    trigger = demo_trigger(NOW)
    trigger["input_mode"] = "recorded"
    with pytest.raises(ValueError, match="mode"):
        runtime.trigger(trigger)
    assert runtime.state() is None


def test_unknown_fire_observation_can_be_replaced_by_timestamped_update(tmp_path):
    runtime = IncidentRuntime(tmp_path, settings())
    trigger = demo_trigger(NOW)
    trigger["fire"]["observed_at"] = None
    runtime.trigger(trigger)
    update = demo_trigger(NOW + timedelta(minutes=2))
    update["trigger_id"] = "known-observation"
    state = runtime.trigger(update)
    assert state["snapshot_id"] == "end-to-end-demo-0002"
    assert state["fire_observed_at"] == update["fire"]["observed_at"]


def test_reported_assistance_updates_group_and_numbered_route_inputs(tmp_path):
    from fireline.dashboard_public import public_state
    from fireline.voice_models import ANSWER_FIELDS

    runtime = IncidentRuntime(tmp_path, settings())
    runtime.trigger(demo_trigger(NOW))
    answers = {
        "identity_confirmed": True,
        "whole_household_confirmed": True,
        "can_self_evacuate": False,
        "transport_available": False,
        "wants_human": True,
        "acknowledged": True,
        "confidence": 0.95,
        "confidence_basis": "synthetic_review",
        "road_warning_acknowledged": True,
        "evidence": {
            field: "Synthetic explicit response"
            for field in (*ANSWER_FIELDS, "road_warning_acknowledged")
        },
    }
    state = public_state(runtime.simulate("C", "completed", answers))
    group = next(g for g in state["peopleClusters"] if g["asset_id"] == "C")
    assert group["status"] == "needs_assistance" and group["people"] == 2
    crew = next(
        t for t in state["plan"]["response"]["teams"] if t["team_id"] == "crew-2"
    )
    assert crew["current_location"]["source"] == "synthetic crew GPS report"
    assert [t["asset_id"] for t in crew["tasks"]] == ["C"]
    assert len(crew["tasks"][0]["path_lonlat"]) >= 2
    assert any(
        t["asset_id"] == "C" and t["kind"] == "human_callback" for t in state["tasks"]
    )


def test_empty_operations_update_preserves_destinations_and_reserved_places(tmp_path):
    runtime = IncidentRuntime(tmp_path, settings())
    runtime.trigger(demo_trigger(NOW))
    update = demo_trigger(NOW + timedelta(minutes=2))
    update.update(trigger_id="no-operational-inputs", operations={})
    state = runtime.trigger(update)
    b = next(r for r in state["plan"]["locations"] if r["asset_id"] == "B")
    assert b["destination_id"] == "hall"
    assert all(a["instruction_allowed"] is False for a in b["allocations"])
    assert state["plan"]["remaining_capacity"]["hall"] == 8
    assert runtime.refresh() == state


def test_synthetic_result_retry_recovers_interrupted_projection(tmp_path, monkeypatch):
    runtime = IncidentRuntime(tmp_path, settings())
    runtime.trigger(demo_trigger(NOW))
    original = runtime.refresh
    monkeypatch.setattr(
        runtime, "refresh", lambda: (_ for _ in ()).throw(RuntimeError("interrupted"))
    )
    with pytest.raises(RuntimeError):
        runtime.simulate("A", "no_answer")
    monkeypatch.setattr(runtime, "refresh", original)
    state = runtime.simulate("A", "no_answer")
    assert (
        next(c for c in state["calls"] if c["asset_id"] == "A")["status"] == "no_answer"
    )
