"""Allowlisted browser projection. Private call content never crosses this boundary."""
import math
import re

# Phone-like text is redacted even inside public provenance. ISO dates are preserved.
_PHONE = re.compile(r'(?<![\w-])(?:\+\d[\d ().-]{7,}\d|\d{9,15})(?![\w-])')
_SECRET = re.compile(r'(?i)(?:bearer\s+\S+|(?:api[_-]?key|token|password)\s*[:=]\s*\S+)')

# Unknown *values* remain null. Unknown fields are withheld until explicitly reviewed.
_FIELDS = set('''reported_needs_assistance dispatch_state transfer_verified review_status evidence_fields
elapsed_min snapshot_as_of required_capabilities deadline_at based_on_snapshot_id affected_by_snapshot_id suggested human_controlled capacity_status
schema_version scenario_id snapshot_id revision as_of input_mode epoch
computed_at data_status fire_observed_at fire_source fire_geometry fire_geometry_kind
asset_id name asset_type latitude longitude geometry area_m2 capacity estimated_occupancy
occupancy_basis value_score value_basis distance_to_fire_m intersects_fire burn_probability
arrival_p10_at arrival_p50_at forecast_horizon_at forecast_source fire_arrival_at
fire_arrival_basis evacuation_min evacuation_source needs_review review_reasons sources
municipality fields source observed_at available_at fetched_at notes type coordinates geometries
rank policy ordering slack_min latest_start_min time_to_impact_min status components
fire_arrival_min now_min buffer_min distance_m request_id queued_at updated_at
needs_assistance wants_human message_acknowledged acknowledged departure_confirmed
arrival_confirmed can_self_evacuate transport_available human_followup_required
human_followup_reasons transfer_status road_warning_acknowledged evidence_time_basis
identity_confirmed whole_household_confirmed confidence confidence_basis contradictory bad_audio
locations response remaining_capacity people assisted value deadline_min x_m y_m
location_id centre_id destination_id route_id team_id task_id id action action_id site_id
kind state capabilities available blocked assigned_to assigned_team_id created_at
started_at completed_at reason reasons event_id changed_asset_ids code
proposed approved confirmed departure_at arrival_at departure_min arrival_min duration_min
path routes evacuation_path firetruck_path provenance route_source route_status
steps actions schedule crews unassigned warnings unserved_people mode served_people
'''.split())
_CALL_FIELDS = set('''reported_needs_assistance dispatch_state transfer_verified review_status evidence_fields provenance input_mode
request_id asset_id snapshot_id status observed_at queued_at updated_at
source needs_assistance wants_human message_acknowledged acknowledged departure_confirmed
arrival_confirmed can_self_evacuate transport_available human_followup_required
human_followup_reasons transfer_status road_warning_acknowledged evidence_time_basis
identity_confirmed whole_household_confirmed contradictory bad_audio'''.split())


def _clean(value):
    if isinstance(value, str):
        return _SECRET.sub('[redacted]', _PHONE.sub('[redacted contact]', value))
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if key in _FIELDS}
    return None


def public_state(state):
    """Project a full coordination envelope without mutating the store's objects."""
    if (not isinstance(state, dict) or state.get('schema_version') != 'coordination-state-1'
            or type(state.get('revision')) is not int or state['revision'] < 0):
        raise ValueError('invalid coordination state')
    result = _clean(state)
    for key in ('assets', 'teams', 'tasks', 'events'):
        result[key] = _clean(state.get(key, []))
    result['contacts'] = {
        key: _clean(state.get('contacts', {}).get(key, [])) for key in ('ranked', 'review')
    }
    result['calls'] = [
        _clean({k: v for k, v in call.items() if k in _CALL_FIELDS})
        for call in state.get('calls', [])
    ]
    plan = state.get('plan') or {}
    result['plan'] = _clean(plan)
    result['plan'].setdefault('locations', [])
    result['plan'].setdefault('response', None)
    result['plan']['remaining_capacity'] = {
        _clean(key): _clean(value) if value is None or type(value) in (int, float) else None
        for key, value in plan.get('remaining_capacity', {}).items()
    }
    # Exception strings and provider errors can include credentials or private content.
    result['errors'] = [{'code': 'coordination_error'} for _ in state.get('errors', [])]
    return result
