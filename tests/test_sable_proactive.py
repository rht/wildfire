from dataclasses import asdict
import pytest
from tests.test_voice_interview import request, EPOCH
from fireline.slng_voice import call_arguments, agent_configuration
from fireline.sable_voice import sable_drill_configuration

MODELS = dict(stt='test', llm='test', tts='test', tts_voice='test')


def test_display_name_is_separate_from_internal_call_bindings():
    req = request(location_display_name='Demo Cedar Care Home')
    args = call_arguments(req)
    assert args['location_display_name'] == 'Demo Cedar Care Home'
    assert (args['request_id'], args['asset_id'], args['snapshot_id']) == ('req-B', 'B', 'snapshot-demo')
    assert 'location_display_name' not in call_arguments(request())
    assert 'Demo Cedar' not in repr(req)


@pytest.mark.parametrize('name', ['', 'B', 'Location B', 'x' * 257, 12])
def test_display_name_validation(name):
    with pytest.raises(ValueError, match='location_display_name'):
        request(location_display_name=name)


@pytest.mark.parametrize('builder', [agent_configuration, sable_drill_configuration])
def test_new_display_template_is_optional_for_old_requests(builder):
    kwargs = dict(region='eu-central', models=MODELS)
    if builder is agent_configuration:
        kwargs['name'] = 'Test'
    doc = builder(request(), **kwargs)
    assert '{{location_display_name}}' in doc['system_prompt']
    assert doc['template_variable_options']['location_display_name'] == {'required': False}
    assert doc['template_defaults']['location_display_name'] == ''
    assert 'Can you answer for everyone' not in doc['system_prompt']
    assert 'Is anyone else with you?' in doc['system_prompt']
    assert 'Does anyone there need help leaving the building?' in doc['system_prompt']


def test_old_persisted_request_remains_idempotent(tmp_path):
    from fireline.voice_store import VoiceStore, encoded
    store = VoiceStore(tmp_path / 'voice.sqlite', epoch=EPOCH, clock=lambda: EPOCH)
    req = request()
    store.register(req)
    old = asdict(req)
    old.pop('location_display_name')
    store.conn.execute('UPDATE voice_calls SET request=?', (encoded(old),))
    store.conn.commit()
    store.register(req)
    with pytest.raises(ValueError, match='immutable'):
        store.register(request(location_display_name='Demo Cedar Care Home'))
    store.close()


def test_briefing_adapter_uses_trusted_name_without_id_fallback():
    from fireline.call_briefing import build_call_request
    req = build_call_request(dict(asset_id='B', name='Demo Cedar Care Home'), None,
        dict(contact_number='+12025550123'), snapshot_id='snapshot-demo', request_id='req-B')
    assert req.location_display_name == 'Demo Cedar Care Home'
    req = build_call_request(dict(asset_id='B'), None, dict(contact_number='+12025550123'),
        snapshot_id='snapshot-demo', request_id='req-B')
    assert req.location_display_name is None


def test_callback_offer_uses_exact_user_wording_without_implying_acceptance():
    from fireline.voice_dialogue import READINESS_DIALOGUE
    assert 'Would you like an emergency responder to call you to further assist you?' in READINESS_DIALOGUE
    assert 'work out what help' not in READINESS_DIALOGUE
    assert 'discuss what help' not in READINESS_DIALOGUE
    assert 'An offer is not acceptance' in READINESS_DIALOGUE
    assert 'Honor a declined offer without repeating it' in READINESS_DIALOGUE


def test_uncertain_needs_remain_reviewable_after_callback_declined_or_accepted():
    from tests.test_voice_interview import result
    from fireline.voice_interview import normalize_result
    for accepted in (False, True, None):
        evidence = {'wants_human': 'Yes, please.' if accepted else 'No, thank you.'} if accepted is not None else {}
        normalized = normalize_result(request(input_mode='live'), result(
            can_self_evacuate=None, transport_available=None, whole_household_confirmed=None,
            wants_human=accepted, evidence=evidence, confidence=None, confidence_basis=None))
        assert normalized.wants_human is accepted
        assert normalized.can_self_evacuate is None
        assert normalized.transport_available is None
        assert normalized.whole_household_confirmed is None
        assert normalized.human_followup_required
        assert 'incomplete_answers' in normalized.human_followup_reasons


def test_known_individual_need_survives_unknown_group_coverage():
    from tests.test_voice_interview import result
    from fireline.voice_interview import normalize_result
    normalized = normalize_result(request(input_mode='live'), result(
        whole_household_confirmed=None, can_self_evacuate=False, transport_available=None,
        evidence={'can_self_evacuate': 'I need someone to help me leave.'}, confidence=None))
    assert normalized.can_self_evacuate is False
    assert normalized.whole_household_confirmed is None
    assert normalized.human_followup_required


def test_voice_configuration_rejects_bypassing_provider_output_filtering():
    models = {**MODELS, 'llm_kwargs': {'extra_body': {'slng_pure_proxy': True}}}
    with pytest.raises(ValueError, match='output filtering'):
        sable_drill_configuration(request(), region='eu-central', models=models)


def test_trusted_display_name_prefers_name_then_address_never_internal_alias():
    from fireline.voice_models import location_display_name
    assert location_display_name({'asset_id': 'B', 'name': 'Demo Cedar Care Home',
                                  'address': 'Exercise address'}) == 'Demo Cedar Care Home'
    assert location_display_name({'asset_id': 'B', 'name': 'Location B',
                                  'address': 'Exercise address'}) == 'Exercise address'
    assert location_display_name({'asset_id': 'B', 'name': 'Location B'}) is None


def test_sable_preserves_model_choice_and_blocks_reasoning_delimiters():
    from copy import deepcopy
    models = {**MODELS, 'llm_kwargs': {'temperature': 0.1, 'extra_body': {'stop': ['END']}}}
    before = deepcopy(models)
    doc = sable_drill_configuration(request(), region='eu-central', models=models)
    assert doc['models']['llm'] == models['llm']
    assert doc['models']['llm_kwargs']['extra_body']['stop'] == ['END', '<think>', '</think>']
    assert models == before


@pytest.mark.parametrize('stops', [42, ['a', 'b', 'c'], ['']])
def test_invalid_or_excess_stop_configuration_fails_explicitly(stops):
    with pytest.raises(ValueError, match='stop sequences'):
        sable_drill_configuration(request(), region='eu-central',
            models={**MODELS, 'llm_kwargs': {'extra_body': {'stop': stops}}})
