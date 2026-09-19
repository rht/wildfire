"""Replay harness and scoring for the recorded Gavarres incident (no network, committed fixtures)."""

import json
import importlib.util
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from fireline import calibrate, config, spread
from fireline.grid import Grid

INCIDENT = "5769dcea"
START = datetime(2026, 7, 3, 11, 47, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]

# observed_watermark -> recomputed area in EPSG:25831 (hectares), handoff 001 table
EXPECTED_AREAS = [
    ("2026-07-03T11:47:00+00:00", 1263.0),
    ("2026-07-03T12:06:00+00:00", 1764.0),
    ("2026-07-03T12:53:00+00:00", 2730.0),
    ("2026-07-03T13:25:00+00:00", 3537.0),
    ("2026-07-03T13:46:00+00:00", 3651.0),
    ("2026-07-03T14:40:00+00:00", 3914.0),
    ("2026-07-04T04:14:00+00:00", 3867.0),
]


@pytest.fixture(scope="module")
def perims():
    return calibrate.load_perimeters()


def test_load_perimeters_matches_the_record(perims):
    assert len(perims) == 7
    assert [p.observed_at.isoformat() for p in perims] == [t for t, _ in EXPECTED_AREAS]
    assert perims == sorted(perims, key=lambda p: p.observed_at)
    for p, (_, ha) in zip(perims, EXPECTED_AREAS):
        assert p.area_ha == pytest.approx(ha, abs=3.0)
        assert p.area_ha == pytest.approx(p.provider_area_ha, rel=0.005)
        assert p.geom.is_valid and p.geom.geom_type in ("Polygon", "MultiPolygon")
        assert p.computed_at > p.observed_at
        assert p.n_hotspots > 0
    assert [p.n_hotspots for p in perims] == sorted(p.n_hotspots for p in perims)


def test_load_perimeters_are_the_one_recorded_incident(perims):
    raw = json.loads(calibrate._perimeters_file(None).read_text())
    feats = raw["body"]["features"]
    assert {f["properties"]["cluster_id"].split("-")[0] for f in feats} == {INCIDENT}
    assert {p.id for p in perims} == {f["properties"]["id"] for f in feats}


def test_load_perimeters_normalizes_paths_before_caching(monkeypatch):
    source = calibrate._perimeters_file(None)
    monkeypatch.chdir(ROOT)
    calibrate._load_perimeters.cache_clear()

    relative = calibrate.load_perimeters(source.relative_to(ROOT))
    absolute = calibrate.load_perimeters(source)

    assert relative == absolute
    assert calibrate._load_perimeters.cache_info().hits == 1
    assert calibrate._load_perimeters.cache_info().misses == 1


def test_pairs_use_observation_times(perims):
    prs = calibrate.pairs(perims)
    assert [p.minutes for p in prs] == [19.0, 47.0, 32.0, 21.0, 54.0, 814.0]
    assert [p.label for p in prs] == ["11:47Z->12:06Z", "12:06Z->12:53Z", "12:53Z->13:25Z",
                                      "13:25Z->13:46Z", "13:46Z->14:40Z", "14:40Z->04:14Z"]
    assert all(a.nxt is b.prev for a, b in zip(prs, prs[1:]))


# ------------------------------------------------------------------------------------------ wind

def test_wind_series_covers_the_window():
    ws = calibrate.wind_series(START, 120)
    assert ws == [(-47.0, 3.0, 6.61), (13.0, 6.0, 7.24), (73.0, 15.0, 5.80)]
    assert ws[0][0] <= 0
    assert all(b[0] > a[0] for a, b in zip(ws, ws[1:]))


def test_wind_series_window_ending_inside_an_hour():
    assert calibrate.wind_series(START, 15) == [(-47.0, 3.0, 6.61), (13.0, 6.0, 7.24)]
    assert calibrate.wind_series(START, 10) == [(-47.0, 3.0, 6.61)]


def test_wind_series_overnight_pair_ends_calm_and_veered():
    ws = calibrate.wind_series(datetime(2026, 7, 3, 14, 40, tzinfo=timezone.utc), 814)
    assert ws[0] == (-40.0, 36.0, 3.72)
    assert ws[-1] == (800.0, 349.0, 2.14)          # 07-04 04:00Z, 13 h 20 min after the start
    assert all(b[0] > a[0] for a, b in zip(ws, ws[1:]))


def test_wind_series_normalizes_paths_before_caching(monkeypatch):
    monkeypatch.chdir(ROOT)
    calibrate._read_wind.cache_clear()

    relative = calibrate.wind_series(START, 10, calibrate.WIND_FILE.relative_to(ROOT))
    absolute = calibrate.wind_series(START, 10, calibrate.WIND_FILE)

    assert relative == absolute
    assert calibrate._read_wind.cache_info().hits == 1
    assert calibrate._read_wind.cache_info().misses == 1


def test_wind_series_without_usable_values_raises(tmp_path):
    path = tmp_path / "41.90_3.05.json"
    path.write_text(json.dumps({
        "lat": 41.9, "lon": 3.05, "model": "test", "slice": "test",
        "time": ["2026-07-03T11:00", "2026-07-03T12:00"],
        "wind_speed_10m": [None, 7.0],
        "wind_direction_10m": [3.0, None],
    }))
    with pytest.raises(ValueError):
        calibrate.wind_series(START, 120, path)


# --------------------------------------------------------------------------------- synthetic scoring

CELL = 100.0
X0, Y0 = 500000.0, 4640000.0


def _grid(n=10):
    return Grid(X0, Y0, X0 + n * CELL, Y0 + n * CELL, CELL)


def _perim(ident, geom, observed_at):
    return calibrate.Perimeter(id=ident, observed_at=observed_at, computed_at=observed_at,
                               geom=geom, area_ha=geom.area / 1e4, provider_area_ha=geom.area / 1e4,
                               n_hotspots=1)


def _pair(ident, seed_geom, observed_geom):
    """Pair of synthetic perimeters 60 min apart; ids are unique so the mask cache cannot collide."""
    t0 = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 3, 11, 0, tzinfo=timezone.utc)
    return calibrate.Pair(_perim(f"{ident}-prev", seed_geom, t0), _perim(f"{ident}-nxt", observed_geom, t1))


def _raster(grid, mask, burn_prob=1.0):
    """ArrivalRaster whose p10/p50/p90 are finite exactly on ``mask`` (cf. test_spread.test_combine)."""
    arr = np.where(mask, 30.0, np.inf)
    bp = np.where(mask, float(burn_prob), 0.0)
    return spread.ArrivalRaster(grid, arr.copy(), arr.copy(), arr.copy(), bp, "synthetic", 60)


def _cells(grid, r0, r1, c0, c1):
    m = np.zeros(grid.shape, dtype=bool)
    m[r0:r1, c0:c1] = True
    return m


SEED_BOX = box(X0, Y0 + 800, X0 + 200, Y0 + 1000)            # rows 0-1, cols 0-1: 4 cells = 4 ha
OBSERVED_BOX = box(X0, Y0 + 600, X0 + 400, Y0 + 1000)        # rows 0-3, cols 0-3: 16 cells = 16 ha


def test_score_pair_perfect_prediction():
    grid = _grid()
    pair = _pair("perfect", SEED_BOX, OBSERVED_BOX)
    s = calibrate.score_pair(pair, _raster(grid, _cells(grid, 0, 4, 0, 4)), grid)
    assert (s.seed_area_ha, s.observed_area_ha, s.observed_new_ha) == (4.0, 16.0, 12.0)
    assert (s.predicted_area_ha, s.predicted_new_ha) == (16.0, 12.0)
    assert s.p10_area_ha == 16.0 and s.p90_area_ha == 16.0
    assert s.area_ratio == 1.0 and s.growth_ratio == 1.0 and s.iou == 1.0
    assert s.p10_recall == 1.0
    assert s.growth_frac_of_observed == pytest.approx(12.0 / 16.0)
    assert s.label == "10:00Z->11:00Z" and s.minutes == 60.0
    assert len(s.row()) == len(calibrate.SCORE_HEADER)


def test_score_pair_under_prediction():
    grid = _grid()
    pair = _pair("under", SEED_BOX, OBSERVED_BOX)
    s = calibrate.score_pair(pair, _raster(grid, _cells(grid, 0, 3, 0, 3)), grid)   # 9 of 16 cells
    assert s.predicted_area_ha == 9.0 and s.predicted_new_ha == 5.0
    assert s.area_ratio == pytest.approx(9.0 / 16.0)
    assert s.growth_ratio == pytest.approx(5.0 / 12.0)
    assert s.iou == pytest.approx(9.0 / 16.0)          # prediction is a subset of the observation
    assert s.p10_recall == pytest.approx(5.0 / 12.0)


def test_score_pair_over_prediction():
    grid = _grid()
    pair = _pair("over", SEED_BOX, OBSERVED_BOX)
    s = calibrate.score_pair(pair, _raster(grid, np.ones(grid.shape, dtype=bool)), grid)
    assert s.predicted_area_ha == 100.0 and s.predicted_new_ha == 96.0
    assert s.area_ratio == pytest.approx(100.0 / 16.0)
    assert s.growth_ratio == pytest.approx(96.0 / 12.0)
    assert s.iou == pytest.approx(16.0 / 100.0)
    assert s.p10_recall == 1.0


def test_score_pair_low_burn_prob_is_not_recalled():
    grid = _grid()
    pair = _pair("lowprob", SEED_BOX, OBSERVED_BOX)
    # p50 footprint is the full observation, but only the seed is burned in >= 10 % of runs
    mask = _cells(grid, 0, 4, 0, 4)
    r = _raster(grid, mask)
    r.burn_prob = np.where(calibrate.mask_of(pair.prev, grid), 1.0, 0.05)
    s = calibrate.score_pair(pair, r, grid)
    assert s.area_ratio == 1.0 and s.iou == 1.0
    assert s.p10_recall == 0.0


def test_score_pair_zero_denominators_never_raise():
    grid = _grid()
    # observation identical to the seed: no observed growth at all
    still = _pair("still", SEED_BOX, SEED_BOX)
    seed_only = calibrate.score_pair(still, _raster(grid, _cells(grid, 0, 2, 0, 2)), grid)
    assert seed_only.observed_new_ha == 0.0
    assert seed_only.growth_ratio == 1.0            # 0 / 0
    assert seed_only.growth_frac_of_observed == 0.0
    assert seed_only.p10_recall == 1.0              # no observed-new cells
    assert seed_only.area_ratio == 1.0 and seed_only.iou == 1.0

    grew = calibrate.score_pair(still, _raster(grid, _cells(grid, 0, 4, 0, 4)), grid)
    assert grew.predicted_new_ha == 12.0
    assert grew.growth_ratio == float("inf")        # positive / 0
    assert np.isfinite(grew.area_ratio) and np.isfinite(grew.iou)

    # empty observation: area_ratio inf, and both zero gives 1.0
    empty = _pair("empty", SEED_BOX, box(X0 - 5000, Y0 - 5000, X0 - 4000, Y0 - 4000))
    s = calibrate.score_pair(empty, _raster(grid, _cells(grid, 0, 2, 0, 2)), grid)
    assert s.observed_area_ha == 0.0 and s.area_ratio == float("inf")
    assert s.iou == 0.0 and s.p10_recall == 1.0


# ------------------------------------------------------------------------------------- end to end

@pytest.fixture(scope="module")
def shortest_pair_run(perims):
    grid = Grid.gavarres()
    pair = min(calibrate.pairs(perims), key=lambda p: p.minutes)
    raster, score = calibrate.run_pair(pair, grid, dict(config.CA), n_runs=4, seed=0)
    return pair, raster, score


def test_run_pair_is_self_consistent(shortest_pair_run):
    pair, raster, score = shortest_pair_run
    assert pair.label == "11:47Z->12:06Z"
    assert raster.source == "ca" and raster.horizon_min == 19
    assert score.label == pair.label and score.minutes == 19.0
    for v in (score.seed_area_ha, score.observed_area_ha, score.predicted_area_ha,
              score.p10_area_ha, score.p90_area_ha, score.area_ratio, score.growth_ratio,
              score.growth_frac_of_observed, score.iou, score.p10_recall):
        assert np.isfinite(v)
    assert score.seed_area_ha == pytest.approx(1263.0, abs=3.0)
    assert score.p10_area_ha >= score.predicted_area_ha >= score.p90_area_ha
    assert score.predicted_area_ha >= score.seed_area_ha
    assert 0.0 <= score.iou <= 1.0
    assert 0.0 <= score.p10_recall <= 1.0


def test_run_pair_constant_wind_differs_from_the_hourly_series(perims):
    """The 13:46->14:40 window crosses the hour the wind drops, so the two paths cannot agree."""
    grid = Grid.gavarres()
    pair = calibrate.pairs(perims)[4]
    _, hourly = calibrate.run_pair(pair, grid, dict(config.CA), n_runs=3, seed=0)
    _, const = calibrate.run_pair(pair, grid, dict(config.CA), n_runs=3, seed=0, constant_wind=True)
    assert hourly.predicted_area_ha != const.predicted_area_ha


def test_calm_wind_area_ha_returns_a_positive_area():
    ha = calibrate.calm_wind_area_ha(dict(config.CA), hours=1.0, n_runs=2, seed=0)
    assert isinstance(ha, float)
    assert ha > 0.0
    assert ha >= np.pi * 1000.0 ** 2 / 1e4 * 0.9      # at least about the seed disc


def _calibration_script():
    spec = importlib.util.spec_from_file_location("calibrate_ca_script", ROOT / "scripts/calibrate_ca.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_git_revision_returns_current_commit():
    script = _calibration_script()
    revision = script._git_revision(ROOT)
    assert revision is not None
    assert re.fullmatch(r"[0-9a-f]{40}", revision)


def test_git_revision_failure_keeps_optional_provenance_empty(tmp_path):
    script = _calibration_script()
    assert script._git_revision(tmp_path) is None


def test_git_revision_timeout_keeps_optional_provenance_empty(tmp_path, monkeypatch):
    script = _calibration_script()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n", encoding="utf-8")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    started = time.monotonic()
    revision = script._git_revision(ROOT, timeout_seconds=0.01)

    assert revision is None
    assert time.monotonic() - started < 1.0
