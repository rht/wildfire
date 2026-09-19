"""The additive wrapper exports approved state without modifying static readiness."""
from dataclasses import asdict, replace
import json
import os
import subprocess
import sys

from fireline.evacuation_plan_adapter import coordinate_approved_evacuation, build_approved_recommendation
from fireline.priority_models import StaticScenario, Location
from fireline.evacuation_readiness import CallAssessment
from tests.test_evacuation_allocations import setup_store, reserve, AT
from tests.test_evacuation_plans import inputs


def scenario():
    return StaticScenario('scenario', (Location('A', 'Building A', 0, 0, 1, 4, 0, 1, 50, 60, 5,
                                               'forecast', 'estimate'),), (), {})


def assessment():
    return CallAssessment('A', 'call-A', 'completed', 0, 'synthetic interview', True, True,
                          True, True, False, .95, 'Everyone can leave')


def test_wrapper_retains_approved_destination_and_distinguishes_unconfirmed_arrival(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    result = coordinate_approved_evacuation(scenario(), [assessment()], store, as_of=AT)
    row = result['locations'][0]
    assert row['destination_id'] == 'centre'
    assert row['allocations'][0]['allocation_id'] == a['allocation_id']
    assert row['evacuation_status'] == 'not_confirmed'
    assert result['remaining_capacity']['centre'] == 2
    assert result['dispatch'] is False
    assert row['human_followup'] is False


def test_wrapper_keeps_uncertain_interviews_in_human_review_despite_allocation(tmp_path):
    store = setup_store(tmp_path)
    reserve(store)
    row = coordinate_approved_evacuation(scenario(), [replace(assessment(), confidence=None)], store, as_of=AT)['locations'][0]
    assert row['human_followup'] is True
    assert row['mode'] == 'undetermined'
    assert 'unknown_confidence' in row['reasons']


def guidance():
    return dict(asset_id='A', centre_id='centre', snapshot_id='snapshot-1', confirmed=True,
                instructions='Follow the supplied inspected road-1 route to the hall.',
                road_ids=['road-1'], road_names=['Synthetic Road'], source='route-desk')


def test_call_briefing_adapter_requires_supplied_verified_route_instructions(tmp_path):
    store = setup_store(tmp_path)
    reserve(store)
    assert build_approved_recommendation(store, 'A', as_of=AT, snapshot_id='snapshot-1', route_guidance=None) is None
    result = build_approved_recommendation(store, 'A', as_of=AT, snapshot_id='snapshot-1', expected_people=4, route_guidance=guidance())
    assert result['approved'] is True
    assert result['destination']['name'] == 'Hall'
    assert result['route']['instructions'] == guidance()['instructions']
    assert result['route']['feasible'] is True
    for change in ({'road_ids': ['other']}, {'snapshot_id': 'old'}, {'asset_id': 'other'}, {'confirmed': False}):
        assert build_approved_recommendation(store, 'A', as_of=AT, snapshot_id='snapshot-1', expected_people=4, route_guidance=guidance() | change) is None


def test_cli_persists_reservation_and_replay_is_idempotent(tmp_path):
    context, centre, group, route, road = inputs()
    approval = dict(approval_id='alloc-approval', analyst_id='analyst', incident_id='incident-1',
                    centre_id='centre', evidence='synthetic reviewed', group_id='group-A')
    document = dict(context=asdict(context), candidates=[asdict(centre)], groups=[asdict(group)],
                    routes=[asdict(route)], roads=[asdict(road)],
                    commands=[dict(kind='reserve', command_id='reserve', group_id='group-A',
                                   centre_id='centre', approval=approval, as_of=AT, snapshot_id='snapshot-1')])
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(document))
    args = [sys.executable, 'scripts/evacuation_plans.py', '--input', str(path), '--database',
            str(tmp_path / 'plans.sqlite'), '--as-of', AT]
    for _ in range(2):
        result = subprocess.run(args, text=True, capture_output=True, env=dict(os.environ, PYTHONPATH='.'))
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload['remaining_capacity'] == {'centre': 2}
        assert len(payload['locations']) == 1


def test_discovery_candidates_remain_unapproved_and_keep_stable_ids():
    from fireline.evacuation_plan_adapter import candidates_from_discovery
    candidates = candidates_from_discovery({'schema_version': 'discovery-1',
        'assets_in': [{'asset_id': 'hospital-1', 'name': 'Hospital', 'kind': 'hospital'},
                      {'asset_id': 'building-1', 'name': 'Building'}],
        'classifications': {'hospital-1': 'destination_candidate', 'building-1': 'threatened_search'}})
    assert [c.centre.centre_id for c in candidates] == ['hospital-1']
    assert candidates[0].approval is None
    assert candidates[0].centre.approved is False


def test_partial_building_allocation_does_not_mark_whole_building_ready(tmp_path):
    store = setup_store(tmp_path)
    reserve(store)
    large_building = replace(scenario(), locations=(replace(scenario().locations[0], people=8),))
    row = coordinate_approved_evacuation(large_building, [assessment()], store, as_of=AT)['locations'][0]
    assert row['human_followup'] is True
    assert 'group_coverage_incomplete' in row['reasons']


def test_new_assistance_report_cannot_be_overridden_by_old_unassisted_allocation(tmp_path):
    store = setup_store(tmp_path)
    reserve(store)
    call = replace(assessment(), can_self_evacuate=False, transport_available=False)
    row = coordinate_approved_evacuation(scenario(), [call], store, as_of=AT)['locations'][0]
    assert row['mode'] == 'assisted_evacuation'
    assert row['human_followup'] is True
    assert 'arrange_assistance' in row['tasks']
    assert 'assistance_record_conflict' in row['reasons']


def test_acknowledged_blocked_road_warning_still_withholds_allocated_route(tmp_path):
    from fireline.route_guidance import road_warning_version
    store = setup_store(tmp_path)
    reserve(store)
    warnings = [{'road_id': 'road-1', 'road_name': 'Synthetic Road', 'reason': 'fire',
                 'source': 'road desk'}]
    call = replace(assessment(), acknowledged_road_warning_version=road_warning_version(warnings))
    row = coordinate_approved_evacuation(scenario(), [call], store, as_of=AT, road_warnings=warnings)['locations'][0]
    assert row['human_followup'] is True
    assert row['allocations'][0]['instruction_allowed'] is False
    assert 'road_danger_reported' in row['reasons']


def test_exhausted_current_scenario_window_cannot_be_overridden_by_older_group_timing(tmp_path):
    store = setup_store(tmp_path)
    reserve(store)
    urgent = replace(scenario(), locations=(replace(scenario().locations[0], fire_arrival_min=4),))
    row = coordinate_approved_evacuation(urgent, [assessment()], store, as_of=AT)['locations'][0]
    assert row['human_followup'] is True
    assert row['allocations'][0]['instruction_allowed'] is False
    assert 'contact_window_unavailable' in row['reasons']


def test_briefing_cannot_address_whole_building_without_current_population_count(tmp_path):
    store = setup_store(tmp_path)
    reserve(store)
    assert build_approved_recommendation(store, 'A', as_of=AT, snapshot_id='snapshot-1',
                                         route_guidance=guidance(), expected_people=8) is None
    assert build_approved_recommendation(store, 'A', as_of=AT, snapshot_id='snapshot-1',
                                         route_guidance=guidance()) is None
