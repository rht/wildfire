"""forecast_input (CONTRACTS 2.2 v1.1): forecast-input-1 loading/validation, attach_forecast selection and
provenance, the committed synthetic forecast files, and the Deepfire adapter placeholder."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shapely.geometry import mapping, box

from fireline import forecast_input
from fireline.forecast_input import (attach_forecast, deepfire_spread_to_forecast, forecast_from_recorded_spread,
                                     load_forecast, read_recorded_spread, validate_forecast)
from tests.helpers import make_asset

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
T0 = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)


def at(minutes, base=T0):
    return (base + timedelta(minutes=minutes)).isoformat()


def forecast(estimates=None, **over):
    fc = {"schema_version": "forecast-input-1", "forecast_source": "fixture:test_forecast", "input_mode": "synthetic",
          "issued_at": T0.isoformat(), "forecast_horizon_at": at(12 * 60), "basis": "p10",
          "note": "unit test; synthetic, not a provider forecast",
          "estimates": estimates if estimates is not None else {
              "fixture:a": {"arrival_p10_at": at(60), "arrival_p50_at": at(90), "burn_probability": 0.7}}}
    fc.update(over)
    return fc


def asset(asset_id="fixture:a", **over):
    return make_asset(asset_id=asset_id, **over)


# ------------------------------------------------------------------------------------ validation
def test_valid_forecast_passes_and_loads(tmp_path):
    fc = forecast()
    validate_forecast(fc)
    path = tmp_path / "f.json"
    path.write_text(json.dumps(fc))
    assert load_forecast(path) == fc


@pytest.mark.parametrize("change, match", [
    ({"schema_version": "forecast-input-2"}, "schema_version"),
    ({"input_mode": "guess"}, "input_mode"),
    ({"forecast_source": ""}, "forecast_source"),
    ({"basis": ""}, "basis"),
    ({"note": "hand-made numbers"}, "synthetic forecast note"),
    ({"issued_at": "yesterday"}, "issued_at"),
    ({"forecast_horizon_at": at(-60)}, "before issued_at"),
    ({"estimates": []}, "estimates"),
    ({"estimates": {"fixture:a": {"arrival_p10_at": at(60)}}}, "keys must be exactly"),
    ({"estimates": {"fixture:a": {"arrival_p10_at": None, "arrival_p50_at": None, "burn_probability": None, "arrival_at": at(13 * 60)}}}, "arrival_at is after"),
    ({"estimates": {"fixture:a": {"arrival_p10_at": at(60), "arrival_p50_at": at(90), "burn_probability": 0.7, "p90": 1}}}, "keys must be exactly"),
    ({"estimates": {"fixture:a": {"arrival_p10_at": at(90), "arrival_p50_at": at(60), "burn_probability": None}}}, "p10_at is after"),
    ({"estimates": {"fixture:a": {"arrival_p10_at": at(13 * 60), "arrival_p50_at": None, "burn_probability": None}}}, "after forecast_horizon_at"),
    ({"estimates": {"fixture:a": {"arrival_p10_at": "soon", "arrival_p50_at": None, "burn_probability": None}}}, "arrival_p10_at"),
    ({"estimates": {"fixture:a": {"arrival_p10_at": None, "arrival_p50_at": None, "burn_probability": 1.5}}}, "burn_probability"),
    ({"estimates": {"fixture:a": {"arrival_p10_at": None, "arrival_p50_at": None, "burn_probability": True}}}, "burn_probability"),
])
def test_validation_rejects_bad_files(change, match, tmp_path):
    fc = forecast(**change)
    with pytest.raises(ValueError, match=match):
        validate_forecast(fc)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(fc))
    with pytest.raises(ValueError, match=match):
        load_forecast(path)


def test_missing_key_and_non_json(tmp_path):
    fc = forecast()
    del fc["estimates"]
    with pytest.raises(ValueError, match="missing key estimates"):
        validate_forecast(fc)
    path = tmp_path / "junk.json"
    path.write_text("{not json")
    with pytest.raises(ValueError, match="not JSON"):
        load_forecast(path)
    with pytest.raises(ValueError):
        validate_forecast(["not", "a", "dict"])


def test_recorded_mode_does_not_need_the_synthetic_label():
    validate_forecast(forecast(input_mode="recorded", note="provider run 42"))


# ---------------------------------------------------------------------------------------- attach
def test_attach_selects_p10_over_p50_and_labels_basis():
    a, b, c = asset("fixture:a"), asset("fixture:b"), asset("fixture:c")
    fc = forecast({"fixture:a": {"arrival_p10_at": at(60), "arrival_p50_at": at(90), "burn_probability": 0.7},
                   "fixture:b": {"arrival_p10_at": None, "arrival_p50_at": at(120), "burn_probability": 0.4},
                   "fixture:c": {"arrival_p10_at": None, "arrival_p50_at": None, "burn_probability": 0.05}})
    assert attach_forecast([a, b, c], fc) is None   # in place
    assert (a["fire_arrival_at"], a["fire_arrival_basis"]) == (at(60), "p10")
    assert (a["arrival_p10_at"], a["arrival_p50_at"], a["burn_probability"]) == (at(60), at(90), 0.7)
    assert a["forecast_source"] == "fixture:test_forecast" and a["forecast_horizon_at"] == at(12 * 60)
    assert a["review_reasons"] == [] and a["needs_review"] is False
    assert (b["fire_arrival_at"], b["fire_arrival_basis"]) == (at(120), "p50") and b["arrival_p10_at"] is None
    assert b["review_reasons"] == []
    # an estimate with no arrival inside the horizon: provenance kept, arrival null, review reason set
    assert c["fire_arrival_at"] is None and c["fire_arrival_basis"] is None and c["burn_probability"] == 0.05
    assert c["forecast_source"] == "fixture:test_forecast"
    assert c["review_reasons"] == ["forecast_unavailable"] and c["needs_review"] is True


def test_attach_sources_entry_lists_exactly_the_fields_set():
    a, b = asset("fixture:a"), asset("fixture:b")
    fc = forecast({"fixture:a": {"arrival_p10_at": at(60), "arrival_p50_at": at(90), "burn_probability": 0.7},
                   "fixture:b": {"arrival_p10_at": None, "arrival_p50_at": at(120), "burn_probability": None}})
    attach_forecast([a, b], fc)
    sa, sb = a["sources"][-1], b["sources"][-1]
    assert sa["fields"] == ["burn_probability", "arrival_p10_at", "arrival_p50_at", "forecast_horizon_at",
                            "forecast_source", "fire_arrival_at", "fire_arrival_basis"]
    assert sb["fields"] == ["arrival_p50_at", "forecast_horizon_at", "forecast_source", "fire_arrival_at", "fire_arrival_basis"]
    for s in (sa, sb):
        assert s["source"] == "fixture:test_forecast" and s["observed_at"] == T0.isoformat()
        assert s["available_at"] is None and s["fetched_at"] is None
        assert "fixture:test_forecast" in s["notes"] and "forecast-input-1" in s["notes"]
        assert "synthetic, not a provider forecast" in s["notes"]
    assert "fire_arrival_at = arrival_p10_at" in sa["notes"] and "fire_arrival_at = arrival_p50_at" in sb["notes"]
    rec = forecast(input_mode="recorded", note="provider run")
    r = asset("fixture:a")
    attach_forecast([r], rec)
    assert "synthetic" not in r["sources"][-1]["notes"] and "recorded provider response" in r["sources"][-1]["notes"]


def test_missing_estimate_or_coordinates_gives_null_and_forecast_unavailable():
    absent = asset("fixture:absent", distance_to_fire_m=100.0)
    unlocated = asset("fixture:unlocated", latitude=None, longitude=None, distance_to_fire_m=None,
                      intersects_fire=None, review_reasons=["location_unknown", "exposure_unknown"])
    fc = forecast({"fixture:unlocated": {"arrival_p10_at": at(10), "arrival_p50_at": at(20), "burn_probability": 0.9}})
    attach_forecast([absent, unlocated], fc)
    for a in (absent, unlocated):
        assert a["fire_arrival_at"] is None and a["fire_arrival_basis"] is None
        assert a["arrival_p10_at"] is None and a["forecast_source"] is None and a["burn_probability"] is None
        assert "forecast_unavailable" in a["review_reasons"] and a["needs_review"] is True
        assert a["sources"] == []
    assert unlocated["review_reasons"] == ["location_unknown", "exposure_unknown", "forecast_unavailable"]
    attach_forecast([absent], fc)   # idempotent on the reason
    assert absent["review_reasons"].count("forecast_unavailable") == 1


def test_attach_rejects_invalid_forecast_before_touching_assets():
    a = asset()
    with pytest.raises(ValueError):
        attach_forecast([a], forecast(schema_version="nope"))
    assert a["fire_arrival_at"] is None and a["review_reasons"] == []


def test_attach_never_uses_distance():
    near, far = asset("fixture:near", distance_to_fire_m=100.0), asset("fixture:far", distance_to_fire_m=9000.0)
    fc = forecast({"fixture:far": {"arrival_p10_at": at(30), "arrival_p50_at": None, "burn_probability": None}})
    attach_forecast([near, far], fc)
    assert near["fire_arrival_at"] is None and "forecast_unavailable" in near["review_reasons"]
    assert far["fire_arrival_at"] == at(30)


# -------------------------------------------------------------------------------- committed files
def test_committed_synthetic_forecast_files():
    for seq, issued in ((1, T0), (2, T0 + timedelta(hours=2))):
        fc = load_forecast(FIX / "forecast" / f"synthetic_gavarres_{seq:04d}.json")
        assert fc["input_mode"] == "synthetic" and fc["basis"] == "p10"
        assert fc["issued_at"] == issued.isoformat()
        assert datetime.fromisoformat(fc["forecast_horizon_at"]) > issued
        assert forecast_input.SYNTHETIC_LABEL in fc["note"] and "readme section 11" in fc["note"]
        assert "fixture:vall_repos" not in fc["estimates"] and "fixture:residencia_sense_coordenades" not in fc["estimates"]
        assert all(e["arrival_p10_at"] is not None and e["arrival_p10_at"] <= e["arrival_p50_at"] for e in fc["estimates"].values())
        assert fc["estimates"]["fixture:monells"]["arrival_p10_at"] == fc["estimates"]["fixture:sant_sadurni"]["arrival_p10_at"]
    fc2 = load_forecast(FIX / "forecast" / "synthetic_gavarres_0002.json")
    assert fc2["estimates"]["fixture:sant_pol"]["arrival_p10_at"] == fc2["issued_at"]   # inside the perimeter


# ------------------------------------------------------------------------------- single estimate
def test_attach_selects_arrival_at_last_with_the_forecast_basis():
    a, b = asset("fixture:a"), asset("fixture:b")
    fc = forecast({"fixture:a": {"arrival_p10_at": None, "arrival_p50_at": None, "burn_probability": None, "arrival_at": at(45)},
                   "fixture:b": {"arrival_p10_at": None, "arrival_p50_at": at(80), "burn_probability": None, "arrival_at": at(45)}},
                  input_mode="recorded", basis="hourly isochrone crossing, deterministic elmfire, t0 = createdAt", note="run")
    attach_forecast([a, b], fc)
    assert a["fire_arrival_at"] == at(45) and a["fire_arrival_basis"] == fc["basis"]
    assert a["arrival_p10_at"] is None and a["arrival_p50_at"] is None
    assert a["sources"][-1]["fields"] == ["forecast_horizon_at", "forecast_source", "fire_arrival_at", "fire_arrival_basis"]
    assert "fire_arrival_at = arrival_at (hourly isochrone crossing" in a["sources"][-1]["notes"]
    assert "recorded provider response" in a["sources"][-1]["notes"]
    assert (b["fire_arrival_at"], b["fire_arrival_basis"]) == (at(80), "p50")   # quantiles win over the single estimate


# ------------------------------------------------------------------------ Deepfire fire-spread
CREATED = "2026-09-19T13:49:18.165734Z"
T_CREATED = datetime(2026, 9, 19, 13, 49, 18, 165734, tzinfo=timezone.utc)


def spread_body(features, members=1, status="COMPLETED", hours=12):
    return {"id": "run-1", "status": status, "model": "elmfire", "durationHours": hours, "ensembleMembers": members,
            "latitude": 41.89134, "longitude": 3.02283, "locationName": "Cruïlles, Girona", "ignitionPointCount": 1,
            "ignition": {"type": "FeatureCollection", "features": []}, "createdAt": CREATED,
            "summary": {"burnedAreaM2": 1.0, "edgeReached": False},
            "result": {"type": "FeatureCollection", "features": features}, "links": {}}


def feat(geom, hour, **props):
    return {"type": "Feature", "geometry": mapping(geom), "properties": dict({"hour": hour, "elapsed_seconds": 3600 * hour}, **props)}


def arrived(fc):
    """asset ids the forecast gives an arrival, i.e. covered by the thresholded hourly union."""
    return {k for k, e in fc["estimates"].items() if e["arrival_at"] is not None}


def test_adapter_first_covering_hour_gives_arrival_and_uncovered_gets_zero_probability():
    h1, h2 = box(3.0, 41.9, 3.01, 41.91), box(3.0, 41.9, 3.03, 41.93)     # cumulative, nested
    body = spread_body([feat(h2, 2), feat(h1, 1)])                          # out of order on purpose
    pts = {"a:hour1": (3.005, 41.905), "a:hour2": (3.02, 41.92), "a:never": (3.1, 41.95)}
    fc = deepfire_spread_to_forecast(body, T_CREATED, pts)
    validate_forecast(fc)
    assert fc["input_mode"] == "recorded" and fc["forecast_source"] == "deepfire:fire-spread/elmfire/run-1"
    assert fc["issued_at"] == T_CREATED.isoformat() and fc["forecast_horizon_at"] == (T_CREATED + timedelta(hours=12)).isoformat()
    assert fc["basis"] == ("hourly isochrone crossing, deterministic elmfire, burn probability 1.0 inside the "
                           "burned area and 0.0 outside it, t0 = createdAt")
    assert set(fc["estimates"]) == {"a:hour1", "a:hour2", "a:never"}   # every located asset gets an estimate
    assert fc["estimates"]["a:hour1"]["arrival_at"] == (T_CREATED + timedelta(hours=1)).isoformat()
    assert fc["estimates"]["a:hour2"]["arrival_at"] == (T_CREATED + timedelta(hours=2)).isoformat()
    assert fc["estimates"]["a:hour1"]["arrival_p10_at"] is None and fc["estimates"]["a:hour1"]["arrival_p50_at"] is None
    # a deterministic run carries no band probability: covered is 1.0, uncovered is 0.0, never null
    assert fc["estimates"]["a:hour1"]["burn_probability"] == 1.0
    assert fc["estimates"]["a:hour2"]["burn_probability"] == 1.0
    assert fc["estimates"]["a:never"] == {"arrival_p10_at": None, "arrival_p50_at": None,
                                          "burn_probability": 0.0, "arrival_at": None}
    for needle in ("run-1", "1 member(s)", "41.89134,3.02283", "Cruïlles, Girona", "t0 = createdAt",
                   "hour 2 keep arrival_at null", "0.0 when no band covers it", "not a distance inference"):
        assert needle in fc["note"], needle
    rec = asset("a:hour2"); never = asset("a:never")
    attach_forecast([rec, never], fc)
    assert rec["fire_arrival_at"] == (T_CREATED + timedelta(hours=2)).isoformat() and rec["fire_arrival_basis"] == fc["basis"]
    assert rec["burn_probability"] == 1.0
    # no arrival, but the run does cover this location and says 0.0 there: still forecast_unavailable
    assert never["fire_arrival_at"] is None and "forecast_unavailable" in never["review_reasons"]
    assert never["burn_probability"] == 0.0 and never["forecast_source"] == "deepfire:fire-spread/elmfire/run-1"
    live = deepfire_spread_to_forecast(body, T_CREATED, pts, input_mode="live")
    assert live["input_mode"] == "live"


def test_adapter_ensemble_unions_bands_above_the_member_fraction():
    core = box(3.0, 41.9, 3.01, 41.91)                # p = 1.0 (all 5 members)
    fringe = box(3.01, 41.9, 3.02, 41.91)             # p = 0.4 (2 of 5)
    outer = box(3.02, 41.9, 3.03, 41.91)              # p = 0.2 (1 of 5)
    body = spread_body([feat(core, 1, burn_probability=1.0), feat(fringe, 1, burn_probability=0.4),
                        feat(outer, 1, burn_probability=0.2)], members=5)
    pts = {"p:core": (3.005, 41.905), "p:fringe": (3.015, 41.905), "p:outer": (3.025, 41.905)}
    any_member = deepfire_spread_to_forecast(body, T_CREATED, pts)          # default 1/5: any member
    assert set(any_member["estimates"]) == {"p:core", "p:fringe", "p:outer"}
    assert arrived(any_member) == {"p:core", "p:fringe", "p:outer"}
    assert any_member["basis"] == ("hourly isochrone crossing, member fraction >= 0.2, burn probability = "
                                   "max band probability covering the point, elmfire, t0 = createdAt")
    assert "union of bands with burn_probability >= 0.2" in any_member["note"]
    at_04 = deepfire_spread_to_forecast(body, T_CREATED, pts, min_burn_probability=0.4)
    assert arrived(at_04) == {"p:core", "p:fringe"}
    all_members = deepfire_spread_to_forecast(body, T_CREATED, pts, min_burn_probability=1.0)
    assert arrived(all_members) == {"p:core"}
    # the member fraction selects the arrival union only: every band counts towards burn_probability,
    # so raising the threshold never lowers a location's probability
    for fc in (any_member, at_04, all_members):
        assert set(fc["estimates"]) == {"p:core", "p:fringe", "p:outer"}
        probs = {k: e["burn_probability"] for k, e in fc["estimates"].items()}
        assert probs == {"p:core": 1.0, "p:fringe": 0.4, "p:outer": 0.2}


def test_adapter_two_member_band_probability_is_kept_per_asset():
    """Handoff 002 Fix A: one asset inside a 0.5 band, one outside every band -> 0.5 and 0.0."""
    half = box(3.0, 41.9, 3.01, 41.91)                # 1 of 2 members
    body = spread_body([feat(half, 1, burn_probability=0.5)], members=2)
    pts = {"m:in_half": (3.005, 41.905), "m:outside": (3.2, 41.95)}
    fc = deepfire_spread_to_forecast(body, T_CREATED, pts)
    validate_forecast(fc)
    assert fc["estimates"]["m:in_half"]["burn_probability"] == 0.5
    assert fc["estimates"]["m:outside"]["burn_probability"] == 0.0
    assert arrived(fc) == {"m:in_half"}               # default member fraction 1/2 = 0.5
    a, b = asset("m:in_half"), asset("m:outside")
    attach_forecast([a, b], fc)
    assert a["burn_probability"] == 0.5 and b["burn_probability"] == 0.0
    assert "max band probability covering the point" in fc["basis"]
    assert "multiples of 1/2" in fc["note"]


def test_adapter_burn_probability_is_the_maximum_over_hours():
    early = box(3.0, 41.9, 3.02, 41.92)               # hour 1, 1 of 5 members
    late = box(3.0, 41.9, 3.02, 41.92)                # hour 3, 3 of 5 members over the same ground
    body = spread_body([feat(early, 1, burn_probability=0.2), feat(late, 3, burn_probability=0.6)], members=5)
    fc = deepfire_spread_to_forecast(body, T_CREATED, {"h:both": (3.01, 41.91)})
    assert fc["estimates"]["h:both"]["burn_probability"] == 0.6          # the maximum, not the first hour
    assert fc["estimates"]["h:both"]["arrival_at"] == (T_CREATED + timedelta(hours=1)).isoformat()   # still the earliest


def test_adapter_zero_probability_estimate_carries_provenance_and_stays_unavailable():
    body = spread_body([feat(box(3.0, 41.9, 3.01, 41.91), 1, burn_probability=0.5)], members=2)
    fc = deepfire_spread_to_forecast(body, T_CREATED, {"z:outside": (3.2, 41.95)})
    validate_forecast(fc)                                                # 0.0 is a valid burn_probability
    rec = asset("z:outside")
    attach_forecast([rec], fc)
    assert rec["burn_probability"] == 0.0 and rec["fire_arrival_at"] is None and rec["fire_arrival_basis"] is None
    assert rec["forecast_source"] == "deepfire:fire-spread/elmfire/run-1"   # non-null: validate_snapshot needs it
    assert rec["forecast_horizon_at"] == (T_CREATED + timedelta(hours=12)).isoformat()
    assert rec["review_reasons"] == ["forecast_unavailable"] and rec["needs_review"] is True
    # 0.0 is not None, so the provenance entry names burn_probability
    assert rec["sources"][-1]["fields"] == ["burn_probability", "forecast_horizon_at", "forecast_source"]
    assert "no arrival within the horizon" in rec["sources"][-1]["notes"]


def test_adapter_rejects_incomplete_runs_and_bad_bodies():
    body = spread_body([feat(box(3.0, 41.9, 3.01, 41.91), 1)], status="RUNNING")
    with pytest.raises(ValueError, match="not COMPLETED"):
        deepfire_spread_to_forecast(body, T_CREATED, {})
    with pytest.raises(ValueError, match="missing createdAt"):
        deepfire_spread_to_forecast({k: v for k, v in spread_body([]).items() if k != "createdAt"}, T_CREATED, {})
    with pytest.raises(ValueError, match="hour/elapsed_seconds"):
        deepfire_spread_to_forecast(spread_body([{"type": "Feature", "geometry": mapping(box(3, 41.9, 3.01, 41.91)), "properties": {}}]), T_CREATED, {})
    with pytest.raises(ValueError, match="input_mode"):
        deepfire_spread_to_forecast(spread_body([]), T_CREATED, {}, input_mode="synthetic")
    empty = deepfire_spread_to_forecast(spread_body([]), T_CREATED, {"x": (3.0, 41.9)})
    assert empty["estimates"] == {"x": {"arrival_p10_at": None, "arrival_p50_at": None,
                                        "burn_probability": 0.0, "arrival_at": None}}


def test_adapter_never_uses_distance():
    body = spread_body([feat(box(3.0, 41.9, 3.01, 41.91), 1)])
    fc = deepfire_spread_to_forecast(body, T_CREATED, {"just_outside": (3.0101, 41.905)})   # ~10 m off the polygon
    # containment only: a point 10 m outside is outside, with no arrival and no probability borrowed
    # from how near it is
    assert fc["estimates"] == {"just_outside": {"arrival_p10_at": None, "arrival_p50_at": None,
                                                "burn_probability": 0.0, "arrival_at": None}}


REAL_DIR = FIX / "fire" / "deepfire" / "real"
REAL_RUNS = {
    "20260919T135029Z_fire-spread-simulation-latlon_12h_1member.json": ("4bbd8e98-1614-454e-aa6c-7d61d401b0ab", 1, 12),
    "20260919T135030Z_fire-spread-simulation-latlon_12h_5members.json": ("c948ef24-6434-4273-8c19-fd2b4fad7b5b", 5, 22),
}


@pytest.mark.parametrize("name", sorted(REAL_RUNS))
def test_recorded_real_fire_spread_runs_parse_into_valid_forecasts(name):
    run_id, members, n_features = REAL_RUNS[name]
    rec = read_recorded_spread(REAL_DIR / name)
    body = rec["body"]
    assert body["status"] == "COMPLETED" and body["id"] == run_id and body["ensembleMembers"] == members
    assert body["model"] == "elmfire" and body["durationHours"] == 12 and len(body["result"]["features"]) == n_features
    assert (body["latitude"], body["longitude"]) == (41.89134, 3.02283)
    assert all({"hour", "elapsed_seconds"} <= set(ft["properties"]) for ft in body["result"]["features"])
    if members > 1:
        assert all("burn_probability" in ft["properties"] for ft in body["result"]["features"])
    # a point at the ignition is inside hour 1; the real Gavarres facilities are all outside the 12 h area
    ign = {"probe:ignition": (body["longitude"], body["latitude"])}
    fc = forecast_from_recorded_spread(REAL_DIR / name, ign)
    validate_forecast(fc)
    assert fc["input_mode"] == "recorded" and fc["forecast_source"] == f"deepfire:fire-spread/elmfire/{run_id}"
    assert fc["issued_at"] == "2026-09-19T13:49:18.165734+00:00" if members == 1 else fc["issued_at"].startswith("2026-09-19T13:49")
    assert fc["estimates"]["probe:ignition"]["arrival_at"] == (datetime.fromisoformat(fc["issued_at"]) + timedelta(hours=1)).isoformat()
    assert fc["estimates"]["probe:ignition"]["burn_probability"] == 1.0   # every member burns the ignition cell
    real = json.loads((FIX / "real_area" / "assets_gavarres.json").read_text())["assets"]
    pts = {a["asset_id"]: (a["longitude"], a["latitude"]) for a in real if a["latitude"] is not None}
    covered = forecast_from_recorded_spread(REAL_DIR / name, pts, min_burn_probability=0.2 if members > 1 else None)
    # about 20 ha burned, nearest facility 5.3 km away (stated in the READMEs): no facility is reached,
    # which the run states as burn_probability 0.0 with no arrival, not as a missing estimate
    assert len(pts) == 99 and len(covered["estimates"]) == 99
    assert all(e["arrival_at"] is None and e["burn_probability"] == 0.0 for e in covered["estimates"].values())
    with pytest.raises(ValueError, match="not a fire-spread"):
        read_recorded_spread(REAL_DIR / "20260919T131340Z_clusters.json")


def test_adapter_null_burn_probability_is_deterministic_and_receipt_time_is_recorded():
    body = spread_body([feat(box(3.0, 41.9, 3.01, 41.91), 1, burn_probability=None)])
    fc = deepfire_spread_to_forecast(body, T_CREATED, {"a:p": (3.005, 41.905)})
    assert fc["estimates"]["a:p"]["arrival_at"] == (T_CREATED + timedelta(hours=1)).isoformat()
    assert fc["received_at"] == T_CREATED.isoformat()
    rec = asset("a:p")
    attach_forecast([rec], fc)
    assert rec["sources"][-1]["fetched_at"] == T_CREATED.isoformat() and rec["sources"][-1]["available_at"] == T_CREATED.isoformat()
