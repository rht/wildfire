"""Durable call facts and answer evidence, with atomic linkage to analyst tasks.

One connection per worker. BEGIN IMMEDIATE serializes deduplication across processes.
No automatic task completion, dispatch retry, evacuation or transfer confirmation.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from .tasks import TaskStore
from .voice_models import CallRequest, CallResult, TERMINAL, STATUSES, association, identifier, utc
from .voice_interview import normalize_result, to_assessment

SCHEMA = '''
CREATE TABLE IF NOT EXISTS voice_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voice_calls (
 request_id TEXT PRIMARY KEY, request TEXT NOT NULL, provider_call_id TEXT UNIQUE,
 status TEXT NOT NULL DEFAULT 'queued', lifecycle_at TEXT, result TEXT,
 followup_reasons TEXT NOT NULL DEFAULT '[]', transfer_status TEXT,
 dispatch_state TEXT NOT NULL DEFAULT 'not_started');
CREATE TABLE IF NOT EXISTS voice_events (
 event_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voice_task_links (
 request_id TEXT NOT NULL, kind TEXT NOT NULL, task_id TEXT NOT NULL,
 PRIMARY KEY (request_id, kind));
'''


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class VoiceStore:
    def __init__(self, path=':memory:', *, epoch, clock=None):
        utc(epoch.isoformat())
        self.epoch = epoch
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.tasks = TaskStore(path, clock=self.clock)
        self.conn = self.tasks.conn
        self.conn.executescript(SCHEMA)
        with self._transaction():
            row = self.conn.execute("SELECT value FROM voice_meta WHERE key='epoch'").fetchone()
            if row and utc(row[0]) != epoch:
                raise ValueError('database scenario epoch mismatch')
            self.conn.execute("INSERT OR IGNORE INTO voice_meta VALUES ('epoch', ?)", (epoch.isoformat(),))

    @contextmanager
    def _transaction(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self):
        self.tasks.close()

    def get(self, request_id):
        row = self.conn.execute('SELECT * FROM voice_calls WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            raise ValueError('unknown call request')
        record = dict(row)
        for key in ('request', 'result', 'followup_reasons'):
            record[key] = json.loads(record[key]) if record[key] is not None else None
        return record

    def _time(self, observed_at):
        now = self.clock()
        utc(now.isoformat())
        if not self.epoch <= utc(observed_at) <= now:
            raise ValueError('evidence outside scenario time range')

    def _task(self, request_id, asset_id, snapshot_id, kind, *, fresh_event=False):
        link = self.conn.execute('SELECT task_id FROM voice_task_links WHERE request_id=? AND kind=?',
                                 (request_id, kind)).fetchone()
        if link:
            if not fresh_event or self.tasks.get(link[0])['status'] != 'done':
                return link[0]
            self.conn.execute('DELETE FROM voice_task_links WHERE request_id=? AND kind=?', (request_id, kind))
        action = 'request_resources' if kind == 'arrange_assistance' else 'contact_facility'
        reason = 'voice:' + kind
        # Reuse held work across requests/snapshots. A new request may create work after an
        # analyst closed the old task; replay of the original request cannot reopen it.
        existing = self.conn.execute("SELECT task_id FROM tasks WHERE asset_id=? AND action=? AND reason=? AND status!='done' ORDER BY id LIMIT 1",
                                     (asset_id, action, reason)).fetchone()
        task_id = existing[0] if existing else self.tasks.create_task(asset_id, action, reason,
                    snapshot_id=snapshot_id, suggested=True, commit=False)['task_id']
        self.conn.execute('INSERT INTO voice_task_links VALUES (?, ?, ?)', (request_id, kind, task_id))
        return task_id

    def _followup(self, record, reasons=(), *, fresh_event=False):
        req = record['request']
        merged = sorted(set(record['followup_reasons']) | set(reasons))
        self.conn.execute('UPDATE voice_calls SET followup_reasons=? WHERE request_id=?',
                          (encoded(merged), req['request_id']))
        self._task(req['request_id'], req['asset_id'], req['snapshot_id'], 'human_callback', fresh_event=fresh_event)

    def human_tasks(self, asset_id):
        return [t for t in self.tasks.tasks(asset_id) if t['reason'] == 'voice:human_callback']

    def register(self, request):
        payload = encoded(asdict(request))
        with self._transaction():
            existing = self.conn.execute('SELECT request FROM voice_calls WHERE request_id=?', (request.request_id,)).fetchone()
            if existing and existing[0] != payload:
                raise ValueError('request association is immutable')
            self.conn.execute('INSERT OR IGNORE INTO voice_calls (request_id, request) VALUES (?, ?)',
                              (request.request_id, payload))
            self._followup(self.get(request.request_id))

    def bind(self, request_id, provider_call_id):
        identifier(provider_call_id, 'provider_call_id')
        with self._transaction():
            record = self.get(request_id)
            if record['provider_call_id'] not in (None, provider_call_id):
                raise ValueError('provider call association is immutable')
            try:
                self.conn.execute("UPDATE voice_calls SET provider_call_id=?, dispatch_state='bound' WHERE request_id=?",
                                  (provider_call_id, request_id))
            except sqlite3.IntegrityError:
                raise ValueError('provider call already associated') from None

    def record_lifecycle(self, *, event_id, request_id, asset_id, snapshot_id,
                         provider_call_id, status, observed_at, transfer_status=None, raw_status=None):
        identifier(event_id, 'event_id')
        if status not in STATUSES or transfer_status not in (None, 'requested', 'failed'):
            raise ValueError('unsupported lifecycle or unverified transfer')
        self._time(observed_at)
        payload = dict(request_id=request_id, asset_id=asset_id, snapshot_id=snapshot_id,
                       provider_call_id=provider_call_id, status=status, observed_at=observed_at,
                       transfer_status=transfer_status, raw_status=raw_status)
        with self._transaction():
            record = self.get(request_id)
            req = CallRequest(**record['request'])
            association(req, CallResult(**{k: v for k, v in payload.items() if k not in ('raw_status', 'transfer_status')},
                                        source='provider lifecycle'), record['provider_call_id'])
            saved = self.conn.execute('SELECT payload FROM voice_events WHERE event_id=?', (event_id,)).fetchone()
            if saved:
                if saved[0] != encoded(payload):
                    raise ValueError('event identity reused with different content')
                return 'duplicate'
            self.conn.execute('INSERT INTO voice_events VALUES (?, ?, ?)', (event_id, request_id, encoded(payload)))
            reasons = []
            if raw_status is not None:
                reasons.append('unknown_provider_status')
            if transfer_status:
                reasons.append('transfer_failed' if transfer_status == 'failed' else 'transfer_pending')
                if transfer_status == 'failed' or (record['transfer_status'] != 'failed' and
                        (not record['lifecycle_at'] or utc(observed_at) >= utc(record['lifecycle_at']))):
                    self.conn.execute('UPDATE voice_calls SET transfer_status=? WHERE request_id=?', (transfer_status, request_id))
            if status in ('failed', 'no_answer', 'declined'):
                reasons.append(status)
            self._followup(record, reasons, fresh_event=bool(reasons) or (status in TERMINAL and not record['result']))
            previous_at = utc(record['lifecycle_at']) if record['lifecycle_at'] else None
            progression = {'queued': 0, 'ringing': 1, 'in_progress': 2}
            ignore = (record['status'] in TERMINAL or
                      (previous_at is not None and utc(observed_at) < previous_at) or
                      (status not in TERMINAL and progression[status] < progression[record['status']]))
            if record['status'] in TERMINAL and status in TERMINAL and record['status'] != status:
                self._followup(self.get(request_id), ['conflicting_terminal_status'])
            if ignore:
                return 'ignored'
            self.conn.execute('UPDATE voice_calls SET status=?, lifecycle_at=? WHERE request_id=?',
                              (status, observed_at, request_id))
            return 'accepted'

    def record_result(self, result, *, delivery_id=None):
        self._time(result.observed_at)
        with self._transaction():
            record = self.get(result.request_id)
            req = CallRequest(**record['request'])
            association(req, result, record['provider_call_id'])
            if delivery_id is not None:
                identifier(delivery_id, 'delivery_id')
            event_id = 'result:' + (delivery_id or digest(asdict(result)))
            if self.conn.execute('SELECT 1 FROM voice_events WHERE event_id=?', (event_id,)).fetchone():
                return 'duplicate'
            normalized = normalize_result(req, result)
            self.conn.execute('INSERT INTO voice_events VALUES (?, ?, ?)', (event_id, req.request_id, '{}'))
            # Adverse facts survive older callbacks; good later answers never silently erase them.
            self._followup(record, normalized.human_followup_reasons, fresh_event=True)
            previous = record['result']
            if previous and utc(result.observed_at) <= utc(previous['observed_at']):
                if utc(result.observed_at) == utc(previous['observed_at']):
                    self._followup(self.get(req.request_id), ['conflicting_answers'])
                return 'ignored'
            self.conn.execute('UPDATE voice_calls SET result=? WHERE request_id=?',
                              (encoded(asdict(normalized)), req.request_id))
            return 'accepted'

    def assessment(self, request_id):
        record = self.get(request_id)
        req = CallRequest(**record['request'])
        if not record['provider_call_id']:
            raise ValueError('provider call is not bound')
        r = CallResult(**record['result']) if record['result'] else CallResult(
            request_id=req.request_id, asset_id=req.asset_id, snapshot_id=req.snapshot_id,
            provider_call_id=record['provider_call_id'], status=record['status'],
            observed_at=record['lifecycle_at'] or self.epoch.isoformat(), source='provider lifecycle only')
        r = replace(r, status=record['status'],
                    human_followup_required=bool(record['followup_reasons']) or r.human_followup_required,
                    human_followup_reasons=sorted(set(r.human_followup_reasons) | set(record['followup_reasons'])))
        return to_assessment(req, r, provider_call_id=record['provider_call_id'], epoch=self.epoch, now=self.clock())

    def start(self, client, request_id, *, mode, approved_target=None):
        if mode not in ('browser', 'outbound'):
            raise ValueError('invalid dispatch mode')
        # Validate configuration before claiming a dispatch attempt.
        if client.configuration_status()['status'] == 'not_configured':
            raise ValueError('SLNG is not configured')
        with self._transaction():
            record = self.get(request_id)
            req = CallRequest(**record['request'])
            if mode == 'outbound' and (req.input_mode != 'live' or approved_target != req.contact_number
                                       or not client.config.outbound_connection_id):
                raise ValueError('approved live target and outbound connection required')
            if record['dispatch_state'] != 'not_started':
                raise ValueError('dispatch attempt already recorded; reconcile manually')
            self.conn.execute("UPDATE voice_calls SET dispatch_state='attempting' WHERE request_id=?", (request_id,))
        # The durable claim deliberately precedes network I/O. A crash leaves attempting,
        # which also blocks retries. There is no documented provider idempotency key.
        try:
            response = (client.create_web_session(req) if mode == 'browser'
                        else client.dispatch(req, approved_target=approved_target))
            self.bind(request_id, response['call_id'])
            return response
        except Exception:
            with self._transaction():
                self.conn.execute("UPDATE voice_calls SET dispatch_state='outcome_unknown' WHERE request_id=?", (request_id,))
                self._followup(self.get(request_id), ['dispatch_outcome_unknown'])
            raise

    def poll(self, client, request_id):
        record = self.get(request_id)
        if not record['provider_call_id']:
            raise ValueError('provider call is not bound')
        body = client.get_call(record['provider_call_id'])
        if any(body.get('arguments', {}).get(k) != record['request'][k]
               for k in ('request_id', 'asset_id', 'snapshot_id')):
            raise ValueError('provider request association mismatch')
        raw_status = body.get('status')
        known = isinstance(raw_status, str) and raw_status in STATUSES
        transfer = None
        for execution in body.get('tool_executions', []):
            if execution.get('tool_kind') == 'transfer_call':
                if execution.get('outcome') in ('failed', 'timed_out', 'cancelled', 'delivery_unknown'):
                    transfer = 'failed'
                    break
                transfer = 'requested'  # even succeeded is only tool execution, not connection proof
        payload = dict(request_id=request_id, asset_id=record['request']['asset_id'],
            snapshot_id=record['request']['snapshot_id'], provider_call_id=record['provider_call_id'],
            status=raw_status if known else record['status'], observed_at=body['updated_at'],
            transfer_status=transfer, raw_status=None if known else 'unrecognized')
        return self.record_lifecycle(event_id='poll:' + digest(payload), **payload)

    def record_plan(self, plan, *, snapshot_id):
        """Keep uncontacted households and departure/arrival tasks in the same durable queue."""
        with self._transaction():
            for row in plan['locations']:
                kinds = set(row['tasks'])
                if row['mode'] in ('self_evacuate', 'assisted_evacuation'):
                    kinds |= {'confirm_departure', 'confirm_arrival'}
                for kind in kinds:
                    identifier(kind, 'task kind')
                    self._task('plan:' + snapshot_id + ':' + row['asset_id'], row['asset_id'], snapshot_id, kind)
