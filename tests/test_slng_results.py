import importlib
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import pytest
from tests.test_voice_interview import EPOCH, request
from tests.test_voice_provider import AGENT, CALL, Transport, client
from tests.test_voice_store import store


def payload():
    return json.loads(Path('fixtures/voice/slng_completed.json').read_text(encoding='utf-8'))


def test_completed_memory_answers_survive_redacted_transcript_and_restart(tmp_path):
    path = tmp_path / 'results.sqlite'
    s = store(path)
    s.register(request(input_mode='recorded'))
    assert s.sync(client(Transport(payload())), 'req-B', provider_call_id=CALL)['result'] == 'accepted'
    s.close()
    s = store(path)
    saved = s.get('req-B')
    assert saved['status'] == 'completed'
    assert saved['result']['can_self_evacuate'] is False
    assert saved['result']['transport_available'] is True
    assert saved['result']['acknowledged'] is None
    assert saved['result']['evidence']['can_self_evacuate'] == 'I need help to leave.'
    assert saved['result']['evidence_verification']['can_self_evacuate'] == 'provider_reported'
    assert saved['result']['confidence'] is None
    assert saved['result']['human_followup_required'] is True
    count = len(s.tasks.tasks())
    assert s.sync(client(Transport(payload())), 'req-B')['result'] == 'duplicate'
    assert len(s.tasks.tasks()) == count
    assert s.human_tasks('B')


def test_unbound_or_cross_agent_calls_cannot_import():
    s = store()
    s.register(request())
    with pytest.raises(ValueError, match='bound'):
        s.sync(client(Transport(payload())), 'req-B')
    s.sync(client(Transport(payload())), 'req-B', provider_call_id=CALL)
    other = '99999999-9999-4999-8999-999999999999'
    body = dict(payload(), agent_id=other)
    with pytest.raises(ValueError, match='agent'):
        s.sync(client(Transport(body), agent_id=other), 'req-B')
    s.register(request(request_id='req-C', asset_id='C'))
    with pytest.raises(ValueError, match='associated'):
        s.sync(client(Transport(payload())), 'req-C', provider_call_id=CALL)
    assert s.get('req-C')['provider_call_id'] is None


@pytest.mark.parametrize('arguments', [dict(request_id='wrong'), dict(asset_id='C'), dict(request_id='req-B')])
def test_explicit_binding_cannot_override_conflicting_or_partial_provider_arguments(arguments):
    s = store()
    s.register(request())
    body = dict(payload(), arguments=arguments)
    with pytest.raises(ValueError, match='association'):
        s.sync(client(Transport(body)), 'req-B', provider_call_id=CALL)
    assert s.get('req-B')['provider_call_id'] is None
    assert s.get('req-B')['result'] is None


def test_matching_provider_arguments_allow_existing_binding():
    s = store()
    s.register(request())
    s.bind('req-B', CALL)
    body = dict(payload(), arguments=dict(request_id='req-B', asset_id='B', snapshot_id='snapshot-demo'))
    assert s.sync(client(Transport(body)), 'req-B')['result'] == 'accepted'


def test_memory_normalizer_verifies_only_exact_user_quotes_and_keeps_unknowns():
    m = importlib.import_module('fireline.slng_results')
    body = payload()
    body['livekit_session_report']['chat_history']['items'] = [
        {'role': 'user', 'content': ['I need help to leave.']},
        {'role': 'assistant', 'content': ['We have a car.']}]
    result = m.normalize_call(request(), body)
    assert result.evidence_verification['can_self_evacuate'] == 'transcript_verified'
    assert result.evidence_verification['transport_available'] == 'provider_reported'
    assert result.acknowledged is None
    assert result.evidence['instruction_received'] == 'I heard your message.'
    body['memory_variables'][0]['value'] = 'probably'
    result = m.normalize_call(request(), body)
    assert result.can_self_evacuate is None


def test_later_missing_memory_cannot_erase_assistance_or_human_request():
    s = store(now=EPOCH + timedelta(minutes=1))
    s.register(request())
    body = payload()
    s.sync(client(Transport(body)), 'req-B', provider_call_id=CALL)
    body['updated_at'] = (EPOCH + timedelta(minutes=1)).isoformat()
    body['memory_variables'] = []
    s.sync(client(Transport(body)), 'req-B')
    result = s.get('req-B')['result']
    assert result['can_self_evacuate'] is False
    assert result['wants_human'] is True
    assert result['evidence']['wants_human'] == 'Please ask a person to call me.'
    assert result['evidence_verification']['wants_human'] == 'provider_reported'
    assert 'human_requested' in s.get('req-B')['followup_reasons']


def test_bad_result_does_not_leave_binding_or_lifecycle():
    s = store()
    s.register(request())
    body = dict(payload(), updated_at='invalid')
    with pytest.raises(ValueError):
        s.sync(client(Transport(body)), 'req-B', provider_call_id=CALL)
    assert s.get('req-B')['provider_call_id'] is None
    assert s.get('req-B')['status'] == 'queued'


def test_cli_sync_fetches_only_and_outputs_no_private_evidence(tmp_path, monkeypatch, capsys):
    from scripts import voice_demo
    req_path = tmp_path / 'request.json'
    req_path.write_text(json.dumps(asdict(request(input_mode='recorded'))))
    c = client(Transport(payload()))
    monkeypatch.setattr(voice_demo.SlngConfig, 'from_env', lambda: c.config)
    monkeypatch.setattr(voice_demo, 'SlngClient', lambda config: c)
    assert voice_demo.main(['--mode', 'sync', '--provider-call-id', CALL,
        '--request-file', str(req_path), '--db', str(tmp_path / 'voice.sqlite'),
        '--epoch', EPOCH.isoformat()]) == 0
    output = capsys.readouterr()
    assert 'I need help' not in output.out
    assert '+12025550123' not in output.out
    assert json.loads(output.out)['result'] == 'accepted'
    assert [call[0] for call in c.transport.calls] == ['GET']


def test_adverse_evidence_keeps_original_time_and_assistance_task_after_partial_update():
    s = store(now=EPOCH + timedelta(minutes=1))
    s.register(request())
    body = payload()
    s.sync(client(Transport(body)), 'req-B', provider_call_id=CALL)
    body['updated_at'] = (EPOCH + timedelta(minutes=1)).isoformat()
    body['memory_variables'] = []
    s.sync(client(Transport(body)), 'req-B')
    saved = s.get('req-B')['result']
    assert saved['evidence_observed_at']['can_self_evacuate'] == EPOCH.isoformat()
    assert {t['reason'] for t in s.tasks.tasks('B')} >= {'voice:human_callback', 'voice:arrange_assistance'}


def test_nonterminal_call_only_records_lifecycle():
    s = store()
    s.register(request())
    outcome = s.sync(client(Transport(dict(payload(), status='in_progress'))), 'req-B', provider_call_id=CALL)
    assert outcome['result'] == 'pending'
    assert s.get('req-B')['status'] == 'in_progress'
    assert s.get('req-B')['result'] is None


def test_older_assistance_report_creates_task_without_replacing_newer_interview():
    s = store(now=EPOCH + timedelta(minutes=1))
    s.register(request())
    newer = dict(payload(), updated_at=(EPOCH + timedelta(minutes=1)).isoformat(), memory_variables=[])
    s.sync(client(Transport(newer)), 'req-B', provider_call_id=CALL)
    outcome = s.sync(client(Transport(payload())), 'req-B')
    assert outcome['result'] == 'ignored'
    saved = s.get('req-B')['result']
    assert saved['can_self_evacuate'] is None
    assert saved['observed_at'] == (EPOCH + timedelta(minutes=1)).isoformat()
    assert 'voice:arrange_assistance' in {task['reason'] for task in s.tasks.tasks('B')}


def test_provider_record_timestamp_does_not_claim_speech_observation_time():
    s = store()
    s.register(request())
    s.sync(client(Transport(payload())), 'req-B', provider_call_id=CALL)
    saved = s.get('req-B')['result']
    assert saved['evidence_time_basis'] == 'provider_record_update'
    assert 'evidence_time_unknown' in saved['human_followup_reasons']


def test_invalid_finalization_rolls_back_answers_and_remains_eligible():
    s = store()
    s.register(request())
    body = dict(payload(), finalized_at='invalid')
    with pytest.raises(ValueError):
        s.sync(client(Transport(body)), 'req-B', provider_call_id=CALL)
    assert s.get('req-B')['provider_call_id'] is None
    assert s.get('req-B')['result'] is None
    assert s.conn.execute('SELECT count(*) FROM voice_result_finalizations').fetchone()[0] == 0


@pytest.mark.parametrize('report', ['malformed', {'chat_history': []},
    {'chat_history': {'items': {}}}, {'chat_history': {'items': ['malformed']}}])
def test_malformed_transcript_is_controlled_and_atomic(report):
    s = store()
    s.register(request())
    body = dict(payload(), livekit_session_report=report)
    with pytest.raises(ValueError, match='transcript'):
        s.sync(client(Transport(body)), 'req-B', provider_call_id=CALL)
    assert s.get('req-B')['provider_call_id'] is None
    assert s.get('req-B')['result'] is None
