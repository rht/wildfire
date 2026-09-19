# Real-area facility extract: Gavarres

Gencat register extract of 2026-09-19 (`scripts/fetch_data.py registers`, cached in `data/`), filtered
to the fixed area `GAVARRES_BBOX = (2.85, 41.8, 3.2, 42.05)` (lon_min, lat_min, lon_max, lat_max) and written
in the CONTRACTS.md 2.4 asset-record shape by `scripts/make_snapshots.py`. Only asset-level fields are kept;
the raw register rows stay in `data/registers/`.

## Counts

| set | rows | by class | by register |
|---|---|---|---|
| located (coordinates inside the bbox) | 99 | {'care_home': 1, 'hospital': 1, 'school': 97} | {'equipaments': 2, 'schools': 97} |
| unlocated (no coordinates, municipality inside the area) | 69 | {'campsite': 42, 'care_home': 27} | {'campsites': 42, 'care_homes': 27} |
| total | 168 | | |

Municipalities with located rows (24): Begur, Bordils, Calonge i Sant Antoni, Cassà de la Selva, Castell d'Aro, Platja d'Aro i s'Agaró, Celrà, Corçà, Cruïlles, Monells i Sant Sadurní de l'He, Flaçà, Forallac, La Bisbal d'Empordà, La Pera, Llagostera, Mont-ras, Palafrugell, PALAMÓS, Pals, Parlavà, Sant Martí Vell, Santa Cristina d'Aro, Torroella de Montgrí, Ullà, Ullastret, Vall-llobrega.

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
  not list, and they keep `occupancy_unknown` (12 assets in all, including the hospital and the
  Equipaments sociosanitari). Unlocated care homes and campsites carry the register capacity
  (`capacity`, not `estimated_occupancy`); no located row has a register capacity.
- No footprints: every distance in a snapshot built from this file is a labelled point fallback.
- `fixtures/snapshots/gavarres_real_0001..0003.json` combine **these real facilities with three real
  recorded Deepfire satellite perimeters** (`fixtures/fire/deepfire/real/`, `input_mode: recorded`);
  the register extract (2026-09-19) postdates the fire (July 2026), so this is a recorded-input demo,
  not historical as-of replay (readme section 4).
- **No forecast covers the July snapshots** (`gavarres_real_0001..0003`): Deepfire fire-spread runs are seeded
  from hotspots observed within a lookback of NOW (no as-of parameter; the July incident returns 422 and the
  account archive starts 2026-07-10), so every asset has `fire_arrival_at` null with `forecast_unavailable`
  (schema 1.1); arrival is never derived from distance, and the consumer shows an unranked review queue.
- `gavarres_real_0004.json` (sequence 4, `as_of` 2026-09-19T13:49:18Z) uses the REAL recorded fire-spread run
  `4bbd8e98` (elmfire, 1 member, 12 h, simulated point ignition at the July incident centroid, run on
  2026-09-19): `fire_geometry` is its hour-1 burned area labelled `fire_geometry_kind: simulated`, and the
  per-location forecast comes from `forecast_input.deepfire_spread_to_forecast` (hourly isochrone crossing,
  t0 = createdAt assumed). Its 12 h burned area is about 20 ha and the nearest located facility is 5.3 km
  away, so **0 of 99 located facilities get a `fire_arrival_at`** (the 5-member run at member fraction 0.2
  also covers none); the adapter path is demonstrated, not tuned to produce arrivals.
  `evacuation_min` comes from `config.EVACUATION_POLICY` (evacuation-proto-2026-09-19) for the
  classes hospital, care_home, school and campsite; no row has an unknown class.
