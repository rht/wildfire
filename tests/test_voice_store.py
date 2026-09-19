import importlib
from dataclasses import asdict, replace
from datetime import timedelta
import json
import pytest
from tests.test_voice_interview import EPOCH, request, result
from tests.test_voice_provider import CALL, AGENT, TRUNK, Transport, client


def store(path=':memory:', now=EPOCH):
    m = importlib.import_module('fireline.voice_store')
    return m.VoiceStore(path, epoch=EPOCH, clock=lambda: now)


def bound(s, req=None):
    req = req or request()
    s.register(req)
    s.bind(req.request_id, 'call-B')
    return req


def event(s, event_id='event-1', status='completed', at=None, **changes):
    return s.record_lifecycle(**(dict(event_id=event_id, request_id='req-B', asset_id='B',
        snapshot_id='snapshot-demo', provider_call_id='call-B', status=status,
        observed_at=(at or EPOCH).isoformat()) | changes))


def test_requests_immutable_and_provider_binding_unique(tmp_path):
    s = store(tmp_path / 'voice.sqlite')
    bound(s)
    s.register(request())
    with pytest.raises(ValueError):
        s.register(request(asset_id='A'))
    with pytest.raises(ValueError):
        s.bind('req-B', 'other-call')
    s.register(request(request_id='req-A', asset_id='A'))
    with pytest.raises(ValueError):
        s.bind('req-A', 'call-B')
    assert s.get('req-B')['provider_call_id'] == 'call-B'


def test_duplicate_and_old_lifecycle_do_not_downgrade_terminal(tmp_path):
    s = store(tmp_path / 'voice.sqlite', now=EPOCH + timedelta(minutes=2))
    bound(s)
    assert event(s, at=EPOCH + timedelta(minutes=1)) == 'accepted'
    assert event(s, at=EPOCH + timedelta(minutes=1)) == 'duplicate'
    assert event(s, 'old', 'ringing') == 'ignored'
    assert event(s, 'late', 'in_progress', EPOCH + timedelta(minutes=2)) == 'ignored'
    assert s.get('req-B')['status'] == 'completed'
    s.close()
    s = store(tmp_path / 'voice.sqlite', now=EPOCH + timedelta(minutes=2))
    assert event(s, at=EPOCH + timedelta(minutes=1)) == 'duplicate'
    assert s.get('req-B')['status'] == 'completed'


def test_event_id_reuse_with_changed_content_is_rejected():
    s = store()
    bound(s)
    event(s)
    with pytest.raises(ValueError):
        event(s, status='failed')
    assert s.human_tasks('B')


@pytest.mark.parametrize('changes', [{'asset_id': 'A'}, {'snapshot_id': 'wrong'},
                                    {'request_id': 'wrong'}, {'provider_call_id': 'wrong'},
                                    {'observed_at': '2026-09-19T12:01:00Z'}])
def test_wrong_lifecycle_association_or_future_time_never_updates(changes):
    s = store()
    bound(s)
    with pytest.raises(ValueError):
        event(s, **changes)
    assert s.get('req-B')['status'] == 'queued'
    assert s.human_tasks('B')


def test_result_does_not_manufacture_completed_provider_state():
    s = store()
    bound(s)
    s.record_result(result())
    assert s.get('req-B')['status'] == 'queued'
    assert s.assessment('req-B').status == 'failed'
    event(s)
    assert s.assessment('req-B').status == 'completed'
    assert s.assessment('req-B').confidence == .95


def test_live_assistance_report_and_tasks_survive_restart(tmp_path):
    from fireline.evacuation_readiness import coordinate_evacuation
    from fireline.priority_examples import load_scenario
    path = tmp_path / 'reported-assistance.sqlite'
    s = store(path)
    s.register(request(input_mode='live'))
    s.bind('req-B', 'call-B')
    event(s)
    evidence = dict(result().evidence, can_self_evacuate='I cannot leave without help.')
    s.record_result(result(can_self_evacuate=False, confidence=None,
                           confidence_basis=None, evidence=evidence))
    plan = coordinate_evacuation(load_scenario('fixtures/static_priority.json'),
                                [s.assessment('req-B')], [], [])
    s.record_plan(plan, snapshot_id='snapshot-demo')
    s.close()
    reopened = store(path)
    try:
        saved = reopened.get('req-B')['result']
        assert saved['can_self_evacuate'] is False
        assert saved['evidence']['can_self_evacuate'] == 'I cannot leave without help.'
        reasons = {t['reason'] for t in reopened.tasks.tasks('B')}
        assert {'voice:human_callback', 'voice:arrange_assistance'} <= reasons
        assert reopened.assessment('req-B').confidence is None
    finally:
        reopened.close()


def test_bad_later_answers_and_old_human_request_cannot_be_erased():
    s = store(now=EPOCH + timedelta(minutes=2))
    bound(s)
    event(s)
    s.record_result(result(observed_at='2026-09-19T12:01:00Z'))
    s.record_result(result(wants_human=True))
    s.record_result(result(observed_at='2026-09-19T12:02:00Z'))
    assert s.assessment('req-B').confidence is None
    assert s.human_tasks('B')


def test_restart_replay_preserves_assignment_and_task_count(tmp_path):
    path = tmp_path / 'voice.sqlite'
    s = store(path)
    bound(s)
    event(s)
    s.record_result(result(wants_human=True))
    tasks = s.human_tasks('B')
    task_id = tasks[0]['task_id']
    s.tasks.conn.execute("UPDATE tasks SET status='assigned', assigned_team_id='synthetic-team' WHERE task_id=?", (task_id,))
    s.tasks.conn.commit()
    s.close()
    s = store(path)
    s.record_result(result(wants_human=True))
    assert len(s.human_tasks('B')) == len(tasks)
    assert s.tasks.get(task_id)['assigned_team_id'] == 'synthetic-team'
    s.register(request(request_id='next-request', snapshot_id='next-snapshot'))
    assert len(s.human_tasks('B')) == len(tasks)
    assert s.tasks.get(task_id)['status'] == 'assigned'


def test_failed_transfer_keeps_followup_after_completed_call():
    s = store()
    bound(s)
    event(s)
    s.record_result(result(transfer_status='failed'))
    assert s.assessment('req-B').confidence is None
    assert 'transfer_failed' in s.get('req-B')['followup_reasons']
    assert s.human_tasks('B')


def test_no_answer_and_completed_without_answers_keep_unknown():
    for status in ('no_answer', 'completed', 'failed', 'declined'):
        s = store()
        bound(s)
        event(s, status=status)
        assert s.assessment('req-B').confidence is None
        assert s.assessment('req-B').can_self_evacuate is None
        assert s.human_tasks('B')


def test_ambiguous_dispatch_cannot_repeat_after_restart(tmp_path):
    import requests
    path = tmp_path / 'voice.sqlite'
    s = store(path)
    req = request(input_mode='live')
    s.register(req)
    transport = Transport([{'id': AGENT, 'sip_outbound_trunk_id': TRUNK}, requests.Timeout('private synthetic text')])
    c = client(transport)
    with pytest.raises(RuntimeError):
        s.start(c, req.request_id, mode='outbound', approved_target=req.contact_number)
    assert s.get(req.request_id)['dispatch_state'] == 'outcome_unknown'
    s.close()
    s = store(path)
    with pytest.raises(ValueError, match='attempt'):
        s.start(c, req.request_id, mode='outbound', approved_target=req.contact_number)
    assert len(transport.calls) == 2
    assert sum(c[0] == 'POST' for c in transport.calls) == 1
    assert s.human_tasks('B')


def test_poll_verifies_arguments_and_never_promotes_tool_success_to_connection():
    s = store()
    s.register(request())
    s.bind('req-B', CALL)
    body = dict(id=CALL, agent_id=AGENT, arguments=dict(request_id='req-B', asset_id='B', snapshot_id='snapshot-demo'),
        status='completed', updated_at=EPOCH.isoformat(),
        tool_executions=[dict(tool_kind='transfer_call', outcome='succeeded')])
    s.poll(client(Transport(body)), 'req-B')
    assert s.get('req-B')['status'] == 'completed'
    assert s.get('req-B')['transfer_status'] != 'connected'
    assert s.human_tasks('B')
    body['arguments']['asset_id'] = 'A'
    with pytest.raises(ValueError):
        s.poll(client(Transport(body)), 'req-B')


def test_unknown_provider_status_is_durable_and_requires_review():
    s = store()
    s.register(request())
    s.bind('req-B', CALL)
    body = dict(id=CALL, agent_id=AGENT, arguments=dict(request_id='req-B', asset_id='B', snapshot_id='snapshot-demo'),
        status='unrecognized-status', updated_at=EPOCH.isoformat())
    s.poll(client(Transport(body)), 'req-B')
    assert s.get('req-B')['status'] == 'queued'
    assert 'unknown_provider_status' in s.get('req-B')['followup_reasons']


def test_bearer_ingestion_rejects_unauthorized_and_cannot_set_lifecycle_or_confidence():
    m = importlib.import_module('fireline.voice_ingest')
    s = store()
    bound(s)
    body = asdict(result())
    with pytest.raises(PermissionError):
        m.ingest(s, json.dumps(body).encode(), authorization='Bearer wrong', token='synthetic-token-32-characters-long')
    with pytest.raises(ValueError):
        m.ingest(s, json.dumps(body).encode(), authorization='Bearer synthetic-token-32-characters-long', token='synthetic-token-32-characters-long')
    allowed = {k: v for k, v in body.items() if k in m.RESULT_FIELDS}
    response = m.ingest(s, json.dumps(allowed).encode(), authorization='Bearer synthetic-token-32-characters-long', token='synthetic-token-32-characters-long')
    assert response['accepted']
    assert s.assessment('req-B').confidence is None
    assert s.get('req-B')['status'] == 'queued'


def test_new_adverse_evidence_after_task_closed_creates_fresh_task_without_reopening():
    s = store()
    bound(s)
    previous = s.human_tasks('B')[0]['task_id']
    s.tasks.set_status(previous, 'done')
    s.record_result(result(wants_human=True))
    assert s.tasks.get(previous)['status'] == 'done'
    assert len([t for t in s.human_tasks('B') if t['status'] != 'done']) == 1
    count = len(s.human_tasks('B'))
    s.record_result(result(wants_human=True))
    assert len(s.human_tasks('B')) == count


def test_late_transfer_request_does_not_erase_failure():
    s = store(now=EPOCH + timedelta(minutes=2))
    bound(s)
    event(s, 'failed-transfer', 'in_progress', EPOCH + timedelta(minutes=2), transfer_status='failed')
    assert event(s, 'old-transfer', 'in_progress', EPOCH + timedelta(minutes=1), transfer_status='requested') == 'ignored'
    assert s.get('req-B')['transfer_status'] == 'failed'


def test_callback_receiver_timestamps_observation_without_inventing_evidence_time():
    m = importlib.import_module('fireline.voice_ingest')
    s = store()
    bound(s)
    payload = {k: v for k, v in asdict(result()).items() if k in m.RESULT_FIELDS}
    payload.pop('observed_at', None)
    auth = dict(authorization='Bearer synthetic-token-32-characters-long', token='synthetic-token-32-characters-long')
    m.ingest(s, json.dumps(payload).encode(), **auth)
    saved = s.get('req-B')['result']
    assert saved['observed_at'] == EPOCH.isoformat()
    assert saved['evidence_time_basis'] == 'receipt_only'
    s.clock = lambda: EPOCH + timedelta(minutes=2)
    assert m.ingest(s, json.dumps(payload).encode(), **auth)['outcome'] == 'duplicate'
    assert s.get('req-B')['result']['observed_at'] == EPOCH.isoformat()


def test_completion_without_interview_after_callback_closed_creates_new_followup():
    s = store()
    bound(s)
    s.tasks.set_status(s.human_tasks('B')[0]['task_id'], 'done')
    event(s)
    assert any(t['status'] != 'done' for t in s.human_tasks('B'))


def test_task_and_call_registration_are_atomic(tmp_path, monkeypatch):
    s = store(tmp_path / 'atomic.sqlite')
    original = s.tasks.create_task
    def fail_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('synthetic crash')
    monkeypatch.setattr(s.tasks, 'create_task', fail_after_insert)
    with pytest.raises(RuntimeError):
        s.register(request())
    assert s.tasks.tasks() == []
    with pytest.raises(ValueError):
        s.get('req-B')


def test_two_connections_cannot_claim_same_dispatch(tmp_path):
    path = tmp_path / 'shared.sqlite'
    one, two = store(path), store(path)
    one.register(request())
    t = Transport(dict(call_id=CALL, room_name='room', livekit_url='wss://synthetic.invalid',
                       livekit_token='synthetic-token', max_session_seconds=300, message='created'))
    c = client(t)
    one.start(c, 'req-B', mode='browser')
    with pytest.raises(ValueError, match='attempt'):
        two.start(c, 'req-B', mode='browser')
    assert len(t.calls) == 1


def test_provider_declined_failure_and_unknown_status_preserve_earlier_terminal():
    s = store()
    bound(s)
    event(s)
    event(s, 'conflict', 'failed')
    assert s.get('req-B')['status'] == 'completed'
    assert s.assessment('req-B').confidence is None


def test_wsgi_receiver_routes_authentication_and_body_limits(tmp_path):
    from io import BytesIO
    m = importlib.import_module('fireline.voice_ingest')
    path = tmp_path / 'endpoint.sqlite'
    s = store(path)
    bound(s)
    s.close()
    token = 'synthetic-token-32-characters-long'
    app = m.make_app(lambda: store(path), token)
    payload = json.dumps({k: v for k, v in asdict(result()).items() if k in m.RESULT_FIELDS}).encode()
    def invoke(**changes):
        env = dict(PATH_INFO='/voice/results', REQUEST_METHOD='POST', CONTENT_LENGTH=str(len(payload)),
                   HTTP_AUTHORIZATION='Bearer ' + token)
        env['wsgi.input'] = BytesIO(payload)
        env.update(changes)
        response = []
        body = b''.join(app(env, lambda status, headers: response.append(status)))
        return response[0], json.loads(body)
    assert invoke()[0] == '200 OK'
    assert invoke(HTTP_AUTHORIZATION='Bearer wrong')[0] == '401 Unauthorized'
    assert invoke(PATH_INFO='/wrong')[0] == '404 Not Found'
    assert invoke(REQUEST_METHOD='GET')[0] == '405 Method Not Allowed'
    assert invoke(CONTENT_LENGTH='32769')[0] == '400 Bad Request'
    assert invoke(CONTENT_LENGTH='invalid')[0] == '400 Bad Request'
