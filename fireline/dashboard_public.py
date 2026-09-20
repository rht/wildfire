"""Allowlisted browser projection. Private call content never crosses this boundary."""
import math
import re

# Phone-like text is redacted even inside public provenance. ISO dates are preserved.
_PHONE = re.compile(r'(?<![\w-])(?:\+?\d(?:[\d ().-]*\d){8,14}|\(\d{2,4}\)[\d .-]{6,}\d)(?![\w-])')
_SECRET = re.compile(r'(?i)(?:bearer\s+\S+|(?:api[_-]?key|token|password)\s*[:=]\s*\S+)')

# Unknown *values* remain null. Unknown fields are withheld until explicitly reviewed.
_FIELDS = set('''queue_state replacement_value_eur replacement_value_basis expected_loss_eur_low
expected_loss_eur_mid expected_loss_eur_high people_exposed people_at_risk_p50 people_at_risk_p10 current_location peopleClusters system_events review unserved blocked_actions assisted_units people_units value_units
allocations allocation_revision allocation_tasks destination_candidates
allocation_id group_id previous_allocation_id incident_id last_at safety
instruction_allowed assistance transport_confirmed reception_confirmed pickup_min
teams tasks locked remaining_transport_capacity path_lonlat path_nodes
depart_min travel_min start_min finish_min prerequisites action_version readiness transport_people
from_node to_node effects coverage confirmed assumptions optimal method dispatch
contact_rank self_evacuation_ability reported_can_self_evacuate reported_transport_available
destination_name evacuation_status human_followup destination_rejections call_source call_confidence
assistance_review_required
route_road_ids road_warnings road_warning_version current_road_warning_acknowledged reception_source
remaining_window_min assessment_request_ids readiness_policy confidence_threshold max_assessment_age_min
response_replanning_required evidence_verification evidence_observed_at
reported_needs_assistance dispatch_state transfer_verified review_status evidence_fields
elapsed_min snapshot_as_of required_capabilities deadline_at based_on_snapshot_id affected_by_snapshot_id suggested human_controlled capacity_status
schema_version scenario_id snapshot_id revision as_of input_mode epoch
computed_at data_status fire_observed_at fire_source fire_geometry fire_geometry_kind
asset_id name asset_type latitude longitude geometry area_m2 capacity estimated_occupancy
occupancy_basis value_score value_basis distance_to_fire_m intersects_fire burn_probability
replacement_value_eur replacement_value_basis expected_loss_eur_low expected_loss_eur_mid
expected_loss_eur_high people_exposed people_at_risk_p10 people_at_risk_p50
criticality_tier criticality_factors criticality_basis
custom_value_eur_low custom_value_eur_mid custom_value_eur_high custom_value_method
custom_value_components custom_value_basis label amount_eur
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
_CALL_FIELDS = set('''queue_state reported_needs_assistance dispatch_state transfer_verified review_status evidence_fields provenance input_mode
request_id asset_id snapshot_id status observed_at queued_at updated_at
source needs_assistance wants_human message_acknowledged acknowledged departure_confirmed
arrival_confirmed can_self_evacuate transport_available human_followup_required
human_followup_reasons transfer_status road_warning_acknowledged evidence_time_basis
identity_confirmed whole_household_confirmed contradictory bad_audio'''.split())


def _clean(value, field=None):
    if isinstance(value, str):
        if field == 'id' or (field and (field.endswith('_id') or field.endswith('_ids'))):
            return value
        return _SECRET.sub('[redacted]', _PHONE.sub('[redacted contact]', value))
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_clean(item, field) for item in value]
    if isinstance(value, dict):
        return {key: _clean(item, key) for key, item in value.items() if key in _FIELDS}
    return None


def public_state(state):
    """Project a full coordination envelope without mutating the store's objects."""
    if (not isinstance(state, dict) or state.get('schema_version') != 'coordination-state-1'
            or isinstance(state.get('revision'), bool) or not isinstance(state.get('revision'), int)
            or state['revision'] < 0):
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
        key: _clean(value) if value is None or type(value) in (int, float) else None
        for key, value in plan.get('remaining_capacity', {}).items()
    }
    response = plan.get('response')
    if isinstance(response, dict):
        public_response = result['plan']['response']
        asset_ids = {asset['asset_id'] for asset in state.get('assets', [])}
        if 'coverage' in response:
            public_response['coverage'] = {
                key: _clean(value) for key, value in response['coverage'].items()
                if key in asset_ids and (value is None or type(value) in (int, float))
            }
        if 'unserved' in response:
            public_response['unserved'] = {
                key: _clean(value) for key, value in response['unserved'].items()
                if key in asset_ids and isinstance(value, (str, list))
            }
        if 'blocked_actions' in response:
            # Reasons are public planner codes; no free-form action payload is accepted.
            public_response['blocked_actions'] = {
                key: [_clean(reason) for reason in reasons if isinstance(reason, str)]
                for key, reasons in response['blocked_actions'].items()
                if isinstance(reasons, list)
            }
    # Exception strings and provider errors can include credentials or private content.
    result['errors'] = [{'code': 'coordination_error'} for _ in state.get('errors', [])]
    return result
