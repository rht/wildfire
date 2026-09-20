#!/usr/bin/env python3
"""Synthetic offline demo by default. Explicit provider operations require separate flags."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

from fireline.evacuation_readiness import coordinate_evacuation, ReceptionCentre, EvacuationRoute
from fireline.priority_examples import load_scenario
from fireline.slng_voice import SlngClient, SlngConfig, ProviderError, agent_configuration
from fireline.voice_interview import normalize_result
from fireline.voice_models import CallRequest, CallResult, utc
from fireline.voice_store import VoiceStore

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def private_output(path):
    """Reserve a private file before any operation that can create credentials."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')


def private_json(path, value):
    with private_output(path) as output:
        json.dump(value, output, indent=2)
        output.write('\n')


def database(path):
    if path != ':memory:':
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    return path


def offline(args):
    fixture = read_json(ROOT / 'fixtures/voice/interviews.json')
    readiness = read_json(ROOT / 'fixtures/evacuation_readiness.json')
    scenario = load_scenario(args.locations)
    epoch = utc(fixture['epoch'])
    cases = [c for c in fixture['cases'] if args.case in ('all', c['name'])]
    if not cases:
        raise ValueError('unknown synthetic case')
    store = VoiceStore(database(args.db), epoch=epoch, clock=lambda: epoch)
    reports = []
    try:
        for case in cases:
            assessments, results = [], []
            for interview in case['interviews']:
                aid = interview['asset_id']
                request_id = 'demo-' + case['name'] + '-' + aid
                req = CallRequest(request_id=request_id, asset_id=aid, snapshot_id=fixture['snapshot_id'],
                    contact_number=fixture['contact_number'], language=args.language,
                    incident_brief=fixture['incident_brief'], input_mode='synthetic')
                call_id = 'call-' + request_id
                store.register(req)
                store.bind(req.request_id, call_id)
                result = CallResult(request_id=request_id, asset_id=aid, snapshot_id=req.snapshot_id,
                    provider_call_id=call_id, status=interview['status'], observed_at=fixture['epoch'],
                    source='synthetic interview fixture', **interview['answers'])
                store.record_lifecycle(event_id='lifecycle-' + request_id, request_id=request_id,
                    asset_id=aid, snapshot_id=req.snapshot_id, provider_call_id=call_id,
                    status=interview['status'], observed_at=fixture['epoch'])
                store.record_result(result)
                results.append(asdict(normalize_result(req, result)))
                assessments.append(store.assessment(request_id))
            plan = coordinate_evacuation(scenario, assessments,
                [ReceptionCentre(**c) for c in readiness['centres']],
                [EvacuationRoute(**r) for r in readiness['routes']])
            store.record_plan(plan, snapshot_id=fixture['snapshot_id'])
            reports.append(dict(name=case['name'], call_results=results, locations=plan['locations']))
        return dict(input_mode='synthetic', dispatch=False, live_validation=False, cases=reports,
            remaining_tasks=[{k: t[k] for k in ('task_id', 'asset_id', 'reason', 'status', 'assigned_team_id')}
                             for t in store.tasks.tasks() if t['status'] != 'done'])
    finally:
        store.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['offline', 'check-config', 'agent-config', 'browser', 'outbound', 'poll', 'sync'], default='offline')
    p.add_argument('--case', default='baseline', help='baseline, all, or one synthetic case name')
    p.add_argument('--db', default='data/voice-demo.sqlite')
    p.add_argument('--locations', default=str(ROOT / 'fixtures/static_priority.json'))
    p.add_argument('--language', default='en')
    p.add_argument('--request-file', help='private JSON CallRequest, required for provider operations')
    p.add_argument('--provider-call-id', help='explicit call association for sync of an existing call')
    p.add_argument('--approval-file', help='private JSON with request_id and approved_target for outbound only')
    p.add_argument('--epoch', help='common UTC scenario epoch; required for provider operations')
    p.add_argument('--output', help='new private output file; required for browser tokens and agent config')
    p.add_argument('--region', default='eu-central')
    for flag in ('stt', 'llm', 'tts', 'voice'):
        p.add_argument('--' + flag)
    args = p.parse_args(argv)
    try:
        if args.mode == 'offline':
            report = offline(args)
        elif args.mode == 'agent-config':
            if not args.output or not all((args.stt, args.llm, args.tts, args.voice)):
                p.error('--output and explicit --stt --llm --tts --voice are required')
            req = CallRequest('config-demo', 'B', 'voice-synthetic-snapshot', '+12025550123', args.language,
                              'SIMULATION: analyst-provided brief.')
            config = agent_configuration(req, name='ResponsAra readiness interview', region=args.region,
                models=dict(stt=args.stt, llm=args.llm, tts=args.tts, tts_voice=args.voice))
            private_json(args.output, config)
            report = dict(status='configuration_written', dispatch=False, live_validation=False)
        elif args.mode == 'check-config':
            report = SlngClient(SlngConfig.from_env()).configuration_status()
        else:
            if not args.request_file or not args.epoch:
                p.error('--request-file and --epoch are required for provider operations')
            if args.db == 'data/voice-demo.sqlite' or args.db == ':memory:':
                p.error('a separate persistent --db is required for provider operations')
            if args.mode == 'browser' and not args.output:
                p.error('--output is required for private browser credentials')
            req = CallRequest(**read_json(args.request_file))
            approved = None
            if args.mode == 'outbound':
                if not args.approval_file:
                    p.error('--approval-file is required for an explicitly authorized outbound test')
                approval = read_json(args.approval_file)
                if approval.get('request_id') != req.request_id:
                    raise ValueError('approval request mismatch')
                approved = approval.get('approved_target')
            client = SlngClient(SlngConfig.from_env())
            store = VoiceStore(database(args.db), epoch=utc(args.epoch))
            try:
                store.register(req)
                if args.mode == 'sync':
                    report = store.sync(client, req.request_id, provider_call_id=args.provider_call_id)
                elif args.mode == 'poll':
                    report = dict(outcome=store.poll(client, req.request_id), live_validation=False)
                else:
                    if args.mode == 'browser':
                        with private_output(args.output) as output:
                            response = store.start(client, req.request_id, mode='browser')
                            json.dump(response, output, indent=2)
                    else:
                        response = store.start(client, req.request_id, mode='outbound', approved_target=approved)
                    report = dict(status='created', provider_call_id=response['call_id'], live_validation=False)
            finally:
                store.close()
        print(json.dumps(report, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, ProviderError):
        # Raw validation/provider errors can contain private paths/data; never dump tracebacks.
        print('Voice operation failed. Check configuration, private input and durable call state; do not retry an ambiguous dispatch.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
