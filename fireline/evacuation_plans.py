"""Explicit destination evidence; no discovery type implies reception approval.

Elapsed minutes use PlanningContext.epoch. Capacity is a budget inclusive of this
ledger's allocations, not an independently refreshed count of vacant beds.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from .evacuation_readiness import EvacuationRoute, ReceptionCentre
from .priority_models import number, string_sequence, unique_ids


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')


def utc(value):
    text(value, 'UTC timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('timestamp must be UTC')
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class PlanningContext:
    scenario_id: str
    incident_id: str
    snapshot_id: str
    epoch: str
    as_of: str
    forecast_observed_min: float | None
    max_age_min: float = 15
    buffer_min: float = 0

    @property
    def now_min(self):
        return (utc(self.as_of) - utc(self.epoch)).total_seconds() / 60


@dataclass(frozen=True)
class AnalystApproval:
    approval_id: str
    analyst_id: str
    incident_id: str
    centre_id: str
    evidence: str
    group_id: str | None = None


@dataclass(frozen=True)
class CandidateFacility:
    centre: ReceptionCentre
    kind: str = 'unknown'
    approval: AnalystApproval | None = None
    safe_until_min: float | None = None
    threat_source: str | None = None
    capabilities: tuple[str, ...] | None = None
    closed: bool | None = False


@dataclass(frozen=True)
class EvacuationGroup:
    group_id: str
    asset_id: str
    people: int | None
    assisted: bool | None
    needs: tuple[str, ...] | None
    fire_arrival_min: float | None
    evacuation_min: float | None
    forecast_source: str | None


@dataclass(frozen=True)
class RoadEvidence:
    road_id: str
    state: str  # open, blocked, unknown
    observed_min: float | None
    available_until_min: float | None
    source: str | None


def candidate_from_record(record):
    """Adapt an explicit candidate, or a raw discovery record with unknown evidence.

    Raw discovery accepts asset_id/name/kind only. Enrichment must use the explicit
    nested centre contract; discovery fields cannot authorize a destination.
    """
    if 'centre' not in record:
        return CandidateFacility(ReceptionCentre(record['asset_id'], record.get('name', record['asset_id']),
                                                  None, False, 0, ''), record.get('kind', 'unknown'),
                                 closed=None)
    data = dict(record)
    data['centre'] = ReceptionCentre(**data['centre'])
    if data.get('approval') is not None:
        data['approval'] = AnalystApproval(**data['approval'])
    if data.get('capabilities') is not None:
        data['capabilities'] = tuple(data['capabilities'])
    return CandidateFacility(**data)


def approval_matches(approval, context, centre_id, group_id=None):
    return (approval is not None and approval.incident_id == context.incident_id
            and approval.centre_id == centre_id and approval.group_id == group_id
            and all(isinstance(v, str) and v.strip() for v in
                    (approval.approval_id, approval.analyst_id, approval.evidence)))


def validate_inputs(context, candidates, groups, routes, roads):
    for name in ('scenario_id', 'incident_id', 'snapshot_id'):
        text(getattr(context, name), name)
    number(context.now_min, 'now_min')
    for name in ('max_age_min', 'buffer_min'):
        number(getattr(context, name), name)
    number(context.forecast_observed_min, 'forecast_observed_min', nullable=True)
    unique_ids(groups, 'group_id')
    unique_ids(roads, 'road_id')
    centres = unique_ids([c.centre for c in candidates], 'centre_id')
    for group in groups:
        text(group.asset_id, 'asset_id')
        if group.people is not None and (not isinstance(group.people, int) or isinstance(group.people, bool) or group.people <= 0):
            raise ValueError('people must be a positive integer or null')
        if group.assisted is not None and not isinstance(group.assisted, bool):
            raise ValueError('assisted must be boolean or null')
        if group.needs is not None:
            string_sequence(group.needs, 'needs')
        for field in ('fire_arrival_min', 'evacuation_min'):
            number(getattr(group, field), field, nullable=True)
    for candidate in candidates:
        centre = candidate.centre
        if centre.remaining_places is not None and (not isinstance(centre.remaining_places, int)
                or isinstance(centre.remaining_places, bool) or centre.remaining_places < 0):
            raise ValueError('capacity must be a nonnegative integer or null')
        if not isinstance(centre.approved, bool):
            raise ValueError('approved must be boolean')
        if candidate.closed is not None and not isinstance(candidate.closed, bool):
            raise ValueError('closed must be boolean or null')
        number(centre.available_until_min, 'available_until_min')
        number(candidate.safe_until_min, 'safe_until_min', nullable=True)
        if candidate.capabilities is not None:
            string_sequence(candidate.capabilities, 'capabilities')
    seen = set()
    for route in routes:
        key = (route.asset_id, route.centre_id)
        if key in seen or route.centre_id not in centres:
            raise ValueError('duplicate route or unknown centre')
        seen.add(key)
        text(route.asset_id, 'route asset_id')
        string_sequence(route.road_ids, 'road_ids')
        if not isinstance(route.confirmed, bool):
            raise ValueError('confirmed must be boolean')
        for field in ('travel_min', 'evacuation_min', 'available_until_min'):
            number(getattr(route, field), field)
        if route.evacuation_min < route.travel_min:
            raise ValueError('evacuation duration must include travel')
    for road in roads:
        if road.state not in ('open', 'blocked', 'unknown'):
            raise ValueError('invalid road state')
        for field in ('observed_min', 'available_until_min'):
            number(getattr(road, field), field, nullable=True)


def _fresh(observed, context):
    return observed is not None and 0 <= context.now_min - observed <= context.max_age_min


def _sourced(value):
    return isinstance(value, str) and bool(value.strip())


def evaluate_candidates(group, candidates, routes, roads, context, occupied=None):
    """Return deterministic eligible/unsafe/review rows with evidence, never allocate."""
    validate_inputs(context, candidates, [group], routes, roads)
    occupied = occupied or {}
    for count in occupied.values():
        number(count, 'occupied capacity')
    by_road = {road.road_id: road for road in roads}
    by_route = {r.centre_id: r for r in routes if r.asset_id == group.asset_id}
    rows = []
    for candidate in candidates:
        centre = candidate.centre
        route = by_route.get(centre.centre_id)
        reasons, danger = [], []
        if candidate.closed is True:
            danger.append('centre_closed')
        elif candidate.closed is None:
            reasons.append('centre_status_unknown')
        if not centre.approved or not _sourced(centre.source) or not approval_matches(candidate.approval, context, centre.centre_id):
            reasons.append('reception_not_approved')
        if not _fresh(context.forecast_observed_min, context):
            reasons.append('stale_forecast')
        remaining = None if centre.remaining_places is None else centre.remaining_places - occupied.get(centre.centre_id, 0)
        if remaining is None:
            reasons.append('capacity_unknown')
        if group.people is None:
            reasons.append('group_size_unknown')
        elif remaining is not None and remaining < group.people:
            reasons.append('insufficient_capacity')
        if group.assisted is None:
            reasons.append('assistance_unknown')
        if group.needs is None:
            reasons.append('group_needs_unknown')
        if candidate.capabilities is None:
            reasons.append('reception_needs_unknown')
        elif group.needs is not None and not set(group.needs) <= set(candidate.capabilities):
            reasons.append('reception_needs_unmet')
        if group.fire_arrival_min is None or not _sourced(group.forecast_source):
            reasons.append('origin_threat_unknown')
        if candidate.safe_until_min is None or not _sourced(candidate.threat_source):
            reasons.append('destination_threat_unknown')
        if group.evacuation_min is None:
            reasons.append('evacuation_duration_unknown')
        if route is None or route.confirmed is not True or not _sourced(route.source):
            reasons.append('route_unconfirmed')
        finish = None
        road_sources = []
        if route is not None:
            finish = context.now_min + max(route.evacuation_min, group.evacuation_min or 0) + context.buffer_min
            if not route.road_ids:
                reasons.append('route_roads_unknown')
            for road_id in route.road_ids:
                road = by_road.get(road_id)
                if road is None:
                    reasons.append('road_unknown')
                    continue
                if road.state == 'blocked':
                    danger.append('road_blocked')
                elif road.state != 'open' or not _sourced(road.source) or road.available_until_min is None:
                    reasons.append('road_unknown')
                if not _fresh(road.observed_min, context):
                    reasons.append('stale_road_evidence')
                if road.available_until_min is not None and finish >= road.available_until_min:
                    danger.append('road_window_exhausted')
                road_sources.append(road.source)
            for deadline, reason in ((group.fire_arrival_min, 'origin_window_exhausted'),
                                     (candidate.safe_until_min, 'destination_window_exhausted'),
                                     (centre.available_until_min, 'centre_window_exhausted'),
                                     (route.available_until_min, 'route_window_exhausted')):
                if deadline is not None and finish >= deadline:
                    danger.append(reason)
        rows.append({'centre_id': centre.centre_id, 'status': 'unsafe' if danger else 'review' if reasons else 'eligible',
                     'reasons': sorted(set(reasons + danger)), 'remaining_capacity': remaining,
                     'finish_min': finish, 'travel_min': route.travel_min if route else None,
                     'evidence': {'approval_id': candidate.approval.approval_id if candidate.approval else None,
                                  'threat_source': candidate.threat_source, 'origin_forecast_source': group.forecast_source,
                                  'forecast_observed_min': context.forecast_observed_min,
                                  'safe_until_min': candidate.safe_until_min,
                                  'capacity_source': centre.source, 'needs': list(group.needs) if group.needs is not None else None,
                                  'capabilities': list(candidate.capabilities) if candidate.capabilities is not None else None,
                                  'route_source': route.source if route else None, 'road_sources': road_sources,
                                  'road_ids': list(route.road_ids) if route else []}})
    return sorted(rows, key=lambda r: (r['status'] != 'eligible', r['travel_min'] if r['travel_min'] is not None else float('inf'), r['centre_id']))
