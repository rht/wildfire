"""Additive adapters for static readiness, discovery and approved call briefings."""
from collections import defaultdict

from .contact_priority import ContactPolicy
from .evacuation_plans import candidate_from_record, evaluate_candidates
from .evacuation_readiness import coordinate_evacuation


def candidates_from_discovery(discovery):
    """Consume discovery-1 without promoting raw facilities to approved reception."""
    if discovery.get('schema_version') != 'discovery-1':
        raise ValueError('expected discovery-1')
    classes = discovery['classifications']
    return [candidate_from_record(record) for record in discovery['assets_in']
            if classes.get(record['asset_id']) == 'destination_candidate']


def coordinate_approved_evacuation(scenario, assessments, store, *, as_of,
                                   readiness_policy=None, road_warnings=()):
    """Overlay durable facts on existing readiness; never reserve or dispatch.

    Candidates are review proposals. Recorded destinations remain visible even when
    instructions are withheld. A household's call assessment gates readiness, while
    ledger facts separately gate allocation validity and communication permission.
    """
    plan, current = store.view(as_of=as_of)
    context, candidates, groups, routes, roads = current
    if scenario.scenario_id != context.scenario_id:
        raise ValueError('scenario mismatch')
    result = coordinate_evacuation(scenario, assessments, [], [],
                                  contact_policy=ContactPolicy(context.now_min, context.buffer_min),
                                  readiness_policy=readiness_policy, road_warnings=road_warnings)
    contacts = {r['asset_id']: r for r in result['contacts']['ranked'] + result['contacts']['review']}
    blocked_roads = {w['road_id'] for w in road_warnings}
    by_asset = defaultdict(list)
    for allocation in plan['locations']:
        by_asset[allocation['asset_id']].append(allocation)
    occupied = defaultdict(int)
    for allocation in plan['locations']:
        occupied[allocation['destination_id']] += allocation['people']
    for row in result['locations']:
        aid = row['asset_id']
        allocated = by_asset[aid]
        asset_groups = [g for g in groups if g.asset_id == aid]
        row['allocations'] = allocated
        row['destination_candidates'] = [dict(group_id=g.group_id, candidates=evaluate_candidates(
            g, candidates, routes, roads, context, occupied)) for g in asset_groups
            if not any(a['group_id'] == g.group_id for a in allocated)]
        if not allocated:
            continue
        destinations = {a['destination_id'] for a in allocated}
        row['destination_id'] = next(iter(destinations)) if len(destinations) == 1 else None
        centre = next((c.centre for c in candidates if c.centre.centre_id == row['destination_id']), None)
        row['destination_name'] = centre.name if centre else None
        row['reasons'] = [r for r in row['reasons'] if r != 'no_feasible_destination']
        asset = next(a for a in scenario.locations if a.asset_id == aid)
        covered = ({g.group_id for g in asset_groups} == {a['group_id'] for a in allocated}
                   and asset.people is not None and sum(a['people'] for a in allocated) == asset.people)
        if not covered:
            row['reasons'].append('group_coverage_incomplete')
        assistance_conflict = row['reported_needs_assistance'] is True and not all(g.assisted is True for g in asset_groups)
        if assistance_conflict:
            row['reasons'].append('assistance_record_conflict')
        if contacts[aid]['status'] != 'window_open':
            row['reasons'].append('contact_window_unavailable')
        used_roads = {road_id for r in routes if r.asset_id == aid and r.centre_id in destinations for road_id in r.road_ids}
        if blocked_roads.intersection(used_roads):
            row['reasons'].append('road_danger_reported')
        if row['reasons']:
            for allocation in allocated:
                allocation['instruction_allowed'] = False
        safe = covered and all(a['instruction_allowed'] or a['state'] == 'arrived' and a['safety'] == 'valid'
                               for a in allocated)
        row['human_followup'] = bool(row['reasons']) or not safe
        if not safe:
            row['reasons'].append('allocation_review_required')
        row['tasks'] = sorted(set([task for a in allocated for task in a['tasks']]
                                 + (['human_callback'] if row['human_followup'] else [])
                                 + (['communicate_road_warning'] if road_warnings else [])
                                 + (['arrange_assistance'] if row['reported_needs_assistance'] is True else [])))
        row['mode'] = 'assisted_evacuation' if row['reported_needs_assistance'] is True else 'undetermined' if row['human_followup'] else (
            'assisted_evacuation' if any(g.assisted for g in asset_groups) else 'self_evacuate')
        if covered and all(a['state'] == 'arrived' for a in allocated):
            row['evacuation_status'] = 'arrival_confirmed'
        elif covered and all(a['state'] in ('departed', 'arrived') for a in allocated):
            row['evacuation_status'] = 'departure_confirmed'
    result.update(remaining_capacity=plan['remaining_capacity'], allocation_revision=plan['revision'],
                  snapshot_id=plan['snapshot_id'], as_of=as_of, allocation_tasks=plan['tasks'])
    return result


def build_approved_recommendation(store, asset_id, *, as_of, snapshot_id, route_guidance, expected_people=None):
    """Return call-briefings' approved mapping, or None for human/readiness-only flow.

    Route instructions must be separately supplied and verified against the same
    snapshot, asset, destination and complete ordered road IDs. No directions are
    generated. Partial-building or differing household destinations need human help.
    """
    plan, current = store.view(as_of=as_of)
    _, candidates, groups, routes, _ = current
    rows = [r for r in plan['locations'] if r['asset_id'] == asset_id]
    if (not isinstance(expected_people, int) or isinstance(expected_people, bool) or expected_people <= 0
            or sum(r['people'] for r in rows) != expected_people
            or plan['snapshot_id'] != snapshot_id or not rows or not isinstance(route_guidance, dict)
            or not all(r['instruction_allowed'] for r in rows)
            or {r['group_id'] for r in rows} != {g.group_id for g in groups if g.asset_id == asset_id}
            or len({r['destination_id'] for r in rows}) != 1):
        return None
    cid = rows[0]['destination_id']
    route = next((r for r in routes if r.asset_id == asset_id and r.centre_id == cid), None)
    guidance = route_guidance
    if (route is None or guidance.get('asset_id') != asset_id or guidance.get('centre_id') != cid
            or guidance.get('snapshot_id') != snapshot_id or guidance.get('confirmed') is not True
            or guidance.get('road_ids') != list(route.road_ids)
            or not isinstance(guidance.get('road_names'), (list, tuple)) or not guidance['road_names']
            or not all(isinstance(n, str) and n.strip() for n in guidance['road_names'])
            or not all(isinstance(guidance.get(k), str) and guidance[k].strip() for k in ('instructions', 'source'))):
        return None
    centre = next(c.centre for c in candidates if c.centre.centre_id == cid)
    return {'asset_id': asset_id, 'snapshot_id': snapshot_id, 'approved': True,
            'plan_id': ':'.join(sorted(r['allocation_id'] for r in rows)), 'revision': plan['revision'],
            'source': 'evacuation-allocation-ledger', 'destination': {'name': centre.name},
            'route': {'instructions': guidance['instructions'], 'road_ids': list(route.road_ids),
                      'road_names': list(guidance['road_names']), 'feasible': True}}
