# Handoff 002: value-at-risk, hackathon cut, and the burn-probability backup

Written 2026-09-19 on branch `claude/value-at-risk-handoff` (from `main` at `c5d74f2`).

## Why

`docs/VALUE_AT_RISK.md` (research note, 2026-09-19) proposes a
research-grade value-at-risk layer: three ledgers, an intensity-blind expected-net-value-change
model, a fatality chain, CVaR over ensemble members, a Catastro footprint fetch, and a value of a
statistical life for planning views. Roughly a third of that is what the hackathon build needs.
This handoff records the agreed reduction, the one data gap it exposed (`burn_probability` null on
`gavarres_real_0004`), and the agreed backup for that gap. Nothing in the code has been changed.

Decisions taken with the user on 2026-09-19:

1. Ship the reduced model below. Everything under "Cut" stays in the research note as an upgrade
   path and is not implemented.
2. Backup for `burn_probability`: keep the Deepfire per-band probability the adapter currently
   discards, and run the existing CA ensemble when no provider forecast exists. No new spread
   model. The readme rule that nothing is inferred from distance is untouched.
3. Use the current, uncalibrated CA now with its existing "not validated" label. Handoff 001
   improves the numbers without changing the interface.

## The reduced model

Inputs per asset: `estimated_occupancy` (never `capacity`), `burn_probability`, the p10 and p50
slack the window ranking already computes, and the asset class. Policy inputs: one replacement
value in euros per class and one damage-ratio band per class. All four computed fields are `null`
when any input is `null`, never zero, and each carries a `sources` entry.

    people_exposed         = estimated_occupancy × burn_probability
    people_at_risk_p50     = estimated_occupancy if slack_p50 < 0 else 0
    people_at_risk_p10     = estimated_occupancy if slack_p10 < 0 else 0
    expected_loss_eur_mid  = burn_probability × d_mid × replacement_value_eur
    expected_loss_eur_low  = burn_probability × d_low × replacement_value_eur
    expected_loss_eur_high = burn_probability × d_high × replacement_value_eur

`people_at_risk` is the headline: it is a re-labelling of `window_exhausted` counts that the
ranking already produces, weighted by headcount. Euros are a secondary column and a scenario
header total, never a sort key or filter. There is no euro figure for lives anywhere in the UI.

Scenario aggregates in `ui_state.Session.status()`: sum of `people_exposed`, `people_at_risk_p50`,
`people_at_risk_p10`, and `expected_loss_eur_mid` with the low/high band, over located assets with
non-null values, plus the count of assets excluded for null inputs so the total is never read as
complete.

### Policy table (all values assumed, labelled `value_basis = "assumed"`)

Derived from the ATC 2025 replacement-cost table in the research note, section 6, times an assumed
built area per class and a 1.4 multiplier for fees, VAT and contents. They are placeholders for a
per-asset figure and should be rounded, not precise.

| class | replacement_value_eur | d_low / d_mid / d_high | note |
|---|---|---|---|
| hospital | 25,000,000 | 0.10 / 0.25 / 0.50 | confinable masonry; equipment not separately valued |
| care_home | 5,000,000 | 0.10 / 0.25 / 0.50 | confinable masonry |
| school | 4,000,000 | 0.15 / 0.40 / 0.80 | |
| camp | 3,000,000 | 0.15 / 0.40 / 0.80 | |
| campsite | 3,000,000 | 0.30 / 0.60 / 0.90 | tents and caravans |
| masia | 400,000 | 0.30 / 0.60 / 0.90 | rural housing in forest |
| nucleus | null | 0.15 / 0.40 / 0.80 | no dwelling count in the data; `value_unknown` |

The existing `value_score` (0.4 to 1.0, "not a monetary valuation") stays as it is and is not
derived from this table.

### Cut

- **Fatality chain** (probability of not evacuating, case fatality rate, non-ambulatory
  multipliers). The note calls the case fatality rate the weakest number in the chain;
  `people_at_risk` carries the same message without inventing a death count.
- **Value of a statistical life** and the planning-only lives view.
- **VaR and CVaR.** Deepfire returns probability bands, not member masks, and the CA collapses
  its members in `aggregate_runs` (`fireline/spread.py:172`), so neither path can produce a tail
  measure today. The low/high band from the damage ratio is the only range shown.
- **Catastro INSPIRE footprint fetch** and built-area valuation. Replaced by the per-class table.
- **Land ledger.** No land-cover raster exists in the repo; burned hectares at p50 may be shown if
  the arrival raster gives it cheaply, but no euros per hectare.
- **Cost indexing, equipment uplifts, shelter cost per evacuee, insurance framing, and the
  intensity upgrade paths.** Documentation only.

## The `burn_probability` gap and the agreed backup

Inventory of the committed snapshots (168 assets, 99 located, in `fixtures/snapshots/`):

| file | burn_probability non-null | fire_arrival_at non-null | why |
|---|---|---|---|
| gavarres_real_0001 | 99 | 18 | CA ensemble, `forecast_enrichment` on |
| gavarres_real_0002 | 99 | 65 | same |
| gavarres_real_0003 | 99 | 94 | same |
| gavarres_real_0004 | 0 | 0 | Deepfire one-member run; adapter drops band probability |
| synthetic_gavarres_0001/0002 | 11 of 12 | 11 | |

The research note's "67 of 168" for sequence 2 counted arrivals, not burn probability. The 69
unlocated assets can never be filled and the layer returns `null` with `location_unknown` for
them. So the only real gap is `gavarres_real_0004`, and the cause is in the adapter, not the data.

### Fix A: keep the Deepfire band probability

`forecast_input.deepfire_spread_to_forecast` (`fireline/forecast_input.py:250-302`) unions the
hourly bands whose `burn_probability` is at least `min_burn_probability`, records the first hour
that covers the asset as `arrival_at`, and then writes `"burn_probability": None` (line 288). The
provider value is available in `_spread_hours` (line 228) as a multiple of 1/N members.

Change: for every located asset inside the query, `burn_probability` becomes the maximum band
probability among bands that cover the point at any hour up to the horizon, and `0.0` when no band
covers it within the horizon. `arrival_at` keeps its current semantics (first covering hour of the
unioned bands) and stays `null` when not reached. The basis string gains "band probability" so the
`sources` entry names it. `attach_forecast` (lines 167-205) already rounds and copies the field;
`validate_snapshot` (`fireline/snapshot.py:593`) requires a non-null `forecast_source` whenever
`burn_probability` is non-null, which `attach_forecast` provides.

Effect on `gavarres_real_0004`: the recorded run is one deterministic ELMFIRE member seeded at a
simulated point ignition, and the nearest located facility is 5.2 km from its final isochrone. All
99 located assets therefore get `burn_probability = 0.0`, `people_exposed = 0` and
`expected_loss_eur_* = 0`. That is the forecast's true statement, not a null, and the header should
say "0 exposed within the 12 h horizon", not "forecast unavailable". The review reason
`forecast_unavailable` currently fires on any asset without `fire_arrival_at`, including the CA
path's not-reached assets in sequences 1 to 3; keep that behaviour for now, and consider a
`not_reached_in_horizon` reason as a follow-up so the UI can distinguish the two.

For the demo, the value-at-risk numbers that are worth showing come from sequences 1 to 3. Do not
plan the demo around sequence 4 producing non-zero exposure.

### Fix B: run the CA when no provider forecast exists

This is the existing mechanism: `snapshot.build_snapshot(arrival=..., cfg=enrichment_config())`
gated by `forecast is None and cfg.FEATURES["forecast_enrichment"] and arrival is not None`
(`fireline/snapshot.py:457`). Nothing changes in the gate. Two things are needed so it applies to
any new snapshot, not only the July ones:

- `scripts/make_snapshots.py` must call `real_arrival` for every real snapshot that comes back from
  `load_forecast` with no forecast, rather than only for the July sequences. The CA needs a
  `FireState` with wind (`fire_state.py:27-28`); wind for a new `as_of` comes from
  `scripts/fetch_data.py wind` or a fixture, and the enrichment note must name the wind source and
  hour. Without wind the CA must not run; the asset stays `forecast_unavailable`.
- The label stays `"ca_ensemble (labelled enrichment, not validated)"` until handoff 001 changes
  it. Known bias: under light wind the current parameters percolate almost isotropically, so
  `burn_probability` from the CA is high for most of the grid within 12 h. Expected loss will be
  correspondingly pessimistic. State this next to the header total.

Combining a provider forecast that reaches nothing with a CA run (`spread.combine`, maximum
burn probability) was considered and not agreed. A provider forecast, when present, is the only
source.

## Where it plugs in

1. **Flag.** `"value_at_risk": False` in `config.FEATURES` (`fireline/config.py:15`), enabled
   per call through `cfg` like the other flags (`scripts/make_snapshots.py:597`, `enrichment_config`).
2. **Policy.** A versioned `VALUE_AT_RISK_POLICY` next to `VALUE_POLICY` (`fireline/config.py:27`)
   holding the table above and `t_warn` if the slack definition needs it.
3. **Fields.** `replacement_value_eur`, `replacement_value_basis`, `expected_loss_eur_low`,
   `expected_loss_eur_mid`, `expected_loss_eur_high`, `people_exposed`, `people_at_risk_p50`,
   `people_at_risk_p10`, computed in `snapshot.asset_record` after `_enrich_forecast` has run
   (`fireline/snapshot.py:281` and `:389`), added to `ASSET_KEYS` (`:56-61`),
   `_COMPUTED_FIELDS` (`:64-67`) and `validate_snapshot` (non-negative or null; `expected_loss_*`
   null whenever `burn_probability` or `replacement_value_eur` is null). Re-derive on
   `asset_type` override like `value_score` (`fireline/priority.py:163-167`).
4. **Adapter.** Fix A in `forecast_input.deepfire_spread_to_forecast`; extend
   `tests/test_forecast_input.py` with a two-member fixture where one asset sits in a 0.5 band and
   one outside every band, asserting `0.5` and `0.0`.
5. **Snapshots.** Fix B in `scripts/make_snapshots.py`; `make snapshots`, then update the
   committed-snapshot test counts in `tests/test_snapshot.py`.
6. **UI.** Header metrics row (`fireline/app.py:472-474`) gains people exposed, people at risk (p50, with
   p10 in the tooltip), expected loss mid with the band, and the excluded-asset count. Per-asset
   columns in `app.ranked_frame` / `review_frame` (`fireline/app.py:154-176`) labelled
   "estimate (assumed replacement cost, ±30%)"; never sortable. Detail in `components_frame`.
7. **Contract.** Add the eight fields to readme section 5 and CONTRACTS.md with the null rules,
   and a one-line note that euros never enter the ranking.

Estimated effort: half a day for steps 1 to 5 and tests, a few hours for 6 and 7.

## Acceptance

- Full test suite green; `make snapshots` deterministic across two runs.
- `gavarres_real_0004`: 99 located assets with `burn_probability = 0.0` and a non-null
  `forecast_source`; the other 69 keep `null` with `location_unknown`.
- `gavarres_real_0001..0003`: unchanged `burn_probability`; `people_exposed`,
  `people_at_risk_*` and `expected_loss_*` non-null on every located asset with a known headcount
  and a valued class; `null` (never zero) on `occupancy_unknown`, `nucleus`, and unlocated assets.
- A snapshot with `value_at_risk` off is byte-identical to today's output.
- No sort, filter or ranking path reads any euro field (grep `expected_loss` and
  `replacement_value` outside `snapshot.py`, `ui_state.py` and the display frames returns nothing).
- The readme rules on distance remain untouched.

## Known limits to state in the write-up

- Replacement values and damage ratios are per-class assumptions with no per-asset basis. The
  band shown is the damage-ratio band only; the value uncertainty is at least as large.
- CA-derived `burn_probability` is uncalibrated and biased high under light wind (handoff 001).
- Provider forecasts that reach no facility produce zeros, which is correct but unspectacular.
- `people_at_risk` counts the whole headcount of an asset whose slack is negative; it does not
  model partial clearance.
- Values are total economic loss (insured and uninsured); wildfire is not an extraordinary risk
  under RD 300/2004, so this is not an insurer's figure.

## Files

- `fireline/config.py` (`FEATURES`, `VALUE_POLICY`, new `VALUE_AT_RISK_POLICY`)
- `fireline/snapshot.py` (`asset_record`, `_enrich_forecast`, `ASSET_KEYS`, `_COMPUTED_FIELDS`,
  `validate_snapshot`)
- `fireline/forecast_input.py` (`_spread_hours`, `deepfire_spread_to_forecast`, `attach_forecast`)
- `fireline/priority.py` (`asset_type` override re-derivation)
- `fireline/ui_state.py` (`Session.status`, line 346), `fireline/app.py` (metrics row, frames)
- `scripts/make_snapshots.py` (`real_arrival`, `enrichment_config`, the 0004 builder)
- `tests/test_forecast_input.py`, `tests/test_snapshot.py`, `tests/test_ui_state.py`
- `readme.md` section 5, `CONTRACTS.md`
- `docs/VALUE_AT_RISK.md` (research note; the source for every figure above)
