"""CA ensemble, polygon rasterisation and combine (no network, small grids)."""

from datetime import datetime, timezone

import numpy as np
import pytest
from shapely.geometry import Point, box

from fireline import spread
from fireline.fire_state import FireState
from fireline.grid import Grid, xy_to_lonlat

T0 = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)


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
