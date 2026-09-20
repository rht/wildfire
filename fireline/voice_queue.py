"""Durable, paced outbound queue consuming the existing contact-priority algorithm.

All workers for a provider account must use one database and the same limits.
Network requests happen after the SQLite admission/dispatch claim commits.
"""
from dataclasses import dataclass
import math
import os

from .contact_priority import rank_contacts
from .env import load_env
from .voice_models import TERMINAL


@dataclass(frozen=True)
class CallQueueConfig:
    max_concurrent_calls: int = 5
    max_call_starts_per_second: float = 1

    def __post_init__(self):
        if (isinstance(self.max_concurrent_calls, bool)
                or not isinstance(self.max_concurrent_calls, int)
                or self.max_concurrent_calls < 1):
            raise ValueError('MAX_CONCURRENT_CALLS must be a positive integer')
        rate = self.max_call_starts_per_second
        if (isinstance(rate, bool) or not isinstance(rate, (int, float))
                or not math.isfinite(rate) or rate <= 0):
            raise ValueError('MAX_CALL_STARTS_PER_SECOND must be finite and positive')

    @classmethod
    def from_env(cls):
        load_env()
        try:
            return cls(int(os.getenv('MAX_CONCURRENT_CALLS', '5')),
                       float(os.getenv('MAX_CALL_STARTS_PER_SECOND', '1')))
        except (ValueError, OverflowError):
            raise ValueError('invalid call queue limits') from None


SCHEMA = '''
CREATE TABLE IF NOT EXISTS voice_queue (
 request_id TEXT PRIMARY KEY REFERENCES voice_calls(request_id),
 asset_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 latest_start REAL, arrival REAL, distance REAL,
 state TEXT NOT NULL, approved_target TEXT NOT NULL,
 UNIQUE(asset_id, snapshot_id));
CREATE INDEX IF NOT EXISTS voice_queue_priority
 ON voice_queue(state, latest_start, arrival, distance, asset_id);
'''


class AdmissionDeferred(Exception):
    """Another worker, the rate gate or occupied capacity deferred this candidate."""


class VoiceCallQueue:
    def __init__(self, store, config=None):
        self.store = store
        self.config = config or CallQueueConfig.from_env()
        store.conn.executescript(SCHEMA)

    def enqueue(self, locations, requests, *, approved_targets, policy=None, allow_synthetic=False):
        """Persist explicitly approved requests. Enqueuing never calls a provider.

        One request per asset/snapshot. Replays preserve started/cancelled state.
        Missing timing remains in review; absent contact records are reported.
        Live-only by default. allow_synthetic=True enables offline tests in a
        separate database; those requests can never pass outbound dispatch.
        """
        locations = tuple(locations)
        ranked = rank_contacts(locations, policy)
        requests = list(requests)
        by_asset = {r.asset_id: r for r in requests}
        if (len(by_asset) != len(requests)
                or len({r.request_id for r in requests}) != len(requests)
                or len({r.snapshot_id for r in requests}) > 1
                or set(by_asset) - {loc.asset_id for loc in locations}):
            raise ValueError('requests must uniquely match locations in one snapshot')
        for req in requests:
            if (req.input_mode != 'live' and not (allow_synthetic is True and req.input_mode == 'synthetic')
                    or approved_targets.get(req.request_id) != req.contact_number):
                raise ValueError('each outbound request needs its approved live target')
        report = dict(queued=[], review=[], missing_contact=[], existing={})
        with self.store._transaction():
            for row in ranked['ranked'] + ranked['review']:
                req = by_asset.get(row['asset_id'])
                if req is None:
                    report['missing_contact'].append(row['asset_id'])
                    continue
                self.store.register(req)
                review = row['rank'] is None
                self.store.conn.execute('''INSERT INTO voice_queue VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(request_id) DO NOTHING''',
                    (req.request_id, req.asset_id, req.snapshot_id, row['latest_start_min'],
                     row['components']['fire_arrival_min'], row['components']['distance_m'],
                     'review' if review else 'pending', req.contact_number))
                state = self.store.conn.execute(
                    'SELECT state FROM voice_queue WHERE request_id=?', (req.request_id,)).fetchone()[0]
                if self.store.get(req.request_id)['dispatch_state'] != 'not_started':
                    state = 'started'
                if state in ('pending', 'review'):
                    report['review' if state == 'review' else 'queued'].append(req.request_id)
                else:
                    report['existing'][req.request_id] = state
        return report

    def _active(self):
        # Includes calls started outside this queue, pending creation and unknown outcomes.
        rows = self.store.conn.execute("SELECT request_id FROM voice_calls WHERE dispatch_state != 'not_started'")
        return [rec for row in rows if (rec := self.store.get(row[0]))['status'] not in TERMINAL]

    def _pending(self):
        return self.store.conn.execute('''SELECT q.* FROM voice_queue q
            JOIN voice_calls c USING(request_id)
            WHERE q.state='pending' AND c.dispatch_state='not_started' AND c.status='queued'
            ORDER BY q.latest_start, q.arrival, q.distance IS NULL, q.distance,
                     q.asset_id, q.request_id''').fetchall()

    def _candidate(self):
        active = self._active()
        if len(active) >= self.config.max_concurrent_calls:
            return None
        busy_phones = {r['request']['contact_number'] for r in active}
        busy_assets = {r['request']['asset_id'] for r in active}
        return next((r for r in self._pending() if r['approved_target'] not in busy_phones
                     and r['asset_id'] not in busy_assets), None)

    def _admit(self, request_id):
        candidate = self._candidate()
        now = self.store.clock().timestamp()
        busy = self.store.conn.execute(
            "SELECT value FROM voice_meta WHERE key='queue_dispatching'").fetchone()
        if busy:
            raise AdmissionDeferred()
        previous = self.store.conn.execute(
            "SELECT value FROM voice_meta WHERE key='queue_next_start'").fetchone()
        if (candidate is None or candidate['request_id'] != request_id
                or (previous and now < float(previous[0]))):
            raise AdmissionDeferred()
        # Round the interval up to microseconds: no burst at a rolling-window boundary.
        self.store.conn.execute("INSERT OR REPLACE INTO voice_meta VALUES ('queue_next_start', ?)",
                                (str(now + self._interval()),))
        self.store.conn.execute("INSERT INTO voice_meta VALUES ('queue_dispatching', ?)", (request_id,))
        self.store.conn.execute("UPDATE voice_queue SET state='started' WHERE request_id=?", (request_id,))

    def _interval(self):
        return math.ceil(1_000_000 / self.config.max_call_starts_per_second) / 1_000_000

    def _release_dispatch(self):
        # Serialize HTTP creation, NOT conversations. Wait a full interval after the
        # previous response to prevent slow preflights/requests bunching actual POSTs.
        # A crashed worker stays held until its call association/outcome is reconciled.
        with self.store._transaction():
            row = self.store.conn.execute(
                "SELECT value FROM voice_meta WHERE key='queue_dispatching'").fetchone()
            if row and self.store.get(row[0])['dispatch_state'] != 'attempting':
                self.store.conn.execute("DELETE FROM voice_meta WHERE key='queue_dispatching'")
                previous = self.store.conn.execute(
                    "SELECT value FROM voice_meta WHERE key='queue_next_start'").fetchone()
                next_start = max(float(previous[0]) if previous else 0,
                                 self.store.clock().timestamp() + self._interval())
                self.store.conn.execute("INSERT OR REPLACE INTO voice_meta VALUES ('queue_next_start', ?)",
                                        (str(next_start),))

    def dispatch_next(self, client):
        """Start the highest-priority eligible call, or return None when gated.

        HTTP dispatch returns while the actual call is still ringing/conversing.
        Provider errors propagate after VoiceStore durably holds the uncertain slot.
        """
        # Selection is optimistic. Admission rechecks it under start's write lock.
        self._release_dispatch()
        candidate = self._candidate()
        if candidate is None:
            return None
        request_id = candidate['request_id']
        if self.store.get(request_id)['request']['input_mode'] != 'live':
            raise ValueError('synthetic queue entries cannot dispatch to a provider')
        try:
            result = self.store.start(client, request_id, mode='outbound',
                approved_target=candidate['approved_target'], admission=self._admit)
        except AdmissionDeferred:
            return None
        finally:
            self._release_dispatch()
        return dict(request_id=request_id, provider_call_id=result['call_id'])

    def cancel(self, request_id):
        """Withdraw an unstarted request; this never hangs up an active call."""
        with self.store._transaction():
            if self.store.get(request_id)['dispatch_state'] != 'not_started':
                raise ValueError('cannot cancel an attempted call')
            changed = self.store.conn.execute(
                "UPDATE voice_queue SET state='cancelled' WHERE request_id=? AND state IN ('pending', 'review')",
                (request_id,)).rowcount
            if not changed:
                raise ValueError('request is not pending in this queue')

    def sync_active(self, client):
        """Refresh provider lifecycle and completed answers; a failed GET holds its slot."""
        from .slng_voice import ProviderError

        errors = []
        for record in self._active():
            if record['provider_call_id']:
                try:
                    self.store.sync(client, record['request_id'])
                except (ProviderError, ValueError, KeyError, TypeError):
                    errors.append(record['request_id'])
        return errors

    def status(self):
        active = self._active()
        return dict(active=len(active), pending=[r['request_id'] for r in self._pending()],
            unknown=[r['request_id'] for r in active
                     if r['dispatch_state'] in ('attempting', 'outcome_unknown')],
            review=[r[0] for r in self.store.conn.execute(
                "SELECT request_id FROM voice_queue WHERE state='review' ORDER BY request_id")],
            max_concurrent_calls=self.config.max_concurrent_calls,
            max_call_starts_per_second=self.config.max_call_starts_per_second)
