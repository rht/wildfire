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


def response_from_snapshot(snapshot, scenario, operations, voice, now, epoch):
    elapsed = (now-epoch).total_seconds()/60
    readiness_validity = operations.get('readiness_validity_min', 15)
    number(readiness_validity, 'readiness_validity_min')
    if readiness_validity <= 0:
        raise ValueError('readiness_validity_min must be positive')
    bound = operations.get('snapshot_id') == snapshot['snapshot_id']
    actions = deepcopy(operations.get('actions', [])) if bound else []
    ids = {a.asset_id for a in scenario.locations}
    actions = [a for a in actions if a['asset_id'] in ids and all(e['asset_id'] in ids for e in a['effects'])]
    present = {a['action_id'] for a in actions}
    # Remove dependants if an upstream location disappeared; never relax prerequisites.
    while any(set(a['requires'])-present for a in actions):
        actions = [a for a in actions if not set(a['requires'])-present]
        present = {a['action_id'] for a in actions}
    readiness = {}
    for row in voice.conn.execute('SELECT request_id FROM voice_calls ORDER BY request_id'):
        record = voice.get(row[0])
        if not record['provider_call_id'] or record['request']['asset_id'] not in ids:
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
        'assets': [{'asset_id': a.asset_id,'node_id': a.asset_id,'people': a.people,'assisted': a.assisted,
                     'value': a.value,'deadline_min': a.deadline_min if a.x_m is not None else None}
                for a in scenario.locations],
        'teams': teams, 'actions': actions, 'routes': [], 'readiness': list(readiness.values()),
        'committed': deepcopy(operations.get('committed', []))}
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
