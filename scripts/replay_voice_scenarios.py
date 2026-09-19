#!/usr/bin/env python3
"""Run synthetic voice outcomes through both algorithms and persist UI-readable state."""
import argparse
import json
from pathlib import Path

from fireline.voice_replay import MockReplay, ROOT, load_cases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='all')
    parser.add_argument('--db-dir', type=Path, default=ROOT / 'data/voice-replay')
    parser.add_argument('--step', action='store_true', help='apply at most one pending event per selected case')
    parser.add_argument('--output', type=Path, help='write the synthetic summary as JSON')
    args = parser.parse_args(argv)
    cases = [c for c in load_cases() if args.case in ('all', c['name'])]
    if not cases:
        parser.error('unknown mock case')
    results = []
    for case in cases:
        session = MockReplay(args.db_dir / (case['name'] + '.sqlite'), case['name'])
        try:
            state = session.state()
            while state['pending_events']:
                state = session.advance()
                if args.step:
                    break
            plan = state['plan']
            modes = {r['asset_id']: r['mode'] for r in plan['locations']}
            passed = None if state['pending_events'] else all(modes[k] == v for k, v in case['expected_modes'].items())
            result = dict(case=case['name'], revision=state['revision'], pending_events=state['pending_events'],
                contact_order=[r['asset_id'] for r in plan['contacts']['ranked']],
                remaining_windows={r['asset_id']: r['slack_min'] for r in plan['contacts']['ranked']},
                response_order=[s['site_id'] for s in plan['response']['steps']],
                response_review_required=state['response_review_required'], modes=modes,
                destinations={r['asset_id']: r['destination_id'] for r in plan['locations']},
                remaining_capacity=plan['remaining_capacity'],
                voice_briefings=state['voice_briefings'],
                last_call_road_warning_acknowledged={r['asset_id']: r.get('road_warning_acknowledged') for r in state['calls']},
                current_road_warning_acknowledged={r['asset_id']: r['current_road_warning_acknowledged'] for r in plan['locations']},
                reported_help={r['asset_id']: r['reported_needs_assistance'] for r in state['calls']},
                escalation_reasons={r['asset_id']: r['human_followup_reasons'] for r in state['calls']},
                evacuation_status={r['asset_id']: r['evacuation_status'] for r in plan['locations']},
                expected_modes=case['expected_modes'], passed=passed,
                open_tasks=[{'asset_id': t['asset_id'], 'reason': t['reason'], 'status': t['status']}
                            for t in sorted(state['tasks'], key=lambda t: (t['asset_id'], t['reason'])) if t['status'] != 'done'])
            results.append(result)
            print(f"{case['name']}: {'PENDING' if passed is None else 'PASS' if passed else 'FAIL'} | "
                  f"contacts {' -> '.join(result['contact_order'])} | crew {' -> '.join(result['response_order'])} | {len(modes)} buildings")
        finally:
            session.close()
    report = dict(schema_version='mock-voice-report-1', input_mode='synthetic', dispatch=False,
                  live_validation=False, cases=results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    return 1 if any(c['passed'] is False for c in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
