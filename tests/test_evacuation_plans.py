"""Supplied evidence must justify reception and route suitability."""
from dataclasses import asdict, replace

import pytest

from fireline.evacuation_readiness import EvacuationRoute, ReceptionCentre
from fireline import evacuation_plans as ep


def inputs():
    context = ep.PlanningContext('scenario', 'incident-1', 'snapshot-1',
                                 '2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z', 0)
    approval = ep.AnalystApproval('approval-centre', 'analyst', 'incident-1', 'centre',
                                  'Reception inspected')
    centre = ep.CandidateFacility(ReceptionCentre('centre', 'Hall', 6, True, 90, 'facility-register'),
                                   'community_centre', approval, 90, 'forecast-1', ())
    group = ep.EvacuationGroup('group-A', 'A', 4, False, (), 60, 5, 'forecast-1')
    route = EvacuationRoute('A', 'centre', 2, 5, True, 60, 'route-inspection', ('road-1',))
    road = ep.RoadEvidence('road-1', 'open', 0, 60, 'road-inspection')
    return context, centre, group, route, road


def evaluate(**changes):
    context, centre, group, route, road = inputs()
    return ep.evaluate_candidates(changes.get('group', group), changes.get('candidates', [centre]),
                                  changes.get('routes', [route]), changes.get('roads', [road]),
                                  changes.get('context', context), changes.get('occupied', {}))


def test_suitability_cites_approval_threat_capacity_needs_and_route():
    row = evaluate()[0]
    assert row['status'] == 'eligible'
    assert row['remaining_capacity'] == 6
    assert row['evidence']['approval_id'] == 'approval-centre'
    assert row['evidence']['road_sources'] == ['road-inspection']
    assert row['evidence']['threat_source'] == 'forecast-1'
    assert row['evidence']['needs'] == []
    assert row['finish_min'] == 5


def test_raw_hospital_is_never_an_approved_centre():
    candidate = ep.candidate_from_record({'asset_id': 'hospital', 'name': 'Hospital', 'kind': 'hospital'})
    result = evaluate(candidates=[candidate], routes=[])[0]
    assert result['status'] == 'review'
    assert 'reception_not_approved' in result['reasons']
    assert 'capacity_unknown' in result['reasons']


@pytest.mark.parametrize('field,value,reason', [
    ('approval', None, 'reception_not_approved'),
    ('safe_until_min', None, 'destination_threat_unknown'),
    ('capabilities', None, 'reception_needs_unknown'),
    ('safe_until_min', 5, 'destination_window_exhausted'),
])
def test_incomplete_or_unsafe_candidate_is_not_eligible(field, value, reason):
    _, centre, *_ = inputs()
    row = evaluate(candidates=[replace(centre, **{field: value})])[0]
    assert row['status'] != 'eligible'
    assert reason in row['reasons']


@pytest.mark.parametrize('state,reason,status', [
    ('blocked', 'road_blocked', 'unsafe'), ('unknown', 'road_unknown', 'review')])
def test_road_safety_is_positive_evidence(state, reason, status):
    *_, road = inputs()
    row = evaluate(roads=[replace(road, state=state)])[0]
    assert row['status'] == status
    assert reason in row['reasons']


def test_missing_road_records_and_route_road_ids_require_review():
    *_, route, _ = inputs()
    assert 'road_unknown' in evaluate(roads=[])[0]['reasons']
    assert 'route_roads_unknown' in evaluate(routes=[replace(route, road_ids=())])[0]['reasons']


def test_stale_forecast_and_road_evidence_require_review():
    context, *_, road = inputs()
    late = replace(context, as_of='2026-09-20T00:20:00Z')
    assert 'stale_forecast' in evaluate(context=late)[0]['reasons']
    assert 'stale_road_evidence' in evaluate(context=late)[0]['reasons']


def test_assisted_needs_are_matched_and_never_inferred():
    _, centre, group, *_ = inputs()
    immobile = replace(group, assisted=True, needs=('wheelchair',))
    assert 'reception_needs_unmet' in evaluate(group=immobile)[0]['reasons']
    assert evaluate(group=immobile, candidates=[replace(centre, capabilities=('wheelchair',))])[0]['status'] == 'eligible'
    assert 'group_needs_unknown' in evaluate(group=replace(group, needs=None))[0]['reasons']
    assert 'assistance_unknown' in evaluate(group=replace(group, assisted=None))[0]['reasons']


def test_existing_occupancy_and_origin_threat_constrain_selection():
    _, _, group, *_ = inputs()
    assert 'insufficient_capacity' in evaluate(occupied={'centre': 3})[0]['reasons']
    assert 'origin_window_exhausted' in evaluate(group=replace(group, fire_arrival_min=5))[0]['reasons']


def test_wrong_incident_or_destination_approval_requires_review():
    _, centre, *_ = inputs()
    for change in ({'incident_id': 'old'}, {'centre_id': 'other'}):
        assert 'reception_not_approved' in evaluate(candidates=[replace(centre, approval=replace(centre.approval, **change))])[0]['reasons']


def test_json_adapter_roundtrip_retains_unknowns_and_provenance():
    _, centre, *_ = inputs()
    assert ep.candidate_from_record(asdict(centre)) == centre


def test_supplied_nonfinite_timing_cannot_pass_safety_checks():
    _, _, group, *_ = inputs()
    with pytest.raises(ValueError):
        evaluate(group=replace(group, evacuation_min=float('nan')))


def test_discovery_source_provenance_survives_adapter_without_approval():
    record = {'asset_id': 'facility-1', 'name': 'Hospital', 'asset_class': 'hospital',
              'sources': [{'source': 'official-register', 'observed_at': '2026-09-20T00:00:00Z',
                           'fields': ['name', 'asset_class']} ]}
    candidate = ep.candidate_from_record(record)
    assert candidate.kind == 'hospital'
    assert candidate.provenance == tuple(record['sources'])
    assert candidate.approval is None
