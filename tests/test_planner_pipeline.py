"""One synthetic fire through assessment, calls, rescue planning and public updates."""

from starlette.testclient import TestClient

from fireline.dashboard_public import public_state
from fireline.incident_runtime import IncidentRuntime, demo_trigger
from fireline.voice_models import ANSWER_FIELDS
from tests.test_incident_runtime import NOW, settings
from tests.test_incident_server import AUTH, app
from tests.test_llm_assessment import Model


def answers(**changes):
    return {
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
            k: "Synthetic explicit response"
            for k in (*ANSWER_FIELDS, "road_warning_acknowledged")
        },
    } | changes


def test_fire_llm_queue_mixed_call_outcomes_complete_mission_websocket_and_restart(
    tmp_path,
):
    cfg = settings() | {"llm_assessment": {"mode": "live"}}
    model = Model()
    runtime = IncidentRuntime(tmp_path, cfg, assessment_backend=model)
    with TestClient(app(runtime, poll_interval=0.01)) as client:
        response = client.post("/api/fire", json=demo_trigger(NOW), headers=AUTH)
        assert response.status_code == 200, response.text
        state = response.json()
        assert len(model.calls) == 3
        assert all(a["llm_assessment"]["status"] == "assessed" for a in state["assets"])
        assert [a["asset_id"] for a in state["contacts"]["ranked"]] == list("ABC")
        with client.websocket_connect(
            f"/api/updates?after_revision={state['revision'] - 1}"
        ) as ws:
            assert ws.receive_json()["revision"] == state["revision"]
            for aid, status, result in [
                ("A", "no_answer", None),
                (
                    "B",
                    "completed",
                    answers(
                        can_self_evacuate=True, transport_available=True, confidence=0.2
                    ),
                ),
                ("C", "completed", answers()),
            ]:
                response = client.post(
                    "/api/simulate",
                    headers=AUTH,
                    json={"asset_id": aid, "status": status, "answers": result},
                )
                assert response.status_code == 200, response.text
                state = response.json()
                assert ws.receive_json()["revision"] == state["revision"]
        assert client.get("/api/state").json() == state
        active = {
            (t["asset_id"], t["kind"]) for t in state["tasks"] if t["status"] != "done"
        }
        assert all((aid, "human_callback") in active for aid in "ABC")
        assert ("C", "arrange_assistance") in active
        crew = next(
            t for t in state["plan"]["response"]["teams"] if t["team_id"] == "crew-2"
        )
        task = next(t for t in crew["tasks"] if t["asset_id"] == "C")
        assert task["mission_status"] == "complete_evacuation"
        assert task["delivered_people"] == 2
        assert task["to_node"] == "hall"
        assert task["mission_legs"][-1]["finish_min"] == task["finish_min"]
        assert task["mission_legs"][-1]["to_node"] == "hall"
        assert crew["remaining_transport_capacity"] == 8
        assert (
            next(g for g in state["peopleClusters"] if g["asset_id"] == "C")["status"]
            == "needs_assistance"
        )
        assert (
            next(l for l in state["plan"]["locations"] if l["asset_id"] == "C")[
                "evacuation_status"
            ]
            != "arrival_confirmed"
        )
        assert "+1202555" not in response.text
        assert (
            client.post("/api/fire", json=demo_trigger(NOW), headers=AUTH).json()
            == state
        )
        assert len(model.calls) == 3
    assert (
        public_state(IncidentRuntime(tmp_path, cfg, assessment_backend=model).state())
        == state
    )


def test_late_intervention_is_visible_in_http_state_as_urgent_review(tmp_path):
    cfg = settings()
    cfg["operations"]["actions"][0]["deadline_min"] = 1
    runtime = IncidentRuntime(tmp_path, cfg)
    with TestClient(app(runtime)) as client:
        response = client.post("/api/fire", json=demo_trigger(NOW), headers=AUTH)
        assert response.status_code == 200, response.text
        response = response.json()["plan"]["response"]
        urgent = next(
            r for r in response["review"] if r.get("action_id") == "protect-B"
        )
        assert urgent["human_decision_required"] is True
        assert urgent["reason"] == "urgent_intervention_review"
        assert "deadline" in urgent["reasons"]
        assert urgent["candidate_attempts"]
