"""Public dashboard boundary: no provider calls or mutable operational endpoints."""
from copy import deepcopy

from starlette.testclient import TestClient

from fireline.dashboard_server import create_app, public_state
from fireline.dashboard_demo import DemoStore


def envelope(revision=1):
    return {
        'schema_version': 'coordination-state-1', 'scenario_id': 's',
        'snapshot_id': 'snap', 'revision': revision, 'as_of': '2026-09-20T10:00:00Z',
        'input_mode': 'offline_demo',
        'assets': [{'asset_id': 'a', 'name': 'Unknown occupancy', 'estimated_occupancy': None,
                    'phone': '+34912345678', 'api_key': 'secret',
                    'sources': [{'fields': ['estimated_occupancy'], 'source': 'fixture',
                                 'notes': 'Contact +34 912 345 678'}]}],
        'contacts': {'ranked': [{'asset_id': 'a', 'rank': 1, 'slack_min': -2}], 'review': []},
        'calls': [{'request_id': 'r', 'asset_id': 'a', 'status': 'completed',
                   'needs_assistance': True, 'wants_human': False, 'message_acknowledged': None,
                   'departure_confirmed': None, 'arrival_confirmed': False,
                   'phone_number': '+34912345678', 'payload': {'token': 'secret'},
                   'transcript': 'private words'}],
        'plan': {'locations': [], 'remaining_capacity': {'shelter': None}, 'response': None},
        'teams': [], 'tasks': [], 'events': [], 'errors': [],
    }


class Store:
    def __init__(self):
        self.history = [envelope(n) for n in (1, 2, 3)]

    def state(self):
        return deepcopy(self.history[-1])

    def updates(self, after_revision):
        return deepcopy([x for x in self.history if x['revision'] > after_revision])


def test_projection_preserves_unknown_order_and_independent_facts_without_private_fields():
    state = envelope()
    result = public_state(state)
    assert result['assets'][0]['estimated_occupancy'] is None
    assert result['contacts'] == state['contacts']
    assert result['calls'][0] == {k: v for k, v in state['calls'][0].items()
                                   if k not in ('phone_number', 'payload', 'transcript')}
    assert result['plan']['remaining_capacity'] == {'shelter': None}
    assert result['assets'][0]['sources'][0]['source'] == 'fixture'
    assert '912' not in str(result)
    assert 'secret' not in str(result)
    assert 'private words' not in str(result)
    assert state['assets'][0]['phone'] == '+34912345678'


def test_rest_is_read_only_and_serves_only_dashboard_files():
    with TestClient(create_app(Store())) as client:
        response = client.get('/api/state')
        assert response.status_code == 200
        assert response.json()['revision'] == 3
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/').status_code == 200
        assert client.get('/.env').status_code == 404
        assert client.post('/api/calls').status_code == 404
        assert client.post('/api/state').status_code == 405


def test_websocket_replays_in_order_and_reconnects_from_last_revision():
    with TestClient(create_app(Store(), poll_interval=0.01)) as client:
        with client.websocket_connect('/api/updates?after_revision=0') as ws:
            assert [ws.receive_json()['revision'] for _ in range(3)] == [1, 2, 3]
        with client.websocket_connect('/api/updates?after_revision=2') as ws:
            assert ws.receive_json()['revision'] == 3


def test_history_gap_or_server_restart_sends_current_state():
    store = Store()
    store.history = [envelope(8)]
    with TestClient(create_app(store)) as client:
        for cursor in (2, 99):
            with client.websocket_connect(f'/api/updates?after_revision={cursor}') as ws:
                state = ws.receive_json()
                assert state['revision'] == 8
                assert state['schema_version'] == 'coordination-state-1'


def test_stream_sends_new_state_and_generic_errors_without_exception_payload():
    store = Store()
    with TestClient(create_app(store, poll_interval=0.01)) as client:
        with client.websocket_connect('/api/updates?after_revision=2') as ws:
            assert ws.receive_json()['revision'] == 3
            store.history.append(envelope(4))
            assert ws.receive_json()['revision'] == 4
            def fail(_):
                raise RuntimeError('private provider token secret')
            store.updates = fail
            error = ws.receive_json()
            assert error == {'type': 'error', 'code': 'state_unavailable'}
        store.state = lambda: (_ for _ in ()).throw(RuntimeError('secret'))
        assert client.get('/api/state').json() == {'error': 'state_unavailable'}


def test_cross_origin_websocket_is_rejected():
    from starlette.websockets import WebSocketDisconnect
    import pytest
    with TestClient(create_app(Store())) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/updates', headers={'origin': 'https://evil.test'}):
                pass


def test_demo_implements_identical_store_contract_and_is_explicit():
    store = DemoStore()
    state = store.state()
    assert state['schema_version'] == 'coordination-state-1'
    assert state['input_mode'] == 'offline_demo'
    assert state['calls'] and all('phone' not in str(c) for c in state['calls'])
    assert store.updates(-1)[-1] == state
    assert store.updates(state['revision']) == []


def test_compact_coordination_events_resolve_latest_full_state_and_zero_cursor():
    class CompactStore(Store):
        def updates(self, after_revision=0):
            assert after_revision >= 0
            return [{'schema_version': 'coordination-update-1', 'revision': s['revision'],
                     'refresh': 'full_state'} for s in self.history if s['revision'] > after_revision]
    with TestClient(create_app(CompactStore())) as client:
        with client.websocket_connect('/api/updates') as ws:
            assert ws.receive_json()['revision'] == 3


def test_coordination_call_projection_preserves_reported_assistance_and_provenance():
    state = envelope()
    state['calls'] = [{'request_id': 'r', 'asset_id': 'a', 'status': 'completed',
        'reported_needs_assistance': True, 'wants_human': False, 'acknowledged': None,
        'dispatch_state': 'dispatched', 'transfer_verified': False,
        'provenance': {'source': 'stored_call_assessment', 'evidence_time_basis': 'source_observation'}}]
    assert public_state(state)['calls'] == state['calls']


def test_read_only_database_adapter_never_creates_missing_database(tmp_path):
    import pytest
    import sqlite3
    from fireline.dashboard_server import CoordinationDatabase
    missing = tmp_path / 'missing.sqlite'
    with pytest.raises(sqlite3.OperationalError):
        CoordinationDatabase(missing).state()
    assert not missing.exists()
    database = tmp_path / 'existing.sqlite'
    import json
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE coordination_revisions (revision INTEGER, payload TEXT, event TEXT)')
        conn.execute('INSERT INTO coordination_revisions VALUES (?, ?, ?)',
                     (1, json.dumps(envelope()), json.dumps({'revision': 1, 'refresh': 'full_state'})))
    reader = CoordinationDatabase(database)
    assert reader.state() == envelope()
    assert reader.updates(0) == [{'revision': 1, 'refresh': 'full_state'}]
    with TestClient(create_app(reader)) as client:
        assert client.get('/api/state').json()['revision'] == 1
