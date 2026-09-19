"""Fire-driven facility collection; offline CLI: ``python -m fireline.discovery``.

Search coverage is not fire arrival, destination suitability, or containment capability.
Sources return existing assets_in or snapshot-style records; no facility-type whitelist.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Callable, Iterable

from pyproj import CRS, Transformer
from shapely.errors import ShapelyError
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import transform, unary_union

from . import feeds
from .snapshot import asset_record


@dataclass(frozen=True)
class DiscoveryConfig:
    """Metres; destination buffer is additional to the threatened search area."""
    threat_buffer_m: float = 1000
    destination_buffer_m: float = 10000
    fallback_radius_m: float | None = None
    fallback_bbox: tuple[float, float, float, float] | None = None

    def __post_init__(self):
        for name in ('threat_buffer_m', 'destination_buffer_m', 'fallback_radius_m'):
            value = getattr(self, name)
            if value is None and name == 'fallback_radius_m':
                continue
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{name} must be finite and nonnegative')
        if self.fallback_radius_m == 0:
            raise ValueError('fallback_radius_m must be positive')
        if self.fallback_bbox is not None:
            w, s, e, n = self.fallback_bbox
            if not (-180 <= w < e <= 180 and -90 < s < n < 90):
                raise ValueError('fallback_bbox must be ordered WGS84 bounds')


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _geometry(value):
    try:
        geom = shape(value)
    except (ShapelyError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise ValueError('invalid GeoJSON geometry') from exc
    if geom.is_empty or not geom.is_valid:
        raise ValueError('empty or invalid geometry')
    w, s, e, n = geom.bounds
    if not all(math.isfinite(v) for v in geom.bounds) or not (-180 <= w <= e <= 180 and -90 < s <= n < 90):
        raise ValueError('geometry must use WGS84 longitude/latitude')
    if e - w > 180:
        raise ValueError('antimeridian geometry is unsupported; split it before discovery')
    return geom


def _forecast_polygons(value, horizon_minutes, limitations):
    if value is None:
        return []
    if value.get('type') == 'FeatureCollection':
        features = value['features']
    elif value.get('type') == 'Feature':
        features = [value]
    else:
        features = [{'geometry': value, 'properties': {}}]
    polygons = []
    for feature in features:
        props = feature.get('properties') or {}
        elapsed = props.get('elapsed_seconds')
        if horizon_minutes is not None:
            if elapsed is None:
                limitations.append('undated forecast footprint included; horizon cannot filter it')
            else:
                if isinstance(elapsed, bool) or not math.isfinite(float(elapsed)) or float(elapsed) < 0:
                    raise ValueError('elapsed_seconds must be finite and nonnegative')
                if float(elapsed) > horizon_minutes * 60:
                    continue
        geom = _geometry(feature['geometry'])
        if geom.geom_type not in ('Polygon', 'MultiPolygon'):
            raise ValueError('predicted spread must contain polygon footprints')
        polygons.append(geom)
    return polygons


def derive_search_areas(fire_update, *, predicted_spread=None, horizon_minutes=None,
                        config=DiscoveryConfig()):
    """Derive WGS84 areas/bounds from polygons, never from a fixed regional extent.

    Horizon filters Feature properties.elapsed_seconds relative to the caller's forecast
    epoch; undated footprints remain included and labelled. No arrival is interpolated.
    """
    if horizon_minutes is not None and (isinstance(horizon_minutes, bool)
            or not math.isfinite(horizon_minutes) or horizon_minutes < 0):
        raise ValueError('horizon_minutes must be finite and nonnegative')
    limitations = ['search areas do not establish destination safety or containment capabilities']
    polygons = _forecast_polygons(predicted_spread, horizon_minutes, limitations)
    current = _geometry(fire_update['geometry']) if fire_update.get('geometry') else None
    if current is not None and current.geom_type not in ('Polygon', 'MultiPolygon', 'Point'):
        raise ValueError('current fire geometry must be a polygon or point')
    basis = 'forecast_and_current_geometry' if polygons else 'current_perimeter'
    if current is not None and current.geom_type != 'Point':
        polygons.append(current)
    if polygons:
        base = unary_union(polygons)
        if current is not None:
            base = base.union(current)
    elif config.fallback_bbox is not None:
        base = box(*config.fallback_bbox)
        if current is not None and not base.covers(current):
            raise ValueError('fallback_bbox must cover the current fire point')
        basis = 'configured_bbox_fallback'
    elif current is not None and config.fallback_radius_m is not None:
        base = current
        basis = 'configured_point_fallback'
    else:
        raise ValueError('point or missing fire geometry requires an explicit fallback extent')
    if 'fallback' in basis:
        limitations.append('configured fallback extent is a search assumption, not a forecast')
    if predicted_spread is None:
        limitations.append('no predicted spread supplied; future threatened extent is unknown')
    # A local azimuthal projection avoids imposing the Gavarres/UTM31 search extent.
    w, s, e, n = base.bounds
    if e - w > 10 or n - s > 10 or max(abs(s), abs(n)) > 80:
        raise ValueError('discovery supports local areas up to 10 degrees wide below 80 degrees latitude')
    crs = CRS.from_proj4(f'+proj=aeqd +lat_0={(s+n)/2} +lon_0={(w+e)/2} +datum=WGS84 +units=m')
    forward = Transformer.from_crs('EPSG:4326', crs, always_xy=True).transform
    inverse = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True).transform
    projected = transform(forward, base)
    radius = config.threat_buffer_m
    if basis == 'configured_point_fallback':
        radius += config.fallback_radius_m
    threat = transform(inverse, projected.buffer(radius)) if radius else base
    destination = transform(inverse, projected.buffer(radius + config.destination_buffer_m))
    if not radius and not config.destination_buffer_m:
        destination = base
    # Projection inversion can wrap even when the input does not cross the date line.
    for area in (threat, destination):
        _geometry(mapping(area))
        if area.bounds[2] - area.bounds[0] > 10 or area.bounds[3] - area.bounds[1] > 10:
            raise ValueError('buffered discovery area exceeds local 10-degree extent')
    def encoded(geom):
        return json.loads(_json(mapping(geom.normalize())))
    return {'basis': basis, 'horizon_minutes': horizon_minutes,
            'threat_geometry': encoded(threat), 'destination_geometry': encoded(destination),
            'threat_bounds': list(threat.bounds), 'destination_bounds': list(destination.bounds),
            'limitations': sorted(set(limitations))}


def records_from_registers(registers: dict) -> list[dict]:
    """Normalize bounded/local raw register extracts without losing unclassified rows.

    Reuses installed feed rules (including future criticality classes). Raw fields stay
    in register_record for internal review. Missing source IDs remain missing.
    """
    records = []
    for register in feeds.REGISTER_ID_COLUMN:
        for raw in registers.get(register, []):
            located, unlocated = feeds.registers_to_assets(
                **{register: [raw]}, schools_enrolment=registers.get('schools_enrolment'))
            if located or unlocated:
                record = (located + unlocated)[0]
            else:
                lon, lat = feeds._lonlat(raw)
                record = {'asset_class': 'unknown', 'name': raw.get(feeds.REGISTER_NAME_COLUMN[register]),
                          'lon': lon, 'lat': lat, 'occupancy': None, 'register': register,
                          'needs_review': ['unclassified_facility']}
            raw_id = raw.get(feeds.REGISTER_ID_COLUMN[register])
            record['asset_id'] = f'{register}:{raw_id}' if raw_id not in (None, '') else None
            record['register_record'] = deepcopy(raw)
            records.append(record)
    return records


def equipaments_source(bounds):
    """Explicit network-capable adapter: only a bounded Equipaments query, no bulk registers.

    Other register extracts need geographic scoping by their caller before normalization;
    registers without coordinates cannot establish geographic coverage.
    """
    return records_from_registers({'equipaments': feeds.equipaments(bbox=tuple(bounds))})


def _location(record):
    try:
        if record.get('geometry') is not None:
            return _geometry(record['geometry'])
        lon = record.get('longitude', record.get('lon'))
        lat = record.get('latitude', record.get('lat'))
        if lon is None or lat is None:
            return None
        return _geometry(mapping(Point(float(lon), float(lat))))
    except (ValueError, TypeError, KeyError):
        return None


def _canonical_group(group):
    """Only mixed input formats need normalization; preserve raw records otherwise."""
    if not any(any(key in r for key in ('asset_type', 'latitude', 'capacity')) for r in group):
        return group
    result = []
    for record in group:
        if any(key in record for key in ('asset_type', 'latitude', 'capacity')):
            result.append(record)
            continue
        normalized = {**record, **asset_record(record)}
        for key in ('review_reasons', 'sources'):
            if isinstance(record.get(key), list):
                normalized[key] = normalized.get(key, []) + record[key]
        normalized['needs_review'] = record.get('needs_review', normalized['needs_review'])
        for key in ('asset_class', 'lon', 'lat', 'occupancy', 'occupancy_source'):
            normalized.pop(key, None)
        result.append(normalized)
    return result


def _deduplicate(records):
    grouped = defaultdict(list)
    review, conflicts, assets = [], [], []
    for record in records:
        aid = record.get('asset_id')
        if not isinstance(aid, str) or not aid.strip():
            review.append({'reason': 'missing_stable_asset_id', 'record': deepcopy(record)})
        else:
            grouped[aid].append(record)
    for aid, group in sorted(grouped.items()):
        group = _canonical_group(group)
        merged = {}
        for field in sorted(set().union(*(r.keys() for r in group))):
            values = {_json(r[field]): r[field] for r in group if r.get(field) is not None}
            unique = [values[key] for key in sorted(values)]
            if len(unique) <= 1:
                merged[field] = deepcopy(unique[0]) if unique else None
            elif field in ('sources', 'review_reasons', 'needs_review') and all(isinstance(v, list) for v in unique):
                items = {_json(item): item for value in unique for item in value}
                merged[field] = deepcopy([items[key] for key in sorted(items)])
            else:
                merged[field] = None
                conflicts.append({'asset_id': aid, 'field': field, 'values': unique})
        assets.append(merged)
    return assets, review, conflicts


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(_json(value) + '\n')
            handle.flush()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


class DiscoveryService:
    """One revisioned facility source; disk cache is opt-in and never expires implicitly.

    Change source_id when the register revision changes, or pass refresh=True. Retention
    across runs is explicit via previous/tracked_assets, so cache is not incident state.
    """
    def __init__(self, source: Callable[[tuple], Iterable[dict]], *, source_id: str, cache_dir=None):
        if not source_id:
            raise ValueError('source_id must identify the source revision')
        self.source = source
        self.source_id = source_id
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    def _collect(self, bounds, refresh):
        key = hashlib.sha256(_json(['discovery-cache-1', self.source_id, bounds]).encode()).hexdigest()
        path = self.cache_dir / f'{key}.json' if self.cache_dir is not None else None
        if path is not None and path.exists() and not refresh:
            return json.loads(path.read_text(encoding='utf-8'))
        records = list(self.source(tuple(bounds)))
        if path is not None:
            _write_json(path, records)
        return records

    def discover(self, fire_update, *, predicted_spread=None, horizon_minutes=None,
                 config=DiscoveryConfig(), previous=None, tracked_assets=(), refresh=False):
        """Return discovery-1; pass result['assets_in'] directly to build_snapshot.

        Previous/tracked fields are merged conservatively: conflicting known values are
        null with evidence in conflicts, never silently last-row-wins. Retained IDs survive
        source outages and changed footprints. Caller must persist/pass previous each run.
        """
        if previous and previous.get('incident_id') != fire_update.get('incident_id'):
            raise ValueError('previous discovery belongs to a different incident')
        areas = derive_search_areas(fire_update, predicted_spread=predicted_spread,
                                    horizon_minutes=horizon_minutes, config=config)
        threat, destination = shape(areas['threat_geometry']), shape(areas['destination_geometry'])
        previous = previous or {}
        retained = list(previous.get('assets_in', [])) + list(tracked_assets)
        retained_ids = {r.get('asset_id') for r in retained if r.get('asset_id')}
        errors = []
        try:
            collected = self._collect(areas['destination_bounds'], refresh)
        except (OSError, ValueError, feeds.FeedError) as exc:
            collected = []
            errors.append({'source_id': self.source_id, 'error': type(exc).__name__,
                           'reason': 'facility_source_unavailable'})
        # Reintroduce unresolved evidence before merging, so a later partial source cannot
        # turn a disputed field into a fact. Resolution belongs to an explicit analyst step.
        prior_records = {r['asset_id']: r for r in previous.get('assets_in', [])}
        evidence = [{**prior_records[c['asset_id']], c['field']: value}
                    for c in previous.get('conflicts', []) if c['asset_id'] in prior_records
                    for value in c['values']]
        assets, review, conflicts = _deduplicate(retained + collected + evidence)
        conflicted_locations = {c['asset_id'] for c in conflicts
                                if c['field'] in ('geometry', 'lon', 'lat', 'longitude', 'latitude')}
        selected, classes = [], {}
        for record in assets:
            if record['asset_id'] in conflicted_locations:
                for key in ('geometry', 'lon', 'lat', 'longitude', 'latitude'):
                    if key in record:
                        record[key] = None
            location = _location(record)
            if location is not None and not destination.intersects(location) and record['asset_id'] not in retained_ids:
                continue
            selected.append(record)
            if location is None:
                category = 'unlocated_review'
                # Keep malformed input for review, but don't pass unusable geometry to snapshots.
                if record.get('geometry') is not None or any(record.get(k) is not None
                        for k in ('lon', 'lat', 'longitude', 'latitude')):
                    review.append({'reason': 'invalid_location', 'record': deepcopy(record)})
                    for key in ('geometry', 'lon', 'lat', 'longitude', 'latitude'):
                        if key in record:
                            record[key] = None
            elif threat.intersects(location):
                category = 'threatened_search'
            elif destination.intersects(location):
                category = 'destination_candidate'
            else:
                category = 'retained_outside_search'
            classes[record['asset_id']] = category
        def unique(values):
            indexed = {_json(value): value for value in values}
            return [indexed[key] for key in sorted(indexed)]
        return {'schema_version': 'discovery-1', 'incident_id': fire_update.get('incident_id'),
                'fire_update': deepcopy(fire_update), 'source_id': self.source_id,
                'predicted_spread': deepcopy(predicted_spread),
                'areas': areas, 'assets_in': selected, 'classifications': classes,
                'review_records': unique(list(previous.get('review_records', [])) + review),
                'conflicts': unique(list(previous.get('conflicts', [])) + conflicts), 'errors': errors}


def main(argv=None):
    """Local bundle: fire_update, assets_in and/or registers, optional predicted_spread/config."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args(argv)
    body = json.loads(args.input.read_text(encoding='utf-8'))
    records = body.get('assets_in', []) + records_from_registers(body.get('registers', {}))
    revision = hashlib.sha256(_json(records).encode()).hexdigest()
    service = DiscoveryService(lambda bounds: records, source_id=f'offline:{revision}', cache_dir=args.cache_dir)
    previous = json.loads(args.previous.read_text(encoding='utf-8')) if args.previous else None
    result = service.discover(body['fire_update'], predicted_spread=body.get('predicted_spread'),
                              horizon_minutes=body.get('horizon_minutes'),
                              config=DiscoveryConfig(**body.get('config', {})), previous=previous,
                              tracked_assets=body.get('tracked_assets', []), refresh=args.refresh)
    _write_json(args.output, result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
