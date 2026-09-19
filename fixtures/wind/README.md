# Wind fixture for the real Gavarres snapshots

`41.90_3.05.json` is a verbatim copy of `data/wind/41.90_3.05.json` as written by
`scripts/fetch_data.py wind` (`fireline.feeds.open_meteo_series(..., replay=True)`) on 2026-09-19.
It is committed because `data/` is gitignored and `scripts/make_snapshots.py` needs the wind to
run the cellular-automaton spread ensemble on the recorded July 2026 perimeters
(`fixtures/snapshots/gavarres_real_0001..0003.json`).

## Provenance

- Source: Open-Meteo **Previous Runs API** (`https://previous-runs-api.open-meteo.com/v1/forecast`),
  model `ecmwf_ifs025`, hourly, UTC, wind speed unit m/s, 2026-07-03 .. 2026-07-05 (72 hours).
- Slice: `_previous_day1`, i.e. each hourly value comes from the model run initialised about 24 h
  **before** its valid time. The value valid at time t was therefore available before t, so nothing
  observed after a snapshot's `as_of` leaks into that snapshot.
- This is real recorded **weather-model output**, not an observation. No station measurement was used
  (Meteocat XEMA was not fetched) and the model is not verified against the July 2026 event.
- Keys: `time` (ISO hour, UTC), `wind_speed_10m` (m/s), `wind_direction_10m` (degrees, meteorological:
  the direction the wind blows FROM), `wind_gusts_10m` (null for every hour with `ecmwf_ifs025`),
  `temperature_2m` (C), `relative_humidity_2m` (%). `lat`/`lon` are the requested 0.1 degree grid point;
  `latitude`/`longitude` are the model cell Open-Meteo snapped it to (0.25 degree: 42.0, 3.0).

## Grid point selection

`fetch_data.py wind` requests a 0.1 degree grid over `GAVARRES_BBOX` (12 points, `<lat>_<lon>.json`).
`make_snapshots.py` picks, per real perimeter, the file whose requested grid point is nearest (in
degrees) to the perimeter centroid, and the hourly value of the hour containing the snapshot `as_of`
(nearest available hour, flagged in the note, when that hour is absent; the script fails when no wind
file exists). All three July perimeters (centroids near 3.027E, 41.90N) resolve to `41.90_3.05.json`,
so only that file is committed; the other 11 points stay in `data/wind/`.

## Values used by the committed snapshots

| snapshot | as_of (UTC) | hour used | speed m/s | from deg |
|---|---|---|---|---|
| gavarres_real_0001 | 2026-07-03T13:20:01Z | 2026-07-03T13:00 | 5.80 | 15 |
| gavarres_real_0002 | 2026-07-03T15:32:24Z | 2026-07-03T15:00 | 2.64 | 65 |
| gavarres_real_0003 | 2026-07-04T06:31:53Z | 2026-07-04T06:00 | 1.58 | 305 |

The CA (`fireline.spread.run_ca`, `config.CA`, no fuel, no slope, 20 runs, 720 min horizon, seed 0) is
not calibrated on Gavarres; its output is attached to the snapshots as labelled enrichment only
(`forecast_source = "ca_ensemble (labelled enrichment, not validated)"`).
