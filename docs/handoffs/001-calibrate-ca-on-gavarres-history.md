# Handoff 001: calibrate the CA spread model against the recorded Gavarres incident

Written 2026-09-19 on branch `claude/window-ranking` (commit `058acdb`).

## Why

`gavarres_real_0001..0003` rank real facilities from the v0 cellular-automaton ensemble
(`fireline/spread.py` `run_ca`, parameters `config.CA`) seeded on each recorded July perimeter.
That was a deliberate stop-gap: no provider forecast covers the July incident, and the CA is
labelled `"ca_ensemble (labelled enrichment, not validated)"`. `config.CA` says "Not calibrated on
Gavarres" and it shows. Under the light wind of the later snapshots the model percolates almost
isotropically and reaches 94 of 99 located facilities within 12 h (sequence 3), while the real fire
grew from 1,263 ha to 3,867 ha in about 16 h and then stopped growing.

The goal of this task is to make the CA reproduce the recorded growth of that incident to within a
stated tolerance, record how well it does, and re-label the enrichment accordingly. It does not turn
the CA into a provider forecast, and it does not change the contract rule that nothing is inferred
from distance (readme section 5.2, CONTRACTS 2.2).

## What the historical record contains

All of it is committed; nothing needs the network.

**Seven satellite perimeters of incident `5769dcea`** in
`fixtures/fire/deepfire/real/20260919T131342Z_satellite-perimeters.json` (one FeatureCollection,
properties `computed_at`, `observed_watermark`, `area_m2`, `n_hotspots`, `active`). Areas below are
recomputed in EPSG:25831 with `fireline.grid.lonlat_to_xy`.

| observed (`observed_watermark`) | available (`computed_at`) | area | used as snapshot |
|---|---|---|---|
| 2026-07-03 11:47Z | 13:20Z | 1,263 ha | seq 1 |
| 2026-07-03 12:06Z | 14:02Z | 1,764 ha | |
| 2026-07-03 12:53Z | 14:22Z | 2,730 ha | |
| 2026-07-03 13:25Z | 15:32Z | 3,537 ha | seq 2 |
| 2026-07-03 13:46Z | 15:42Z | 3,651 ha | |
| 2026-07-03 14:40Z | 16:27Z | 3,914 ha | |
| 2026-07-04 04:14Z | 06:31Z | 3,867 ha | seq 3 |

Growth is fast between 11:47Z and 13:25Z (about 1,400 ha per hour), slows sharply after 13:25Z,
and is over by 14:40Z. The last perimeter is slightly smaller than the sixth, which is perimeter
re-estimation noise, not shrinkage.

**Hourly wind** for 2026-07-03..05 at twelve 0.1 degree grid points is in `data/wind/` (gitignored,
rebuilt by `scripts/fetch_data.py wind`); the point the snapshots use, 41.90N 3.05E, is committed as
`fixtures/wind/41.90_3.05.json` with provenance in `fixtures/wind/README.md`. It is Open-Meteo
Previous Runs output (model `ecmwf_ifs025`, `_previous_day1` slice), a model product not an
observation. During the growth window it reads:

| hour (UTC) | speed m/s | from deg |
|---|---|---|
| 07-03 11:00 | 6.61 | 3 |
| 07-03 12:00 | 7.24 | 6 |
| 07-03 13:00 | 5.80 | 15 |
| 07-03 14:00 | 3.72 | 36 |
| 07-03 15:00 | 2.64 | 65 |
| 07-03 16:00 | 1.92 | 62 |
| 07-04 04:00 | 2.14 | 349 |
| 07-04 06:00 | 1.58 | 305 |

So the observed growth coincides with a northerly wind of 6 to 7 m/s, and the fire stops when the
wind drops below about 3 m/s and veers. A model that spreads 10,000 ha under 1.6 m/s is wrong in a
way this record can measure.

**Not available:** fuel or land-cover raster, DEM. `run_ca` accepts `fuel` and `slope` arrays and
`tests/test_spread.py` covers them, but no data is in the repo. Suppression activity is also
unrecorded, and the fire almost certainly stopped partly because of it. Treat "matches the record"
as "matches the record given the wind, with suppression absorbed into the parameters", and say so.

## How the CA works and which knobs exist

`spread.run_ca(fire_state, grid, fuel=None, slope=None, n_runs, horizon_min, seed, cfg)`:

- Each step of `minutes_per_step` minutes, every burning cell tries to ignite each of its 8
  neighbours with `p = p0 * fuel * wind_factor * slope_factor`, clipped to 1. A cell burns for one
  step then is burned.
- `wind_factor = exp(wind_c1 * v) * exp(v * wind_c2 * (cos(theta) - 1))`, theta being the angle
  between the spread direction and the direction the wind blows towards (`_wind_factors`).
- Each of `n_runs` runs perturbs wind speed by plus or minus 30 percent and direction by plus or
  minus 20 degrees. `aggregate_runs` turns arrival steps into `arrival_p10/p50/p90` and
  `burn_prob`; p10 is what becomes `fire_arrival_at`.
- Current `config.CA = {"p0": 0.5, "minutes_per_step": 4, "wind_c1": 0.045, "wind_c2": 0.30,
  "slope_a": 0.078}`. With `p0` 0.5 and eight neighbours the no-wind ignition probability per step
  is far above the site-percolation threshold, which is why calm wind still burns everything.

The snapshot side is `scripts/make_snapshots.py`: `real_fire_state` (perimeter to EPSG:25831,
wind at the hour containing `as_of` from the nearest fixture file), `real_arrival` (one `run_ca`
call per snapshot with `CA_N_RUNS = 20`, `CA_HORIZON_MIN = 720`, `CA_SEED = 0`), and
`enrichment_config()` (a config copy with `forecast_enrichment` on). Note that `real_arrival`
uses one wind sample for the whole 12 h horizon; the wind changes a lot over those hours.

## Proposed method

1. **Build a replay harness** (suggested: `scripts/calibrate_ca.py`, library code in
   `fireline/calibrate.py` if it grows). For each consecutive pair of recorded perimeters (i, i+1),
   seed the CA on perimeter i at its `observed_watermark`, run to the `observed_watermark` of
   perimeter i+1, and compare the p50 burned footprint with perimeter i+1. Use observation times, not
   `computed_at`, for the physics. Six pairs are available; the 11:47Z to 13:25Z pairs carry the
   growth signal and the 14:40Z to 04:14Z pair carries the "it stopped" signal.
2. **Score** per pair and overall: area ratio (predicted / observed), intersection-over-union of the
   p50 footprint with the observed perimeter, and the fraction of observed newly burned cells that
   the ensemble burns in at least 10 percent of runs (the p10 recall, since p10 is what feeds
   ranking). Report the ensemble spread (p10 and p90 areas) as well, so a fit that only works on the
   median is visible.
3. **Add hourly wind stepping.** `run_ca` takes one wind vector. Either extend it with an optional
   `wind_series: list[(minutes_from_start, dir_deg, speed_mps)]` and change `_wind_factors` per
   step, or run it hour by hour re-seeding on the previous hour's burned set. The first is cleaner
   and keeps determinism; the second needs no signature change. The 12 h horizon of the snapshots
   crosses the calm evening, so this matters more than any single parameter.
4. **Fit `p0`, `wind_c1`, `wind_c2`** by grid search (the run is a few seconds; a 10 by 10 by 10
   grid is minutes). Keep `minutes_per_step` fixed unless the fit demands it, because it also sets
   the maximum spread speed (one cell per step, 100 m per 4 min = 25 m/s), which is already far
   above any real rate. Constrain `p0` so that the calm-wind spread rate is small: a useful sanity
   check is the no-wind burned area after 12 h from a 1 km disc, which should be tens of hectares,
   not thousands.
5. **Hold out** the 12:53Z to 13:25Z pair (the largest single growth step) and the overnight pair
   from the fit, and report the holdout scores separately. With six pairs this is thin, but it is
   the difference between "fitted" and "reproduces".
6. **Decide the label.** Whatever the fit achieves, record it in `config.CA` as a versioned dict
   (add `"version"`, `"calibrated_on"`, `"holdout_area_ratio"` or similar) and change the
   enrichment label in `snapshot._enrich_forecast` from "not validated" to something that names the
   evidence, for example `"ca_ensemble (calibrated on incident 5769dcea, 2026-07-03; not a provider
   forecast)"`. The label must still make clear it is a model, and `fire_arrival_basis` keeps the
   `p10` semantics. Update the readme status paragraph, CONTRACTS 2.2 and the fixtures section,
   V4_GAPS row "CA spread ensemble", and `fixtures/real_area/README.md` (generated by
   `write_real_area`).
7. **Regenerate and re-check.** `make snapshots`, then the demo counts per sequence through
   `fireline.ui_state` (see `tests/test_ui_state.py` for how a `Session` is built with a temporary
   database). Expect fewer ranked assets in sequence 3 and a slightly earlier or later opening list
   in sequence 1. Update `tests/test_snapshot.py`
   `test_committed_real_snapshots_carry_labelled_ca_arrivals_and_policy_evacuation` to the new
   counts and label, and add a calibration test that asserts the holdout scores stay within the
   recorded tolerance so a later parameter edit cannot silently regress. Record the outcome in
   `VALIDATION.md` (`scripts/validate.py --write` plus a new check, or a hand-written section that
   names the script and date).

## Acceptance

- Per-pair area ratio within 0.5 to 2.0 on every fitted pair and on the holdout pairs, with the
  overnight pair predicting growth under 20 percent of the observed area (the fire had stopped).
- Calm-wind sanity check documented (no-wind 12 h burned area from a 1 km disc).
- `config.CA` versioned; enrichment label updated; every doc that says "not calibrated" or "not
  validated" for the CA updated to the new wording; the readme rules on distance untouched.
- Full test suite green; `make snapshots` deterministic across two runs; demo opens on sequence 1
  with a nonzero ranked count.

## Known limits to state in the write-up

- One incident, seven perimeters, one wind point: this is calibration, not validation. A second
  incident (the two June/July clusters near Alfarràs have 12 perimeters between them in Deepfire,
  see the memory note on the 2026-09-19 probe) would be the first real test, but there are no cached
  facilities or wind there yet.
- Perimeter timestamps are satellite watermarks with unknown latency inside each hour.
- Suppression is absorbed into the parameters, which will make the model under-predict an
  unsuppressed fire.
- Without a fuel layer the model cannot distinguish forest from the built-up coast; expect the
  Sant Feliu de Guíxols and Platja d'Aro facilities to be over-predicted whatever the fit.

## Files

- `fireline/spread.py` (CA, `_wind_factors`, `run_ca`, `aggregate_runs`), `fireline/config.py`
  (`CA`), `fireline/grid.py` (`Grid.gavarres`, 100 m cells)
- `scripts/make_snapshots.py` (`real_fire_state`, `real_arrival`, `enrichment_config`,
  `CA_N_RUNS`, `CA_HORIZON_MIN`, `CA_SEED`, `write_real_area`)
- `fireline/snapshot.py` (`_enrich_forecast`, the label strings)
- `fixtures/fire/deepfire/real/20260919T131342Z_satellite-perimeters.json`,
  `fixtures/wind/41.90_3.05.json`, `data/wind/` for the other grid points
- `tests/test_spread.py`, `tests/test_snapshot.py`
- `readme.md` (status paragraph, section 4, section 5.2), `CONTRACTS.md` 2.2 and fixtures,
  `V4_GAPS.md`, `VALIDATION.md`
