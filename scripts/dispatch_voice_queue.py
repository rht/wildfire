#!/usr/bin/env python3
"""Inspect or enqueue without calling; --mode run --dispatch starts approved calls."""
import argparse
import json
import sqlite3
import sys
import time

from fireline.contact_priority import ContactPolicy
from fireline.priority_examples import load_scenario
from fireline.slng_voice import ProviderError, SlngClient, SlngConfig
from fireline.voice_models import CallRequest, utc
from fireline.voice_queue import VoiceCallQueue
from fireline.voice_store import VoiceStore
from scripts.voice_demo import database, read_json, ROOT


def run_worker(queue, client, *, once=False):
    """Keep slots filled while polling lifecycle/results every five seconds."""
    next_sync = 0
    while True:
        if time.monotonic() >= next_sync:
            errors = queue.sync_active(client)
            if errors:
                print(json.dumps(dict(sync_errors=errors)), flush=True)
            next_sync = time.monotonic() + 5
        try:
            dispatched = queue.dispatch_next(client)
            if dispatched:
                print(json.dumps(dict(dispatched=dispatched)), flush=True)
        except ProviderError:
            # VoiceStore retained the uncertain slot and created human follow-up.
            print(json.dumps(dict(dispatch_error='reconciliation_required')), flush=True)
        if once:
            print(json.dumps(queue.status()), flush=True)
            return
        time.sleep(0.1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['status', 'enqueue', 'run', 'cancel'], default='status')
    parser.add_argument('--db', required=True, help='one private persistent database for all account workers')
    parser.add_argument('--epoch', required=True, help='common UTC scenario epoch')
    parser.add_argument('--locations', default=str(ROOT / 'fixtures/static_priority.json'))
    parser.add_argument('--requests-file', help='private JSON array of CallRequest objects')
    parser.add_argument('--approval-file', help='private JSON object mapping request IDs to approved numbers')
    parser.add_argument('--request-id', help='pending request to cancel')
    parser.add_argument('--dispatch', action='store_true', help='explicitly start the outbound worker')
    parser.add_argument('--once', action='store_true', help='one sync/dispatch pass, then exit')
    args = parser.parse_args(argv)
    if args.db == ':memory:':
        parser.error('a persistent database is required')
    if args.mode == 'run' and not args.dispatch:
        parser.error('--dispatch is required to place calls')
    if args.mode == 'enqueue' and not (args.requests_file and args.approval_file):
        parser.error('--requests-file and --approval-file are required')
    if args.mode == 'cancel' and not args.request_id:
        parser.error('--request-id is required')
    store = None
    try:
        store = VoiceStore(database(args.db), epoch=utc(args.epoch))
        queue = VoiceCallQueue(store)
        if args.mode == 'enqueue':
            scenario = load_scenario(args.locations)
            requests = [CallRequest(**r) for r in read_json(args.requests_file)]
            approvals = read_json(args.approval_file)
            if not isinstance(approvals, dict):
                raise ValueError('invalid approvals')
            report = queue.enqueue(scenario.locations, requests, approved_targets=approvals,
                                   policy=ContactPolicy(buffer_min=scenario.buffer_min))
        elif args.mode == 'run':
            run_worker(queue, SlngClient(SlngConfig.from_env()), once=args.once)
            return 0
        elif args.mode == 'cancel':
            queue.cancel(args.request_id)
            report = queue.status()
        else:
            report = queue.status()
        print(json.dumps(report, indent=2))
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, ProviderError):
        print('Queue operation failed. Check configuration and durable call state; '
              'reconcile uncertain dispatches before retrying.', file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
