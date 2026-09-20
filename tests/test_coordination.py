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


def test_subminute_call_evidence_uses_the_exact_shared_epoch(tmp_path):
    path = tmp_path / 'live.sqlite'
    args = supplied()
    now = EPOCH + timedelta(seconds=30)
    save_call(path, args[0], at=now)
    store = coordinator(path, now)
    state = store.refresh(*args)
    assert 'future_assessment' not in location(state)['reasons']
    assert state['elapsed_min'] == 0.5
    assert location(state)['mode'] == 'self_evacuate'
    store.close()


def test_existing_unscoped_analyst_task_for_current_asset_is_visible(tmp_path):
    store = coordinator(tmp_path / 'live.sqlite')
    task = store.tasks.create_task('B', 'contact_facility', 'PRIVATE analyst task', snapshot_id=None)
    state = store.refresh(*supplied())
    assert task['task_id'] in {t['task_id'] for t in state['tasks']}
    assert 'PRIVATE analyst task' not in json.dumps(state)
    store.close()


def test_starting_with_newer_snapshot_preserves_existing_assigned_work(tmp_path):
    path = tmp_path / 'live.sqlite'
    older = supplied()
    save_call(path, older[0], can_self_evacuate=False, wants_human=True)
    voice = VoiceStore(path, epoch=EPOCH, clock=lambda: EPOCH)
    callback = voice.human_tasks('B')[0]
    roster = tmp_path / 'roster.json'
    roster.write_text(json.dumps([dict(team_id='desk', name='Desk', available=True,
        capabilities=callback['required_capabilities'])]))
    voice.tasks.load_roster(roster)
    voice.tasks.assign(callback['task_id'], 'desk')
    existing_ids = {t['task_id'] for t in voice.tasks.tasks()}
    voice.close()
    store = coordinator(path)
    state = store.refresh(*supplied(2))
    assert existing_ids <= {t['task_id'] for t in state['tasks']}
    held = next(t for t in state['tasks'] if t['task_id'] == callback['task_id'])
    assert held['assigned_team_id'] == 'desk'
    assert held['status'] == 'assigned'
    # Unknown earlier snapshot facts still require explicit association; work stays visible.
    assert state['errors'][0]['code'] == 'call_snapshot_not_accepted'
    store.close()


def test_public_allowlists_exclude_private_extensions_and_arbitrary_task_reasons(tmp_path):
    args = supplied()
    args[0]['assets'][0]['metadata'] = dict(credentials=dict(slng_api_key='PRIVATE_CREDENTIAL'),
        notes='Call 202-555-0123')
    store = coordinator(tmp_path / 'live.sqlite')
    store.tasks.create_task('B', 'contact_facility', 'voice:PRIVATE respondent excerpt',
        snapshot_id=args[0]['snapshot_id'])
    state = store.refresh(*args)
    assert 'PRIVATE' not in json.dumps(state)
    assert '202-555-0123' not in json.dumps(state)
    store.close()


def test_prior_taskstore_accepted_snapshot_supplies_durable_assessments(tmp_path):
    path = tmp_path / 'live.sqlite'
    older = supplied()
    save_call(path, older[0])
    voice = VoiceStore(path, epoch=EPOCH, clock=lambda: EPOCH)
    assert voice.tasks.apply_snapshot(older[0])['accepted']
    voice.close()
    store = coordinator(path)
    state = store.refresh(*supplied(2))
    assert location(state)['mode'] == 'self_evacuate'
    assert state['calls'][0]['snapshot_id'] == older[0]['snapshot_id']
    store.close()


def test_explicit_human_request_without_excerpt_survives_later_call(tmp_path):
    path = tmp_path / 'live.sqlite'
    args = supplied()
    evidence = {k: v for k, v in result().evidence.items() if k != 'wants_human'}
    save_call(path, args[0], wants_human=True, evidence=evidence)
    save_call(path, args[0], rid='req-new', at=EPOCH + timedelta(seconds=30))
    store = coordinator(path, EPOCH + timedelta(seconds=30))
    state = store.refresh(*args)
    assert location(state)['human_followup'] is True
    assert 'human_requested' in location(state)['reasons']
    assert location(state)['mode'] == 'undetermined'
    store.close()


def multi_export():
    from pathlib import Path
    return json.loads(Path('fixtures/coordination/multi_response.json').read_text())['response']


def test_verified_multi_crew_export_is_published_only_as_a_proposal(tmp_path):
    store = coordinator(tmp_path / 'live.sqlite')
    response = multi_export()
    state = store.refresh(*supplied(), response_plan=response)
    assert state['plan']['response']['schema_version'] == 'multi-response-plan-1'
    assert state['plan']['response']['teams'][0]['tasks'][0]['status'] == 'proposed'
    assert state['plan']['response_replanning_required'] is False
    assert state['teams'] == []  # proposals do not claim roster membership/assignment
    assert all(t['assigned_team_id'] is None for t in state['tasks'])
    assert store.refresh(*supplied(), response_plan=response) == state
    # An omitted export is not silently reused on a later tick.
    assert store.refresh(*supplied())['plan']['response'].get('schema_version') != 'multi-response-plan-1'
    store.close()


@pytest.mark.parametrize('change', ['snapshot', 'time', 'dispatch', 'asset'])
def test_stale_or_mismatched_multi_crew_export_is_rejected_atomically(tmp_path, change):
    store = coordinator(tmp_path / 'live.sqlite')
    first = store.refresh(*supplied())
    response = multi_export()
    if change == 'snapshot':
        response['snapshot_id'] = 'wrong'
    elif change == 'time':
        response['now_min'] = 1
    elif change == 'dispatch':
        response['dispatch'] = True
    else:
        response['teams'][0]['tasks'][0]['asset_id'] = 'unknown'
    with pytest.raises(ValueError):
        store.refresh(*supplied(), response_plan=response)
    assert store.state() == first
    store.close()


def test_delayed_adverse_result_keeps_assistance_review_even_when_result_is_ignored(tmp_path):
    path = tmp_path / 'live.sqlite'
    args = supplied()
    now = EPOCH + timedelta(minutes=1)
    save_call(path, args[0], at=now)
    voice = VoiceStore(path, epoch=EPOCH, clock=lambda: now)
    assert voice.record_result(result(snapshot_id=args[0]['snapshot_id'],
        provider_call_id='call-req-B', can_self_evacuate=False)) == 'ignored'
    voice.close()
    store = coordinator(path, now)
    state = store.refresh(*args)
    assert location(state)['human_followup'] is True
    assert location(state)['mode'] == 'undetermined'
    assert 'unresolved_assistance_request' in location(state)['reasons']
    assert location(state)['destination_id'] is None
    assert state['plan']['remaining_capacity']['centre'] == 10
    assert state['calls'][0]['can_self_evacuate'] is True  # do not fabricate the retained answer
    store.close()


def test_startup_rejects_snapshot_older_than_existing_taskstore_history(tmp_path):
    path = tmp_path / 'live.sqlite'
    voice = VoiceStore(path, epoch=EPOCH, clock=lambda: EPOCH)
    assert voice.tasks.apply_snapshot(supplied(2)[0])['accepted']
    voice.close()
    store = coordinator(path)
    with pytest.raises(ValueError, match='regress'):
        store.refresh(*supplied(1))
    assert store.state() is None
    assert store.refresh(*supplied(2))['revision'] == 1
    store.close()


def test_first_tick_after_closed_assistance_work_is_already_idempotent(tmp_path):
    path = tmp_path / 'live.sqlite'
    args = supplied()
    save_call(path, args[0], can_self_evacuate=False)
    voice = VoiceStore(path, epoch=EPOCH, clock=lambda: EPOCH)
    task = next(t for t in voice.tasks.tasks() if t['reason'] == 'voice:arrange_assistance')
    voice.tasks.set_status(task['task_id'], 'done')
    voice.close()
    store = coordinator(path)
    first = store.refresh(*args)
    assert store.refresh(*args) == first
    assert location(first)['assistance_review_required'] is True
    assert next(t for t in first['tasks'] if t['task_id'] == task['task_id'])['status'] == 'done'
    store.close()


def test_multi_crew_preserved_commitment_for_missing_asset_remains_in_review(tmp_path):
    from pathlib import Path
    response = json.loads(Path('fixtures/coordination/multi_response.json').read_text())['missing_asset_response']
    snapshot, scenario, centres, _ = supplied(2)
    snapshot['assets'] = [a for a in snapshot['assets'] if a['asset_id'] != 'B']
    scenario = replace(scenario, locations=tuple(a for a in scenario.locations if a.asset_id != 'B'),
        actions=tuple(a for a in scenario.actions if a.site_id != 'B'),
        travel={k: v for k, v in scenario.travel.items() if 'B' not in k})
    store = coordinator(tmp_path / 'live.sqlite')
    state = store.refresh(snapshot, scenario, centres, [], response_plan=response)
    task = state['plan']['response']['teams'][0]['tasks'][0]
    assert task['asset_id'] == 'B' and task['status'] == 'informed'
    assert any('missing_asset' in r.get('reasons', []) for r in state['plan']['response']['review'])
    assert store.refresh(snapshot, scenario, centres, [], response_plan=response) == state
    store.close()
