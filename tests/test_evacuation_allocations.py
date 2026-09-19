"""Real SQLite writers compete for durable reception capacity."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest

from fireline import evacuation_plans as ep
from fireline.evacuation_allocations import AllocationStore, AssistancePlan
from tests.test_evacuation_plans import inputs

AT = '2026-09-20T00:00:00Z'


def setup_store(tmp_path, *, assisted=False):
    context, centre, group, route, road = inputs()
    group = replace(group, assisted=assisted)
    other = replace(group, group_id='group-B', asset_id='B')
    store = AllocationStore(tmp_path / 'allocations.sqlite')
    store.update_inputs(context, [centre], [group, other], [route, replace(route, asset_id='B')], [road])
    return store


def reserve(store, command='reserve-A', group='group-A', centre='centre', **kwargs):
    approval = ep.AnalystApproval('allocation-' + command, 'analyst', 'incident-1', centre,
                                  'private analyst notes', group)
    return store.reserve(command, group, centre, approval, as_of=AT, snapshot_id='snapshot-1', **kwargs)


def confirm(store, allocation_id, state, command=None, at=AT):
    return store.confirm(command or state, allocation_id, state, actor='operator',
                         evidence='private confirmation', as_of=at)


def test_multiple_buildings_compete_transactionally_and_restart_keeps_slots(tmp_path):
    store = setup_store(tmp_path)
    def attempt(group):
        try:
            return reserve(AllocationStore(store.path), command=group, group=group)['group_id']
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = list(pool.map(attempt, ['group-A', 'group-B']))
    assert sum(winner is not None for winner in winners) == 1
    reopened = AllocationStore(store.path)
    assert reopened.public_plan(as_of=AT)['remaining_capacity'] == {'centre': 2}
    assert len(reopened.public_plan(as_of=AT)['locations']) == 1


def test_retries_are_idempotent_but_key_reuse_with_changed_arguments_fails(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    assert reserve(store)['allocation_id'] == a['allocation_id']
    with pytest.raises(ValueError, match='command'):
        reserve(store, group='group-B')
    with pytest.raises(ValueError, match='active'):
        reserve(store, command='second')
    assert store.public_plan(as_of=AT)['remaining_capacity']['centre'] == 2


def test_arrival_requires_departure_and_never_releases_capacity(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    with pytest.raises(ValueError, match='transition'):
        confirm(store, a['allocation_id'], 'arrived')
    for state in ('communicated', 'departed', 'arrived'):
        assert confirm(store, a['allocation_id'], state)['state'] == state
    assert AllocationStore(store.path).public_plan(as_of=AT)['remaining_capacity']['centre'] == 2
    store.release('release', a['allocation_id'], actor='operator', evidence='checked out', as_of=AT)
    assert store.public_plan(as_of=AT)['remaining_capacity']['centre'] == 6


def test_updates_preserve_communicated_destination_and_invalidate_on_danger(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    confirm(store, a['allocation_id'], 'communicated')
    context, centre, group, route, road = inputs()
    store.update_inputs(replace(context, snapshot_id='snapshot-2'), [centre], [group], [route], [road])
    assert store.briefing('A', as_of=AT)[0]['destination_id'] == 'centre'
    store.update_inputs(replace(context, snapshot_id='snapshot-3'), [replace(centre, closed=True)], [group], [route], [road])
    row = store.briefing('A', as_of=AT)[0]
    assert row['destination_id'] == 'centre'
    assert row['safety'] == 'invalidated'
    assert row['instruction_allowed'] is False
    assert 'issue_new_instructions' in row['tasks']
    assert store.public_plan(as_of=AT)['remaining_capacity']['centre'] == 2
    store.update_inputs(replace(context, snapshot_id='snapshot-4'), [centre], [group], [route], [road])
    assert store.briefing('A', as_of=AT)[0]['safety'] == 'invalidated'


def test_new_incident_cannot_forget_old_occupants_or_reuse_approval(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    context, centre, group, route, road = inputs()
    store.update_inputs(replace(context, incident_id='incident-2', snapshot_id='new-incident'),
                        [centre], [group], [route], [road])
    row = store.public_plan(as_of=AT)['locations'][0]
    assert row['allocation_id'] == a['allocation_id']
    assert 'incident_changed' in row['reasons']
    assert row['safety'] == 'invalidated'
    assert store.public_plan(as_of=AT)['remaining_capacity']['centre'] == 2


def test_read_at_later_time_withholds_stale_plan_even_without_update(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    row = store.briefing('A', as_of='2026-09-20T00:20:00Z')[0]
    assert row['instruction_allowed'] is False
    assert 'stale_forecast' in row['reasons']
    with pytest.raises(ValueError, match='safety'):
        confirm(store, a['allocation_id'], 'communicated', at='2026-09-20T00:20:00Z')


def test_assisted_allocation_requires_separate_confirmed_transport_and_reception(tmp_path):
    store = setup_store(tmp_path, assisted=True)
    a = reserve(store)
    assert 'arrange_transport' in a['tasks']
    assert 'arrange_reception' in a['tasks']
    assert a['instruction_allowed'] is False
    with pytest.raises(ValueError, match='assistance'):
        confirm(store, a['allocation_id'], 'communicated')
    plan = AssistancePlan('transport-1', 'reception-1', 2, 4, ())
    store.set_assistance('support', a['allocation_id'], plan, actor='analyst', evidence='bookings', as_of=AT)
    confirm(store, a['allocation_id'], 'transport_confirmed')
    assert store.briefing('A', as_of=AT)[0]['instruction_allowed'] is False
    confirm(store, a['allocation_id'], 'reception_confirmed')
    assert store.briefing('A', as_of=AT)[0]['instruction_allowed'] is True
    assert store.briefing('A', as_of=AT)[0]['state'] == 'reserved'


def test_reassignment_is_explicit_atomic_and_needs_new_communication(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    confirm(store, a['allocation_id'], 'communicated')
    context, centre, group, route, road = inputs()
    other = replace(centre, centre=replace(centre.centre, centre_id='other'),
                    approval=replace(centre.approval, approval_id='approval-other', centre_id='other'))
    store.update_inputs(replace(context, snapshot_id='snapshot-2'), [centre, other], [group],
                        [route, replace(route, centre_id='other')], [road])
    approval = ep.AnalystApproval('move-approval', 'analyst', 'incident-1', 'other', 'approved move', 'group-A')
    with pytest.raises(ValueError):
        store.reassign('bad-move', a['allocation_id'], 'missing', approval, as_of=AT, snapshot_id='snapshot-2')
    assert store.briefing('A', as_of=AT)[0]['destination_id'] == 'centre'
    b = store.reassign('move', a['allocation_id'], 'other', approval, as_of=AT, snapshot_id='snapshot-2')
    assert b['state'] == 'reserved'
    assert b['previous_allocation_id'] == a['allocation_id']
    assert 'issue_new_instructions' in b['tasks']
    assert store.public_plan(as_of=AT)['remaining_capacity'] == {'centre': 6, 'other': 2}


def test_read_model_contains_no_private_confirmation_or_approval_text(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    confirm(store, a['allocation_id'], 'communicated')
    result = store.public_plan(as_of=AT)
    assert result['schema_version'] == 'evacuation-plan-1'
    assert 'private' not in json.dumps(result)
    assert result['locations'][0]['asset_id'] == 'A'
    assert result['events'][-1]['kind'] == 'communicated'


def test_snapshot_conflicts_and_time_rewind_are_rejected(tmp_path):
    store = setup_store(tmp_path)
    context, centre, group, route, road = inputs()
    with pytest.raises(ValueError, match='snapshot'):
        store.update_inputs(context, [replace(centre, closed=True)], [group], [route], [road])
    with pytest.raises(ValueError, match='snapshot'):
        store.reserve('bad', 'group-A', 'centre', centre.approval, as_of=AT, snapshot_id='missing')


def test_delayed_assisted_pickup_must_fit_route_and_destination_window(tmp_path):
    store = setup_store(tmp_path, assisted=True)
    a = reserve(store)
    plan = AssistancePlan('transport', 'reception', 58, 4, ())
    store.set_assistance('support', a['allocation_id'], plan, actor='analyst', evidence='booked', as_of=AT)
    confirm(store, a['allocation_id'], 'transport_confirmed')
    confirm(store, a['allocation_id'], 'reception_confirmed')
    assert store.briefing('A', as_of=AT)[0]['instruction_allowed'] is False


def test_assistance_must_fit_route_deadline_even_when_origin_window_is_long(tmp_path):
    store = setup_store(tmp_path, assisted=True)
    context, centre, group, route, road = inputs()
    group = replace(group, assisted=True, fire_arrival_min=200)
    store.update_inputs(replace(context, snapshot_id='support-window'), [centre], [group],
                        [replace(route, available_until_min=10)], [road])
    approval = ep.AnalystApproval('alloc', 'analyst', 'incident-1', 'centre', 'reviewed', 'group-A')
    a = store.reserve('reserve', 'group-A', 'centre', approval, as_of=AT, snapshot_id='support-window')
    store.set_assistance('support', a['allocation_id'], AssistancePlan('van', 'staff', 8, 4, ()),
                         actor='analyst', evidence='booked', as_of=AT)
    confirm(store, a['allocation_id'], 'transport_confirmed')
    confirm(store, a['allocation_id'], 'reception_confirmed')
    assert store.briefing('A', as_of=AT)[0]['instruction_allowed'] is False


def test_routine_forecast_changes_do_not_invalidate_still_safe_approved_destination(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    context, centre, group, route, road = inputs()
    store.update_inputs(replace(context, snapshot_id='new-forecast'), [centre],
                        [replace(group, fire_arrival_min=70, forecast_source='forecast-2')], [route], [road])
    assert store.briefing('A', as_of=AT)[0]['safety'] == 'valid'
    assert store.briefing('A', as_of=AT)[0]['allocation_id'] == a['allocation_id']


def test_input_update_cannot_rewind_past_confirmed_departure(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    confirm(store, a['allocation_id'], 'communicated', at='2026-09-20T00:02:00Z')
    context, centre, group, route, road = inputs()
    with pytest.raises(ValueError, match='rewind'):
        store.update_inputs(replace(context, snapshot_id='out-of-order'), [centre], [group], [route], [road])


def test_safe_replacement_route_cannot_hide_danger_on_previously_communicated_route(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    confirm(store, a['allocation_id'], 'communicated')
    context, centre, group, route, road = inputs()
    store.update_inputs(replace(context, snapshot_id='road-change'), [centre], [group],
                        [replace(route, road_ids=('road-2',))],
                        [replace(road, state='blocked'), replace(road, road_id='road-2')])
    row = store.briefing('A', as_of=AT)[0]
    assert row['instruction_allowed'] is False
    assert row['destination_id'] == 'centre'
    assert 'approved_route_changed' in row['reasons']
    assert 'issue_new_instructions' in row['tasks']


def test_removed_centre_preserves_unknown_capacity_and_occupied_allocation(tmp_path):
    store = setup_store(tmp_path)
    reserve(store)
    context, _, group, _, road = inputs()
    store.update_inputs(replace(context, snapshot_id='centre-missing'), [], [group], [], [road])
    plan = store.public_plan(as_of=AT)
    assert plan['remaining_capacity']['centre'] is None
    assert plan['locations'][0]['people'] == 4


def test_cannot_reserve_other_group_at_time_before_latest_ledger_event(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    store.release('release', a['allocation_id'], actor='analyst', evidence='cancelled', as_of='2026-09-20T00:01:00Z')
    with pytest.raises(ValueError, match='rewind'):
        reserve(store, command='reserve-B', group='group-B')


def test_successful_command_retry_survives_new_snapshot_and_returns_current_safety(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    context, centre, group, route, road = inputs()
    store.update_inputs(replace(context, snapshot_id='later', as_of='2026-09-20T00:01:00Z'),
                        [replace(centre, closed=True)], [group], [route], [road])
    replay = reserve(store)
    assert replay['allocation_id'] == a['allocation_id']
    assert replay['instruction_allowed'] is False
    assert replay['evaluated_as_of'] == '2026-09-20T00:01:00Z'


def test_same_approval_cannot_reauthorize_an_invalidated_allocation(tmp_path):
    store = setup_store(tmp_path)
    a = reserve(store)
    approval = ep.AnalystApproval('allocation-reserve-A', 'analyst', 'incident-1', 'centre',
                                  'private analyst notes', 'group-A')
    with pytest.raises(ValueError, match='new approval'):
        store.reassign('new-command', a['allocation_id'], 'centre', approval, as_of=AT, snapshot_id='snapshot-1')
