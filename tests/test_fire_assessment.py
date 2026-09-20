"""The fire producer bounds discovery without manufacturing operational facts."""

import json
from copy import deepcopy

import pytest

from fireline import config, feeds
from fireline.fire_assessment import assess_fire
from fireline.snapshot import validate_snapshot

NOW = "2026-09-20T10:00:00+00:00"
FIRE = {"incident_id": "incident-1", "source": "deepfire:clusters",
        "observed_at": NOW, "received_at": NOW,
        "geometry": {"type": "Point", "coordinates": [3.0, 42.0]},
        "geometry_kind": "hotspot_centre"}


def assess(rows=None, **kwargs):
    options = {"scenario_id": "assessment", "sequence": 1, "as_of": NOW,
               "input_mode": "live", "search_radius_m": 1000, "facility_rows": rows}
    options.update(kwargs)
    return assess_fire(FIRE, **options)


def row(aid="near", lon=3.005, lat=42.0, **kwargs):
    return {"asset_id": aid, "name": aid, "asset_class": "hospital", "lon": lon,
            "lat": lat, "register": "test-catalog", **kwargs}


def forecast(mode="live"):
    return {"schema_version": "forecast-input-1", "input_mode": mode,
            "forecast_source": "provider:model", "issued_at": NOW,
            "forecast_horizon_at": "2026-09-20T15:00:00+00:00",
            "basis": "provider arrival quantiles",
            "note": "synthetic, not a provider forecast" if mode == "synthetic" else "provider run",
            "estimates": {"near": {"arrival_p10_at": "2026-09-20T11:00:00+00:00",
                                    "arrival_p50_at": "2026-09-20T12:00:00+00:00",
                                    "burn_probability": 0.5}}}


def test_catalog_filters_radius_and_retains_unknown_locations_for_review():
    result = assess([row(), row("far", lon=3.1), row("unknown", lon=None, lat=None),
                     row("bbox-corner", lon=3.01, lat=42.008)])
    snap = result["snapshot"]
    assert validate_snapshot(snap) == []
    assert [a["asset_id"] for a in snap["assets"]] == ["near", "unknown"]
    assert 410 < snap["assets"][0]["distance_to_fire_m"] < 420
    assert "location_unknown" in snap["assets"][1]["review_reasons"]
    assert result["discovery"]["unlocated_asset_ids"] == ["unknown"]
    assert result["discovery"]["excluded_count"] == 2


def test_provider_adapter_uses_geographic_bbox_and_registered_facility_conversion(monkeypatch):
    queries = []

    def socrata(dataset, params):
        queries.append((dataset, params))
        return [{"idequipament": "123", "nom": "Hospital", "categoria": "3. Hospitals",
                 "longitud": "3.005", "latitud": "42.0"},
                {"idequipament": "999", "nom": "Far hospital", "categoria": "3. Hospitals",
                 "longitud": "3.1", "latitud": "42.0"}]

    monkeypatch.setattr(feeds, "socrata", socrata)
    result = assess()
    assert [a["asset_id"] for a in result["snapshot"]["assets"]] == ["equipaments:123"]
    bbox = result["discovery"]["bbox_wgs84"]
    assert 2.98 < bbox[0] < 3.0 < bbox[2] < 3.02
    assert 41.98 < bbox[1] < 42.0 < bbox[3] < 42.02
    assert queries[0][0] == feeds.SOCRATA_DATASETS["equipaments"]
    assert "within_box(localitzacio," in queries[0][1]["$where"]
    assert "private homes" in result["discovery"]["coverage"]
    assert result["snapshot"]["assets"][0]["sources"][0]["fetched_at"]


def test_no_forecast_or_occupancy_stays_unknown_and_values_keep_policy_provenance():
    before = deepcopy(config.FEATURES)
    rec = assess([row()])["snapshot"]["assets"][0]
    assert rec["fire_arrival_at"] is None
    assert rec["burn_probability"] is None
    assert rec["estimated_occupancy"] is None
    assert rec["people_exposed"] is None
    assert rec["expected_loss_eur_mid"] is None
    assert rec["replacement_value_eur"] > 0
    assert {"forecast_unavailable", "occupancy_unknown"} <= set(rec["review_reasons"])
    assert any(s["source"] == "config.VALUE_AT_RISK_POLICY" for s in rec["sources"])
    assert config.FEATURES == before


def test_explicit_forecast_supplies_risk_without_mutating_catalog():
    rows = [row(occupancy=8, occupancy_source="headcount")]
    original = deepcopy(rows)
    rec = assess(rows, forecast=forecast())["snapshot"]["assets"][0]
    assert rec["fire_arrival_at"] == "2026-09-20T11:00:00+00:00"
    assert rec["burn_probability"] == 0.5
    assert rec["expected_loss_eur_mid"] > 0
    assert rec["people_exposed"] == 4
    assert rows == original


@pytest.mark.parametrize("mode", ["recorded", "synthetic"])
def test_nonlive_forecast_cannot_be_promoted_to_live(mode):
    with pytest.raises(ValueError, match="input_mode"):
        assess([row()], forecast=forecast(mode))


@pytest.mark.parametrize("options", [
    {"search_radius_m": 0}, {"search_radius_m": -1}, {"search_radius_m": 50001},
    {"search_radius_m": float("nan")}, {"search_radius_m": True},
    {"sequence": True}, {"sequence": 1.5}, {"sequence": 0},
    {"as_of": "yesterday"}, {"as_of": "2026-09-20T10:00:00"},
    {"input_mode": "production"}, {"scenario_id": ""},
])
def test_invalid_envelope_or_radius_is_rejected_before_fetch(options):
    def unexpected_fetch(**kwargs):
        pytest.fail("invalid input must not cause a provider request")

    with pytest.raises(ValueError):
        assess(fetcher=unexpected_fetch, **options)


@pytest.mark.parametrize("geometry", [None, {"type": "Point", "coordinates": [181, 42]},
    {"type": "Point", "coordinates": [3, float("nan")]},
    {"type": "LineString", "coordinates": [[3, 42], [3.1, 42]]},
    {"type": "Polygon", "coordinates": []}])
def test_invalid_fire_geometry_rejected(geometry):
    with pytest.raises(ValueError, match="geometry"):
        assess_fire({**FIRE, "geometry": geometry}, scenario_id="s", sequence=1,
                    as_of=NOW, input_mode="live", search_radius_m=1000, facility_rows=[])


def test_v11_catalog_uses_footprint_distance_and_preserves_occupancy_source():
    footprint = {"type": "Polygon", "coordinates": [[[2.999, 41.999], [3.001, 41.999],
                 [3.001, 42.001], [2.999, 42.001], [2.999, 41.999]]]}
    asset = {"asset_id": "building", "asset_type": "hospital", "longitude": 3.1,
             "latitude": 42.0, "geometry": footprint, "estimated_occupancy": 3,
             "occupancy_basis": "confirmed headcount", "sources": [
                 {"fields": ["estimated_occupancy"], "source": "operator", "observed_at": NOW,
                  "available_at": NOW, "fetched_at": NOW, "notes": "confirmed headcount"}]}
    result = assess([asset])
    rec = result["snapshot"]["assets"][0]
    assert rec["intersects_fire"] is True
    assert rec["distance_to_fire_m"] == 0
    assert rec["estimated_occupancy"] == 3
    assert any(s["source"] == "operator" for s in rec["sources"])


def test_recorded_spread_converts_arrival_and_is_not_silently_live():
    body = {"id": "run-1", "status": "COMPLETED", "model": "test-model",
            "durationHours": 3, "ensembleMembers": 1, "createdAt": NOW,
            "result": {"features": [{"type": "Feature", "properties": {
                "hour": 1, "elapsed_seconds": 3600}, "geometry": {"type": "Polygon",
                "coordinates": [[[2.9, 41.9], [3.1, 41.9], [3.1, 42.1],
                                 [2.9, 42.1], [2.9, 41.9]]]}}]}}
    spread = {"body": body, "received_at": NOW, "input_mode": "recorded"}
    result = assess([row()], input_mode="recorded", spread=spread)
    assert result["snapshot"]["assets"][0]["fire_arrival_at"] == "2026-09-20T11:00:00+00:00"
    with pytest.raises(ValueError, match="input_mode"):
        assess([row()], spread=spread)


def test_synthetic_fire_label_cannot_be_promoted_to_live():
    with pytest.raises(ValueError, match="live"):
        assess_fire({**FIRE, "source": "fixture:synthetic_ignition"}, scenario_id="s",
                    sequence=1, as_of=NOW, input_mode="live", search_radius_m=1000,
                    facility_rows=[])


def test_malformed_polygon_is_a_validation_error():
    with pytest.raises(ValueError, match="geometry"):
        assess_fire({**FIRE, "geometry": {"type": "Polygon", "coordinates": [[[3, 42], [4, 42]]] }},
                    scenario_id="s", sequence=1, as_of=NOW, input_mode="live",
                    search_radius_m=1000, facility_rows=[])


def test_malformed_forecast_timestamp_is_a_validation_error_before_fetch():
    malformed = forecast()
    malformed["issued_at"] = 123
    with pytest.raises(ValueError, match="forecast"):
        assess([], forecast=malformed)


def test_polygon_search_measures_from_its_edge_not_its_centroid():
    fire = {**FIRE, "geometry_kind": "perimeter", "geometry": {
        "type": "Polygon", "coordinates": [[[2.9, 41.99], [3.1, 41.99],
            [3.1, 42.01], [2.9, 42.01], [2.9, 41.99]]]}}
    result = assess_fire(fire, scenario_id="s", sequence=1, as_of=NOW, input_mode="live",
                         search_radius_m=1000, facility_rows=[row("edge", lon=3.105),
                                                             row("far", lon=3.13)])
    assert [a["asset_id"] for a in result["snapshot"]["assets"]] == ["edge"]
    assert result["discovery"]["bbox_wgs84"][2] > 3.11


def test_invalid_catalog_coordinate_and_duplicate_ids_do_not_escape_schema_checks():
    with pytest.raises(ValueError, match="coordinates"):
        assess([row(lon=float("inf"))])
    with pytest.raises(ValueError, match="duplicate asset_id"):
        assess([row(), row()])


def test_empty_catalog_does_not_fetch_and_empty_matches_are_valid():
    def unexpected_fetch(**kwargs):
        pytest.fail("an explicitly supplied empty catalog must not fetch")

    result = assess([], fetcher=unexpected_fetch)
    assert result["snapshot"]["assets"] == []
    assert validate_snapshot(result["snapshot"]) == []


def test_capacity_is_never_presented_as_current_occupancy():
    rec = assess([row(occupancy=500, occupancy_source="register")])["snapshot"]["assets"][0]
    assert rec["capacity"] == 500
    assert rec["estimated_occupancy"] is None
    assert rec["people_exposed"] is None


def test_unclassified_registered_facility_is_retained_without_private_fields():
    raw = {"idequipament": "library-12", "nom": "Municipal Library", "categoria": "Biblioteques",
           "localitzacio": {"coordinates": [3.005, 42.0]}, "telefon": "+34999111222",
           "email": "private@example.test", "adreca": "Private address sentinel",
           "capacity": 500, "occupancy": 250}
    result = assess(fetcher=lambda **kwargs: [raw])
    assert len(result["snapshot"]["assets"]) == 1
    rec = result["snapshot"]["assets"][0]
    assert rec["asset_id"] == "equipaments:library-12"
    assert rec["name"] == "Municipal Library"
    assert rec["asset_type"] == "unknown"
    assert rec["latitude"] == 42.0 and rec["longitude"] == 3.005
    for key in ("capacity", "estimated_occupancy", "value_score", "replacement_value_eur",
                "fire_arrival_at", "evacuation_min"):
        assert rec[key] is None
    assert {"class_ambiguous", "value_unknown", "forecast_unavailable", "occupancy_unknown"} <= set(rec["review_reasons"])
    assert rec["sources"][0]["source"] == "equipaments"
    assert rec["sources"][0]["fetched_at"]
    serialized = json.dumps(result)
    for private in (raw["telefon"], raw["email"], raw["adreca"]):
        assert private not in serialized
    assert "unknown classes" in result["discovery"]["coverage"]


def test_unknown_facilities_share_radius_filter_and_unlocated_review_path():
    raw = [{"idequipament": "far", "nom": "Far library", "categoria": "Biblioteques",
            "longitud": 3.1, "latitud": 42.0},
           {"idequipament": "unlocated", "nom": "Unlocated library", "categoria": "Biblioteques"}]
    result = assess(fetcher=lambda **kwargs: raw)
    assert [a["asset_id"] for a in result["snapshot"]["assets"]] == ["equipaments:unlocated"]
    assert result["discovery"]["excluded_count"] == 1
    assert result["discovery"]["unlocated_asset_ids"] == ["equipaments:unlocated"]
    assert "location_unknown" in result["snapshot"]["assets"][0]["review_reasons"]


@pytest.mark.parametrize("identity", [None, "", "  ", False, {}, []])
@pytest.mark.parametrize("category", ["Biblioteques", "3. Hospitals"])
def test_fetched_register_rows_require_stable_public_identity(identity, category):
    raw = {"idequipament": identity, "nom": "Facility", "categoria": category,
           "longitud": 3.005, "latitud": 42.0}
    with pytest.raises(ValueError, match="idequipament"):
        assess(fetcher=lambda **kwargs: [raw])
