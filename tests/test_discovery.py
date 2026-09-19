"""Offline behavior contracts for geographic facility discovery."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from shapely.geometry import Point, box, mapping, shape

from fireline import discovery
from fireline.snapshot import build_snapshot, validate_snapshot

T0 = '2026-07-03T08:00:00+00:00'


def fire(geometry=None, kind='perimeter'):
    return {'incident_id': 'synthetic-discovery', 'provider': 'fixture',
            'source': 'fixture:discovery', 'observed_at': T0, 'received_at': T0,
            'geometry_kind': kind, 'geometry': geometry}


def row(aid, lon=2.01, lat=42.01, **extra):
    return {'asset_id': aid, 'name': aid, 'asset_class': 'hospital',
            'lon': lon, 'lat': lat, 'occupancy': None, **extra}


def config(**extra):
    return discovery.DiscoveryConfig(threat_buffer_m=0, destination_buffer_m=5000, **extra)


def test_polygon_uses_all_forecast_features_not_last_and_applies_horizon():
    update = fire(mapping(box(2, 42, 2.02, 42.02)))
    features = [{'type': 'Feature', 'properties': {'elapsed_seconds': t},
                 'geometry': mapping(box(x, 42, x + .02, 42.02))}
                for t, x in [(3600, 2.1), (7200, 2.2), (10800, 3)]]
    spread = {'type': 'FeatureCollection', 'features': features}
    area = discovery.derive_search_areas(update, predicted_spread=spread,
                                         horizon_minutes=120, config=config())
    assert shape(area['threat_geometry']).covers(Point(2.21, 42.01))
    assert shape(area['threat_geometry']).covers(Point(2.01, 42.01))
    assert not shape(area['threat_geometry']).covers(Point(3.01, 42.01))
    assert area['threat_bounds'] == pytest.approx([2, 42, 2.22, 42.02])
    spread['features'].reverse()
    assert area == discovery.derive_search_areas(update, predicted_spread=spread,
                                                horizon_minutes=120, config=config())


def test_point_requires_explicit_extent_and_labels_fallback():
    update = fire(mapping(Point(2, 42)), 'hotspot_centre')
    with pytest.raises(ValueError, match='fallback'):
        discovery.derive_search_areas(update)
    area = discovery.derive_search_areas(update, config=config(fallback_radius_m=1000))
    assert area['basis'] == 'configured_point_fallback'
    assert any('not a forecast' in note for note in area['limitations'])
    assert shape(area['threat_geometry']).covers(Point(2, 42))
    assert not shape(area['threat_geometry']).covers(Point(2.03, 42))


def test_missing_geometry_requires_explicit_bounds_and_point_with_forecast_does_not():
    with pytest.raises(ValueError, match='fallback'):
        discovery.derive_search_areas(fire())
    area = discovery.derive_search_areas(fire(), config=config(fallback_bbox=(2, 42, 2.1, 42.1)))
    assert area['basis'] == 'configured_bbox_fallback'
    area = discovery.derive_search_areas(fire(mapping(Point(2, 42)), 'hotspot_centre'),
                                         predicted_spread=mapping(box(2, 42, 2.1, 42.1)))
    assert area['basis'] == 'forecast_and_current_geometry'


def test_polygon_holes_and_disjoint_parts_are_not_treated_as_solid_bounds():
    donut = box(2, 42, 2.1, 42.1).difference(box(2.02, 42.02, 2.08, 42.08))
    result = discovery.DiscoveryService(lambda bounds: [row('ring', 2.01, 42.01),
            row('hole', 2.05, 42.05)], source_id='test').discover(fire(mapping(donut)), config=config())
    assert result['classifications']['ring'] == 'threatened_search'
    assert result['classifications']['hole'] == 'destination_candidate'


def test_cache_dedup_and_changed_footprints_retain_contacted_assets(tmp_path):
    requests = []
    records = [row('a'), row('a'), row('destination', 2.04), row('later', 2.21),
               row('unknown', None, None, review_reasons=['needs_geocoding']),
               row('research', asset_class='research_facility')]
    def source(bounds):
        requests.append(bounds)
        return records
    service = discovery.DiscoveryService(source, source_id='fixture:v1', cache_dir=tmp_path)
    first = service.discover(fire(mapping(box(2, 42, 2.02, 42.02))), config=config())
    assert [r['asset_id'] for r in first['assets_in']] == ['a', 'destination', 'research', 'unknown']
    assert first['classifications']['destination'] == 'destination_candidate'
    assert first['classifications']['unknown'] == 'unlocated_review'
    again = service.discover(fire(mapping(box(2, 42, 2.02, 42.02))), config=config())
    assert len(requests) == 1
    assert again['assets_in'] == first['assets_in']
    changed = service.discover(fire(mapping(box(2.2, 42, 2.22, 42.02))), config=config(),
                               previous=first, tracked_assets=[row('contacted', 1, 41, contacted=True)])
    assert len(requests) == 2
    assert changed['classifications']['a'] == 'retained_outside_search'
    assert changed['classifications']['later'] == 'threatened_search'
    assert next(r for r in changed['assets_in'] if r['asset_id'] == 'contacted')['contacted'] is True
    assert next(r for r in changed['assets_in'] if r['asset_id'] == 'unknown')['review_reasons'] == ['needs_geocoding']
    revised = discovery.DiscoveryService(source, source_id='fixture:v2', cache_dir=tmp_path)
    revised.discover(fire(mapping(box(2, 42, 2.02, 42.02))), config=config())
    assert len(requests) == 3


def test_duplicate_conflicts_and_missing_ids_are_reviewable_not_fabricated():
    records = [row('same', occupancy=10), row('same', occupancy=20), row(None)]
    service = discovery.DiscoveryService(lambda bounds: records, source_id='test')
    result = service.discover(fire(mapping(box(2, 42, 2.02, 42.02))), config=config())
    assert len(result['assets_in']) == 1
    assert result['assets_in'][0]['occupancy'] is None
    assert result['review_records'][0]['reason'] == 'missing_stable_asset_id'
    assert result['conflicts'][0]['field'] == 'occupancy'
    assert result['conflicts'][0]['values'] == [10, 20]
    records.reverse()
    assert service.discover(fire(mapping(box(2, 42, 2.02, 42.02))), config=config())['assets_in'] == result['assets_in']


def test_raw_register_adapter_preserves_unclassified_and_unlocated_records():
    records = discovery.records_from_registers({'equipaments': [
        {'idequipament': '1', 'nom': 'Synthetic unknown type', 'categoria': 'unknown',
         'longitud': '2.01', 'latitud': '42.01'},
        {'idequipament': '2', 'nom': 'Synthetic hospital', 'categoria': '3. hospitals'}],
        'care_homes': [{'registre': '3', 'nom': 'Synthetic residence', 'tipologia': 'residència'}]})
    assert {r['asset_id'] for r in records} == {'equipaments:1', 'equipaments:2', 'care_homes:3'}
    assert next(r for r in records if r['asset_id'] == 'equipaments:1')['asset_class'] == 'unknown'
    assert next(r for r in records if r['asset_id'] == 'equipaments:2')['lon'] is None


def test_feed_failure_preserves_previous_and_is_not_cached(tmp_path):
    update = fire(mapping(box(2, 42, 2.02, 42.02)))
    first = discovery.DiscoveryService(lambda bounds: [row('a')], source_id='one').discover(update)
    def failed(bounds):
        raise OSError('private provider detail')
    result = discovery.DiscoveryService(failed, source_id='two', cache_dir=tmp_path).discover(update, previous=first)
    assert result['assets_in'] == first['assets_in']
    assert result['errors'] == [{'source_id': 'two', 'error': 'OSError', 'reason': 'facility_source_unavailable'}]
    assert not list(tmp_path.glob('*.json'))


def test_offline_cli_example_produces_deterministic_snapshot(tmp_path):
    output = tmp_path / 'discovery.json'
    example = Path('fixtures/discovery/synthetic.json')
    command = [sys.executable, '-m', 'fireline.discovery', '--input', str(example), '--output', str(output)]
    subprocess.run(command, check=True, capture_output=True, text=True)
    result = json.loads(output.read_text())
    example_data = json.loads(example.read_text())
    kwargs = dict(scenario_id='discovery', incident_id='synthetic-discovery', sequence=1,
                  as_of=T0, computed_at=T0, input_mode='synthetic')
    snapshot = build_snapshot(result['assets_in'], example_data['fire_update'], **kwargs)
    assert validate_snapshot(snapshot) == []
    assert snapshot == build_snapshot(result['assets_in'], example_data['fire_update'], **kwargs)
    assert any(a['asset_type'] == 'hospital' for a in snapshot['assets'])
    assert any(a['latitude'] is None for a in snapshot['assets'])
    before = output.read_bytes()
    subprocess.run(command, check=True, capture_output=True, text=True)
    assert output.read_bytes() == before
