"""Offline multi-team proposal heuristic; JSON contract and limits are in readme.md.

No provider access, crew dispatch, inferred roads or spread effects. Existing exact
one-crew planning is independent. Every elapsed minute uses the caller's scenario epoch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .priority_models import number, string_sequence
from .response_priority import after_deadline
from .routing import RoadGraph


DIMENSIONS = ('assisted', 'people', 'value')
ACTIVE_STATUSES = ('informed', 'en_route', 'in_progress')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')


def _integer(value, label):
    number(value, label)
    # JSON count contract deliberately rejects bool and custom numeric objects.
    if type(value) is not int:
        raise ValueError(f'{label} must be an integer')


def _boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(f'{label} must be boolean')


def _indexed(rows, key):
    result = {}
    for row in rows:
        _text(row[key], key)
        if row[key] in result:
            raise ValueError(f'duplicate {key}')
        result[row[key]] = row
    return dict(sorted(result.items()))


def _effects(effects, assets):
    _indexed(effects, 'asset_id')
    for effect in effects:
        if effect['asset_id'] not in assets:
            raise ValueError('unknown effect asset_id')
        number(effect['coverage'], 'coverage')
        if effect['coverage'] > 1:
            raise ValueError('coverage must be <= 1')
        _boolean(effect['confirmed'], 'confirmed')
        _text(effect['source'], 'effect source')


def _validate(data):
    if data['schema_version'] != 'multi-response-input-1':
        raise ValueError('expected multi-response-input-1')
    for key in ('scenario_id', 'snapshot_id'):
        _text(data[key], key)
    for key in ('now_min', 'horizon_min', 'buffer_min'):
        number(data[key], key)
    if data['now_min'] > data['horizon_min']:
        raise ValueError('now_min exceeds horizon_min')
    assets = _indexed(data['assets'], 'asset_id')
    teams = _indexed(data['teams'], 'team_id')
    actions = _indexed(data['actions'], 'action_id')
    readiness = _indexed(data.get('readiness', []), 'asset_id')
    for asset in assets.values():
        _text(asset['node_id'], 'node_id')
        for key in (*DIMENSIONS, 'deadline_min'):
            number(asset[key], key, nullable=True)
        for key in ('people', 'assisted'):
            if asset[key] is not None:
                _integer(asset[key], key)
        if asset['people'] is not None and asset['assisted'] is not None:
            if asset['assisted'] > asset['people']:
                raise ValueError('assisted exceeds people')
    for team in teams.values():
        _text(team['start_node_id'], 'start_node_id')
        _boolean(team['available'], 'available')
        _integer(team['transport_capacity'], 'transport_capacity')
        string_sequence(team['capabilities'], 'capabilities')
        for key in ('available_from_min', 'available_until_min'):
            number(team[key], key)
        if team['available_from_min'] > team['available_until_min']:
            raise ValueError('invalid team availability window')
    for action in actions.values():
        if action['asset_id'] not in assets:
            raise ValueError('unknown action asset_id')
        number(action['duration_min'], 'duration_min')
        if action['duration_min'] <= 0:
            raise ValueError('duration_min must be positive')
        number(action['deadline_min'], 'deadline_min', nullable=True)
        _integer(action['transport_people'], 'transport_people')
        _boolean(action['readiness_required'], 'readiness_required')
        string_sequence(action['capabilities'], 'capabilities')
        string_sequence(action['requires'], 'requires')
        if len(set(action['requires'])) != len(action['requires']):
            raise ValueError('duplicate prerequisite')
        if not set(action['requires']) <= actions.keys():
            raise ValueError('unknown prerequisite')
        _effects(action['effects'], assets)
    # Iterative topological check avoids recursive traversal of untrusted action graphs.
    remaining = set(actions)
    resolved = set()
    while remaining:
        ready = {key for key in remaining if set(actions[key]['requires']) <= resolved}
        if not ready:
            raise ValueError('prerequisite cycle')
        remaining -= ready
        resolved |= ready
    seen_routes = set()
    for route in data['routes']:
        endpoints = (route['from_node'], route['to_node'])
        for node in endpoints:
            _text(node, 'route node')
        if endpoints in seen_routes:
            raise ValueError('duplicate directed route')
        seen_routes.add(endpoints)
        number(route['minutes'], 'route minutes')
        number(route['available_until_min'], 'route availability', nullable=True)
        for key in ('confirmed', 'safe'):
            _boolean(route[key], key)
        _text(route['source'], 'route source')
    for aid, outcome in readiness.items():
        if aid not in assets:
            raise ValueError('unknown readiness asset_id')
        if outcome['status'] not in ('assistance_required', 'unknown', 'no_answer',
                                     'self_evacuating', 'completed'):
            raise ValueError('invalid readiness status')
        for key in ('source', 'request_id'):
            _text(outcome[key], key)
        for key in ('observed_min', 'valid_until_min'):
            number(outcome[key], key)
        if outcome['observed_min'] > outcome['valid_until_min']:
            raise ValueError('invalid readiness window')
    return assets, teams, actions, readiness


class _Routes:
    """Adapt supplied legs or explicitly qualified graph edges; never infer travel."""

    def __init__(self, data, graph):
        if graph is not None and data['routes']:
            raise ValueError('supply routes or graph, not both')
        self.routes = {(r['from_node'], r['to_node']): r for r in data['routes']}
        self.buffer = data['buffer_min']
        self.graph = None
        if graph is not None:
            if graph.g.is_multigraph():
                raise ValueError('parallel graph edges are unsupported')
            self.graph = RoadGraph(graph.g.copy())
            for u, v, edge in list(self.graph.g.edges(data=True)):
                number(edge['travel_min'], 'graph travel_min')
                cut = edge.get('cut_min')
                # The old graph default is infinity, which is not hazard evidence.
                if (edge.get('confirmed') is not True or edge.get('safe') is not True
                        or not isinstance(edge.get('source'), str) or not edge['source'].strip()
                        or cut is None or cut == float('inf')):
                    self.graph.g.remove_edge(u, v)
                else:
                    number(cut, 'graph cut_min')

    def leg(self, source, target, depart):
        if source == target:
            return dict(minutes=0, source='same supplied node')
        if self.graph is None:
            route = self.routes.get((source, target))
            if (route is None or not route['confirmed'] or not route['safe']
                    or route['available_until_min'] is None
                    or depart + route['minutes'] + self.buffer >= route['available_until_min']):
                return None
            return dict(minutes=route['minutes'], source=route['source'])
        graph = self.graph
        if source not in graph.g or target not in graph.g:
            return None
        dist, prev = graph.earliest_arrival(source, depart + self.buffer)
        path = graph.path_to(prev, source, target)
        if not path:
            return None
        result = dict(minutes=dist[target], source=sorted({
            graph.g[u][v]['source'] for u, v in zip(path, path[1:])}), path_nodes=path)
        geometry = self._geometry(path)
        if geometry is not None:
            result['path_lonlat'] = geometry
        return result

    def _geometry(self, path):
        points = []
        for u, v in zip(path, path[1:]):
            segment = self.graph.g[u][v].get('geometry_lonlat')
            if not isinstance(segment, (list, tuple)) or len(segment) < 2:
                return None
            segment = [list(point) for point in segment]
            for point in segment:
                if len(point) != 2:
                    raise ValueError('geometry point must be lon/lat')
                number(point[0], 'longitude', minimum=-180)
                number(point[1], 'latitude', minimum=-90)
                if point[0] > 180 or point[1] > 90:
                    raise ValueError('invalid geometry coordinates')
            try:
                start, end = list(self.graph.node_lonlat(u)), list(self.graph.node_lonlat(v))
            except KeyError:
                return None
            if segment[0] == end and segment[-1] == start:
                segment.reverse()
            if segment[0] != start or segment[-1] != end:
                return None
            points.extend(segment if not points else segment[1:])
        return points


def _public_effects(action):
    return [{key: effect[key] for key in ('asset_id', 'coverage', 'confirmed', 'source')}
            for effect in sorted(action['effects'], key=lambda e: e['asset_id'])]


def _action_version(action):
    fields = {k: action[k] for k in ('action_id', 'asset_id', 'duration_min', 'deadline_min',
              'transport_people', 'readiness_required')}
    fields.update(requires=sorted(action['requires']), capabilities=sorted(action['capabilities']),
                  effects=_public_effects(action))
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


class _Planner:
    def __init__(self, data, graph):
        self.data = data
        self.assets, self.teams, self.actions, self.readiness = _validate(data)
        self.routes = _Routes(data, graph)
        self.coverage = dict.fromkeys(self.assets, 0.0)
        self.done = {}
        self.reserved = set()
        self.review = []
        self.states = {key: dict(node=team['start_node_id'],
                       time=max(data['now_min'], team['available_from_min']),
                       capacity=team['transport_capacity'], tasks=[], locked=False)
                       for key, team in self.teams.items()}

    def _need_reasons(self, action):
        asset = self.assets[action['asset_id']]
        reasons = []
        if asset['deadline_min'] is None or action['deadline_min'] is None:
            reasons.append('unknown_deadline')
        targets = [self.assets[e['asset_id']] for e in action['effects']]
        if any(any(a[key] is None for key in DIMENSIONS) for a in targets):
            reasons.append('unknown_needs')
        if any(a['deadline_min'] is None for a in targets):
            reasons.append('unknown_deadline')
        if any(not e['confirmed'] for e in action['effects']):
            reasons.append('unconfirmed_effect')
        return reasons

    def candidate(self, action, team_id):
        state, team = self.states[team_id], self.teams[team_id]
        reasons = self._need_reasons(action)
        if state['locked']:
            reasons.append('committed_team')
        if not team['available']:
            reasons.append('team_unavailable')
        if not set(action['capabilities']) <= set(team['capabilities']):
            reasons.append('capabilities')
        if action['transport_people'] > state['capacity']:
            reasons.append('transport_capacity')
        if not set(action['requires']) <= self.done.keys():
            reasons.append('prerequisites')
        if reasons:
            return None, reasons
        # Waiting at the current node is conservative; site arrival never precedes dependencies.
        depart = max(state['time'], *(self.done[k] for k in action['requires']), 0)
        target = self.assets[action['asset_id']]
        leg = self.routes.leg(state['node'], target['node_id'], depart)
        if leg is None:
            return None, ['route_unavailable']
        start = depart + leg['minutes']
        finish = start + action['duration_min']
        deadline = min(self.data['horizon_min'], team['available_until_min'],
                       target['deadline_min'], action['deadline_min'])
        if after_deadline(finish + self.data['buffer_min'], deadline):
            return None, ['deadline']
        outcome = self.readiness.get(action['asset_id'])
        if action['readiness_required'] or (outcome and action['transport_people']):
            if (outcome is None or outcome['status'] != 'assistance_required'
                    or outcome['observed_min'] > self.data['now_min']
                    or finish + self.data['buffer_min'] >= outcome['valid_until_min']):
                return None, ['readiness_review']
        row = dict(action_id=action['action_id'], asset_id=action['asset_id'], team_id=team_id,
                   scenario_id=self.data['scenario_id'], snapshot_id=self.data['snapshot_id'],
                   action_version=_action_version(action), status='proposed',
                   from_node=state['node'], to_node=target['node_id'], depart_min=depart,
                   travel_min=leg['minutes'], start_min=start, finish_min=finish,
                   prerequisites=sorted(action['requires']), effects=_public_effects(action),
                   transport_people=action['transport_people'], route_source=leg['source'])
        if outcome:
            row['readiness'] = {k: outcome[k] for k in ('asset_id', 'status', 'observed_min',
                                'valid_until_min', 'source', 'request_id')}
        for key in ('path_lonlat', 'path_nodes'):
            value = leg.get(key)
            if value is not None:
                row[key] = value
        return row, []

    def gain(self, action, finish=0):
        gains = [0.0, 0.0, 0.0]
        for effect in action['effects']:
            asset = self.assets[effect['asset_id']]
            if (not effect['confirmed'] or asset['deadline_min'] is None
                    or any(asset[k] is None for k in DIMENSIONS)
                    or after_deadline(finish + self.data['buffer_min'], asset['deadline_min'])):
                continue
            delta = max(0, effect['coverage'] - self.coverage[effect['asset_id']])
            for i, key in enumerate(DIMENSIONS):
                gains[i] += delta * asset[key]
        return tuple(gains)

    def priority(self, action, row):
        """Best downstream benefit is a heuristic hint, never feasibility or optimality proof."""
        best = self.gain(action, row['finish_min'])
        reachable = {action['action_id']}
        changed = True
        while changed:
            changed = False
            for key, child in self.actions.items():
                if key not in reachable and key not in self.reserved and reachable.intersection(child['requires']):
                    reachable.add(key)
                    changed = True
                    if not self._need_reasons(child):
                        best = max(best, self.gain(child, row['finish_min'] + child['duration_min']))
        return (*(-x for x in best), row['finish_min'], row['action_id'], row['team_id'])

    def credit(self, row):
        finish = row.get('actual_finish_min', row['finish_min'])
        gained = {}
        for effect in row['effects']:
            asset = self.assets[effect['asset_id']]
            if (not effect['confirmed'] or asset['deadline_min'] is None
                    or any(asset[k] is None for k in DIMENSIONS)
                    or after_deadline(finish + self.data['buffer_min'], asset['deadline_min'])):
                continue
            delta = max(0, effect['coverage'] - self.coverage[effect['asset_id']])
            self.coverage[effect['asset_id']] += delta
            if delta:
                gained[effect['asset_id']] = delta
        row['coverage_gained'] = gained

    def load_commitments(self):
        commitments = _indexed(self.data.get('committed', []), 'action_id')
        rows = sorted(commitments.values(), key=lambda r: (r['depart_min'], r['action_id']))
        for original in rows:
            fields = ('action_id', 'asset_id', 'team_id', 'scenario_id', 'snapshot_id',
                      'action_version', 'status', 'from_node', 'to_node', 'depart_min',
                      'travel_min', 'start_min', 'finish_min', 'prerequisites', 'transport_people')
            row = {key: original[key] for key in fields}
            row['prerequisites'] = list(original['prerequisites'])
            for key in fields[:9]:
                _text(row[key], key)
            if row['scenario_id'] != self.data['scenario_id']:
                raise ValueError('committed assignment belongs to another scenario')
            if row['status'] not in (*ACTIVE_STATUSES, 'completed'):
                raise ValueError('committed status must be informed, en_route, in_progress or completed')
            for key in ('depart_min', 'travel_min', 'start_min', 'finish_min'):
                number(row[key], key)
            if (row['start_min'] < row['depart_min'] or row['finish_min'] < row['start_min']
                    or abs(row['start_min'] - row['depart_min'] - row['travel_min']) > 1e-9):
                raise ValueError('invalid committed timing')
            _integer(row['transport_people'], 'transport_people')
            string_sequence(row['prerequisites'], 'prerequisites')
            _effects(original['effects'], {e['asset_id']: {} for e in original['effects']})
            row['effects'] = _public_effects(original)
            # Preserve only public route provenance, never arbitrary persisted call payloads.
            source = original['route_source']
            if isinstance(source, list):
                string_sequence(source, 'route_source')
            else:
                _text(source, 'route_source')
            row['route_source'] = list(source) if isinstance(source, list) else source
            self.reserved.add(row['action_id'])
            reasons = []
            team_id = row['team_id']
            if team_id not in self.states:
                self.states[team_id] = dict(node=row['to_node'], time=self.data['now_min'],
                                           capacity=0, tasks=[], locked=True)
                reasons.append('missing_team')
            state = self.states[team_id]
            if row['transport_people'] > state['capacity']:
                reasons.append('transport_capacity')
            state['capacity'] = max(0, state['capacity'] - row['transport_people'])
            action = self.actions.get(row['action_id'])
            if action is None:
                reasons.append('missing_action')
            elif row['action_version'] != _action_version(action):
                reasons.append('changed_action')
            elif (row['asset_id'] != action['asset_id']
                  or row['transport_people'] != action['transport_people']
                  or sorted(row['prerequisites']) != sorted(action['requires'])
                  or row['effects'] != _public_effects(action)):
                raise ValueError('committed payload does not match its action version')
            if row['asset_id'] not in self.assets or any(e['asset_id'] not in self.assets for e in row['effects']):
                reasons.append('missing_asset')
            row['coverage_gained'] = {}
            if row['status'] == 'completed':
                actual_finish = original.get('actual_finish_min')
                number(actual_finish, 'actual_finish_min')
                if actual_finish > self.data['now_min'] or actual_finish < row['start_min']:
                    raise ValueError('actual_finish_min must be between start_min and now_min')
                row['actual_finish_min'] = actual_finish
                # A current roster/capacity change cannot undo actual completed work.
                if 'missing_asset' not in reasons:
                    self.credit(row)
                if not {'missing_action', 'changed_action', 'missing_asset'}.intersection(reasons):
                    self.done[row['action_id']] = actual_finish
                if reasons:
                    state['locked'] = True
            else:
                state['locked'] = True
                reasons.append('active_commitment')
                if row['snapshot_id'] != self.data['snapshot_id']:
                    reasons.append('stale_snapshot')
                if team_id in self.teams and not self.teams[team_id]['available']:
                    reasons.append('team_unavailable')
                if row['finish_min'] < self.data['now_min']:
                    reasons.append('overdue_commitment')
                leg = self.routes.leg(row['from_node'], row['to_node'], max(
                    row['depart_min'], self.data['now_min']))
                if leg is None:
                    reasons.append('route_unavailable')
            state['tasks'].append(row)
            if reasons:
                self.review.append(dict(action_id=row['action_id'], asset_id=row['asset_id'],
                                        team_id=team_id, reason='active_commitment'
                                        if row['status'] in ACTIVE_STATUSES else 'committed_review',
                                        reasons=sorted(set(reasons))))

    def run(self):
        self.load_commitments()
        while True:
            candidates = []
            for key, action in self.actions.items():
                if key in self.reserved:
                    continue
                for team_id in self.teams:
                    row, _ = self.candidate(action, team_id)
                    if row is not None:
                        candidates.append((self.priority(action, row), row))
            if not candidates:
                break
            _, row = min(candidates, key=lambda candidate: candidate[0])
            self.reserved.add(row['action_id'])
            self.done[row['action_id']] = row['finish_min']
            self.credit(row)
            state = self.states[row['team_id']]
            state.update(node=row['to_node'], time=row['finish_min'],
                         capacity=state['capacity'] - row['transport_people'])
            state['tasks'].append(row)
        unassigned = []
        for key, action in self.actions.items():
            if key not in self.reserved:
                reasons = set()
                for team_id in self.teams:
                    _, blocked = self.candidate(action, team_id)
                    reasons.update(blocked)
                unassigned.append(dict(action_id=key, asset_id=action['asset_id'],
                                       reasons=sorted(reasons or {'no_teams'})))
        return dict(schema_version='multi-response-plan-1', scenario_id=self.data['scenario_id'],
                    snapshot_id=self.data['snapshot_id'], now_min=self.data['now_min'],
                    optimal=False, dispatch=False, method='deterministic prerequisite-aware greedy heuristic',
                    teams=[dict(team_id=key, tasks=state['tasks'], locked=state['locked'],
                                remaining_transport_capacity=state['capacity'])
                           for key, state in sorted(self.states.items())],
                    coverage=self.coverage, objective={key+'_units': sum(
                        self.coverage[aid] * asset[key] for aid, asset in self.assets.items()
                        if asset[key] is not None) for key in DIMENSIONS},
                    unassigned=unassigned, review=self.review + unassigned)


def plan_multi_response(data, *, graph=None):
    """Return JSON-safe proposed per-team tasks; never mutate inputs or dispatch crews.

    ``data`` follows multi-response-input-1 in readme.md. Supply either directed
    ``routes`` or a qualified RoadGraph, never both. Unknown route safety is unusable.
    """
    return _Planner(data, graph).run()


def main(argv=None):
    """Read an offline JSON input file and emit a proposal to stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args(argv)
    try:
        with args.input.open(encoding='utf-8') as source:
            data = json.load(source)
        result = plan_multi_response(data)
        sys.stdout.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Do not echo arbitrary rejected values, file contents or private input payloads.
        parser.exit(2, f'invalid multi-response input ({type(exc).__name__})\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
