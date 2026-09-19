"""FireState helpers: JSON round-trip, hotspot perimeter and FROS."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon

from fireline import fire_state as fsm
from fireline.fire_state import FireState
from fireline.grid import lonlat_to_xy

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_ignition.json"
T0 = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)


def test_fixture_loads_and_matches_spec():
    fs = FireState.from_json(FIXTURE)
    assert fs.t == T0 and fs.t.tzinfo is not None
    assert fs.wind_dir_deg == 340 and fs.wind_speed_mps == 8
    assert isinstance(fs.perimeter, Polygon)
    x, y = lonlat_to_xy(3.03, 41.95)
    assert fs.perimeter.contains(Point(x, y))
    assert fs.perimeter.area == pytest.approx(3.14159 * 300 ** 2, rel=0.02)
    assert len(fs.hotspots) == 3
    assert [h["t"] for h in fs.hotspots] == [T0 - timedelta(minutes=20), T0 - timedelta(minutes=10), T0]
    assert all(h["t"].tzinfo is not None for h in fs.hotspots)


def test_fros_direction_south_east_for_fixture():
    fs = FireState.from_json(FIXTURE)
    d, v = fsm.fros_from_hotspots(fs.hotspots, fs.t)
    assert d is not None and 110 <= d <= 160
    assert v is not None and 0.05 < v < 1.0
    assert fs.fros_dir_deg == pytest.approx(d)
    assert fs.fros_speed_mps == pytest.approx(v)


def test_fros_none_when_insufficient():
    hs = [{"lon": 3.0, "lat": 41.9, "t": T0, "frp": 5.0, "source": "s"}]
    assert fsm.fros_from_hotspots(hs, T0) == (None, None)
    assert fsm.fros_from_hotspots([], T0) == (None, None)
    # everything older than the window is ignored
    old = [{"lon": 2.99, "lat": 41.89, "t": T0 - timedelta(hours=2), "frp": 5.0, "source": "s"}] + hs
    assert fsm.fros_from_hotspots(old, T0, window_min=30) == (None, None)
    assert fsm.fros_from_hotspots(old, T0, window_min=180)[0] is not None


def test_fros_uses_last_three_slots_and_frp_weights():
    base = {"frp": 10.0, "source": "s"}
    hs = [dict(base, lon=3.00, lat=41.90, t=T0 - timedelta(minutes=40)),   # outside 30 min window
          dict(base, lon=3.00, lat=41.90, t=T0 - timedelta(minutes=20)),
          dict(base, lon=3.00, lat=41.91, t=T0)]                             # due north
    d, v = fsm.fros_from_hotspots(hs, T0)
    assert d == pytest.approx(0.0, abs=2.0)
    assert v == pytest.approx(1111 / 1200, rel=0.05)
    # FRP weighting: a strong hotspot to the east dominates the last slot's centroid
    hs.append(dict(base, lon=3.02, lat=41.90, t=T0, frp=1000.0))
    d2, _ = fsm.fros_from_hotspots(hs, T0)
    assert 80 <= d2 <= 100


def test_perimeter_from_hotspots():
    fs = FireState.from_json(FIXTURE)
    perim_all = fsm.perimeter_from_hotspots(fs.hotspots, fs.t, buffer_m=375)
    assert isinstance(perim_all, (Polygon, MultiPolygon)) and not perim_all.is_empty
    assert perim_all.area > 3.14159 * 375 ** 2
    for h in fs.hotspots:
        assert perim_all.contains(Point(*lonlat_to_xy(h["lon"], h["lat"])))
    # as-of guard: only hotspots at or before t
    perim_first = fsm.perimeter_from_hotspots(fs.hotspots, fs.t - timedelta(minutes=20))
    assert perim_first.area == pytest.approx(3.14159 * 375 ** 2, rel=0.02)
    assert fsm.perimeter_from_hotspots(fs.hotspots, fs.t - timedelta(hours=1)).is_empty


def test_json_round_trip(tmp_path):
    fs = FireState.from_json(FIXTURE)
    out = tmp_path / "fs.json"
    fs.to_json(out)
    fs2 = FireState.from_json(out)
    assert fs2.cluster_id == fs.cluster_id
    assert fs2.t == fs.t
    assert fs2.perimeter.equals(fs.perimeter)
    assert fs2.hotspots == fs.hotspots
    assert fs2.fros_dir_deg == fs.fros_dir_deg
    assert fs2.wind_dir_deg == fs.wind_dir_deg and fs2.wind_speed_mps == fs.wind_speed_mps
    text = out.read_text()
    assert "POLYGON" in text and "2026-07-03T08:00:00+00:00" in text


def test_synthetic_accepts_naive_time_as_utc():
    fs = FireState.synthetic(3.03, 41.95, datetime(2026, 7, 3, 8, 0), radius_m=100, wind_dir_deg=90, wind_speed_mps=3)
    assert fs.t == T0
    assert fs.fros_dir_deg is None and fs.hotspots == []
    assert fs.perimeter.area == pytest.approx(3.14159 * 100 ** 2, rel=0.02)
