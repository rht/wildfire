"""New planning evidence survives publication and cannot bypass order validation."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from fireline.coordination import _response_proposal
from fireline.dashboard_approvals import plans_for
from fireline.dashboard_plan_review import preview_order
from fireline.dashboard_public import public_state
from tests.test_dashboard_plan_review import review_state
from tests.test_multi_response import plan, scene


def engine_output():
    data = scene()
    response = plan(data)
    snapshot = {
        "scenario_id": data["scenario_id"],
        "snapshot_id": data["snapshot_id"],
        "assets": [{"asset_id": a["asset_id"]} for a in data["assets"]],
    }
    return response, snapshot


def publish(response, snapshot):
    response = _response_proposal(
        response, snapshot, response["now_min"], now=datetime(2026, 9, 20, tzinfo=UTC)
    )
    return public_state(
        {
            "schema_version": "coordination-state-1",
            "revision": 1,
            "assets": snapshot["assets"],
            "plan": {"response": response},
        }
    )["plan"]["response"]


def test_urgent_review_timing_and_remaining_needs_reach_browser_without_private_data():
    response, snapshot = engine_output()
    response["review"] = [
        {
            "asset_id": "A",
            "action_id": "assist-A",
            "reason": "urgent_intervention_review",
            "reasons": ["deadline"],
            "human_decision_required": True,
            "candidate_attempts": [
                {
                    "team_id": "one",
                    "start_min": 12,
                    "finish_min": 20,
                    "deadline_min": 15,
                    "private_note": "secret",
                }
            ],
        }
    ]
    response["remaining_needs"] = [
        {
            "asset_id": "A",
            "original_people": 5,
            "remaining_people": 2,
            "original_assisted": 3,
            "remaining_assisted": 2,
            "confirmed_arrived": 3,
            "source": "confirmed allocation ledger",
            "review_reasons": [],
        }
    ]
    result = publish(response, snapshot)
    assert result["review"][0]["human_decision_required"] is True
    assert result["review"][0]["candidate_attempts"][0]["finish_min"] == 20
    assert "private_note" not in result["review"][0]["candidate_attempts"][0]
    assert result["remaining_needs"][0]["remaining_people"] == 2


def test_full_mission_and_uncertainty_are_bound_to_approval_version():
    state = review_state()
    args = {
        "source": "live",
        "incident_id": state["scenario_id"],
        "revision": state["revision"],
        "snapshot_id": state["snapshot_id"],
    }
    before = plans_for(state, **args)[0]["plan_version"]
    task = state["plan"]["response"]["teams"][0]["tasks"][0]
    task.update(
        mission_status="complete_evacuation",
        delivered_people=2,
        mission_legs=[
            {
                "kind": "unload",
                "from_node": "hall",
                "to_node": "hall",
                "finish_min": 8,
                "people": 2,
            }
        ],
        sensitivity={"status": "fragile", "reasons": ["early_arrival"]},
    )
    after = plans_for(state, **args)[0]["plan_version"]
    assert before != after
    task["mission_legs"][0]["finish_min"] = 9
    assert plans_for(state, **args)[0]["plan_version"] != after
    result = public_state(state)["plan"]["response"]["teams"][0]["tasks"][0]
    assert result["mission_legs"][0]["finish_min"] == 9
    assert result["sensitivity"]["status"] == "fragile"


@pytest.mark.parametrize(
    "details",
    [
        {
            "mission_status": "complete_evacuation",
            "evacuation": {"destination_id": "hall"},
        },
        {"required_team_count": 2, "team_ids": ["crew-1", "crew-2"], "mission_id": "a"},
    ],
)
def test_single_crew_reorder_cannot_recalculate_advanced_mission_as_a_simple_stop(
    details,
):
    state = review_state()
    state["plan"]["response"]["teams"][0]["tasks"][0].update(details)
    args = {
        "source": "live",
        "incident_id": state["scenario_id"],
        "revision": state["revision"],
        "snapshot_id": state["snapshot_id"],
    }
    base = plans_for(state, **args)[0]
    result = preview_order(
        state,
        **args,
        team_id=base["team_id"],
        plan_version=base["plan_version"],
        action_ids=["b", "a"],
    )
    assert result["can_confirm"] is False
    assert "coordinated_mission_review" in {b["code"] for b in result["blockers"]}


def test_reordered_assistance_uses_freshness_at_start_not_finish_plus_fire_buffer():
    state = review_state()
    crew = state["plan"]["response"]["teams"][0]
    crew["tasks"] = crew["tasks"][:1]
    crew["planning_context"]["buffer_min"] = 30
    crew["tasks"][0].update(
        readiness_required=True,
        readiness={
            "status": "assistance_required",
            "observed_min": 0,
            "valid_until_min": 15,
        },
    )
    args = {
        "source": "live",
        "incident_id": state["scenario_id"],
        "revision": state["revision"],
        "snapshot_id": state["snapshot_id"],
    }
    base = plans_for(state, **args)[0]
    result = preview_order(
        state,
        **args,
        team_id=base["team_id"],
        plan_version=base["plan_version"],
        action_ids=["a"],
    )
    assert result["can_confirm"] is True, result["blockers"]


def test_joint_membership_must_match_all_crew_rows_and_survive_public_projection():
    response, snapshot = engine_output()
    task = deepcopy(response["teams"][0]["tasks"][0])
    tids = [t["team_id"] for t in response["teams"]]
    assert len(tids) == 2
    for team in response["teams"]:
        team["tasks"] = [
            task
            | {
                "team_id": team["team_id"],
                "team_ids": tids,
                "required_team_count": 2,
                "mission_id": task["action_id"],
                "benefit_owner_team_id": tids[0],
            }
        ]
    result = publish(response, snapshot)
    assert result["teams"][1]["tasks"][0]["team_ids"] == tids
    response["teams"][1]["tasks"][0]["asset_id"] = "C"
    with pytest.raises(ValueError, match="joint"):
        publish(response, snapshot)


def test_public_per_asset_coverage_preserves_identifiers_and_separate_dimensions():
    response, snapshot = engine_output()
    response["coverage_by_dimension"] = {
        "A": {"assisted": 0.5, "people": 0.5, "value": 1}
    }
    result = publish(response, snapshot)
    assert result["coverage"]["A"] == response["coverage"]["A"]
    assert result["coverage_by_dimension"]["A"] == {
        "assisted": 0.5,
        "people": 0.5,
        "value": 1,
    }


def test_reorder_discards_sensitivity_computed_for_the_old_sequence():
    state = review_state()
    crew = state["plan"]["response"]["teams"][0]
    crew["tasks"][0]["sensitivity"] = {
        "status": "robust",
        "duration_high_min": 3,
        "deadline_early_min": 5,
        "stress_finish_min": 4,
        "stress_deadline_min": 5,
        "reasons": [],
    }
    args = {
        "source": "live",
        "incident_id": state["scenario_id"],
        "revision": state["revision"],
        "snapshot_id": state["snapshot_id"],
    }
    base = plans_for(state, **args)[0]
    result = preview_order(
        state,
        **args,
        team_id=base["team_id"],
        plan_version=base["plan_version"],
        action_ids=["b", "a"],
    )
    row = next(t for t in result["reviewed_plan"]["tasks"] if t["action_id"] == "a")
    assert row["sensitivity"]["status"] == "unknown"
    assert "stress_finish_min" not in row["sensitivity"]
    assert (
        "analyst_order_requires_sensitivity_reassessment"
        in row["sensitivity"]["reasons"]
    )
