# fixtures/forecast — per-location fire arrival estimates (SYNTHETIC)

`forecast-input-1` files read by `fireline.forecast_input.load_forecast` and attached to snapshot
assets by `snapshot.build_snapshot(forecast=...)` (CONTRACTS.md 2.2, v1.1 timing fields). Each file
carries `issued_at`, `forecast_horizon_at`, a `forecast_source` label, a `basis`, a `note` and one
estimate (`arrival_p10_at`, `arrival_p50_at`, `burn_probability`) per covered asset. The producer selects
`fire_arrival_at` = p10 (basis `p10`), else p50 (basis `p50`); assets without an estimate or without
coordinates get `forecast_unavailable`. Nothing is derived from distance.

| file | issued | feeds | content |
|---|---|---|---|
| `synthetic_gavarres_0001.json` | 2026-07-03 08:00Z | `fixtures/snapshots/synthetic_gavarres_0001.json` | 11 hand-designed estimates |
| `synthetic_gavarres_0002.json` | 2026-07-03 10:00Z | `fixtures/snapshots/synthetic_gavarres_0002.json` | 11 hand-designed estimates, fire grown SSE |

Both are **synthetic, not a provider forecast**: the numbers are the `SYNTHETIC_FORECAST` table in
`scripts/make_snapshots.py`, chosen by hand from each asset's bearing and range to the ignition so that
the readme section 11 "Priority" checks are demonstrable (farther downwind outranks nearer off-axis,
exhausted windows and an order change at 10:00, a located asset without an estimate, an exact tie).
The reasoning is in each file's `note`. `fixture:vall_repos` and the unlocated care home are
deliberately absent. No real forecast file lives here: the real fire-spread runs are recorded as raw
responses in `fixtures/fire/deepfire/real/` and turned into a forecast dict at build time by
`forecast_input.deepfire_spread_to_forecast` (see that directory's README), which fills the optional
per-estimate `arrival_at` key and a descriptive `basis`.
