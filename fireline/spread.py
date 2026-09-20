"""Spread forecast -> arrival-time rasters (PLAN 6.2). Contract in CONTRACTS.md.

Two sources, both normalised to an :class:`ArrivalRaster` on the shared grid:

* :func:`run_ca` -- a numpy cellular automaton in the style of Alexandridis et al. (2008),
  run as a stochastic ensemble that perturbs the wind per run. The wind is one vector for the whole
  run, or a ``wind_series`` of samples that take effect at given elapsed minutes.
* :func:`arrival_from_polygons` -- Deepfire-style hourly polygons rasterised to arrival hours.

:func:`combine` takes the elementwise earlier arrival of two rasters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon

from . import config
from .grid import Grid


@dataclass
class ArrivalRaster:
    grid: Grid
    arrival_p10: np.ndarray   # minutes after t; inf where not burned in >=10% of runs
    arrival_p50: np.ndarray
    arrival_p90: np.ndarray
    burn_prob: np.ndarray     # 0..1
    source: str
    horizon_min: int

    def sample(self, layer: str, lon, lat):
        return self.grid.sample(getattr(self, layer), lon, lat)


# Eight neighbour offsets as (drow, dcol); row 0 is north, so drow=-1 points north.
_OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _bearing_deg(dr: int, dc: int) -> float:
    """Compass bearing (deg clockwise from north) of the spread direction (dr, dc)."""
    return math.degrees(math.atan2(dc, -dr)) % 360.0


def _wind_factors(wind_dir_from_deg: float, wind_speed_mps: float, cfg: dict) -> np.ndarray:
    """Alexandridis wind factor per neighbour direction.

    ``theta`` is the angle between the spread direction and the direction the wind blows
    TOWARD; ``wind_dir_from_deg`` is the meteorological FROM direction.
    """
    wind_to = (wind_dir_from_deg + 180.0) % 360.0
    v = wind_speed_mps
    out = np.empty(len(_OFFSETS), dtype=np.float64)
    for k, (dr, dc) in enumerate(_OFFSETS):
        theta = math.radians(_bearing_deg(dr, dc) - wind_to)
        out[k] = math.exp(cfg["wind_c1"] * v) * math.exp(v * cfg["wind_c2"] * (math.cos(theta) - 1.0))
    return out


def _wind_schedule(wind_series, n_steps: int, minutes_per_step: float):
    """Split a wind series into its samples and a per-step index into them.

    ``wind_series`` is a sequence of ``(minutes_from_start, wind_dir_from_deg, wind_speed_mps)``
    ascending in ``minutes_from_start``. The sample in effect during a step is the last one at or
    before the elapsed minutes at the START of that step; the first sample also covers any time
    before it. Returns ``(samples, step_idx)`` with ``samples`` a list of ``(dir_deg, speed_mps)``
    and ``step_idx`` an (n_steps,) int array.
    """
    samples = [(float(t), float(d), float(v)) for t, d, v in wind_series]
    times = np.array([s[0] for s in samples], dtype=np.float64)
    if np.any(np.diff(times) <= 0):
        raise ValueError(f"wind_series minutes_from_start must ascend, got {list(times)}")
    elapsed = np.arange(n_steps, dtype=np.float64) * minutes_per_step
    step_idx = np.maximum(np.searchsorted(times, elapsed, side="right") - 1, 0)
    return [(d, v) for _, d, v in samples], step_idx


def _shift(arr: np.ndarray, dr: int, dc: int) -> np.ndarray:
    """out[i, j] = arr[i - dr, j - dc] (zero outside), i.e. the value at the upwind source cell."""
    h, w = arr.shape
    padded = np.zeros((h + 2, w + 2), dtype=arr.dtype)
    padded[1:-1, 1:-1] = arr
    return padded[1 - dr:1 - dr + h, 1 - dc:1 - dc + w]


def _slope_factors(elevation: np.ndarray | None, cell: float, cfg: dict) -> list[np.ndarray | float]:
    """Per-direction slope factor exp(slope_a * slope_deg_along_direction) at the TARGET cell.

    ``elevation`` is a DEM in metres on the grid; the along-direction slope is the angle of the
    line from the burning (source) cell up to the target cell. 1.0 everywhere when None.
    """
    if elevation is None:
        return [1.0] * len(_OFFSETS)
    e = np.asarray(elevation, dtype=np.float32)
    out = []
    for dr, dc in _OFFSETS:
        dist = cell * math.hypot(dr, dc)
        src = _shift(e, dr, dc)
        slope_deg = np.degrees(np.arctan((e - src) / dist))
        # Outside the grid the shifted source is 0; treat those edge cells as flat.
        slope_deg[_shift(np.ones_like(e), dr, dc) == 0] = 0.0
        out.append(np.exp(cfg["slope_a"] * slope_deg).astype(np.float32))
    return out


def seed_mask(perimeter: Polygon | MultiPolygon, grid: Grid) -> np.ndarray:
    """Boolean (nrows, ncols): cells whose centre lies inside the perimeter (EPSG:25831).

    If the perimeter is smaller than a cell so that no centre falls inside, the cell containing
    its representative point is used, so a tiny ignition still seeds the automaton.
    """
    mask = np.zeros(grid.shape, dtype=bool)
    if perimeter is None or perimeter.is_empty:
        return mask
    rows, cols = np.mgrid[0:grid.nrows, 0:grid.ncols]
    x, y = grid.to_xy(rows, cols)
    mask = shapely.contains_xy(perimeter, x, y)
    if not mask.any():
        p = perimeter.representative_point()
        r, c = grid.to_rowcol(p.x, p.y)
        if grid.inside(r, c):
            mask[r, c] = True
    return mask


def _run_one(rng, seed, base, wind_k, fuel_ok, n_steps):
    """One stochastic CA run. Returns int32 array of arrival step (-1 = never burned).

    ``wind_k`` is either an (8,) array of per-direction wind factors held for the whole run, or an
    (n_steps, 8) array giving those factors per step.

    Vectorised on a window that starts at the bounding box of the seed cells and grows by one
    cell per step (fire cannot outrun that), so cost scales with the burned area, not the grid.
    """
    per_step = wind_k.ndim == 2
    nrows, ncols = seed.shape
    arrival = np.full((nrows, ncols), -1, dtype=np.int32)
    arrival[seed] = 0
    rr, cc = np.nonzero(seed)
    r0, r1, c0, c1 = rr.min(), rr.max(), cc.min(), cc.max()
    for step in range(n_steps):
        wr0, wr1 = max(r0 - 1, 0), min(r1 + 1, nrows - 1)
        wc0, wc1 = max(c0 - 1, 0), min(c1 + 1, ncols - 1)
        win = (slice(wr0, wr1 + 1), slice(wc0, wc1 + 1))
        arr_w = arrival[win]
        burning = arr_w == step
        if not burning.any():
            break
        h, w = burning.shape
        padded = np.zeros((h + 2, w + 2), dtype=np.float32)
        padded[1:-1, 1:-1] = burning
        survive = np.ones((h, w), dtype=np.float32)
        wk = wind_k[step] if per_step else wind_k
        for k, (dr, dc) in enumerate(_OFFSETS):
            b_k = padded[1 - dr:1 - dr + h, 1 - dc:1 - dc + w]
            base_k = base[k]
            p_k = base_k[win] if isinstance(base_k, np.ndarray) else base_k
            p_k = np.minimum(p_k * wk[k], 1.0)
            survive *= 1.0 - p_k * b_k
        u = rng.random((h, w), dtype=np.float32)
        new = (arr_w == -1) & (u < 1.0 - survive)
        if fuel_ok is not None:
            new &= fuel_ok[win]
        if not new.any():
            continue
        arr_w[new] = step + 1
        rows_any = new.any(axis=1)
        cols_any = new.any(axis=0)
        r0 = min(r0, wr0 + int(np.argmax(rows_any)))
        r1 = max(r1, wr0 + h - 1 - int(np.argmax(rows_any[::-1])))
        c0 = min(c0, wc0 + int(np.argmax(cols_any)))
        c1 = max(c1, wc0 + w - 1 - int(np.argmax(cols_any[::-1])))
    return arrival


def _percentile_over_burned(sorted_minutes: np.ndarray, n_burned: np.ndarray, q: float) -> np.ndarray:
    """Linear-interpolated q-quantile (0..1) along axis 0 of an ascending-sorted (runs, ...) array,
    using only the first n_burned (finite) entries per cell. inf where n_burned == 0."""
    n = np.maximum(n_burned, 1)
    pos = q * (n - 1)
    lo = np.floor(pos).astype(np.int64)
    hi = np.minimum(lo + 1, n - 1)
    frac = (pos - lo).astype(np.float32)
    v_lo = np.take_along_axis(sorted_minutes, lo[None], axis=0)[0]
    v_hi = np.take_along_axis(sorted_minutes, hi[None], axis=0)[0]
    with np.errstate(invalid="ignore"):  # inf - inf where no run burned; overwritten below
        out = v_lo + frac * (v_hi - v_lo)
    out[n_burned == 0] = np.inf
    return out


def aggregate_runs(minutes: np.ndarray, grid: Grid, horizon_min: int, source: str) -> ArrivalRaster:
    """Ensemble (n_runs, nrows, ncols) of arrival minutes (inf = not burned) -> ArrivalRaster."""
    minutes = np.asarray(minutes, dtype=np.float32)
    minutes = np.where(minutes <= horizon_min, minutes, np.inf).astype(np.float32)
    n_runs = minutes.shape[0]
    burned = np.isfinite(minutes)
    n_burned = burned.sum(axis=0)
    burn_prob = (n_burned / n_runs).astype(np.float64)
    srt = np.sort(minutes, axis=0)
    out = {}
    for name, q in (("arrival_p10", 0.10), ("arrival_p50", 0.50), ("arrival_p90", 0.90)):
        arr = _percentile_over_burned(srt, n_burned, q).astype(np.float64)
        arr[burn_prob < q] = np.inf
        out[name] = arr
    return ArrivalRaster(grid=grid, burn_prob=burn_prob, source=source, horizon_min=int(horizon_min), **out)


def run_ca(fire_state, grid, fuel=None, slope=None, n_runs=50, horizon_min=720, seed=0,
           cfg: dict | None = None, wind_series=None) -> ArrivalRaster:
    """Stochastic CA ensemble (Alexandridis et al. 2008 style) -> ArrivalRaster with source "ca".

    Each step every burning cell tries to ignite its 8 neighbours with
    ``p = p0 * fuel * wind_factor * slope_factor`` (clipped to 1); burning cells burn for one step
    then become burned. Seed cells are the grid cells covered by ``fire_state.perimeter``
    (EPSG:25831) and have arrival 0. Each of ``n_runs`` runs perturbs wind speed by +-30 % and
    wind direction by +-20 deg (uniform), on top of the per-cell randomness.

    ``fuel``: optional (nrows, ncols) multiplier on the ignition probability of the target cell;
    cells with fuel <= 0 never burn. ``slope``: optional (nrows, ncols) elevation in metres (DEM),
    from which the slope along each spread direction is derived; 1.0 everywhere when None.

    ``wind_series``: optional sequence of ``(minutes_from_start, wind_dir_from_deg,
    wind_speed_mps)`` ascending in ``minutes_from_start``, elapsed minutes after ``fire_state.t``.
    A step uses the last sample at or before its start time; the first sample also covers the time
    before it, so a series need not start at 0. Non-ascending raises ValueError. None or empty
    means the constant ``fire_state`` wind. One run's speed factor and direction offset are drawn
    once and applied to every sample, so a member is a coherent variant of the whole history.
    """
    cfg = dict(config.CA) if cfg is None else cfg
    mps = float(cfg["minutes_per_step"])
    n_steps = int(horizon_min // mps)
    rng = np.random.default_rng(seed)
    series = None
    if wind_series is not None and len(wind_series) > 0:
        series, step_idx = _wind_schedule(wind_series, n_steps, mps)

    seed_cells = seed_mask(fire_state.perimeter, grid)
    if fuel is not None:
        fuel = np.asarray(fuel, dtype=np.float32)
        if fuel.shape != grid.shape:
            raise ValueError(f"fuel shape {fuel.shape} != grid shape {grid.shape}")
        fuel_ok = fuel > 0
    else:
        fuel_ok = None
    if slope is not None and np.shape(slope) != grid.shape:
        raise ValueError(f"slope shape {np.shape(slope)} != grid shape {grid.shape}")

    slope_f = _slope_factors(slope, grid.cell, cfg)
    base = []
    for factor in slope_f:
        b = cfg["p0"] * factor
        if fuel is not None:
            b = b * fuel
        base.append(b.astype(np.float32) if isinstance(b, np.ndarray) else float(b))

    minutes = np.full((n_runs,) + grid.shape, np.inf, dtype=np.float32)
    if not seed_cells.any():
        return aggregate_runs(minutes, grid, horizon_min, "ca")

    v0 = float(fire_state.wind_speed_mps)
    d0 = float(fire_state.wind_dir_deg)
    for i in range(n_runs):
        speed_f = 1.0 + rng.uniform(-0.3, 0.3)
        dir_off = rng.uniform(-20.0, 20.0)
        if series is None:
            wind_k = _wind_factors(d0 + dir_off, v0 * speed_f, cfg)
        else:
            per_sample = np.stack([_wind_factors(d + dir_off, v * speed_f, cfg) for d, v in series])
            wind_k = per_sample[step_idx]
        arrival = _run_one(rng, seed_cells, base, wind_k, fuel_ok, n_steps)
        burned = arrival >= 0
        minutes[i][burned] = arrival[burned] * mps
    return aggregate_runs(minutes, grid, horizon_min, "ca")


def arrival_from_polygons(hourly_polygons: list[tuple[int, Polygon | MultiPolygon]], grid: Grid,
                          horizon_min: int = 720) -> ArrivalRaster:
    """Deepfire-style hourly polygons (hour, polygon in EPSG:25831) -> ArrivalRaster, source "deepfire".

    Per cell the arrival is the first hour whose polygon covers the cell centre (minutes = 60 * hour);
    burn_prob is 1 where covered within the horizon, else 0. p10 = p50 = p90 (single member).
    """
    arrival = np.full(grid.shape, np.inf, dtype=np.float64)
    rows, cols = np.mgrid[0:grid.nrows, 0:grid.ncols]
    x, y = grid.to_xy(rows, cols)
    for hour, poly in sorted(hourly_polygons, key=lambda hp: hp[0]):
        minutes = 60.0 * float(hour)
        if minutes > horizon_min:
            break
        if poly is None or poly.is_empty:
            continue
        covered = shapely.contains_xy(poly, x, y)
        arrival = np.where(covered & (minutes < arrival), minutes, arrival)
    burn_prob = np.isfinite(arrival).astype(np.float64)
    return ArrivalRaster(grid=grid, arrival_p10=arrival.copy(), arrival_p50=arrival.copy(),
                         arrival_p90=arrival.copy(), burn_prob=burn_prob, source="deepfire",
                         horizon_min=int(horizon_min))


def combine(a: ArrivalRaster, b: ArrivalRaster) -> ArrivalRaster:
    """Elementwise earlier arrival and max burn probability; source "min(<a>,<b>)"."""
    if a.grid.shape != b.grid.shape:
        raise ValueError(f"grid shapes differ: {a.grid.shape} vs {b.grid.shape}")
    return ArrivalRaster(
        grid=a.grid,
        arrival_p10=np.minimum(a.arrival_p10, b.arrival_p10),
        arrival_p50=np.minimum(a.arrival_p50, b.arrival_p50),
        arrival_p90=np.minimum(a.arrival_p90, b.arrival_p90),
        burn_prob=np.maximum(a.burn_prob, b.burn_prob),
        source=f"min({a.source},{b.source})",
        horizon_min=int(max(a.horizon_min, b.horizon_min)),
    )
