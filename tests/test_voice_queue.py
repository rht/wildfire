"""Real SQLite queue and SLNG adapter; only provider HTTP is replaced."""
from dataclasses import replace
from datetime import timedelta
import importlib
from uuid import uuid4

import pytest
import requests

from fireline.contact_priority import ContactPolicy
from fireline.priority_examples import load_scenario
from fireline.voice_store import VoiceStore
from tests.test_voice_interview import EPOCH, request
from tests.test_voice_provider import configured_agent, AGENT, TRUNK, Transport, client


def api():
    return importlib.import_module('fireline.voice_queue')


def setup_queue(tmp_path, *, concurrent=2, rate=1):
    now = [EPOCH]
    store = VoiceStore(tmp_path / 'queue.sqlite', epoch=EPOCH, clock=lambda: now[0])
    q = api().VoiceCallQueue(store, api().CallQueueConfig(concurrent, rate))
    return q, store, now


def enqueue(q, *, locations=None, same_phone=False):
    locations = locations or load_scenario('fixtures/static_priority.json').locations
    reqs = [request(request_id='req-' + loc.asset_id, asset_id=loc.asset_id,
                    contact_number='+120255501' + ('00' if same_phone else str(i).zfill(2)),
                    input_mode='live') for i, loc in enumerate(locations)]
    result = q.enqueue(locations, reqs,
                       approved_targets={r.request_id: r.contact_number for r in reqs})
    return result, reqs


def provider(count=3, error=None):
    bodies = []
    for i in range(count):
        bodies += [configured_agent(),
                   error or dict(call_id=str(uuid4()))]
    return client(Transport(bodies))


def complete(store, request_id):
    rec = store.get(request_id)
    store.record_lifecycle(event_id='end-' + request_id, request_id=request_id,
        asset_id=rec['request']['asset_id'], snapshot_id=rec['request']['snapshot_id'],
        provider_call_id=rec['provider_call_id'], status='completed',
        observed_at=store.clock().isoformat())


def test_priority_rate_capacity_and_terminal_refill(tmp_path):
    q, s, now = setup_queue(tmp_path)
    report, _ = enqueue(q)
    assert report['queued'] == ['req-A', 'req-B', 'req-C']
    c = provider()
    assert q.dispatch_next(c)['request_id'] == 'req-A'
    assert q.dispatch_next(c) is None  # rate, even though one slot is free
    now[0] += timedelta(seconds=1)
    assert q.dispatch_next(c)['request_id'] == 'req-B'
    now[0] += timedelta(seconds=1)
    assert q.dispatch_next(c) is None  # two ongoing calls, HTTP already returned
    complete(s, 'req-A')
    assert q.dispatch_next(c)['request_id'] == 'req-C'
    assert q.status()['active'] == 2
    assert q.status()['pending'] == []


def test_restart_and_second_worker_share_capacity_and_rate(tmp_path):
    q, s, now = setup_queue(tmp_path, concurrent=1)
    enqueue(q)
    q.dispatch_next(provider())
    s.close()
    s2 = VoiceStore(tmp_path / 'queue.sqlite', epoch=EPOCH, clock=lambda: now[0])
    q2 = api().VoiceCallQueue(s2, api().CallQueueConfig(1, 1))
    now[0] += timedelta(seconds=2)
    assert q2.dispatch_next(provider()) is None
    complete(s2, 'req-A')
    assert q2.dispatch_next(provider())['request_id'] == 'req-B'
    s3 = VoiceStore(tmp_path / 'queue.sqlite', epoch=EPOCH, clock=lambda: now[0])
    q3 = api().VoiceCallQueue(s3, api().CallQueueConfig(1, 1))
    complete(s2, 'req-B')
    assert q3.dispatch_next(provider()) is None  # persisted start-rate clock


def test_unknown_dispatch_holds_slot_without_retry(tmp_path):
    q, s, now = setup_queue(tmp_path, concurrent=1)
    enqueue(q)
    with pytest.raises(Exception, match='outcome_unknown'):
        q.dispatch_next(provider(error=requests.Timeout('private error')))
    assert s.get('req-A')['dispatch_state'] == 'outcome_unknown'
    now[0] += timedelta(minutes=5)
    assert q.dispatch_next(provider()) is None
    assert q.status()['unknown'] == ['req-A']
    assert s.human_tasks('A')


def test_missing_timing_stays_in_human_review_and_reenqueue_is_idempotent(tmp_path):
    q, s, _ = setup_queue(tmp_path)
    locs = load_scenario('fixtures/static_priority.json').locations
    locs = tuple(replace(l, fire_arrival_min=None) if l.asset_id == 'A' else l for l in locs)
    report, _ = enqueue(q, locations=locs)
    assert report['review'] == ['req-A']
    enqueue(q, locations=locs)
    assert q.status()['pending'] == ['req-B', 'req-C']
    assert s.human_tasks('A')
    assert q.dispatch_next(provider())['request_id'] == 'req-B'


def test_approval_failure_is_atomic_and_synthetic_requests_cannot_dial(tmp_path):
    q, s, _ = setup_queue(tmp_path)
    locs = load_scenario('fixtures/static_priority.json').locations
    req = request(asset_id='B', input_mode='live')
    with pytest.raises(ValueError):
        q.enqueue(locs, [req], approved_targets={})
    assert s.conn.execute('SELECT COUNT(*) FROM voice_calls').fetchone()[0] == 0
    with pytest.raises(ValueError):
        q.enqueue(locs, [replace(req, input_mode='synthetic')],
                  approved_targets={req.request_id: req.contact_number})


def test_same_phone_not_called_simultaneously_and_pending_can_be_cancelled(tmp_path):
    q, s, now = setup_queue(tmp_path, concurrent=5)
    enqueue(q, same_phone=True)
    q.dispatch_next(provider())
    now[0] += timedelta(seconds=1)
    assert q.dispatch_next(provider()) is None
    q.cancel('req-B')
    with pytest.raises(ValueError):
        q.cancel('req-A')
    complete(s, 'req-A')
    assert q.dispatch_next(provider())['request_id'] == 'req-C'


def test_manual_existing_call_consumes_queue_capacity(tmp_path):
    q, s, _ = setup_queue(tmp_path, concurrent=1)
    s.register(request(request_id='manual', asset_id='manual'))
    s.bind('manual', 'manual-call')
    enqueue(q)
    assert q.dispatch_next(provider()) is None


@pytest.mark.parametrize('concurrent,rate', [(0, 1), (-1, 1), (True, 1), (1.5, 1),
    (1, 0), (1, -1), (1, True), (1, float('nan')), (1, float('inf'))])
def test_invalid_limits_rejected(concurrent, rate):
    with pytest.raises(ValueError):
        api().CallQueueConfig(concurrent, rate)


def test_environment_limits_control_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv('MAX_CONCURRENT_CALLS', '1')
    monkeypatch.setenv('MAX_CALL_STARTS_PER_SECOND', '0.5')
    _, s, now = setup_queue(tmp_path)
    q = api().VoiceCallQueue(s, api().CallQueueConfig.from_env())
    enqueue(q)
    q.dispatch_next(provider())
    complete(s, 'req-A')
    now[0] += timedelta(seconds=1)
    assert q.dispatch_next(provider()) is None
    now[0] += timedelta(seconds=1)
    assert q.dispatch_next(provider())['request_id'] == 'req-B'


def test_simultaneous_workers_cannot_claim_same_contact(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    q, s, _ = setup_queue(tmp_path, concurrent=1)
    enqueue(q)
    s.close()
    ready = Barrier(2)

    def work():
        store = VoiceStore(tmp_path / 'queue.sqlite', epoch=EPOCH, clock=lambda: EPOCH)
        queue = api().VoiceCallQueue(store, api().CallQueueConfig(1, 1))
        start = store.start

        def coordinated_start(*args, **kwargs):
            ready.wait(timeout=5)  # force both workers to select the same pending row
            return start(*args, **kwargs)

        store.start = coordinated_start
        try:
            return queue.dispatch_next(provider())
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: work(), range(2)))
    assert sum(outcome is None for outcome in outcomes) == 1
    assert [o['request_id'] for o in outcomes if o] == ['req-A']


def test_pending_priorities_use_common_epoch_even_when_enqueued_later(tmp_path):
    q, s, _ = setup_queue(tmp_path)
    locs = load_scenario('fixtures/static_priority.json').locations
    b = next(l for l in locs if l.asset_id == 'B')  # latest start 8
    c = next(l for l in locs if l.asset_id == 'C')  # latest start 10
    rb = request(input_mode='live')
    rc = request(request_id='req-C', asset_id='C', input_mode='live', contact_number='+12025550124')
    q.enqueue([b], [rb], approved_targets={rb.request_id: rb.contact_number})
    q.enqueue([c], [rc], policy=ContactPolicy(now_min=7),
              approved_targets={rc.request_id: rc.contact_number})
    assert q.dispatch_next(provider())['request_id'] == 'req-B'


def test_slow_provider_creation_cannot_cause_a_burst(tmp_path):
    q, s, now = setup_queue(tmp_path, concurrent=5)
    enqueue(q)

    class SlowTransport(Transport):
        def request(self, method, url, **kwargs):
            if method == 'POST':
                now[0] += timedelta(seconds=3)
            return super().request(method, url, **kwargs)

    c = client(SlowTransport([configured_agent(),
                              dict(call_id=str(uuid4()))]))
    q.dispatch_next(c)
    assert q.dispatch_next(provider()) is None
    now[0] += timedelta(seconds=1)
    assert q.dispatch_next(provider())['request_id'] == 'req-B'


def test_queue_command_defaults_to_status_and_enqueue_never_dispatches(tmp_path, capsys):
    from dataclasses import asdict
    import json
    cli = importlib.import_module('scripts.dispatch_voice_queue')
    db = tmp_path / 'cli.sqlite'
    base = ['--db', str(db), '--epoch', EPOCH.isoformat()]
    assert cli.main(base) == 0
    assert json.loads(capsys.readouterr().out)['active'] == 0
    req = request(input_mode='live')
    reqs = tmp_path / 'requests.json'
    reqs.write_text(json.dumps([asdict(req)]), encoding='utf-8')
    approvals = tmp_path / 'approvals.json'
    approvals.write_text(json.dumps({req.request_id: req.contact_number}), encoding='utf-8')
    assert cli.main(base + ['--mode', 'enqueue', '--requests-file', str(reqs),
                           '--approval-file', str(approvals)]) == 0
    assert json.loads(capsys.readouterr().out)['queued'] == ['req-B']
    s = VoiceStore(db, epoch=EPOCH)
    assert s.get('req-B')['dispatch_state'] == 'not_started'
    with pytest.raises(SystemExit):
        cli.main(base + ['--mode', 'run'])  # dispatch requires an explicit command flag


def test_worker_syncs_terminal_call_then_refills_without_printing_private_data(tmp_path, capsys):
    from scripts.dispatch_voice_queue import run_worker
    q, s, now = setup_queue(tmp_path, concurrent=1)
    enqueue(q)
    first = q.dispatch_next(provider())
    now[0] += timedelta(seconds=1)
    rec = s.get('req-A')
    body = dict(id=first['provider_call_id'], agent_id=AGENT, status='no_answer',
                arguments={k: rec['request'][k] for k in ('request_id', 'asset_id', 'snapshot_id')},
                updated_at=now[0].isoformat(), tool_executions=[])
    c = client(Transport([body, configured_agent(), dict(call_id=str(uuid4()))]))
    run_worker(q, c, once=True)
    assert s.get('req-A')['status'] == 'no_answer'
    assert s.get('req-B')['dispatch_state'] == 'bound'
    assert q.status()['active'] == 1
    assert s.human_tasks('A')
    output = capsys.readouterr().out
    assert '+1202555' not in output
    assert 'SIMULATION' not in output


def test_failed_status_poll_does_not_release_capacity(tmp_path):
    q, s, now = setup_queue(tmp_path, concurrent=1)
    enqueue(q)
    q.dispatch_next(provider())
    now[0] += timedelta(seconds=5)
    assert q.sync_active(client(Transport(status=503))) == ['req-A']
    assert q.dispatch_next(provider()) is None


def test_three_per_second_pacing_and_clock_rollback(tmp_path):
    q, s, now = setup_queue(tmp_path, concurrent=3, rate=3)
    enqueue(q)
    c = provider()
    q.dispatch_next(c)
    now[0] -= timedelta(seconds=1)
    assert q.dispatch_next(c) is None
    now[0] = EPOCH + timedelta(microseconds=333333)
    assert q.dispatch_next(c) is None
    now[0] += timedelta(microseconds=1)
    assert q.dispatch_next(c)['request_id'] == 'req-B'


def test_reenqueue_reports_cancelled_and_started_requests_accurately(tmp_path):
    q, s, _ = setup_queue(tmp_path)
    enqueue(q)
    q.cancel('req-A')
    q.dispatch_next(provider())
    report, _ = enqueue(q)
    assert report['queued'] == ['req-C']
    assert report['existing'] == {'req-A': 'cancelled', 'req-B': 'started'}


def test_crash_holds_creation_until_provider_reconciliation_even_with_free_slots(tmp_path):
    q, s, now = setup_queue(tmp_path, concurrent=5)
    enqueue(q)
    with pytest.raises(KeyboardInterrupt):
        q.dispatch_next(client(Transport(error=KeyboardInterrupt())))
    assert s.get('req-A')['dispatch_state'] == 'attempting'
    s.close()
    now[0] += timedelta(seconds=5)
    reopened = VoiceStore(tmp_path / 'queue.sqlite', epoch=EPOCH, clock=lambda: now[0])
    q = api().VoiceCallQueue(reopened, api().CallQueueConfig(5, 1))
    assert q.dispatch_next(provider()) is None
    call_id = str(uuid4())
    body = dict(id=call_id, agent_id=AGENT, status='no_answer', updated_at=now[0].isoformat(),
                arguments=dict(request_id='req-A', asset_id='A', snapshot_id='snapshot-demo'),
                tool_executions=[])
    reopened.sync(client(Transport(body)), 'req-A', provider_call_id=call_id)
    assert q.dispatch_next(provider()) is None  # recovery also respects pacing
    now[0] += timedelta(seconds=1)
    assert q.dispatch_next(provider())['request_id'] == 'req-B'
