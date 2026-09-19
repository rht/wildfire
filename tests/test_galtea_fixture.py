from scripts.prepare_galtea_voice import prepare
import pytest


def test_inbound_fixture_resolves_bindings_and_detaches_existing_trunks():
    source = dict(system_prompt='Location: {{incident_brief}} {{road_warning_brief}}',
                  greeting='{{scenario_notice}}', template_defaults={'old': 'value'},
                  template_variable_options={'incident_brief': {'required': True}},
                  sip_outbound_trunk_id='old-outbound', sip_inbound_trunk_id='old-inbound')
    config = prepare(source, {'incident_brief': 'Willow House',
        'road_warning_brief': 'Avoid Mill Road', 'scenario_notice': 'This is a simulation'})
    prompt = config['system_prompt']
    assert '{{' not in prompt + config['greeting']
    assert 'Willow House' in prompt and 'Mill Road' in prompt
    assert 'simulation' in config['greeting']
    assert config['template_defaults'] == config['template_variable_options'] == {}
    assert config['sip_outbound_trunk_id'] is None
    assert config['sip_inbound_trunk_id'] is None
    assert config['region'] == 'eu-central'
    assert config['name'] == 'fireline-galtea-synthetic'
    assert source['sip_outbound_trunk_id'] == 'old-outbound'


def test_missing_fixture_argument_is_rejected():
    with pytest.raises(ValueError, match='Unresolved'):
        prepare(dict(system_prompt='{{unknown}}', greeting='Simulation'), {})
