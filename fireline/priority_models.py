"""Static decision-support inputs; no spread, road or suppression effects are inferred."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    asset_id: str
    name: str
    x_m: float | None
    y_m: float | None
    distance_m: float | None
    people: int | None
    assisted: int | None
    value: float | None
    deadline_min: float | None
    fire_arrival_min: float | None = None
    evacuation_min: float | None = None
    forecast_source: str | None = None
    evacuation_source: str | None = None


@dataclass(frozen=True)
class Benefit:
    asset_id: str
    coverage: float
    confirmed: bool
    evidence: str


@dataclass(frozen=True)
class Action:
    action_id: str
    site_id: str
    duration_min: float
    deadline_min: float | None
    benefits: tuple[Benefit, ...]
    requires: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class Travel:
    minutes: float
    available_until_min: float | None = None


@dataclass(frozen=True)
class StaticScenario:
    scenario_id: str
    locations: tuple[Location, ...]
    actions: tuple[Action, ...]
    travel: dict[tuple[str, str], Travel]
    start_id: str = "START"
    horizon_min: float = 20
    capabilities: tuple[str, ...] = ()
    buffer_min: float = 0


def number(value, label, *, minimum=0, nullable=False):
    if value is None and nullable:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{label} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")


def unique_ids(items, field):
    ids = [getattr(item, field) for item in items]
    if any(not isinstance(key, str) or not key.strip() for key in ids):
        raise ValueError(f"{field} must be a nonempty string")
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate {field}")
    return set(ids)


def string_sequence(value, label):
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list or tuple of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must contain nonempty strings")
    return tuple(value)


def validate_locations(locations):
    unique_ids(locations, "asset_id")
    for a in locations:
        if not isinstance(a.name, str) or not a.name.strip():
            raise ValueError(f"{a.asset_id}: name is required")
        for field in ("x_m", "y_m"):
            number(getattr(a, field), field, minimum=None, nullable=True)
        for field in (
            "distance_m",
            "value",
            "deadline_min",
            "fire_arrival_min",
            "evacuation_min",
        ):
            number(getattr(a, field), field, nullable=True)
        for field in ("forecast_source", "evacuation_source"):
            if getattr(a, field) is not None and not isinstance(getattr(a, field), str):
                raise ValueError(f"{field} must be a string or null")
        for field in ("people", "assisted"):
            value = getattr(a, field)
            number(value, field, nullable=True)
            if value is not None and not isinstance(value, int):
                raise ValueError(f"{field} must be an integer")
        if a.people is not None and a.assisted is not None and a.assisted > a.people:
            raise ValueError("assisted people cannot exceed total people")


def validate_scenario(s):
    validate_locations(s.locations)
    aids = unique_ids(s.actions, "action_id")
    if len(aids) > 8:
        raise ValueError("exact static planner supports at most eight (8) actions")
    lids = {a.asset_id for a in s.locations}
    if not isinstance(s.start_id, str) or not s.start_id:
        raise ValueError("start_id must be nonempty")
    number(s.horizon_min, "horizon_min")
    number(s.buffer_min, "buffer_min")
    for tags in [s.capabilities, *(a.capabilities for a in s.actions)]:
        string_sequence(tags, "capabilities")
    for a in s.actions:
        string_sequence(a.requires, "requires")
        if a.site_id not in lids:
            raise ValueError(f"{a.action_id}: unknown site {a.site_id}")
        number(a.duration_min, "duration_min")
        if a.duration_min <= 0:
            raise ValueError("duration_min must be positive")
        number(a.deadline_min, "action deadline", nullable=True)
        if not set(a.requires) <= aids:
            raise ValueError(f"{a.action_id}: unknown prerequisite")
        unique_ids(a.benefits, "asset_id")
        for b in a.benefits:
            if b.asset_id not in lids:
                raise ValueError(f"{a.action_id}: unknown benefit target {b.asset_id}")
            number(b.coverage, "coverage")
            if b.coverage > 1:
                raise ValueError("coverage must be <= 1")
            if not isinstance(b.confirmed, bool):
                raise ValueError("confirmed must be boolean")
            if not isinstance(b.evidence, str) or (
                b.confirmed and not b.evidence.strip()
            ):
                raise ValueError(
                    "confirmed benefit needs evidence or a labelled assumption"
                )
    actions = {a.action_id: a for a in s.actions}
    visited, active = set(), set()

    def visit(key):
        if key in active:
            raise ValueError("prerequisite cycle")
        if key in visited:
            return
        active.add(key)
        for dep in actions[key].requires:
            visit(dep)
        active.remove(key)
        visited.add(key)

    for key in actions:
        visit(key)
    for endpoints, leg in s.travel.items():
        if len(endpoints) != 2 or not set(endpoints) <= lids | {s.start_id}:
            raise ValueError("travel leg references unknown site")
        number(leg.minutes, "travel minutes")
        number(leg.available_until_min, "travel availability", nullable=True)


def scenario_from_dict(data):
    if data.get("schema_version") != "static-priority-1":
        raise ValueError("expected schema_version static-priority-1")
    scenario = StaticScenario(
        scenario_id=data["scenario_id"],
        locations=tuple(Location(**a) for a in data["locations"]),
        actions=tuple(
            Action(
                **{
                    **a,
                    "benefits": tuple(Benefit(**b) for b in a["benefits"]),
                    "requires": string_sequence(a.get("requires", []), "requires"),
                    "capabilities": string_sequence(
                        a.get("capabilities", []), "capabilities"
                    ),
                }
            )
            for a in data["actions"]
        ),
        travel={
            (leg["from"], leg["to"]): Travel(
                leg["minutes"], leg.get("available_until_min")
            )
            for leg in data["travel"]
        },
        start_id=data.get("start_id", "START"),
        horizon_min=data["horizon_min"],
        capabilities=string_sequence(data.get("capabilities", []), "capabilities"),
        buffer_min=data.get("buffer_min", 0),
    )
    if len(scenario.travel) != len(data["travel"]):
        raise ValueError("duplicate directed travel leg")
    validate_scenario(scenario)
    return scenario
