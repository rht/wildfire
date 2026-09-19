"""Only injected offline HTTP transports: never contact SLNG in the test suite."""
import importlib
import json
import requests
import pytest
from tests.test_voice_interview import request

AGENT = '00000000-0000-4000-8000-000000000001'
CALL = '00000000-0000-4000-8000-000000000002'
TRUNK = '00000000-0000-4000-8000-000000000003'


class Transport:
    def __init__(self, body=None, status=200, error=None):
        self.body, self.status, self.error = body, status, error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        response = requests.Response()
        response.status_code = self.status
        body = self.body.pop(0) if isinstance(self.body, list) else self.body
        if isinstance(body, Exception):
            raise body
        response._content = json.dumps(body).encode()
        return response


def client(transport, **changes):
    m = importlib.import_module('fireline.slng_voice')
    return m.SlngClient(m.SlngConfig(**(dict(api_key='synthetic-secret', agent_id=AGENT,
                          outbound_connection_id=TRUNK) | changes)), transport=transport)


def test_missing_key_is_not_configured_without_http():
    t = Transport()
    c = client(t, api_key=None)
    assert c.configuration_status()['status'] == 'not_configured'
    with pytest.raises(ValueError, match='SLNG_API_KEY'):
        c.create_web_session(request())
    assert not t.calls


def test_browser_uses_documented_endpoint_and_no_phone_number():
    t = Transport(dict(call_id=CALL, room_name='room', livekit_url='wss://synthetic.invalid',
                       livekit_token='synthetic-token', max_session_seconds=300, message='created'))
    result = client(t).create_web_session(request())
    method, url, args = t.calls[0]
    assert method == 'POST'
    assert url == f'https://api.agents.slng.ai/v1/agents/{AGENT}/web-sessions'
    assert args['json']['arguments']['asset_id'] == 'B'
    assert 'phone_number' not in args['json']
    assert args['timeout'] == (5, 20)
    assert args['allow_redirects'] is False
    assert result['call_id'] == CALL


def test_dispatch_requires_live_request_and_explicit_authorized_target():
    t = Transport([{'id': AGENT, 'sip_outbound_trunk_id': TRUNK}, dict(call_id=CALL, message='created')])
    c = client(t)
    for req, approved in [(request(), '+12025550123'), (request(input_mode='live'), None),
                           (request(input_mode='live'), '+12025550124')]:
        with pytest.raises(ValueError):
            c.dispatch(req, approved_target=approved)
    assert not t.calls
    assert c.dispatch(request(input_mode='live'), approved_target='+12025550123')['call_id'] == CALL
    assert t.calls[-1][2]['json']['phone_number'] == '+12025550123'


def test_missing_outbound_connection_blocks_dispatch():
    t = Transport()
    with pytest.raises(ValueError, match='outbound'):
        client(t, outbound_connection_id=None).dispatch(request(input_mode='live'),
                                                         approved_target='+12025550123')
    assert not t.calls


@pytest.mark.parametrize('error', [requests.Timeout('secret phone'), requests.ConnectionError('secret phone')])
def test_creation_failure_is_ambiguous_sanitized_and_never_retried(error):
    t = Transport(error=error)
    with pytest.raises(RuntimeError, match='outcome_unknown') as exc:
        client(t).create_web_session(request())
    assert 'secret' not in str(exc.value)
    assert len(t.calls) == 1


@pytest.mark.parametrize('status', [301, 401, 429, 500])
def test_response_error_never_echoes_provider_payload(status):
    t = Transport({'message': 'private conversation synthetic-secret'}, status=status)
    with pytest.raises(RuntimeError) as exc:
        client(t).get_call(CALL)
    assert 'private' not in str(exc.value)
    assert 'synthetic-secret' not in str(exc.value)
    assert len(t.calls) == 1


def test_agent_configuration_has_explicit_models_prompt_and_shared_tools():
    m = importlib.import_module('fireline.slng_voice')
    doc = m.agent_configuration(request(), name='Synthetic readiness', region='eu-central',
        models={'stt': 'chosen-stt', 'llm': 'chosen-llm', 'tts': 'chosen-tts', 'tts_voice': 'chosen-voice'})
    assert doc['language'] == 'en'
    assert doc['tool_mode'] == 'shared'
    assert 'AI' in doc['system_prompt']
    assert '{{scenario_notice}}' in doc['greeting']
    assert doc['template_defaults']['scenario_notice'] == 'SIMULATION'
    t = Transport({'id': AGENT})
    assert client(t, agent_id=None).create_agent(doc)['id'] == AGENT
    assert t.calls[0][1] == 'https://api.agents.slng.ai/v1/agents'


def test_poll_rejects_wrong_provider_identity():
    for body in ({'id': 'different', 'agent_id': AGENT}, {'id': CALL, 'agent_id': 'different'}):
        with pytest.raises(ValueError, match='association'):
            client(Transport(body)).get_call(CALL)


def test_poll_uses_bearer_authenticated_get():
    t = Transport({'id': CALL, 'agent_id': AGENT})
    assert client(t).get_call(CALL)['id'] == CALL
    assert t.calls[0][0] == 'GET'
    assert t.calls[0][2]['headers']['Authorization'] == 'Bearer synthetic-secret'


def test_config_repr_does_not_expose_secret_and_ids_are_validated():
    c = client(Transport())
    assert 'synthetic-secret' not in repr(c.config)
    with pytest.raises(ValueError):
        client(Transport(), agent_id='../other')


def test_result_tool_uses_documented_raw_bearer_format_and_locks_identity():
    m = importlib.import_module('fireline.slng_voice')
    tool = m.result_tool_configuration('https://synthetic.invalid/voice/results', 'VOICE_RESULT_TOKEN')
    assert tool['config']['auth'] == {'type': 'bearer', 'secret_name': 'VOICE_RESULT_TOKEN'}
    assert tool['config']['webhook_format'] == 'raw'
    assert tool['config']['parameters']['additionalProperties'] is False
    attachment = m.result_tool_attachment(AGENT, 1)
    assert attachment['argument_overrides']['provider_call_id'] == '{{@call_id}}'
    assert attachment['argument_overrides']['request_id'] == '{{request_id}}'


def test_outbound_checks_actual_agent_connection_before_dispatch():
    t = Transport({'sip_outbound_trunk_id': None})
    with pytest.raises(ValueError, match='outbound connection'):
        client(t).dispatch(request(input_mode='live'), approved_target='+12025550123')
    assert len(t.calls) == 1
    assert t.calls[0][0] == 'GET'
    assert t.calls[0][1] == f'https://api.agents.slng.ai/v1/agents/{AGENT}'


def test_invalid_success_response_is_ambiguous_and_never_retried():
    t = Transport({'call_id': 'not-a-uuid'})
    with pytest.raises(RuntimeError, match='outcome_unknown'):
        client(t).create_web_session(request())
    assert len(t.calls) == 1
