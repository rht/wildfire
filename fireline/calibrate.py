"""Replay the recorded Gavarres incident through the CA and score it (handoff 001, steps 1-2).

The record is seven satellite perimeters of incident ``5769dcea`` plus an hourly wind series, both
committed fixtures; nothing here touches the network. :func:`load_perimeters` reads the perimeters,
:func:`pairs` turns them into consecutive (i, i+1) replay windows, :func:`run_pair` seeds
:func:`fireline.spread.run_ca` on perimeter i at its observation time and runs it to the observation
time of perimeter i+1, and :func:`score_pair` compares the p50 footprint with perimeter i+1.

Every footprint is a boolean mask on the caller's grid (``spread.seed_mask``); no function picks a
grid of its own. Observation times (``observed_watermark``) drive the physics, never ``computed_at``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from shapely import make_valid
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform, unary_union

from . import spread
from .fire_state import FireState
from .grid import Grid, lonlat_to_xy

ROOT = Path(__file__).resolve().parents[1]
PERIMETERS_DIR = ROOT / "fixtures" / "fire" / "deepfire" / "real"
PERIMETERS_GLOB = "*satellite-perimeters.json"
WIND_FILE = ROOT / "fixtures" / "wind" / "41.90_3.05.json"


# ----------------------------------------------------------------------------- recorded perimeters

@dataclass(frozen=True)
class Perimeter:
    """One recorded satellite perimeter, reprojected to EPSG:25831.

    ``area_ha`` is recomputed from ``geom``; ``provider_area_ha`` is the provider's ``area_m2``.
    """
    id: str
    observed_at: datetime      # properties.observed_watermark, UTC
    computed_at: datetime      # properties.computed_at, UTC
    geom: BaseGeometry         # EPSG:25831, made valid, GeometryCollection flattened to polygons
    area_ha: float
    provider_area_ha: float
    n_hotspots: int

    @property
    def hhmm(self) -> str:
        return self.observed_at.strftime("%H:%M") + "Z"


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _polygons_25831(geometry: dict) -> BaseGeometry:
    """GeoJSON (EPSG:4326) -> valid polygonal geometry in EPSG:25831."""
    geom = make_valid(shape(geometry))
    if geom.geom_type == "GeometryCollection":
        geom = unary_union([g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
    return shapely_transform(lonlat_to_xy, geom)


def _perimeters_file(path: Path | None) -> Path:
    if path is not None:
        return Path(path)
    files = sorted(PERIMETERS_DIR.glob(PERIMETERS_GLOB))
    if not files:
        raise FileNotFoundError(f"no {PERIMETERS_GLOB} under {PERIMETERS_DIR}")
    return files[-1]


@lru_cache(maxsize=8)
def _load_perimeters(path_str: str) -> tuple[Perimeter, ...]:
    rec = json.loads(Path(path_str).read_text())
    body = rec.get("body", rec)
    out = []
    for feat in body["features"]:
        props = feat["properties"]
        geom = _polygons_25831(feat["geometry"])
        out.append(Perimeter(
            id=str(props["id"]),
            observed_at=_utc(props["observed_watermark"]),
            computed_at=_utc(props["computed_at"]),
            geom=geom,
            area_ha=geom.area / 1e4,
            provider_area_ha=float(props["area_m2"]) / 1e4,
            n_hotspots=int(props["n_hotspots"]),
        ))
    out.sort(key=lambda p: p.observed_at)
    return tuple(out)


def load_perimeters(path: Path | None = None) -> list[Perimeter]:
    """Every perimeter of the recorded incident, ascending by ``observed_at``."""
    return list(_load_perimeters(str(_perimeters_file(path))))


@dataclass(frozen=True)
class Pair:
    """Consecutive recorded perimeters: seed on ``prev``, predict ``nxt``."""
    prev: Perimeter
    nxt: Perimeter

    @property
    def label(self) -> str:
        return f"{self.prev.hhmm}->{self.nxt.hhmm}"

    @property
    def minutes(self) -> float:
        """Observation-time difference (``observed_at``, not ``computed_at``)."""
        return (self.nxt.observed_at - self.prev.observed_at).total_seconds() / 60.0


def pairs(perims: list[Perimeter]) -> list[Pair]:
    """Consecutive (i, i+1) pairs of an ascending perimeter list."""
    return [Pair(a, b) for a, b in zip(perims, perims[1:])]


# ------------------------------------------------------------------------------------------ wind

@lru_cache(maxsize=8)
def _read_wind(path_str: str) -> tuple[tuple[datetime, float, float], ...]:
    """(valid hour UTC, dir_from_deg, speed_mps) of every hour with both values, ascending."""
    series = json.loads(Path(path_str).read_text())
    rows = [(datetime.fromisoformat(t).replace(tzinfo=timezone.utc), float(d), float(v))
            for t, v, d in zip(series["time"], series["wind_speed_10m"], series["wind_direction_10m"])
            if v is not None and d is not None]
    return tuple(sorted(rows))


def wind_series(start: datetime, minutes: float, path: Path | None = None
                ) -> list[tuple[float, float, float]]:
    """Hourly ``(minutes_from_start, wind_dir_from_deg, wind_speed_mps)`` covering
    ``[start, start + minutes]`` from the committed Open-Meteo fixture.

    The first sample is the hour containing ``start``, at ``minutes_from_start`` 0 or below; then one
    sample per following hour boundary inside the window. Hours with a null speed or direction are
    skipped. Raises ValueError when the window holds no usable value.
    """
    series = _read_wind(str(path or WIND_FILE))
    start = start.astimezone(timezone.utc)
    hour0 = start.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=float(minutes))
    out = [((valid - start).total_seconds() / 60.0, d, v)
           for valid, d, v in series if hour0 <= valid <= end]
    if not out:
        raise ValueError(f"no usable hourly wind in [{start.isoformat()}, {end.isoformat()}] "
                         f"in {path or WIND_FILE}")
    return out


def fire_state(p: Perimeter, wind: tuple[float, float]) -> FireState:
    """FireState seeded on ``p`` (EPSG:25831) at its ``observed_at`` with
    ``(wind_dir_from_deg, wind_speed_mps)`` -- the first entry of the wind series."""
    return FireState(cluster_id=p.id, t=p.observed_at, perimeter=p.geom,
                     wind_dir_deg=float(wind[0]), wind_speed_mps=float(wind[1]))


# ---------------------------------------------------------------------------------------- scoring

_MASKS: dict[tuple[str, Grid], np.ndarray] = {}


def mask_of(p: Perimeter, grid: Grid) -> np.ndarray:
    """Cached boolean footprint of ``p`` on ``grid`` (``spread.seed_mask``). Read-only."""
    key = (p.id, grid)
    m = _MASKS.get(key)
    if m is None:
        m = spread.seed_mask(p.geom, grid)
        m.flags.writeable = False
        _MASKS[key] = m
    return m


def _ha(mask: np.ndarray, grid: Grid) -> float:
    return float(mask.sum()) * grid.cell ** 2 / 1e4


def _ratio(num: float, den: float) -> float:
    """num / den, with inf for a positive numerator over zero and 1.0 for 0 / 0. Never raises."""
    if den == 0.0:
        return 1.0 if num == 0.0 else float("inf")
    return num / den


@dataclass(frozen=True)
class PairScore:
    """One replay window scored against the next recorded perimeter, all areas on the shared grid.

    Ratios whose denominator is zero are ``inf`` when the numerator is positive and ``1.0`` when
    both are zero; none of them raises.
    """
    label: str
    minutes: float
    seed_area_ha: float          # perimeter i footprint
    observed_area_ha: float      # perimeter i+1 footprint
    observed_new_ha: float       # observed minus seed
    predicted_area_ha: float     # seed + cells with a finite arrival_p50 (burn_prob >= 0.5)
    predicted_new_ha: float
    p10_area_ha: float           # seed + cells with a finite arrival_p10
    p90_area_ha: float           # seed + cells with a finite arrival_p90
    area_ratio: float            # predicted_area_ha / observed_area_ha
    growth_ratio: float          # predicted_new_ha / observed_new_ha
    growth_frac_of_observed: float   # predicted_new_ha / observed_area_ha
    iou: float                   # p50 footprint vs observed footprint, cell-wise
    p10_recall: float            # observed-new cells with burn_prob >= 0.1 (1.0 when there are none)

    def row(self) -> str:
        """One fixed-width line for a report table (see :data:`SCORE_HEADER`)."""
        return (f"{self.label:<17}{self.minutes:>7.0f}{self.seed_area_ha:>9.0f}"
                f"{self.observed_area_ha:>9.0f}{self.observed_new_ha:>9.0f}"
                f"{self.predicted_area_ha:>10.0f}{self.predicted_new_ha:>9.0f}"
                f"{self.p10_area_ha:>9.0f}{self.p90_area_ha:>9.0f}"
                f"{self.area_ratio:>8.2f}{self.growth_ratio:>8.2f}"
                f"{self.iou:>7.2f}{self.p10_recall:>7.2f}")


SCORE_HEADER = (f"{'pair':<17}{'min':>7}{'seed':>9}{'obs':>9}{'obs_new':>9}"
                f"{'pred':>10}{'pred_new':>9}{'p10':>9}{'p90':>9}"
                f"{'area_r':>8}{'grow_r':>8}{'iou':>7}{'p10rec':>7}")


def score_pair(pair: Pair, raster, grid) -> PairScore:
    """Score an ``ArrivalRaster`` from ``pair.prev`` against ``pair.nxt`` on ``grid``."""
    seed = mask_of(pair.prev, grid)
    observed = mask_of(pair.nxt, grid)
    pred50 = seed | np.isfinite(raster.arrival_p50)
    pred10 = seed | np.isfinite(raster.arrival_p10)
    pred90 = seed | np.isfinite(raster.arrival_p90)
    observed_new = observed & ~seed

    seed_ha = _ha(seed, grid)
    observed_ha = _ha(observed, grid)
    observed_new_ha = _ha(observed_new, grid)
    predicted_ha = _ha(pred50, grid)
    predicted_new_ha = predicted_ha - seed_ha
    union = float((pred50 | observed).sum())
    n_new = int(observed_new.sum())
    return PairScore(
        label=pair.label,
        minutes=pair.minutes,
        seed_area_ha=seed_ha,
        observed_area_ha=observed_ha,
        observed_new_ha=observed_new_ha,
        predicted_area_ha=predicted_ha,
        predicted_new_ha=predicted_new_ha,
        p10_area_ha=_ha(pred10, grid),
        p90_area_ha=_ha(pred90, grid),
        area_ratio=_ratio(predicted_ha, observed_ha),
        growth_ratio=_ratio(predicted_new_ha, observed_new_ha),
        growth_frac_of_observed=_ratio(predicted_new_ha, observed_ha),
        iou=_ratio(float((pred50 & observed).sum()), union),
        p10_recall=1.0 if n_new == 0 else float((raster.burn_prob[observed_new] >= 0.10).sum()) / n_new,
    )


# ----------------------------------------------------------------------------------------- replay

def run_pair(pair: Pair, grid, cfg: dict, *, n_runs: int = 20, seed: int = 0,
             wind_path: Path | None = None, constant_wind: bool = False) -> tuple[object, PairScore]:
    """Seed the CA on ``pair.prev`` at its ``observed_at``, run to ``pair.nxt.observed_at``, score
    against ``pair.nxt``. Returns ``(ArrivalRaster, PairScore)``.

    Uses the hourly wind series unless ``constant_wind``, which keeps the first sample for the whole
    window (the pre-calibration behaviour, for comparison).
    """
    series = wind_series(pair.prev.observed_at, pair.minutes, wind_path)
    fs = fire_state(pair.prev, (series[0][1], series[0][2]))
    raster = spread.run_ca(fs, grid, n_runs=n_runs, horizon_min=math.ceil(pair.minutes), seed=seed,
                           cfg=cfg, wind_series=None if constant_wind else series)
    return raster, score_pair(pair, raster, grid)


def calm_wind_area_ha(cfg: dict, *, hours: float = 12.0, radius_m: float = 1000.0,
                      n_runs: int = 10, seed: int = 0, cell: float = 100.0) -> float:
    """p50 burned area (ha) after ``hours`` of zero wind from a ``radius_m`` disc.

    The grid is sized so the fire cannot reach its edge (one cell per step is the CA's speed limit).
    Handoff 001 sanity check: tens of hectares for a plausible parameter set, not thousands.
    """
    horizon_min = float(hours) * 60.0
    n_steps = int(horizon_min // float(cfg["minutes_per_step"]))
    half = radius_m + (n_steps + 2) * cell
    gav = Grid.gavarres()
    cx, cy = (gav.xmin + gav.xmax) / 2, (gav.ymin + gav.ymax) / 2
    grid = Grid(cx - half, cy - half, cx + half, cy + half, cell)
    fs = FireState(cluster_id="calm", t=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
                   perimeter=Point(cx, cy).buffer(radius_m), wind_dir_deg=0.0, wind_speed_mps=0.0)
    raster = spread.run_ca(fs, grid, n_runs=n_runs, horizon_min=int(horizon_min), seed=seed, cfg=cfg)
    burned = spread.seed_mask(fs.perimeter, grid) | np.isfinite(raster.arrival_p50)
    return _ha(burned, grid)
