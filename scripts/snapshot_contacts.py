"""Enqueue approved snapshot contacts locally. There is deliberately no dispatch option."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from fireline.snapshot_contacts import enqueue_snapshot_contacts
from fireline.voice_models import utc
from fireline.voice_queue import CallQueueConfig, VoiceCallQueue
from fireline.voice_store import VoiceStore


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique_object)


def _private_database(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('database must be a regular file')
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return str(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('snapshot', 'contacts', 'approvals', 'requests'):
        parser.add_argument('--' + name, required=True, help='Path to ' + name + ' JSON')
    parser.add_argument('--epoch', required=True, help='Shared scenario epoch, UTC ISO timestamp')
    parser.add_argument('--now-at', help='UTC evaluation time; defaults to current UTC')
    parser.add_argument('--db', required=True, help='Shared private queue database for this account')
    parser.add_argument('--max-age-min', type=float, help='Defaults to config.FRESHNESS stale threshold')
    parser.add_argument('--buffer-min', type=float, help='Defaults to config.CONTACT_POLICY buffer')
    args = parser.parse_args(argv)
    store = None
    try:
        # Read all inputs before creating any local artifact. Never echo raw errors
        # from private files or providers; only the public adapter report goes out.
        snapshot, contacts, approvals, requests = [_read(getattr(args, name))
            for name in ('snapshot', 'contacts', 'approvals', 'requests')]
        epoch = utc(args.epoch)
        now_at = args.now_at or datetime.now(timezone.utc).isoformat()
        utc(now_at)
        limits = CallQueueConfig.from_env()
        store = VoiceStore(_private_database(args.db), epoch=epoch)
        report = enqueue_snapshot_contacts(VoiceCallQueue(store, limits), snapshot,
            contacts, approvals, requests, epoch=args.epoch, now_at=now_at,
            max_age_min=args.max_age_min, buffer_min=args.buffer_min)
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        print('Snapshot enqueue failed: input or database validation failed.', file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
