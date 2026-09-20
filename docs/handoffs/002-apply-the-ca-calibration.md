# Handoff 002: apply the CA calibration, and decide what to do about it reaching nothing

Written 2026-09-19 on branch `claude/ca-calibration` (commits `c36d1ec`, `f31158e`), worktree
`.worktrees/ca-calibration`, branched from `claude/window-ranking` at `f8e417f`. Continues
`001-calibrate-ca-on-gavarres-history.md`.

Steps 1 to 5 of handoff 001 are done and committed. Step 6 (version `config.CA`, re-label the
enrichment) and step 7 (regenerate, re-check, update the docs and tests) are **not** done, because
the fit produced a result that makes the handoff's own acceptance line unreachable and the right
response is a judgement call, not a code change. That decision is the first thing below.

## What was built

**`spread.run_ca(..., wind_series=...)`** (`c36d1ec`). An optional ascending sequence of
`(minutes_from_start, wind_dir_from_deg, wind_speed_mps)`. A step uses the last sample at or before
its start time; the first sample also covers the time before it; non-ascending raises `ValueError`;
`None` or empty keeps the constant `fire_state` wind. One run's speed factor (+-30 %) and direction
offset (+-20 deg) are drawn once and applied to every sample, so an ensemble member stays a coherent
variant of the whole wind history rather than independent noise per hour. The no-series path is
bit-identical to the previous implementation (checked against arrays recorded before the edit), so
the committed snapshots did not move. Seven tests in `tests/test_spread.py`.

**`fireline/calibrate.py`** (`c36d1ec`). The replay harness: `load_perimeters` (all seven perimeters
of incident `5769dcea` in EPSG:25831 with their observed watermarks), `pairs`, `wind_series` (hourly
samples from the committed Open-Meteo fixture), `fire_state`, `run_pair`, `score_pair`, `PairScore`,
`mask_of`, `calm_wind_area_ha`. Fifteen tests in `tests/test_calibrate.py`.

**`scripts/calibrate_ca.py`** and **`reports/ca-calibration.json`** (`f31158e`). The grid search and
its machine-readable result. The driver runs offline in 374 s and is deterministic for a seed; two
runs give byte-identical output apart from the elapsed-time line. Flags: `--n-runs --sweep-runs
--seed --stage {coarse,full} --top --finalists --free-step --stability-seeds --json`.

The measured perimeter areas agree with the table in handoff 001 to within 1 ha, and with the
provider's own `area_m2` to within 0.1 %. The wind fixture has no null hours over 2026-07-03..05.

## The fit

```python
CA = {"p0": 0.006, "minutes_per_step": 4, "wind_c1": 0.7, "wind_c2": 0.30, "slope_a": 0.078}
```

`p0` 0.5 -> 0.006 and `wind_c1` 0.045 -> 0.7. `minutes_per_step` did **not** have to be freed;
`--free-step` confirms the fit does not want it (J at n_runs 8 is 0.410 at 2 min, 0.204 at 4,
0.214 at 8). `wind_c2` and `slope_a` are unchanged, `slope_a` because there is no DEM to exercise it.

Objective, fitted on four pairs and holding out two:

```
J = mean over fitted pairs of (log((pred_new_ha + E)/(obs_new_ha + E)))**2
    + 0.25 * mean over fitted pairs of (1 - iou),        E = 10.0 ha
```

The growth term, not the total-area ratio, is the substance: the seed perimeter dominates the total
area, so five of the six pairs already scored between 0.89 and 1.48 on area ratio with parameters
that were four orders of magnitude wrong. Fitted pairs `11:47->12:06`, `12:06->12:53`,
`13:25->13:46`, `13:46->14:40`; held out `12:53->13:25` (the largest single growth step) and
`14:40->04:14` (overnight, the fire had stopped).

Search: `p0` log-spaced 0.01..0.5 (8), `wind_c1` 0..1.0 (6), `wind_c2` 0..0.6 (5) = 240 coarse
configs at n_runs 8, a local refinement around the best five, then the best 24 candidates re-scored
at n_runs 20 over seeds 0, 1 and 2 and ranked by **mean** J. That last step matters: the n_runs 8
sweep ranking does not survive re-scoring (best sweep J 0.0699 -> mean J 0.5102, and the eventual
winner ranked 10th in the sweep), and picking on seed 0 alone would have given
`p0 0.008, wind_c1 0.65` with mean J 0.3297 — worse and less stable. Mean J of the chosen point is
0.1632 (0.1502 / 0.2423 / 0.0971 by seed) against a baseline of 1.6979.

### Before and after, n_runs 20, seed 0, hourly wind series, `Grid.gavarres()`

Areas in hectares. `H` marks a holdout pair. `area_r` = predicted total / observed total;
`grow_r` = predicted new / observed new.

```
    pair                 min     seed      obs  obs_new      pred pred_new      p10      p90  area_r  grow_r    iou p10rec
BEFORE (config.CA as committed)
    11:47Z->12:06Z        19     1263     1770      511      1581      318     1825     1323    0.89    0.62   0.76   0.50
    12:06Z->12:53Z        47     1770     2731      962      2622      852     3233     1898    0.96    0.89   0.80   0.74
H   12:53Z->13:25Z        32     2731     3547      816      3653      922     4218     2891    1.03    1.13   0.73   0.60
    13:25Z->13:46Z        21     3547     3662      157      4248      701     4686     3722    1.16    4.46   0.85   0.98
    13:46Z->14:40Z        54     3662     3926      265      5804     2142     6821     4503    1.48    8.08   0.66   0.96
H   14:40Z->04:14Z       814     3926     3882       67     40397    36471    61455    17229   10.41  544.34   0.10   1.00

AFTER (p0 0.006, wind_c1 0.7)
    11:47Z->12:06Z        19     1263     1770      511      1636      373     1907     1281    0.92    0.73   0.76   0.52
    12:06Z->12:53Z        47     1770     2731      962      3310     1540     3942     1789    1.21    1.60   0.70   0.78
H   12:53Z->13:25Z        32     2731     3547      816      3135      404     4031     2762    0.88    0.50   0.77   0.55
    13:25Z->13:46Z        21     3547     3662      157      3757      210     4600     3547    1.03    1.34   0.92   0.95
    13:46Z->14:40Z        54     3662     3926      265      3878      216     4613     3662    0.99    0.82   0.93   0.77
H   14:40Z->04:14Z       814     3926     3882       67      3926        0     4165     3926    1.01    0.00   0.96   0.07
```

### Seed stability, n_runs 20, seeds 0/1/2

```
pair                area_r min..max    pred_new min..max     iou min..max
11:47Z->12:06Z        0.88..0.92           302..373            0.76..0.76
12:06Z->12:53Z        0.99..1.21           926..1540           0.70..0.79
12:53Z->13:25Z  H     0.88..0.90           377..461            0.76..0.77
13:25Z->13:46Z        1.01..1.04           135..264            0.91..0.93
13:46Z->14:40Z        0.98..0.99           171..216            0.93..0.93
14:40Z->04:14Z  H     1.01..1.01             0..0              0.96..0.96
```

No acceptance criterion is seed-dependent. Growth in hectares does swing about 1.5x on the two long
pairs; that is inherent to the fit, see the tensions below.

### Acceptance (handoff 001), all three PASS

| criterion | before | after | target |
|---|---|---|---|
| area ratio on every fitted and holdout pair | 0.89 .. 10.41 | 0.88 .. 1.21 | 0.5 .. 2.0 |
| overnight pair growth as a fraction of observed area | 5.44 (36,471 ha) | 0.000 (0 ha) | < 0.20 |
| no-wind 12 h burned area from a 1 km disc | 129,321 ha | 316 ha | tens of ha |

The 316 ha is the rasterised 1 km disc itself (pi km2 = 314 ha on a 100 m grid), so the calm-wind
spread in 12 h is **zero cells**, not "tens of hectares".

## The finding that blocks step 7

With the calibrated parameters and the hourly wind series, the 12 h ensemble reaches **0 of the 99
located facilities in all three real sequences**, against 18 / 65 / 94 today.

| sequence | as_of | seeded on | hourly series | constant wind | committed today |
|---|---|---|---|---|---|
| `gavarres_real_0001` | 2026-07-03T13:20:01Z | 11:47Z perimeter | 0 | 15 | 18 |
| `gavarres_real_0002` | 2026-07-03T15:32:24Z | 13:25Z perimeter | 0 | 0 | 65 |
| `gavarres_real_0003` | 2026-07-04T06:31:53Z | 04:14Z perimeter | 0 | 0 | 94 |

12 h burned area including the seed: seq 1 2,514 ha with the series against 19,040 ha with one
constant sample (seed 1,263 ha); seq 2 3,679 / 3,679 (seed 3,547); seq 3 3,929 / 3,929 (seed 3,926).
Holding 5.80 m/s for 12 h on sequence 1 burns five times the whole recorded fire, so the series is
doing real work and the regenerated snapshots should use it.

**0 is the right answer.** The union of all seven recorded perimeters is 4,020 ha and **no located
facility lies inside any of them**: the nearest is CFA de Calonge 561 m outside the burn edge, then
Llar d'infants La Palmera at 1,272 m, and the median located facility is 8.2 km away. The burn is
7 x 10 km of the Gavarres massif itself (centroid 3.027E 41.890N), forested and essentially
uninhabited; the facilities ring it, in Calonge, La Bisbal d'Empordà, Cruïlles, Palamós and
Palafrugell. The recorded outcome for the located set is zero reached, so today's 18 / 65 / 94 are
**all false positives** and the calibrated 0 / 0 / 0 matches the record.

**But state the claim narrowly.** The located set is 97 schools, 1 hospital and 1 care home. All 42
campsites and 27 of the 28 care homes have no coordinates in their Gencat registers and are
`location_unknown`, and campsites are exactly the category that sits in or at the edge of the woods.
So the defensible sentence is "of the facilities that can be placed on a map, which are almost
entirely town-centre schools, the fire reached none, and the calibrated model agrees". The model
cannot be credited with predicting no impact on the campsites; they were never on the map to be
reached. Whatever wording lands in the readme and `VALIDATION.md` must carry that caveat.

## The open decision (deferred to whoever picks this up)

Applying the calibration makes the CA-arrival ranking empty for every real sequence, which collides
with the last acceptance line of handoff 001, "demo opens on sequence 1 with a nonzero ranked count".
`fireline/app.py` currently has `DEFAULT_SCENARIO = "gavarres_real"` (line 25). Three options, none
of them obviously right:

1. **Keep the enrichment, reopen the demo on the synthetic scenario.** Regenerate
   `gavarres_real_0001..0003` with the calibrated CA: all 99 located assets get `burn_probability`
   0.0, `forecast_source` and the wind/parameter provenance entry, none gets a `fire_arrival_at`, so
   all are `forecast_unavailable`. Set `DEFAULT_SCENARIO` back to `synthetic_gavarres` so the demo
   opens with a nonzero ranked count; the real scenario stays selectable and shows the honest empty
   result. Costs: the demo's opening screen is synthetic again, undoing `f91178e`.
2. **Keep the enrichment, keep the demo on the real scenario.** Same regeneration, first screen ranks
   nothing and shows 99 needs-review. Most faithful; the UI caption at `app.py:425` has to explain
   why an empty list is the correct output, and a reviewer sees an empty product.
3. **Drop the CA enrichment from the real snapshots.** If the calibrated model reaches nothing, stop
   attaching it: no forecast fields at all, every asset plainly `forecast_unavailable`. Simplest
   contract; loses the "calibrated model says 0 % within 12 h" statement and its provenance, which is
   arguably the most defensible thing the pipeline now produces.

A fourth possibility worth considering rather than choosing blind: the p10 threshold is what makes
the count exactly zero. `fire_arrival_at` is `arrival_p10_at`, finite only where `burn_prob >= 0.1`.
Check what the burn probabilities actually are at the near facilities before deciding — if CFA de
Calonge at 561 m sits at, say, 0.05, that is a different story from 0.00 and might argue for
surfacing `burn_probability` without an arrival.

Do not resolve this by loosening the calibration. The fit is the evidence; the product question is
separate.

## Remaining work

1. **Decide the above.**
2. **Version `config.CA`** (handoff 001 step 6): the new values plus `"version"`, `"calibrated_on"`
   (incident `5769dcea`, 2026-07-03), the holdout scores or a pointer to `reports/ca-calibration.json`.
   Delete the "Not calibrated on Gavarres" comment at `fireline/config.py:122`.
3. **Re-label the enrichment** in `snapshot._enrich_forecast` (`fireline/snapshot.py:402`, `:406`,
   `:411`): `"ca_ensemble (labelled enrichment, not validated)"` ->
   something naming the evidence, e.g. `"ca_ensemble (calibrated on incident 5769dcea, 2026-07-03;
   not a provider forecast)"`. `fire_arrival_basis` keeps its `p10` semantics. Note this label is
   also asserted in `tests/test_snapshot.py:570-571`, and the string appears in
   `fixtures/real_area/README.md`, which is generated by `write_real_area`.
4. **Make `scripts/make_snapshots.py` pass the hourly wind series.** `real_arrival` currently takes
   one wind sample for the whole 12 h horizon (`make_snapshots.py:581`); use
   `calibrate.wind_series` (or the same logic) so the horizon crosses the real evening drop. Update
   `CA_N_RUNS`/`CA_HORIZON_MIN`/`CA_SEED` comments and the provenance `note` and `summary` strings,
   which name the wind and the parameters.
5. **Regenerate**: `make snapshots`, twice, and confirm the output is deterministic.
6. **Tests**: update
   `tests/test_snapshot.py::test_committed_real_snapshots_carry_labelled_ca_arrivals_and_policy_evacuation`
   — note its `assert 0 < len(with_arrival) < len(located)` cannot hold under options 1 or 2 and must
   be rewritten to the new counts and label. Add a calibration regression test so a later parameter
   edit cannot silently regress the holdout scores (assert the area ratios and the overnight growth
   stay inside the recorded tolerance; keep it fast by using a small `n_runs` and recording the
   tolerance that ensemble actually supports). Check the demo counts through `fireline.ui_state`
   (`tests/test_ui_state.py` shows how to build a `Session` on a temporary database).
7. **Docs**, every place that says the CA is uncalibrated or unvalidated: `readme.md` status
   paragraph (lines ~43-56) and section 4; `CONTRACTS.md` 2.2 (lines ~113-118) and the fixtures
   paragraph (~155-163, which also says "one sample per snapshot" of wind); `V4_GAPS.md` row "CA
   spread ensemble"; `fixtures/wind/README.md` (~41-42); `fixtures/real_area/README.md`, which is
   generated, so change `write_real_area` in `make_snapshots.py` (~371-374) rather than the file; and
   the UI caption at `fireline/app.py:427-428`, which says arrivals come from "an uncalibrated spread
   model". The readme and CONTRACTS rules that nothing is inferred from distance stay untouched.
8. **`VALIDATION.md`**: record the calibration. Either add a `check_calibration` to
   `scripts/validate.py` (`CHECKS` at line 718) and rerun `--write`, or write a section by hand that
   names `scripts/calibrate_ca.py`, the date and the numbers. Note `NOT_VERIFIED_ITEMS` at line 721
   contains a "Forecast accuracy" entry that should be narrowed, not deleted: this is calibration on
   one incident, not validation.

## Known limits to carry into the write-up

- One incident, seven perimeters, one wind point. This is calibration, not validation. The two
  June/July clusters near Alfarràs have 12 perimeters between them in Deepfire and would be the first
  real test, but there are no cached facilities or wind there yet.
- **A 2x wind-speed span has to produce a ~100x spread-rate span.** The fire grows at 6.6-7.2 m/s and
  stops at 2.6-3.7 m/s. Only `wind_c1` can separate those, which is why it lands at 0.7
  (`exp(0.7*7.2)` = 157 against `exp(0.7*2.6)` = 6.2) and why `p0` is driven down to 0.006 to keep
  calm-wind spread below percolation.
- **That sensitivity is the model's noise floor.** At `wind_c1` 0.7 the ensemble's own +-30 % speed
  perturbation swings the ignition probability about 4x, which is why the p10/p90 band is wide
  (3,942 against 1,789 ha on the first pair) and why predicted growth moves 926 -> 1,540 ha between
  seeds. Tightening the growth fit would mean pretending the wind is better known than it is.
- **The first two pairs pull against each other.** The record implies about 27 ha/min over the
  19-minute window and about 20 ha/min over the following 47 minutes at near-identical wind. The CA
  grows linearly in steps at fixed wind and cannot deliver a faster initial burst; it settles at
  `grow_r` 0.73 and 1.60. Watermark latency of unknown size inside the hour is a plausible part of
  this and is unrecoverable from the data.
- **The holdout `12:53->13:25` is the biggest growth miss** (`grow_r` 0.46-0.56) though its area ratio
  holds at 0.88-0.93. It is the largest single growth step and the neighbouring pairs pull the fit
  away from it.
- **Rate, not direction.** IoU saturates near 0.76 and p10 recall near 0.52 on the early pairs: half
  the observed new cells are not burned in even 10 % of runs. One wind point, no fuel layer, no DEM.
  The claim is "reproduces the recorded growth", never "reproduces the recorded footprint".
- Suppression is unrecorded and is absorbed into these parameters, so they will under-predict an
  unsuppressed fire.
- Without a fuel layer the model still cannot tell forest from the built-up coast.
- Perimeter timestamps are satellite watermarks with unknown latency inside each hour; the physics
  uses `observed_watermark`, not `computed_at`.

## Files

- Done: `fireline/spread.py`, `fireline/calibrate.py`, `scripts/calibrate_ca.py`,
  `reports/ca-calibration.json`, `tests/test_spread.py`, `tests/test_calibrate.py`
- To change: `fireline/config.py` (`CA`), `fireline/snapshot.py` (`_enrich_forecast` labels),
  `scripts/make_snapshots.py` (`real_arrival`, `write_real_area`, the header docstring),
  `fireline/app.py` (`DEFAULT_SCENARIO`, the caption), `tests/test_snapshot.py`,
  `readme.md`, `CONTRACTS.md`, `V4_GAPS.md`, `VALIDATION.md`, `fixtures/wind/README.md`,
  `scripts/validate.py`
- Regenerated: `fixtures/snapshots/gavarres_real_0001..0003.json`,
  `fixtures/real_area/README.md`

## State

Branch `claude/ca-calibration` is pushed to `origin`, worktree `.worktrees/ca-calibration` (its
`.venv` is its own; `data/` is a symlink to the main checkout's, which is gitignored). Full suite
green: 314 passed. `config.CA`, the snapshots and every doc are untouched, so `main` behaviour is
unchanged by these two commits.
