"""Readiness is distinct from a call connection, crew coverage and completed evacuation."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from enum import IntEnum

import pytest

from fireline import evacuation_readiness as er
from fireline.contact_priority import ContactPolicy
from fireline.priority_examples import load_scenario


def inputs():
    scenario = load_scenario("fixtures/static_priority.json")
    call = er.CallAssessment(
        asset_id="B",
        call_id="demo-B",
        status="completed",
        observed_min=0,
        source="synthetic interview",
        identity_confirmed=True,
        whole_household_confirmed=True,
        can_self_evacuate=True,
        transport_available=True,
        wants_human=False,
        confidence=0.95,
        evidence="We are the eight residents at B. Everyone can leave in our two cars. No callback needed.",
    )
    centre = er.ReceptionCentre(
        "centre", "Community centre", 10, True, 60, "synthetic analyst approval"
    )
    route = er.EvacuationRoute("B", "centre", 2, 4, True, 30, "synthetic route check")
    return scenario, call, centre, route


def run(call_changes=None, centre_changes=None, route_changes=None, **kwargs):
    scenario, call, centre, route = inputs()
    result = er.coordinate_evacuation(
        scenario,
        [replace(call, **(call_changes or {}))],
        [replace(centre, **(centre_changes or {}))],
        [replace(route, **(route_changes or {}))],
        **kwargs,
    )
    return result, next(r for r in result["locations"] if r["asset_id"] == "B")


def test_house_outside_crew_plan_gets_self_evacuation_and_arrival_followup():
    result, house = run()
    assert {r["asset_id"] for r in result["locations"]} == {"A", "B", "C"}
    assert [s["action_id"] for s in result["response"]["steps"]] == ["act_C", "act_A"]
    assert house["mode"] == "self_evacuate"
    assert house["destination_id"] == "centre"
    assert house["evacuation_status"] == "not_confirmed"
    assert "confirm_arrival" in house["tasks"]
    assert house["human_followup"] is False
    assert result["remaining_capacity"]["centre"] == 2


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"confidence": 0.4}, "low_confidence"),
        ({"confidence": None}, "unknown_confidence"),
        ({"wants_human": True}, "human_requested"),
        ({"wants_human": None}, "incomplete_answers"),
        ({"identity_confirmed": False}, "identity_unconfirmed"),
        ({"whole_household_confirmed": False}, "household_unconfirmed"),
        ({"can_self_evacuate": None}, "incomplete_answers"),
        ({"transport_available": None}, "incomplete_answers"),
        ({"status": "no_answer"}, "no_answer"),
        ({"status": "failed"}, "failed"),
        ({"evidence": ""}, "missing_evidence"),
        ({"source": ""}, "missing_evidence"),
        ({"contradictory": True}, "contradictory_answers"),
    ],
)
def test_uncertain_or_human_requested_contact_cannot_be_marked_self_evacuate(
    change, reason
):
    result, house = run(call_changes=change)
    assert house["mode"] == "undetermined"
    assert house["human_followup"] is True
    assert reason in house["reasons"]
    assert house["destination_id"] is None
    assert result["remaining_capacity"]["centre"] == 10


@pytest.mark.parametrize(
    "change", [{"can_self_evacuate": False}, {"transport_available": False}]
)
def test_confirmed_assistance_need_produces_response_task(change):
    _, house = run(call_changes=change)
    assert house["mode"] == "assisted_evacuation"
    assert "arrange_assistance" in house["tasks"]
    assert house["evacuation_status"] == "not_confirmed"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"centre_changes": {"approved": False}},
        {"centre_changes": {"remaining_places": 7}},
        {"centre_changes": {"available_until_min": 3}},
        {"route_changes": {"confirmed": False}},
        {"route_changes": {"available_until_min": 3}},
        {"route_changes": {"evacuation_min": 13}},
        {"route_changes": {"source": ""}},
    ],
)
def test_infeasible_reception_or_route_keeps_house_in_review(kwargs):
    _, house = run(**kwargs)
    assert house["mode"] == "undetermined"
    assert house["self_evacuation_ability"] == "confirmed"
    assert house["human_followup"] is True
    assert "no_feasible_destination" in house["reasons"]


def test_expired_or_future_interview_requires_new_contact():
    _, stale = run(contact_policy=ContactPolicy(now_min=16))
    assert "stale_assessment" in stale["reasons"]
    _, future = run(call_changes={"observed_min": 1})
    assert "future_assessment" in future["reasons"]


def test_known_assistance_cannot_be_erased_by_positive_call():
    scenario, call, centre, route = inputs()
    scenario = replace(
        scenario,
        locations=tuple(
            replace(a, assisted=1) if a.asset_id == "B" else a
            for a in scenario.locations
        ),
    )
    result = er.coordinate_evacuation(scenario, [call], [centre], [route])
    house = next(r for r in result["locations"] if r["asset_id"] == "B")
    assert house["mode"] == "undetermined"
    assert "conflicts_with_assistance_record" in house["reasons"]


def test_nearest_eligible_centre_by_supplied_travel_time_not_building_type():
    scenario, call, centre, route = inputs()
    hospital = replace(
        centre, centre_id="hospital", name="Nearby hospital", approved=False
    )
    near_route = replace(route, centre_id="hospital", travel_min=1)
    result = er.coordinate_evacuation(
        scenario, [call], [hospital, centre], [near_route, route]
    )
    assert (
        next(r for r in result["locations"] if r["asset_id"] == "B")["destination_id"]
        == "centre"
    )


def test_capacity_is_reserved_in_contact_order_and_never_overbooked():
    scenario, call, centre, route = inputs()
    c_call = replace(call, asset_id="C", call_id="demo-C")
    c_route = replace(route, asset_id="C")
    result = er.coordinate_evacuation(
        scenario, [c_call, call], [centre], [c_route, route]
    )
    by_id = {r["asset_id"]: r for r in result["locations"]}
    assert by_id["B"]["destination_id"] == "centre"
    assert by_id["C"]["destination_id"] is None
    assert result["remaining_capacity"]["centre"] == 2


def test_capacity_rejects_integer_subclasses():
    class ReportedPlaces(int):
        pass

    with pytest.raises(ValueError, match="nonnegative integer"):
        run(centre_changes={"remaining_places": ReportedPlaces(10)})


def test_capacity_rejects_integer_enums():
    class Places(IntEnum):
        TEN = 10

    with pytest.raises(ValueError, match="nonnegative integer"):
        run(centre_changes={"remaining_places": Places.TEN})


def test_zero_capacity_is_valid_but_cannot_receive_household():
    result, house = run(centre_changes={"remaining_places": 0})
    assert house["destination_id"] is None
    assert result["remaining_capacity"]["centre"] == 0
    assert "insufficient_capacity" in house["destination_rejections"][0]["reasons"]


@pytest.mark.parametrize("remaining_places", [True, False, 10.0, 1.5, "10", -1, None])
def test_capacity_rejects_non_integer_and_negative_inputs(remaining_places):
    with pytest.raises(ValueError, match="nonnegative integer"):
        run(centre_changes={"remaining_places": remaining_places})


def test_missing_contact_not_ignored_even_if_crew_selected():
    result, _ = run()
    a = next(r for r in result["locations"] if r["asset_id"] == "A")
    assert a["mode"] == "undetermined"
    assert a["human_followup"] is True
    assert "contact_household" in a["tasks"]


@pytest.mark.parametrize(
    "changes",
    [{"confidence": float("nan")}, {"confidence": 1.1}, {"can_self_evacuate": "yes"}],
)
def test_invalid_call_fields_are_rejected(changes):
    with pytest.raises(ValueError):
        run(call_changes=changes)


def test_duplicate_assessments_and_unknown_assets_are_rejected():
    scenario, call, centre, route = inputs()
    with pytest.raises(ValueError, match="duplicate"):
        er.coordinate_evacuation(scenario, [call, call], [centre], [route])
    with pytest.raises(ValueError, match="unknown"):
        er.coordinate_evacuation(
            scenario, [replace(call, asset_id="unknown")], [centre], [route]
        )


def test_elapsed_time_does_not_present_old_crew_sequence_as_current():
    result, _ = run(contact_policy=ContactPolicy(now_min=1))
    assert result["response"] is None
    assert result["response_replanning_required"] is True


def test_zero_remaining_window_does_not_authorize_self_evacuation():
    _, house = run(route_changes={"evacuation_min": 12})
    assert house["mode"] == "undetermined"
    assert (
        "evacuation_window_exhausted" in house["destination_rejections"][0]["reasons"]
    )


def test_unknown_household_size_does_not_reserve_zero_places():
    scenario, call, centre, route = inputs()
    scenario = replace(
        scenario,
        locations=tuple(
            replace(a, people=None) if a.asset_id == "B" else a
            for a in scenario.locations
        ),
    )
    result = er.coordinate_evacuation(scenario, [call], [centre], [route])
    b = next(r for r in result["locations"] if r["asset_id"] == "B")
    assert b["mode"] == "undetermined"
    assert result["remaining_capacity"]["centre"] == 10


def test_cli_includes_household_readiness():
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/static_priorities.py",
            "--readiness-input",
            "fixtures/evacuation_readiness.json",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert {r["asset_id"]: r["mode"] for r in result["locations"]} == {
        "A": "assisted_evacuation",
        "B": "self_evacuate",
        "C": "undetermined",
    }
    assert result["dispatch"] is False


def test_cli_reads_non_ascii_readiness_input_with_ascii_process_locale(tmp_path):
    readiness = json.loads(
        Path("fixtures/evacuation_readiness.json").read_text(encoding="utf-8")
    )
    readiness["assessments"][0]["evidence"] = "Família preparada per sortir"
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(
        json.dumps(readiness, ensure_ascii=False), encoding="utf-8"
    )
    output_path = tmp_path / "result.json"
    env = os.environ.copy()
    env.update(
        LC_ALL="C",
        LANG="C",
        PYTHONUTF8="0",
        PYTHONCOERCECLOCALE="0",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/static_priorities.py",
            "--readiness-input",
            str(readiness_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    result = json.loads(output_path.read_text(encoding="utf-8"))
    a = next(row for row in result["locations"] if row["asset_id"] == "A")
    assert a["call_evidence"] == "Família preparada per sortir"
