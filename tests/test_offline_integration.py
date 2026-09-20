"""Real module/database integration with fictional calls, never provider access."""

import importlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from fireline.dashboard_server import CoordinationDatabase, create_app

EPOCH = datetime(2026, 9, 20, 8, tzinfo=UTC)


def api():
    return importlib.import_module("fireline.offline_integration")


def test_no_answer_connects_queue_escalation_allocations_and_crew_plan(
    tmp_path, monkeypatch
):
    from fireline.voice_store import VoiceStore

    def forbidden(*args, **kwargs):
        pytest.fail("offline integration tried to start a provider call")

    monkeypatch.setattr(VoiceStore, "start", forbidden)
    run = api().OfflineScenario(tmp_path, epoch=EPOCH)
    queued = run.prepare()
    assert [r["asset_id"] for r in queued["contacts"]["ranked"]] == ["A", "B", "C"]
    assert all(c["status"] == "queued" for c in queued["calls"])
    state = run.complete()
    assert state["revision"] > queued["revision"]
    assert state["input_mode"] == "synthetic"
    a = next(c for c in state["calls"] if c["asset_id"] == "A")
    assert a["status"] == "no_answer"
    assert a["human_followup_required"] is True
    assert "no_answer" in a["human_followup_reasons"]
    assert a["can_self_evacuate"] is None and a["transport_available"] is None
    assert a["acknowledged"] is None
    assert any(
        t["asset_id"] == "A" and t["kind"] == "human_callback" and t["status"] != "done"
        for t in state["tasks"]
    )
    locations = {r["asset_id"]: r for r in state["plan"]["locations"]}
    assert locations["A"]["mode"] == "undetermined"
    assert locations["A"]["evacuation_status"] == "not_confirmed"
    assert locations["B"]["destination_id"] == "hall"
    assert locations["B"]["allocations"][0]["state"] == "reserved"
    assert locations["B"]["allocations"][0]["instruction_allowed"] is True
    assert state["plan"]["remaining_capacity"]["hall"] == 16
    response = state["plan"]["response"]
    assert response["dispatch"] is False
    tasks = [t for team in response["teams"] for t in team["tasks"]]
    assert {t["action_id"] for t in tasks} == {"protect-B", "assist-C"}
    assert all(t["status"] == "proposed" and t["path_lonlat"] for t in tasks)
    blocked = next(t for t in response["unassigned"] if t["action_id"] == "assist-A")
    assert "readiness_review" in blocked["reasons"]
    for rid in run.queue.status()["pending"]:
        pytest.fail("finished synthetic calls remained pending: " + rid)
    b = next(c for c in state["calls"] if c["asset_id"] == "B")
    brief = run.coordinator.voice.get(b["request_id"])["request"]
    assert "Demo Reception Hall" in brief["incident_brief"]
    assert brief["road_warnings"][0]["road_name"] == "Forest Road"
    assert run.complete() == state
    run.close()
    reopened = api().OfflineScenario(tmp_path, epoch=EPOCH)
    assert reopened.prepare() == state
    assert reopened.complete() == state
    reopened.close()


def test_real_rest_and_websocket_publish_the_no_answer_transition(tmp_path):
    run = api().OfflineScenario(tmp_path, epoch=EPOCH)
    first = run.prepare()
    reader = CoordinationDatabase(run.database)
    with TestClient(create_app(reader, poll_interval=0.01)) as client:
        assert client.get("/api/state").json()["revision"] == first["revision"]
        with client.websocket_connect(
            "/api/updates?after_revision=" + str(first["revision"])
        ) as ws:
            after = run.complete()
            public = ws.receive_json()
            assert public["revision"] == after["revision"]
            assert any(c["status"] == "no_answer" for c in public["calls"])
            assert public["plan"]["locations"][1]["allocations"]
            assert public["plan"]["response"]["teams"]
            body = json.dumps(public)
            assert "+1202555" not in body and "incident_brief" not in body
            assert "Synthetic respondent:" not in body
    run.close()


@pytest.mark.parametrize(
    "change",
    [
        {"snapshot_id": "other"},
        {"incident_id": "other"},
        {"scenario_id": "other"},
        {"buffer_min": 0},
        {"epoch": "2026-09-20T07:00:00Z"},
    ],
)
def test_foreign_allocation_context_cannot_be_published(tmp_path, change, monkeypatch):
    run = api().OfflineScenario(tmp_path, epoch=EPOCH)
    first = run.prepare()
    original_view = run.allocations.view

    def foreign_view(*, as_of):
        plan, inputs = original_view(as_of=as_of)
        context, *rest = inputs
        return plan, (replace(context, **change), *rest)

    monkeypatch.setattr(run.allocations, "view", foreign_view)
    with pytest.raises(ValueError, match="allocation"):
        run.refresh()
    assert run.coordinator.state() == first
    run.close()


@pytest.mark.parametrize("interrupted_after", ["binding", "lifecycle"])
def test_interrupted_outcomes_resume_after_restart(tmp_path, interrupted_after):
    run = api().OfflineScenario(tmp_path, epoch=EPOCH)
    queued = run.prepare()
    request = next(c for c in queued["calls"] if c["asset_id"] == "B")
    rid = request["request_id"]
    run.coordinator.voice.bind(rid, "offline-B")
    if interrupted_after == "lifecycle":
        run.now = EPOCH + timedelta(minutes=1)
        run.coordinator.voice.record_lifecycle(
            event_id="offline-result-B",
            request_id=rid,
            asset_id="B",
            snapshot_id=request["snapshot_id"],
            provider_call_id="offline-B",
            status="completed",
            observed_at=run.at(1),
        )
    run.close()
    run = api().OfflineScenario(tmp_path, epoch=EPOCH)
    state = run.complete()
    b = next(c for c in state["calls"] if c["asset_id"] == "B")
    assert b["status"] == "completed" and b["can_self_evacuate"] is True
    assert run.queue.status()["active"] == 0
    assert run.complete() == state
    run.close()
