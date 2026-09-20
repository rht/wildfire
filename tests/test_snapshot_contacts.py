"""Offline adapter contracts using the actual snapshot producer and forecast attachment."""
import copy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from fireline import config
from fireline.grid import lonlat_to_xy
from fireline.priority import rank_snapshot
from fireline.snapshot import build_snapshot, validate_snapshot
from fireline.snapshot_contacts import enqueue_snapshot_contacts, snapshot_request_id
from fireline.voice_queue import CallQueueConfig, VoiceCallQueue
from fireline.voice_store import VoiceStore

EPOCH = datetime(2026, 9, 20, 8, tzinfo=timezone.utc)
# Reserved fictional test number, used only inside offline tests.
PHONE = '+12025550123'


def at(minutes):
    return (EPOCH + timedelta(minutes=minutes)).isoformat()


def produced_snapshot():
    forecast = dict(schema_version='forecast-input-1', forecast_source='offline test forecast',
        input_mode='synthetic', issued_at=at(10), received_at=at(10),
        forecast_horizon_at=at(240), basis='p10',
        note='synthetic, not a provider forecast', estimates={
            'test:near': dict(arrival_p10_at=at(150), arrival_p50_at=at(180), burn_probability=.5),
            'test:far': dict(arrival_p10_at=at(90), arrival_p50_at=at(120), burn_probability=.5)})
    snap = build_snapshot([
        dict(asset_id='test:near', name='Near school', asset_class='school', lon=3.01, lat=41.9),
        dict(asset_id='test:far', name='Far school', asset_class='school', lon=3.04, lat=41.9),
        dict(asset_id='test:unknown', name='Unknown school', asset_class='school', lon=3.02, lat=41.9)],
        dict(geometry={'type': 'Point', 'coordinates': [3.0, 41.9]},
             geometry_kind='hotspot_centre', observed_at=at(10), received_at=at(10), source='test observation'),
        scenario_id='adapter-test', incident_id='test-fire', sequence=1,
        as_of=at(15), computed_at=at(15), input_mode='live', forecast=forecast)
    assert validate_snapshot(snap) == []
    return snap


def private_inputs(snap):
    contacts = [dict(asset_id=a['asset_id'], contact_number=PHONE) for a in snap['assets']]
    approvals = [dict(asset_id=a['asset_id'], snapshot_id=snap['snapshot_id'], contact_number=PHONE)
                 for a in snap['assets']]
    requests = {a['asset_id']: dict(language='en', incident_brief='Offline test incident brief.')
                for a in snap['assets']}
    return contacts, approvals, requests


@pytest.fixture
def queue():
    store = VoiceStore(epoch=EPOCH, clock=lambda: EPOCH + timedelta(minutes=20))
    yield VoiceCallQueue(store, CallQueueConfig())
    store.close()


def enqueue(queue, snap=None, **kwargs):
    snap = snap or produced_snapshot()
    contacts, approvals, requests = private_inputs(snap)
    return enqueue_snapshot_contacts(queue, snap, kwargs.pop('contacts', contacts),
        kwargs.pop('approvals', approvals), kwargs.pop('request_data', requests),
        epoch=kwargs.pop('epoch', EPOCH.isoformat()), now_at=kwargs.pop('now_at', at(20)), **kwargs)


def test_producer_shape_priority_epoch_and_provenance(queue, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('enqueue attempted provider dispatch')
    monkeypatch.setattr(queue, 'dispatch_next', forbidden)
    monkeypatch.setattr(queue.store, 'start', forbidden)
    snap = produced_snapshot()
    before = copy.deepcopy(snap)
    result = enqueue(queue, snap)
    expected = rank_snapshot(snap, now_at=at(20))
    assert [r['asset_id'] for r in result['ranked']] == [r['asset_id'] for r in expected['ranked']]
    assert result['ranked'][0]['asset_id'] == 'test:far'
    for row, source in zip(result['ranked'], expected['ranked']):
        assert row['slack_min'] == source['slack_min']
        assert row['latest_start_min'] == source['latest_start_min'] + 20
        assert row['components']['now_min'] == 20
        assert row['components']['buffer_min'] == 30
        assert row['forecast_source'] == source['forecast_source']
        assert row['evacuation_source'] == source['evacuation_source']
        assert row['evidence']['sources'] == source['sources']
        assert row['evidence']['fire_arrival_at'] == source['fire_arrival_at']
        assert row['components']['distance_m'] == source['distance_to_fire_m']
    assert result['review'][0]['asset_id'] == 'test:unknown'
    assert result['review'][0]['components']['fire_arrival_min'] is None
    assert result['blocked'][0]['reasons'] == ['missing_fire_arrival_min', 'missing_forecast_source']
    assert len(queue.status()['pending']) == 2
    assert PHONE not in json.dumps(result)
    assert snap == before


@pytest.mark.parametrize('kind,reason', [
    ('missing_contact', 'missing_contact'), ('missing_phone', 'missing_phone'),
    ('duplicate_contact', 'ambiguous_contact'), ('missing_approval', 'missing_authorization'),
    ('wrong_target', 'target_not_authorized'), ('wrong_snapshot', 'missing_authorization'),
    ('duplicate_approval', 'ambiguous_authorization'), ('missing_request', 'missing_request_data')])
def test_private_matching_reports_without_phone_leaks(queue, kind, reason):
    snap = produced_snapshot()
    contacts, approvals, requests = private_inputs(snap)
    aid = 'test:far'
    if kind == 'missing_contact':
        contacts = [r for r in contacts if r['asset_id'] != aid]
    elif kind == 'missing_phone':
        contacts[1]['contact_number'] = None
    elif kind == 'duplicate_contact':
        contacts.append(copy.deepcopy(contacts[1]))
    elif kind == 'missing_approval':
        approvals = []
    elif kind == 'wrong_target':
        approvals[1]['contact_number'] = '+12025550124'
    elif kind == 'wrong_snapshot':
        approvals[1]['snapshot_id'] = 'older'
    elif kind == 'duplicate_approval':
        approvals.append(copy.deepcopy(approvals[1]))
    else:
        del requests[aid]
    result = enqueue(queue, snap, contacts=contacts, approvals=approvals, request_data=requests)
    blocked = next(r for r in result['blocked'] if r['asset_id'] == aid)
    assert reason in blocked['reasons']
    assert snapshot_request_id(snap['snapshot_id'], aid) not in queue.status()['pending']
    assert PHONE not in json.dumps(result)


def test_repeat_and_started_calls_are_preserved(queue):
    snap = produced_snapshot()
    first = enqueue(queue, snap)
    rid = first['enqueue']['queued'][0]
    queue.store.bind(rid, 'offline-bound-call')
    before = queue.store.get(rid)
    contacts, approvals, requests = private_inputs(snap)
    requests['test:far']['incident_brief'] = 'Changed brief must not replace a started call.'
    result = enqueue(queue, snap, request_data=requests)
    assert result['existing'][rid] == 'started'
    assert queue.store.get(rid) == before
    assert queue.store.conn.execute('SELECT COUNT(*) FROM voice_calls').fetchone()[0] == 2


def test_stale_snapshot_and_expired_horizon_block_without_changing_rank(queue):
    snap = produced_snapshot()
    result = enqueue(queue, snap, now_at=at(80))
    assert all('stale_snapshot' in r['reasons'] for r in result['blocked'])
    assert not queue.status()['pending']
    result = enqueue(queue, snap, now_at=at(241), max_age_min=1000)
    assert all('forecast_horizon_expired' in r['reasons'] for r in result['blocked'] if r['asset_id'] != 'test:unknown')


def test_snapshot_and_epoch_validation_is_atomic(queue):
    snap = produced_snapshot()
    snap['assets'].append(copy.deepcopy(snap['assets'][0]))
    with pytest.raises(ValueError, match='snapshot'):
        enqueue(queue, snap)
    with pytest.raises(ValueError, match='epoch'):
        enqueue(queue, epoch=at(1))
    with pytest.raises(ValueError, match='UTC'):
        enqueue(queue, now_at='2026-09-20T08:20:00')
    assert not queue.status()['pending']


def test_custom_buffer_and_real_coordinate_projection(queue):
    snap = produced_snapshot()
    result = enqueue(queue, snap, buffer_min=12)
    cfg = SimpleNamespace(CONTACT_POLICY={**config.CONTACT_POLICY, 'buffer_min': 12},
                          EVACUATION_POLICY=config.EVACUATION_POLICY,
                          CRITICALITY_POLICY=config.CRITICALITY_POLICY)
    expected = rank_snapshot(snap, cfg=cfg, now_at=at(20))
    assert [r['slack_min'] for r in result['ranked']] == [r['slack_min'] for r in expected['ranked']]
    # Capture the queue boundary: only actual coordinates may be projected.
    seen = []
    original = queue.enqueue
    def capture(locations, *args, **kwargs):
        seen.extend(locations)
        return original(locations, *args, **kwargs)
    queue.enqueue = capture
    snap['sequence'] = 2
    snap['snapshot_id'] = 'adapter-test-0002'
    enqueue(queue, snap)
    assert len(seen) == 2
    for loc in seen:
        asset = next(a for a in snap['assets'] if a['asset_id'] == loc.asset_id)
        assert (loc.x_m, loc.y_m) == lonlat_to_xy(asset['longitude'], asset['latitude'])
        assert loc.value is None and loc.deadline_min is None and loc.assisted is None


def test_optional_brief_adapter_is_explicit_and_association_is_fixed(queue):
    snap = produced_snapshot()
    def brief(asset, data):
        return {**data, 'incident_brief': 'Approved per-location briefing for ' + asset['asset_id']}
    result = enqueue(queue, snap, briefing_adapter=brief)
    for rid in result['enqueue']['queued']:
        request = queue.store.get(rid)['request']
        assert request['incident_brief'].endswith(request['asset_id'])
        assert request['input_mode'] == 'live'


def test_stale_replay_cancels_pending_but_preserves_started(queue):
    snap = produced_snapshot()
    result = enqueue(queue, snap)
    started, pending = result['enqueue']['queued']
    queue.store.bind(started, 'offline-held-call')
    result = enqueue(queue, snap, now_at=at(80))
    assert result['existing'] == {started: 'started', pending: 'cancelled'}
    assert queue.store.get(started)['provider_call_id'] == 'offline-held-call'
    assert not queue.status()['pending']


def test_unknown_coordinates_do_not_fabricate_locations(queue):
    snap = produced_snapshot()
    asset = snap['assets'][1]
    asset['longitude'] = asset['latitude'] = None
    asset['needs_review'] = True
    asset['review_reasons'].append('location_unknown')
    result = enqueue(queue, snap)
    assert result['ranked'][0]['asset_id'] == 'test:far'
    assert 'queue_coordinates_unavailable' in result['blocked'][0]['reasons']


def test_old_forecast_in_fresh_snapshot_and_future_evidence(queue):
    snap = produced_snapshot()
    snap['as_of'] = snap['computed_at'] = at(80)
    result = enqueue(queue, snap, now_at=at(80))
    assert 'stale_forecast' in result['blocked'][0]['reasons']
    assert 'stale_snapshot' not in result['blocked'][0]['reasons']
    snap = produced_snapshot()
    for source in snap['assets'][1]['sources']:
        if 'fire_arrival_at' in source['fields']:
            source['available_at'] = at(30)
    result = enqueue(queue, snap)
    assert 'future_forecast_evidence' in result['blocked'][0]['reasons']


def test_missing_distance_does_not_replace_arrival_or_block_queue(queue):
    snap = produced_snapshot()
    asset = snap['assets'][1]
    asset['distance_to_fire_m'] = asset['intersects_fire'] = None
    asset['review_reasons'].append('exposure_unknown')
    asset['needs_review'] = True
    result = enqueue(queue, snap)
    assert result['ranked'][0]['components']['distance_m'] is None
    assert result['ranked'][0]['components']['fire_arrival_min'] == 90
    assert len(result['enqueue']['queued']) == 2


def test_no_upgrade_of_recorded_snapshot_to_live(queue):
    snap = produced_snapshot()
    snap['input_mode'] = 'recorded'
    result = enqueue(queue, snap)
    assert all('snapshot_not_live' in r['reasons'] for r in result['blocked'])
    assert not queue.status()['pending']


def test_immutable_pending_request_change_is_reported(queue):
    snap = produced_snapshot()
    result = enqueue(queue, snap)
    rid = result['enqueue']['queued'][0]
    contacts, approvals, requests = private_inputs(snap)
    requests['test:far']['incident_brief'] = 'Replacement brief'
    result = enqueue(queue, snap, request_data=requests)
    assert result['existing'][rid] == 'cancelled'
    assert 'immutable_request_conflict' in result['blocked'][0]['reasons']


def test_malformed_brief_callback_cannot_change_association(queue):
    result = enqueue(queue, briefing_adapter=lambda asset, data: {**data, 'contact_number': PHONE})
    assert all('invalid_request_data' in row['reasons'] for row in result['blocked'][:2])
    assert not queue.status()['pending']


def test_cli_enqueues_only_and_private_database(tmp_path, monkeypatch, capsys):
    from scripts.snapshot_contacts import main
    import socket
    def forbidden(*args, **kwargs):
        pytest.fail('CLI tried networking or dispatch')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(VoiceStore, 'start', forbidden)
    monkeypatch.setattr(VoiceCallQueue, 'dispatch_next', forbidden)
    snap = produced_snapshot()
    contacts, approvals, requests = private_inputs(snap)
    argv = []
    for name, data in [('snapshot', snap), ('contacts', contacts), ('approvals', approvals), ('requests', requests)]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(data))
        argv.extend(['--' + name, str(path)])
    db = tmp_path / 'queue.db'
    assert main(argv + ['--epoch', at(0), '--now-at', at(20), '--db', str(db)]) == 0
    output = capsys.readouterr().out
    assert PHONE not in output and 'Offline test incident brief' not in output
    assert len(json.loads(output)['enqueue']['queued']) == 2
    assert db.stat().st_mode & 0o777 == 0o600


def test_cli_rejects_duplicate_json_keys_without_echo(tmp_path, capsys):
    from scripts.snapshot_contacts import main
    path = tmp_path / 'bad.json'
    path.write_text('{"secret":"private marker", "secret":"another value"}')
    args = sum((['--' + flag, str(path)] for flag in ('snapshot', 'contacts', 'approvals', 'requests')), [])
    assert main(args + ['--epoch', at(0), '--now-at', at(20), '--db', str(tmp_path / 'no.db')]) == 2
    captured = capsys.readouterr()
    assert 'private marker' not in captured.err and 'secret' not in captured.err
    assert not (tmp_path / 'no.db').exists()


@pytest.mark.parametrize('missing', ['record', 'observed_at'])
def test_missing_forecast_observation_is_not_fresh_evidence(queue, missing):
    snap = produced_snapshot()
    for asset in snap['assets']:
        if missing == 'record':
            asset['sources'] = [s for s in asset['sources'] if 'fire_arrival_at' not in s['fields']]
        else:
            for source in asset['sources']:
                if 'fire_arrival_at' in source['fields']:
                    source['observed_at'] = None
    assert validate_snapshot(snap) == []
    result = enqueue(queue, snap)
    assert all('missing_forecast_observed_at' in r['reasons'] for r in result['blocked'][:2])
    assert not queue.status()['pending']


@pytest.mark.parametrize('change', ['evacuation', 'buffer', 'distance', 'arrival'])
def test_changed_priority_on_pending_replay_is_explicit_conflict(queue, change):
    snap = produced_snapshot()
    first = enqueue(queue, snap)
    kwargs = {}
    if change == 'evacuation':
        snap['assets'][0]['evacuation_min'] = 140
        snap['assets'][1]['evacuation_min'] = 0
    elif change == 'buffer':
        kwargs['buffer_min'] = 5
    elif change == 'distance':
        for a in snap['assets']:
            a['distance_to_fire_m'] += 100
    else:
        snap['assets'][0]['fire_arrival_at'] = at(160)
        snap['assets'][1]['fire_arrival_at'] = at(120)
    result = enqueue(queue, snap, **kwargs)
    assert all('immutable_priority_conflict' in r['reasons'] for r in result['blocked'][:2])
    assert all(result['existing'][rid] == 'cancelled' for rid in first['enqueue']['queued'])
    assert not queue.status()['pending']


def test_optional_sibling_briefing_export_adapter(queue):
    from fireline.snapshot_contacts import briefing_adapter_from_recommendations
    snap = produced_snapshot()
    recommendation = {'asset_id': 'test:far', 'snapshot_id': snap['snapshot_id'], 'approved': True}
    observed = []
    def build(asset, recommendation, *, snapshot_id):
        observed.append((asset['asset_id'], recommendation, snapshot_id))
        return SimpleNamespace(asset_id=asset['asset_id'], snapshot_id=snapshot_id,
                               incident_brief='Verified briefing export', road_warnings=[])
    adapter = briefing_adapter_from_recommendations(snap['snapshot_id'], {'test:far': recommendation},
                                                    build_briefing=build)
    result = enqueue(queue, snap, briefing_adapter=adapter)
    assert observed[0] == ('test:far', recommendation, snap['snapshot_id'])
    assert observed[1][1] is None
    assert queue.store.get(result['enqueue']['queued'][0])['request']['incident_brief'] == 'Verified briefing export'


def test_bound_briefing_cannot_be_reused_for_another_snapshot(queue):
    from fireline.snapshot_contacts import briefing_adapter_from_recommendations
    def build(asset, recommendation, *, snapshot_id):
        return SimpleNamespace(asset_id=asset['asset_id'], snapshot_id=snapshot_id,
                               incident_brief='Old snapshot briefing', road_warnings=[])
    adapter = briefing_adapter_from_recommendations('old-0001', {}, build_briefing=build)
    result = enqueue(queue, briefing_adapter=adapter)
    assert not result['enqueue']['queued']
    assert all('invalid_request_data' in r['reasons'] for r in result['blocked'][:2])
