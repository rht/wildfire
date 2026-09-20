"""Bridge assessed assets and explicit operational evidence into existing planners."""
from copy import deepcopy

import networkx as nx

from .grid import lonlat_to_xy
from .multi_response import plan_multi_response
from .priority_models import Location, StaticScenario, number
from .routing import RoadGraph
from .voice_models import utc


def _lonlat(point, label):
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError(f'{label} must be a longitude/latitude pair')
    lon, lat = point
    number(lon, f'{label} longitude', minimum=-180)
    number(lat, f'{label} latitude', minimum=-90)
    if lon > 180 or lat > 90:
        raise ValueError(f'{label} exceeds WGS84 bounds')
    return [lon, lat]


def _route_geometry(route, nodes):
    source, destination = route['from_node'], route['to_node']
    if source not in nodes or destination not in nodes:
        raise ValueError('graph route requires declared endpoint road nodes')
    path = route['path_lonlat']
    if not isinstance(path, (list, tuple)) or len(path) < 2:
        raise ValueError('path_lonlat must contain at least two WGS84 points')
    path = [_lonlat(point, 'path_lonlat') for point in path]
    start, finish = list(nodes[source]), list(nodes[destination])
    if path[0] == finish and path[-1] == start:
        path.reverse()
    if path[0] != start or path[-1] != finish:
        raise ValueError('path_lonlat endpoints must match declared road nodes')
    return path


def validate_current_location(value, *, now):
    """Validate a reported crew position, retaining its observation and source.

    None is unknown. A starting road node never supplies a current position, and
    an old observation remains labelled with its time rather than being refreshed.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('current_location must be an object or null')  # noqa: TRY004
    latitude, longitude = value.get('latitude'), value.get('longitude')
    number(latitude, 'current_location.latitude', minimum=-90)
    number(longitude, 'current_location.longitude', minimum=-180)
    if latitude > 90 or longitude > 180:
        raise ValueError('current_location coordinates exceed WGS84 bounds')
    observed = utc(value.get('observed_at'))
    if observed > now:
        raise ValueError('current_location observation is in the future')
    source = value.get('source')
    if not isinstance(source, str) or not source.strip():
        raise ValueError('current_location requires a source')
    return {'latitude': latitude, 'longitude': longitude,
                'observed_at': observed.isoformat(), 'source': source}


def scenario_from_snapshot(snapshot, operations, epoch):
    locations = []
    bound = operations.get('snapshot_id') == snapshot['snapshot_id']
    for asset in snapshot['assets']:
        located = asset['longitude'] is not None and asset['latitude'] is not None
        xy = lonlat_to_xy(asset['longitude'], asset['latitude']) if located else (None, None)
        arrival = max(0, (utc(asset['fire_arrival_at']) - epoch).total_seconds()/60) if asset['fire_arrival_at'] else None
        locations.append(Location(asset['asset_id'], asset['name'], *xy,
            asset['distance_to_fire_m'], asset['estimated_occupancy'],
            operations.get('assisted', {}).get(asset['asset_id']) if bound else None, asset['value_score'],
            arrival, arrival, asset['evacuation_min'], asset['forecast_source'], asset['evacuation_source']))
    return StaticScenario(snapshot['scenario_id'], tuple(locations), (), {},
                          horizon_min=operations.get('horizon_min', 720), buffer_min=30)


def _remaining_needs(snapshot, scenario, operations, allocation_view, now, epoch):
    """Reconcile physical arrivals, never intentions or departure alone.

    Group counts may overlap. Without trusted individual membership, the largest
    arrived group is the only safe lower bound; group.assisted is not a headcount.
    Member identifiers remain private and are never returned to the planner/UI.
    """
    arrivals = {}
    released = set()
    if allocation_view is not None:
        ledger, inputs = allocation_view
        context = inputs[0]
        if (context.scenario_id != snapshot['scenario_id']
                or context.snapshot_id != snapshot['snapshot_id']
                or context.incident_id != snapshot.get('incident_id')
                or utc(context.epoch) != epoch or utc(context.as_of) != now):
            raise ValueError('allocation context does not match planning snapshot')
        active = {row['allocation_id'] for row in ledger['locations']}
        for row in ledger['locations']:
            if row['state'] == 'arrived':
                arrivals.setdefault(row['asset_id'], []).append(row)
        # Released rows no longer expose their counts. Preserve the uncertainty.
        released = {event['asset_id'] for event in ledger['events']
                    if event['kind'] == 'arrived' and event['allocation_id'] not in active}
    membership = (operations.get('arrival_group_membership', {})
                  if operations.get('snapshot_id') == snapshot['snapshot_id'] else {})
    if not isinstance(membership, dict):
        raise ValueError('arrival_group_membership must be an object')  # noqa: TRY004
    # Explicit binding states that current occupancy still describes this old cohort.
    baselines = (operations.get('arrival_baseline_snapshot_ids', {})
                 if operations.get('snapshot_id') == snapshot['snapshot_id'] else {})
    if not isinstance(baselines, dict):
        raise ValueError('arrival_baseline_snapshot_ids must be an object')  # noqa: TRY004
    rows = []
    for asset in scenario.locations:
        # Reconciliation must not turn invalid source counts into valid zeroes.
        for field in ('people', 'assisted'):
            count = getattr(asset, field)
            number(count, field, nullable=True)
            if count is not None and type(count) is not int:
                raise ValueError(f'{field} must be an integer')
        if asset.people is not None and asset.assisted is not None and asset.assisted > asset.people:
            raise ValueError('assisted exceeds people')
        arrived = arrivals.get(asset.asset_id, [])
        reasons = []
        eligible = []
        for row in arrived:
            if row['safety'] != 'valid' or row['reasons']:
                reasons.append('arrived_destination_requires_review')
            if row['snapshot_id'] not in (snapshot['snapshot_id'], baselines.get(asset.asset_id)):
                reasons.append('arrival_baseline_review')
            else:
                eligible.append(row)
        arrived = eligible
        confirmed = max((row['people'] for row in arrived), default=0)
        known_members, known_assisted, known_unassisted = set(), set(), set()
        exact = bool(arrived)
        for row in arrived:
            record = membership.get(row['group_id'])
            valid = isinstance(record, dict)
            if valid:
                members, assisted = record.get('member_ids'), record.get('assisted_member_ids')
                source = record.get('source')
                valid = (isinstance(members, (list, tuple)) and isinstance(assisted, (list, tuple))
                         and all(isinstance(item, str) and item.strip() for item in [*members, *assisted])
                         and len(set(members)) == len(members) == row['people']
                         and len(set(assisted)) == len(assisted) and set(assisted) <= set(members)
                         and isinstance(source, str) and bool(source.strip()))
            if valid:
                known_members.update(members)
                known_assisted.update(assisted)
                known_unassisted.update(set(members) - set(assisted))
            else:
                exact = False
                if record is not None:
                    reasons.append('arrival_membership_invalid')
        if exact:
            confirmed = len(known_members)
        elif len(arrived) > 1:
            reasons.append('arrival_group_overlap_unknown')
        if asset.people is not None and confirmed > asset.people:
            # Counts no longer identify a consistent original-building cohort.
            reasons.append('arrival_occupancy_conflict')
            confirmed = 0
            exact = False
        remaining_people = None if asset.people is None else asset.people - confirmed
        remaining_assisted = asset.assisted
        assistance_conflict = bool(known_assisted & known_unassisted)
        if assistance_conflict:
            reasons.append('arrival_assistance_membership_conflict')
        if exact and not assistance_conflict and asset.assisted is not None:
            if len(known_assisted) > asset.assisted:
                reasons.append('arrival_assistance_count_conflict')
            else:
                remaining_assisted -= len(known_assisted)
        if confirmed and not exact and remaining_people != 0:
            reasons.extend(['arrival_membership_unknown', 'arrived_assisted_membership_unknown'])
        if remaining_people is not None:
            if remaining_people == 0:
                remaining_assisted = 0
            elif remaining_assisted is not None:
                remaining_assisted = min(remaining_assisted, remaining_people)
        if asset.asset_id in released:
            reasons.append('released_arrival_location_requires_review')
        rows.append({'asset_id': asset.asset_id, 'original_people': asset.people,
                     'remaining_people': remaining_people, 'original_assisted': asset.assisted,
                     'remaining_assisted': remaining_assisted, 'confirmed_arrived': confirmed,
                     'source': 'confirmed reception arrival ledger' if arrived else 'snapshot occupancy and operational assistance',
                     'review_reasons': sorted(set(reasons))})
    return rows


def _bind_evacuation_reservations(actions, allocation_view, snapshot):
    """Raw mission places cannot overrule the current authoritative reservation."""
    if allocation_view is None:
        return []
    ledger, inputs = allocation_view
    candidates = {candidate.centre.centre_id: candidate for candidate in inputs[1]}
    review = []
    for action in actions:
        mission = action.get('evacuation')
        if not isinstance(mission, dict):
            continue
        rows = [row for row in ledger['locations']
                if row['asset_id'] == action['asset_id']
                and row['destination_id'] == mission['destination_id']
                and row['snapshot_id'] == snapshot['snapshot_id']
                and row['state'] in ('reserved', 'communicated', 'departed')
                and row['safety'] == 'valid' and not row['reasons']]
        # Multiple group labels alone never establish disjoint reception occupants.
        places = max((row['people'] for row in rows), default=0)
        candidate = candidates.get(mission['destination_id'])
        if places < action['transport_people'] or candidate is None:
            mission.update(confirmed=False, safe=False, places_reserved=0)
            review.append({'asset_id': action['asset_id'], 'action_id': action['action_id'],
                           'reason': 'evacuation_reservation_unavailable'})
            continue
        mission['places_reserved'] = min(mission['places_reserved'], places)
        # A supplied wider window cannot extend an approved destination's lifetime.
        windows = [mission['available_until_min'], candidate.centre.available_until_min,
                   candidate.safe_until_min]
        if any(value is None for value in windows):
            mission.update(confirmed=False, safe=False)
            review.append({'asset_id': action['asset_id'], 'action_id': action['action_id'],
                           'reason': 'evacuation_reservation_window_unknown'})
        else:
            mission['available_until_min'] = min(windows)
    return review


def response_from_snapshot(snapshot, scenario, operations, voice, now, epoch, *, allocation_store=None):
    if voice.epoch != epoch:
        raise ValueError('voice evidence epoch does not match planning epoch')
    if scenario.scenario_id != snapshot['scenario_id']:
        raise ValueError('scenario does not match planning snapshot')
    allocation_view = allocation_store.view(as_of=now.isoformat()) if allocation_store is not None else None
    remaining_needs = _remaining_needs(snapshot, scenario, operations, allocation_view, now, epoch)
    needs = {row['asset_id']: row for row in remaining_needs}
    elapsed = (now-epoch).total_seconds()/60
    readiness_validity = operations.get('readiness_validity_min', 15)
    number(readiness_validity, 'readiness_validity_min')
    if readiness_validity <= 0:
        raise ValueError('readiness_validity_min must be positive')
    bound = operations.get('snapshot_id') == snapshot['snapshot_id']
    actions = deepcopy(operations.get('actions', [])) if bound else []
    ids = {a.asset_id for a in scenario.locations}
    actions = [a for a in actions if a['asset_id'] in ids and all(e['asset_id'] in ids for e in a['effects'])]
    population_review = []
    retained = []
    for action in actions:
        need = needs[action['asset_id']]
        remaining = need['remaining_people']
        if (need['confirmed_arrived'] and remaining is not None
                and action['transport_people'] > remaining):
            # The original mission's passenger cohort must be reviewed, not guessed.
            reason = ('transport_no_remaining_people' if remaining == 0
                      else 'resize_evacuation_mission_required')
            population_review.append({'asset_id': action['asset_id'], 'action_id': action['action_id'],
                                      'reason': reason, 'human_decision_required': remaining != 0,
                                      'requested_transport_people': action['transport_people'],
                                      'remaining_people': remaining})
        else:
            retained.append(action)
    actions = retained
    present = {a['action_id'] for a in actions}
    # A removed prerequisite never silently becomes completed work.
    while any(set(a['requires']) - present for a in actions):
        retained = []
        for action in actions:
            if set(action['requires']) - present:
                population_review.append({'asset_id': action['asset_id'], 'action_id': action['action_id'],
                                          'reason': 'prerequisite_requires_replanning',
                                          'human_decision_required': True})
            else:
                retained.append(action)
        actions = retained
        present = {a['action_id'] for a in actions}
    reservation_review = _bind_evacuation_reservations(actions, allocation_view, snapshot)
    readiness = {}
    for row in voice.conn.execute('SELECT request_id FROM voice_calls ORDER BY request_id'):
        record = voice.get(row[0])
        if (not record['provider_call_id'] or record['request']['asset_id'] not in ids
                or record['request']['snapshot_id'] != snapshot['snapshot_id']):
            continue
        assessment = voice.assessment(row[0])
        aid = assessment.asset_id
        status = 'no_answer' if assessment.status == 'no_answer' else 'unknown'
        if (assessment.identity_confirmed is True and assessment.evidence and
                (assessment.can_self_evacuate is False or assessment.transport_available is False)):
            status = 'assistance_required'
        item = {'asset_id': aid,'status': status,'observed_min': assessment.observed_min,
                    'valid_until_min': assessment.observed_min+readiness_validity,
                    'source': 'stored call assessment; operational freshness policy','request_id': row[0]}
        if aid not in readiness or item['observed_min'] > readiness[aid]['observed_min']:
            readiness[aid] = item
    teams = deepcopy(operations.get('teams', []))
    positions = {team['team_id']: validate_current_location(team.get('current_location'), now=now)
                 for team in teams}
    data = {'schema_version': 'multi-response-input-1', 'scenario_id': snapshot['scenario_id'],
        'snapshot_id': snapshot['snapshot_id'], 'now_min': elapsed,
        'horizon_min': max(elapsed, scenario.horizon_min),'buffer_min': 30,
        'assets': [{'asset_id': a.asset_id,'node_id': a.asset_id,'people': needs[a.asset_id]['remaining_people'],
                     'assisted': needs[a.asset_id]['remaining_assisted'],
                     'value': a.value,'deadline_min': a.deadline_min if a.x_m is not None else None}
                for a in scenario.locations],
        'teams': teams, 'actions': actions, 'routes': [], 'readiness': list(readiness.values()),
        'committed': deepcopy(operations.get('committed', []))}
    if 'search' in operations:
        data['search'] = deepcopy(operations['search'])
    supplied_assets = {asset['asset_id']: asset for asset in snapshot['assets']}
    for asset in data['assets']:
        early = supplied_assets[asset['asset_id']].get('arrival_p10_at')
        if early is not None and asset['deadline_min'] is not None:
            asset['deadline_early_min'] = max(0, (utc(early) - epoch).total_seconds() / 60)
    graph = RoadGraph(nx.DiGraph())
    if bound:
        nodes = operations.get('road_nodes', {})
        for node, point in nodes.items():
            if not isinstance(node, str) or not node.strip():
                raise ValueError('road node ID must be a nonempty string')
            graph.add_node(node, *_lonlat(point, 'road node'))
        route_pairs = set()
        for route in operations.get('routes', []):
            endpoints = (route.get('from_node'), route.get('to_node'))
            if any(not isinstance(node, str) or not node.strip() for node in endpoints):
                raise ValueError('route endpoints must be nonempty road node IDs')
            if endpoints in route_pairs:
                raise ValueError('duplicate directed route')
            route_pairs.add(endpoints)
            if 'path_lonlat' in route:
                path = _route_geometry(route, nodes)
                graph.g.add_edge(route['from_node'],route['to_node'],travel_min=route['minutes'],
                    cut_min=route['available_until_min'],confirmed=route['confirmed'],safe=route['safe'],
                    source=route['source'],closed=False,geometry_lonlat=path)
            else:
                data['routes'].append(dict(route))
    if data['routes'] and graph.g.number_of_edges():
        raise ValueError('supply either graph routes with geometry or explicit route legs')
    plan = plan_multi_response(data, graph=graph if not data['routes'] else None)
    plan['review'].extend(population_review)
    plan['review'].extend(reservation_review)
    plan['remaining_needs'] = remaining_needs
    for row in remaining_needs:
        plan['review'].extend({'asset_id': row['asset_id'], 'reason': reason}
                              for reason in row['review_reasons'])
    for team in plan['teams']:
        team['current_location'] = positions.get(team['team_id'])
        context = team.get('planning_context')
        if bound and context:
            start = context['start']
            supplied = operations.get('road_nodes', {}).get(start['node_id'])
            if supplied is not None:
                lon, lat = _lonlat(supplied, 'supplied operational start node')
                start.update(longitude=lon, latitude=lat, source='supplied operational start node')
    covered = {a['asset_id'] for a in actions}
    for aid in sorted(ids-covered):
        plan['review'].append({'asset_id': aid,'reason': 'operational_inputs_missing_or_stale'})
    return plan
