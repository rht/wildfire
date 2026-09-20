"""Deterministic evidence-only transport journeys; no route or reception inference."""
from copy import deepcopy


def validate_evacuation(evidence, number, integer, boolean, text):
    if not isinstance(evidence, dict):
        raise ValueError('evacuation must be an object')  # noqa: TRY004 — planner input errors use ValueError
    for key in ('destination_id', 'node_id', 'source'):
        text(evidence[key], key)
    for key in ('unload_min', 'available_until_min'):
        number(evidence[key], key)
    integer(evidence['places_reserved'], 'places_reserved')
    for key in ('confirmed', 'safe'):
        boolean(evidence[key], key)


def journey(action, rows, states, teams, routes, duration, horizon, buffer):
    """Synchronize each pickup round; reserve reception once across participating crews."""
    destination = action['evacuation']
    if not destination['confirmed'] or not destination['safe']:
        return None, ['evacuation_destination_unconfirmed']
    if destination['places_reserved'] < action['transport_people']:
        return None, ['evacuation_places']
    capacities = {r['team_id']: states[r['team_id']]['capacity'] for r in rows}
    if sum(capacities.values()) <= 0:
        return None, ['transport_capacity']
    # An existing occupied seat has no supplied discharge evidence in this mission.
    if any(capacities[r['team_id']] != teams[r['team_id']]['transport_capacity'] for r in rows):
        return None, ['occupied_transport_review']
    remaining = action['transport_people']
    journeys = {r['team_id']: [dict(kind='approach', from_node=r['from_node'],
        to_node=r['to_node'], depart_min=r['depart_min'], arrive_min=r['start_min'],
        finish_min=r['start_min'], people=0, route_source=r['route_source'],
        **{k: r[k] for k in ('path_nodes', 'path_lonlat') if k in r})] for r in rows}
    site = rows[0]['to_node']
    start = rows[0]['start_min']
    delivered = dict.fromkeys(capacities, 0)
    rounds = 0
    while remaining:
        rounds += 1
        if rounds > 64:
            return None, ['evacuation_trip_limit']
        if any(r.get('readiness') and start >= r['readiness']['valid_until_min'] for r in rows):
            return None, ['readiness_review']
        finish_round = start
        for row in rows:
            key = row['team_id']
            count = min(remaining, capacities[key])
            # All crews travel together even in a final partially loaded round.
            depart = start + duration
            leg = routes.leg(site, destination['node_id'], depart)
            if leg is None:
                return None, ['evacuation_route_unavailable']
            arrival = depart + leg['minutes']
            finish = arrival + destination['unload_min']
            if finish > destination['available_until_min']:
                return None, ['evacuation_destination_window']
            if finish + buffer > min(horizon, teams[key]['available_until_min']):
                return None, ['deadline']
            journeys[key].append(dict(kind='delivery', from_node=site,
                to_node=destination['node_id'], depart_min=depart, arrive_min=arrival,
                finish_min=finish, people=count, route_source=leg['source'],
                **{k: leg[k] for k in ('path_nodes', 'path_lonlat') if k in leg}))
            finish_round = max(finish_round, finish)
            delivered[key] += count
            remaining -= count
        if remaining:
            arrivals = []
            for row in rows:
                key = row['team_id']
                leg = routes.leg(destination['node_id'], site, finish_round)
                if leg is None:
                    return None, ['evacuation_route_unavailable']
                arrival = finish_round + leg['minutes']
                journeys[key].append(dict(kind='return', from_node=destination['node_id'],
                    to_node=site, depart_min=finish_round, arrive_min=arrival,
                    finish_min=arrival, people=0, route_source=leg['source'],
                    **{k: leg[k] for k in ('path_nodes', 'path_lonlat') if k in leg}))
                arrivals.append(arrival)
            start = max(arrivals)
    result = deepcopy(rows)
    for row in result:
        key = row['team_id']
        row.update(mission_status='complete_evacuation', mission_legs=journeys[key],
            delivered_people=delivered[key], transport_people=delivered[key], mission_delivered_people=action['transport_people'],
            evacuation=deepcopy(destination), to_node=destination['node_id'], finish_min=finish_round,
            trip_count=rounds)
    return result, []
