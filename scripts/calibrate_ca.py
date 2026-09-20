#!/usr/bin/env python
"""Fit the CA spread parameters to the recorded Gavarres incident (handoff 001, steps 4-5).

    .venv/bin/python scripts/calibrate_ca.py                     # coarse sweep, refinement, report
    .venv/bin/python scripts/calibrate_ca.py --stage coarse      # coarse sweep only (quick look)
    .venv/bin/python scripts/calibrate_ca.py --free-step         # also search minutes_per_step
    .venv/bin/python scripts/calibrate_ca.py --json <path>       # where the result JSON goes

Offline: everything replays committed fixtures through `fireline.calibrate`. The record is the seven
satellite perimeters of incident 5769dcea in
`fixtures/fire/deepfire/real/20260919T131342Z_satellite-perimeters.json` (observation times, never
`computed_at`) and the hourly Open-Meteo wind at 41.90N 3.05E in `fixtures/wind/41.90_3.05.json`,
stepped through `spread.run_ca(wind_series=...)`. The grid is `Grid.gavarres()` (100 m cells).

Free parameters: `p0`, `wind_c1`, `wind_c2` (and `minutes_per_step` only with `--free-step`).
`slope_a` is left alone: there is no DEM in the repo, so nothing exercises it.

Objective, minimised over the fitted pairs:

    J = mean over fitted pairs of ( log( (pred_new_ha + E) / (obs_new_ha + E) ) )**2
        + 0.25 * mean over fitted pairs of (1 - iou)
    with E = 10.0 ha   (smoothing so a zero denominator or numerator stays finite)

The growth log-ratio is the substance; the IoU term is a weak shape/direction regulariser. The fit is
on growth, not on total area, because the seed perimeter dominates the total-area ratio: every short
pair scores between 0.89 and 1.48 on total area even with parameters that are four orders of
magnitude wrong on the calm-wind check. Total area is still reported -- it is the handoff's
acceptance criterion -- but it is a weak objective.

Six consecutive pairs. Fitted: indices 0, 1, 3, 4. Held out: index 2 (12:53Z->13:25Z, the largest
single growth step) and index 5 (14:40Z->04:14Z, the overnight "it stopped" pair).

Limits of what this can establish, from the handoff:
  - one incident, seven perimeters, one wind point: this is calibration, not validation;
  - perimeter timestamps are satellite watermarks with unknown latency inside the hour;
  - the wind is an ECMWF model product (Open-Meteo previous runs), not an observation;
  - suppression is unrecorded and is absorbed into the fitted parameters, so the model will
    under-predict an unsuppressed fire;
  - there is no fuel or land-cover layer, so the built-up coast is indistinguishable from forest.

The script prints the before table, the search progress, the after table for all six pairs, the
seed-stability spread, the calm-wind check, the facility-count preview for the three snapshot
`as_of` times, and the acceptance verdict, and writes all of it to `reports/ca-calibration.json`.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fireline import calibrate as cal
from fireline import config, spread
from fireline.grid import Grid

ROOT = Path(__file__).resolve().parents[1]

FITTED = (0, 1, 3, 4)
HOLDOUT = (2, 5)
OVERNIGHT = 5
E_HA = 10.0
IOU_WEIGHT = 0.25

# Coarse search space (handoff 001 step 4 suggests these ranges).
COARSE_P0 = [round(x, 5) for x in np.geomspace(0.01, 0.5, 8)]
COARSE_C1 = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
COARSE_C2 = [0.0, 0.15, 0.30, 0.45, 0.60]
STEP_CHOICES = [2, 4, 8]          # only searched with --free-step

# Snapshot `as_of` times of gavarres_real_0001..0003 and the perimeter each seeds on.
SNAPSHOT_AS_OF = [
    ("gavarres_real_0001", "2026-07-03T13:20:01Z", "2026-07-03T11:47"),
    ("gavarres_real_0002", "2026-07-03T15:32:24Z", "2026-07-03T13:25"),
    ("gavarres_real_0003", "2026-07-04T06:31:53Z", "2026-07-04T04:14"),
]
SNAPSHOT_HORIZON_MIN = 720
ASSETS_FILE = ROOT / "fixtures" / "real_area" / "assets_gavarres.json"
GIT_REVISION_TIMEOUT_SECONDS = 5.0


# ------------------------------------------------------------------------------------- objective

def objective(scores: dict[int, cal.PairScore], idxs=FITTED) -> float:
    """J for the fitted pairs (see the module docstring); lower is better."""
    logs, ious = [], []
    for i in idxs:
        s = scores[i]
        logs.append(math.log((s.predicted_new_ha + E_HA) / (s.observed_new_ha + E_HA)) ** 2)
        ious.append(1.0 - s.iou)
    return sum(logs) / len(logs) + IOU_WEIGHT * (sum(ious) / len(ious))


def cfg_of(p0: float, c1: float, c2: float, step: int) -> dict:
    return {"p0": float(p0), "minutes_per_step": int(step), "wind_c1": float(c1),
            "wind_c2": float(c2), "slope_a": config.CA["slope_a"]}


def key_of(cfg: dict) -> tuple:
    return (round(cfg["p0"], 6), round(cfg["wind_c1"], 6), round(cfg["wind_c2"], 6),
            int(cfg["minutes_per_step"]))


def score_set(prs, grid, cfg, idxs, *, n_runs: int, seed: int) -> dict[int, cal.PairScore]:
    return {i: cal.run_pair(prs[i], grid, cfg, n_runs=n_runs, seed=seed)[1] for i in idxs}


# ---------------------------------------------------------------------------------------- tables

def print_table(scores: dict[int, cal.PairScore], holdout=HOLDOUT) -> None:
    print("    " + cal.SCORE_HEADER)
    for i in sorted(scores):
        print(("H   " if i in holdout else "    ") + scores[i].row())


def score_json(s: cal.PairScore) -> dict:
    d = {k: getattr(s, k) for k in ("label", "minutes", "seed_area_ha", "observed_area_ha",
                                    "observed_new_ha", "predicted_area_ha", "predicted_new_ha",
                                    "p10_area_ha", "p90_area_ha", "area_ratio", "growth_ratio",
                                    "growth_frac_of_observed", "iou", "p10_recall")}
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in d.items()}


# ----------------------------------------------------------------------------------------- search

def sweep(prs, grid, cfgs, *, n_runs: int, seed: int, tag: str) -> list[tuple[float, dict]]:
    """Score every cfg on the fitted pairs; return (J, cfg) ascending. Prints progress."""
    out, t0 = [], time.time()
    for n, cfg in enumerate(cfgs, 1):
        scores = score_set(prs, grid, cfg, FITTED, n_runs=n_runs, seed=seed)
        j = objective(scores)
        out.append((j, cfg))
        if n % 10 == 0 or n == len(cfgs):
            best = min(j for j, _ in out)
            print(f"  [{tag}] {n:>4}/{len(cfgs)}  {time.time() - t0:>6.0f}s  best J={best:.4f}",
                  flush=True)
    out.sort(key=lambda jc: jc[0])
    return out


def refine_grid(cfg: dict, free_step: bool) -> list[dict]:
    """Local grid around one cfg: p0 by x0.6/x0.8/x1.25/x1.67, c1 and c2 by +-0.05 and +-0.10."""
    p0s = sorted({round(cfg["p0"] * f, 6) for f in (0.6, 0.8, 1.0, 1.25, 1.67)})
    c1s = sorted({round(max(cfg["wind_c1"] + d, 0.0), 6) for d in (-0.10, -0.05, 0.0, 0.05, 0.10)})
    c2s = sorted({round(max(cfg["wind_c2"] + d, 0.0), 6) for d in (-0.10, 0.0, 0.10)})
    steps = STEP_CHOICES if free_step else [cfg["minutes_per_step"]]
    return [cfg_of(p, a, b, s) for p in p0s for a in c1s for b in c2s for s in steps]


# ------------------------------------------------------------------------------- extra diagnostics

def seed_stability(prs, grid, cfg, seeds, *, n_runs: int) -> dict:
    out = {}
    for sd in seeds:
        scores = score_set(prs, grid, cfg, range(len(prs)), n_runs=n_runs, seed=sd)
        out[sd] = scores
        print(f"  seed {sd}:")
        print_table(scores)
    return out


def located_assets() -> list[tuple[float, float]]:
    rows = json.loads(ASSETS_FILE.read_text(encoding="utf-8"))["assets"]
    return [(float(r["longitude"]), float(r["latitude"])) for r in rows
            if r.get("latitude") is not None and r.get("longitude") is not None]


def facility_counts(perims, grid, cfg, *, n_runs: int, seed: int) -> dict:
    """Reached-facility counts at the three snapshot `as_of` times, hourly series vs constant wind.

    Also counts how many located facilities fall inside each recorded perimeter. That is the ground
    truth the 12 h forecasts are being compared against: the fire stopped at 3,914 ha and reached
    none of the 99, so every facility the uncalibrated model "reaches" is a false positive.
    """
    assets = located_assets()
    lons = np.array([a[0] for a in assets])
    lats = np.array([a[1] for a in assets])
    in_perim = {}
    for p in perims:
        m = cal.mask_of(p, grid)
        v = grid.sample(m.astype(float), lons, lats)
        in_perim[p.hhmm] = int(np.sum(np.nan_to_num(v) > 0.5))
    print(f"  recorded perimeters contain {in_perim} of {len(assets)} located facilities")
    by_obs = {p.observed_at.strftime("%Y-%m-%dT%H:%M"): p for p in perims}
    out = []
    for name, as_of_s, obs_key in SNAPSHOT_AS_OF:
        as_of = datetime.fromisoformat(as_of_s.replace("Z", "+00:00")).astimezone(timezone.utc)
        perim = by_obs[obs_key]
        series = cal.wind_series(as_of, SNAPSHOT_HORIZON_MIN)
        fs = cal.fire_state(perim, (series[0][1], series[0][2]))
        row = {"snapshot": name, "as_of": as_of_s, "perimeter_observed_at": obs_key + "Z",
               "wind_first_sample": {"dir_from_deg": series[0][1], "speed_mps": series[0][2]},
               "n_located_assets": len(assets)}
        for label, ws in (("series", series), ("constant", None)):
            r = spread.run_ca(fs, grid, n_runs=n_runs, horizon_min=SNAPSHOT_HORIZON_MIN, seed=seed,
                              cfg=cfg, wind_series=ws)
            v = grid.sample(r.arrival_p10, lons, lats)
            reached = int(np.sum(np.isfinite(v)))   # NaN outside the grid counts as unreached
            row[f"reached_{label}"] = reached
            row[f"burned_ha_{label}"] = float(np.isfinite(r.arrival_p10).sum()) * grid.cell ** 2 / 1e4
        row["assets_in_seed_perimeter"] = in_perim[perim.hhmm]
        out.append(row)
        print(f"  {name}  as_of {as_of_s}  seeded on {obs_key}Z  "
              f"series {row['reached_series']}/{len(assets)}  "
              f"constant {row['reached_constant']}/{len(assets)}  "
              f"(seed perimeter already contains {in_perim[perim.hhmm]})", flush=True)
    return {"n_located_assets": len(assets), "assets_inside_recorded_perimeters": in_perim,
            "snapshots": out}


def verdict(scores: dict[int, cal.PairScore]) -> dict:
    ratios = {i: scores[i].area_ratio for i in sorted(scores)}
    c1_bad = [scores[i].label for i in sorted(scores) if not 0.5 <= scores[i].area_ratio <= 2.0]
    c2_val = scores[OVERNIGHT].growth_frac_of_observed
    return {
        "criterion_1_area_ratio_0.5_to_2.0": {
            "pass": not c1_bad, "failing_pairs": c1_bad,
            "per_pair": {scores[i].label: ratios[i] for i in ratios}},
        "criterion_2_overnight_growth_under_20pct": {
            "pass": bool(c2_val < 0.20), "growth_frac_of_observed": c2_val,
            "pair": scores[OVERNIGHT].label},
    }


def _git_revision(root: Path, *, timeout_seconds: float = GIT_REVISION_TIMEOUT_SECONDS) -> str | None:
    """Return the current revision when available; report provenance remains optional."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
            timeout=timeout_seconds,
            encoding="utf-8",
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


# ------------------------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Calibrate fireline.config.CA on the Gavarres record.")
    ap.add_argument("--n-runs", type=int, default=20, help="ensemble size for final scoring")
    ap.add_argument("--sweep-runs", type=int, default=8, help="ensemble size during the sweep")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--stage", choices=("coarse", "full"), default="full")
    ap.add_argument("--top", type=int, default=5, help="coarse survivors to refine around")
    ap.add_argument("--finalists", type=int, default=24,
                    help="sweep candidates re-scored at --n-runs over the stability seeds")
    ap.add_argument("--free-step", action="store_true",
                    help="also search minutes_per_step in {2, 4, 8} (only if the fit demands it)")
    ap.add_argument("--stability-seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--json", type=Path, default=ROOT / "reports" / "ca-calibration.json")
    args = ap.parse_args()

    t_start = time.time()
    perims = cal.load_perimeters()
    prs = cal.pairs(perims)
    grid = Grid.gavarres()
    allidx = range(len(prs))
    print(f"grid {grid.shape} cell {grid.cell:.0f} m; {len(perims)} perimeters, {len(prs)} pairs; "
          f"fitted {list(FITTED)}, holdout {list(HOLDOUT)} (marked H)\n")

    print("BEFORE -- current config.CA", json.dumps(config.CA))
    before = score_set(prs, grid, dict(config.CA), allidx, n_runs=args.n_runs, seed=args.seed)
    print_table(before)
    j_before = objective(before)
    calm_before = cal.calm_wind_area_ha(dict(config.CA))
    print(f"  J(fitted) = {j_before:.4f}   calm-wind 12 h area = {calm_before:,.0f} ha\n")

    coarse_cfgs = [cfg_of(p, c1, c2, 4) for p in COARSE_P0 for c1 in COARSE_C1 for c2 in COARSE_C2]
    print(f"COARSE sweep: {len(coarse_cfgs)} configs, n_runs {args.sweep_runs}, "
          f"fitted pairs only", flush=True)
    ranked = sweep(prs, grid, coarse_cfgs, n_runs=args.sweep_runs, seed=args.seed, tag="coarse")
    print("  best 5 coarse:")
    for j, c in ranked[:5]:
        print(f"    J={j:.4f}  p0={c['p0']:<8} c1={c['wind_c1']:<5} c2={c['wind_c2']:<5} "
              f"step={c['minutes_per_step']}")

    candidates = list(ranked)
    if args.stage == "full":
        seen = {key_of(c) for _, c in ranked}
        ref_cfgs = []
        for _, c in ranked[:args.top]:
            for rc in refine_grid(c, args.free_step):
                if key_of(rc) not in seen:
                    seen.add(key_of(rc))
                    ref_cfgs.append(rc)
        print(f"\nREFINE sweep: {len(ref_cfgs)} configs around the best {args.top}", flush=True)
        ref = sweep(prs, grid, ref_cfgs, n_runs=args.sweep_runs, seed=args.seed, tag="refine")
        candidates = sorted(candidates + ref, key=lambda jc: jc[0])
        print("  best 5 overall (sweep ensemble):")
        for j, c in candidates[:5]:
            print(f"    J={j:.4f}  p0={c['p0']:<8} c1={c['wind_c1']:<5} c2={c['wind_c2']:<5} "
                  f"step={c['minutes_per_step']}")

    sel_seeds = list(args.stability_seeds)
    print(f"\nFINAL scoring of the best {args.finalists} at n_runs {args.n_runs} over seeds "
          f"{sel_seeds}, ranked by mean J (a single-seed rank is a seed artefact: the sweep runs at "
          f"n_runs {args.sweep_runs} and its ranking does not survive re-scoring)", flush=True)
    finalists = []
    for j_sweep, c in candidates[:args.finalists]:
        js = {sd: objective(score_set(prs, grid, c, FITTED, n_runs=args.n_runs, seed=sd))
              for sd in sel_seeds}
        j_mean = sum(js.values()) / len(js)
        finalists.append({"cfg": c, "j_sweep": j_sweep, "j": j_mean,
                          "j_by_seed": {str(k): v for k, v in js.items()},
                          "j_worst_seed": max(js.values())})
        print(f"    J={j_mean:.4f} (worst seed {max(js.values()):.4f}, sweep {j_sweep:.4f})  "
              f"p0={c['p0']:<9} c1={c['wind_c1']:<5} c2={c['wind_c2']:<5} "
              f"step={c['minutes_per_step']}", flush=True)
    finalists.sort(key=lambda f: f["j"])
    best = finalists[0]["cfg"]
    j_after = finalists[0]["j"]

    after = score_set(prs, grid, best, allidx, n_runs=args.n_runs, seed=args.seed)
    print(f"\nCHOSEN {json.dumps(best)}   mean J(fitted) over seeds {sel_seeds} = {j_after:.4f}"
          f"   (seed {args.seed}: {objective(after):.4f})")
    print("AFTER -- all six pairs")
    print_table(after)
    calm_after = cal.calm_wind_area_ha(best)
    print(f"  calm-wind 12 h area from a 1 km disc: {calm_before:,.0f} ha -> {calm_after:,.1f} ha\n")

    print(f"SEED STABILITY at n_runs {args.n_runs}, seeds {args.stability_seeds}")
    stab = seed_stability(prs, grid, best, args.stability_seeds, n_runs=args.n_runs)
    spread_rows = []
    for i in allidx:
        ar = [stab[s][i].area_ratio for s in args.stability_seeds]
        gr = [stab[s][i].predicted_new_ha for s in args.stability_seeds]
        io = [stab[s][i].iou for s in args.stability_seeds]
        spread_rows.append({"pair": after[i].label, "holdout": i in HOLDOUT,
                            "area_ratio": ar, "area_ratio_range": max(ar) - min(ar),
                            "predicted_new_ha": gr, "predicted_new_ha_range": max(gr) - min(gr),
                            "iou": io, "iou_range": max(io) - min(io)})
    print(f"  {'pair':<17}{'area_r min..max':>22}{'pred_new min..max':>24}{'iou min..max':>20}")
    for r in spread_rows:
        print(f"  {r['pair']:<17}{min(r['area_ratio']):>10.2f}..{max(r['area_ratio']):<11.2f}"
              f"{min(r['predicted_new_ha']):>11.0f}..{max(r['predicted_new_ha']):<12.0f}"
              f"{min(r['iou']):>9.2f}..{max(r['iou']):<10.2f}")
    j_by_seed = {s: objective(stab[s]) for s in args.stability_seeds}
    print("  J(fitted) by seed: " + ", ".join(f"{s}={j:.4f}" for s, j in j_by_seed.items()))

    print(f"\nFACILITY PREVIEW (720 min, n_runs {args.n_runs}, seed {args.seed}, "
          f"finite arrival_p10)")
    fac_after = facility_counts(perims, grid, best, n_runs=args.n_runs, seed=args.seed)

    v = verdict(after)
    v["criterion_3_calm_wind_ha"] = {"before_ha": calm_before, "after_ha": calm_after,
                                     "handoff_target": "tens of hectares, not thousands",
                                     "pass": bool(calm_after < 1000.0)}
    print("\nACCEPTANCE")
    c1 = v["criterion_1_area_ratio_0.5_to_2.0"]
    print(f"  1 area ratio 0.5..2.0 on every pair: {'PASS' if c1['pass'] else 'FAIL'}"
          + ("" if c1["pass"] else "  failing: " + ", ".join(c1["failing_pairs"])))
    c2 = v["criterion_2_overnight_growth_under_20pct"]
    print(f"  2 overnight growth < 20 % of observed area: "
          f"{'PASS' if c2['pass'] else 'FAIL'}  ({c2['growth_frac_of_observed']:.3f})")
    c3 = v["criterion_3_calm_wind_ha"]
    print(f"  3 calm-wind 12 h area documented: {calm_after:,.1f} ha "
          f"({'tens of hectares' if c3['pass'] else 'still large'})")

    commit = _git_revision(ROOT)
    payload = {
        "produced_on": "2026-09-19",
        "git_commit": commit,
        "script": "scripts/calibrate_ca.py",
        "incident": "5769dcea",
        "data": {
            "perimeters": "fixtures/fire/deepfire/real/20260919T131342Z_satellite-perimeters.json",
            "wind": "fixtures/wind/41.90_3.05.json (Open-Meteo ecmwf_ifs025 previous runs, hourly)",
            "grid": {"shape": list(grid.shape), "cell_m": grid.cell, "name": "Grid.gavarres()"},
        },
        "objective": ("J = mean over fitted pairs of (log((pred_new_ha + E)/(obs_new_ha + E)))**2 "
                      "+ 0.25 * mean over fitted pairs of (1 - iou), with E = 10.0 ha"),
        "split": {"fitted": [prs[i].label for i in FITTED],
                  "holdout": [prs[i].label for i in HOLDOUT],
                  "holdout_reason": {prs[2].label: "largest single growth step",
                                     prs[5].label: "overnight, the fire had stopped"}},
        "search_space": {"p0": COARSE_P0, "wind_c1": COARSE_C1, "wind_c2": COARSE_C2,
                         "minutes_per_step": STEP_CHOICES if args.free_step else [4],
                         "slope_a": "fixed at config.CA (no DEM in the repo)",
                         "coarse_configs": len(coarse_cfgs),
                         "sweep_n_runs": args.sweep_runs, "final_n_runs": args.n_runs,
                         "seed": args.seed,
                         "refinement": "local grid around the best %d coarse configs" % args.top,
                         "finalists_rescored": args.finalists,
                         "selection_seeds": sel_seeds},
        "before": {"cfg": dict(config.CA), "j_fitted": j_before,
                   "calm_wind_12h_ha": calm_before,
                   "pairs": [score_json(before[i]) for i in allidx]},
        "chosen": {"cfg": best,
                   "j_fitted_mean_over_seeds": j_after,
                   "j_fitted_by_seed": finalists[0]["j_by_seed"],
                   "selection": ("lowest mean J over seeds %s at n_runs %d among the best %d sweep "
                                 "candidates" % (sel_seeds, args.n_runs, args.finalists)),
                   "calm_wind_12h_ha": calm_after,
                   "pairs_seed": args.seed,
                   "pairs": [score_json(after[i]) for i in allidx]},
        "finalists": finalists,
        "seed_stability": {"seeds": list(args.stability_seeds), "n_runs": args.n_runs,
                           "j_fitted_by_seed": {str(s): j for s, j in j_by_seed.items()},
                           "per_pair": spread_rows},
        "facility_preview": {
            "assets": "fixtures/real_area/assets_gavarres.json",
            "horizon_min": SNAPSHOT_HORIZON_MIN, "n_runs": args.n_runs, "seed": args.seed,
            "basis": "facility cell has a finite arrival_p10",
            "before_counts_recorded_in_handoff": [18, 65, 94],
            "n_located_assets": fac_after["n_located_assets"],
            "assets_inside_recorded_perimeters": fac_after["assets_inside_recorded_perimeters"],
            "ground_truth_note": ("no located facility lies inside any recorded perimeter, so the "
                                  "recorded outcome of this incident is 0 reached facilities; the "
                                  "18/65/94 of the uncalibrated model are all false positives"),
            "after": fac_after["snapshots"]},
        "acceptance": v,
        "limits": [
            "one incident, seven perimeters, one wind point: calibration, not validation",
            "perimeter timestamps are satellite watermarks with unknown latency inside the hour",
            "wind is an ECMWF model product, not an observation",
            "suppression is unrecorded and absorbed into the parameters",
            "no fuel or land-cover layer: the built-up coast is indistinguishable from forest",
            "no DEM: slope_a is unexercised and was not fitted",
        ],
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"\nwrote {args.json.relative_to(ROOT) if args.json.is_relative_to(ROOT) else args.json}"
          f"  ({time.time() - t_start:.0f}s total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
