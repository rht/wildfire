"""CA ensemble, polygon rasterisation and combine (no network, small grids)."""

from datetime import datetime, timezone

import numpy as np
import pytest
from shapely.geometry import Point, box

from fireline import config, spread
from fireline.fire_state import FireState
from fireline.grid import Grid, xy_to_lonlat

T0 = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
# Wind-driven parameters: calm stays below the percolation threshold, so a wind change shows.
_WIND_CFG = dict(config.CA, p0=0.15, wind_c1=0.25)


def _small_grid(n=60, cell=100.0):
    return Grid(500000.0, 4640000.0, 500000.0 + n * cell, 4640000.0 + n * cell, cell)


def _centre_state(grid, wind_dir_deg, wind_speed_mps, radius_m=150.0):
    cx = (grid.xmin + grid.xmax) / 2
    cy = (grid.ymin + grid.ymax) / 2
    lon, lat = xy_to_lonlat(cx, cy)
    return FireState.synthetic(lon, lat, T0, radius_m=radius_m, wind_dir_deg=wind_dir_deg,
                               wind_speed_mps=wind_speed_mps)


@pytest.fixture(scope="module")
def ca_north_wind():
    grid = _small_grid()
    fs = _centre_state(grid, wind_dir_deg=0.0, wind_speed_mps=8.0)   # wind FROM north -> spreads south
    ar = spread.run_ca(fs, grid, n_runs=20, horizon_min=120, seed=1)
    return grid, fs, ar


def test_run_ca_contract(ca_north_wind):
    grid, fs, ar = ca_north_wind
    assert ar.source == "ca"
    assert ar.horizon_min == 120
    for layer in ("arrival_p10", "arrival_p50", "arrival_p90", "burn_prob"):
        assert getattr(ar, layer).shape == grid.shape
    assert np.all(ar.burn_prob >= 0) and np.all(ar.burn_prob <= 1)
    fin = np.isfinite(ar.arrival_p90)
    assert fin.any()
    assert np.all(ar.arrival_p10[fin] <= ar.arrival_p50[fin])
    assert np.all(ar.arrival_p50[fin] <= ar.arrival_p90[fin])
    # p10 is inf wherever burn_prob < 0.1 etc.
    assert np.all(np.isinf(ar.arrival_p10[ar.burn_prob < 0.10]))
    assert np.all(np.isinf(ar.arrival_p50[ar.burn_prob < 0.50]))
    assert np.all(np.isinf(ar.arrival_p90[ar.burn_prob < 0.90]))
    # arrivals never exceed the horizon
    assert ar.arrival_p90[fin].max() <= 120
    # sample() works through the grid
    lon, lat = xy_to_lonlat(*fs.perimeter.centroid.coords[0])
    assert ar.sample("burn_prob", lon, lat) == 1.0


def test_seed_cells_have_arrival_zero(ca_north_wind):
    grid, fs, ar = ca_north_wind
    seed = spread.seed_mask(fs.perimeter, grid)
    assert seed.sum() > 1
    assert np.all(ar.arrival_p10[seed] == 0)
    assert np.all(ar.arrival_p50[seed] == 0)
    assert np.all(ar.arrival_p90[seed] == 0)
    assert np.all(ar.burn_prob[seed] == 1.0)


def test_downwind_burns_earlier_than_upwind(ca_north_wind):
    grid, fs, ar = ca_north_wind
    r, c = grid.to_rowcol(*fs.perimeter.centroid.coords[0])
    d = 8  # cells
    south, north = ar.arrival_p50[r + d, c], ar.arrival_p50[r - d, c]
    assert np.isfinite(south)
    assert south < north  # north may be inf
    # whole southern half burns more (and sooner) than the northern half
    assert ar.burn_prob[r + 1:, :].sum() > 1.5 * ar.burn_prob[:r, :].sum()


def test_fuel_zero_blocks_spread():
    grid = _small_grid()
    fs = _centre_state(grid, wind_dir_deg=0.0, wind_speed_mps=5.0)
    r, c = grid.to_rowcol(*fs.perimeter.centroid.coords[0])
    fuel = np.ones(grid.shape)
    fuel[r + 4, :] = 0.0   # firebreak row south of the seed
    ar = spread.run_ca(fs, grid, fuel=fuel, n_runs=10, horizon_min=120, seed=3)
    assert np.all(ar.burn_prob[r + 4, :] == 0)
    assert np.all(ar.burn_prob[r + 5:, :] == 0)
    assert ar.burn_prob[r + 3, c] > 0


def test_slope_uphill_faster_than_downhill():
    grid = _small_grid()
    fs = _centre_state(grid, wind_dir_deg=0.0, wind_speed_mps=0.0)
    rows = np.arange(grid.nrows)[:, None]
    dem = np.repeat((grid.nrows - rows) * 30.0, grid.ncols, axis=1)  # rises northwards, 30 m/cell
    ar = spread.run_ca(fs, grid, slope=dem, n_runs=20, horizon_min=120, seed=5)
    r, c = grid.to_rowcol(*fs.perimeter.centroid.coords[0])
    assert ar.burn_prob[:r, :].sum() > ar.burn_prob[r + 1:, :].sum()


def test_run_ca_is_deterministic_for_seed():
    grid = _small_grid(30)
    fs = _centre_state(grid, 90.0, 4.0)
    a = spread.run_ca(fs, grid, n_runs=5, horizon_min=60, seed=7)
    b = spread.run_ca(fs, grid, n_runs=5, horizon_min=60, seed=7)
    assert np.array_equal(a.burn_prob, b.burn_prob)
    assert np.array_equal(a.arrival_p50, b.arrival_p50)


@pytest.fixture(scope="module")
def ca_wind_switch():
    """Strong north wind that drops to calm after 1 h, against the same wind held for 4 h."""
    grid = _small_grid(90)
    fs = _centre_state(grid, wind_dir_deg=0.0, wind_speed_mps=8.0)
    kw = dict(n_runs=12, horizon_min=240, seed=2, cfg=_WIND_CFG)
    steady = spread.run_ca(fs, grid, **kw)
    switched = spread.run_ca(fs, grid, wind_series=[(0.0, 0.0, 8.0), (60.0, 0.0, 0.4)], **kw)
    return grid, fs, steady, switched


def test_wind_series_matching_fire_state_reproduces_constant_wind(ca_north_wind):
    grid, fs, ar = ca_north_wind
    same = spread.run_ca(fs, grid, n_runs=20, horizon_min=120, seed=1,
                         wind_series=[(0.0, fs.wind_dir_deg, fs.wind_speed_mps)])
    assert np.array_equal(same.burn_prob, ar.burn_prob)
    assert np.array_equal(same.arrival_p50, ar.arrival_p50)


def test_empty_wind_series_is_the_same_as_none(ca_north_wind):
    grid, fs, ar = ca_north_wind
    empty = spread.run_ca(fs, grid, n_runs=20, horizon_min=120, seed=1, wind_series=[])
    assert np.array_equal(empty.burn_prob, ar.burn_prob)
    assert np.array_equal(empty.arrival_p50, ar.arrival_p50)


def test_drop_to_calm_stops_growth_but_keeps_the_early_spread(ca_wind_switch):
    grid, fs, steady, switched = ca_wind_switch
    r, c = grid.to_rowcol(*fs.perimeter.centroid.coords[0])
    # The fire runs south under the north wind, then all but stops when the wind drops.
    assert switched.burn_prob.sum() < 0.5 * steady.burn_prob.sum()
    south_steady = int(np.nonzero((steady.burn_prob >= 0.5).any(axis=1))[0].max()) - r
    south_switched = int(np.nonzero((switched.burn_prob >= 0.5).any(axis=1))[0].max()) - r
    assert 0 < south_switched < 0.5 * south_steady
    # Before the switch the two are the same fire; one member makes that exact (no percentile
    # interpolation over an ensemble whose members diverge later).
    kw = dict(n_runs=1, horizon_min=240, seed=2, cfg=_WIND_CFG)
    one_steady = spread.run_ca(fs, grid, **kw)
    one_switched = spread.run_ca(fs, grid, wind_series=[(0.0, 0.0, 8.0), (60.0, 0.0, 0.4)], **kw)
    early = one_steady.arrival_p50 <= 60           # arrivals up to the switch; inf is excluded
    assert early.sum() > 20
    assert np.array_equal(one_switched.arrival_p50[early], one_steady.arrival_p50[early])
    assert (one_switched.arrival_p50 <= 60).sum() == early.sum()


def test_wind_reversal_burns_both_sides_of_the_seed(ca_wind_switch):
    grid, fs, steady, _ = ca_wind_switch
    r, c = grid.to_rowcol(*fs.perimeter.centroid.coords[0])
    reversed_ = spread.run_ca(fs, grid, n_runs=12, horizon_min=240, seed=2, cfg=_WIND_CFG,
                              wind_series=[(0.0, 0.0, 8.0), (120.0, 180.0, 8.0)])
    north_steady, south_steady = steady.burn_prob[:r].sum(), steady.burn_prob[r + 1:].sum()
    north_rev, south_rev = reversed_.burn_prob[:r].sum(), reversed_.burn_prob[r + 1:].sum()
    assert north_steady < 0.05 * south_steady          # constant north wind: one side only
    assert north_rev > 50 and south_rev > 50           # reversal: both sides
    assert north_rev > 10 * north_steady


def test_first_sample_applies_before_its_own_time(ca_wind_switch):
    grid, fs, _, switched = ca_wind_switch
    late_first = spread.run_ca(fs, grid, n_runs=12, horizon_min=240, seed=2, cfg=_WIND_CFG,
                               wind_series=[(45.0, 0.0, 8.0), (60.0, 0.0, 0.4)])
    assert np.array_equal(late_first.burn_prob, switched.burn_prob)
    assert np.array_equal(late_first.arrival_p50, switched.arrival_p50)


def test_wind_series_with_a_series_is_deterministic_for_seed():
    grid = _small_grid(30)
    fs = _centre_state(grid, 90.0, 4.0)
    series = [(0.0, 90.0, 4.0), (20.0, 200.0, 7.0), (40.0, 340.0, 1.5)]
    a = spread.run_ca(fs, grid, n_runs=5, horizon_min=60, seed=7, wind_series=series)
    b = spread.run_ca(fs, grid, n_runs=5, horizon_min=60, seed=7, wind_series=series)
    assert np.array_equal(a.burn_prob, b.burn_prob)
    assert np.array_equal(a.arrival_p50, b.arrival_p50)


@pytest.mark.parametrize("series", [
    [(0.0, 0.0, 8.0), (0.0, 90.0, 4.0)],          # equal times
    [(60.0, 0.0, 8.0), (30.0, 90.0, 4.0)],        # descending
])
def test_non_ascending_wind_series_raises(series):
    grid = _small_grid(20)
    fs = _centre_state(grid, 0.0, 5.0)
    with pytest.raises(ValueError):
        spread.run_ca(fs, grid, n_runs=2, horizon_min=60, wind_series=series)


def test_perimeter_outside_grid_gives_empty_raster():
    grid = _small_grid(20)
    fs = FireState("x", T0, Point(0.0, 0.0).buffer(100.0), wind_dir_deg=0, wind_speed_mps=5)
    ar = spread.run_ca(fs, grid, n_runs=3, horizon_min=60)
    assert ar.burn_prob.sum() == 0
    assert np.all(np.isinf(ar.arrival_p10))


def test_arrival_from_polygons():
    grid = _small_grid(20)
    x0, y0 = grid.to_xy(10, 10)
    polys = [(2, Point(x0, y0).buffer(450.0)), (1, Point(x0, y0).buffer(150.0)),
             (3, Point(x0, y0).buffer(800.0)), (20, box(grid.xmin, grid.ymin, grid.xmax, grid.ymax))]
    ar = spread.arrival_from_polygons(polys, grid, horizon_min=720)
    assert ar.source == "deepfire"
    assert ar.horizon_min == 720
    assert ar.arrival_p50[10, 10] == 60
    assert ar.arrival_p50[10, 13] == 120      # 300 m away: covered by hour 2 first
    assert ar.arrival_p50[10, 17] == 180      # 700 m away: hour 3
    assert np.isinf(ar.arrival_p50[0, 0])     # hour 20 is beyond the horizon
    assert ar.burn_prob[0, 0] == 0 and ar.burn_prob[10, 17] == 1
    assert np.array_equal(ar.arrival_p10, ar.arrival_p50)
    assert np.array_equal(ar.arrival_p90, ar.arrival_p50)
    assert set(np.unique(ar.burn_prob)) <= {0.0, 1.0}


def test_combine():
    grid = _small_grid(4)
    inf = np.inf
    a = spread.ArrivalRaster(grid, np.full(grid.shape, 10.0), np.full(grid.shape, 20.0), np.full(grid.shape, 30.0),
                             np.full(grid.shape, 0.4), "ca", 720)
    p10 = np.full(grid.shape, inf); p10[0, 0] = 5.0
    p50 = np.full(grid.shape, inf); p50[0, 0] = 25.0
    p90 = np.full(grid.shape, inf); p90[0, 0] = 35.0
    bp = np.zeros(grid.shape); bp[0, 0] = 1.0
    b = spread.ArrivalRaster(grid, p10, p50, p90, bp, "deepfire", 720)
    c = spread.combine(a, b)
    assert c.source == "min(ca,deepfire)"
    assert c.arrival_p10[0, 0] == 5.0 and c.arrival_p10[1, 1] == 10.0
    assert c.arrival_p50[0, 0] == 20.0
    assert c.arrival_p90[0, 0] == 30.0
    assert c.burn_prob[0, 0] == 1.0 and c.burn_prob[1, 1] == 0.4
    assert c.horizon_min == 720
    with pytest.raises(ValueError):
        spread.combine(a, spread.arrival_from_polygons([], _small_grid(5), 60))
