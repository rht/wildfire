"""Build a fixed synthetic inbound test package without deploying or calling."""
import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare(compiled, arguments):
    config = copy.deepcopy(compiled)
    for field in ('system_prompt', 'greeting'):
        for name, value in arguments.items():
            config[field] = config[field].replace('{{' + name + '}}', value)
        if '{{' in config[field]:
            raise ValueError('Unresolved fixture argument')
    config.update(name='fireline-galtea-synthetic', region='eu-central',
        template_defaults={}, template_variable_options={},
        sip_inbound_trunk_id=None, sip_outbound_trunk_id=None)
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiled', type=Path,
        default=ROOT / 'voice-agent/dynamic/build/slng/agent.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/galtea-agent/agent.json')
    args = parser.parse_args(argv)
    fixture = json.loads((ROOT / 'fixtures/voice_script_probes.json').read_text())
    config = prepare(json.loads(args.compiled.read_text()), fixture['arguments'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2) + '\n')
    print(f'Prepared {args.output}. Not deployed; inbound routing remains required.')


if __name__ == '__main__':
    main()
