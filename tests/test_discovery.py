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


@pytest.mark.parametrize('options', [
    {'threat_buffer_m': -1}, {'destination_buffer_m': float('inf')},
    {'fallback_radius_m': 0}, {'fallback_bbox': (3, 42, 2, 43)}])
def test_invalid_search_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        discovery.DiscoveryConfig(**options)


def test_ring_order_and_multipart_order_do_not_change_areas():
    a, b = box(2, 42, 2.02, 42.02), box(2.1, 42, 2.12, 42.02)
    from shapely.geometry import MultiPolygon, Polygon
    reordered = MultiPolygon([Polygon(list(b.exterior.coords)[::-1]), Polygon(list(a.exterior.coords)[::-1])])
    assert discovery.derive_search_areas(fire(mapping(MultiPolygon([a, b])))) == discovery.derive_search_areas(fire(mapping(reordered)))


def test_unusable_location_preserves_evidence_but_can_build_snapshot():
    bad = row('broken', lon='unknown', geometry={'type': 'NotGeoJSON'})
    result = discovery.DiscoveryService(lambda bounds: [bad], source_id='bad:v1').discover(
        fire(mapping(box(2, 42, 2.02, 42.02))))
    assert result['classifications']['broken'] == 'unlocated_review'
    assert result['review_records'][0]['record']['geometry'] == {'type': 'NotGeoJSON'}
    snap = build_snapshot(result['assets_in'], fire(mapping(box(2, 42, 2.02, 42.02))),
                          scenario_id='s', incident_id='synthetic-discovery', sequence=1,
                          as_of=T0, computed_at=T0, input_mode='synthetic')
    assert validate_snapshot(snap) == []
    assert snap['assets'][0]['longitude'] is None


def test_outside_duplicate_cannot_hide_conflicting_location():
    records = [row('same', 2.01), row('same', 5.0)]
    result = discovery.DiscoveryService(lambda bounds: records, source_id='dupes:v1').discover(
        fire(mapping(box(2, 42, 2.02, 42.02))), config=config())
    assert result['classifications']['same'] == 'unlocated_review'
    assert any(c['field'] == 'lon' for c in result['conflicts'])


def test_duplicate_uncertainty_is_not_silently_resolved_by_next_single_row():
    update = fire(mapping(box(2, 42, 2.02, 42.02)))
    first = discovery.DiscoveryService(lambda bounds: [row('a', occupancy=10), row('a', occupancy=20)],
                                       source_id='v1').discover(update)
    second = discovery.DiscoveryService(lambda bounds: [row('a', occupancy=20)], source_id='v2').discover(
        update, previous=first)
    assert second['assets_in'][0]['occupancy'] is None
    assert second['conflicts'] == first['conflicts']


def test_geometry_coordinate_lists_are_not_unioned_as_review_lists():
    records = [row('same', geometry=mapping(Point(2.01, 42.01))),
               row('same', geometry=mapping(Point(2.015, 42.01)))]
    result = discovery.DiscoveryService(lambda bounds: records, source_id='v1').discover(
        fire(mapping(box(2, 42, 2.02, 42.02))))
    assert result['assets_in'][0]['geometry'] is None
    assert result['classifications']['same'] == 'unlocated_review'


def test_forecast_provenance_and_source_input_are_not_mutated():
    spread = {'type': 'Feature', 'properties': {'source': 'synthetic only', 'elapsed_seconds': 60},
              'geometry': mapping(box(2, 42, 2.03, 42.03))}
    original = row('a', sources=[{'source':'local'}], custom={'nested': ['original']})
    service = discovery.DiscoveryService(lambda bounds: [original], source_id='fixture:v1')
    result = service.discover(fire(mapping(box(2, 42, 2.02, 42.02))), predicted_spread=spread)
    assert result['predicted_spread'] == spread
    result['assets_in'][0]['custom']['nested'].append('changed')
    assert original['custom']['nested'] == ['original']
    result['predicted_spread']['properties']['source'] = 'changed'
    assert spread['properties']['source'] == 'synthetic only'


def test_cache_refresh_and_new_service_reuse(tmp_path):
    update = fire(mapping(box(2, 42, 2.02, 42.02)))
    service = discovery.DiscoveryService(lambda bounds: [row('old')], source_id='v1', cache_dir=tmp_path)
    service.discover(update)
    fresh = discovery.DiscoveryService(lambda bounds: [row('new')], source_id='v1', cache_dir=tmp_path)
    assert fresh.discover(update)['assets_in'][0]['asset_id'] == 'old'
    assert fresh.discover(update, refresh=True)['assets_in'][0]['asset_id'] == 'new'


def test_forecast_without_timing_remains_included_with_limitation():
    area = discovery.derive_search_areas(fire(), predicted_spread=mapping(box(2, 42, 2.1, 42.1)),
                                         horizon_minutes=1)
    assert any('undated' in note for note in area['limitations'])


def test_missing_raw_register_ids_are_quarantined_separately():
    records = discovery.records_from_registers({'schools': [{'denominaci_completa': 'Unknown A'},
                                                           {'denominaci_completa': 'Unknown B'}]})
    result = discovery.DiscoveryService(lambda bounds: records, source_id='test').discover(
        fire(mapping(box(2, 42, 2.02, 42.02))))
    assert result['assets_in'] == []
    assert len(result['review_records']) == 2


def test_previous_cannot_cross_incident_boundary():
    with pytest.raises(ValueError, match='different incident'):
        discovery.DiscoveryService(lambda bounds: [], source_id='test').discover(
            fire(mapping(box(2, 42, 2.02, 42.02))), previous={'incident_id': 'different'})


def test_bounded_equipaments_adapter_preserves_newly_admitted_types(monkeypatch):
    def get_rows(bbox):
        assert bbox == (2, 42, 2.1, 42.1)
        return [{'idequipament': 'new', 'categoria': 'centres de recerca', 'longitud': '2.01', 'latitud': '42.01'}]
    monkeypatch.setattr(discovery.feeds, 'equipaments', get_rows)
    monkeypatch.setitem(discovery.feeds.ASSET_CLASS_RULES, 'equipaments',
                        discovery.feeds.ASSET_CLASS_RULES['equipaments'] + [('centres de recerca', 'research_facility')])
    result = discovery.equipaments_source((2, 42, 2.1, 42.1))
    assert result[0]['asset_class'] == 'research_facility'
    assert result[0]['asset_id'] == 'equipaments:new'


def test_mixed_snapshot_and_register_rows_keep_known_location_and_capacity():
    tracked = {'asset_id': 'a', 'asset_type': 'hospital', 'name': 'a', 'longitude': None,
               'latitude': None, 'capacity': None, 'estimated_occupancy': None,
               'occupancy_basis': None, 'sources': [], 'review_reasons': ['location_unknown']}
    result = discovery.DiscoveryService(lambda bounds: [row('a', occupancy=12, occupancy_source='register')],
                                         source_id='mixed').discover(
        fire(mapping(box(2, 42, 2.02, 42.02))), tracked_assets=[tracked])
    assert result['classifications']['a'] == 'threatened_search'
    snapshot = build_snapshot(result['assets_in'], fire(mapping(box(2, 42, 2.02, 42.02))),
                              scenario_id='s', incident_id='synthetic-discovery', sequence=1,
                              as_of=T0, computed_at=T0, input_mode='synthetic')
    asset = snapshot['assets'][0]
    assert asset['longitude'] == 2.01
    assert asset['capacity'] == 12
    assert asset['estimated_occupancy'] is None


def test_buffer_crossing_antimeridian_is_rejected_before_source_query():
    def source(bounds):
        pytest.fail('unsupported wrapped bounds must not reach facility source')
    with pytest.raises(ValueError, match='antimeridian'):
        discovery.DiscoveryService(source, source_id='test').discover(
            fire(mapping(Point(179.999, 42)), 'hotspot_centre'),
            config=discovery.DiscoveryConfig(fallback_radius_m=1000))


def test_snapshot_style_conflict_retains_other_fields_on_refresh():
    one = {'asset_id': 'a', 'asset_type': 'hospital', 'longitude': 2.01, 'latitude': 42.01,
           'capacity': 10, 'sources': [], 'name': 'a'}
    two = {**one, 'capacity': 20, 'name': 'disputed name'}
    update = fire(mapping(box(2, 42, 2.02, 42.02)))
    first = discovery.DiscoveryService(lambda bounds: [one, two], source_id='v1').discover(update)
    second = discovery.DiscoveryService(lambda bounds: [one], source_id='v2').discover(update, previous=first)
    assert second['assets_in'][0]['asset_type'] == 'hospital'
    assert second['assets_in'][0]['capacity'] is None
    assert second['classifications']['a'] == 'threatened_search'


def test_mixed_format_canonicalization_preserves_review_and_provenance():
    raw = row('a', occupancy=10, needs_review=['confirm_contact_permission'],
              review_reasons=['operator_requested_verification'], sources=[{'source': 'manual-review-file'}])
    tracked = {'asset_id': 'a', 'asset_type': 'hospital', 'longitude': 2.01, 'latitude': 42.01}
    result = discovery.DiscoveryService(lambda bounds: [raw], source_id='v1').discover(
        fire(mapping(box(2, 42, 2.02, 42.02))), tracked_assets=[tracked])
    record = result['assets_in'][0]
    assert 'confirm_contact_permission' in record['needs_review']
    assert 'operator_requested_verification' in record['review_reasons']
    assert {'source': 'manual-review-file'} in record['sources']


def test_conflicts_do_not_resurrect_excluded_assets():
    update = fire(mapping(box(2, 42, 2.02, 42.02)))
    first = discovery.DiscoveryService(lambda bounds: [row('outside', 5, occupancy=10),
        row('outside', 5, occupancy=20)], source_id='v1').discover(update)
    assert first['assets_in'] == []
    second = discovery.DiscoveryService(lambda bounds: [], source_id='v2').discover(update, previous=first)
    assert second['assets_in'] == []
