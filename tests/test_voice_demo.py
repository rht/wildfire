import json
import os
import subprocess
import sys


def run(*args):
    return subprocess.run([sys.executable, 'scripts/voice_demo.py', *args], capture_output=True,
                          text=True, env=dict(os.environ, SLNG_API_KEY=''))


def test_no_key_demo_reuses_location_fixture_and_never_confirms_evacuation(tmp_path):
    proc = run('--db', str(tmp_path / 'demo.sqlite'))
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report['input_mode'] == 'synthetic'
    assert report['dispatch'] is False
    rows = {r['asset_id']: r for r in report['cases'][0]['locations']}
    assert {aid: r['mode'] for aid, r in rows.items()} == {
        'A': 'assisted_evacuation', 'B': 'self_evacuate', 'C': 'undetermined'}
    assert all(r['evacuation_status'] == 'not_confirmed' for r in rows.values())
    assert all(any(t['asset_id'] == aid for t in report['remaining_tasks']) for aid in rows)
    assert 'confirm_departure' in str(report['remaining_tasks'])
    assert 'confirm_arrival' in str(report['remaining_tasks'])


def test_all_adverse_cases_are_demonstrated_and_restart_does_not_duplicate_tasks(tmp_path):
    args = ('--case', 'all', '--db', str(tmp_path / 'demo.sqlite'))
    first, second = run(*args), run(*args)
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    one, two = json.loads(first.stdout), json.loads(second.stdout)
    assert len(one['remaining_tasks']) == len(two['remaining_tasks'])
    cases = {c['name']: c for c in two['cases']}
    for name in ('human_requested', 'low_confidence', 'unknown_confidence', 'missing_evidence',
                 'contradictory', 'no_answer', 'bad_audio', 'failed_transfer'):
        b = next(r for r in cases[name]['locations'] if r['asset_id'] == 'B')
        assert b['mode'] == 'undetermined'
        assert b['human_followup']


def test_explicit_live_mode_without_request_cannot_dispatch():
    proc = run('--mode', 'outbound')
    assert proc.returncode != 0
    assert 'required' in proc.stderr


def test_agent_config_is_offline_and_requires_explicit_model_selection(tmp_path):
    config = tmp_path / 'agent.json'
    proc = run('--mode', 'agent-config', '--output', str(config), '--stt', 'selected-stt',
               '--llm', 'selected-llm', '--tts', 'selected-tts', '--voice', 'selected-voice')
    assert proc.returncode == 0, proc.stderr
    document = json.loads(config.read_text())
    assert document['models']['stt'] == 'selected-stt'
    assert document['template_defaults']['scenario_notice'] == 'SIMULATION'


def test_browser_reserves_private_output_before_network(tmp_path, monkeypatch):
    from dataclasses import asdict
    from scripts import voice_demo
    from tests.test_voice_interview import request, EPOCH
    from tests.test_voice_provider import client, Transport, CALL
    req_path = tmp_path / 'request.json'
    req_path.write_text(json.dumps(asdict(request())))
    output = tmp_path / 'existing.json'
    output.write_text('do not overwrite')
    t = Transport(dict(call_id=CALL, room_name='room', livekit_url='wss://synthetic.invalid',
                       livekit_token='synthetic-token', max_session_seconds=300, message='created'))
    c = client(t)
    monkeypatch.setattr(voice_demo.SlngConfig, 'from_env', lambda: c.config)
    monkeypatch.setattr(voice_demo, 'SlngClient', lambda config: c)
    code = voice_demo.main(['--mode', 'browser', '--request-file', str(req_path),
        '--output', str(output), '--db', str(tmp_path / 'private.sqlite'), '--epoch', EPOCH.isoformat()])
    assert code == 1
    assert t.calls == []
    assert output.read_text() == 'do not overwrite'
