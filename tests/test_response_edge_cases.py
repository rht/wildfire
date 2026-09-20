"""Behavior regressions for bounded rescue planning and explicit mission evidence."""
import json
from copy import deepcopy

import pytest

from tests.test_multi_response import action, asset, plan, route, scene, tasks, team


def single():
    data = scene()
    data['teams'] = [team('one')]
    data['actions'] = [action('help-A', 'A')]
    return data


def evacuation(people=16):
    data = single()
    data['assets'] = [asset('A', people=people, assisted=4, deadline=100)]
    data['horizon_min'] = 100
    data['teams'] = [team('one', capacity=8)]
    data['teams'][0]['available_until_min'] = 100
    task = data['actions'][0]
    task.update(transport_people=people, deadline_min=100, evacuation={
        'destination_id': 'shelter', 'node_id': 'SAFE', 'unload_min': 1,
        'available_until_min': 100, 'confirmed': True, 'safe': True,
        'source': 'confirmed reception', 'places_reserved': people})
    data['routes'] = [route('BASE', 'A'), route('A', 'SAFE'), route('SAFE', 'A')]
    for leg in data['routes']:
        leg['available_until_min'] = 100
    return data


def test_unknown_money_does_not_suppress_known_people_or_assistance():
    data = single()
    data['assets'][0]['value'] = None
    row = tasks(plan(data))[0]
    assert row['unknown_dimensions'] == ['value']
    assert plan(data)['objective']['assisted_units'] == 2
    assert plan(data)['objective']['people_units'] == 2
    assert data['assets'][0]['value'] is None
    assert any('unknown_value' in r['reasons'] for r in plan(data)['review'])


def test_unknown_assistance_is_never_invented_but_people_can_be_protected():
    data = single()
    data['assets'][0]['assisted'] = None
    result = plan(data)
    assert tasks(result)[0]['unknown_dimensions'] == ['assisted']
    assert result['objective']['people_units'] == 2
    assert result['objective']['assisted_units'] == 0


def test_fresh_call_is_checked_at_intervention_start_not_fire_buffer():
    data = single()
    data.update(buffer_min=30, horizon_min=100)
    data['assets'][0]['deadline_min'] = 100
    data['teams'][0]['available_until_min'] = 100
    data['actions'][0].update(deadline_min=100, readiness_required=True)
    for leg in data['routes']:
        leg['available_until_min'] = 100
    data['readiness'] = [{'asset_id': 'A', 'status': 'assistance_required', 'observed_min': 0,
        'valid_until_min': 15, 'source': 'confirmed call', 'request_id': 'call'}]
    assert tasks(plan(data))[0]['start_min'] == 1
    data['readiness'][0]['valid_until_min'] = 1
    assert not tasks(plan(data))
    assert 'readiness_review' in plan(data)['review'][0]['reasons']


def test_late_safe_reachable_action_stays_explicit_urgent_human_review():
    data = single()
    data['assets'][0]['deadline_min'] = 2
    result = plan(data)
    assert tasks(result) == []
    item = result['review'][0]
    assert item['reason'] == 'urgent_intervention_review'
    assert item['human_decision_required'] is True
    assert 'deadline' in item['reasons']
    assert item['candidate_attempts'][0]['start_min'] == 1
    assert item['candidate_attempts'][0]['finish_min'] == 3
    data['routes'][0]['safe'] = False
    assert not plan(data)['review'][0]['candidate_attempts']


def test_lookahead_saves_urgent_small_before_later_large():
    data = single()
    data['assets'] = [asset('A', people=1, assisted=1, deadline=3),
                      asset('B', people=10, assisted=10, deadline=20)]
    data['actions'] = [action('urgent', 'A'), action('large', 'B')]
    result = plan(data)
    assert [r['action_id'] for r in tasks(result)] == ['urgent', 'large']
    assert result['objective']['assisted_units'] == 11
    assert result['optimal'] is False
    assert 'lookahead' in result['method']


def test_lookahead_preserves_quick_property_before_rescue_if_both_fit():
    data = single()
    data['assets'] = [asset('A', people=0, assisted=0, value=100, deadline=3),
                      asset('B', people=10, assisted=10, deadline=20)]
    data['actions'] = [action('quick', 'A'), action('rescue', 'B')]
    result = plan(data)
    assert [r['action_id'] for r in tasks(result)] == ['quick', 'rescue']
    assert result['objective']['value_units'] == 110


def test_explicit_evacuation_repeats_trips_and_finishes_at_destination():
    result = plan(evacuation())
    row = tasks(result)[0]
    assert row['mission_status'] == 'complete_evacuation'
    assert row['delivered_people'] == 16
    assert row['to_node'] == 'SAFE'
    assert row['finish_min'] == 10
    assert [leg['kind'] for leg in row['mission_legs']] == [
        'approach', 'delivery', 'return', 'delivery']
    assert [leg['people'] for leg in row['mission_legs'] if leg['kind'] == 'delivery'] == [8, 8]
    assert result['teams'][0]['remaining_transport_capacity'] == 8
    assert result['objective']['people_units'] == 16


@pytest.mark.parametrize('change,reason', [('unsafe_return', 'evacuation_route_unavailable'),
    ('places', 'evacuation_places'), ('window', 'evacuation_destination_window'),
    ('unconfirmed', 'evacuation_destination_unconfirmed')])
def test_incomplete_missions_are_reviewed_without_claiming_delivery(change, reason):
    data = evacuation()
    if change == 'unsafe_return':
        data['routes'][-1]['safe'] = False
    elif change == 'places':
        data['actions'][0]['evacuation']['places_reserved'] = 8
    elif change == 'window':
        data['actions'][0]['evacuation']['available_until_min'] = 5
    else:
        data['actions'][0]['evacuation']['confirmed'] = False
    result = plan(data)
    assert not tasks(result)
    assert result['objective']['people_units'] == 0
    assert reason in result['review'][0]['reasons']


def test_pickup_only_is_distinct_and_requires_destination_review():
    data = single()
    data['actions'][0]['transport_people'] = 2
    result = plan(data)
    assert tasks(result)[0]['mission_status'] == 'pickup_only'
    assert 'delivered_people' not in tasks(result)[0]
    assert any('evacuation_details_missing' in r['reasons'] for r in result['review'])


def test_delivery_caps_human_credit_without_destroying_property_value():
    data = evacuation(16)
    data['assets'][0]['people'] = 20
    result = plan(data)
    assert result['objective']['people_units'] == 16
    assert result['objective']['value_units'] == 10


def test_joint_crews_start_together_reserve_both_and_credit_once():
    data = single()
    data['teams'] = [team('one'), team('two')]
    data['teams'][1]['available_from_min'] = 5
    data['actions'][0]['required_team_count'] = 2
    result = plan(data)
    rows = tasks(result)
    assert len(rows) == 2
    assert {r['start_min'] for r in rows} == {6}
    assert all(r['team_ids'] == ['one', 'two'] for r in rows)
    assert result['objective']['people_units'] == 2
    assert sum(bool(r['coverage_gained']) for r in rows) == 1
    data['teams'][1]['available'] = False
    assert not tasks(plan(data))
    assert 'required_teams_unavailable' in plan(data)['review'][0]['reasons']


def test_supplied_uncertainty_marks_fragility_and_prefers_robust_equal_benefit():
    data = single()
    data['assets'][0]['deadline_early_min'] = 5
    data['actions'] = [action('fragile', 'A'), action('robust', 'A')]
    data['actions'][0]['duration_high_min'] = 6
    data['actions'][1]['duration_high_min'] = 3
    result = plan(data)
    assert tasks(result)[0]['action_id'] == 'robust'
    assert tasks(result)[0]['sensitivity']['status'] == 'robust'
    data['actions'] = data['actions'][:1]
    assert tasks(plan(data))[0]['sensitivity']['status'] == 'fragile'
    del data['actions'][0]['duration_high_min']
    assert tasks(plan(data))[0]['sensitivity']['status'] == 'unknown'
    json.dumps(plan(data), allow_nan=False)


@pytest.mark.parametrize('field,value', [('duration_high_min', 1), ('duration_high_min', float('inf')),
    ('required_team_count', 0), ('required_team_count', True)])
def test_invalid_action_evidence_is_rejected(field, value):
    data = single()
    data['actions'][0][field] = value
    with pytest.raises(ValueError):
        plan(data)


@pytest.mark.parametrize('change', ['early_late', 'places_bool', 'unload_nan', 'search_zero'])
def test_invalid_mission_uncertainty_and_search_evidence(change):
    data = evacuation()
    if change == 'early_late':
        data['assets'][0]['deadline_early_min'] = 101
    elif change == 'places_bool':
        data['actions'][0]['evacuation']['places_reserved'] = True
    elif change == 'unload_nan':
        data['actions'][0]['evacuation']['unload_min'] = float('nan')
    else:
        data['search'] = {'depth': 0}
    with pytest.raises(ValueError):
        plan(data)


def test_joint_evacuation_reserves_places_once_and_splits_delivery_between_crews():
    data = evacuation(24)
    data['teams'].append(team('two', capacity=8))
    data['teams'][1]['available_until_min'] = 100
    data['actions'][0]['required_team_count'] = 2
    result = plan(data)
    rows = tasks(result)
    assert len(rows) == 2
    assert [r['delivered_people'] for r in rows] == [16, 8]
    assert all(r['mission_delivered_people'] == 24 for r in rows)
    assert all(r['transport_people'] == r['delivered_people'] for r in rows)
    assert {r['finish_min'] for r in rows} == {10}
    assert result['objective']['people_units'] == 24
    assert all(t['remaining_transport_capacity'] == 8 for t in result['teams'])


def test_advanced_commitments_lock_all_participants_without_duplicate_credit():
    data = evacuation(16)
    data['teams'].append(team('two', capacity=8))
    data['teams'][1]['available_until_min'] = 100
    data['actions'][0]['required_team_count'] = 2
    rows = tasks(plan(data))
    assert len(rows) == 2
    data['committed'] = deepcopy(rows)
    for row in data['committed']:
        row['status'] = 'en_route'
    result = plan(data)
    assert all(t['locked'] for t in result['teams'])
    assert len(tasks(result)) == 2
    assert result['objective']['people_units'] == 0
    assert any('advanced_commitment_review' in r['reasons'] for r in result['review'])


def test_stressed_prior_duration_propagates_to_next_action():
    data = single()
    data['assets'] = [asset('A'), asset('B')]
    for target in data['assets']:
        target['deadline_early_min'] = 8
    data['actions'] = [action('first', 'A'), action('next', 'B', requires=['first'])]
    data['actions'][0]['duration_high_min'] = 5
    data['actions'][1]['duration_high_min'] = 3
    rows = tasks(plan(data))
    assert rows[0]['sensitivity']['status'] == 'robust'
    assert rows[1]['sensitivity']['status'] == 'fragile'
    assert rows[1]['sensitivity']['stress_finish_min'] == 10


def test_partial_population_transport_does_not_invent_assisted_membership():
    data = evacuation(16)
    data['assets'][0]['people'] = 20
    result = plan(data)
    assert result['objective']['assisted_units'] == 0
    assert result['coverage_by_dimension']['A']['people'] == 0.8
    assert result['coverage_by_dimension']['A']['value'] == 1


def test_repeated_pickup_requires_fresh_readiness_for_each_intervention():
    data = evacuation()
    data['actions'][0]['readiness_required'] = True
    data['readiness'] = [{'asset_id': 'A', 'status': 'assistance_required', 'observed_min': 0,
        'valid_until_min': 6, 'source': 'confirmed call', 'request_id': 'call'}]
    result = plan(data)
    assert not tasks(result)
    assert 'readiness_review' in result['review'][0]['reasons']


def test_unknown_previous_duration_prevents_claiming_later_robustness():
    data = single()
    data['assets'] = [asset('A'), asset('B')]
    data['assets'][1]['deadline_early_min'] = 20
    data['actions'] = [action('first', 'A'), action('next', 'B', requires=['first'])]
    data['actions'][1]['duration_high_min'] = 3
    assert tasks(plan(data))[1]['sensitivity']['status'] == 'unknown'


def test_transport_cannot_invent_people_beyond_known_population():
    data = evacuation()
    data['assets'][0]['people'] = 10
    result = plan(data)
    assert not tasks(result)
    assert 'transport_people_exceeds_people' in result['review'][0]['reasons']


def test_overflow_cannot_escape_as_nonfinite_public_json():
    data = single()
    data['assets'] = [asset('A', value=1.7e308), asset('B', value=1.7e308)]
    data['actions'] = [action('a', 'A'), action('b', 'B')]
    with pytest.raises(ValueError):
        plan(data)


def test_duration_bound_propagates_even_when_earlier_fire_bound_is_unknown():
    data = single()
    data['assets'] = [asset('A'), asset('B')]
    data['assets'][1]['deadline_early_min'] = 8
    data['actions'] = [action('first', 'A'), action('next', 'B', requires=['first'])]
    data['actions'][0]['duration_high_min'] = 5
    data['actions'][1]['duration_high_min'] = 3
    rows = tasks(plan(data))
    assert rows[0]['sensitivity']['status'] == 'unknown'
    assert rows[1]['sensitivity']['status'] == 'fragile'
    assert rows[1]['sensitivity']['stress_finish_min'] == 10


def test_prerequisite_stress_propagates_across_crews():
    data = single()
    data['assets'] = [asset('A'), asset('B')]
    data['assets'][0]['deadline_early_min'] = 20
    data['assets'][1]['deadline_early_min'] = 8
    data['teams'] = [team('access', capabilities=['access']), team('rescue', capabilities=['rescue'])]
    data['actions'] = [action('first', 'A', capabilities=['access']),
                       action('next', 'B', requires=['first'], capabilities=['rescue'])]
    data['actions'][0]['duration_high_min'] = 5
    data['actions'][1]['duration_high_min'] = 3
    rows = {r['action_id']: r for r in tasks(plan(data))}
    assert rows['next']['sensitivity']['status'] == 'fragile'
    assert rows['next']['sensitivity']['stress_finish_min'] == 10


def supplied_graph(edges):
    from fireline.routing import RoadGraph
    graph = RoadGraph()
    for index, node in enumerate(sorted({n for edge in edges for n in edge[:2]})):
        graph.add_node(node, 2 + index * .01, 41)
    for source, destination, minutes, cut in edges:
        graph.add_edge(source, destination, 'supplied', length_m=minutes * 1000, speed_kmh=60)
        graph.g[source][destination].update(source='confirmed graph', confirmed=True, safe=True, cut_min=cut)
    return graph


def test_joint_synchronized_departure_cannot_reuse_a_now_closed_shortcut():
    data = single()
    data['teams'] = [team('fast', start='FAST'), team('slow', start='SLOW')]
    data['actions'][0]['required_team_count'] = 2
    data['routes'] = []
    graph = supplied_graph([('FAST', 'A', 1, 3), ('FAST', 'MID', 3, 100),
                            ('MID', 'A', 3, 100), ('SLOW', 'A', 5, 100)])
    result = plan(data, graph=graph)
    assert not tasks(result)
    assert 'synchronized_route_changed' in result['review'][0]['reasons']


def test_stress_does_not_reuse_nominal_travel_time_after_route_changes():
    data = single()
    data['assets'] = [asset('P'), asset('A')]
    data['assets'][0]['deadline_early_min'] = 30
    data['assets'][1]['deadline_early_min'] = 11
    data['actions'] = [action('first', 'P'), action('second', 'A', requires=['first'])]
    data['actions'][0]['duration_high_min'] = 6
    data['actions'][1]['duration_high_min'] = 2
    data['routes'] = []
    graph = supplied_graph([('BASE', 'P', 1, 100), ('P', 'A', 1, 5),
                            ('P', 'MID', 3, 100), ('MID', 'A', 3, 100)])
    rows = {r['action_id']: r for r in tasks(plan(data, graph=graph))}
    assert rows['second']['sensitivity']['status'] == 'fragile'
    assert 'stress_route_changed' in rows['second']['sensitivity']['reasons']


def test_distinct_action_ids_cannot_evacuate_same_population_twice():
    data = evacuation()
    second = deepcopy(data['actions'][0])
    second['action_id'] = 'second'
    data['actions'].append(second)
    result = plan(data)
    assert sum(r['delivered_people'] for r in tasks(result)) == 16
    assert len(tasks(result)) == 1
    assert 'transport_people_exceeds_remaining' in result['unassigned'][0]['reasons']


def test_separate_partial_population_actions_require_disjoint_group_evidence():
    data = evacuation(8)
    data['assets'][0]['people'] = 16
    second = deepcopy(data['actions'][0])
    second['action_id'] = 'second'
    data['actions'].append(second)
    result = plan(data)
    assert sum(r['delivered_people'] for r in tasks(result)) == 8
    assert 'transport_population_overlap_review' in result['unassigned'][0]['reasons']


def test_pickup_only_reserves_population_against_a_second_action():
    data = single()
    data['teams'].append(team('two'))
    data['actions'][0]['transport_people'] = 2
    second = deepcopy(data['actions'][0])
    second.update(action_id='second', effects=[{'asset_id': 'B', 'coverage': 1,
        'confirmed': True, 'source': 'distinct side effect'}])
    data['actions'].append(second)
    result = plan(data)
    assert sum(r['transport_people'] for r in tasks(result)) == 2
    assert 'transport_people_exceeds_remaining' in result['unassigned'][0]['reasons']


def test_zero_gain_redundant_work_is_not_scheduled_without_a_dependency_need():
    data = single()
    data['actions'].append(action('redundant', 'A'))
    result = plan(data)
    assert len(tasks(result)) == 1
    assert 'no_incremental_benefit' in result['unassigned'][0]['reasons']


def test_failed_stress_mission_cannot_enable_robust_downstream_action():
    data = evacuation(8)
    data['actions'][0]['duration_high_min'] = 10
    data['assets'][0]['deadline_early_min'] = 100
    data['routes'][1]['available_until_min'] = 10
    data['assets'].append(asset('B', deadline=100))
    data['assets'][1]['deadline_early_min'] = 100
    data['actions'].append(action('next', 'B', requires=['help-A']))
    data['actions'][1].update(duration_high_min=2, deadline_min=100)
    data['routes'].append(dict(route('SAFE', 'B'), available_until_min=100))
    rows = {r['action_id']: r for r in tasks(plan(data))}
    assert rows['help-A']['sensitivity']['status'] == 'fragile'
    assert rows['next']['sensitivity']['status'] == 'fragile'
    assert 'upstream_stress_infeasible' in rows['next']['sensitivity']['reasons']


def test_pickup_only_cannot_exceed_known_population_even_with_spare_seats():
    data = single()
    data['teams'][0]['transport_capacity'] = 8
    data['actions'][0]['transport_people'] = 3
    result = plan(data)
    assert not tasks(result)
    assert 'transport_people_exceeds_people' in result['unassigned'][0]['reasons']
