"""Offline service-boundary tests using real SQLite stores and synthetic inputs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import importlib
import json
import subprocess
import sys

import pytest

from fireline.snapshot import build_snapshot, validate_snapshot
from fireline.voice_store import VoiceStore
from tests.test_evacuation_readiness import inputs
from tests.test_voice_interview import EPOCH, request, result


def supplied(sequence=1, mode='synthetic'):
    scenario, _, centre, route = inputs()
    snapshot = build_snapshot([
        dict(asset_id=a.asset_id, name=a.name, asset_type='house', latitude=None,
             longitude=None, estimated_occupancy=a.people, occupancy_basis='synthetic')
        for a in scenario.locations
    ], None, scenario_id=scenario.scenario_id, incident_id='synthetic-incident',
        sequence=sequence, as_of=EPOCH, input_mode=mode)
    snapshot['computed_at'] = EPOCH.isoformat()
    for row, asset in zip(snapshot['assets'], scenario.locations):
        row.update(estimated_occupancy=asset.people, occupancy_basis='synthetic',
            distance_to_fire_m=asset.distance_m, intersects_fire=False,
            forecast_source=asset.forecast_source,
            forecast_horizon_at=(EPOCH + timedelta(minutes=60)).isoformat(),
            evacuation_min=asset.evacuation_min,
            evacuation_source=asset.evacuation_source,
            fire_arrival_at=(EPOCH + timedelta(minutes=asset.fire_arrival_min)).isoformat(),
            fire_arrival_basis=asset.forecast_source)
        row['review_reasons'] = [r for r in row['review_reasons']
            if r not in ('forecast_unavailable', 'evacuation_unknown')]
    assert validate_snapshot(snapshot) == []
    return snapshot, scenario, [centre], [route]


def coordinator(path, now=EPOCH):
    api = importlib.import_module('fireline.coordination')
    return api.CoordinationStore(path, epoch=EPOCH, clock=lambda: now)


def save_call(path, snapshot, *, rid='req-B', at=EPOCH, **changes):
    voice = VoiceStore(path, epoch=EPOCH, clock=lambda: at)
    req = request(request_id=rid, snapshot_id=snapshot['snapshot_id'], input_mode=snapshot['input_mode'])
    voice.register(req)
    voice.bind(rid, 'call-' + rid)
    voice.record_lifecycle(event_id='lifecycle-' + rid, request_id=rid, asset_id='B',
        snapshot_id=req.snapshot_id, provider_call_id='call-' + rid, status='completed',
        observed_at=at.isoformat())
    voice.record_result(result(request_id=rid, snapshot_id=req.snapshot_id,
        provider_call_id='call-' + rid, observed_at=at.isoformat(), **changes))
    voice.close()


def location(state, aid='B'):
    return next(r for r in state['plan']['locations'] if r['asset_id'] == aid)


def test_persistent_projection_refreshes_calls_without_ui_and_restarts(tmp_path):
    path = tmp_path / 'live.sqlite'
    args = supplied()
    store = coordinator(path)
    assert store.state() is None
    first = store.refresh(*args)
    assert first['schema_version'] == 'coordination-state-1'
    assert first['revision'] == 1
    assert location(first)['reasons'] == ['not_contacted']
    save_call(path, args[0])
    second = store.refresh(*args)
    assert second['revision'] == 2
    assert location(second)['mode'] == 'self_evacuate'
    assert location(second)['evacuation_status'] == 'not_confirmed'
    assert second['plan']['remaining_capacity']['centre'] == 2
    assert store.refresh(*args) == second
    assert [e['revision'] for e in store.updates()] == [1, 2]
    assert store.updates(1)[0]['changed_asset_ids'] == ['B']
    store.close()
    reopened = coordinator(path)
    assert reopened.state() == second
    assert reopened.refresh(*args) == second
    assert all(t['status'] != 'done' for t in second['tasks'])
    reopened.close()


def test_live_assistance_unknown_confidence_and_private_payload(tmp_path):
    args = supplied(mode='live')
    path = tmp_path / 'live.sqlite'
    save_call(path, args[0], can_self_evacuate=False, wants_human=True,
        confidence=None, confidence_basis=None,
        evidence={k: 'PRIVATE respondent excerpt +12025550123' for k in result().evidence})
    store = coordinator(path)
    state = store.refresh(*args)
    row = location(state)
    assert row['reported_needs_assistance'] is True
    assert row['call_confidence'] is None
    assert row['mode'] == 'undetermined'
    assert {'human_callback', 'arrange_assistance'} <= set(row['tasks'])
    assert state['calls'][0]['wants_human'] is True
    assert state['calls'][0]['provenance']['request_id'] == 'req-B'
    assert state['calls'][0]['evidence_fields']
    serialized = json.dumps([state, store.updates()])
    assert '+12025550123' not in serialized
    assert 'PRIVATE respondent' not in serialized
    assert 'contact_number' not in serialized
    assert 'incident_brief' not in serialized
    assert state['calls'][0]['transfer_status'] is None
    store.close()


def test_assigned_callback_and_human_completion_survive_refresh(tmp_path):
    args = supplied(mode='live')
    path = tmp_path / 'live.sqlite'
    save_call(path, args[0], wants_human=True)
    store = coordinator(path)
    store.refresh(*args)
    roster = tmp_path / 'roster.json'
    roster.write_text(json.dumps([dict(team_id='desk', name='Analyst desk',
        capabilities=['phone'], available=True, source='synthetic roster')]))
    task = store.voice.human_tasks('B')[0]
    roster.write_text(json.dumps([dict(team_id='desk', name='Analyst desk',
        capabilities=task['required_capabilities'], available=True, source='synthetic roster')]))
    store.tasks.load_roster(roster)
    store.tasks.assign(task['task_id'], 'desk')
    state = store.refresh(*args)
    held = next(t for t in state['tasks'] if t['task_id'] == task['task_id'])
    assert held['assigned_team_id'] == 'desk'
    assert held['status'] == 'assigned'
    store.tasks.set_status(task['task_id'], 'done', note='PRIVATE callback details')
    state = store.refresh(*args)
    assert next(t for t in state['tasks'] if t['task_id'] == task['task_id'])['status'] == 'done'
    assert 'PRIVATE callback' not in json.dumps(state)
    assert store.refresh(*args) == state
    assert len(store.voice.human_tasks('B')) == 1
    store.close()


def test_snapshot_changes_drive_plan_timing_unknowns_and_preserve_asset_id(tmp_path):
    args = supplied()
    store = coordinator(tmp_path / 'live.sqlite')
    first = store.refresh(*args)
    changed = supplied(2)
    b = next(a for a in changed[0]['assets'] if a['asset_id'] == 'B')
    b.update(fire_arrival_at=None, fire_arrival_basis=None,
             estimated_occupancy=None, distance_to_fire_m=None, intersects_fire=None)
    b['review_reasons'].append('forecast_unavailable')
    second = store.refresh(*changed)
    assert second['revision'] == first['revision'] + 1
    assert 'B' in {r['asset_id'] for r in second['contacts']['review']}
    assert next(a for a in second['assets'] if a['asset_id'] == 'B')['estimated_occupancy'] is None
    assert len(second['tasks']) == len(first['tasks'])
    store.close()


def test_one_epoch_tick_expires_assessments_and_does_not_replay_old_response(tmp_path):
    path = tmp_path / 'live.sqlite'
    args = supplied()
    save_call(path, args[0])
    store = coordinator(path)
    store.refresh(*args)
    store.close()
    later = coordinator(path, EPOCH + timedelta(minutes=16))
    state = later.refresh(*args)
    assert 'stale_assessment' in location(state)['reasons']
    assert state['elapsed_min'] == 16
    assert state['plan']['response'] is None
    assert state['plan']['response_replanning_required'] is True
    assert later.refresh(*args) == state
    later.close()


def test_adverse_requests_survive_newer_optimistic_call(tmp_path):
    path = tmp_path / 'live.sqlite'
    args = supplied(mode='live')
    save_call(path, args[0], can_self_evacuate=False, wants_human=True)
    save_call(path, args[0], rid='req-new', at=EPOCH + timedelta(minutes=1))
    store = coordinator(path, EPOCH + timedelta(minutes=1))
    state = store.refresh(*args)
    assert location(state)['reported_needs_assistance'] is True
    assert 'human_requested' in location(state)['reasons']
    assert location(state)['human_followup'] is True
    assert len(state['calls']) == 2
    store.close()


def test_foreign_calls_cannot_influence_current_scenario(tmp_path):
    path = tmp_path / 'live.sqlite'
    args = supplied(mode='live')
    foreign = dict(args[0], snapshot_id='foreign-0001')
    save_call(path, foreign)
    store = coordinator(path)
    state = store.refresh(*args)
    assert location(state)['reasons'] == ['not_contacted']
    assert state['calls'] == []
    assert state['errors'][0]['code'] == 'call_snapshot_not_accepted'
    store.close()


def test_atomic_publication_rolls_back_tasks_and_revisions_on_failure(tmp_path):
    path = tmp_path / 'live.sqlite'
    store = coordinator(path)
    store.voice.conn.execute('''CREATE TRIGGER fail_publication BEFORE INSERT ON coordination_revisions
        BEGIN SELECT RAISE(ABORT, 'simulated publication failure'); END''')
    store.voice.conn.commit()
    with pytest.raises(Exception, match='simulated publication failure'):
        store.refresh(*supplied())
    assert store.state() is None
    assert store.updates() == []
    assert store.tasks.tasks() == []
    store.voice.conn.execute('DROP TRIGGER fail_publication')
    store.voice.conn.commit()
    assert store.refresh(*supplied())['revision'] == 1
    store.close()


def test_two_independent_refresh_workers_publish_one_revision(tmp_path):
    path = tmp_path / 'live.sqlite'
    coordinator(path).close()
    def tick(_):
        store = coordinator(path)
        try:
            return store.refresh(*supplied())['revision']
        finally:
            store.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(tick, range(2))) == [1, 1]
    store = coordinator(path)
    assert len(store.updates()) == 1
    store.close()


@pytest.mark.parametrize('change', ['identity', 'sequence', 'scenario', 'mode', 'future'])
def test_bad_snapshot_never_replaces_published_state(tmp_path, change):
    store = coordinator(tmp_path / 'live.sqlite')
    args = supplied(2)
    first = store.refresh(*args)
    bad = deepcopy(args[0])
    if change == 'identity':
        bad['assets'][0]['name'] = 'different immutable content'
    elif change == 'sequence':
        bad = supplied(1)[0]
    elif change == 'scenario':
        bad.update(scenario_id='foreign', snapshot_id='foreign-0002')
    elif change == 'mode':
        bad['input_mode'] = 'live'
    elif change == 'future':
        bad['as_of'] = (EPOCH + timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError):
        store.refresh(bad, *args[1:])
    assert store.state() == first
    assert len(store.updates()) == 1
    store.close()


def test_prior_accepted_snapshot_call_carries_forward_with_original_time(tmp_path):
    path = tmp_path / 'live.sqlite'
    args = supplied()
    store = coordinator(path)
    store.refresh(*args)
    save_call(path, args[0])
    state = store.refresh(*supplied(2))
    assert location(state)['mode'] == 'self_evacuate'
    assert state['calls'][0]['snapshot_id'] == args[0]['snapshot_id']
    assert state['calls'][0]['observed_at'] == EPOCH.isoformat()
    store.close()


def test_offline_cli_tick_publishes_readable_state_and_updates(tmp_path):
    args = supplied()
    snapshot_path = tmp_path / 'snapshot.json'
    snapshot_path.write_text(json.dumps(args[0]))
    readiness_path = tmp_path / 'readiness.json'
    from dataclasses import asdict
    readiness_path.write_text(json.dumps(dict(centres=[asdict(c) for c in args[2]],
        routes=[asdict(r) for r in args[3]], road_warnings=[])))
    command = [sys.executable, '-m', 'fireline.coordination', 'tick',
        '--database', str(tmp_path / 'service.sqlite'), '--epoch', EPOCH.isoformat(),
        '--as-of', EPOCH.isoformat(), '--snapshot', str(snapshot_path),
        '--scenario', 'fixtures/static_priority.json', '--readiness', str(readiness_path)]
    first = subprocess.run(command, capture_output=True, text=True, check=True)
    assert first.stdout.strip(), 'tick must output the public state'
    state = json.loads(first.stdout)
    assert state['schema_version'] == 'coordination-state-1'
    second = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(second.stdout) == state
    store = coordinator(tmp_path / 'service.sqlite')
    assert store.state() == state
    assert store.updates()[0]['revision'] == state['revision']
    store.close()


def test_unbound_queue_request_is_visible_without_fabricated_call_result(tmp_path):
    args = supplied(mode='live')
    store = coordinator(tmp_path / 'live.sqlite')
    store.voice.register(request(snapshot_id=args[0]['snapshot_id'], input_mode='live'))
    state = store.refresh(*args)
    assert state['calls'][0]['status'] == 'queued'
    assert state['calls'][0]['observed_at'] is None
    assert state['calls'][0]['can_self_evacuate'] is None
    assert location(state)['reasons'] == ['not_contacted']
    assert len(store.voice.human_tasks('B')) == 1
    store.close()


def test_road_warnings_survive_projection_and_create_human_work(tmp_path):
    args = supplied()
    store = coordinator(tmp_path / 'live.sqlite')
    save_call(tmp_path / 'live.sqlite', args[0])
    warning = dict(road_id='road-1', road_name='Synthetic road',
                   reason='synthetic fire report', source='synthetic observer')
    state = store.refresh(*args, road_warnings=[warning])
    assert location(state)['road_warnings'] == [warning]
    assert 'communicate_road_warning' in location(state)['tasks']
    assert 'road_warning_update_unconfirmed' in location(state)['reasons']
    assert location(state)['evacuation_status'] == 'not_confirmed'
    assert any(t['kind'] == 'communicate_road_warning' for t in state['tasks'])
    store.close()


def test_snapshot_public_metadata_does_not_expose_credentials_or_numbers(tmp_path):
    args = supplied()
    args[0]['assets'][0].update(contact_number='+12025550123', api_key='PRIVATE_KEY')
    store = coordinator(tmp_path / 'live.sqlite')
    state = store.refresh(*args)
    assert 'PRIVATE_KEY' not in json.dumps(state)
    assert '+12025550123' not in json.dumps(state)
    store.close()


@pytest.mark.parametrize('cursor', [True, -1, 1.5, '1'])
def test_event_cursor_requires_nonnegative_builtin_integer(tmp_path, cursor):
    store = coordinator(tmp_path / 'live.sqlite')
    with pytest.raises(ValueError, match='after_revision'):
        store.updates(cursor)
    store.close()
