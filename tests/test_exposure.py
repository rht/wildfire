"""Exposure: tiering on a synthetic raster, deterministic edge cases, pessimistic overrides."""

import math

import numpy as np
import pytest

from fireline import config
from fireline.exposure import OptimisticMoveError, apply_override, build_asset_table, tier_for
from fireline.grid import Grid, lonlat_to_xy
from fireline.spread import ArrivalRaster

IGNITION = (3.03, 41.95)   # south of la Bisbal d'Empordà


def make_arrival(grid=None, ign_lon=IGNITION[0], ign_lat=IGNITION[1], head_bearing_deg=175.0,
                 head_speed=48.0, base_speed=3.0, exponent=6, horizon_min=720) -> ArrivalRaster:
    """Synthetic anisotropic arrival raster: arrival = distance / speed(bearing). Speed (m/min) is
    `head_speed` down the head bearing (SSE under tramuntana) falling to `base_speed` upwind.
    burn_prob falls linearly with p10 arrival; cells with burn_prob < 0.1 never burn (inf), matching
    the ArrivalRaster contract. No CA involved, so tests do not depend on spread.run_ca."""
    grid = grid or Grid.gavarres(cell=100)
    ix, iy = lonlat_to_xy(ign_lon, ign_lat)
    rows, cols = np.mgrid[0:grid.nrows, 0:grid.ncols]
    x, y = grid.to_xy(rows, cols)
    dx, dy = x - ix, y - iy
    dist = np.hypot(dx, dy)
    bearing = np.degrees(np.arctan2(dx, dy)) % 360.0
    dtheta = np.radians(bearing - head_bearing_deg)
    speed = base_speed + (head_speed - base_speed) * np.clip(np.cos(dtheta), 0, None) ** exponent
    p50 = dist / speed
    p10 = p50 / 1.25
    p90 = p50 * 1.25
    bp = np.clip(1.25 - p10 / 400.0, 0.0, 1.0)
    burned = bp >= 0.10
    p10 = np.where(burned, p10, np.inf)
    p50 = np.where(burned, p50, np.inf)
    p90 = np.where(burned, p90, np.inf)
    return ArrivalRaster(grid, p10, p50, p90, bp, "synthetic", horizon_min)


def make_no_fire(grid=None) -> ArrivalRaster:
    grid = grid or Grid.gavarres(cell=100)
    inf = np.full(grid.shape, np.inf)
    return ArrivalRaster(grid, inf.copy(), inf.copy(), inf.copy(), np.zeros(grid.shape), "synthetic", 720)


@pytest.fixture(scope="module")
def arrival():
    return make_arrival()


def _asset(aid, cls, lon, lat, **kw):
    d = {"asset_id": f"test:{aid}", "name": aid, "asset_class": cls, "lon": lon, "lat": lat,
         "municipality": "test"}
    d.update(kw)
    return d


def test_tier_rule_thresholds():
    assert tier_for(config.ACT_NOW_MIN, config.BURN_PROB_MIN) == "act_now"
    assert tier_for(config.ACT_NOW_MIN + 1, config.BURN_PROB_MIN) == "prepare"
    assert tier_for(config.PREPARE_MIN, config.BURN_PROB_MIN) == "prepare"
    assert tier_for(config.PREPARE_MIN + 1, 1.0) == "monitor"
    assert tier_for(0, config.BURN_PROB_MIN - 0.01) == "monitor"
    assert tier_for(math.inf, 0.0) == "monitor"


def test_tiers_on_synthetic_raster(arrival):
    rows = build_asset_table([
        _asset("near", "nucleus", 3.036, 41.942, occupancy=85, occupancy_source="allocated"),   # ~1 km SE
        _asset("mid", "nucleus", 3.060, 41.860, occupancy=120),                                  # ~10 km S
        _asset("far_west", "nucleus", 2.874, 41.889, occupancy=1000),                            # Cassà, upwind
    ], arrival)
    by = {r["name"]: r for r in rows}
    assert [r["asset_id"] for r in rows] == ["test:near", "test:mid", "test:far_west"]  # order preserved
    assert by["near"]["tier"] == "act_now"
    assert by["mid"]["tier"] == "prepare"
    assert by["far_west"]["tier"] == "monitor"
    assert math.isinf(by["far_west"]["arrival_p10_min"]) and by["far_west"]["burn_prob"] == 0.0
    n = by["near"]
    assert n["lead_time_min"] == config.LEAD_TIME_MIN["nucleus"]
    assert n["lead_adjusted_p10_min"] == pytest.approx(n["arrival_p10_min"] - 60)
    assert 0 < n["arrival_p10_min"] < n["arrival_p50_min"]
    assert n["burn_prob"] == 1.0
    assert n["occupancy"] == 85 and n["occupancy_source"] == "allocated"
    assert by["mid"]["occupancy_source"] == "register"       # occupancy given, source not: register
    assert n["needs_review"] == [] and n["notes"] == [] and n["overrides"] == []


def test_needs_review_codes(arrival):
    rows = build_asset_table([
        _asset("camp", "camp", 3.040, 41.933, occupancy=None, seasonal=True),
        _asset("campsite", "campsite", 3.075, 41.885, occupancy=200, class_ambiguous=True),
        _asset("plain", "masia", 3.03, 41.923, occupancy=4),
    ], arrival)
    assert rows[0]["needs_review"] == ["occupancy_unknown", "occupancy_seasonal"]
    assert rows[0]["occupancy"] is None and rows[0]["occupancy_source"] == "unknown"
    assert rows[1]["needs_review"] == ["class_ambiguous"]
    assert rows[2]["needs_review"] == []
    for r in rows:
        assert "no_exit" not in r["needs_review"]   # only scenario.py adds no_exit


def test_location_conflict_uses_earlier_arrival(arrival):
    # primary location upwind (late), alternate 2 km downwind (early): the alternate wins, with a note
    rows = build_asset_table([
        _asset("conflict", "nucleus", 2.975, 41.955, occupancy=10, alt_lon=3.040, alt_lat=41.933),
    ], arrival)
    r = rows[0]
    assert (r["lon"], r["lat"]) == (3.040, 41.933)
    assert r["tier"] == "act_now"
    assert any("location conflict" in n and "alternate" in n for n in r["notes"])
    # and the other way round keeps the primary
    rows = build_asset_table([
        _asset("conflict", "nucleus", 3.040, 41.933, occupancy=10, alt_lon=2.975, alt_lat=41.955),
    ], arrival)
    assert (rows[0]["lon"], rows[0]["lat"]) == (3.040, 41.933)
    assert any("using primary" in n for n in rows[0]["notes"])


def test_outside_grid_is_monitor_with_note(arrival):
    rows = build_asset_table([_asset("bcn", "nucleus", 2.17, 41.38, occupancy=1)], arrival)
    assert rows[0]["tier"] == "monitor"
    assert rows[0]["burn_prob"] == 0.0 and math.isinf(rows[0]["arrival_p10_min"])
    assert any("outside" in n for n in rows[0]["notes"])


def test_unknown_class_raises(arrival):
    with pytest.raises(ValueError):
        build_asset_table([_asset("x", "castle", 3.0, 41.9)], arrival)


def test_apply_override_pessimistic_moves(arrival):
    row = build_asset_table([_asset("camp", "camp", 3.038, 41.962, occupancy=None, seasonal=True)], arrival)[0]
    assert row["tier"] == "prepare"
    apply_override(row, {"field": "occupancy", "value": 80, "source": "operator page",
                         "quoted_snippet": "80 places", "confidence": "medium"})
    assert row["occupancy"] == 80 and row["occupancy_source"] == "override"
    assert "occupancy_unknown" not in row["needs_review"] and "occupancy_seasonal" in row["needs_review"]
    rec = row["overrides"][-1]
    assert rec["field"] == "occupancy" and rec["previous"] is None and rec["direction"] == "pessimistic"
    assert rec["source"] == "operator page" and rec["quoted_snippet"] == "80 places" and rec["fetched_at"]
    apply_override(row, {"field": "occupancy", "value": 120})
    with pytest.raises(OptimisticMoveError):
        apply_override(row, {"field": "occupancy", "value": 100})
    assert row["occupancy"] == 120

    apply_override(row, {"field": "tier", "value": "act_now", "source": "agent"})
    assert row["tier"] == "act_now"
    with pytest.raises(OptimisticMoveError):
        apply_override(row, {"field": "tier", "value": "prepare"})
    with pytest.raises(OptimisticMoveError):
        apply_override(row, {"field": "tier", "value": "monitor"})

    apply_override(row, {"field": "shelter_viable", "value": False})
    assert row["shelter_viable"] is False
    with pytest.raises(OptimisticMoveError):
        apply_override(row, {"field": "shelter_viable", "value": True})
    with pytest.raises(OptimisticMoveError):
        apply_override(row, {"field": "name", "value": "x"})
    assert len(row["overrides"]) == 4
