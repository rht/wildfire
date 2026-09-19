#!/usr/bin/env python
"""Precompute the v0 demo scenarios into data/scenarios/<name>/ (no network, no LLM key needed).

v0 engine only: spread (CA), routing and confine/evacuate decisions are gated behind
`config.FEATURES` and are labelled enrichment in the v4 path. This script is not the default
path any more (`scripts/make_snapshots.py` builds the v4 snapshots); run it explicitly for the demo.

    .venv/bin/python scripts/precompute.py            # all three demo scenarios
    .venv/bin/python scripts/precompute.py --fast     # 10 runs, 360 min (smoke test)

Each scenario directory holds `scenario.json` + `scenario.npz` (Scenario.to_json), `burn_prob.png`
(RGBA raster for the map) and `layers.json` (bounds, perimeter, roads with cut times) for the UI.
`data/scenarios/index.json` lists the scenarios and the 08:00 -> 10:00 diff.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
import zlib
from datetime import timedelta
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fireline import config, spread  # noqa: E402
from fireline.exposure import TIER_RANK  # noqa: E402
from fireline.fire_state import FireState  # noqa: E402
from fireline.grid import Grid, xy_to_lonlat  # noqa: E402
from fireline.routing import RoadGraph  # noqa: E402
from fireline.scenario import Scenario  # noqa: E402

FIX = ROOT / "fixtures"
OUT = ROOT / "data" / "scenarios"
# SCT closure of GI-660 plus the Can Xic track (the track closure is what raises the no_exit review flag).
CLOSURES_1000 = ["GI-660", "Camí de Can Xic"]


# ----------------------------------------------------------------------------- helpers
def write_png_rgba(path: Path, rgba: np.ndarray) -> None:
    """Minimal PNG encoder (8-bit RGBA) with numpy + zlib, no extra dependencies."""
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[i].astype(np.uint8).tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def burn_prob_rgba(burn_prob: np.ndarray) -> np.ndarray:
    """Yellow -> red ramp, alpha proportional to probability (transparent where 0)."""
    p = np.clip(np.nan_to_num(burn_prob), 0.0, 1.0)
    rgba = np.zeros(p.shape + (4,), dtype=np.uint8)
    rgba[..., 0] = 255
    rgba[..., 1] = (220 * (1.0 - p)).astype(np.uint8)
    rgba[..., 2] = 0
    rgba[..., 3] = (np.where(p > 0, 60 + 160 * p, 0)).astype(np.uint8)
    return rgba


def grid_bounds_lonlat(grid: Grid) -> list[float]:
    w, s = xy_to_lonlat(grid.xmin, grid.ymin)
    e, n = xy_to_lonlat(grid.xmax, grid.ymax)
    return [float(w), float(s), float(e), float(n)]


def polygon_lonlat_rings(geom) -> list[list[list[float]]]:
    """Exterior rings of a (Multi)Polygon in EPSG:25831 as lon/lat coordinate lists."""
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    rings = []
    for p in polys:
        if p.is_empty:
            continue
        xs, ys = zip(*p.exterior.coords)
        lon, lat = xy_to_lonlat(np.array(xs), np.array(ys))
        rings.append([[float(a), float(b)] for a, b in zip(lon, lat)])
    return rings


def advanced_perimeter(arrival: spread.ArrivalRaster, minutes: float, prob_min: float = 0.5, seed_perimeter=None):
    """Union of the cell squares with burn_prob >= prob_min and arrival_p50 <= minutes (EPSG:25831)."""
    g = arrival.grid
    mask = (arrival.burn_prob >= prob_min) & (arrival.arrival_p50 <= minutes)
    rows, cols = np.nonzero(mask)
    x, y = g.to_xy(rows, cols)
    half = g.cell / 2.0
    squares = [box(xi - half, yi - half, xi + half, yi + half) for xi, yi in zip(x, y)]
    if seed_perimeter is not None:
        squares.append(seed_perimeter)
    poly = unary_union(squares) if squares else shapely.Polygon()
    return poly.buffer(1.0).buffer(-1.0).simplify(10.0)


def roads_layer(rg: RoadGraph) -> list[dict]:
    out = []
    for u, v, d in rg.g.edges(data=True):
        lu, lv = rg.node_lonlat(u), rg.node_lonlat(v)
        cut = d.get("cut_min", float("inf"))
        out.append({"name": d.get("name"), "highway": d.get("highway"), "closed": bool(d.get("closed")),
                    "cut_min": None if cut == float("inf") else round(float(cut), 1),
                    "path": [[float(lu[0]), float(lu[1])], [float(lv[0]), float(lv[1])]]})
    return out


def summarize(sc: Scenario) -> dict:
    tiers, decisions = {}, {}
    for a in sc.assets:
        tiers[a["tier"]] = tiers.get(a["tier"], 0) + 1
        d = (a.get("decision") or {}).get("decision", "?")
        decisions[d] = decisions.get(d, 0) + 1
    tiers = dict(sorted(tiers.items(), key=lambda kv: TIER_RANK.get(kv[0], 9)))
    return {"tiers": tiers, "decisions": decisions, "queue": len(sc.queue),
            "needs_review": sum(1 for a in sc.assets if a["needs_review"])}


def feature_label() -> str:
    """One-line label of which gated v0 features are on (they are enrichment, not the v4 default path)."""
    flags = config.FEATURES
    state = ", ".join(f"{k}={'on' if v else 'off'}" for k, v in flags.items())
    return (f"v0 engine: spread/routing/decisions are labelled enrichment under config.FEATURES ({state}); "
            "this script runs them explicitly for the v0 demo")


def run_triage(sc: Scenario) -> str:
    """Call fireline.agent.triage with the fake LLM if the v0 agent entry point still exists."""
    try:
        from fireline.agent import triage
    except (ImportError, AttributeError):
        return "v0 agent.triage not available; skipped triage"
    try:
        triage(sc, llm=None)
        return "triage ran (fake LLM)"
    except Exception as exc:  # the agent lane's module; keep the demo build alive
        return f"WARNING triage failed: {type(exc).__name__}: {exc}"


# ----------------------------------------------------------------------------- pipeline
def build_scenario(name: str, fire_state: FireState, closures: list[str] | None = None, *,
                   n_runs: int = 50, horizon_min: int = 720, grid: Grid | None = None,
                   out_dir: Path | None = None, fixtures: Path = FIX, seed: int = 0,
                   triage: bool = True, source: str = "synthetic") -> tuple[Scenario, Path]:
    """Run CA -> Scenario -> (agent triage) and write data/scenarios/<name>/. Returns (scenario, dir)."""
    grid = grid or Grid.gavarres()
    out = (out_dir or OUT) / name
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    arrival = spread.run_ca(fire_state, grid, n_runs=n_runs, horizon_min=horizon_min, seed=seed)
    with open(fixtures / "assets.json", encoding="utf-8") as stream:
        assets = json.load(stream)
    with open(fixtures / "destinations.json", encoding="utf-8") as stream:
        dests = json.load(stream)
    rg = RoadGraph.from_fixture(fixtures / "roads.json")
    sc = Scenario.run(fire_state, arrival, assets, dests, rg, config, closures=list(closures or []))
    note = run_triage(sc) if triage else "triage skipped"
    sc.to_json(out / "scenario.json")
    write_png_rgba(out / "burn_prob.png", burn_prob_rgba(arrival.burn_prob))
    cx, cy = fire_state.perimeter.centroid.x, fire_state.perimeter.centroid.y
    clon, clat = xy_to_lonlat(cx, cy)
    layers = {
        "name": name, "scenario_id": sc.id, "source": source, "t": sc.t.isoformat(),
        "bounds_lonlat": grid_bounds_lonlat(grid), "centre_lonlat": [float(clon), float(clat)],
        "perimeter_lonlat": polygon_lonlat_rings(fire_state.perimeter),
        "perimeter_area_ha": round(fire_state.perimeter.area / 1e4, 1),
        "wind_dir_deg": fire_state.wind_dir_deg, "wind_speed_mps": fire_state.wind_speed_mps,
        "roads": roads_layer(rg), "closures": sc.closures,
        "burned_ha_p50_at_horizon": round(float((arrival.burn_prob >= 0.5).sum()) * grid.cell ** 2 / 1e4, 0),
    }
    (out / "layers.json").write_text(json.dumps(layers), encoding="utf-8")
    s = summarize(sc)
    print(f"[{name}] id={sc.id} t={sc.t:%Y-%m-%d %H:%M}Z wind {fire_state.wind_dir_deg:.0f}deg/{fire_state.wind_speed_mps:.0f}m/s "
          f"closures={sc.closures or '-'} | tiers {s['tiers']} | decisions {s['decisions']} | "
          f"queue {s['queue']} | review {s['needs_review']} | {note} | {time.time() - t0:.1f}s")
    return sc, out


def build_all(out_dir: Path = OUT, n_runs: int = 50, horizon_min: int = 720, grid: Grid | None = None) -> dict:
    grid = grid or Grid.gavarres()
    fs0 = FireState.from_json(FIX / "synthetic_ignition.json")
    sc0, _ = build_scenario("synthetic_0800", fs0, n_runs=n_runs, horizon_min=horizon_min, grid=grid, out_dir=out_dir)

    perim = advanced_perimeter(sc0.arrival, minutes=120, seed_perimeter=fs0.perimeter)
    t1 = fs0.t + timedelta(hours=2)
    fs1 = FireState(cluster_id=fs0.cluster_id, t=t1, perimeter=perim, hotspots=fs0.hotspots,
                    fros_dir_deg=fs0.fros_dir_deg, fros_speed_mps=fs0.fros_speed_mps,
                    wind_dir_deg=fs0.wind_dir_deg, wind_speed_mps=fs0.wind_speed_mps)
    sc1, _ = build_scenario("synthetic_1000", fs1, closures=CLOSURES_1000, n_runs=n_runs, horizon_min=horizon_min,
                            grid=grid, out_dir=out_dir)

    # Distinct cluster_id so the scenario id (cluster, t, config) differs from synthetic_1000.
    fs2 = FireState(cluster_id=fs0.cluster_id + "-whatif-east", t=t1, perimeter=perim, hotspots=fs0.hotspots,
                    fros_dir_deg=fs0.fros_dir_deg, fros_speed_mps=fs0.fros_speed_mps,
                    wind_dir_deg=90.0, wind_speed_mps=fs0.wind_speed_mps)
    sc2, _ = build_scenario("whatif_east_1000", fs2, closures=CLOSURES_1000, n_runs=n_runs, horizon_min=horizon_min,
                            grid=grid, out_dir=out_dir, source="synthetic what-if (wind from 90 deg)")

    index = {
        "mode": "Replay (synthetic)",
        "config_hash": config.config_hash(),
        "scenarios": [
            {"name": n, "scenario_id": s.id, "t": s.t.isoformat(), "source": src, "closures": s.closures,
             "cluster_id": s.cluster_id, "spread_source": s.spread_source, "summary": summarize(s)}
            for n, s, src in (("synthetic_0800", sc0, "synthetic ignition fixture"),
                              ("synthetic_1000", sc1, "synthetic fire advanced 2 h from the 08:00 CA run"),
                              ("whatif_east_1000", sc2, "what-if: wind from 90 deg at 10:00"))
        ],
        "diff_0800_1000": sc0.diff(sc1),
        "diff_1000_whatif_east": sc1.diff(sc2),
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"index -> {out_dir / 'index.json'}")
    print("diff 08:00 -> 10:00: " + ("; ".join(index["diff_0800_1000"]) or "no tier/decision changes"))
    print("diff 10:00 -> what-if east: " + ("; ".join(index["diff_1000_whatif_east"]) or "no tier/decision changes"))
    return index


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--horizon", type=int, default=720)
    ap.add_argument("--fast", action="store_true", help="10 runs, 360 min")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    if a.fast:
        a.runs, a.horizon = 10, 360
    print(feature_label())
    t0 = time.time()
    build_all(a.out, n_runs=a.runs, horizon_min=a.horizon)
    print(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
