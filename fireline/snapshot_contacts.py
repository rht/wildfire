"""Validated snapshot v1.1 -> approved, enqueue-only voice requests.

No provider client is imported. Private contact/request inputs never enter the report.
Coordinates are projected only to satisfy the existing Location contract; no actions,
travel, effects, headcounts or arrival forecasts are inferred here.
"""
from collections import defaultdict
import copy
from dataclasses import asdict
import hashlib
import json

from . import config
from .contact_priority import ContactPolicy, missing_timing, window_arithmetic, contact_sort_key
from .grid import lonlat_to_xy
from .priority_models import Location, number
from .snapshot import validate_snapshot
from .voice_models import CallRequest, identifier, phone, utc


def snapshot_request_id(snapshot_id, asset_id):
    """Stable identity for one asset/snapshot, independent of phone, order and timing."""
    identifier(snapshot_id, 'snapshot_id')
    identifier(asset_id, 'asset_id')
    key = json.dumps([snapshot_id, asset_id], separators=(',', ':'))
    return 'snapshot-' + hashlib.sha256(key.encode()).hexdigest()


def briefing_adapter_from_recommendations(snapshot_id, recommendations, *, build_briefing=None):
    """Optional bridge to call-briefings' build_call_briefing export.

    Recommendations are keyed by stable asset_id. Missing recommendations deliberately
    reach the builder as None (readiness/human help only). Import the sibling module
    only when requested, so the base adapter has no dependency on its installation.
    """
    identifier(snapshot_id, 'snapshot_id')
    if not isinstance(recommendations, dict):
        raise ValueError('recommendations must be keyed by asset_id')
    if build_briefing is None:
        from .call_briefing import build_call_briefing
        build_briefing = build_call_briefing
    recommendations = copy.deepcopy(recommendations)

    def adapt(asset, data):
        briefing = build_briefing(asset, recommendations.get(asset['asset_id']), snapshot_id=snapshot_id)
        if briefing.asset_id != asset['asset_id'] or briefing.snapshot_id != snapshot_id:
            raise ValueError('briefing association mismatch')
        return {**data, 'incident_brief': briefing.incident_brief,
                'road_warnings': copy.deepcopy(briefing.road_warnings)}

    adapt.snapshot_id = snapshot_id
    return adapt


def _index(records, label):
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError(f'{label} must be a list of asset_id records')
    indexed = defaultdict(list)
    for record in records:
        identifier(record.get('asset_id'), 'asset_id')
        indexed[record['asset_id']].append(record)
    return indexed


def _validate(snapshot, epoch, now_at, max_age_min, buffer_min):
    # The producer validator is deliberately broad; errors here do not echo payloads.
    try:
        if validate_snapshot(snapshot) or snapshot['schema_version'] != '1.1':
            raise ValueError()
        identifier(snapshot['snapshot_id'], 'snapshot_id')
        for asset in snapshot['assets']:
            identifier(asset['asset_id'], 'asset_id')
            for field in ('forecast_source', 'evacuation_source', 'fire_arrival_basis'):
                if asset[field] is not None and not isinstance(asset[field], str):
                    raise ValueError()
            number(asset['distance_to_fire_m'], 'distance', nullable=True)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('invalid snapshot v1.1') from None
    epoch, now = utc(epoch), utc(now_at)
    as_of = utc(snapshot['as_of'])
    if epoch > now or epoch > as_of:
        raise ValueError('epoch must precede snapshot and evaluation time')
    number(max_age_min, 'max_age_min')
    number(buffer_min, 'buffer_min')
    return epoch, now, as_of


def _rank(asset, epoch, now, policy):
    arrival = asset['fire_arrival_at']
    arrival_min = None if arrival is None else (utc(arrival) - epoch).total_seconds() / 60
    missing = missing_timing(arrival_min, asset['evacuation_min'],
                             asset['forecast_source'], asset['evacuation_source'])
    row = dict(asset_id=asset['asset_id'], rank=None, policy=policy.version,
        slack_min=None, latest_start_min=None, time_to_impact_min=None, status='needs_review',
        components=dict(fire_arrival_min=arrival_min, now_min=policy.now_min,
            evacuation_min=asset['evacuation_min'], buffer_min=policy.buffer_min,
            distance_m=asset['distance_to_fire_m']),
        forecast_source=asset['forecast_source'], evacuation_source=asset['evacuation_source'],
        review_reasons=missing,
        evidence={key: copy.deepcopy(asset[key]) for key in (
            'fire_arrival_at', 'fire_arrival_basis', 'forecast_horizon_at', 'sources',
            'distance_to_fire_m', 'intersects_fire', 'review_reasons')})
    if not missing:
        row.update(window_arithmetic(arrival_min, asset['evacuation_min'], policy.now_min, policy.buffer_min))
    return row


def _timing_blocks(asset, row, now, as_of, max_age_min):
    reasons = ['missing_' + field for field in row['review_reasons']]
    age = (now - as_of).total_seconds() / 60
    if age < 0:
        reasons.append('future_snapshot')
    elif age > max_age_min:
        reasons.append('stale_snapshot')
    arrival = row['components']['fire_arrival_min']
    if arrival is not None and arrival < 0:
        reasons.append('arrival_before_epoch')
    horizon = asset['forecast_horizon_at']
    if horizon is not None:
        horizon = utc(horizon)
        if horizon <= now:
            reasons.append('forecast_horizon_expired')
        if asset['fire_arrival_at'] and utc(asset['fire_arrival_at']) > horizon:
            reasons.append('arrival_after_forecast_horizon')
    # Fresh snapshot assembly cannot make an old forecast fresh. Geometry age is
    # separate: it never supplies or invalidates an otherwise current prediction.
    forecast_sources = [s for s in asset['sources']
                        if set(s['fields']) & {'fire_arrival_at', 'forecast_source'}]
    if asset['forecast_source'] and (not forecast_sources or any(
            source.get('observed_at') is None for source in forecast_sources)):
        reasons.append('missing_forecast_observed_at')
    for source in forecast_sources:
        for key in ('observed_at', 'available_at', 'fetched_at'):
            if source.get(key) is None:
                continue
            timestamp = utc(source[key])
            if timestamp > now:
                reasons.append('future_forecast_evidence')
            elif key == 'observed_at' and (now - timestamp).total_seconds() / 60 > max_age_min:
                reasons.append('stale_forecast')
    return list(dict.fromkeys(reasons))


def _contact(asset_id, snapshot_id, contacts, approvals):
    matches = contacts.get(asset_id, [])
    if not matches:
        return None, ['missing_contact']
    if len(matches) != 1:
        return None, ['ambiguous_contact']
    target = matches[0].get('contact_number')
    if target is None or target == '':
        return None, ['missing_phone']
    try:
        phone(target)
    except ValueError:
        return None, ['invalid_phone']
    approved = [r for r in approvals.get(asset_id, []) if r.get('snapshot_id') == snapshot_id]
    if not approved:
        return None, ['missing_authorization']
    if len(approved) != 1:
        return None, ['ambiguous_authorization']
    if approved[0].get('contact_number') != target:
        return None, ['target_not_authorized']
    return target, []


def _location(asset, row):
    if asset['longitude'] is None or asset['latitude'] is None:
        return None
    lon, lat = asset['longitude'], asset['latitude']
    number(lon, 'longitude', minimum=None)
    number(lat, 'latitude', minimum=None)
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError('invalid coordinates')
    x, y = lonlat_to_xy(lon, lat)
    number(x, 'x_m', minimum=None)
    number(y, 'y_m', minimum=None)
    return Location(asset_id=asset['asset_id'], name=asset['name'], x_m=x, y_m=y,
        distance_m=asset['distance_to_fire_m'], people=asset['estimated_occupancy'],
        assisted=None, value=None, deadline_min=None,
        fire_arrival_min=row['components']['fire_arrival_min'], evacuation_min=asset['evacuation_min'],
        forecast_source=asset['forecast_source'], evacuation_source=asset['evacuation_source'])


def _request(asset, snapshot_id, target, data, briefing_adapter):
    if briefing_adapter is not None:
        # Callback owns briefing content only, never association or authorization.
        if getattr(briefing_adapter, 'snapshot_id', snapshot_id) != snapshot_id:
            raise ValueError('briefing snapshot mismatch')
        data = briefing_adapter(copy.deepcopy(asset), copy.deepcopy(data))
    allowed = {'language', 'incident_brief', 'human_callback_number', 'road_warnings'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('invalid request data')
    return CallRequest(request_id=snapshot_request_id(snapshot_id, asset['asset_id']),
        asset_id=asset['asset_id'], snapshot_id=snapshot_id, contact_number=target,
        input_mode='live', **copy.deepcopy(data))


def enqueue_snapshot_contacts(queue, snapshot, contacts, approvals, request_data, *,
                              epoch, now_at, max_age_min=None, buffer_min=None,
                              briefing_adapter=None):
    """Enqueue exact approved targets, returning public ranking and blocked reasons.

    Contacts: [{asset_id, contact_number}]; approvals additionally require snapshot_id.
    request_data: {asset_id: {language, incident_brief, optional callback/road_warnings}}.
    All timestamps are UTC ISO strings. Optional briefing_adapter(asset, data) returns
    request-data fields only. This function never dispatches or imports a provider.
    A blocked replay cancels only its own unstarted queue entry; started calls stay held.
    """
    max_age_min = config.FRESHNESS['stale_after_s'] / 60 if max_age_min is None else max_age_min
    buffer_min = config.CONTACT_POLICY['buffer_min'] if buffer_min is None else buffer_min
    epoch_dt, now, as_of = _validate(snapshot, epoch, now_at, max_age_min, buffer_min)
    if queue.store.epoch != epoch_dt:
        raise ValueError('queue scenario epoch mismatch')
    contacts, approvals = _index(contacts, 'contacts'), _index(approvals, 'approvals')
    if not isinstance(request_data, dict):
        raise ValueError('request_data must be keyed by asset_id')
    policy = ContactPolicy(now_min=(now - epoch_dt).total_seconds() / 60,
                           buffer_min=buffer_min, version=config.CONTACT_POLICY['version'])
    report = dict(schema_version='snapshot-contacts-1', scenario_id=snapshot['scenario_id'],
        snapshot_id=snapshot['snapshot_id'], epoch=epoch, as_of=now_at,
        input_mode=snapshot['input_mode'], policy=dict(version=policy.version,
            now_min=policy.now_min, buffer_min=buffer_min, max_age_min=max_age_min),
        observed_geometry=dict(observed_at=snapshot['fire_observed_at'],
            source=snapshot['fire_source'], data_status=snapshot['data_status']),
        ranked=[], review=[], blocked=[], existing={}, enqueue={})
    assets = {a['asset_id']: a for a in snapshot['assets']}
    for asset in assets.values():
        row = _rank(asset, epoch_dt, now, policy)
        report['review' if row['review_reasons'] else 'ranked'].append(row)
    report['ranked'].sort(key=lambda r: contact_sort_key(r['slack_min'],
        r['components']['fire_arrival_min'], r['components']['distance_m'], r['asset_id']))
    report['review'].sort(key=lambda r: r['asset_id'])
    for rank, row in enumerate(report['ranked'], 1):
        row['rank'] = rank
    locations, requests, targets = [], [], {}
    with queue.store._transaction():
        for row in report['ranked'] + report['review']:
            asset_id = row['asset_id']
            asset = assets[asset_id]
            rid = snapshot_request_id(snapshot['snapshot_id'], asset_id)
            reasons = _timing_blocks(asset, row, now, as_of, max_age_min)
            if snapshot['input_mode'] != 'live':
                reasons.append('snapshot_not_live')
            target, contact_reasons = _contact(asset_id, snapshot['snapshot_id'], contacts, approvals)
            reasons.extend(contact_reasons)
            data = request_data.get(asset_id)
            if data is None:
                reasons.append('missing_request_data')
            loc, request = None, None
            try:
                loc = _location(asset, row)
                if loc is None:
                    reasons.append('queue_coordinates_unavailable')
            except (ValueError, TypeError):
                reasons.append('invalid_coordinates')
            existing = queue.store.conn.execute(
                'SELECT * FROM voice_queue WHERE asset_id=? AND snapshot_id=?',
                (asset_id, snapshot['snapshot_id'])).fetchone()
            if existing:
                stored = queue.store.get(existing['request_id'])
                state = 'started' if stored['dispatch_state'] != 'not_started' else existing['state']
                report['existing'][existing['request_id']] = state
                if state == 'started':
                    if reasons:
                        report['blocked'].append(dict(asset_id=asset_id, request_id=rid, reasons=reasons))
                    continue
            if not reasons:
                try:
                    request = _request(asset, snapshot['snapshot_id'], target, data, briefing_adapter)
                except (ValueError, TypeError, KeyError):
                    reasons.append('invalid_request_data')
            if existing and not reasons:
                if stored['request'] != asdict(request):
                    reasons.append('immutable_request_conflict')
                expected_priority = (row['latest_start_min'], row['components']['fire_arrival_min'],
                                     row['components']['distance_m'])
                if tuple(existing[k] for k in ('latest_start', 'arrival', 'distance')) != expected_priority:
                    reasons.append('immutable_priority_conflict')
            if reasons:
                if existing and state in ('pending', 'review'):
                    queue.cancel(existing['request_id'])
                    report['existing'][existing['request_id']] = 'cancelled'
                report['blocked'].append(dict(asset_id=asset_id, request_id=rid, reasons=reasons))
            elif not existing:
                locations.append(loc)
                requests.append(request)
                targets[rid] = target
        report['enqueue'] = queue.enqueue(locations, requests, approved_targets=targets, policy=policy)
    return report
