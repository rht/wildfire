"""Offline, resumable O/B/C/A scenario replay using the real voice/task stores.

A dedicated database per case isolates alternative worlds. No provider or network calls.
The durable revision describes a processed fixture event, not a live call or a dispatch.
"""
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .evacuation_readiness import coordinate_evacuation, ReceptionCentre, EvacuationRoute
from .priority_examples import load_scenario
from .voice_models import CallRequest, CallResult, utc
from .voice_store import VoiceStore, encoded

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/voice/replay_scenarios.json'


def load_cases():
    return json.loads(FIXTURE.read_text())['cases']


class MockReplay:
    def __init__(self, path, case_name):
        fixture = json.loads(FIXTURE.read_text())
        matches = [c for c in fixture['cases'] if c['name'] == case_name]
        if not matches:
            raise ValueError('unknown mock case')
        self.case = matches[0]
        self.epoch = utc(fixture['epoch'])
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_id = 'mock-voice-' + case_name
        self.scenario = load_scenario(ROOT / 'fixtures/static_priority.json')
        self.readiness = json.loads((ROOT / 'fixtures/evacuation_readiness.json').read_text())
        fingerprint = hashlib.sha256(encoded([fixture, self.readiness,
            json.loads((ROOT / 'fixtures/static_priority.json').read_text())]).encode()).hexdigest()
        with self._lock():
            if not self.path.exists():
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            conn = sqlite3.connect(self.path)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and 'mock_replay' not in tables:
                    raise ValueError('use a dedicated mock case database')
                conn.execute('CREATE TABLE IF NOT EXISTS mock_replay (id INTEGER PRIMARY KEY CHECK(id=1), case_name TEXT, fingerprint TEXT, revision INTEGER)')
                row = conn.execute('SELECT case_name, fingerprint FROM mock_replay WHERE id=1').fetchone()
                if row and row != (case_name, fingerprint):
                    raise ValueError('mock case or fixture changed; use a new database')
                conn.execute('INSERT OR IGNORE INTO mock_replay VALUES (1, ?, ?, 0)', (case_name, fingerprint))
                conn.execute('CREATE TABLE IF NOT EXISTS mock_updates (revision INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
                conn.commit()
            finally:
                conn.close()
            self.store = VoiceStore(self.path, epoch=self.epoch, clock=lambda: self.epoch)
            self.store.record_plan(self._project(self._revision())['plan'], snapshot_id=self.snapshot_id)

    @contextmanager
    def _lock(self):
        # Serialize local UI/CLI writers and readers, including separate processes.
        fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _revision(self):
        return self.store.conn.execute('SELECT revision FROM mock_replay WHERE id=1').fetchone()[0]

    def _request_id(self, index):
        return f'{self.snapshot_id}-{index}'

    def _project(self, revision):
        scenario = self.scenario
        assessments, calls = {}, []
        review_required = False
        for index, event in enumerate(self.case['events'][:revision]):
            if event['kind'] == 'call_result':
                rid = self._request_id(index)
                record = self.store.get(rid)
                result = record['result']
                aid = record['request']['asset_id']
                assessments[aid] = self.store.assessment(rid)
                # Keep reported help needs visible even when uncertainty blocks a decision.
                ability, transport = result['can_self_evacuate'], result['transport_available']
                assistance = (True if ability is False or transport is False else
                              False if ability is True and transport is True else None)
                calls.append(dict(asset_id=aid, request_id=rid, status=record['status'],
                    can_self_evacuate=ability, transport_available=transport,
                    reported_needs_assistance=assistance, wants_human=result['wants_human'],
                    acknowledged=result['acknowledged'], confidence=result['confidence'],
                    confidence_basis=result['confidence_basis'], evidence=result['evidence'],
                    human_followup_reasons=record['followup_reasons']))
                review_required |= assistance is True or bool(record['followup_reasons'])
            elif event['kind'] == 'forecast_update':
                scenario = replace(scenario, locations=tuple(replace(a,
                    fire_arrival_min=event['fire_arrival_min'],
                    deadline_min=min(a.deadline_min, event['fire_arrival_min']),
                    forecast_source='Synthetic changed forecast')
                    if a.asset_id == event['asset_id'] else a for a in scenario.locations))
                review_required = True
            elif event['kind'] == 'resource_update':
                scenario = replace(scenario, capabilities=tuple(event['capabilities']))
                review_required = True
            else:
                raise ValueError('unknown mock event')
        centres = [ReceptionCentre(**c) for c in self.readiness['centres']]
        routes = [EvacuationRoute(**r) for r in self.readiness['routes']]
        if 'centre_capacity' in self.case:
            centres = [replace(c, remaining_places=self.case['centre_capacity'])
                       if c.centre_id == 'community_centre' else c for c in centres]
        if 'route_confirmed' in self.case:
            routes = [replace(r, confirmed=self.case['route_confirmed']) for r in routes]
        plan = coordinate_evacuation(scenario, list(assessments.values()), centres, routes)
        if plan['response'] is not None:
            plan['response']['sequence'] = [s['action_id'] for s in plan['response']['steps']]
        return dict(schema_version='mock-coordination-state-1', case_id=self.case['name'],
            scenario_id=scenario.scenario_id, snapshot_id=self.snapshot_id, revision=revision,
            as_of=self.epoch.isoformat(), input_mode='synthetic', dispatch=False, live_validation=False,
            pending_events=len(self.case['events'])-revision, calls=calls, plan=plan,
            response_review_required=bool(review_required),
            response_basis='Static one-crew proposal from supplied counts, deadlines and effects; interview answers do not supply revised counts or task durations. No action is dispatched.',
            tasks=self.store.tasks.tasks())

    def state(self):
        with self._lock():
            return self._project(self._revision())

    def apply_event(self, index):
        with self._lock(), self.store._transaction():
            revision = self._revision()
            if index < revision:
                return self._project(revision)
            if index != revision or index >= len(self.case['events']):
                raise ValueError('mock event out of order')
            before = self._project(revision)
            event = self.case['events'][index]
            if event['kind'] == 'call_result':
                i = event['interview']
                rid = self._request_id(index)
                req = CallRequest(rid, i['asset_id'], self.snapshot_id, '+12025550123', 'en',
                                  'SIMULATION: synthetic O/B/C/A readiness exercise.', input_mode='synthetic')
                self.store.register(req)
                self.store.bind(rid, 'call-' + rid)
                self.store.record_lifecycle(event_id='lifecycle-' + rid, request_id=rid,
                    asset_id=req.asset_id, snapshot_id=req.snapshot_id, provider_call_id='call-' + rid,
                    status=i['status'], observed_at=self.epoch.isoformat())
                self.store.record_result(CallResult(request_id=rid, asset_id=req.asset_id,
                    snapshot_id=req.snapshot_id, provider_call_id='call-' + rid,
                    status=i['status'], observed_at=self.epoch.isoformat(),
                    source='synthetic structured interview', **i['answers']))
            state = self._project(revision + 1)
            self.store.record_plan(state['plan'], snapshot_id=self.snapshot_id)
            def affected_rows(projection):
                contacts = projection['plan']['contacts']
                timing = {r['asset_id']: r for r in contacts['ranked'] + contacts['review']}
                return {r['asset_id']: [r, timing[r['asset_id']]] for r in projection['plan']['locations']}
            previous_rows, next_rows = affected_rows(before), affected_rows(state)
            changed = sorted(aid for aid in next_rows if next_rows[aid] != previous_rows[aid])
            if before['plan']['response'] != state['plan']['response']:
                changed = sorted(next_rows)
            update = dict(schema_version='mock-coordination-update-1', case_id=self.case['name'],
                revision=revision+1, event_id=f'{self.snapshot_id}:{index}', kind=event['kind'],
                changed_asset_ids=changed, refresh='full_state', input_mode='synthetic')
            with self.store._transaction():
                self.store.conn.execute('UPDATE mock_replay SET revision=? WHERE id=1', (revision+1,))
                self.store.conn.execute('INSERT INTO mock_updates VALUES (?, ?)', (revision+1, encoded(update)))
            return self._project(revision + 1)

    def advance(self):
        # A competing writer may have already applied this index; apply_event is idempotent.
        return self.apply_event(self._revision())

    def updates(self, after_revision=0):
        with self._lock():
            return [json.loads(r[0]) for r in self.store.conn.execute(
                'SELECT payload FROM mock_updates WHERE revision>? ORDER BY revision', (after_revision,))]

    def close(self):
        self.store.close()
