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


def test_projection_preserves_snapshot_valuation_and_criticality_fields():
    state = envelope()
    state['assets'][0].update({
        'value_score': 0.8,
        'value_basis': 'operational policy v1',
        'replacement_value_eur': 750000,
        'replacement_value_basis': 'assumed school replacement cost',
        'expected_loss_eur_low': 45000,
        'expected_loss_eur_mid': 112500,
        'expected_loss_eur_high': 225000,
        'people_exposed': 24.5,
        'people_at_risk_p10': 0,
        'people_at_risk_p50': 49,
        'criticality_tier': 'high',
        'criticality_factors': ['sole_local_service', 'high_occupancy'],
        'criticality_basis': 'analyst-confirmed override',
        'valuation_private_note': 'do not publish',
    })

    projected = public_state(state)['assets'][0]

    assert projected == {
        'asset_id': 'a',
        'name': 'Unknown occupancy',
        'estimated_occupancy': None,
        'sources': [{'fields': ['estimated_occupancy'], 'source': 'fixture',
                     'notes': 'Contact [redacted contact]'}],
        'value_score': 0.8,
        'value_basis': 'operational policy v1',
        'replacement_value_eur': 750000,
        'replacement_value_basis': 'assumed school replacement cost',
        'expected_loss_eur_low': 45000,
        'expected_loss_eur_mid': 112500,
        'expected_loss_eur_high': 225000,
        'people_exposed': 24.5,
        'people_at_risk_p10': 0,
        'people_at_risk_p50': 49,
        'criticality_tier': 'high',
        'criticality_factors': ['sole_local_service', 'high_occupancy'],
        'criticality_basis': 'analyst-confirmed override',
    }


def test_rest_is_read_only_and_serves_only_dashboard_files(tmp_path, monkeypatch):
    frontend_dist = tmp_path / 'dist'
    frontend_dist.mkdir()
    (frontend_dist / 'index.html').write_text('<main>React dashboard</main>', encoding='utf-8')
    monkeypatch.setattr('fireline.dashboard_server.FRONTEND_DIST', frontend_dist)
    with TestClient(create_app(Store())) as client:
        response = client.get('/api/state')
        assert response.status_code == 200
        assert response.json()['revision'] == 3
        assert response.headers['cache-control'] == 'no-store'
        page = client.get('/')
        assert page.status_code == 200
        assert page.text == '<main>React dashboard</main>'
        assert page.headers['content-type'].startswith('text/html')
        assert client.get('/.env').status_code == 404
        assert client.post('/api/calls').status_code == 404
        assert client.post('/api/state').status_code == 405


def test_missing_frontend_build_returns_helpful_plain_text_503(tmp_path, monkeypatch):
    monkeypatch.setattr('fireline.dashboard_server.FRONTEND_DIST', tmp_path / 'missing-dist')

    with TestClient(create_app(Store())) as client:
        response = client.get('/')

    assert response.status_code == 503
    assert response.headers['content-type'].startswith('text/plain')
    assert 'npm --prefix frontend ci' in response.text
    assert 'npm --prefix frontend run build' in response.text


def test_built_assets_have_content_types_and_cannot_escape_asset_directory(tmp_path, monkeypatch):
    frontend_dist = tmp_path / 'dist'
    assets = frontend_dist / 'assets'
    assets.mkdir(parents=True)
    (frontend_dist / 'index.html').write_text('private index marker', encoding='utf-8')
    (frontend_dist / 'outside.js').write_text('private sibling marker', encoding='utf-8')
    (assets / 'app.js').write_text('window.dashboard = true;', encoding='utf-8')
    (assets / 'app.css').write_text('body { color: navy; }', encoding='utf-8')
    monkeypatch.setattr('fireline.dashboard_server.FRONTEND_DIST', frontend_dist)

    with TestClient(create_app(Store())) as client:
        script = client.get('/assets/app.js')
        stylesheet = client.get('/assets/app.css')
        traversal = client.get('/assets/%2e%2e/outside.js')
        nested_traversal = client.get('/assets/nested/%2e%2e/%2e%2e/index.html')

    assert script.status_code == 200
    assert script.text == 'window.dashboard = true;'
    assert script.headers['content-type'].startswith(('text/javascript', 'application/javascript'))
    assert stylesheet.status_code == 200
    assert stylesheet.headers['content-type'].startswith('text/css')
    assert traversal.status_code == 404
    assert nested_traversal.status_code == 404
    assert 'private sibling marker' not in traversal.text
    assert 'private index marker' not in nested_traversal.text


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


def test_public_plan_projection_preserves_assistance_review_without_private_notes():
    state = envelope()
    state['plan']['locations'] = [{
        'asset_id': 'a',
        'assistance_review_required': True,
        'reported_needs_assistance': True,
        'private_assistance_note': 'private household details',
    }]

    location = public_state(state)['plan']['locations'][0]

    assert location == {
        'asset_id': 'a',
        'assistance_review_required': True,
        'reported_needs_assistance': True,
    }


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


def test_public_projection_redacts_formatted_phones_but_preserves_stable_numeric_ids():
    state = envelope()
    state['assets'][0].update(asset_id='123456789', name='First')
    state['assets'][0]['sources'][0]['notes'] = 'Call 912 345 678 or (212) 555-1234'
    state['contacts']['ranked'][0]['asset_id'] = '123456789'
    projected = public_state(state)
    assert projected['assets'][0]['asset_id'] == '123456789'
    assert projected['contacts']['ranked'][0]['asset_id'] == '123456789'
    notes = projected['assets'][0]['sources'][0]['notes']
    assert '912' not in notes and '555' not in notes
    assert projected['as_of'] == state['as_of']


def test_verified_coordination_export_keeps_readiness_and_response_timings():
    import json
    from pathlib import Path
    state = json.loads(Path('fixtures/dashboard/coordination-export.json').read_text(encoding='utf-8'))
    projected = public_state(state)
    assert projected['plan']['locations'][0]['evacuation_status'] == 'not_confirmed'
    assert projected['plan']['locations'][0]['self_evacuation_ability'] == 'unknown'
    assert projected['plan']['response']['steps'][0]['depart_min'] == 0
    assert projected['plan']['response']['steps'][0]['finish_min'] == 3
    assert projected['plan']['response']['assumptions'] == state['plan']['response']['assumptions']


def test_multi_crew_supplied_routes_preserve_lonlat_and_task_status():
    state = envelope()
    state['plan']['response'] = {'schema_version': 'multi-response-plan-1', 'teams': [
        {'team_id': 'truck', 'locked': False, 'remaining_transport_capacity': 2,
         'tasks': [{'action_id': 'evac', 'asset_id': 'a', 'status': 'proposed', 'depart_min': 3,
                    'finish_min': 9, 'route_source': 'verified graph fixture',
                    'path_lonlat': [[3.1, 41.9], [3.2, 41.8]]}]}]}
    assert public_state(state)['plan']['response'] == state['plan']['response']


def test_unserved_assets_and_numeric_coverage_survive_public_response_projection():
    state = envelope()
    state['plan']['response'] = {'coverage': {'a': 0, 'api_key': 'secret'},
        'unserved': {'a': 'needs additional resources', 'private': 'secret'},
        'blocked_actions': {'act-a': ['missing_route']}, 'review': [{'asset_id': 'a', 'reason': 'unknown_transport'}]}
    public = public_state(state)['plan']['response']
    assert public['coverage'] == {'a': 0}
    assert public['unserved'] == {'a': 'needs additional resources'}
    assert public['blocked_actions'] == {'act-a': ['missing_route']}
    assert public['review'] == [{'asset_id': 'a', 'reason': 'unknown_transport'}]
