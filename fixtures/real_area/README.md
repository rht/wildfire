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
  hospital and sociosanitari rows only (`class_ambiguous` for the latter). No located row has a register
  capacity, so every located asset carries `occupancy_unknown`; unlocated care homes and campsites carry the
  register capacity (`capacity`, not `estimated_occupancy`).
- No footprints: every distance in a snapshot built from this file is a labelled point fallback.
- `fixtures/snapshots/gavarres_real_0001..0003.json` combine **these real facilities with three real
  recorded Deepfire satellite perimeters** (`fixtures/fire/deepfire/real/`, `input_mode: recorded`);
  the register extract (2026-09-19) postdates the fire (July 2026), so this is a recorded-input demo,
  not historical as-of replay (readme section 4).
- **No forecast covers the real area** (no per-location spread product has been recorded for the incident),
  so in the real snapshots every asset has `fire_arrival_at` null with `forecast_unavailable` (schema 1.1);
  arrival is never derived from distance, and the consumer shows them as an unranked review queue.
  `evacuation_min` comes from `config.EVACUATION_POLICY` (evacuation-proto-2026-09-19) for the
  classes hospital, care_home, school and campsite; no row has an unknown class.
