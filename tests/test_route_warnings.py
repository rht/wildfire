"""Named road restrictions must constrain allocation and reach the call boundary."""
from dataclasses import asdict, replace
import json

import pytest

from fireline import evacuation_readiness as er
from fireline.slng_voice import agent_configuration, call_arguments
from fireline.voice_interview import interview_prompt, normalize_result
from fireline.voice_replay import MockReplay
from fireline.route_guidance import road_warning_version
from tests.test_evacuation_readiness import inputs
from tests.test_voice_interview import request, result

WARNING = dict(road_id='mill-road', road_name='Mill Road',
               reason='fire reported beside the bridge', source='Synthetic incident command update')


def test_dangerous_road_overrides_confirmed_route_and_chooses_alternative():
    scenario, call, centre, route = inputs()
    call = replace(call, acknowledged_road_warning_version=road_warning_version([WARNING]))
    unsafe = replace(route, road_ids=('mill-road',))
    alternate = replace(centre, centre_id='annex', name='Annex')
    safe = replace(route, centre_id='annex', travel_min=3, road_ids=('ridge-road',))
    plan = er.coordinate_evacuation(scenario, [call], [centre, alternate], [unsafe, safe],
                                   road_warnings=[WARNING])
    row = next(r for r in plan['locations'] if r['asset_id'] == 'B')
    assert row['destination_id'] == 'annex'
    assert row['route_road_ids'] == ['ridge-road']
    assert row['road_warnings'] == [WARNING]
    assert 'road_danger_reported' in row['destination_rejections'][0]['reasons']
    assert 'communicate_road_warning' in row['tasks']
    assert plan['remaining_capacity'] == {'centre': 10, 'annex': 2}


@pytest.mark.parametrize('roads', [('mill-road',), ()])
def test_no_named_eligible_alternative_retains_warning_and_human_followup(roads):
    scenario, call, centre, route = inputs()
    call = replace(call, acknowledged_road_warning_version=road_warning_version([WARNING]))
    plan = er.coordinate_evacuation(scenario, [call], [centre], [replace(route, road_ids=roads)],
                                   road_warnings=[WARNING])
    row = next(r for r in plan['locations'] if r['asset_id'] == 'B')
    assert row['destination_id'] is None
    assert row['human_followup']
    assert row['road_warnings'] == [WARNING]
    assert row['mode'] == 'undetermined'


def test_call_payload_keeps_road_name_reason_and_source_without_baking_in_first_household():
    req = request(road_warnings=[WARNING])
    payload = call_arguments(req)
    assert 'Do not take Mill Road' in payload['road_warning_brief']
    assert WARNING['reason'] in payload['road_warning_brief']
    assert WARNING['source'] in payload['road_warning_brief']
    assert 'Do not take Mill Road' in interview_prompt(req)
    config = agent_configuration(req, name='Test', region='eu-central',
        models=dict(stt='test', llm='test', tts='test', tts_voice='test'))
    assert '{{road_warning_brief}}' in config['system_prompt']
    assert 'Mill Road' not in config['system_prompt']
    assert 'Mill Road' not in call_arguments(request())['road_warning_brief']


@pytest.mark.parametrize('ack,evidence,followup', [
    (True, 'I understand: I must avoid Mill Road.', False),
    (False, 'I did not understand which road.', True),
    (None, None, True),
    (True, None, True),
])
def test_generic_readback_does_not_substitute_for_road_warning_acknowledgement(ack, evidence, followup):
    req = request(road_warnings=[WARNING])
    evidence_map = dict(result().evidence)
    if evidence:
        evidence_map['road_warning_acknowledged'] = evidence
    normalized = normalize_result(req, result(road_warning_acknowledged=ack, evidence=evidence_map))
    assert normalized.human_followup_required is followup
    assert ('road_warning_unconfirmed' in normalized.human_followup_reasons) is followup


@pytest.mark.parametrize('warning', [dict(WARNING, source=''), dict(WARNING, road_name=''),
                                     dict(WARNING, reason=''), dict(WARNING, road_id='')])
def test_unsourced_or_unnamed_warning_is_rejected_at_call_boundary(warning):
    with pytest.raises(ValueError):
        request(road_warnings=[warning])


def test_village_warning_reaches_persisted_call_and_missing_ack_changes_readiness(tmp_path):
    for case, want in [('village_road_warning', 'self_evacuate'),
                       ('village_road_warning_unconfirmed', 'undetermined')]:
        replay = MockReplay(tmp_path / (case + '.sqlite'), case)
        while replay.state()['pending_events']:
            replay.advance()
        state = replay.state()
        row = next(r for r in state['plan']['locations'] if r['asset_id'] == 'H2')
        call = next(c for c in state['calls'] if c['asset_id'] == 'H2')
        assert row['mode'] == want
        assert 'Do not take Mill Road' in state['voice_briefings']['H2']
        assert replay.store.get(call['request_id'])['request']['road_warnings'][0]['road_id'] == 'mill-road'
        if want == 'self_evacuate':
            assert row['destination_id'] == 'ANNEX'
        else:
            assert 'road_warning_unconfirmed' in call['human_followup_reasons']
        replay.close()


def test_new_closure_refreshes_warning_but_does_not_rewrite_old_call(tmp_path):
    replay = MockReplay(tmp_path / 'closure.sqlite', 'village_road_closure')
    for _ in range(6):
        replay.advance()
    before = replay.state()
    old_call = next(c for c in before['calls'] if c['asset_id'] == 'H1')
    state = replay.advance()
    assert 'Do not take Oak Lane' in state['voice_briefings']['H1']
    assert 'H1' in replay.updates()[-1]['changed_asset_ids']
    assert not replay.store.get(old_call['request_id'])['request']['road_warnings']
    assert any(t['asset_id'] == 'H1' and t['reason'] == 'voice:communicate_road_warning' for t in state['tasks'])
    replay.close()


def test_old_request_without_optional_warnings_can_be_registered_idempotently():
    from tests.test_voice_store import store, bound
    s = store()
    req = bound(s)
    old = asdict(req)
    old.pop('road_warnings')
    s.conn.execute('UPDATE voice_calls SET request=? WHERE request_id=?',
                   (json.dumps(old, sort_keys=True, separators=(',', ':')), req.request_id))
    s.conn.commit()
    s.register(req)
    with pytest.raises(ValueError, match='immutable'):
        s.register(replace(req, road_warnings=[WARNING]))
    s.close()


def test_authenticated_result_ingest_preserves_road_warning_readback():
    from fireline.voice_ingest import ingest
    from fireline.slng_voice import result_tool_configuration
    from tests.test_voice_store import store, bound
    from fireline.voice_models import ANSWER_FIELDS
    s = store()
    bound(s, request(road_warnings=[WARNING]))
    record = asdict(result(road_warning_acknowledged=True))
    payload = {k: record[k] for k in ('request_id', 'asset_id', 'snapshot_id', 'provider_call_id',
               'evidence', 'contradictory', 'bad_audio', 'road_warning_acknowledged') + ANSWER_FIELDS}
    payload['evidence']['road_warning_acknowledged'] = 'I will avoid Mill Road.'
    token = 'test-token-' + 'x' * 32
    reply = ingest(s, json.dumps(payload).encode(), authorization='Bearer ' + token, token=token)
    assert reply['accepted']
    assert s.get('req-B')['result']['road_warning_acknowledged'] is True
    assert s.get('req-B')['result']['evidence']['road_warning_acknowledged'] == 'I will avoid Mill Road.'
    tool = result_tool_configuration('https://synthetic.invalid/voice/results', 'VOICE_TOKEN')
    assert 'road_warning_acknowledged' in tool['config']['parameters']['properties']
    s.close()


def test_agent_template_is_independent_of_overlapping_incident_and_road_text():
    models = dict(stt='test', llm='test', tts='test', tts_voice='test')
    overlapping = request(incident_brief='Mill Road', road_warnings=[WARNING])
    template = agent_configuration(overlapping, name='Test', region='eu-central', models=models)['system_prompt']
    ordinary = agent_configuration(request(), name='Test', region='eu-central', models=models)['system_prompt']
    assert template == ordinary
    assert '<road_restrictions>{{road_warning_brief}}</road_restrictions>' in template
    assert WARNING['reason'] not in template


def test_changed_warning_needs_fresh_acknowledgement_and_work_after_previous_task_done(tmp_path):
    replay = MockReplay(tmp_path / 'change.sqlite', 'village_road_warning')
    while replay.state()['pending_events']:
        replay.advance()
    before = replay.state()
    for task in before['tasks']:
        if task['asset_id'] == 'H2' and task['reason'] in ('voice:communicate_road_warning', 'voice:human_callback'):
            replay.store.tasks.set_status(task['task_id'], 'done')
    replay.case['events'].append(dict(kind='route_update', asset_id='H2', confirmed=True,
        road_warnings=[dict(WARNING, road_id='oak-lane', road_name='Oak Lane')]))
    state = replay.advance()
    row = next(r for r in state['plan']['locations'] if r['asset_id'] == 'H2')
    assert row['human_followup']
    assert row['current_road_warning_acknowledged'] is False
    assert 'road_warning_update_unconfirmed' in row['reasons']
    assert row['mode'] == 'undetermined'
    for reason in ('voice:communicate_road_warning', 'voice:human_callback'):
        assert any(t['asset_id'] == 'H2' and t['reason'] == reason and t['status'] != 'done' for t in state['tasks'])
    count = len(state['tasks'])
    replay.apply_event(0)
    assert len(replay.state()['tasks']) == count
    replay.close()
