"""Synthetic text-only speech probes; never connects to SLNG or a phone.

Without --run-live-nebius this only lists the scenarios. Live mode makes one
paid model request per scenario; responses require review, not keyword scoring.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/voice_script_probes.json'
SPEECH_ONLY = '''
Evaluation transport: text-only, with no tools available. Produce only the next
spoken response to the last respondent turn. Do not output tool syntax or claim
a successful save, callback or transfer. This probe cannot test tool execution.
'''


def probe(model, prompt, case):
    turns = [dict(turn) for turn in case['turns']]
    row = dict(id=case['id'], review_criteria=case['review_criteria'],
               conversation_turns=turns, review='pending')
    try:
        response = model.create(system=prompt, messages=turns, tools=[])
    except Exception as exc:
        return dict(row, status='error', error_type=type(exc).__name__)
    spoken = '\n'.join(block.text for block in response.content if block.type == 'text').strip()
    if spoken:
        turns.append(dict(role='assistant', content=spoken))
    complete = response.stop_reason == 'end_turn' and bool(spoken)
    return dict(row, status='generated' if complete else 'incomplete',
                stop_reason=response.stop_reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live-nebius', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--case', help='Run or list one named probe')
    args = parser.parse_args(argv)
    fixture = json.loads(FIXTURE.read_text())
    cases = [case for case in fixture['cases'] if args.case is None or case['id'] == args.case]
    if not cases:
        parser.error('unknown probe case')
    if not args.run_live_nebius:
        for case in cases:
            print(f"{case['id']} ({case['package']}): {'; '.join(case['review_criteria'])}")
        print('No network requests. Use --run-live-nebius --output PATH to capture model responses.')
        return 0
    if not args.output:
        parser.error('--output is required for a live probe')
    from fireline.env import load_env
    from fireline.llm import NebiusLLM
    load_env()
    model = NebiusLLM()
    report = dict(schema_version='voice-script-probes-1',
        created_at=datetime.now(timezone.utc).isoformat(), model=model.model,
        input_mode='synthetic', scope='single-turn text continuations; preceding turns are fixtures',
        hosted_agent_tested=False, audio_tested=False, runtime_variables_tested=False,
        galtea_evaluation=False, dispatch=False,
        fixture_sha256=hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), cases=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        path = ROOT / fixture['packages'][case['package']]
        raw = path.read_text()
        prompt = raw
        for key, value in (fixture['arguments'] | case.get('arguments', {})).items():
            prompt = prompt.replace('{{' + key + '}}', value)
        if '{{' in prompt:
            raise ValueError('Unresolved prompt binding')
        prompt += SPEECH_ONLY
        row = probe(model, prompt, case)
        row.update(package=case['package'], prompt_path=str(path.relative_to(ROOT)),
                   prompt_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                   resolved_system_prompt=prompt)
        report['cases'].append(row)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        print(f"{case['id']}: {row['status']} (review pending)", flush=True)
    return int(any(row['status'] != 'generated' for row in report['cases']))


if __name__ == '__main__':
    raise SystemExit(main())
