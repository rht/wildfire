# Real-area facility extract: Gavarres

Gencat register extract of 2026-09-19 (`scripts/fetch_data.py registers`, cached in `data/`), filtered
to the fixed area `GAVARRES_BBOX = (2.85, 41.8, 3.2, 42.05)` (lon_min, lat_min, lon_max, lat_max) and written
in the CONTRACTS.md 2.4 asset-record shape by `scripts/make_snapshots.py`. Only asset-level fields are kept;
the raw register rows stay in `data/registers/`.

## Counts

| set | rows | by class | by register |
|---|---|---|---|
| located (coordinates inside the bbox) | 111 | {'aerodrome': 5, 'care_home': 1, 'fire_station': 6, 'hospital': 1, 'research_facility': 1, 'school': 97} | {'equipaments': 14, 'schools': 97} |
| unlocated (no coordinates, municipality inside the area) | 69 | {'campsite': 42, 'care_home': 27} | {'campsites': 42, 'care_homes': 27} |
| total | 180 | | |

Municipalities with located rows (28): , Begur, Bordils, Calonge i Sant Antoni, Cassà de la Selva, Castell d'Aro, Platja d'Aro i s'Agaró, Celrà, Corçà, Cruïlles, Monells i Sant Sadurní de l'He, Cruïlles, Monells i Sant Sadurní de l'Heura, Flaçà, Foixà, Fontanilles, Forallac, La Bisbal d'Empordà, Pera, la, Llagostera, Mont-ras, Palafrugell, PALAMÓS, Pals, Parlavà, Sant Martí Vell, Santa Cristina d'Aro, Torroella de Montgrí, Ullà, Ullastret, Vall-llobrega.

## Limitations

- The care-home (`ivft-vegh`) and campsite (`t2h3-cgys`) registers carry **no coordinates**; those rows are
  included with null `latitude`/`longitude` and the `location_unknown` review reason. They are matched to the
  area by municipality name (accent/case folded, inverted article normalised), not by geometry, so a
  municipality that straddles the bbox contributes all of its unlocated rows. The schools register truncates
  long names (`Cruïlles, Monells i Sant Sadurní de l'He`), so unlocated rows of that municipality would not
  match; none exist in the 2026-09-19 extract.
- Located rows are almost all schools (`kvmv-ahh4`, school year 2025/2026); Equipaments contributes the
  hospital and sociosanitari rows only (`class_ambiguous` for the latter).
- Occupancy: 87 of 97 schools carry `estimated_occupancy` = enrolled pupils joined on
  `codi_centre` from the enrolment register `xvme-26kg` (current school year, previous year where the
  current one is not published yet); pupils only, staff not counted, and an enrolment is not a
  time-of-day headcount. The rest are music, dance and adult-education centres, which that register does
  not list, and they keep `occupancy_unknown` (24 assets in all, including the hospital and the
  Equipaments sociosanitari). Unlocated care homes and campsites carry the register capacity
  (`capacity`, not `estimated_occupancy`); no located row has a register capacity.
- No footprints: every distance in a snapshot built from this file is a labelled point fallback.
- `fixtures/snapshots/gavarres_real_0001..0003.json` combine **these real facilities with three real
  recorded Deepfire satellite perimeters** (`fixtures/fire/deepfire/real/`, `input_mode: recorded`);
  the register extract (2026-09-19) postdates the fire (July 2026), so this is a recorded-input demo,
  not historical as-of replay (readme section 4).
- **No provider forecast covers the July snapshots** (`gavarres_real_0001..0003`): Deepfire fire-spread runs
  are seeded from hotspots observed within a lookback of NOW (no as-of parameter; the July incident returns 422
  and the account archive starts 2026-07-10). Their `fire_arrival_at` therefore comes from the **v0
  cellular-automaton ensemble** (`fireline.spread.run_ca`, `config.CA`, not calibrated on Gavarres) seeded on
  each recorded perimeter (20 runs, 720 min horizon, seed 0, no fuel, no slope,
  100 m grid), attached through the `forecast_enrichment` hook as **labelled enrichment**:
  `forecast_source = "ca_ensemble (labelled enrichment, not validated)"`, `fire_arrival_basis = p10`. Wind is the
  Open-Meteo Previous Runs slice committed in `fixtures/wind/` (model `ecmwf_ifs025`, `previous_day1`, real
  model output, not an observation; nearest 0.1 deg grid point to the perimeter centroid, hour containing
  `as_of`; see `fixtures/wind/README.md`). Assets the ensemble does not reach within the horizon in at least
  10 % of runs keep `fire_arrival_at` null with `forecast_unavailable` (schema 1.1); arrival is never derived
  from distance. Per snapshot (wind speed / from, hour used; located assets with an arrival):
  - `gavarres_real-0001`: CA ensemble 20 runs, 720 min horizon, seed 0, 100 m grid, no fuel/slope; wind 5.80 m/s from 15 deg at 2026-07-03T13:00Z (fixtures/wind/41.90_3.05.json, grid point 41.90N 3.05E); 21 of 111 located assets get a `fire_arrival_at`.
  - `gavarres_real-0002`: CA ensemble 20 runs, 720 min horizon, seed 0, 100 m grid, no fuel/slope; wind 2.64 m/s from 65 deg at 2026-07-03T15:00Z (fixtures/wind/41.90_3.05.json, grid point 41.90N 3.05E); 72 of 111 located assets get a `fire_arrival_at`.
  - `gavarres_real-0003`: CA ensemble 20 runs, 720 min horizon, seed 0, 100 m grid, no fuel/slope; wind 1.58 m/s from 305 deg at 2026-07-04T06:00Z (fixtures/wind/41.90_3.05.json, grid point 41.90N 3.05E); 106 of 111 located assets get a `fire_arrival_at`.
- `gavarres_real_0004.json` (sequence 4, `as_of` 2026-09-19T13:49:18Z) uses the REAL recorded fire-spread run
  `4bbd8e98` (elmfire, 1 member, 12 h, simulated point ignition at the July incident centroid, run on
  2026-09-19): `fire_geometry` is its hour-1 burned area labelled `fire_geometry_kind: simulated`, and the
  per-location forecast comes from `forecast_input.deepfire_spread_to_forecast` (hourly isochrone crossing,
  t0 = createdAt assumed). Its 12 h burned area is about 20 ha and the nearest located facility is 5.3 km
  away, so **0 of 99 located facilities get a `fire_arrival_at`** (the 5-member run at member fraction 0.2
  also covers none); the adapter path is demonstrated, not tuned to produce arrivals. The adapter does
  keep the run's own probability for every located facility: all 99 get `burn_probability: 0.0` with the
  run named in `forecast_source` and in a `sources` entry. A `0.0` there is the forecast's statement that
  it covers that location and puts no burned area on it within its 12 h horizon, which is not the same as
  the `null` the 69 unlocated rows keep, meaning no forecast covers them at all; neither is inferred from
  distance, and both still carry `forecast_unavailable` for want of an arrival.
  `evacuation_min` comes from `config.EVACUATION_POLICY` (evacuation-proto-2026-09-19) for the
  classes hospital, care_home, school and campsite; no row has an unknown class.
- **Value at risk** (`config.FEATURES["value_at_risk"]`, policy value-at-risk-proto-2026-09-19):
  every snapshot above carries `people_exposed`, `people_at_risk_p50` / `_p10` and `expected_loss_eur_low`
  / `_mid` / `_high`. People exposed is the estimated headcount times the burn probability; people at risk
  is the whole headcount of an asset whose remaining evacuation window at that arrival quantile is
  exhausted, and it does not model partial clearance. The euros are an **assumed per-class replacement
  value times an assumed damage ratio**, rounded placeholders for a per-asset figure with no per-asset
  basis: the band shown is the damage-ratio band only, so the value uncertainty is at least as large.
  They are total economic loss, insured and uninsured, not an insurer's figure, and they never enter the
  ranking, the sort or a filter. A field is null, never zero, when an input is missing (no headcount, no
  burn probability, no evacuation estimate, no forecast, no location, or a class with no replacement value
  such as `nucleus`), so a total is always reported with the count of assets left out of it. The
  CA-derived burn probabilities of `gavarres_real_0001..0003` are uncalibrated and, under light wind, the
  ensemble percolates almost isotropically, so those probabilities are high across most of the grid and
  the expected loss is correspondingly pessimistic (handoff 001 recalibrates; the interface does not
  change). Totals over the located assets of each snapshot:
  - `gavarres_real-0001`: 1866.7 people exposed, 0 at risk at p50 and 0 at p10, expected loss EUR 13,280,000 (band 4,980,000 to 26,560,000); of 111 located assets, 24 are left out of the people totals and 12 out of the euro totals for want of an input.
  - `gavarres_real-0002`: 6632.5 people exposed, 0 at risk at p50 and 45 at p10, expected loss EUR 43,390,000 (band 16,290,000 to 86,780,000); of 111 located assets, 24 are left out of the people totals and 12 out of the euro totals for want of an input.
  - `gavarres_real-0003`: 18433.9 people exposed, 1680 at risk at p50 and 3040 at p10, expected loss EUR 137,420,000 (band 51,720,000 to 274,840,000); of 111 located assets, 24 are left out of the people totals and 12 out of the euro totals for want of an input.
  - `gavarres_real-0004`: 0 people exposed, 0 at risk at p50 and 0 at p10, expected loss EUR 0 (band 0 to 0); of 111 located assets, 24 are left out of the people totals and 12 out of the euro totals for want of an input.
