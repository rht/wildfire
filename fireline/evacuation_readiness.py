"""Static household readiness proposals from sourced interviews and reception routes.

No call is placed and no evacuation is reported as completed by this module.
Confidence is an input review signal, never a probability of a safe evacuation.
"""

from dataclasses import dataclass

from .contact_priority import ContactPolicy, rank_contacts
from .priority_models import number, unique_ids, validate_scenario
from .response_priority import plan_response


@dataclass(frozen=True)
class CallAssessment:
    asset_id: str
    call_id: str
    status: str
    observed_min: float
    source: str
    identity_confirmed: bool | None = None
    whole_household_confirmed: bool | None = None
    can_self_evacuate: bool | None = None
    transport_available: bool | None = None
    wants_human: bool | None = None
    confidence: float | None = None
    evidence: str = ""
    contradictory: bool = False


@dataclass(frozen=True)
class ReceptionCentre:
    centre_id: str
    name: str
    remaining_places: int
    approved: bool
    available_until_min: float
    source: str


@dataclass(frozen=True)
class EvacuationRoute:
    asset_id: str
    centre_id: str
    travel_min: float
    evacuation_min: float  # Total: notification/mobilisation + preparation + travel.
    confirmed: bool
    available_until_min: float
    source: str


@dataclass(frozen=True)
class ReadinessPolicy:
    confidence_threshold: float = 0.85
    max_assessment_age_min: float = 15
    version: str = "household-readiness-prototype-v1"


def _boolean(value, name, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean" + (" or null" if nullable else ""))


def _text(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")


def _validate(scenario, assessments, centres, routes, policy):
    validate_scenario(scenario)
    ids = {a.asset_id for a in scenario.locations}
    number(policy.confidence_threshold, "confidence_threshold")
    if policy.confidence_threshold > 1:
        raise ValueError("confidence_threshold must be <= 1")
    number(policy.max_assessment_age_min, "max_assessment_age_min")
    unique_ids(assessments, "asset_id")
    unique_ids(assessments, "call_id")
    centre_ids = unique_ids(centres, "centre_id")
    for call in assessments:
        if call.asset_id not in ids:
            raise ValueError("unknown assessment asset_id")
        if call.status not in ("completed", "no_answer", "failed", "declined"):
            raise ValueError("invalid call status")
        number(call.observed_min, "observed_min")
        if call.confidence is not None:
            number(call.confidence, "confidence")
            if call.confidence > 1:
                raise ValueError("confidence must be <= 1")
        for key in (
            "identity_confirmed",
            "whole_household_confirmed",
            "can_self_evacuate",
            "transport_available",
            "wants_human",
        ):
            _boolean(getattr(call, key), key, nullable=True)
        _boolean(call.contradictory, "contradictory")
        _text(call.source, "source")
        _text(call.evidence, "evidence")
    for centre in centres:
        if type(centre.remaining_places) is not int or centre.remaining_places < 0:
            raise ValueError("remaining_places must be a nonnegative integer")
        _boolean(centre.approved, "approved")
        number(centre.available_until_min, "available_until_min")
        _text(centre.source, "source")
        _text(centre.name, "name")
    seen = set()
    for route in routes:
        key = (route.asset_id, route.centre_id)
        if key in seen:
            raise ValueError("duplicate evacuation route")
        seen.add(key)
        if route.asset_id not in ids or route.centre_id not in centre_ids:
            raise ValueError("unknown route asset or centre")
        for field in ("travel_min", "evacuation_min", "available_until_min"):
            number(getattr(route, field), field)
        if route.evacuation_min < route.travel_min:
            raise ValueError("total evacuation_min must include travel_min")
        _boolean(route.confirmed, "confirmed")
        _text(route.source, "source")


def _review_reasons(call, asset, now, policy):
    if call is None:
        return ["not_contacted"]
    reasons = []
    if call.status != "completed":
        reasons.append(call.status)
    if call.observed_min > now:
        reasons.append("future_assessment")
    elif now - call.observed_min > policy.max_assessment_age_min:
        reasons.append("stale_assessment")
    if call.confidence is None:
        reasons.append("unknown_confidence")
    elif call.confidence < policy.confidence_threshold:
        reasons.append("low_confidence")
    if not call.source.strip() or not call.evidence.strip():
        reasons.append("missing_evidence")
    if call.identity_confirmed is not True:
        reasons.append("identity_unconfirmed")
    if call.whole_household_confirmed is not True:
        reasons.append("household_unconfirmed")
    if any(
        getattr(call, f) is None
        for f in ("can_self_evacuate", "transport_available", "wants_human")
    ):
        reasons.append("incomplete_answers")
    if call.wants_human is True:
        reasons.append("human_requested")
    if call.contradictory:
        reasons.append("contradictory_answers")
    if call.can_self_evacuate is True and asset.assisted and asset.assisted > 0:
        reasons.append("conflicts_with_assistance_record")
    return reasons


def _destinations(asset, contact, centres, routes, capacity, policy):
    eligible, rejected = [], []
    for route in sorted(routes, key=lambda r: r.centre_id):
        centre = centres[route.centre_id]
        reasons = []
        if not centre.approved or not centre.source.strip():
            reasons.append("reception_not_approved")
        if not route.confirmed or not route.source.strip():
            reasons.append("route_unconfirmed")
        if asset.people is None or asset.people <= 0:
            reasons.append("household_size_unknown_or_zero")
        elif capacity[centre.centre_id] < asset.people:
            reasons.append("insufficient_capacity")
        if contact["status"] != "window_open":
            reasons.append("contact_window_unavailable")
        # Keep the larger supplied duration; changing reception cannot silently shorten
        # the upstream estimate. Both durations already include movement (no double count).
        duration = max(route.evacuation_min, asset.evacuation_min or 0)
        finish = policy.now_min + duration
        deadline = min(
            route.available_until_min,
            centre.available_until_min,
            asset.fire_arrival_min if asset.fire_arrival_min is not None else 0,
        )
        if finish + policy.buffer_min >= deadline - 1e-9:
            reasons.append("evacuation_window_exhausted")
        if reasons:
            rejected.append({"centre_id": centre.centre_id, "reasons": reasons})
        else:
            eligible.append(
                (
                    route.travel_min,
                    centre.centre_id,
                    duration,
                    deadline - finish - policy.buffer_min,
                    route,
                )
            )
    return sorted(eligible, key=lambda r: (r[0], r[1])), rejected


def coordinate_evacuation(
    scenario,
    assessments,
    centres,
    routes,
    *,
    contact_policy=None,
    readiness_policy=None,
):
    """Return one proposal per asset, contact queue and the original crew sequence.

    A pure static allocation: capacities are reserved only within this result; persist
    confirmed reservations before another run. It does not remove crew actions or claim
    arrival. Inputs must be the latest assessment for each location, not a call history.
    """
    contact_policy = contact_policy or ContactPolicy()
    readiness_policy = readiness_policy or ReadinessPolicy()
    _validate(scenario, assessments, centres, routes, readiness_policy)
    contacts = rank_contacts(scenario.locations, contact_policy)
    response = plan_response(scenario) if contact_policy.now_min == 0 else None
    by_id = {a.asset_id: a for a in scenario.locations}
    calls = {c.asset_id: c for c in assessments}
    destinations = {c.centre_id: c for c in centres}
    capacity = {c.centre_id: c.remaining_places for c in centres}
    rows = []
    for contact in contacts["ranked"] + contacts["review"]:
        aid = contact["asset_id"]
        asset, call = by_id[aid], calls.get(aid)
        reasons = _review_reasons(call, asset, contact_policy.now_min, readiness_policy)
        row = {
            "asset_id": aid,
            "name": asset.name,
            "contact_rank": contact["rank"],
            "mode": "undetermined",
            "self_evacuation_ability": "unknown",
            "destination_id": None,
            "destination_name": None,
            "evacuation_status": "not_confirmed",
            "human_followup": True,
            "tasks": [],
            "reasons": reasons,
            "destination_rejections": [],
            "call_id": call.call_id if call else None,
            "call_source": call.source if call else None,
            "call_evidence": call.evidence if call else None,
            "call_confidence": call.confidence if call else None,
            "route_source": None,
            "reception_source": None,
            "evacuation_min": None,
            "remaining_window_min": None,
        }
        if reasons:
            row["tasks"] = ["contact_household" if call is None else "human_callback"]
        elif call.can_self_evacuate is False or call.transport_available is False:
            row["mode"] = "assisted_evacuation"
            row["self_evacuation_ability"] = "assistance_required"
            row["tasks"] = ["arrange_assistance", "confirm_arrival"]
        else:
            row["self_evacuation_ability"] = "confirmed"
            options, rejections = _destinations(
                asset,
                contact,
                destinations,
                [r for r in routes if r.asset_id == aid],
                capacity,
                contact_policy,
            )
            row["destination_rejections"] = rejections
            if not options:
                row["reasons"].append("no_feasible_destination")
                row["tasks"] = ["human_callback", "arrange_reception_or_assistance"]
            else:
                _, cid, duration, window, route = options[0]
                centre = destinations[cid]
                capacity[cid] -= asset.people
                row.update(
                    mode="self_evacuate",
                    destination_id=cid,
                    destination_name=centre.name,
                    human_followup=False,
                    tasks=[
                        "confirm_instructions",
                        "confirm_departure",
                        "confirm_arrival",
                    ],
                    route_source=route.source,
                    reception_source=centre.source,
                    evacuation_min=duration,
                    remaining_window_min=window,
                )
        rows.append(row)
    return {
        "contacts": contacts,
        "response": response,
        "locations": rows,
        "remaining_capacity": capacity,
        "readiness_policy": readiness_policy.version,
        "confidence_threshold": readiness_policy.confidence_threshold,
        "max_assessment_age_min": readiness_policy.max_assessment_age_min,
        "response_replanning_required": contact_policy.now_min != 0,
        "input_mode": "static",
        "dispatch": False,
    }
