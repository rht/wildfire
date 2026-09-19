"""Behavioural checks for static contact ranking and action-sequence planning."""

import itertools
from dataclasses import replace

import pytest

from fireline.contact_priority import rank_contacts
from fireline.priority_models import Action, Benefit, Location, StaticScenario, Travel
from fireline.response_priority import plan_response


def loc(key, value=10, people=0, assisted=0, distance=200, deadline=20):
    return Location(key, key, 0, 0, distance, people, assisted, value, deadline)


def act(key, site, benefits=None, requires=(), duration=2, capabilities=()):
    return Action(
        key,
        site,
        duration,
        20,
        tuple(benefits or [Benefit(site, 1, True, "fixture")]),
        tuple(requires),
        tuple(capabilities),
    )


def scene(locations, actions, legs=None, horizon=10, capabilities=()):
    nodes = ["START"] + [a.asset_id for a in locations]
    if legs is None:
        legs = {(a, b): Travel(1) for a in nodes for b in nodes if a != b}
    return StaticScenario(
        "test",
        tuple(locations),
        tuple(actions),
        legs,
        "START",
        horizon,
        tuple(capabilities),
        0,
    )


def ids(result):
    return [s["action_id"] for s in result["steps"]]


def forecast_loc(key, arrival=30, evacuation=10, distance=200, **kwargs):
    return replace(
        loc(key, distance=distance, **kwargs),
        fire_arrival_min=arrival,
        evacuation_min=evacuation,
        forecast_source="synthetic forecast",
        evacuation_source="synthetic estimate",
    )


def test_contacts_use_forecast_arrival_minus_evacuation_not_value():
    result = rank_contacts(
        [
            forecast_loc("B", arrival=20, evacuation=5, value=10000),
            forecast_loc("C", arrival=20, evacuation=18, value=1),
            forecast_loc("A", arrival=40, evacuation=30, distance=500, value=100),
        ]
    )
    assert [r["asset_id"] for r in result["ranked"]] == ["C", "A", "B"]
    assert [r["slack_min"] for r in result["ranked"]] == [2, 10, 15]


def test_unknown_is_not_zero_and_ties_are_stable():
    result = rank_contacts(
        [
            forecast_loc("Z"),
            forecast_loc("B"),
            forecast_loc("unknown", arrival=None),
        ]
    )
    assert [r["asset_id"] for r in result["ranked"]] == ["B", "Z"]
    assert result["review"][0]["asset_id"] == "unknown"
    assert result["review"][0]["slack_min"] is None


def test_farther_location_can_be_more_urgent_due_to_spread_prediction():
    result = rank_contacts(
        [
            forecast_loc("near", arrival=60, evacuation=10, distance=100),
            forecast_loc("far", arrival=15, evacuation=10, distance=1000),
        ]
    )
    assert result["ranked"][0]["asset_id"] == "far"


def test_elapsed_time_buffer_and_negative_windows_are_visible():
    from fireline.contact_priority import ContactPolicy

    result = rank_contacts(
        [forecast_loc("A", arrival=20, evacuation=10)],
        ContactPolicy(now_min=7, buffer_min=5),
    )
    row = result["ranked"][0]
    assert row["time_to_impact_min"] == 13
    assert row["slack_min"] == -2
    assert row["status"] == "window_exhausted"
    assert row["latest_start_min"] == 5
    zero = rank_contacts(
        [forecast_loc("A", arrival=20, evacuation=10)],
        ContactPolicy(now_min=5, buffer_min=5),
    )["ranked"][0]
    assert zero["slack_min"] == 0 and zero["status"] == "window_exhausted"


def test_missing_forecast_or_evacuation_and_evidence_need_review():
    result = rank_contacts(
        [
            forecast_loc("no_evac", evacuation=None),
            replace(forecast_loc("no_source"), forecast_source=None),
            replace(forecast_loc("no_evac_source"), evacuation_source=None),
        ]
    )
    assert not result["ranked"]
    assert len(result["review"]) == 3


def test_people_value_and_missing_geographic_distance_do_not_override_known_window():
    result = rank_contacts(
        [
            forecast_loc("A", distance=None, people=None, assisted=None, value=None),
            forecast_loc("B", arrival=50, value=100000),
        ]
    )
    assert [r["asset_id"] for r in result["ranked"]] == ["A", "B"]


def test_equal_windows_use_arrival_then_geographic_distance_then_id():
    result = rank_contacts(
        [
            forecast_loc("Z", arrival=30, evacuation=20, distance=200),
            forecast_loc("C", arrival=20, evacuation=10, distance=500),
            forecast_loc("B", arrival=20, evacuation=10, distance=100),
        ]
    )
    assert [r["asset_id"] for r in result["ranked"]] == ["B", "C", "Z"]


@pytest.mark.parametrize(
    "changes",
    [
        {"fire_arrival_min": -1},
        {"evacuation_min": float("nan")},
        {"evacuation_min": -2},
    ],
)
def test_invalid_contact_timing_rejected(changes):
    with pytest.raises(ValueError):
        rank_contacts([replace(forecast_loc("A"), **changes)])


def test_lookahead_takes_zero_benefit_prerequisite_before_high_value_detour():
    a, b, c = loc("A", 100, 80, 60, deadline=8), loc("B", 80), loc("C", 0)
    actions = [
        act("B", "B"),
        act("C", "C", [Benefit("C", 0, True, "access only")]),
        act("A", "A", requires=["C"], duration=3),
    ]
    result = plan_response(scene([a, b, c], actions, horizon=7))
    assert ids(result) == ["C", "A"]
    assert result["objective"]["assisted_units"] == 60
    assert result["optimal"] is True


def test_explicit_protection_prioritises_c_and_does_not_double_count_a():
    a, b, c = loc("A", 100, 80, 60), loc("B", 80), loc("C", 20)
    actions = [
        act("B", "B"),
        act(
            "C",
            "C",
            [Benefit("C", 1, True, "fixture"), Benefit("A", 1, True, "fixture")],
        ),
        act("A", "A"),
    ]
    result = plan_response(scene([a, b, c], actions, horizon=6))
    assert ids(result) == ["C", "B"]
    assert result["objective"]["people_units"] == 80
    assert result["coverage"]["A"] == 1


def test_nearby_does_not_imply_protection_or_prerequisite():
    result = plan_response(
        scene(
            [loc("A", 100, 80, 60), loc("C", 20)],
            [act("A", "A"), act("C", "C")],
            horizon=3,
        )
    )
    assert ids(result) == ["A"]


def test_blocked_direction_and_missing_capability_are_not_traversed():
    s = scene(
        [loc("A", 100, 80, 60), loc("B", 80)],
        [act("A", "A", capabilities=["medical"]), act("B", "B")],
        {("START", "B"): Travel(1), ("B", "A"): Travel(1)},
    )
    result = plan_response(s)
    assert ids(result) == ["B"]
    assert result["unserved"]["A"]
    s = replace(s, capabilities=("medical",), travel={("A", "START"): Travel(1)})
    assert ids(plan_response(s)) == []


def test_deadline_includes_service_time_and_buffer():
    s = scene([loc("A", 100, 10, 5, deadline=3)], [act("A", "A")], horizon=10)
    assert ids(plan_response(s)) == ["A"]  # completes exactly at 3
    assert ids(plan_response(replace(s, buffer_min=0.01))) == []
    s = replace(s, travel={("START", "A"): Travel(1, 0.5)})
    assert ids(plan_response(s)) == []


def test_unknown_and_unconfirmed_effects_do_not_gain_benefit():
    s = scene(
        [loc("A", 100, None, None), loc("C", 20)],
        [
            act(
                "C",
                "C",
                [
                    Benefit("A", 1, False, "unverified"),
                    Benefit("C", 1, True, "fixture"),
                ],
            )
        ],
    )
    result = plan_response(s)
    assert result["coverage"]["A"] == 0
    assert result["review"]
    assert result["objective"]["people_units"] == 0


def test_partial_overlap_uses_maximum_not_sum():
    s = scene(
        [loc("A", 100, 100, 50), loc("B"), loc("C")],
        [
            act("B", "B", [Benefit("A", 0.6, True, "fixture")]),
            act("C", "C", [Benefit("A", 0.8, True, "fixture")]),
        ],
        horizon=6,
    )
    result = plan_response(s)
    assert result["objective"]["people_units"] == 80
    assert result["objective"]["assisted_units"] == 40


def test_property_cannot_outweigh_assisted_people_and_input_order_does_not_matter():
    locations = [loc("A", 1, 1, 1), loc("B", 1_000_000)]
    actions = [act("B", "B"), act("A", "A")]
    s = scene(locations, actions, horizon=3)
    assert ids(plan_response(s)) == ["A"]
    assert plan_response(s) == plan_response(
        replace(
            s, locations=tuple(reversed(locations)), actions=tuple(reversed(actions))
        )
    )


def test_empty_scenario_and_unreachable_deadline_return_explicit_results():
    assert ids(plan_response(scene([], []))) == []
    s = scene([loc("A", deadline=0)], [act("A", "A")])
    assert plan_response(s)["unserved"]["A"]


@pytest.mark.parametrize(
    "changes",
    [
        {"people": -1},
        {"assisted": 3, "people": 2},
        {"value": float("nan")},
        {"distance_m": float("inf")},
        {"deadline_min": -1},
    ],
)
def test_invalid_location_fields_rejected(changes):
    with pytest.raises(ValueError):
        plan_response(scene([replace(loc("A"), **changes)], [act("A", "A")]))


def test_cycles_duplicate_ids_and_large_search_rejected():
    with pytest.raises(ValueError, match="cycl"):
        plan_response(
            scene(
                [loc("A"), loc("B")],
                [act("A", "A", requires=["B"]), act("B", "B", requires=["A"])],
            )
        )
    with pytest.raises(ValueError, match="duplicate"):
        rank_contacts([loc("A"), loc("A")])
    with pytest.raises(ValueError, match="eight|8"):
        plan_response(
            scene(
                [loc(str(i)) for i in range(9)], [act(str(i), str(i)) for i in range(9)]
            )
        )


def test_small_random_problems_match_independent_permutation_oracle():
    import random

    rng = random.Random(741)
    for trial in range(12):
        locations = [
            loc(
                str(i),
                rng.randint(1, 100),
                rng.randint(1, 20),
                0,
                deadline=rng.randint(3, 12),
            )
            for i in range(4)
        ]
        locations = [replace(a, assisted=rng.randint(0, a.people)) for a in locations]
        actions = [
            act(a.asset_id, a.asset_id, duration=rng.randint(1, 3)) for a in locations
        ]
        s = scene(locations, actions, horizon=9)
        best = (0, 0, 0)
        for count in range(5):
            for order in itertools.permutations(range(4), count):
                t = 0
                objective = [0, 0, 0]
                valid = True
                for i in order:
                    t += 1 + actions[i].duration_min
                    a = locations[i]
                    if t > min(a.deadline_min, s.horizon_min):
                        valid = False
                        break
                    objective[0] += a.assisted
                    objective[1] += a.people
                    objective[2] += a.value
                if valid:
                    best = max(best, tuple(objective))
        result = plan_response(s)["objective"]
        assert (
            tuple(result[k] for k in ["assisted_units", "people_units", "value_units"])
            == best
        )


def test_all_documented_edge_cases_match_their_expected_behaviour():
    from pathlib import Path

    from fireline.priority_examples import edge_cases, evaluate_case, load_scenario

    base = load_scenario(
        Path(__file__).resolve().parents[1] / "fixtures/static_priority.json"
    )
    for case in edge_cases(base):
        outcome = evaluate_case(case)
        assert outcome["passed"], (
            case["case_id"],
            outcome["checks"],
            ids(outcome["response"]),
        )


def test_json_loader_rejects_duplicate_legs_and_wrong_schema():
    import json
    from pathlib import Path

    from fireline.priority_models import scenario_from_dict

    data = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures/static_priority.json"
        ).read_text()
    )
    data["travel"].append(dict(data["travel"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        scenario_from_dict(data)
    data["schema_version"] = "wrong"
    with pytest.raises(ValueError, match="schema_version"):
        scenario_from_dict(data)


def test_fractional_minute_equality_is_feasible_for_site_and_effect_deadlines():
    s = scene(
        [loc("A", 100, 10, 5, deadline=0.3)],
        [replace(act("A", "A", duration=0.2), deadline_min=0.3)],
        {("START", "A"): Travel(0.1)},
        horizon=0.3,
    )
    assert ids(plan_response(s)) == ["A"]
    assert plan_response(s)["objective"]["assisted_units"] == 5
    # Substantive excess still fails; tolerance handles arithmetic noise only.
    assert ids(plan_response(replace(s, horizon_min=0.299))) == []


def test_fractional_leg_cutoff_equality_is_feasible():
    s = scene(
        [loc("A"), loc("B")],
        [act("A", "A", duration=0.1), act("B", "B", requires=["A"])],
        {("START", "A"): Travel(0.1), ("A", "B"): Travel(0.1, 0.3)},
    )
    assert ids(plan_response(s)) == ["A", "B"]


@pytest.mark.parametrize("field", ["capabilities", "requires"])
def test_scalar_collection_input_is_rejected_before_tuple_conversion(field):
    import json
    from pathlib import Path

    from fireline.priority_models import scenario_from_dict

    data = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures/static_priority.json"
        ).read_text()
    )
    data["actions"][0][field] = "medical"
    with pytest.raises(ValueError, match="array|sequence|list|tuple"):
        scenario_from_dict(data)


def test_direct_dataclass_capabilities_must_be_collections():
    s = scene(
        [loc("A")], [act("A", "A", capabilities=["aid"])], capabilities=["medical"]
    )
    with pytest.raises(ValueError, match="array|sequence|list|tuple"):
        plan_response(
            replace(
                s,
                capabilities="medical",
                actions=(replace(s.actions[0], capabilities="aid"),),
            )
        )


def test_constrained_random_problems_match_independent_coverage_oracle():
    """Enumerate independently: shared targets, prerequisites, directed/closing legs, capabilities."""
    import random

    rng = random.Random(9531)
    for _ in range(30):
        locations = [
            loc(str(i), rng.randint(0, 100), 20, 5, deadline=rng.randint(3, 10))
            for i in range(4)
        ]
        actions = []
        for i in range(4):
            targets = rng.sample(range(4), rng.randint(1, 3))
            effects = [
                Benefit(
                    str(j),
                    rng.choice([0.25, 0.5, 1.0]),
                    rng.random() > 0.15,
                    "oracle fixture",
                )
                for j in targets
            ]
            actions.append(
                act(
                    str(i),
                    str(i),
                    effects,
                    requires=[str(i - 1)] if i and rng.random() < 0.4 else [],
                    duration=rng.randint(1, 3),
                    capabilities=["special"] if rng.random() < 0.3 else [],
                )
            )
        nodes = ["START"] + [a.asset_id for a in locations]
        legs = {
            (a, b): Travel(rng.randint(1, 3), rng.choice([None, 4, 8]))
            for a in nodes
            for b in nodes
            if a != b and rng.random() < 0.7
        }
        capabilities = ["special"] if rng.random() < 0.5 else []
        s = scene(locations, actions, legs, horizon=9, capabilities=capabilities)
        best = (0, 0, 0)
        for count in range(5):
            for order in itertools.permutations(range(4), count):
                time = 0
                current = "START"
                done = set()
                coverage = [0.0] * 4
                valid = True
                for i in order:
                    action = actions[i]
                    leg = legs.get((current, str(i)))
                    if (
                        not set(action.requires) <= done
                        or not set(action.capabilities) <= set(capabilities)
                        or leg is None
                    ):
                        valid = False
                        break
                    time += leg.minutes
                    if (
                        leg.available_until_min is not None
                        and time > leg.available_until_min
                    ):
                        valid = False
                        break
                    time += action.duration_min
                    if time > min(locations[i].deadline_min, action.deadline_min, 9):
                        valid = False
                        break
                    for effect in action.benefits:
                        j = int(effect.asset_id)
                        if effect.confirmed and time <= locations[j].deadline_min:
                            coverage[j] = max(coverage[j], effect.coverage)
                    current = str(i)
                    done.add(str(i))
                if valid:
                    objective = tuple(
                        sum(
                            coverage[i] * getattr(a, key)
                            for i, a in enumerate(locations)
                        )
                        for key in ["assisted", "people", "value"]
                    )
                    best = max(best, objective)
        result = plan_response(s)["objective"]
        assert (
            tuple(result[k] for k in ["assisted_units", "people_units", "value_units"])
            == best
        )


def test_static_cli_writes_valid_json_and_bad_inputs_fail_cleanly(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "result.json"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/static_priorities.py"),
            "--input",
            str(root / "fixtures/static_priority.json"),
            "--all-cases",
            "--output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert data["passed"] and len(data["cases"]) == 14
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version":"invalid"}')
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/static_priorities.py"),
            "--input",
            str(bad),
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "schema_version" in result.stderr and "Traceback" not in result.stderr


def test_search_counts_every_feasible_prefix_and_retains_first_action_alternatives():
    scenario = scene([loc("A"), loc("B"), loc("C")], [act("A", "A"), act("B", "B"), act("C", "C")])
    result = plan_response(scenario)
    assert result["states_evaluated"] == 16  # empty + 3 singles + 6 pairs + 6 triples
    assert ids(result) == ["A", "B", "C"]
    assert [(x["first_action"], x["sequence"]) for x in result["first_action_alternatives"]] == [
        ("A", ["A", "B", "C"]), ("B", ["B", "A", "C"]), ("C", ["C", "A", "B"]),
    ]
    assert plan_response(scenario) == result
