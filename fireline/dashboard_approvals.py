"""Version-bound analyst confirmations for public dashboard crew plans."""
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


_TASK_FIELDS = (
    'action_id', 'task_id', 'asset_id', 'site_id', 'action', 'status',
    'action_version',
    'depart_min', 'travel_min', 'start_min', 'finish_min', 'deadline_min',
    'duration_min', 'prerequisites', 'required_capabilities', 'route_source',
    'route_status', 'route_id', 'route_road_ids', 'from_node', 'to_node',
    'destination_id', 'path_lonlat', 'path_nodes', 'effects', 'readiness',
    'transport_people',
)
_TEAM_FIELDS = ('locked', 'remaining_transport_capacity')
_ASSET_FIELDS = ('asset_id', 'name', 'latitude', 'longitude')
_LOCATION_FIELDS = (
    'asset_id', 'destination_id', 'destination_name', 'evacuation_status',
    'assistance_review_required',
)


class PlanChanged(ValueError):
    """The browser identifiers do not name the current server-owned plan."""


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def _selected(record, fields):
    if not isinstance(record, Mapping):
        return {}
    return {field: record[field] for field in fields if field in record}


def _incident_id(state):
    return state.get('incident_id') or state.get('scenario_id') or state.get('id')


def plans_for(state, *, source, incident_id, revision, snapshot_id):
    """Return current public crew plans after exact rendered-state validation."""
    if (not isinstance(state, Mapping) or _incident_id(state) != incident_id
            or state.get('snapshot_id') != snapshot_id
            or type(state.get('revision')) is not int or state['revision'] != revision):
        raise PlanChanged('rendered plan is no longer current')
    plan = state.get('plan') if isinstance(state.get('plan'), Mapping) else {}
    response = plan.get('response') or {}
    response_teams = response.get('teams') if isinstance(response, Mapping) else []
    response_teams = response_teams if isinstance(response_teams, list) else []
    roster = state.get('teams') if isinstance(state.get('teams'), list) else []
    ids = []
    for team in [*roster, *response_teams]:
        team_id = team.get('team_id') if isinstance(team, Mapping) else None
        if _nonblank(team_id) and team_id not in ids:
            ids.append(team_id)
    asset_rows = state.get('assets') if isinstance(state.get('assets'), list) else []
    assets = {row.get('asset_id'): row for row in asset_rows
              if isinstance(row, Mapping) and _nonblank(row.get('asset_id'))}
    location_rows = plan.get('locations') if isinstance(plan.get('locations'), list) else []
    locations = {row.get('asset_id'): row for row in location_rows
                 if isinstance(row, Mapping) and _nonblank(row.get('asset_id'))}
    plans = []
    for team_id in ids:
        team = next((row for row in response_teams
                     if isinstance(row, Mapping) and row.get('team_id') == team_id), {})
        tasks = team.get('tasks') if isinstance(team.get('tasks'), list) else []
        public_tasks = [_selected(task, _TASK_FIELDS) for task in tasks
                        if isinstance(task, Mapping)]
        asset_ids = []
        for task in public_tasks:
            asset_id = task.get('asset_id') or task.get('site_id')
            if _nonblank(asset_id) and asset_id not in asset_ids:
                asset_ids.append(asset_id)
        reviewed = {
            'source': source,
            'incident_id': incident_id,
            'snapshot_id': snapshot_id,
            'epoch': state.get('epoch'),
            'input_mode': state.get('input_mode'),
            'team_id': team_id,
            'team': _selected(team, _TEAM_FIELDS),
            'tasks': public_tasks,
            'assets': [_selected(assets[asset_id], _ASSET_FIELDS)
                       for asset_id in asset_ids if asset_id in assets],
            'locations': [_selected(locations[asset_id], _LOCATION_FIELDS)
                          for asset_id in asset_ids if asset_id in locations],
        }
        encoded = json.dumps(reviewed, ensure_ascii=True, allow_nan=False,
                             sort_keys=True, separators=(',', ':')).encode('utf-8')
        plans.append({
            'team_id': team_id,
            'plan_version': hashlib.sha256(encoded).hexdigest(),
            'can_confirm': any(task.get('status') == 'proposed' for task in public_tasks),
        })
    return plans


class ApprovalStore:
    """Separate append-only SQLite records; never opens a coordination database."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute('''
                CREATE TABLE IF NOT EXISTS dashboard_crew_approvals (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    approval_id TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    plan_version TEXT NOT NULL,
                    analyst TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    UNIQUE(source, incident_id, team_id, plan_version)
                )
            ''')

    @contextmanager
    def _connect(self):
        database = sqlite3.connect(self.path, timeout=5)
        try:
            with database:
                yield database
        finally:
            database.close()

    def confirm(self, *, source, incident_id, snapshot_id, team_id,
                plan_version, analyst):
        approval_id = str(uuid4())
        confirmed_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        with self._connect() as database:
            database.execute('''
                INSERT INTO dashboard_crew_approvals
                    (approval_id, source, incident_id, snapshot_id, team_id,
                     plan_version, analyst, confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, incident_id, team_id, plan_version) DO NOTHING
            ''', (approval_id, source, incident_id, snapshot_id, team_id,
                  plan_version, analyst, confirmed_at))
            row = database.execute('''
                SELECT approval_id, analyst, confirmed_at, plan_version
                FROM dashboard_crew_approvals
                WHERE source=? AND incident_id=? AND team_id=? AND plan_version=?
            ''', (source, incident_id, team_id, plan_version)).fetchone()
        return dict(zip(('approval_id', 'analyst', 'confirmed_at', 'plan_version'), row))

    def approval(self, *, source, incident_id, team_id, plan_version):
        with self._connect() as database:
            row = database.execute('''
                SELECT approval_id, analyst, confirmed_at, plan_version
                FROM dashboard_crew_approvals
                WHERE source=? AND incident_id=? AND team_id=? AND plan_version=?
            ''', (source, incident_id, team_id, plan_version)).fetchone()
        if row is None:
            return None
        return dict(zip(('approval_id', 'analyst', 'confirmed_at', 'plan_version'), row))

    def events(self, *, source, incident_id):
        with self._connect() as database:
            rows = database.execute('''
                SELECT approval_id, confirmed_at, analyst, team_id
                FROM dashboard_crew_approvals
                WHERE source=? AND incident_id=? ORDER BY sequence
            ''', (source, incident_id)).fetchall()
        return [{
            'event_id': approval_id,
            'as_of': confirmed_at,
            'kind': 'plan_confirmed',
            'notes': f'Crew plan confirmed by {analyst}',
            'source': source,
            'team_id': team_id,
            'incident_id': incident_id,
        } for approval_id, confirmed_at, analyst, team_id in rows]


def envelope(approvals, plans, *, source, incident_id, revision, snapshot_id, analyst):
    return {
        'source': source,
        'incident_id': incident_id,
        'revision': revision,
        'snapshot_id': snapshot_id,
        'analyst': analyst,
        'plans': [{
            **plan,
            'approval': approvals.approval(
                source=source, incident_id=incident_id, team_id=plan['team_id'],
                plan_version=plan['plan_version']),
        } for plan in plans],
        'events': approvals.events(source=source, incident_id=incident_id),
    }
