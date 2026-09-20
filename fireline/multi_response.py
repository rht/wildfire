"""Offline multi-team proposal heuristic; JSON contract and limits are in readme.md.

No provider access, crew dispatch, inferred roads or spread effects. Existing exact
one-crew planning is independent. Every elapsed minute uses the caller's scenario epoch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from itertools import combinations
from pathlib import Path

from .priority_models import number, string_sequence
from .response_missions import journey, validate_evacuation
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
    search = data.get('search', {})
    if not isinstance(search, dict) or set(search) - {'depth', 'beam_width', 'max_expansions'}:
        raise ValueError('invalid search configuration')
    for key, limit in (('depth', 5), ('beam_width', 32), ('max_expansions', 2048)):
        if key in search:
            _integer(search[key], key)
            if not 1 <= search[key] <= limit:
                raise ValueError('search limit out of range')
    assets = _indexed(data['assets'], 'asset_id')
    teams = _indexed(data['teams'], 'team_id')
    actions = _indexed(data['actions'], 'action_id')
    readiness = _indexed(data.get('readiness', []), 'asset_id')
    for asset in assets.values():
        _text(asset['node_id'], 'node_id')
        if 'deadline_early_min' in asset:
            number(asset['deadline_early_min'], 'deadline_early_min', nullable=True)
            if (asset['deadline_early_min'] is not None and asset['deadline_min'] is not None
                    and asset['deadline_early_min'] > asset['deadline_min']):
                raise ValueError('early deadline exceeds nominal deadline')
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
        count = action.get('required_team_count', 1)
        _integer(count, 'required_team_count')
        if count < 1:
            raise ValueError('required_team_count must be positive')
        if action.get('duration_high_min') is not None:
            number(action['duration_high_min'], 'duration_high_min')
            if action['duration_high_min'] < action['duration_min']:
                raise ValueError('high duration is less than nominal duration')
        if 'evacuation' in action:
            validate_evacuation(action['evacuation'], number, _integer, _boolean, _text)
            if action['transport_people'] <= 0:
                raise ValueError('evacuation requires transport_people')
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
    fields.update({key: action[key] for key in ('evacuation', 'required_team_count',
                  'duration_high_min') if key in action})
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


class _Planner:
    def __init__(self, data, graph):
        self.data = data
        self.assets, self.teams, self.actions, self.readiness = _validate(data)
        self.routes = _Routes(data, graph)
        self.coverage = dict.fromkeys(self.assets, 0.0)
        self.dimension_coverage = {key: dict.fromkeys(DIMENSIONS, 0.0) for key in self.assets}
        self.search = dict(depth=3, beam_width=8, max_expansions=256)
        self.search.update(data.get('search', {}))
        self.done = {}
        self.stress_done = {}
        self.stress_unknown = set()
        self.stress_failed = set()
        self.transport_reserved = dict.fromkeys(self.assets, 0)
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
        if targets and all(all(a[key] is None for key in DIMENSIONS) for a in targets):
            reasons.append('unknown_needs')
        if any(a['deadline_min'] is None for a in targets):
            reasons.append('unknown_deadline')
        if any(not e['confirmed'] for e in action['effects']):
            reasons.append('unconfirmed_effect')
        return reasons

    def candidate(self, action, team_id):
        rows, reasons = self.group_candidate(action, (team_id,))
        return (rows[0] if rows else None), reasons

    def group_candidate(self, action, team_ids, *, attempts=None):
        reasons = self._need_reasons(action)
        people = self.assets[action['asset_id']]['people']
        if people is not None and action['transport_people'] > people:
            reasons.append('transport_people_exceeds_people')
        already = self.transport_reserved[action['asset_id']]
        if action['transport_people'] and already:
            reasons.append('transport_people_exceeds_remaining'
                           if people is not None and already + action['transport_people'] > people
                           else 'transport_population_overlap_review')
        if len(team_ids) != action.get('required_team_count', 1):
            reasons.append('required_teams_unavailable')
        for key in team_ids:
            state, team = self.states[key], self.teams[key]
            if state['locked']:
                reasons.append('committed_team')
            if not team['available']:
                reasons.append('team_unavailable')
            if not set(action['capabilities']) <= set(team['capabilities']):
                reasons.append('capabilities')
        if not set(action['requires']) <= self.done.keys():
            reasons.append('prerequisites')
        if 'evacuation' not in action and action['transport_people'] > sum(
                self.states[key]['capacity'] for key in team_ids):
            reasons.append('transport_capacity')
        if reasons:
            return None, sorted(set(reasons))
        target = self.assets[action['asset_id']]
        depart = max([self.states[key]['time'] for key in team_ids] +
                     [self.done[key] for key in action['requires']] + [0])
        arrivals = []
        for key in team_ids:
            state = self.states[key]
            leg = self.routes.leg(state['node'], target['node_id'], depart)
            if leg is None:
                return None, ['route_unavailable']
            arrivals.append((key, leg))
        start = max(depart + leg['minutes'] for _, leg in arrivals)
        finish = start + action['duration_min']
        deadline = min(self.data['horizon_min'], target['deadline_min'], action['deadline_min'],
                       *(self.teams[key]['available_until_min'] for key in team_ids))
        outcome = self.readiness.get(action['asset_id'])
        if action['readiness_required'] or (outcome and action['transport_people']):
            if (outcome is None or outcome['status'] != 'assistance_required'
                    or outcome['observed_min'] > self.data['now_min']
                    or start >= outcome['valid_until_min']):
                return None, ['readiness_review']
        unknown = sorted({key for e in action['effects'] for key in DIMENSIONS
                          if self.assets[e['asset_id']][key] is None})
        rows = []
        remaining = action['transport_people']
        for key, leg in arrivals:
            count = min(remaining, self.states[key]['capacity'])
            remaining -= count
            # Synchronize arrival by waiting at the known departure node.
            actual_depart = start - leg['minutes']
            synchronized_leg = self.routes.leg(self.states[key]['node'], target['node_id'], actual_depart)
            if synchronized_leg is None:
                return None, ['route_unavailable']
            if synchronized_leg != leg:
                return None, ['synchronized_route_changed']
            row = dict(action_id=action['action_id'], asset_id=action['asset_id'], team_id=key,
                       scenario_id=self.data['scenario_id'], snapshot_id=self.data['snapshot_id'],
                       action_version=_action_version(action), status='proposed',
                       from_node=self.states[key]['node'], to_node=target['node_id'],
                       depart_min=actual_depart, travel_min=leg['minutes'], start_min=start,
                       finish_min=finish, prerequisites=sorted(action['requires']),
                       effects=_public_effects(action), transport_people=count,
                       route_source=leg['source'], duration_min=action['duration_min'],
                       deadline_min=deadline, required_capabilities=sorted(action['capabilities']),
                       readiness_required=action['readiness_required'], route_status='qualified',
                       unknown_dimensions=unknown)
            if len(team_ids) > 1:
                row.update(team_ids=list(team_ids), required_team_count=len(team_ids),
                           benefit_owner_team_id=team_ids[0], mission_id=action['action_id'])
            if action['transport_people']:
                row['mission_status'] = 'pickup_only'
            if isinstance(action.get('action'), str) and action['action'].strip():
                row['action'] = action['action']
            if outcome:
                row['readiness'] = {k: outcome[k] for k in ('asset_id', 'status', 'observed_min',
                                    'valid_until_min', 'source', 'request_id')}
            row.update({k: leg[k] for k in ('path_lonlat', 'path_nodes') if k in leg})
            rows.append(row)
        if attempts is not None:
            attempts.extend(dict(team_id=r['team_id'], start_min=start, finish_min=finish,
                                 route_status='qualified') for r in rows)
        approach_rows = deepcopy(rows)
        if 'evacuation' in action:
            rows, reasons = journey(action, rows, self.states, self.teams, self.routes,
                                   action['duration_min'], self.data['horizon_min'], self.data['buffer_min'])
            if rows is None:
                return None, reasons
            finish = rows[0]['finish_min']
        if after_deadline(finish + self.data['buffer_min'], deadline):
            return None, ['deadline']
        sensitivity = self.sensitivity(action, approach_rows, rows, target, deadline)
        for row in rows:
            row['sensitivity'] = sensitivity
        if not any(self.priority(action, rows[0])[:3]):
            return None, ['no_incremental_benefit']
        return rows, []

    def sensitivity(self, action, approaches, rows, target, deadline):
        high, early = action.get('duration_high_min'), target.get('deadline_early_min')
        info = dict(status='unknown', duration_high_min=high, deadline_early_min=early,
                    reasons=[])
        if (any(self.states[r['team_id']].get('stress_failed') for r in rows)
                or self.stress_failed.intersection(action['requires'])):
            info.update(status='fragile', reasons=['upstream_stress_infeasible'])
            return info
        if (high is None
                or any(self.states[r['team_id']].get('stress_unknown') for r in rows)
                or self.stress_unknown.intersection(action['requires'])):
            info['reasons'] = ['uncertainty_bounds_missing']
            return info
        stress_rows = deepcopy(approaches)
        # Propagate supplied high-duration delay through earlier work on each crew.
        delay = max([self.states[r['team_id']].get('stress_delay', 0) for r in rows] +
                    [max(0, self.stress_done.get(key, self.done[key]) - self.done[key])
                     for key in action['requires']])
        for row in stress_rows:
            row['depart_min'] += delay
            row['start_min'] += delay
        stress_finish = stress_rows[0]['start_min'] + high
        reasons = []
        for row in stress_rows:
            stressed_leg = self.routes.leg(row['from_node'], row['to_node'], row['depart_min'])
            if stressed_leg is None:
                reasons.append('stress_route_unavailable')
            elif (stressed_leg['minutes'] != row['travel_min']
                  or stressed_leg.get('path_nodes') != row.get('path_nodes')
                  or stressed_leg['source'] != row['route_source']):
                reasons.append('stress_route_changed')
            if row.get('readiness') and row['start_min'] >= row['readiness']['valid_until_min']:
                reasons.append('stress_readiness_expired')
        if 'evacuation' in action:
            journeys, blocked = journey(action, stress_rows, self.states, self.teams, self.routes,
                                       high, self.data['horizon_min'], self.data['buffer_min'])
            reasons.extend(blocked)
            if journeys:
                stress_finish = journeys[0]['finish_min']
        stress_deadline = min(deadline, early) if early is not None else deadline
        if after_deadline(stress_finish + self.data['buffer_min'], stress_deadline):
            reasons.append('stress_deadline')
        if early is None:
            reasons.append('uncertainty_bounds_missing')
        info.update(status='fragile' if set(reasons) - {'uncertainty_bounds_missing'}
                    else 'unknown' if early is None else 'robust', stress_finish_min=stress_finish,
                    stress_deadline_min=stress_deadline, reasons=sorted(set(reasons)))
        return info

    def _effect_coverage(self, row, effect, dimension):
        amount = effect['coverage']
        if (row.get('mission_status') == 'complete_evacuation' and dimension != 'value'
                and effect['asset_id'] == row['asset_id']):
            people = self.assets[effect['asset_id']]['people']
            # Delivery count is exact; assistance membership is not inferable for partial loads.
            delivered = row['mission_delivered_people']
            if people is None or people <= 0:
                return 0
            amount = min(amount, delivered / people)
            if dimension == 'assisted' and delivered < people:
                amount = 0
        return amount

    def gain(self, action, finish=0, row=None):
        gains = [0.0, 0.0, 0.0]
        for effect in action['effects']:
            asset = self.assets[effect['asset_id']]
            if (not effect['confirmed'] or asset['deadline_min'] is None
                    or after_deadline(finish + self.data['buffer_min'], asset['deadline_min'])):
                continue
            for i, key in enumerate(DIMENSIONS):
                if asset[key] is not None:
                    coverage = self._effect_coverage(row, effect, key) if row else effect['coverage']
                    gains[i] += max(0, coverage - self.dimension_coverage[effect['asset_id']][key]) * asset[key]
        return tuple(gains)

    def priority(self, action, row):
        """Prerequisite hints preserve productive chains in a bounded frontier."""
        best = self.gain(action, row['finish_min'], row)
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
        return (*(-x for x in best), row['sensitivity']['status'] == 'fragile',
                row['finish_min'], row['action_id'], row['team_id'])

    def credit(self, row):
        finish = row.get('actual_finish_min', row['finish_min'])
        gained = {}
        for effect in row['effects']:
            asset = self.assets[effect['asset_id']]
            if (not effect['confirmed'] or asset['deadline_min'] is None
                    or after_deadline(finish + self.data['buffer_min'], asset['deadline_min'])):
                continue
            aid = effect['asset_id']
            old = self.coverage[aid]
            for key in DIMENSIONS:
                if asset[key] is not None:
                    self.dimension_coverage[aid][key] = max(self.dimension_coverage[aid][key],
                                                           self._effect_coverage(row, effect, key))
            self.coverage[aid] = max(self.dimension_coverage[aid].values())
            if self.coverage[aid] > old:
                gained[aid] = self.coverage[aid] - old
        row['coverage_gained'] = gained

    def candidates(self):
        result = []
        for key, action in self.actions.items():
            if key in self.reserved:
                continue
            # Bound combinatorial joint-team matching conservatively and deterministically.
            for index, ids in enumerate(combinations(self.teams, action.get('required_team_count', 1))):
                if index >= 128:
                    break
                rows, _ = self.group_candidate(action, ids)
                if rows:
                    result.append((self.priority(action, rows[0]), rows))
        return sorted(result, key=lambda item: item[0])

    def apply(self, rows, *, record=False):
        first = rows[0]
        self.reserved.add(first['action_id'])
        self.transport_reserved[first['asset_id']] += self.actions[first['action_id']]['transport_people']
        if first['sensitivity']['status'] == 'fragile':
            self.stress_failed.add(first['action_id'])
        self.done[first['action_id']] = first['finish_min']
        stress_finish = first.get('sensitivity', {}).get('stress_finish_min')
        if stress_finish is None:
            self.stress_unknown.add(first['action_id'])
        else:
            self.stress_done[first['action_id']] = stress_finish
        self.credit(first)
        for index, row in enumerate(rows):
            if index:
                row['coverage_gained'] = {}
            state = self.states[row['team_id']]
            state.update(node=row['to_node'], time=row['finish_min'],
                         capacity=state['capacity'] if row.get('mission_status') == 'complete_evacuation'
                         else state['capacity'] - row['transport_people'])
            if row['sensitivity']['status'] == 'fragile':
                state['stress_failed'] = True
            if self.actions[row['action_id']].get('duration_high_min') is None:
                state['stress_unknown'] = True
            stress_finish = row.get('sensitivity', {}).get('stress_finish_min')
            if stress_finish is not None:
                state['stress_delay'] = max(0, stress_finish - row['finish_min'])
            if record:
                state['tasks'].append(row)
        if record:
            reasons = ['unknown_' + key for key in first['unknown_dimensions']]
            if first.get('mission_status') == 'pickup_only':
                reasons.append('evacuation_details_missing')
            if first['sensitivity']['status'] == 'fragile':
                reasons.append('fragile_plan')
            if reasons:
                self.review.append(dict(action_id=first['action_id'], asset_id=first['asset_id'],
                    team_id=first['team_id'], reason='intervention_review', reasons=reasons,
                    human_decision_required=True, unknown_dimensions=first['unknown_dimensions']))

    def objective(self):
        return tuple(sum(self.dimension_coverage[aid][key] * asset[key]
                         for aid, asset in self.assets.items() if asset[key] is not None)
                     for key in DIMENSIONS)

    def checkpoint(self):
        return deepcopy((self.states, self.coverage, self.dimension_coverage, self.done, self.reserved,
                         self.stress_done, self.stress_unknown, self.stress_failed, self.transport_reserved))

    def restore(self, checkpoint):
        (self.states, self.coverage, self.dimension_coverage, self.done, self.reserved,
         self.stress_done, self.stress_unknown, self.stress_failed, self.transport_reserved) = deepcopy(checkpoint)

    def choose(self, candidates):
        """Bounded sequence comparison, with no optimality claim or assumed future work."""
        root = self.checkpoint()
        # Each frontier entry carries a concrete first choice and its realized objective.
        frontier = [(root, None, 0, ())]
        best = None
        expansions = 0
        for _ in range(self.search['depth']):
            next_frontier = []
            for state, first, fragile, trace in frontier:
                self.restore(state)
                options = candidates if first is None else self.candidates()
                for priority, rows in options[:self.search['beam_width']]:
                    if expansions >= self.search['max_expansions']:
                        break
                    self.restore(state)
                    self.apply(deepcopy(rows))
                    expansions += 1
                    choice = (priority, rows) if first is None else first
                    risk = fragile + (rows[0]['sensitivity']['status'] == 'fragile')
                    sequence = trace + (priority,)
                    score = (*(-v for v in self.objective()[:2]), risk,
                             -self.objective()[2], sequence)
                    entry = (self.checkpoint(), choice, risk, sequence)
                    if best is None or score < best[0]:
                        best = (score, choice)
                    next_frontier.append((score, entry))
            if not next_frontier or expansions >= self.search['max_expansions']:
                break
            frontier = [entry for _, entry in sorted(next_frontier, key=lambda x: x[0])[:self.search['beam_width']]]
        self.restore(root)
        return best[1] if best else candidates[0]

    def load_commitments(self):
        commitments = self.data.get('committed', [])
        seen = {}
        for original in commitments:
            key = original['action_id']
            siblings = seen.setdefault(key, [])
            if siblings:
                common = ('asset_id', 'scenario_id', 'snapshot_id', 'action_version', 'status',
                          'start_min', 'team_ids', 'required_team_count', 'benefit_owner_team_id', 'effects')
                if (original.get('required_team_count', 1) <= 1
                        or original['team_id'] in [r['team_id'] for r in siblings]
                        or any(original.get(k) != siblings[0].get(k) for k in common)):
                    raise ValueError('inconsistent duplicate committed action')
            siblings.append(original)
        rows = sorted(commitments, key=lambda r: (r['depart_min'], r['action_id'], r['team_id']))
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
            if row['asset_id'] in self.transport_reserved:
                self.transport_reserved[row['asset_id']] += row['transport_people']
            action = self.actions.get(row['action_id'])
            advanced = (original.get('mission_status') == 'complete_evacuation'
                        or original.get('required_team_count', 1) > 1
                        or (action is not None and ('evacuation' in action
                            or action.get('required_team_count', 1) > 1)))
            if advanced:
                # Active or completed mission ledgers require arrival reconciliation by the
                # incident adapter. Preserve all reservations without re-crediting delivery.
                for name in ('mission_status', 'mission_legs', 'delivered_people', 'mission_delivered_people',
                             'evacuation', 'trip_count', 'team_ids', 'required_team_count',
                             'benefit_owner_team_id', 'mission_id', 'sensitivity', 'unknown_dimensions'):
                    if name in original:
                        row[name] = deepcopy(original[name])
                members = original.get('team_ids', [row['team_id']])
                string_sequence(members, 'committed team_ids')
                if row['team_id'] not in members or len(set(members)) != len(members):
                    raise ValueError('invalid committed team membership')
                if row['status'] == 'completed':
                    number(original.get('actual_finish_min'), 'actual_finish_min')
                    if original['actual_finish_min'] > self.data['now_min']:
                        raise ValueError('actual_finish_min must not exceed now_min')
                    row['actual_finish_min'] = original['actual_finish_min']
                for member in members:
                    if member not in self.states:
                        self.states[member] = dict(node=row['to_node'], time=self.data['now_min'],
                                                  capacity=0, tasks=[], locked=True)
                    self.states[member]['locked'] = True
                row['coverage_gained'] = {}
                self.states[row['team_id']]['tasks'].append(row)
                self.review.append(dict(action_id=row['action_id'], asset_id=row['asset_id'],
                    team_id=row['team_id'], reason='committed_review',
                    reasons=['advanced_commitment_review'], human_decision_required=True))
                continue
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
                if actual_finish > self.data['now_min']:
                    raise ValueError('actual_finish_min must not exceed now_min')
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
        method = 'bounded deterministic prerequisite-aware sequence lookahead'
        while True:
            candidates = self.candidates()
            if not candidates:
                break
            _priority, rows = self.choose(candidates)
            gain = self.gain(self.actions[rows[0]['action_id']], rows[0]['finish_min'], rows[0])
            for row in rows:
                row['ordering_evidence'] = dict(method=method,
                    assisted_gain=gain[0], people_gain=gain[1], value_gain=gain[2],
                    candidate_count=len(candidates), selection_rank=len(self.reserved) + 1,
                    tie_break='supplied stress robustness, prerequisite benefit, earliest finish, action ID, team ID',
                    reason='Best realized lexicographic benefit within bounded candidate sequences',
                    search_limits=self.search)
            self.apply(rows, record=True)
        unassigned = []
        for key, action in self.actions.items():
            if key in self.reserved:
                continue
            reasons, attempts = set(), []
            groups = combinations(self.teams, action.get('required_team_count', 1))
            for index, ids in enumerate(groups):
                if index >= 128:
                    reasons.add('joint_candidate_limit')
                    break
                _, blocked = self.group_candidate(action, ids, attempts=attempts)
                reasons.update(blocked)
            if action.get('required_team_count', 1) > 1:
                reasons.add('required_teams_unavailable')
            unassigned.append(dict(action_id=key, asset_id=action['asset_id'],
                reason='urgent_intervention_review', human_decision_required=True,
                reasons=sorted(reasons or {'no_teams'}), candidate_attempts=attempts))
        objective = dict(zip((key + '_units' for key in DIMENSIONS), self.objective()))
        return dict(schema_version='multi-response-plan-1', scenario_id=self.data['scenario_id'],
                    snapshot_id=self.data['snapshot_id'], now_min=self.data['now_min'],
                    optimal=False, dispatch=False, method=method, search_limits=self.search,
                    teams=[dict(team_id=key, tasks=state['tasks'], locked=state['locked'],
                                remaining_transport_capacity=state['capacity'],
                                planning_context=self.review_context(key))
                           for key, state in sorted(self.states.items())],
                    coverage=self.coverage, objective=objective,
                    coverage_by_dimension=self.dimension_coverage,
                    unassigned=unassigned, review=self.review + unassigned)

    def review_context(self, team_id):
        """Freeze public start/route evidence for conservative permutation validation."""
        team = self.teams.get(team_id)
        if team is None:
            return None
        start = dict(node_id=team['start_node_id'],
                     available_min=max(self.data['now_min'], team['available_from_min']))
        graph = self.routes.graph
        if graph is not None and team['start_node_id'] in graph.g:
            try:
                lon, lat = graph.node_lonlat(team['start_node_id'])
                start.update(longitude=lon, latitude=lat, source='supplied planning road node')
            except KeyError:
                pass
        routes = []
        nodes = sorted({team['start_node_id'], *[task['to_node']
                       for task in self.states[team_id]['tasks']]})
        if graph is None:
            for route in sorted(self.data['routes'], key=lambda r: (r['from_node'], r['to_node'])):
                if route['from_node'] not in nodes or route['to_node'] not in nodes:
                    continue
                row = dict(from_node=route['from_node'], to_node=route['to_node'],
                           travel_min=route['minutes'], route_source=route['source'],
                           route_status='qualified' if route['safe'] and route['confirmed'] else 'unqualified',
                           safe=route['safe'], confirmed=route['confirmed'],
                           available_until_min=route['available_until_min'])
                routes.append(row)
        else:
            for source in nodes:
                for target in nodes:
                    if source == target:
                        continue
                    leg = self.routes.leg(source, target, start['available_min'])
                    if leg is None:
                        continue
                    path = leg['path_nodes']
                    # Minimum edge closure is conservative for every later departure.
                    row = dict(from_node=source, to_node=target, travel_min=leg['minutes'],
                               route_source=leg['source'], route_status='qualified',
                               safe=True, confirmed=True, available_until_min=min(
                                   graph.g[u][v]['cut_min'] for u, v in zip(path, path[1:])))
                    for key in ('path_nodes', 'path_lonlat'):
                        if key in leg:
                            row[key] = leg[key]
                    routes.append(row)
        # Zero travel only when the original planner also uses the same supplied node.
        for node in nodes:
            if not any(r['from_node'] == node and r['to_node'] == node for r in routes):
                routes.append(dict(from_node=node, to_node=node, travel_min=0,
                                   route_source='same supplied node', route_status='qualified',
                                   safe=True, confirmed=True,
                                   available_until_min=self.data['horizon_min']))
        return dict(start=start, routes=routes, capabilities=sorted(team['capabilities']),
                    available=team['available'], transport_capacity=team['transport_capacity'],
                    now_min=self.data['now_min'], buffer_min=self.data['buffer_min'],
                    horizon_min=self.data['horizon_min'], available_until_min=team['available_until_min'],
                    prerequisites=[dict(action_id=row['action_id'], status='completed',
                                        finish_min=row['actual_finish_min'])
                                   for state in self.states.values() for row in state['tasks']
                                   if row['status'] == 'completed' and 'actual_finish_min' in row])


def plan_multi_response(data, *, graph=None):
    """Return JSON-safe proposed per-team tasks; never mutate inputs or dispatch crews.

    ``data`` follows multi-response-input-1 in readme.md. Supply either directed
    ``routes`` or a qualified RoadGraph, never both. Unknown route safety is unusable.
    """
    result = _Planner(data, graph).run()
    # Finite inputs can still overflow a summed objective; never publish invalid JSON.
    json.dumps(result, allow_nan=False)
    return result


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
