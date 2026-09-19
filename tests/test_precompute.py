"""Precompute pipeline on a small grid with few runs: writes and reloads a scenario (no network, no LLM key)."""

import importlib.util
import json
from pathlib import Path

import numpy as np

from fireline.fire_state import FireState
from fireline.grid import Grid, lonlat_to_xy
from fireline.scenario import Scenario

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"

_spec = importlib.util.spec_from_file_location("precompute", ROOT / "scripts" / "precompute.py")
precompute = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(precompute)


def small_grid(fs: FireState, half_km: float = 6.0, cell: float = 200.0) -> Grid:
    cx, cy = fs.perimeter.centroid.x, fs.perimeter.centroid.y
    h = half_km * 1000.0
    return Grid(cx - h, cy - h, cx + h, cy + h, cell)


def test_build_scenario_writes_and_reloads(tmp_path):
    fs = FireState.from_json(FIX / "synthetic_ignition.json")
    grid = small_grid(fs)
    sc, out = precompute.build_scenario("smoke_0800", fs, closures=["GI-660"], n_runs=5, horizon_min=240,
                                        grid=grid, out_dir=tmp_path, triage=False)
    assert out == tmp_path / "smoke_0800"
    for f in ("scenario.json", "scenario.npz", "burn_prob.png", "layers.json"):
        assert (out / f).exists(), f
    assert (out / "burn_prob.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    back = Scenario.from_json(out / "scenario.json")
    assert back.id == sc.id and len(back.assets) == len(sc.assets) == 12
    assert back.closures == ["GI-660"] and back.horizon_min == 240 and back.spread_source == "ca"
    assert back.arrival is not None and back.arrival.burn_prob.shape == grid.shape
    assert 0.0 < back.arrival.burn_prob.max() <= 1.0
    for a in back.assets:
        assert a["tier"] in ("act_now", "prepare", "monitor") and "decision" in a

    layers = json.loads((out / "layers.json").read_text())
    w, s, e, n = layers["bounds_lonlat"]
    assert w < e and s < n and len(layers["roads"]) > 0 and layers["perimeter_lonlat"]
    lon, lat = layers["centre_lonlat"]
    x, y = lonlat_to_xy(lon, lat)
    assert abs(x - fs.perimeter.centroid.x) < 5 and abs(y - fs.perimeter.centroid.y) < 5
    assert any(r["cut_min"] is not None for r in layers["roads"])


def test_advanced_perimeter_grows_downwind():
    fs = FireState.from_json(FIX / "synthetic_ignition.json")
    grid = small_grid(fs)
    from fireline import spread
    ar = spread.run_ca(fs, grid, n_runs=5, horizon_min=240, seed=1)
    poly = precompute.advanced_perimeter(ar, minutes=120, seed_perimeter=fs.perimeter)
    assert poly.area > fs.perimeter.area
    # wind from 340 deg: the new perimeter's centroid moves roughly south-south-east of the ignition
    assert poly.centroid.y < fs.perimeter.centroid.y
    rings = precompute.polygon_lonlat_rings(poly)
    assert rings and all(len(r) >= 4 for r in rings)


def test_png_encoder_roundtrip_shape(tmp_path):
    rgba = precompute.burn_prob_rgba(np.array([[0.0, 0.5], [1.0, np.nan]]))
    assert rgba.shape == (2, 2, 4) and rgba[0, 0, 3] == 0 and rgba[1, 0, 3] == 220
    precompute.write_png_rgba(tmp_path / "x.png", rgba)
    assert (tmp_path / "x.png").stat().st_size > 30
