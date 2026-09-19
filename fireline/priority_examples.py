"""Reproducible variants used by the CLI, tests and generated report."""

import json
from dataclasses import replace
from pathlib import Path

from .contact_priority import rank_contacts
from .priority_models import Benefit, Travel, scenario_from_dict
from .response_priority import greedy_response, plan_response


def load_scenario(path):
    return scenario_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def edge_cases(base):
    """Each case changes an explicit assumption; geometry alone adds no dependency."""
    cases = []

    def add(key, title, scenario, expected):
        cases.append(
            {
                "case_id": key,
                "title": title,
                "scenario": replace(scenario, scenario_id=key),
                "expected": expected,
            }
        )

    add(
        "access",
        "C unlocks timely assistance at A",
        base,
        {"first": "act_C", "assisted": 60},
    )
    actions = tuple(
        replace(
            a,
            benefits=(
                Benefit("C", 1, True, "synthetic own effect"),
                Benefit("A", 1, True, "synthetic protective effect"),
            ),
        )
        if a.action_id == "act_C"
        else replace(a, requires=())
        for a in base.actions
    )
    add(
        "protective_effect",
        "Declared C-to-A protection, no implicit propagation",
        replace(base, actions=actions),
        {"first": "act_C", "assisted": 60},
    )
    actions = tuple(replace(a, requires=()) for a in base.actions)
    travel = {**base.travel, ("START", "A"): Travel(1)}
    add(
        "direct_access",
        "A directly accessible: bypass unnecessary C action",
        replace(base, actions=actions, travel=travel),
        {"first": "act_A", "assisted": 60},
    )
    locations = tuple(
        replace(a, people=0, assisted=0)
        if a.asset_id == "A"
        else replace(a, deadline_min=3)
        if a.asset_id == "B"
        else a
        for a in base.locations
    )
    add(
        "urgent_b",
        "No people at A and B has a tight deadline",
        replace(base, locations=locations),
        {"first": "act_B", "assisted": 0},
    )
    travel = {leg: t for leg, t in base.travel.items() if leg[1] != "C"}
    add(
        "blocked_c",
        "C unreachable; do not invent a straight-line shortcut",
        replace(base, travel=travel),
        {"first": "act_B", "assisted": 0},
    )
    add(
        "missing_capability",
        "Crew lacks assisted-evacuation capability",
        replace(base, capabilities=()),
        {"first": "act_B", "assisted": 0},
    )
    locations = tuple(
        replace(a, deadline_min=5) if a.asset_id == "A" else a for a in base.locations
    )
    add(
        "missed_window",
        "A deadline expires before the C-to-A sequence completes",
        replace(base, locations=locations),
        {"first": "act_B", "assisted": 0},
    )
    locations = tuple(
        replace(a, people=None, assisted=None) if a.asset_id == "A" else a
        for a in base.locations
    )
    add(
        "unknown_people",
        "Unknown occupancy is review, not zero or credited benefit",
        replace(base, locations=locations),
        {"assisted": 0, "review": True},
    )
    actions = tuple(
        replace(
            a,
            benefits=(
                Benefit("C", 1, True, "fixture"),
                Benefit("A", 1, False, "unverified proximity hypothesis"),
            ),
        )
        if a.action_id == "act_C"
        else a
        for a in base.actions
        if a.action_id != "act_A"
    )
    add(
        "unconfirmed_effect",
        "Nearby A receives no credit from an unconfirmed effect",
        replace(base, actions=actions),
        {"assisted": 0, "review": True},
    )
    actions = tuple(
        replace(
            a,
            benefits=(
                Benefit(
                    "A",
                    0.6 if a.site_id == "B" else 0.8,
                    True,
                    "synthetic overlapping protection",
                ),
            ),
        )
        for a in base.actions
        if a.action_id != "act_A"
    )
    add(
        "overlap",
        "Overlapping effects use maximum coverage, not addition",
        replace(base, actions=actions),
        {"first": "act_C", "assisted": 48},
    )
    actions = tuple(
        replace(
            a, benefits=(Benefit("C", 0, True, "access only; no immediate benefit"),)
        )
        if a.action_id == "act_C"
        else a
        for a in base.actions
    )
    add(
        "zero_gain_prerequisite",
        "Zero-benefit C still unlocks A",
        replace(base, actions=actions),
        {"first": "act_C", "assisted": 60},
    )
    travel = {**base.travel, ("START", "C"): Travel(1, 0.5), ("B", "C"): Travel(3, 0.5)}
    add(
        "closing_access",
        "C access closes before traversal completes",
        replace(base, travel=travel),
        {"first": "act_B", "assisted": 0},
    )
    add(
        "positive_buffer",
        "Three-minute buffer makes the A window infeasible",
        replace(base, buffer_min=3),
        {"first": "act_B", "assisted": 0},
    )
    locations = tuple(replace(a, deadline_min=0) for a in base.locations)
    add(
        "no_feasible_action",
        "All work-site deadlines expired",
        replace(base, locations=locations),
        {"first": None, "assisted": 0},
    )
    return cases


def evaluate_case(case):
    s = case["scenario"]
    result = {
        "case_id": case["case_id"],
        "title": case["title"],
        "contacts": rank_contacts(s.locations),
        "response": plan_response(s),
        "greedy": greedy_response(s),
    }
    actual = result["response"]
    expected = case["expected"]
    checks = {}
    if "first" in expected:
        checks["first_action"] = (
            actual["steps"][0]["action_id"] if actual["steps"] else None
        ) == expected["first"]
    checks["assisted_units"] = (
        abs(actual["objective"]["assisted_units"] - expected["assisted"]) < 1e-9
    )
    if expected.get("review"):
        checks["review_visible"] = bool(actual["review"])
    result["checks"] = checks
    result["passed"] = all(checks.values())
    return result
