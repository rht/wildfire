"""Edited orders must be server validated and remain separate from dispatch."""
from copy import deepcopy
import json

import pytest
from starlette.testclient import TestClient

from fireline.dashboard_approvals import ApprovalStore
from fireline.dashboard_server import create_app
from tests.test_dashboard_approvals import Store, coordination_state, query, post_body


ORIGIN = {'origin': 'http://testserver'}


def review_state():
    state = coordination_state()
    crew = state['plan']['response']['teams'][0]
    crew['planning_context'] = {
        'start': {'node_id': 'BASE', 'available_min': 0, 'name': 'Station',
                  'source': 'synthetic roster', 'latitude': 41.9, 'longitude': 3.0},
        'capabilities': ['medical'], 'transport_capacity': 5, 'available': True,
        'now_min': 0, 'buffer_min': 1, 'horizon_min': 90, 'available_until_min': 90,
        'prerequisites': [],
        'routes': [
            {'from_node': a, 'to_node': b, 'travel_min': minutes,
             'route_source': 'synthetic roads', 'route_status': 'qualified',
             'confirmed': True, 'safe': True, 'available_until_min': 90,
             'path_lonlat': [[3.0, 41.9], [3.1, 41.91]]}
            for a, b, minutes in [('BASE', 'A', 1), ('A', 'B', 2),
                                  ('BASE', 'B', 4), ('B', 'A', 6)]],
    }
    crew['tasks'] = [
        {'action_id': aid, 'asset_id': site, 'to_node': node, 'action': 'assist',
         'status': 'proposed', 'duration_min': duration, 'deadline_min': 80,
         'depart_min': depart, 'travel_min': travel, 'start_min': start,
         'finish_min': finish, 'required_capabilities': ['medical'],
         'prerequisites': [], 'readiness_required': False, 'transport_people': people}
        for aid, site, node, duration, depart, travel, start, finish, people in [
            ('a', 'site-a', 'A', 3, 0, 1, 1, 4, 2),
            ('b', 'site-b', 'B', 5, 4, 2, 6, 11, 1)]]
    return state


def body_for(client, action_ids=('b', 'a')):
    plan = client.get('/api/crew-approvals', params=query()).json()['plans'][0]
    return post_body(plan['plan_version'], action_ids=list(action_ids))


def test_preview_recalculates_exact_legs_and_saved_order_survives_restart(tmp_path):
    state = review_state()
    live = Store(state)
    path = tmp_path / 'approvals.db'
    with TestClient(create_app(live, approvals=ApprovalStore(path))) as client:
        body = body_for(client)
        response = client.post('/api/crew-plan-preview', json=body, headers=ORIGIN)
        assert response.status_code == 200
        preview = response.json()
        assert preview['can_confirm'] is True
        assert preview['blockers'] == []
        assert [(t['action_id'], t['from_node'], t['depart_min'], t['travel_min'],
                 t['start_min'], t['finish_min']) for t in preview['reviewed_plan']['tasks']] == [
            ('b', 'BASE', 0, 4, 4, 9), ('a', 'B', 9, 6, 15, 18)]
        assert preview['reviewed_plan']['remaining_transport_capacity'] == 2
        assert client.get('/api/crew-approvals', params=query()).json()['events'] == []
        saved = client.post('/api/crew-approvals',
                            json=body | {'review_version': preview['review_version']},
                            headers=ORIGIN)
        assert saved.status_code == 200
        saved_plan = saved.json()['plans'][0]
        assert saved_plan['reviewed_plan'] == preview['reviewed_plan']
        assert saved_plan['approval']['review_version'] == preview['review_version']
        assert live.current == state
        assert live.update_calls == 0
    with TestClient(create_app(live, approvals=ApprovalStore(path))) as client:
        assert client.get('/api/crew-approvals', params=query()).json()['plans'][0] == saved_plan


@pytest.mark.parametrize('order', [[], ['a'], ['a', 'a'], ['a', 'unknown']])
def test_preview_rejects_nonpermutations(tmp_path, order):
    with TestClient(create_app(Store(review_state()), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        response = client.post('/api/crew-plan-preview', json=body_for(client, order), headers=ORIGIN)
        assert response.status_code == 422
        assert response.json()['error'] == 'invalid_order'


def test_committed_work_cannot_move_or_have_its_timing_changed(tmp_path):
    state = review_state()
    tasks = state['plan']['response']['teams'][0]['tasks']
    fixed = deepcopy(tasks[0]) | {'action_id': 'fixed', 'status': 'in_progress',
                                 'transport_people': 1}
    tasks.insert(0, fixed)
    state['plan']['response']['teams'][0]['planning_context']['routes'].append({
        'from_node': 'A', 'to_node': 'A', 'travel_min': 0,
        'route_source': 'same supplied node', 'route_status': 'qualified',
        'confirmed': True, 'safe': True, 'available_until_min': 90})
    with TestClient(create_app(Store(state), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        response = client.post('/api/crew-plan-preview',
                               json=body_for(client, ['b', 'fixed', 'a']), headers=ORIGIN)
        assert response.status_code == 422
        legal = client.post('/api/crew-plan-preview',
                            json=body_for(client, ['fixed', 'b', 'a']), headers=ORIGIN).json()
        assert legal['reviewed_plan']['tasks'][0] == fixed
        assert legal['reviewed_plan']['tasks'][1]['depart_min'] == 4
        assert legal['reviewed_plan']['remaining_transport_capacity'] == 1


def test_completed_history_keeps_supplied_current_planning_start(tmp_path):
    state = review_state()
    crew = state['plan']['response']['teams'][0]
    crew['tasks'][0].update(status='completed', actual_finish_min=4)
    completed = deepcopy(crew['tasks'][0])
    crew['tasks'][1]['prerequisites'] = ['a']
    crew['planning_context']['now_min'] = 10
    crew['planning_context']['start']['available_min'] = 10
    with TestClient(create_app(Store(state), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        response = client.post('/api/crew-plan-preview',
                               json=body_for(client, ['a', 'b']), headers=ORIGIN)
        assert response.status_code == 200
        preview = response.json()
        assert preview['can_confirm'] is True
        assert preview['reviewed_plan']['tasks'][0] == completed
        proposed = preview['reviewed_plan']['tasks'][1]
        assert (proposed['from_node'], proposed['depart_min'], proposed['travel_min'],
                proposed['start_min'], proposed['finish_min']) == ('BASE', 10, 4, 14, 19)
        assert preview['reviewed_plan']['remaining_transport_capacity'] == 2


def test_review_deadline_uses_planner_floating_point_tolerance(tmp_path):
    state = review_state()
    crew = state['plan']['response']['teams'][0]
    crew['tasks'] = crew['tasks'][:1]
    crew['tasks'][0].update(duration_min=0.2, deadline_min=0.3)
    crew['planning_context']['routes'][0]['travel_min'] = 0.1
    crew['planning_context']['buffer_min'] = 0
    with TestClient(create_app(Store(state), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        preview = client.post('/api/crew-plan-preview',
                              json=body_for(client, ['a']), headers=ORIGIN).json()
        assert preview['reviewed_plan']['tasks'][0]['finish_min'] == 0.1 + 0.2
        assert preview['can_confirm'] is True
        assert preview['blockers'] == []


@pytest.mark.parametrize('change,code', [
    ('missing_context', 'planning_context_missing'), ('missing_route', 'route_unavailable'),
    ('unsafe_route', 'route_unavailable'), ('expired_route', 'route_unavailable'),
    ('missing_duration', 'task_evidence_missing'), ('missing_deadline', 'task_evidence_missing'),
    ('deadline', 'deadline'), ('capacity', 'transport_capacity'),
    ('capabilities', 'capabilities'), ('prerequisite', 'prerequisites'),
    ('readiness', 'readiness_review'),
])
def test_missing_or_infeasible_evidence_blocks_edited_confirmation(tmp_path, change, code):
    state = review_state()
    crew = state['plan']['response']['teams'][0]
    context = crew['planning_context']
    if change == 'missing_context':
        del crew['planning_context']
    elif change == 'missing_route':
        context['routes'] = []
    elif change == 'unsafe_route':
        context['routes'][2]['safe'] = False
    elif change == 'expired_route':
        context['routes'][2]['available_until_min'] = 4
    elif change.startswith('missing_'):
        del crew['tasks'][0][change.removeprefix('missing_') + '_min']
    elif change == 'deadline':
        crew['tasks'][0]['deadline_min'] = 18
    elif change == 'capacity':
        context['transport_capacity'] = 2
    elif change == 'capabilities':
        context['capabilities'] = []
    elif change == 'prerequisite':
        crew['tasks'][1]['prerequisites'] = ['a']
    elif change == 'readiness':
        crew['tasks'][0]['readiness_required'] = True
    with TestClient(create_app(Store(state), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        body = body_for(client)
        response = client.post('/api/crew-plan-preview', json=body, headers=ORIGIN)
        assert response.status_code == 200
        preview = response.json()
        assert preview['can_confirm'] is False
        assert code in [blocker['code'] for blocker in preview['blockers']]
        rejected = client.post('/api/crew-approvals',
                               json=body | {'review_version': preview['review_version']}, headers=ORIGIN)
        assert rejected.status_code == 422
        assert rejected.json()['error'] == 'plan_not_confirmable'
        assert client.get('/api/crew-approvals', params=query()).json()['events'] == []


def test_preview_binding_rejects_changed_context_revision_and_tampered_order(tmp_path):
    live = Store(review_state())
    with TestClient(create_app(live, approvals=ApprovalStore(tmp_path / 'a'))) as client:
        body = body_for(client)
        preview = client.post('/api/crew-plan-preview', json=body, headers=ORIGIN).json()
        confirmation = body | {'review_version': preview['review_version']}
        assert client.post('/api/crew-approvals',
                           json=confirmation | {'action_ids': ['a', 'b']}, headers=ORIGIN).status_code == 409
        live.current['revision'] = 5
        assert client.post('/api/crew-approvals',
                           json=confirmation | {'revision': 5}, headers=ORIGIN).status_code == 409
        live.current['revision'] = 4
        live.current['plan']['response']['teams'][0]['planning_context']['routes'][2]['travel_min'] = 7
        assert client.post('/api/crew-approvals', json=confirmation, headers=ORIGIN).status_code == 409


def test_new_evidence_invalidates_base_and_private_context_is_withheld(tmp_path):
    live = Store(review_state())
    live.current['plan']['response']['teams'][0]['planning_context']['private_note'] = 'SECRET'
    live.current['plan']['response']['teams'][0]['planning_context']['start']['name'] = 'Station +34612345678'
    with TestClient(create_app(live, approvals=ApprovalStore(tmp_path / 'a'))) as client:
        body = body_for(client)
        preview = client.post('/api/crew-plan-preview', json=body, headers=ORIGIN)
        assert preview.status_code == 200
        assert 'SECRET' not in preview.text
        assert '+34612345678' not in preview.text
        live.current['assets'][0]['burn_probability'] = 0.9
        assert client.post('/api/crew-plan-preview', json=body, headers=ORIGIN).status_code == 409


def test_unvalidated_order_never_reuses_original_timing_or_geometry(tmp_path):
    state = review_state()
    crew = state['plan']['response']['teams'][0]
    del crew['planning_context']
    crew['tasks'][0]['path_lonlat'] = [[3, 41.9], [3.1, 41.91]]
    with TestClient(create_app(Store(state), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        preview = client.post('/api/crew-plan-preview', json=body_for(client), headers=ORIGIN).json()
        assert preview['can_confirm'] is False
        assert all('finish_min' not in task and 'path_lonlat' not in task
                   for task in preview['reviewed_plan']['tasks'])


def test_changed_order_cannot_overrun_a_fixed_later_departure(tmp_path):
    state = review_state()
    tasks = state['plan']['response']['teams'][0]['tasks']
    tasks.append(deepcopy(tasks[0]) | {'action_id': 'fixed', 'status': 'informed',
                                      'from_node': 'B', 'depart_min': 12, 'start_min': 13,
                                      'finish_min': 15, 'transport_people': 0})
    with TestClient(create_app(Store(state), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        preview = client.post('/api/crew-plan-preview',
                              json=body_for(client, ['b', 'a', 'fixed']), headers=ORIGIN).json()
        assert preview['can_confirm'] is False
        assert 'committed_conflict' in [b['code'] for b in preview['blockers']]


def test_cross_team_unfinished_dependency_requires_coordinated_replan(tmp_path):
    state = review_state()
    state['plan']['response']['teams'][1]['tasks'][0].update(
        status='proposed', prerequisites=['a'], depart_min=5)
    with TestClient(create_app(Store(state), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        preview = client.post('/api/crew-plan-preview', json=body_for(client), headers=ORIGIN).json()
        assert preview['can_confirm'] is False
        assert 'cross_team_prerequisites' in [b['code'] for b in preview['blockers']]


def test_new_saved_review_invalidates_other_tabs_preview_but_duplicate_is_idempotent(tmp_path):
    with TestClient(create_app(Store(review_state()), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        reverse = body_for(client)
        original = body_for(client, ['a', 'b'])
        previews = [client.post('/api/crew-plan-preview', json=body, headers=ORIGIN).json()
                    for body in (reverse, original)]
        first = reverse | {'review_version': previews[0]['review_version']}
        saved = client.post('/api/crew-approvals', json=first, headers=ORIGIN)
        assert saved.status_code == 200
        assert client.post('/api/crew-approvals', json=first, headers=ORIGIN).json() == saved.json()
        stale = client.post('/api/crew-approvals',
                            json=original | {'review_version': previews[1]['review_version']}, headers=ORIGIN)
        assert stale.status_code == 409


@pytest.mark.parametrize('change', ['call_assistance', 'mobility', 'risk_score', 'ordering_reason'])
def test_all_displayed_review_evidence_is_bound_to_base_version(tmp_path, change):
    live = Store(review_state())
    with TestClient(create_app(live, approvals=ApprovalStore(tmp_path / 'a'))) as client:
        body = body_for(client)
        if change == 'call_assistance':
            live.current['calls'][0]['can_self_evacuate'] = False
        elif change == 'ordering_reason':
            live.current['plan']['response']['teams'][0]['tasks'][0]['ordering_reason'] = 'Updated reason'
        else:
            live.current['assets'][0][change] = 'Supplied limited mobility' if change == 'mobility' else 90
        assert client.post('/api/crew-plan-preview', json=body, headers=ORIGIN).status_code == 409


@pytest.mark.parametrize('source', ['', '  ', [], ['  '], [123]])
def test_route_without_usable_provenance_cannot_validate(tmp_path, source):
    state = review_state()
    state['plan']['response']['teams'][0]['planning_context']['routes'][2]['route_source'] = source
    with TestClient(create_app(Store(state), approvals=ApprovalStore(tmp_path / 'a'))) as client:
        preview = client.post('/api/crew-plan-preview', json=body_for(client), headers=ORIGIN).json()
        assert preview['can_confirm'] is False
        assert 'route_unavailable' in [b['code'] for b in preview['blockers']]
