"""Offline behavior contracts for the additive fleet proposal, not real dispatch."""

from copy import deepcopy
import importlib
import json
import subprocess
import sys

import pytest


def asset(key, people=2, assisted=1, value=10, deadline=30):
    return dict(asset_id=key, node_id=key, people=people, assisted=assisted,
                value=value, deadline_min=deadline)


def action(key, site, *, requires=(), transport=0, capabilities=(), effects=None):
    return dict(action_id=key, asset_id=site, duration_min=2, deadline_min=30,
                requires=list(requires), capabilities=list(capabilities),
                transport_people=transport, readiness_required=False,
                effects=effects if effects is not None else [
                    dict(asset_id=site, coverage=1, confirmed=True, source='synthetic assumption')])


def team(key, start='BASE', capacity=2, capabilities=()):
    return dict(team_id=key, start_node_id=start, available=True, available_from_min=0,
                available_until_min=30, transport_capacity=capacity,
                capabilities=list(capabilities))


def route(a, b, minutes=1, **kwargs):
    return dict(from_node=a, to_node=b, minutes=minutes, confirmed=True, safe=True,
                available_until_min=30, source='synthetic route', **kwargs)


def scene():
    nodes = ['BASE', 'A', 'B', 'C']
    return dict(schema_version='multi-response-input-1', scenario_id='synthetic',
                snapshot_id='s1', now_min=0, horizon_min=30, buffer_min=0,
                assets=[asset('A', assisted=2), asset('B'), asset('C', assisted=0, value=999)],
                teams=[team('truck-1'), team('truck-2')],
                actions=[action('help-A', 'A', transport=2), action('help-B', 'B', transport=2),
                         action('protect-C', 'C')],
                routes=[route(a, b) for a in nodes for b in nodes if a != b],
                readiness=[], committed=[])


def plan(data, **kwargs):
    # Keeping import inside the call makes the initial red test a missing-behavior assertion.
    assert importlib.util.find_spec('fireline.multi_response'), 'multi-team planner is missing'
    return importlib.import_module('fireline.multi_response').plan_multi_response(data, **kwargs)


def tasks(result):
    return [t for row in result['teams'] for t in row['tasks']]


def test_two_trucks_parallel_capacity_no_double_assignment_and_assisted_first():
    data = scene()
    original = deepcopy(data)
    result = plan(data)
    rows = {t['action_id']: t for t in tasks(result)}
    assert rows['help-A']['team_id'] != rows['help-B']['team_id']
    assert rows['help-A']['start_min'] == rows['help-B']['start_min'] == 1
    assert rows['protect-C']['start_min'] == 4
    assert len(rows) == len(tasks(result)) == 3
    assert result['objective'] == dict(assisted_units=3, people_units=6, value_units=1019)
    assert all(t['remaining_transport_capacity'] == 0 for t in result['teams'])
    assert all(t['status'] == 'proposed' and 'path_lonlat' not in t for t in tasks(result))
    assert result['optimal'] is False and result['dispatch'] is False
    assert data == original


def test_shared_prerequisite_finishes_before_work_on_both_teams():
    data = scene()
    data['actions'] = [action('access', 'C', effects=[], capabilities=['access']),
                       action('help-A', 'A', requires=['access']),
                       action('help-B', 'B', requires=['access'])]
    data['teams'][0]['capabilities'] = ['access']
    result = plan(data)
    rows = {t['action_id']: t for t in tasks(result)}
    assert len(rows) == len(tasks(result)) == 3
    assert rows['access']['team_id'] == 'truck-1'
    assert rows['help-A']['start_min'] >= rows['access']['finish_min']
    assert rows['help-B']['start_min'] >= rows['access']['finish_min']
    assert rows['help-A']['prerequisites'] == ['access']
    assert rows['help-A']['effects'] == data['actions'][1]['effects']


@pytest.mark.parametrize('change,reason', [
    ('capacity', 'transport_capacity'), ('capability', 'capabilities'),
    ('deadline', 'deadline'), ('unavailable', 'team_unavailable'),
    ('unsafe', 'route_unavailable'), ('expired', 'route_unavailable'),
    ('missing', 'route_unavailable'), ('unknown', 'unknown_needs'),
])
def test_infeasible_work_has_explicit_reason(change, reason):
    data = scene()
    data['actions'] = data['actions'][:1]
    if change == 'capacity':
        data['actions'][0]['transport_people'] = 3
    elif change == 'capability':
        data['actions'][0]['capabilities'] = ['medical']
    elif change == 'deadline':
        data['assets'][0]['deadline_min'] = 2
    elif change == 'unavailable':
        for row in data['teams']:
            row['available'] = False
    elif change in ('unsafe', 'expired', 'missing'):
        data['routes'] = [route('BASE', 'A')]
        if change == 'unsafe':
            data['routes'][0]['safe'] = False
        elif change == 'expired':
            data['routes'][0]['available_until_min'] = 1
        else:
            data['routes'] = []
    else:
        data['assets'][0]['assisted'] = None
    result = plan(data)
    assert not tasks(result)
    assert reason in result['unassigned'][0]['reasons']


def test_actual_readiness_is_required_and_unknown_never_becomes_zero():
    data = scene()
    data['actions'] = data['actions'][:1]
    data['actions'][0]['readiness_required'] = True
    assert not tasks(plan(data))
    outcome = dict(asset_id='A', status='assistance_required', observed_min=0,
                   valid_until_min=10, source='actual-call-normalizer', request_id='req-A')
    data['readiness'] = [outcome]
    result = plan(data)
    assert tasks(result)[0]['readiness'] == outcome
    for status in ['unknown', 'no_answer', 'self_evacuating', 'completed']:
        data['readiness'][0]['status'] = status
        assert not tasks(plan(data))
    data['readiness'][0].update(status='assistance_required', valid_until_min=0)
    assert not tasks(plan(data))


def test_declared_effects_are_max_coverage_not_transitive_or_double_counted():
    data = scene()
    data['actions'] = [action('one', 'A'), action('two', 'B', effects=[
        dict(asset_id='A', coverage=1, confirmed=True, source='explicit assumption')])]
    result = plan(data)
    assert result['coverage'] == {'A': 1, 'B': 0, 'C': 0}
    assert result['objective']['assisted_units'] == 2


def test_more_than_eight_actions_is_additive_and_deterministic():
    data = scene()
    data['assets'] = [asset(str(i)) for i in range(10)]
    data['actions'] = [action('a'+str(i), str(i)) for i in range(10)]
    nodes = ['BASE'] + [str(i) for i in range(10)]
    data['routes'] = [route(a, b) for a in nodes for b in nodes if a != b]
    first = plan(data)
    assert len(tasks(first)) == 10
    for key in ('teams', 'assets', 'actions', 'routes'):
        data[key].reverse()
    assert plan(data) == first


def test_route_graph_reuses_cut_times_and_only_exports_supplied_geometry():
    from fireline.routing import RoadGraph
    graph = RoadGraph()
    graph.add_node('BASE', 2.0, 41.0)
    graph.add_node('MID', 2.1, 41.1)
    graph.add_node('A', 2.2, 41.2)
    for u, v in [('BASE', 'MID'), ('MID', 'A')]:
        graph.add_edge(u, v, 'synthetic', length_m=1000, speed_kmh=60)
        graph.g[u][v].update(source='synthetic geometry', confirmed=True, safe=True, cut_min=20)
    data = scene()
    data['actions'] = data['actions'][:1]
    data['routes'] = []
    assert 'path_lonlat' not in tasks(plan(data, graph=graph))[0]
    graph.g['BASE']['MID']['geometry_lonlat'] = [[2.0, 41.0], [2.05, 41.07], [2.1, 41.1]]
    graph.g['MID']['A']['geometry_lonlat'] = [[2.1, 41.1], [2.2, 41.2]]
    row = tasks(plan(data, graph=graph))[0]
    assert row['travel_min'] == 2
    assert row['path_lonlat'] == [[2.0, 41.0], [2.05, 41.07], [2.1, 41.1], [2.2, 41.2]]
    graph.g['MID']['A']['cut_min'] = 2
    assert not tasks(plan(data, graph=graph))
    assert graph.g['MID']['A']['closed'] is False


@pytest.mark.parametrize('mutation', ['duplicate', 'cycle', 'nan', 'bool_capacity', 'unknown_target'])
def test_invalid_input_rejected(mutation):
    data = scene()
    if mutation == 'duplicate':
        data['teams'].append(data['teams'][0])
    elif mutation == 'cycle':
        data['actions'][0]['requires'] = ['help-A']
    elif mutation == 'nan':
        data['routes'][0]['minutes'] = float('nan')
    elif mutation == 'bool_capacity':
        data['teams'][0]['transport_capacity'] = True
    else:
        data['actions'][0]['effects'][0]['asset_id'] = 'missing'
    with pytest.raises(ValueError):
        plan(data)


def committed_scene(status='en_route'):
    data = scene()
    first = tasks(plan(data))[0]
    first['status'] = status
    data['committed'] = [first]
    data['now_min'] = 1
    return data, first


@pytest.mark.parametrize('status', ['informed', 'en_route', 'in_progress'])
def test_active_assignments_retained_and_team_locked_until_actual_completion(status):
    data, first = committed_scene(status)
    result = plan(data)
    assigned = [t for t in tasks(result) if t['action_id'] == first['action_id']]
    assert len(assigned) == 1
    assert assigned[0]['status'] == status
    assert assigned[0]['depart_min'] == first['depart_min']
    truck = next(t for t in result['teams'] if t['team_id'] == first['team_id'])
    assert truck['locked'] is True and len(truck['tasks']) == 1
    assert result['coverage']['A'] == 0  # En route is not an actual delivered benefit.
    assert any(t['action_id'] == 'help-B' for t in tasks(result))
    assert any(r['reason'] == 'active_commitment' for r in result['review'])


@pytest.mark.parametrize('change,reason', [
    ('lost', 'team_unavailable'), ('stale', 'stale_snapshot'),
    ('changed', 'changed_action'), ('removed', 'missing_action'),
    ('late', 'overdue_commitment'), ('unsafe', 'route_unavailable'),
])
def test_replanning_preserves_bad_commitments_and_does_not_reassign_them(change, reason):
    data, first = committed_scene()
    if change == 'lost':
        data['teams'][0]['available'] = False
    elif change == 'stale':
        data['snapshot_id'] = 's2'
    elif change == 'changed':
        data['actions'][0]['duration_min'] = 5
    elif change == 'removed':
        data['actions'] = data['actions'][1:]
    elif change == 'late':
        data['now_min'] = 10
    else:
        data['routes'] = []
    result = plan(data)
    matches = [t for t in tasks(result) if t['action_id'] == first['action_id']]
    assert len(matches) == 1 and matches[0]['status'] == 'en_route'
    assert matches[0]['finish_min'] == first['finish_min']
    assert any(reason in r.get('reasons', []) for r in result['review'])


def test_completion_unlocks_prerequisite_and_consumes_capacity_without_repeating_work():
    data, first = committed_scene('completed')
    data['now_min'] = 4
    data['actions'][1]['requires'] = [first['action_id']]
    result = plan(data)
    assert result['coverage']['A'] == 1
    assert len([t for t in tasks(result) if t['action_id'] == 'help-A']) == 1
    help_b = next(t for t in tasks(result) if t['action_id'] == 'help-B')
    assert help_b['start_min'] >= 4
    assert help_b['team_id'] != first['team_id']


def test_new_incident_uses_free_team_while_old_committed_task_is_preserved():
    data, first = committed_scene()
    data['assets'].append(asset('D', assisted=2))
    data['actions'].append(action('help-D', 'D', transport=2))
    data['routes'].append(route('BASE', 'D'))
    data['snapshot_id'] = 'new-incident-snapshot'
    result = plan(data)
    emergency = next(t for t in tasks(result) if t['action_id'] == 'help-D')
    assert emergency['team_id'] != first['team_id']
    assert next(t for t in tasks(result) if t['action_id'] == 'help-A')['snapshot_id'] == 's1'


def test_committed_unknown_team_is_retained_for_review_and_duplicate_commitment_rejected():
    data, first = committed_scene()
    data['teams'] = data['teams'][1:]
    result = plan(data)
    assert next(t for t in tasks(result) if t['action_id'] == 'help-A')['team_id'] == 'truck-1'
    assert any('missing_team' in r.get('reasons', []) for r in result['review'])
    data['committed'].append(first)
    with pytest.raises(ValueError, match='duplicate'):
        plan(data)


def test_public_output_does_not_forward_private_extra_fields():
    data, first = committed_scene()
    first['private_call_payload'] = {'contact_number': 'PRIVATE_SENTINEL'}
    first['effects'][0]['raw_transcript'] = 'PRIVATE_SENTINEL'
    data['actions'][1]['effects'][0]['private'] = 'PRIVATE_SENTINEL'
    data['readiness'] = [dict(asset_id='B', status='assistance_required', observed_min=0,
                             valid_until_min=20, source='normalized-call', request_id='req-B',
                             contact_number='PRIVATE_SENTINEL')]
    assert 'PRIVATE_SENTINEL' not in json.dumps(plan(data))


def test_cli_emits_offline_json_plan_and_rejects_invalid_input(tmp_path):
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(scene()))
    proc = subprocess.run([sys.executable, '-m', 'fireline.multi_response', str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert len(tasks(json.loads(proc.stdout))) == 3
    path.write_text('{"schema_version":"wrong"}')
    proc = subprocess.run([sys.executable, '-m', 'fireline.multi_response', str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    assert not proc.stdout and 'invalid multi-response input' in proc.stderr


def test_synthetic_demo_contains_two_trucks_shared_access_and_three_explicit_rejections():
    from pathlib import Path
    data = json.loads((Path(__file__).parent / 'fixtures/multi_response_demo.json').read_text())
    result = plan(data)
    rows = {t['action_id']: t for t in tasks(result)}
    assert set(rows) == {'open-access', 'help-A', 'help-B'}
    assert rows['help-A']['team_id'] != rows['help-B']['team_id']
    reasons = {r['action_id']: r['reasons'] for r in result['unassigned']}
    assert 'route_unavailable' in reasons['protect-D']
    assert 'deadline' in reasons['protect-E']
    assert 'transport_capacity' in reasons['help-F']
