from fireline.slng_voice import call_arguments
from tests.test_voice_interview import request


def test_drill_configuration_separates_spoken_greeting_and_internal_rules():
    from fireline.sable_voice import sable_drill_configuration
    doc = sable_drill_configuration(request(), region='eu-central', models={
        'stt': 'test-stt', 'llm': 'test-llm', 'tts': 'test-tts', 'tts_voice': 'test-voice'})
    assert doc['greeting'] == "Hello, I'm Sable, an AI assistant. This is a test call for our wildfire response app. Can you hear me clearly?"
    assert doc['outbound_greeting'] == doc['greeting']
    assert doc['inbound_greeting'] == doc['greeting']
    assert doc['enable_interruptions'] is True
    for key in call_arguments(request()):
        assert '{{' + key + '}}' in doc['system_prompt']
    assert 'mock' not in doc['system_prompt'].lower()
    assert 'Never read' in doc['system_prompt']
    assert 'Can everyone at location {{asset_id}} leave without emergency assistance?' in doc['system_prompt']


def test_drill_scope_and_answer_capture_remain_conservative():
    from fireline.sable_voice import sable_drill_configuration
    doc = sable_drill_configuration(request(), region='eu-central', models={
        'stt': 'test-stt', 'llm': 'test-llm', 'tts': 'test-tts', 'tts_voice': 'test-voice'})
    prompt = doc['system_prompt']
    for phrase in ('unrelated questions', 'pending question', 'Do not advance',
                   'exact quote', 'inconclusive', 'clear', 'stop', 'human',
                   'Never promise', 'No real travel', 'corrections', 'bad audio'):
        assert phrase in prompt
    names = {v['name'] for v in doc['runtime_variables']}
    for field in ('identity_confirmed','whole_household_confirmed','can_self_evacuate',
                  'transport_available','wants_human','acknowledged'):
        assert {field, field + '_evidence'} <= names


def test_english_drill_rejects_other_languages_instead_of_mixing_disclosure():
    import pytest
    from fireline.sable_voice import sable_drill_configuration
    with pytest.raises(ValueError, match='English'):
        sable_drill_configuration(request(language='es'), region='eu-central', models={
            'stt': 'test', 'llm': 'test', 'tts': 'test', 'tts_voice': 'test'})


def test_drill_model_route_and_terminal_policy_are_explicit():
    from fireline.sable_voice import sable_drill_configuration
    models = {'stt': 'test', 'llm': 'test', 'tts': 'test', 'tts_voice': 'test'}
    doc = sable_drill_configuration(request(), region='eu-central', models=models)
    assert doc['models'] == models
    assert 'second consecutive off-topic turn' in doc['system_prompt']
    assert 'record wants_human immediately' in doc['system_prompt']
    assert 'overwrite the old answer with inconclusive and clear' in doc['system_prompt']
    assert doc['idle_nudges']['final_hangup_text'] == "I'll end the call now. Thank you."


def test_early_help_does_not_confirm_identity_or_advance_household_answers():
    from fireline.sable_voice import DRILL_PROMPT
    assert 'If assistance is mentioned at ANY point' in DRILL_PROMPT
    assert 'without assuming identity, household coverage or transport' in DRILL_PROMPT
