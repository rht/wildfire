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
    result = build_approved_recommendation(store, 'A', as_of=AT, snapshot_id='snapshot-1', route_guidance=guidance())
    assert result['approved'] is True
    assert result['destination']['name'] == 'Hall'
    assert result['route']['instructions'] == guidance()['instructions']
    assert result['route']['feasible'] is True
    for change in ({'road_ids': ['other']}, {'snapshot_id': 'old'}, {'asset_id': 'other'}, {'confirmed': False}):
        assert build_approved_recommendation(store, 'A', as_of=AT, snapshot_id='snapshot-1', route_guidance=guidance() | change) is None


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
