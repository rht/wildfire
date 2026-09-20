"""Saved analyst confirmation stays version-bound and separate from coordination."""
from copy import deepcopy
import json
import sys

import pytest
from starlette.testclient import TestClient

from fireline.dashboard_approvals import ApprovalStore, plans_for
from fireline.dashboard_server import create_app


def coordination_state(*, revision=4, snapshot_id='snapshot-4'):
    return {
        'schema_version': 'coordination-state-1',
        'scenario_id': 'incident-a',
        'incident_id': 'incident-a',
        'snapshot_id': snapshot_id,
        'revision': revision,
        'as_of': '2026-09-20T10:00:00Z',
        'epoch': '2026-09-20T09:00:00Z',
        'input_mode': 'synthetic',
        'assets': [
            {'asset_id': 'site-a', 'name': 'Care home', 'latitude': 41.9,
             'longitude': 3.0, 'private_note': 'resident names'},
        ],
        'contacts': {'ranked': [], 'review': []},
        'calls': [{'request_id': 'private-call', 'asset_id': 'site-a',
                   'transcript': 'private call words'}],
        'teams': [
            {'team_id': 'crew-1', 'name': 'Crew one', 'available': True},
            {'team_id': 'crew-2', 'name': 'Crew two', 'available': True},
        ],
        'tasks': [],
        'events': [],
        'errors': [],
        'plan': {
            'locations': [{'asset_id': 'site-a', 'destination_id': 'hall',
                           'destination_name': 'North hall'}],
            'remaining_capacity': {},
            'response': {'teams': [
                {'team_id': 'crew-1', 'locked': False, 'tasks': [{
                    'action_id': 'assist-a', 'asset_id': 'site-a',
                    'action': 'assisted_evacuation', 'status': 'proposed',
                    'action_version': 'action-v1',
                    'start_min': 3, 'finish_min': 12,
                    'prerequisites': ['transport_confirmed'],
                    'private_instruction': 'private route words',
                }]},
                {'team_id': 'crew-2', 'tasks': [{
                    'action_id': 'check-a', 'asset_id': 'site-a',
                    'status': 'completed', 'finish_min': 2,
                }]},
            ]},
        },
    }


class Store:
    def __init__(self, state=None):
        self.current = state or coordination_state()
        self.update_calls = 0

    def state(self):
        return deepcopy(self.current)

    def updates(self, after_revision):
        self.update_calls += 1
        raise AssertionError('approval requests must not start coordination work')


def query(**changes):
    return {
        'source': 'connected',
        'incident_id': 'incident-a',
        'revision': '4',
        'snapshot_id': 'snapshot-4',
    } | changes


def post_body(plan_version, **changes):
    return {
        'source': 'connected',
        'incident_id': 'incident-a',
        'revision': 4,
        'snapshot_id': 'snapshot-4',
        'team_id': 'crew-1',
        'plan_version': plan_version,
    } | changes


def version(state):
    return plans_for(
        state, source='connected', incident_id='incident-a',
        revision=state['revision'], snapshot_id=state['snapshot_id'],
    )[0]['plan_version']


def test_plan_version_binds_authoritative_action_timing_and_source_context():
    original = coordination_state()
    baseline = version(original)

    action_changed = deepcopy(original)
    action_changed['plan']['response']['teams'][0]['tasks'][0]['action_version'] = 'action-v2'
    epoch_changed = deepcopy(original)
    epoch_changed['epoch'] = '2026-09-20T09:05:00Z'
    mode_changed = deepcopy(original)
    mode_changed['input_mode'] = 'live'

    assert version(action_changed) != baseline
    assert version(epoch_changed) != baseline
    assert version(mode_changed) != baseline


def test_confirmation_persists_is_idempotent_and_never_mutates_coordination(tmp_path):
    state = coordination_state()
    live = Store(state)
    database = tmp_path / 'approvals.sqlite3'
    app = create_app(live, approvals=ApprovalStore(database), analyst='@analyst')
    with TestClient(app) as client:
        initial = client.get('/api/crew-approvals', params=query())
        assert initial.status_code == 200
        envelope = initial.json()
        assert envelope == {
            'source': 'connected', 'incident_id': 'incident-a', 'revision': 4,
            'snapshot_id': 'snapshot-4', 'analyst': '@analyst',
            'plans': [
                {'team_id': 'crew-1', 'plan_version': envelope['plans'][0]['plan_version'],
                 'can_confirm': True, 'approval': None},
                {'team_id': 'crew-2', 'plan_version': envelope['plans'][1]['plan_version'],
                 'can_confirm': False, 'approval': None},
            ],
            'events': [],
        }
        version = envelope['plans'][0]['plan_version']
        assert len(version) == 64

        confirmed = client.post('/api/crew-approvals', json=post_body(version),
                                headers={'origin': 'http://testserver'})
        duplicate = client.post('/api/crew-approvals', json=post_body(version),
                                headers={'origin': 'http://testserver'})

    assert confirmed.status_code == duplicate.status_code == 200
    assert duplicate.json() == confirmed.json()
    approval = confirmed.json()['plans'][0]['approval']
    assert set(approval) == {'approval_id', 'analyst', 'confirmed_at', 'plan_version'}
    assert approval['analyst'] == '@analyst'
    assert approval['plan_version'] == version
    assert confirmed.json()['events'] == [{
        'event_id': approval['approval_id'],
        'as_of': approval['confirmed_at'],
        'kind': 'plan_confirmed',
        'notes': 'Crew plan confirmed by @analyst',
        'source': 'connected',
        'team_id': 'crew-1',
        'incident_id': 'incident-a',
    }]
    assert live.current == state
    assert live.update_calls == 0
    assert 'private' not in json.dumps(confirmed.json()).lower()

    reloaded = create_app(live, approvals=ApprovalStore(database), analyst='@analyst')
    with TestClient(reloaded) as client:
        assert client.get('/api/crew-approvals', params=query()).json() == confirmed.json()
        live.current['revision'] = 5
        refreshed = client.get('/api/crew-approvals', params=query(revision='5')).json()
        assert refreshed['revision'] == 5
        assert refreshed['plans'][0]['plan_version'] == version
        assert refreshed['plans'][0]['approval'] == approval
        live.current['plan']['response']['teams'][0]['tasks'][0]['start_min'] = 4
        changed = client.get('/api/crew-approvals', params=query(revision='5')).json()
        assert changed['plans'][0]['plan_version'] != version
        assert changed['plans'][0]['approval'] is None
        assert changed['events'] == confirmed.json()['events']


def test_stale_render_and_changed_plan_are_rejected_without_reusing_approval(tmp_path):
    live = Store()
    approvals = ApprovalStore(tmp_path / 'approvals.sqlite3')
    with TestClient(create_app(live, approvals=approvals)) as client:
        current = client.get('/api/crew-approvals', params=query()).json()
        old_version = current['plans'][0]['plan_version']
        assert client.get('/api/crew-approvals', params=query(revision='3')).status_code == 409
        assert client.get('/api/crew-approvals', params=query(snapshot_id='old')).json() == {
            'error': 'plan_changed'}

        live.current['plan']['response']['teams'][0]['tasks'][0]['finish_min'] = 13
        stale = client.post('/api/crew-approvals', json=post_body(old_version),
                            headers={'origin': 'http://testserver'})
        assert stale.status_code == 409
        assert stale.json() == {'error': 'plan_changed'}
        changed = client.get('/api/crew-approvals', params=query()).json()
        assert changed['plans'][0]['plan_version'] != old_version
        assert changed['plans'][0]['approval'] is None

        live.current['plan']['locations'][0]['destination_name'] = 'South hall'
        context_changed = client.get('/api/crew-approvals', params=query()).json()
        assert context_changed['plans'][0]['plan_version'] != changed['plans'][0]['plan_version']

        live.current['revision'] = 5
        assert client.get('/api/crew-approvals', params=query()).status_code == 409


def test_design_demo_is_server_verified_and_isolated_from_connected_source(tmp_path, monkeypatch):
    frontend = tmp_path / 'dist'
    assets = frontend / 'assets'
    assets.mkdir(parents=True)
    demo = coordination_state()
    demo['id'] = demo['incident_id'] = 'incident-a'
    (assets / 'design-demo.json').write_text(json.dumps([demo]), encoding='utf-8')
    monkeypatch.setattr('fireline.dashboard_server.FRONTEND_DIST', frontend)
    approvals = ApprovalStore(tmp_path / 'approvals.sqlite3')
    app = create_app(Store(), approvals=approvals, allow_demo_approvals=True)
    demo_query = query(source='design_demo')
    with TestClient(app) as client:
        connected = client.get('/api/crew-approvals', params=query()).json()
        design = client.get('/api/crew-approvals', params=demo_query).json()
        assert connected['plans'][0]['plan_version'] != design['plans'][0]['plan_version']
        body = post_body(design['plans'][0]['plan_version'], source='design_demo')
        confirmed = client.post('/api/crew-approvals', json=body,
                                headers={'origin': 'http://testserver'})
        assert confirmed.status_code == 200
        assert confirmed.json()['plans'][0]['approval'] is not None
        assert client.get('/api/crew-approvals', params=query()).json()['plans'][0]['approval'] is None

    disabled = create_app(Store(), approvals=approvals, allow_demo_approvals=False)
    with TestClient(disabled) as client:
        response = client.get('/api/crew-approvals', params=demo_query)
        assert response.status_code == 403
        assert response.json() == {'error': 'source_not_allowed'}


def test_write_boundary_rejects_origins_media_types_extra_fields_and_tampering(tmp_path):
    approvals = ApprovalStore(tmp_path / 'approvals.sqlite3')
    app = create_app(Store(), approvals=approvals)
    with TestClient(app) as client:
        version = client.get('/api/crew-approvals', params=query()).json()['plans'][0]['plan_version']
        body = post_body(version)
        for origin in (None, 'https://evil.test', 'http://testserver.evil'):
            headers = {} if origin is None else {'origin': origin}
            response = client.post('/api/crew-approvals', json=body, headers=headers)
            assert response.status_code == 403
            assert response.json() == {'error': 'origin_not_allowed'}
        response = client.post('/api/crew-approvals', content=json.dumps(body),
                               headers={'origin': 'http://testserver',
                                        'content-type': 'text/plain'})
        assert response.status_code == 415
        assert response.json() == {'error': 'json_required'}
        for invalid in (
            body | {'analyst': '@attacker'},
            body | {'plan': {'dispatch': True}},
            body | {'revision': True},
            body | {'team_id': ''},
        ):
            response = client.post('/api/crew-approvals', json=invalid,
                                   headers={'origin': 'http://testserver'})
            assert response.status_code == 422
            assert response.json() == {'error': 'invalid_request'}
        tampered = client.post('/api/crew-approvals', json=post_body('0' * 64),
                               headers={'origin': 'http://testserver'})
        assert tampered.status_code == 409
        assert tampered.json() == {'error': 'plan_changed'}
        assert client.get('/api/crew-approvals', params=query()).json()['events'] == []


def test_missing_or_nonproposed_team_cannot_be_confirmed_and_disabled_store_is_503(tmp_path):
    app = create_app(Store(), approvals=ApprovalStore(tmp_path / 'approvals.sqlite3'))
    with TestClient(app) as client:
        envelope = client.get('/api/crew-approvals', params=query()).json()
        completed = next(plan for plan in envelope['plans'] if plan['team_id'] == 'crew-2')
        for team_id, version in (('crew-2', completed['plan_version']),
                                 ('missing', completed['plan_version'])):
            response = client.post('/api/crew-approvals',
                                   json=post_body(version, team_id=team_id),
                                   headers={'origin': 'http://testserver'})
            assert response.status_code == 422
            assert response.json() == {'error': 'plan_not_confirmable'}

    with TestClient(create_app(Store())) as client:
        response = client.get('/api/crew-approvals', params=query())
        assert response.status_code == 503
        assert response.json() == {'error': 'approval_unavailable'}


def test_cli_refuses_to_share_the_operational_database(tmp_path, monkeypatch, capsys):
    from fireline.dashboard_server import main

    database = tmp_path / 'coordination.sqlite3'
    monkeypatch.setattr(sys, 'argv', [
        'dashboard-server', '--database', str(database),
        '--approvals-database', str(database),
    ])

    with pytest.raises(SystemExit) as stopped:
        main()

    assert stopped.value.code == 2
    assert 'must differ' in capsys.readouterr().err
    assert not database.exists()
