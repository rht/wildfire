"""Recalculate an analyst's permutation using only supplied planning evidence."""
from copy import deepcopy
import hashlib
import json
import math

from .dashboard_approvals import PlanChanged, _TASK_FIELDS, plans_for
from .dashboard_public import _clean
from .response_priority import after_deadline


class InvalidOrder(ValueError):
    """A requested order removes work or moves an immutable task."""


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _strings(value):
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def preview_order(state, *, source, incident_id, revision, snapshot_id, team_id,
                  plan_version, action_ids, prior_approval_id=None):
    plans = plans_for(state, source=source, incident_id=incident_id,
                      revision=revision, snapshot_id=snapshot_id)
    base = next((p for p in plans if p['team_id'] == team_id), None)
    if base is None or base['plan_version'] != plan_version:
        raise PlanChanged('The crew plan or its evidence has changed.')
    team = next((t for t in state['plan']['response']['teams'] if t['team_id'] == team_id), {})
    tasks = [_clean({k: v for k, v in task.items() if k in _TASK_FIELDS})
             for task in team.get('tasks', [])]
    original_ids = [t.get('action_id') for t in tasks]
    if (not _strings(action_ids) or not _strings(original_ids)
            or len(set(original_ids)) != len(original_ids)
            or len(action_ids) != len(original_ids) or set(action_ids) != set(original_ids)):
        raise InvalidOrder('Include every current action exactly once.')
    for index, task in enumerate(tasks):
        if task.get('status') != 'proposed' and action_ids[index] != task['action_id']:
            raise InvalidOrder('Started, committed and completed actions must stay in place.')
    # A proposed action may not cross an immutable task even if its index stays fixed.
    for index, task in enumerate(tasks):
        if task.get('status') != 'proposed' and set(action_ids[:index]) != set(original_ids[:index]):
            raise InvalidOrder('Proposed actions cannot move across committed work.')
    indexed = {t['action_id']: t for t in tasks}
    ordered = [deepcopy(indexed[key]) for key in action_ids]
    context = _clean(team.get('planning_context'))
    blockers = []

    def block(code, reason, task=None):
        row = {'code': code, 'reason': reason}
        if task is not None:
            row['action_id'] = task['action_id']
        blockers.append(row)

    for task in ordered:
        if task.get('status') == 'proposed':
            for key in ('from_node', 'depart_min', 'travel_min', 'start_min', 'finish_min',
                        'path_lonlat', 'path_nodes', 'route_id', 'route_road_ids',
                        'route_source', 'route_status', 'ordering_evidence', 'ordering_reason'):
                task.pop(key, None)
            task['ordering_evidence'] = {'method': 'analyst_requested_order',
                                         'selection_rank': action_ids.index(task['action_id']) + 1}
    for other in state['plan']['response']['teams']:
        if other.get('team_id') == team_id:
            continue
        for task in other.get('tasks', []):
            if (task.get('status') != 'completed'
                    and set(task.get('prerequisites', [])) & set(original_ids)):
                block('cross_team_prerequisites',
                      'Another crew depends on this work; request a coordinated replan before editing.')
                break

    start = context.get('start') if isinstance(context, dict) else None
    valid_context = (isinstance(start, dict) and isinstance(start.get('node_id'), str)
                     and bool(start['node_id'].strip()) and _number(start.get('available_min'))
                     and all(_number(context.get(k)) for k in (
                         'now_min', 'buffer_min', 'horizon_min', 'available_until_min',
                         'transport_capacity'))
                     and _strings(context.get('capabilities'))
                     and isinstance(context.get('routes'), list)
                     and isinstance(context.get('prerequisites'), list)
                     and type(context.get('available')) is bool)
    remaining = None
    if not valid_context:
        block('planning_context_missing', 'Supplied crew start, routing and validation evidence is required.')
    else:
        node = start['node_id']
        clock = max(start['available_min'], context['now_min'])
        remaining = context['transport_capacity']
        buffer = context['buffer_min']
        done = {p['action_id']: p['finish_min'] for p in context['prerequisites']
                if isinstance(p, dict) and isinstance(p.get('action_id'), str)
                and p.get('status') == 'completed' and _number(p.get('finish_min'))
                and p['finish_min'] <= context['now_min']}
        if context['available'] is not True or team.get('locked'):
            block('team_unavailable', 'Crew availability or committed review prevents approval.')
        for task in ordered:
            if task.get('status') != 'proposed':
                finish = task.get('actual_finish_min', task.get('finish_min'))
                people = task.get('transport_people')
                if not _number(finish) or not _number(people) or not task.get('to_node'):
                    block('task_evidence_missing', 'Committed work lacks supplied finish, destination or capacity evidence.', task)
                    clock = None
                    continue
                if (task.get('status') != 'completed' and ordered.index(task) > 0
                        and (clock is None or not _number(task.get('depart_min'))
                             or clock > task['depart_min'] or node != task.get('from_node'))):
                    block('committed_conflict', 'Revised work conflicts with a fixed departure or origin.', task)
                remaining -= people
                # Completed rows are history, not a new crew-position report.
                # Match the planner: keep the supplied current planning start.
                if task.get('status') != 'completed':
                    node = task['to_node']
                    if clock is not None:
                        clock = max(clock, finish)
                done[task['action_id']] = finish
                continue
            required = (all(_number(task.get(k)) for k in (
                'duration_min', 'deadline_min', 'transport_people'))
                and task['duration_min'] > 0 and _strings(task.get('required_capabilities'))
                and _strings(task.get('prerequisites')) and isinstance(task.get('to_node'), str)
                and bool(task['to_node'].strip()) and type(task.get('readiness_required')) is bool)
            if not required:
                block('task_evidence_missing', 'Duration, deadline, capability, dependency and capacity evidence is required.', task)
                clock = None
                continue
            if not set(task['required_capabilities']) <= set(context['capabilities']):
                block('capabilities', 'Crew lacks a required capability.', task)
            remaining -= task['transport_people']
            if remaining < 0:
                block('transport_capacity', 'Requested work exceeds supplied crew transport capacity.', task)
            unresolved = set(task['prerequisites']) - done.keys()
            if unresolved:
                block('prerequisites', 'Required actions must finish before this action: ' + ', '.join(sorted(unresolved)), task)
                clock = None
            if clock is None:
                block('timing_unavailable', 'An earlier stop has no validated finish time.', task)
                continue
            depart = max([clock, *[done[key] for key in task['prerequisites']]])
            matches = [r for r in context['routes'] if isinstance(r, dict)
                       and r.get('from_node') == node and r.get('to_node') == task['to_node']]
            route = matches[0] if len(matches) == 1 else None
            route_source = route.get('route_source') if route else None
            sourced = (isinstance(route_source, str) and bool(route_source.strip())
                       or bool(route_source) and _strings(route_source))
            if (route is None or not _number(route.get('travel_min'))
                    or not _number(route.get('available_until_min'))
                    or route.get('confirmed') is not True or route.get('safe') is not True
                    or route.get('route_status') != 'qualified'
                    or not sourced
                    or depart + route['travel_min'] + buffer >= route['available_until_min']):
                block('route_unavailable', 'No unique qualified supplied route is available for this departure.', task)
                clock = None
                continue
            arrival = depart + route['travel_min']
            finish = arrival + task['duration_min']
            task.update(from_node=node, depart_min=depart, travel_min=route['travel_min'],
                        start_min=arrival, finish_min=finish,
                        route_source=route['route_source'], route_status='qualified')
            for key in ('path_lonlat', 'path_nodes', 'route_id', 'route_road_ids'):
                if key in route:
                    task[key] = deepcopy(route[key])
            if after_deadline(finish + buffer, min(task['deadline_min'], context['horizon_min'],
                                                  context['available_until_min'])):
                block('deadline', 'Recalculated finish plus buffer exceeds a supplied deadline.', task)
            readiness = task.get('readiness')
            if task['readiness_required'] or (readiness and task['transport_people']):
                if (not isinstance(readiness, dict) or readiness.get('status') != 'assistance_required'
                        or not _number(readiness.get('observed_min'))
                        or not _number(readiness.get('valid_until_min'))
                        or readiness['observed_min'] > context['now_min']
                        or readiness['valid_until_min'] <= finish + buffer):
                    block('readiness_review', 'Assistance readiness evidence is absent or expired at the revised finish.', task)
            node, clock = task['to_node'], finish
            done[task['action_id']] = finish
    reviewed = dict(team_id=team_id, revision=revision, action_ids=list(action_ids), original_action_ids=original_ids,
                    tasks=ordered, planning_context=context, remaining_transport_capacity=remaining)
    identity = dict(source=source, incident_id=incident_id, revision=revision,
                    snapshot_id=snapshot_id, team_id=team_id, plan_version=plan_version)
    encoded = json.dumps(dict(identity, reviewed_plan=reviewed, blockers=blockers,
                             prior_approval_id=prior_approval_id),
                         sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return dict(identity, review_version=hashlib.sha256(encoded).hexdigest(),
                can_confirm=base['can_confirm'] and not blockers,
                blockers=blockers, reviewed_plan=reviewed)
