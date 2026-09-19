"""Offline contracts for dynamic dispatch; all phone values are test fixtures."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from fireline.slng_voice import agent_configuration
from tests.test_voice_interview import request
from tests.test_voice_provider import AGENT, CALL, TRUNK, Transport, client


ARGUMENTS = {
    'request_id': 'req-B', 'asset_id': 'B', 'snapshot_id': 'snapshot-demo',
    'incident_brief': 'SIMULATION: analyst brief only.',
    'scenario_notice': 'Analyst-authorized contact', 'language': 'en',
    'road_warning_brief': 'No road restrictions supplied. This does not establish that any road is safe.',
}


def dynamic_agent():
    return dict(id=AGENT, sip_outbound_trunk_id=TRUNK,
                template_variables={key: {'required': True} for key in ARGUMENTS})


def test_exact_outbound_contract_retains_all_bindings():
    transport = Transport([dynamic_agent(), {'call_id': CALL}])
    result = client(transport).dispatch(request(input_mode='live'), approved_target='+12025550123')
    assert result == {'call_id': CALL}
    assert [c[:2] for c in transport.calls] == [
        ('GET', f'https://api.agents.slng.ai/v1/agents/{AGENT}'),
        ('POST', f'https://api.agents.slng.ai/v1/agents/{AGENT}/calls')]
    assert transport.calls[1][2]['json'] == {'phone_number': '+12025550123', 'arguments': ARGUMENTS}
    assert transport.calls[1][2]['allow_redirects'] is False


@pytest.mark.parametrize('metadata', [None, {}, [], {'asset_id': {'required': True}}])
def test_incompatible_agent_never_posts_or_falls_back_to_argument_free(metadata):
    config = dynamic_agent()
    config['template_variables'] = metadata
    transport = Transport([config, {'call_id': CALL}])
    with pytest.raises(ValueError, match='template'):
        client(transport).dispatch(request(input_mode='live'), approved_target='+12025550123')
    assert [c[0] for c in transport.calls] == ['GET']


def test_missing_template_metadata_never_posts():
    transport = Transport([dict(id=AGENT, sip_outbound_trunk_id=TRUNK), {'call_id': CALL}])
    with pytest.raises(ValueError, match='template'):
        client(transport).dispatch(request(input_mode='live'), approved_target='+12025550123')
    assert len(transport.calls) == 1


@pytest.mark.parametrize('extra', [{'required': True}, 'invalid', {'required': 'yes'}])
def test_unbound_required_or_malformed_template_blocks_dispatch(extra):
    config = dynamic_agent()
    config['template_variables']['private-extra-name'] = extra
    transport = Transport([config, {'call_id': CALL}])
    with pytest.raises(ValueError, match='template') as exc:
        client(transport).dispatch(request(input_mode='live'), approved_target='+12025550123')
    assert 'private-extra-name' not in str(exc.value)
    assert len(transport.calls) == 1


def test_additional_defaulted_template_is_compatible():
    config = dynamic_agent()
    config['template_variables']['department'] = {'required': False, 'default': 'Readiness team'}
    transport = Transport([config, {'call_id': CALL}])
    assert client(transport).dispatch(request(input_mode='live'), approved_target='+12025550123')['call_id'] == CALL


@pytest.mark.parametrize('operation', ['dispatch', 'create_web_session'])
def test_oversized_road_warning_is_rejected_without_http_or_truncation(operation):
    req = request(input_mode='live', road_warnings=[dict(
        road_id='road-1', road_name='R' * 512, reason='PRIVATE' * 70, source='S' * 100)])
    transport = Transport([dynamic_agent(), {'call_id': CALL}])
    if operation == 'create_web_session':
        transport = Transport(dict(call_id=CALL, room_name='room', livekit_url='wss://synthetic.invalid',
                                   livekit_token='synthetic-token', max_session_seconds=300))
    kwargs = {'approved_target': req.contact_number} if operation == 'dispatch' else {}
    with pytest.raises(ValueError, match='argument') as exc:
        getattr(client(transport), operation)(req, **kwargs)
    assert 'PRIVATE' not in str(exc.value)
    assert transport.calls == []


def test_rest_configuration_declares_every_sent_variable_without_identity_defaults():
    config = agent_configuration(request(), name='Test', region='eu-central',
        models=dict(stt='stt', llm='llm', tts='tts', tts_voice='voice'))
    variables = set(re.findall(r'\{\{(\w+)\}\}', config['system_prompt'] + config['greeting']))
    assert set(ARGUMENTS) <= variables
    assert not set(('request_id', 'asset_id', 'snapshot_id')) & config['template_defaults'].keys()


def test_dynamic_package_compiles_to_bound_hosted_contract(tmp_path):
    binary = os.environ.get('UNMUTE_BIN') or shutil.which('unmute')
    if not binary:
        pytest.skip('Set UNMUTE_BIN to run offline Unmute package validation/compilation')
    package = Path(__file__).resolve().parents[1] / 'voice-agent' / 'dynamic'
    assert package.is_dir(), 'dynamic deployment package is missing'
    dest = tmp_path / 'dynamic'
    shutil.copytree(package, dest, ignore=shutil.ignore_patterns('build', '.env*'))
    for command in ('validate', 'compile'):
        subprocess.run([str(Path(binary).resolve()), command, str(dest), '--target', 'slng'],
                       check=True, capture_output=True, text=True)
    compiled = json.loads((dest / 'build/slng/agent.json').read_text())
    variables = set(re.findall(r'\{\{(\w+)\}\}', compiled['system_prompt'] + compiled['greeting']))
    assert variables == set(ARGUMENTS)
    assert not set(('request_id', 'asset_id', 'snapshot_id')) & compiled.get('template_defaults', {}).keys()
    runtime_names = {v['name'] for v in compiled['runtime_variables']}
    assert {'identity_confirmed', 'whole_household_confirmed', 'wants_human',
            'acknowledged', 'can_self_evacuate', 'transport_available',
            'road_warning_acknowledged', 'road_warning_acknowledged_evidence'} <= runtime_names
    assert not set(ARGUMENTS) & runtime_names
