"""Explicit, offline coordination ticks over the durable voice/task database.

One store/connection per worker; no UI or provider I/O. Revisions and suggested
work publish in the same SQLite transaction. Public state is a persisted view,
not a claim that a call connected a human or that anyone evacuated.
"""
from dataclasses import replace
from datetime import datetime, timezone
import json
import re

from .contact_priority import ContactPolicy
from .evacuation_readiness import coordinate_evacuation
from .snapshot import validate_snapshot
from .voice_models import ANSWER_FIELDS, utc
from .voice_store import VoiceStore, digest, encoded

SCHEMA = '''
CREATE TABLE IF NOT EXISTS coordination_snapshots (
 snapshot_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE,
 fingerprint TEXT NOT NULL, as_of TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS coordination_revisions (
 revision INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL,
 payload TEXT NOT NULL, event TEXT NOT NULL);
'''

# Private free-text call/task payloads are never passed to this sanitizer.
# This additionally protects optional snapshot metadata and provenance strings.
PRIVATE_KEYS = frozenset({
    'contact_number', 'human_callback_number', 'phone', 'phone_number', 'telephone',
    'email', 'api_key', 'authorization', 'token', 'access_token', 'secret',
    'password', 'incident_brief', 'transcript', 'raw_payload', 'arguments',
})
PHONE = re.compile(r'(?<!\w)\+\d[\d ()-]{6,}\d')


def _public(value):
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items() if k.lower() not in PRIVATE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_public(v) for v in value]
    if isinstance(value, str):
        return PHONE.sub('[redacted phone]', value)
    return value


def _snapshot_scenario(snapshot, scenario, epoch):
    """Explicit actions/effects come from scenario; current risk comes from snapshot."""
    assets = {a['asset_id']: a for a in snapshot['assets']}
    if set(assets) != {a.asset_id for a in scenario.locations}:
        raise ValueError('snapshot and scenario asset IDs must match')
    locations = []
    for location in scenario.locations:
        asset = assets[location.asset_id]
        arrival = asset.get('fire_arrival_at')
        minutes = max(0, (utc(arrival) - epoch).total_seconds() / 60) if arrival else None
        deadline = location.deadline_min
        if minutes is not None:
            deadline = min(deadline, minutes) if deadline is not None else minutes
        locations.append(replace(location, name=asset['name'],
            people=asset['estimated_occupancy'], distance_m=asset['distance_to_fire_m'],
            fire_arrival_min=minutes, deadline_min=deadline,
            forecast_source=asset.get('forecast_source') if arrival else None,
            evacuation_min=asset.get('evacuation_min'),
            evacuation_source=asset.get('evacuation_source')))
    return replace(scenario, locations=tuple(locations))


def _merge_assessments(assessments):
    """Newest report per asset, retaining earlier explicit help/person requests."""
    selected = []
    for aid in sorted(assessments):
        ordered = sorted(assessments[aid], key=lambda a: (a.observed_min, a.call_id))
        latest = ordered[-1]
        retained = {}
        for field, adverse in (('can_self_evacuate', False),
                               ('transport_available', False), ('wants_human', True)):
            if any(getattr(a, field) is adverse for a in ordered):
                retained[field] = adverse
        conflict = any(getattr(latest, k) is not None and getattr(latest, k) != v
                       for k, v in retained.items())
        selected.append(replace(latest, **retained,
            confidence=None if conflict else latest.confidence,
            contradictory=latest.contradictory or conflict,
            # Evidence is internal-only; references and availability are public.
            evidence='; '.join(a.evidence for a in ordered if a.evidence)))
    return selected


def _call_summary(record, assessment):
    request, result = record['request'], record['result'] or {}
    ability, transport = result.get('can_self_evacuate'), result.get('transport_available')
    assistance = (True if ability is False or transport is False else
                  False if ability is True and transport is True else None)
    transfer = record['transfer_status'] or result.get('transfer_status')
    return dict(request_id=request['request_id'], asset_id=request['asset_id'],
        snapshot_id=request['snapshot_id'], input_mode=request['input_mode'],
        status=record['status'], dispatch_state=record['dispatch_state'],
        observed_at=result.get('observed_at') or record['lifecycle_at'],
        **{k: result.get(k) for k in ANSWER_FIELDS}, reported_needs_assistance=assistance,
        confidence=assessment.confidence if assessment else None,
        human_followup_required=True, human_followup_reasons=record['followup_reasons'],
        transfer_status=transfer if transfer in ('requested', 'failed') else None,
        transfer_verified=False, review_status='human_review_required',
        evidence_fields=sorted(result.get('evidence', {})),
        provenance=dict(request_id=request['request_id'], snapshot_id=request['snapshot_id'],
            source='stored_call_assessment' if result else 'stored_call_lifecycle',
            evidence_time_basis=result.get('evidence_time_basis'),
            evidence_verification=result.get('evidence_verification', {}),
            evidence_observed_at=result.get('evidence_observed_at', {})))


def _task_summary(task):
    keys = ('task_id', 'asset_id', 'action', 'status', 'required_capabilities',
            'assigned_team_id', 'deadline_at', 'created_at', 'updated_at',
            'based_on_snapshot_id', 'affected_by_snapshot_id', 'suggested')
    public = {k: task[k] for k in keys}
    public['kind'] = task['reason'][6:] if task['reason'].startswith('voice:') else 'analyst_task'
    public['human_controlled'] = True
    return public


def _asset_views(state):
    views = {}
    for section in (state['assets'], state['calls'], state['tasks'],
                    state['plan']['locations'], state['contacts']['ranked'], state['contacts']['review']):
        for row in section:
            views.setdefault(row['asset_id'], []).append(row)
    return views


class CoordinationStore:
    """Persistent public projection; use a single scenario and input mode per DB.

    `epoch` and `clock()` must be aware UTC datetimes. `state()` is None until
    the first successful refresh. `updates()` returns compact event envelopes.
    `voice` and `tasks` provide the existing explicit ingestion/analyst APIs.
    """
    def __init__(self, path, *, epoch, clock=None):
        self.epoch = utc(epoch.isoformat())
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.voice = VoiceStore(path, epoch=self.epoch, clock=self.clock)
        self.tasks = self.voice.tasks
        self.conn = self.voice.conn
        self.conn.executescript(SCHEMA)

    def close(self):
        self.voice.close()

    def state(self):
        row = self.conn.execute(
            'SELECT payload FROM coordination_revisions ORDER BY revision DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def updates(self, after_revision=0):
        if type(after_revision) is not int or after_revision < 0:
            raise ValueError('after_revision must be a nonnegative integer')
        return [json.loads(r[0]) for r in self.conn.execute(
            'SELECT event FROM coordination_revisions WHERE revision>? ORDER BY revision',
            (after_revision,))]

    def _accept_snapshot(self, snapshot, scenario, now, previous):
        if validate_snapshot(snapshot):
            raise ValueError('invalid location snapshot')
        if snapshot['scenario_id'] != scenario.scenario_id:
            raise ValueError('snapshot scenario mismatch')
        as_of = utc(snapshot['as_of'])
        utc(snapshot['computed_at'])
        if not self.epoch <= as_of <= now:
            raise ValueError('snapshot outside scenario time range')
        if previous:
            if (snapshot['scenario_id'], snapshot['input_mode']) != (
                    previous['scenario_id'], previous['input_mode']):
                raise ValueError('database scenario or input mode mismatch')
            if now < utc(previous['as_of']):
                raise ValueError('coordination clock cannot regress')
        fingerprint = digest(snapshot)
        same = self.conn.execute('SELECT fingerprint FROM coordination_snapshots WHERE snapshot_id=?',
                                 (snapshot['snapshot_id'],)).fetchone()
        newest = self.conn.execute(
            'SELECT sequence, as_of FROM coordination_snapshots ORDER BY sequence DESC LIMIT 1').fetchone()
        if newest and (snapshot['sequence'] < newest['sequence'] or as_of < utc(newest['as_of'])):
            raise ValueError('snapshot sequence or time cannot regress')
        if same and same[0] != fingerprint:
            raise ValueError('snapshot identity is immutable')
        self.conn.execute('INSERT OR IGNORE INTO coordination_snapshots VALUES (?, ?, ?, ?)',
            (snapshot['snapshot_id'], snapshot['sequence'], fingerprint, snapshot['as_of']))

    def _calls(self, snapshot):
        accepted = {r[0] for r in self.conn.execute('SELECT snapshot_id FROM coordination_snapshots')}
        asset_ids = {a['asset_id'] for a in snapshot['assets']}
        assessments, summaries, records, errors = {}, [], [], []
        for row in self.conn.execute('SELECT request_id FROM voice_calls ORDER BY request_id').fetchall():
            record = self.voice.get(row[0])
            req = record['request']
            if req['asset_id'] not in asset_ids:
                continue
            if req['snapshot_id'] not in accepted or req['input_mode'] != snapshot['input_mode']:
                code = ('call_snapshot_not_accepted' if req['snapshot_id'] not in accepted
                        else 'call_input_mode_mismatch')
                errors.append(dict(code=code, asset_id=req['asset_id'], request_id=req['request_id']))
                continue
            assessment = self.voice.assessment(row[0]) if record['provider_call_id'] else None
            if assessment:
                assessments.setdefault(req['asset_id'], []).append(assessment)
            summaries.append(_call_summary(record, assessment))
            records.append(record)
        return _merge_assessments(assessments), summaries, records, errors

    def refresh(self, snapshot, scenario, centres, routes, *, road_warnings=()):
        """Recompute at whole elapsed minutes and atomically publish changed state.

        Snapshot records are authoritative for timing, distance and occupancy.
        Call evidence keeps its exact UTC observation time. Capacity is proposed,
        never durably reserved here. Existing analyst task ownership/status survive.
        """
        now = utc(self.clock().isoformat())
        if now < self.epoch:
            raise ValueError('coordination clock precedes epoch')
        with self.voice._transaction():
            previous = self.state()
            self._accept_snapshot(snapshot, scenario, now, previous)
            current = _snapshot_scenario(snapshot, scenario, self.epoch)
            assessments, calls, records, errors = self._calls(snapshot)
            elapsed = int((now - self.epoch).total_seconds() // 60)
            plan = coordinate_evacuation(current, assessments, centres, routes,
                contact_policy=ContactPolicy(now_min=elapsed, buffer_min=scenario.buffer_min),
                road_warnings=road_warnings)
            self.voice.record_plan(plan, snapshot_id=snapshot['snapshot_id'])
            accepted = {r[0] for r in self.conn.execute('SELECT snapshot_id FROM coordination_snapshots')}
            tasks = [t for t in self.tasks.tasks() if t['based_on_snapshot_id'] in accepted]
            for row in plan['locations']:
                row['call_evidence'] = None
                row['call_source'] = 'stored_call_assessment' if row['call_id'] else None
                row['assessment_request_ids'] = [c['request_id'] for c in calls
                                                  if c['asset_id'] == row['asset_id']]
                row['call_id'] = None  # provider IDs remain in the private store
            plan['input_mode'] = snapshot['input_mode']
            plan['capacity_status'] = 'proposal_only'
            candidate = _public(dict(schema_version='coordination-state-1',
                scenario_id=snapshot['scenario_id'], snapshot_id=snapshot['snapshot_id'],
                input_mode=snapshot['input_mode'], epoch=self.epoch.isoformat(), elapsed_min=elapsed,
                assets=snapshot['assets'], contacts=plan['contacts'], calls=calls, plan=plan,
                teams=self.tasks.teams(), tasks=[_task_summary(t) for t in tasks], errors=errors,
                snapshot_as_of=snapshot['as_of'], data_status=snapshot['data_status'],
                dispatch=False, live_validation=False))
            # Private text affects the revision digest but never the public payload.
            fingerprint = digest([candidate, records, tasks])
            last = self.conn.execute(
                'SELECT revision, fingerprint FROM coordination_revisions ORDER BY revision DESC LIMIT 1').fetchone()
            if last and last['fingerprint'] == fingerprint:
                return previous
            revision = last['revision'] + 1 if last else 1
            before, after = _asset_views(previous) if previous else {}, _asset_views(candidate)
            changed = sorted(aid for aid in before.keys() | after.keys() if before.get(aid) != after.get(aid))
            if previous and (previous['teams'] != candidate['teams'] or
                             previous['plan']['response'] != plan['response']):
                changed = sorted(before.keys() | after.keys())
            event = dict(schema_version='coordination-update-1', event_id=f'coordination:{revision}',
                revision=revision, scenario_id=snapshot['scenario_id'], snapshot_id=snapshot['snapshot_id'],
                as_of=now.isoformat(), kind='coordination_refreshed', changed_asset_ids=changed,
                refresh='full_state', input_mode=snapshot['input_mode'])
            candidate.update(revision=revision, as_of=now.isoformat(), events=[event])
            self.conn.execute('INSERT INTO coordination_revisions VALUES (?, ?, ?, ?)',
                              (revision, fingerprint, encoded(candidate), encoded(event)))
            return candidate


def main(argv=None):
    """One explicit local service tick; never starts a call worker."""
    import argparse
    from pathlib import Path
    from .evacuation_readiness import ReceptionCentre, EvacuationRoute
    from .priority_models import scenario_from_dict

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['tick'])
    parser.add_argument('--database', required=True)
    parser.add_argument('--epoch', required=True, type=utc)
    parser.add_argument('--as-of', type=utc, help='explicit UTC clock for offline replay')
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--readiness', required=True,
                        help='JSON object with centres, routes, optional road_warnings')
    args = parser.parse_args(argv)
    store = None
    try:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
        scenario = scenario_from_dict(json.loads(Path(args.scenario).read_text(encoding='utf-8')))
        readiness = json.loads(Path(args.readiness).read_text(encoding='utf-8'))
        centres = [ReceptionCentre(**c) for c in readiness['centres']]
        routes = [EvacuationRoute(**r) for r in readiness['routes']]
        clock = (lambda: args.as_of) if args.as_of is not None else None
        store = CoordinationStore(args.database, epoch=args.epoch, clock=clock)
        state = store.refresh(snapshot, scenario, centres, routes,
                              road_warnings=readiness.get('road_warnings', ()))
        print(encoded(state))
    except (ValueError, TypeError, KeyError, OSError):
        parser.exit(2, 'Coordination tick rejected: check input contracts, database and UTC times.\n')
    finally:
        if store is not None:
            store.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
