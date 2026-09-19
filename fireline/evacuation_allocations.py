"""Transactional, scenario-scoped allocations. All writers must share this database.

All timestamps are caller-supplied UTC, allowing reproducible offline scenarios.
Callers must pass current scenario time on reads and mutations. Actor/evidence are
trusted analyst inputs, not an authentication mechanism. Private data stays local.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .evacuation_plans import (
    AnalystApproval, EvacuationGroup, EvacuationRoute, PlanningContext, RoadEvidence,
    approval_matches, candidate_from_record, evaluate_candidates, text, utc, validate_inputs,
)
from .priority_models import number, string_sequence


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class AssistancePlan:
    transport_id: str
    reception_id: str
    pickup_min: float
    seats: int
    capabilities: tuple[str, ...]


def _decode_inputs(payload):
    data = json.loads(payload)
    return (PlanningContext(**data['context']),
            [candidate_from_record(row) for row in data['candidates']],
            [EvacuationGroup(**row) for row in data['groups']],
            [EvacuationRoute(**row) for row in data['routes']],
            [RoadEvidence(**row) for row in data['roads']])


class AllocationStore:
    def __init__(self, path):
        self.path = Path(path)
        with self._transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS inputs (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (snapshot_id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS allocations (
                    allocation_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, active INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS active_group ON allocations(group_id) WHERE active=1;
                CREATE TABLE IF NOT EXISTS commands (command_id TEXT PRIMARY KEY, body TEXT NOT NULL, allocation_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (revision INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
            ''')

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _inputs(db, as_of=None):
        row = db.execute('SELECT body FROM inputs WHERE id=1').fetchone()
        if row is None:
            raise ValueError('supply inputs before allocating')
        context, *rest = _decode_inputs(row[0])
        if as_of is not None:
            if utc(as_of) < utc(context.as_of):
                raise ValueError('cannot rewind scenario time')
            context = replace(context, as_of=as_of)
        return context, *rest

    @staticmethod
    def _active(db):
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM allocations WHERE active=1 ORDER BY allocation_id')]

    @staticmethod
    def _allocation(db, allocation_id):
        row = db.execute('SELECT body FROM allocations WHERE allocation_id=?', (allocation_id,)).fetchone()
        if row is None:
            raise ValueError('unknown allocation')
        return json.loads(row[0])

    @staticmethod
    def _save(db, allocation):
        db.execute('INSERT OR REPLACE INTO allocations VALUES (?,?,?,?)',
                   (allocation['allocation_id'], allocation['group_id'], int(allocation['state'] != 'released'), _json(allocation)))

    @staticmethod
    def _event(db, kind, as_of, allocation=None, **private):
        db.execute('INSERT INTO events(body) VALUES (?)', (_json({
            'kind': kind, 'as_of': as_of, 'allocation_id': allocation['allocation_id'] if allocation else None,
            'asset_id': allocation['asset_id'] if allocation else None, **private}),))

    @staticmethod
    def _occupied(allocations, exclude=None):
        result = {}
        for row in allocations:
            if row['allocation_id'] != exclude:
                cid = row['destination_id']
                result[cid] = result.get(cid, 0) + row['people']
        return result

    def _safety(self, allocation, inputs, active):
        context, candidates, groups, routes, roads = inputs
        group = next((g for g in groups if g.group_id == allocation['group_id']), None)
        reasons = list(allocation['invalidation_reasons'])
        if allocation['incident_id'] != context.incident_id:
            reasons.append('incident_changed')
        if group is None:
            reasons.append('group_missing')
        elif any(_json(getattr(group, key)) != _json(allocation['group'][key])
                 for key in ('asset_id', 'people', 'needs', 'assisted')):
            reasons.append('group_changed')
        if group is not None:
            rows = evaluate_candidates(group, candidates, routes, roads, context,
                                       self._occupied(active, allocation['allocation_id']))
            match = next((r for r in rows if r['centre_id'] == allocation['destination_id']), None)
            reasons.extend(match['reasons'] if match else ['destination_missing'])
        return sorted(set(reasons))

    def update_inputs(self, context, candidates, groups, routes, roads):
        """Persist a complete snapshot. Replays are idempotent; snapshots are immutable."""
        validate_inputs(context, candidates, groups, routes, roads)
        body = _json({'context': asdict(context), 'candidates': [asdict(c) for c in candidates],
                      'groups': [asdict(g) for g in groups], 'routes': [asdict(r) for r in routes],
                      'roads': [asdict(r) for r in roads]})
        with self._transaction() as db:
            seen = db.execute('SELECT body FROM snapshots WHERE snapshot_id=?', (context.snapshot_id,)).fetchone()
            if seen:
                if seen[0] != body:
                    raise ValueError('snapshot ID already has different inputs')
                return
            old = db.execute('SELECT body FROM inputs WHERE id=1').fetchone()
            if old:
                previous = _decode_inputs(old[0])[0]
                if context.scenario_id != previous.scenario_id or utc(context.epoch) != utc(previous.epoch):
                    raise ValueError('database belongs to another scenario or epoch')
                if utc(context.as_of) < utc(previous.as_of):
                    raise ValueError('cannot rewind scenario time')
            latest = db.execute('SELECT body FROM events ORDER BY revision DESC LIMIT 1').fetchone()
            if latest and utc(context.as_of) < utc(json.loads(latest[0])['as_of']):
                raise ValueError('cannot rewind past recorded events')
            db.execute('INSERT INTO snapshots VALUES (?,?)', (context.snapshot_id, body))
            db.execute('INSERT OR REPLACE INTO inputs VALUES (1,?)', (body,))
            self._event(db, 'inputs_updated', context.as_of)
            active = self._active(db)
            for allocation in active:
                reasons = self._safety(allocation, (context, candidates, groups, routes, roads), active)
                if reasons != allocation['invalidation_reasons']:
                    allocation['invalidation_reasons'] = reasons
                    self._save(db, allocation)
                    self._event(db, 'allocation_invalidated', context.as_of, allocation)

    def _command(self, db, command_id, payload):
        text(command_id, 'command_id')
        prior = db.execute('SELECT body, allocation_id FROM commands WHERE command_id=?', (command_id,)).fetchone()
        if prior:
            if prior[0] != _json(payload):
                raise ValueError('command ID reused with different arguments')
            return self._allocation(db, prior[1])
        return None

    def _finish(self, db, command_id, payload, allocation, kind, as_of):
        self._save(db, allocation)
        db.execute('INSERT INTO commands VALUES (?,?,?)', (command_id, _json(payload), allocation['allocation_id']))
        self._event(db, kind, as_of, allocation, command_id=command_id, evidence=payload)
        return self._public(allocation, self._inputs(db, as_of), self._active(db))

    def _new_allocation(self, db, group_id, centre_id, approval, as_of, snapshot_id, previous=None):
        context, candidates, groups, routes, roads = self._inputs(db, as_of)
        if context.snapshot_id != snapshot_id:
            raise ValueError('snapshot changed; review current inputs')
        if not approval_matches(approval, context, centre_id, group_id):
            raise ValueError('scoped analyst allocation approval required')
        group = next((g for g in groups if g.group_id == group_id), None)
        if group is None:
            raise ValueError('unknown group')
        active = self._active(db)
        if any(row['group_id'] == group_id for row in active):
            raise ValueError('group already has an active allocation; explicitly reassign')
        rows = evaluate_candidates(group, candidates, routes, roads, context, self._occupied(active))
        chosen = next((r for r in rows if r['centre_id'] == centre_id), None)
        if chosen is None or chosen['status'] != 'eligible':
            raise ValueError('destination not eligible: ' + ', '.join(chosen['reasons'] if chosen else ['unknown destination']))
        return {'allocation_id': str(uuid4()), 'group_id': group_id, 'asset_id': group.asset_id,
                'destination_id': centre_id, 'people': group.people, 'group': asdict(group),
                'incident_id': context.incident_id, 'snapshot_id': snapshot_id, 'approval': asdict(approval),
                'evidence': chosen['evidence'], 'state': 'reserved', 'invalidation_reasons': [],
                'created_at': as_of, 'last_at': as_of, 'previous_allocation_id': previous,
                'assistance': None, 'transport_confirmed': False, 'reception_confirmed': False}

    def reserve(self, command_id, group_id, centre_id, approval, *, as_of, snapshot_id):
        payload = dict(kind='reserve', group_id=group_id, centre_id=centre_id, approval=asdict(approval),
                       as_of=as_of, snapshot_id=snapshot_id)
        with self._transaction() as db:
            prior = self._command(db, command_id, payload)
            if prior:
                return self._public(prior, self._inputs(db, as_of), self._active(db))
            allocation = self._new_allocation(db, group_id, centre_id, approval, as_of, snapshot_id)
            return self._finish(db, command_id, payload, allocation, 'reserved', as_of)

    def reassign(self, command_id, allocation_id, centre_id, approval, *, as_of, snapshot_id):
        """Explicit release-and-reserve; failure rolls back both. Never automatic."""
        payload = dict(kind='reassign', allocation_id=allocation_id, centre_id=centre_id,
                       approval=asdict(approval), as_of=as_of, snapshot_id=snapshot_id)
        with self._transaction() as db:
            prior = self._command(db, command_id, payload)
            if prior:
                return self._public(prior, self._inputs(db, as_of), self._active(db))
            old = self._allocation(db, allocation_id)
            self._check_active_time(old, as_of)
            # Confirmed physical movement requires explicit reconciliation, not a new
            # reservation that would silently forget the group's actual location.
            if old['state'] in ('departed', 'arrived'):
                raise ValueError('release with physical-location evidence before reallocating')
            old['state'] = 'released'
            old['last_at'] = as_of
            self._save(db, old)
            new = self._new_allocation(db, old['group_id'], centre_id, approval, as_of, snapshot_id, allocation_id)
            self._event(db, 'released_for_reassignment', as_of, old)
            return self._finish(db, command_id, payload, new, 'reassigned', as_of)

    @staticmethod
    def _check_active_time(allocation, as_of):
        if allocation['state'] == 'released':
            raise ValueError('allocation is released')
        if utc(as_of) < utc(allocation['last_at']):
            raise ValueError('cannot rewind confirmation time')

    def _mutate(self, command_id, allocation_id, kind, actor, evidence, as_of, change, extra=None):
        text(actor, 'actor')
        text(evidence, 'evidence')
        payload = dict(kind=kind, allocation_id=allocation_id, actor=actor, evidence=evidence, as_of=as_of, extra=extra)
        with self._transaction() as db:
            prior = self._command(db, command_id, payload)
            if prior:
                return self._public(prior, self._inputs(db, as_of), self._active(db))
            allocation = self._allocation(db, allocation_id)
            self._check_active_time(allocation, as_of)
            current = self._inputs(db, as_of)
            change(allocation, current, self._active(db))
            allocation['last_at'] = as_of
            return self._finish(db, command_id, payload, allocation, kind, as_of)

    def release(self, command_id, allocation_id, *, actor, evidence, as_of):
        """Explicitly free slots, including arrived occupants; evidence is required."""
        def change(allocation, current, active):
            allocation['state'] = 'released'
        return self._mutate(command_id, allocation_id, 'released', actor, evidence, as_of, change)

    def set_assistance(self, command_id, allocation_id, plan, *, actor, evidence, as_of):
        text(plan.transport_id, 'transport_id')
        text(plan.reception_id, 'reception_id')
        number(plan.pickup_min, 'pickup_min')
        if not isinstance(plan.seats, int) or isinstance(plan.seats, bool) or plan.seats <= 0:
            raise ValueError('seats must be a positive integer')
        string_sequence(plan.capabilities, 'capabilities')
        def change(allocation, current, active):
            if allocation['state'] != 'reserved':
                raise ValueError('assistance changes require reapproval before communication')
            group = allocation['group']
            if plan.seats < allocation['people'] or not set(group['needs'] or ()) <= set(plan.capabilities):
                raise ValueError('assistance does not meet group needs or size')
            if plan.pickup_min < current[0].now_min:
                raise ValueError('pickup is in the past')
            allocation['assistance'] = asdict(plan)
            allocation['transport_confirmed'] = False
            allocation['reception_confirmed'] = False
        return self._mutate(command_id, allocation_id, 'assistance_planned', actor, evidence, as_of, change, asdict(plan))

    def _assistance_ready(self, allocation, inputs, active):
        context, candidates, groups, routes, roads = inputs
        if not allocation['group']['assisted']:
            return True
        plan = allocation['assistance']
        if not plan:
            return False
        group = next((g for g in groups if g.group_id == allocation['group_id']), None)
        if group is None:
            return False
        # Evaluate the whole supplied journey at pickup, not merely at reservation.
        delay = max(0, plan['pickup_min'] - context.now_min)
        delayed = replace(group, evacuation_min=(group.evacuation_min or 0) + delay)
        delayed_routes = [replace(r, evacuation_min=r.evacuation_min + delay) for r in routes]
        choices = evaluate_candidates(delayed, candidates, delayed_routes, roads, context,
                                      self._occupied(active, allocation['allocation_id']))
        choice = next((r for r in choices if r['centre_id'] == allocation['destination_id']), None)
        return bool(choice and choice['status'] == 'eligible' and allocation['transport_confirmed'] and allocation['reception_confirmed']
                    and (allocation['state'] in ('departed', 'arrived') or plan['pickup_min'] >= context.now_min)
                    and plan['pickup_min'] + allocation['group']['evacuation_min'] + context.buffer_min < allocation['group']['fire_arrival_min'])

    def confirm(self, command_id, allocation_id, state, *, actor, evidence, as_of):
        def change(allocation, current, active):
            if state in ('transport_confirmed', 'reception_confirmed'):
                if allocation['assistance'] is None:
                    raise ValueError('assistance plan required')
                allocation[state] = True
                return
            transitions = {'communicated': 'reserved', 'departed': 'communicated', 'arrived': 'departed'}
            if state not in transitions or allocation['state'] != transitions[state]:
                raise ValueError('invalid lifecycle transition')
            # Record physical facts even after new danger; never hide a reported arrival.
            if state == 'communicated':
                if self._safety(allocation, current, active):
                    raise ValueError('safety review required before instructions')
                if not self._assistance_ready(allocation, current, active):
                    raise ValueError('assistance transport/reception confirmations required')
            allocation['state'] = state
        return self._mutate(command_id, allocation_id, state, actor, evidence, as_of, change)

    def _public(self, allocation, inputs, active):
        context = inputs[0]
        reasons = self._safety(allocation, inputs, active) if allocation['state'] != 'released' else []
        assistance_ready = self._assistance_ready(allocation, inputs, active)
        tasks = []
        if allocation['state'] != 'released':
            if reasons:
                tasks.extend(['human_review', 'issue_new_instructions'])
            if allocation['group']['assisted'] and not assistance_ready:
                tasks.extend(['arrange_transport', 'arrange_reception'])
            if allocation['state'] == 'reserved':
                tasks.append('issue_new_instructions' if allocation['previous_allocation_id'] else 'confirm_instructions')
            elif allocation['state'] == 'communicated':
                tasks.append('confirm_departure')
            elif allocation['state'] == 'departed':
                tasks.append('confirm_arrival')
        return {key: allocation[key] for key in (
            'allocation_id', 'group_id', 'asset_id', 'destination_id', 'people', 'state',
            'previous_allocation_id', 'snapshot_id', 'incident_id', 'created_at', 'last_at') } | {
                'safety': 'invalidated' if allocation['invalidation_reasons'] else 'review' if reasons else 'valid',
                'reasons': reasons, 'tasks': sorted(set(tasks)),
                'instruction_allowed': allocation['state'] in ('reserved', 'communicated') and not reasons and assistance_ready,
                'assistance': None if allocation['assistance'] is None else {
                    'transport_confirmed': allocation['transport_confirmed'],
                    'reception_confirmed': allocation['reception_confirmed'],
                    'pickup_min': allocation['assistance']['pickup_min']}}

    def view(self, *, as_of):
        """Consistent (public plan, private input records) for trusted adapters."""
        with self._transaction() as db:
            current = self._inputs(db, as_of)
            context, candidates, *_ = current
            active = self._active(db)
            rows = [self._public(row, current, active) for row in active]
            occupied = self._occupied(active)
            capacity = {c.centre.centre_id: None if c.centre.remaining_places is None else
                        c.centre.remaining_places - occupied.get(c.centre.centre_id, 0) for c in candidates}
            events = []
            for revision, body in db.execute('SELECT revision, body FROM events ORDER BY revision'):
                event = json.loads(body)
                events.append({k: event[k] for k in ('kind', 'as_of', 'allocation_id', 'asset_id')} | {'revision': revision})
            plan = {'schema_version': 'evacuation-plan-1', 'scenario_id': context.scenario_id,
                    'snapshot_id': context.snapshot_id, 'revision': events[-1]['revision'] if events else 0,
                    'as_of': as_of, 'locations': rows, 'remaining_capacity': capacity, 'response': None,
                    'tasks': [{'asset_id': r['asset_id'], 'group_id': r['group_id'], 'allocation_id': r['allocation_id'],
                               'kind': task} for r in rows for task in r['tasks']], 'events': events}
            return plan, current

    def public_plan(self, *, as_of):
        """Allowlisted export for live coordination; no private free-text payloads."""
        return self.view(as_of=as_of)[0]

    def briefing(self, asset_id, *, as_of):
        """Public allocation facts for one asset; instruction_allowed must be honored."""
        return [row for row in self.public_plan(as_of=as_of)['locations'] if row['asset_id'] == asset_id]
