"""Fire state at time t: perimeter, hotspots, forward-rate-of-spread vector (PLAN 6.1)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from shapely import wkt
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from .grid import lonlat_to_xy


@dataclass
class FireState:
    cluster_id: str
    t: datetime
    perimeter: Polygon | MultiPolygon          # EPSG:25831
    hotspots: list[dict] = field(default_factory=list)   # {lon, lat, t, frp, source}
    fros_dir_deg: float | None = None          # direction of spread, deg clockwise from north
    fros_speed_mps: float | None = None
    wind_dir_deg: float = 0.0                  # meteorological: direction wind blows FROM
    wind_speed_mps: float = 0.0

    # ----- construction helpers -------------------------------------------------------------

    @classmethod
    def synthetic(cls, lon: float, lat: float, t: datetime, radius_m: float = 300.0,
                  wind_dir_deg: float = 0.0, wind_speed_mps: float = 0.0,
                  cluster_id: str = "synthetic", hotspots: list[dict] | None = None) -> "FireState":
        """Circular ignition of ``radius_m`` around lon/lat. If ``hotspots`` are given the FROS
        vector is derived from them; otherwise it is None."""
        t = _ensure_utc(t)
        x, y = lonlat_to_xy(lon, lat)
        hotspots = [dict(h, t=_ensure_utc(h["t"])) for h in (hotspots or [])]
        fros_dir, fros_speed = fros_from_hotspots(hotspots, t) if hotspots else (None, None)
        return cls(cluster_id=cluster_id, t=t, perimeter=Point(x, y).buffer(radius_m),
                   hotspots=hotspots, fros_dir_deg=fros_dir, fros_speed_mps=fros_speed,
                   wind_dir_deg=float(wind_dir_deg), wind_speed_mps=float(wind_speed_mps))

    # ----- JSON ------------------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "t": _iso(self.t),
            "perimeter_wkt": self.perimeter.wkt,
            "perimeter_crs": "EPSG:25831",
            "hotspots": [dict(h, t=_iso(h["t"])) if isinstance(h.get("t"), datetime) else dict(h)
                         for h in self.hotspots],
            "fros_dir_deg": self.fros_dir_deg,
            "fros_speed_mps": self.fros_speed_mps,
            "wind_dir_deg": self.wind_dir_deg,
            "wind_speed_mps": self.wind_speed_mps,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FireState":
        return cls(
            cluster_id=d["cluster_id"],
            t=parse_time(d["t"]),
            perimeter=wkt.loads(d["perimeter_wkt"]),
            hotspots=[dict(h, t=parse_time(h["t"])) for h in d.get("hotspots", [])],
            fros_dir_deg=d.get("fros_dir_deg"),
            fros_speed_mps=d.get("fros_speed_mps"),
            wind_dir_deg=float(d.get("wind_dir_deg", 0.0)),
            wind_speed_mps=float(d.get("wind_speed_mps", 0.0)),
        )

    def to_json(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def from_json(cls, path) -> "FireState":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ----- time helpers ---------------------------------------------------------------------------

def _ensure_utc(t: datetime) -> datetime:
    if t.tzinfo is None:
        return t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return _ensure_utc(t).isoformat()


def parse_time(s: str | datetime) -> datetime:
    """ISO 8601 -> tz-aware UTC datetime ('Z' suffix accepted; naive means UTC)."""
    if isinstance(s, datetime):
        return _ensure_utc(s)
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return _ensure_utc(datetime.fromisoformat(s))


# ----- hotspot-derived quantities --------------------------------------------------------------

def _hotspots_upto(hotspots: list[dict], t: datetime) -> list[dict]:
    t = _ensure_utc(t)
    return [h for h in hotspots if parse_time(h["t"]) <= t]


def perimeter_from_hotspots(hotspots: list[dict], t: datetime, buffer_m: float = 375.0) -> Polygon | MultiPolygon:
    """Union of ``buffer_m`` discs (EPSG:25831) around hotspots detected at or before ``t``.
    Empty Polygon when there are none."""
    hs = _hotspots_upto(hotspots, t)
    if not hs:
        return Polygon()
    lon = np.array([h["lon"] for h in hs], dtype=float)
    lat = np.array([h["lat"] for h in hs], dtype=float)
    x, y = lonlat_to_xy(lon, lat)
    return unary_union([Point(xi, yi).buffer(buffer_m) for xi, yi in zip(x, y)])


def fros_from_hotspots(hotspots: list[dict], t: datetime, window_min: float = 30.0,
                       n_slots: int = 3) -> tuple[float | None, float | None]:
    """Forward rate of spread from FRP-weighted hotspot centroids.

    Hotspots with ``t - window_min <= hotspot.t <= t`` are grouped by detection time into
    slots; the last ``n_slots`` slots are kept and the displacement of the FRP-weighted centroid
    from the earliest to the latest of those slots gives ``(direction_deg, speed_mps)``.
    Direction is degrees clockwise from north. ``(None, None)`` when fewer than two slots or no
    displacement.
    """
    t = _ensure_utc(t)
    t0 = t - timedelta(minutes=window_min)
    hs = [h for h in hotspots if t0 <= parse_time(h["t"]) <= t]
    if not hs:
        return None, None
    slots: dict[datetime, list[dict]] = {}
    for h in hs:
        slots.setdefault(parse_time(h["t"]), []).append(h)
    times = sorted(slots)[-n_slots:]
    if len(times) < 2:
        return None, None

    def centroid(group):
        lon = np.array([g["lon"] for g in group], dtype=float)
        lat = np.array([g["lat"] for g in group], dtype=float)
        w = np.array([max(float(g.get("frp") or 0.0), 0.0) for g in group], dtype=float)
        if w.sum() <= 0:
            w = np.ones_like(w)
        x, y = lonlat_to_xy(lon, lat)
        return float(np.average(x, weights=w)), float(np.average(y, weights=w))

    x0, y0 = centroid(slots[times[0]])
    x1, y1 = centroid(slots[times[-1]])
    dt_s = (times[-1] - times[0]).total_seconds()
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dt_s <= 0 or dist <= 0:
        return None, None
    direction = math.degrees(math.atan2(dx, dy)) % 360.0
    return direction, dist / dt_s
