# Handoff 003: value at risk, as built

Written 2026-09-19 on branch `claude/value-at-risk` (from `claude/value-at-risk-handoff` at `696741b`).
This records what handoff 002 became in code, the decisions its text left open, the measured
numbers, and what is deliberately still missing.

Commits: `48f2d5f` (the layer and the adapter), `4ff2e60` (the snapshots and the contract),
`6814cee` (the analyst screen).

## What shipped

All seven steps of handoff 002. `config.FEATURES["value_at_risk"]` (off by default) gates eight
optional per-asset keys filled by `snapshot.derive_value_at_risk` after the forecast pass:
`replacement_value_eur`, `replacement_value_basis`, `expected_loss_eur_low` / `_mid` / `_high`,
`people_exposed`, `people_at_risk_p50`, `people_at_risk_p10`. They are present in full or absent in
full; `schema_version` stays `1.1`. `config.VALUE_AT_RISK_POLICY` holds the handoff's per-class
table unchanged. `priority.apply_overrides` re-derives the layer when a confirmed class, headcount
or evacuation duration moves one of its inputs. `ui_state.value_at_risk_totals` is the one
definition of the scenario aggregate; `Session.status()["value_at_risk"]` and the generated
real-area README both use it, so the header and that file cannot disagree.

Fix A landed in `forecast_input.deepfire_spread_to_forecast`: `burn_probability` is the maximum
band probability covering the facility point at any hour within the horizon, and `0.0` when no band
covers it. Every band is scanned; `min_burn_probability` now selects only the arrival union, so
raising it can never lower a location's probability. `arrival_at` semantics are unchanged.

Fix B landed in `scripts/make_snapshots.py`: one list of `(fire, as_of, forecast | None)` drives
every real snapshot, a provider forecast suppresses the CA where it exists, and the CA runs on every
other real snapshot whatever its `as_of`. The two are never combined.

## Decisions taken while building

1. **`slack_p10` / `slack_p50` did not exist.** Handoff 002 says the window ranking already computes
   them; it computes a single `slack_min` from the *selected* arrival. The layer now computes one
   slack per quantile in `snapshot.py`, with `contact_priority.window_arithmetic` and
   `CONTACT_POLICY["buffer_min"]`, so the producer owns all eight fields as the handoff specifies.
2. **The boundary is `<= 0`, not `< 0`.** `people_at_risk` re-labels `window_exhausted`, which is
   `slack <= 0`. Using the handoff's `< 0` would have made the headline disagree with the status it
   re-labels on exactly the boundary case.
3. **Covered but not reached is `0`, not `null`.** An asset a forecast covers whose arrival quantile
   is null is not reached inside the horizon, so its window is not exhausted. Without this rule the
   handoff's own acceptance criterion for sequences 1 to 3 could not hold, since most located assets
   have no `arrival_p10_at`. An *unusable* timestamp still gives `null`: a gap stays a gap.
4. **No review reason for an unvalued class.** The handoff's table annotates `nucleus` with
   `value_unknown`. Adding it would have pushed every nucleus into the review queue and changed
   `needs_review` for assets whose data is not in fact missing. `REVIEW_REASONS` is unchanged and the
   header's excluded count carries that message instead. `replacement_value_eur` and
   `replacement_value_basis` are null together, the convention `value_score` / `value_basis` uses,
   and the reason a class is not valued lives in the `sources` note.
5. **The layer is on for every committed snapshot**, synthetic and real, through per-call cfg shims
   (`feature_config`, `enrichment_config`, `snapshot_config`). `fixtures/real_area/assets_gavarres.json`
   is an asset extract, not a snapshot, and stays flag-off.
6. **The CA is wind-gated.** `ca_arrival` probes the committed wind series first and skips the
   ensemble when none covers the perimeter, rather than running the model on a defaulted wind. The
   snapshot then keeps its assets `forecast_unavailable`.

## Measured

Header totals over located assets, from the committed fixtures. `excluded` is located assets left
out of the people and euro totals respectively, for a null input.

| snapshot | exposed | at risk p50 / p10 | expected loss mid (low-high) | excluded people / eur |
|---|---|---|---|---|
| synthetic_gavarres_0001 | 438 | 0 / 0 | 6,519,500 (2,711,000-12,268,000) | 6 / 6 of 12 |
| synthetic_gavarres_0002 | 505.8 | 89 / 89 | 7,623,000 (3,169,000-14,367,000) | 6 / 6 of 12 |
| gavarres_real_0001 | 1,866.7 | 0 / 0 | 13,280,000 (4,980,000-26,560,000) | 12 / 0 of 99 |
| gavarres_real_0002 | 6,632.5 | 0 / 45 | 43,390,000 (16,290,000-86,780,000) | 12 / 0 of 99 |
| gavarres_real_0003 | 18,433.9 | 1,680 / 3,040 | 137,420,000 (51,720,000-274,840,000) | 12 / 0 of 99 |
| gavarres_real_0004 | 0 | 0 / 0 | 0 (0-0) | 12 / 0 of 99 |

The real-snapshot euro exclusions are zero because every located asset is a valued class; the 12
people exclusions are the located `occupancy_unknown` assets. The synthetic euro exclusions are the
five `nucleus` assets and one care home outside the raster.

Located-asset `burn_probability` medians run 0.00, 0.10 and 0.95 across sequences 1 to 3: by 0003,
68 of 99 assets are at 0.9 or above and 35 at exactly 1.0, under a 1.58 m/s wind. That is the
handoff's predicted light-wind bias of the uncalibrated CA, and it is what drives 0003's EUR 137 M
and its 3,040 people at p10. Nothing was tuned; calibration is handoff 001.

## Acceptance, verified

- Full suite green: 353 passed, 1 skipped (from 321 at the branch point; +32 tests).
- `scripts/make_snapshots.py` byte-identical across two consecutive runs.
- `gavarres_real_0004`: 99 located assets at `burn_probability = 0.0` with a non-null
  `forecast_source`, `people_exposed` and `expected_loss_eur_*` of 0; the other 69 null with
  `location_unknown`.
- `gavarres_real_0001..0003`: `burn_probability` and `fire_arrival_at` unchanged on every asset;
  all six risk fields non-null on all 87 located, occupied, valued assets; null and never zero on
  the 12 `occupancy_unknown` assets and on all 69 unlocated ones. `nucleus` keeps its people fields
  and has null euros, with no review reason added.
- **A snapshot built with the flag off is byte-identical to the pre-layer output.** Rebuilt
  `gavarres_real_0001..0003` with `value_at_risk` off and compared field by field against the files
  committed at `696741b`: identical apart from `computed_at`, which is wall-clock by design.
- `grep -rn "expected_loss\|replacement_value" fireline/ scripts/ --include="*.py"` hits only
  `config.py` (the policy), `snapshot.py` (producer and validation), `ui_state.py` (the aggregate),
  `app.py` (display only) and `make_snapshots.py` (the totals it writes into the README). Nothing in
  `priority.py`, `contact_priority.py`, `tasks.py`, `agent.py` or any sort, filter or ranking path.
  The euro column is rendered as a display string with its band, so a header click cannot order by it.
- The readme's rules on distance are untouched.

## Known limits, to state wherever these numbers appear

- Replacement values and damage ratios are per-class assumptions with no per-asset basis. The band
  shown is the damage-ratio band only; the value uncertainty is at least as large.
- CA-derived `burn_probability` is uncalibrated and biased high under light wind (handoff 001), so
  the expected-loss totals of sequences 2 and 3 are pessimistic by construction.
- A provider forecast that reaches no facility produces zeros. That is correct and unspectacular;
  the demo numbers worth showing come from sequences 1 to 3.
- `people_at_risk` counts the whole headcount of an asset whose window is exhausted. It does not
  model partial clearance.
- Values are total economic loss, insured and uninsured. Wildfire is not an extraordinary risk under
  RD 300/2004, so this is not an insurer's figure.
- The `estimated_occupancy` behind `people_exposed` is a school enrolment for most located assets:
  pupils only, not staff, and not a time-of-day headcount.

## Not built, and what is next

Everything under handoff 002's "Cut" stays cut: the fatality chain, a value of a statistical life,
VaR and CVaR, the Catastro footprint fetch and the land ledger. `docs/VALUE_AT_RISK.md` remains the
research note and the upgrade path.

Two follow-ups the work surfaced:

- **`not_reached_in_horizon`.** `forecast_unavailable` still fires for any asset without
  `fire_arrival_at`, so it now covers two different situations: no forecast at all, and a forecast
  that covers the asset and puts no fire there. The header distinguishes them in wording only
  (`covered` and `reached` in the aggregate). A separate review reason would let the review queue do
  the same, and would change `validate_snapshot`'s `forecast_unavailable` rule, so it was left out
  of this cut as handoff 002 suggests.
- **Per-asset replacement values.** The per-class table is the placeholder the research note says it
  is. A footprint-based figure needs the Catastro fetch that was cut.
