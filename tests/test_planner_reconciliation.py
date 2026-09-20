"""Crew needs reflect confirmed physical arrivals in the authoritative ledger."""
from dataclasses import replace
from datetime import timedelta

import pytest

from fireline.evacuation_allocations import AllocationStore
from fireline.evacuation_plans import AnalystApproval
from fireline.incident_planning import response_from_snapshot, scenario_from_snapshot
from fireline.voice_store import VoiceStore
from tests.test_evacuation_plans import inputs
from tests.test_incident_planning import EPOCH, operations, snapshot


def ledger(tmp_path, *, people=2, state='arrived', groups=1):
    store = AllocationStore(tmp_path / 'allocations.sqlite')
    context, centre, group, route, road = inputs()
    context = replace(context, scenario_id='s', incident_id='incident', snapshot_id='s-0001',
                      epoch=EPOCH.isoformat(), as_of=EPOCH.isoformat())
    centre = replace(centre, centre=replace(centre.centre, remaining_places=20),
                     approval=replace(centre.approval, incident_id='incident'))
    group = replace(group, people=people)
    group_rows = [replace(group, group_id=f'group-{i}') for i in range(groups)]
    store.update_inputs(context, [centre], group_rows, [route], [road])
    for i in range(groups):
        approval = AnalystApproval(f'approval-{i}', 'analyst', 'incident', 'centre',
                                   'verified reception', f'group-{i}')
        allocation = store.reserve(f'reserve-{i}', f'group-{i}', 'centre', approval,
                                   as_of=EPOCH.isoformat(), snapshot_id='s-0001')
        for step in ('communicated', 'departed', 'arrived'):
            if state == 'reserved':
                break
            store.confirm(f'{step}-{i}', allocation['allocation_id'], step, actor='analyst',
                          evidence='physical observation', as_of=EPOCH.isoformat())
            if step == state:
                break
    return store


def response(store=None, *, people=2, assisted=1, snap=None, membership=None, extra_ops=None):
    snap = snapshot() if snap is None else snap
    snap['incident_id'] = 'incident'
    snap['assets'][0]['estimated_occupancy'] = people
    ops = operations()
    ops['snapshot_id'] = snap['snapshot_id']
    ops['assisted']['A'] = assisted
    if membership is not None:
        ops['arrival_group_membership'] = membership
    ops.update(extra_ops or {})
    scenario = scenario_from_snapshot(snap, ops, EPOCH)
    voice = VoiceStore(epoch=EPOCH, clock=lambda: EPOCH)
    try:
        return response_from_snapshot(snap, scenario, ops, voice, EPOCH, EPOCH,
                                      allocation_store=store)
    finally:
        voice.close()


def test_confirmed_arrival_removes_human_need_but_retains_property(tmp_path):
    store = ledger(tmp_path)
    result = response(store)
    need = result['remaining_needs'][0]
    assert (need['original_people'], need['remaining_people'], need['remaining_assisted']) == (2, 0, 0)
    assert need['confirmed_arrived'] == 2
    assert result['objective']['people_units'] == 0
    assert result['objective']['assisted_units'] == 0
    assert result['objective']['value_units'] == 10
    assert response(store)['remaining_needs'] == result['remaining_needs']


@pytest.mark.parametrize('state', ['reserved', 'communicated', 'departed'])
def test_promises_and_departure_do_not_erase_rescue_need(tmp_path, state):
    result = response(ledger(tmp_path, state=state))
    need = result['remaining_needs'][0]
    assert (need['remaining_people'], need['remaining_assisted'], need['confirmed_arrived']) == (2, 1, 0)


def test_partial_arrival_without_membership_keeps_conservative_assistance(tmp_path):
    result = response(ledger(tmp_path, people=2), people=5, assisted=4)
    need = result['remaining_needs'][0]
    assert (need['remaining_people'], need['remaining_assisted']) == (3, 3)
    assert 'arrived_assisted_membership_unknown' in need['review_reasons']


def test_groups_without_disjoint_membership_do_not_double_count_arrivals(tmp_path):
    result = response(ledger(tmp_path, people=2, groups=2), people=5, assisted=4)
    need = result['remaining_needs'][0]
    assert need['confirmed_arrived'] == 2
    assert 'arrival_group_overlap_unknown' in need['review_reasons']


def test_new_destination_danger_does_not_put_arrived_people_back_in_building(tmp_path):
    store = ledger(tmp_path)
    _, current = store.view(as_of=EPOCH.isoformat())
    context, candidates, groups, routes, roads = current
    store.update_inputs(replace(context, snapshot_id='s-0002'),
                        [replace(candidates[0], closed=True)], groups, routes, roads)
    snap = snapshot()
    snap['snapshot_id'] = 's-0002'
    result = response(store, snap=snap, extra_ops={'arrival_baseline_snapshot_ids': {'A': 's-0001'}})
    need = result['remaining_needs'][0]
    assert need['remaining_people'] == 0
    assert 'arrived_destination_requires_review' in need['review_reasons']
    assert any(r.get('reason') == 'arrived_destination_requires_review' for r in result['review'])


@pytest.mark.parametrize('field,value', [('scenario_id', 'other'), ('snapshot_id', 'wrong'),
                                        ('epoch', (EPOCH - timedelta(days=1)).isoformat())])
def test_wrong_ledger_context_cannot_reduce_current_needs(tmp_path, field, value):
    store = ledger(tmp_path)
    if field == 'snapshot_id':
        snap = snapshot()
        snap['snapshot_id'] = value
        with pytest.raises(ValueError, match='allocation context'):
            response(store, snap=snap)
    else:
        # A valid independent ledger must still match this planning context.
        _, current = store.view(as_of=EPOCH.isoformat())
        context, candidates, groups, routes, roads = current
        other = AllocationStore(tmp_path / 'other.sqlite')
        other.update_inputs(replace(context, **{field: value}), candidates, groups, routes, roads)
        with pytest.raises(ValueError, match='allocation context'):
            response(other)



def test_arrived_unknown_original_occupancy_stays_unknown(tmp_path):
    result = response(ledger(tmp_path, people=2), people=None, assisted=None)
    need = result['remaining_needs'][0]
    assert need['confirmed_arrived'] == 2
    assert need['remaining_people'] is None and need['remaining_assisted'] is None
    assert 'arrival_membership_unknown' in need['review_reasons']


def test_released_arrival_has_explicit_location_reconciliation_review(tmp_path):
    store = ledger(tmp_path)
    allocation = store.public_plan(as_of=EPOCH.isoformat())['locations'][0]
    store.release('release', allocation['allocation_id'], actor='analyst',
                  evidence='departed reception; new location unconfirmed', as_of=EPOCH.isoformat())
    need = response(store)['remaining_needs'][0]
    assert 'released_arrival_location_requires_review' in need['review_reasons']


def test_exact_partial_membership_unions_overlap_and_subtracts_only_known_assisted(tmp_path):
    store = ledger(tmp_path, people=2, groups=2)
    membership = {
        'group-0': {'member_ids': ['p1', 'p2'], 'assisted_member_ids': ['p1'], 'source': 'register'},
        'group-1': {'member_ids': ['p2', 'p3'], 'assisted_member_ids': ['p3'], 'source': 'register'},
    }
    result = response(store, people=5, assisted=3, membership=membership)
    need = result['remaining_needs'][0]
    assert (need['confirmed_arrived'], need['remaining_people'], need['remaining_assisted']) == (3, 2, 1)
    assert not need['review_reasons']
    import json
    assert 'p1' not in json.dumps(result) and 'member_ids' not in json.dumps(result)


def test_conflicting_assistance_membership_retains_conservative_count(tmp_path):
    store = ledger(tmp_path, people=2, groups=2)
    membership = {
        'group-0': {'member_ids': ['p1', 'p2'], 'assisted_member_ids': ['p1'], 'source': 'register'},
        'group-1': {'member_ids': ['p2', 'p3'], 'assisted_member_ids': ['p2'], 'source': 'register'},
    }
    need = response(store, people=5, assisted=3, membership=membership)['remaining_needs'][0]
    assert need['remaining_assisted'] == 2
    assert 'arrival_assistance_membership_conflict' in need['review_reasons']


def test_malformed_membership_collection_fails_with_clear_input_error(tmp_path):
    with pytest.raises(ValueError, match='arrival_group_membership'):
        response(ledger(tmp_path), membership=[])


def test_membership_count_must_match_ledger_group_count(tmp_path):
    membership = {'group-0': {'member_ids': ['p1'], 'assisted_member_ids': ['p1'], 'source': 'register'}}
    need = response(ledger(tmp_path, people=2), people=5, assisted=3,
                    membership=membership)['remaining_needs'][0]
    assert need['remaining_assisted'] == 3
    assert 'arrival_membership_invalid' in need['review_reasons']


def test_old_snapshot_arrival_cannot_double_subtract_already_netted_occupancy(tmp_path):
    store = ledger(tmp_path)
    _, current = store.view(as_of=EPOCH.isoformat())
    context, candidates, groups, routes, roads = current
    store.update_inputs(replace(context, snapshot_id='s-0002'), candidates, groups, routes, roads)
    snap = snapshot()
    snap['snapshot_id'] = 's-0002'
    need = response(store, people=2, snap=snap)['remaining_needs'][0]
    assert need['remaining_people'] == 2
    assert need['confirmed_arrived'] == 0
    assert 'arrival_baseline_review' in need['review_reasons']


def mission_operations():
    ops = operations()
    ops['actions'][0].update(transport_people=2, evacuation={
        'destination_id': 'centre', 'node_id': 'centre', 'unload_min': 1,
        'available_until_min': 120, 'confirmed': True, 'safe': True,
        'source': 'analyst supplied mission', 'places_reserved': 2})
    ops['routes'].append({'from_node': 'A', 'to_node': 'centre', 'minutes': 2,
                         'available_until_min': 120, 'confirmed': True,
                         'safe': True, 'source': 'road desk'})
    return ops


def all_tasks(result):
    return [task for team in result['teams'] for task in team['tasks']]


def test_complete_mission_uses_current_reserved_reception_capacity(tmp_path):
    result = response(ledger(tmp_path, state='reserved'), extra_ops=mission_operations())
    task = all_tasks(result)[0]
    assert task['mission_status'] == 'complete_evacuation'
    assert task['delivered_people'] == 2


def test_raw_mission_places_cannot_override_released_reservation(tmp_path):
    store = ledger(tmp_path, state='reserved')
    allocation = store.public_plan(as_of=EPOCH.isoformat())['locations'][0]
    store.release('release', allocation['allocation_id'], actor='analyst',
                  evidence='reservation cancelled', as_of=EPOCH.isoformat())
    result = response(store, extra_ops=mission_operations())
    assert all_tasks(result) == []
    assert any(r.get('reason') == 'evacuation_reservation_unavailable' for r in result['review'])


def test_raw_safe_mission_cannot_override_unsafe_reception(tmp_path):
    store = ledger(tmp_path, state='reserved')
    _, current = store.view(as_of=EPOCH.isoformat())
    context, candidates, groups, routes, roads = current
    store.update_inputs(replace(context, snapshot_id='s-0002'),
                        [replace(candidates[0], closed=True)], groups, routes, roads)
    snap = snapshot()
    snap['snapshot_id'] = 's-0002'
    ops = mission_operations()
    ops['snapshot_id'] = 's-0002'
    result = response(store, snap=snap, extra_ops=ops)
    assert all_tasks(result) == []
    assert any(r.get('reason') == 'evacuation_reservation_unavailable' for r in result['review'])


def test_full_arrival_suppresses_transport_and_marks_dependent_work_for_review(tmp_path):
    ops = mission_operations()
    ops['actions'].append({**operations()['actions'][0], 'action_id': 'followup', 'requires': ['help-A']})
    result = response(ledger(tmp_path), extra_ops=ops)
    assert all_tasks(result) == []
    assert any(r.get('action_id') == 'help-A' and r.get('reason') == 'transport_no_remaining_people'
               for r in result['review'])
    assert any(r.get('action_id') == 'followup' and r.get('reason') == 'prerequisite_requires_replanning'
               for r in result['review'])


def test_partial_arrival_requires_explicit_mission_resizing(tmp_path):
    result = response(ledger(tmp_path, people=1), extra_ops=mission_operations())
    assert all_tasks(result) == []
    review = next(r for r in result['review'] if r.get('reason') == 'resize_evacuation_mission_required')
    assert review['human_decision_required'] is True
    assert review['requested_transport_people'] == 2
    assert review['remaining_people'] == 1


@pytest.mark.parametrize('people,assisted', [(2, 3), (True, 1), (2, True), (2, 1.0)])
def test_reconciliation_cannot_silently_repair_invalid_original_counts(tmp_path, people, assisted):
    with pytest.raises(ValueError):
        response(ledger(tmp_path), people=people, assisted=assisted)
