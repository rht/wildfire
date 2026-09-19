"""forecast_input (CONTRACTS 2.2 v1.1): forecast-input-1 loading/validation, attach_forecast selection and
provenance, the committed synthetic forecast files, and the Deepfire adapter placeholder."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fireline import forecast_input
from fireline.forecast_input import attach_forecast, deepfire_spread_to_forecast, load_forecast, validate_forecast
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
    ({"input_mode": "live"}, "input_mode"),
    ({"forecast_source": ""}, "forecast_source"),
    ({"basis": ""}, "basis"),
    ({"note": "hand-made numbers"}, "synthetic forecast note"),
    ({"issued_at": "yesterday"}, "issued_at"),
    ({"forecast_horizon_at": at(-60)}, "before issued_at"),
    ({"estimates": []}, "estimates"),
    ({"estimates": {"fixture:a": {"arrival_p10_at": at(60)}}}, "keys must be exactly"),
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


# -------------------------------------------------------------------------------- extension point
def test_deepfire_adapter_is_an_unimplemented_extension_point():
    with pytest.raises(NotImplementedError):
        deepfire_spread_to_forecast({"type": "FeatureCollection", "features": []}, T0, asset_points={})
