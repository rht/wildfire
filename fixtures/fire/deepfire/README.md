# fixtures/fire/deepfire — recorded Deepfire responses

Files here are `{"collection", "received_at", "body", "note"}` records read by
`fireline.fire_input.load_recorded()` and passed through `parse_deepfire()` on the same path as a
live poll (CONTRACTS.md section 3). `scripts/fetch_data.py deepfire` writes real responses in the
same shape to `data/deepfire/recorded/` when credentials are set.

## Verification status (2026-09-19)

- **Verified: documentation schema only.** Field names and collection names were read from
  docs.deepfire.co on 2026-09-19 (see `data/README.md`): `satellite-perimeters` features carry
  `computed_at` and a nullable `observed_watermark`; `clusters` carry `first_observed` /
  `last_observed`; `hotspots` carry `observed_at`. Perimeter features have Polygon/MultiPolygon
  geometry; clusters are treated as a hotspot centre (Point; any area geometry is reduced to its
  centroid), never as a surveyed perimeter.
- **Verified live on 2026-09-19 13:13Z:** `scripts/fetch_data.py deepfire` authenticated with the
  client id/secret from `.env` (loaded by `fireline/env.py`, python-dotenv) and pulled the Gavarres
  bbox for 2026-07-03..05: 30 clusters, 1272 hotspots, 7 satellite perimeters. The real property
  sets match the parser's assumptions: perimeters `{active, algo_version, area_m2, cluster_id,
  computed_at, id, n_hotspots, observed_watermark, perimeter_m}` with MultiPolygon geometry;
  clusters `{active, first_observed, id, last_observed}` with Point geometry; hotspots `{active,
  cluster_id, confidence, country, fire_radiative_power, id, observed_at, source}`. Times are `Z`
  suffixed. Paging was not exercised (all responses fit one page).

## `real/` — REAL recorded responses (incident 5769dcea-385a-4ee5-9313-804f55ddb5fa)

| file | collection | content |
|---|---|---|
| `real/20260919T131342Z_satellite-perimeters.json` | satellite-perimeters | 7 MultiPolygon perimeters of one incident, `observed_watermark` 2026-07-03 11:47Z to 2026-07-04 04:14Z, `computed_at` about 2 h later each, 1264 to 3870 ha |
| `real/20260919T131340Z_clusters.json` | clusters | 30 Point cluster centres in the bbox, June and July 2026 |

The hotspots response (680 KB) stays in `data/deepfire/recorded/` (gitignored). `scripts/make_snapshots.py`
splits the perimeters response into one single-feature body per perimeter, passes each through
`fire_input.parse_deepfire` and builds `fixtures/snapshots/gavarres_real_0001..0003.json` from the
first, middle and last perimeter (`input_mode: recorded`). Their `data_status` is `stale` because the
provider computed each perimeter about two hours after its observation watermark and `as_of` is the
`computed_at`; that is the measured provider latency, not a fixture artefact.

**Fire-spread simulations (probed live 2026-09-19).** `POST /v1/fire-spread/simulations` works with
`latitude`/`longitude` or `clusterId`, `durationHours` 1-24, `model` elmfire|forefire, `ensembleMembers`
1-50 and `lookbackHours`. Runs are always seeded from hotspots observed within `lookbackHours` of NOW:
there is no as-of parameter, the July incident (cluster 5769dcea) returns 422 "No cluster hotspots match
the selected sources and lookback", and no archived run exists for it (the account archive starts
2026-07-10). The July snapshots `gavarres_real_0001..0003` therefore stay `fire_arrival_at: null` with
`forecast_unavailable` on every asset (schema 1.1); arrival is never derived from distance.

| file | run | content |
|---|---|---|
| `real/20260919T135029Z_fire-spread-simulation-latlon_12h_1member.json` | `4bbd8e98`, elmfire, 1 member, 12 h | 12 cumulative burned-area MultiPolygons, one per hour, properties `{hour, elapsed_seconds}` |
| `real/20260919T135030Z_fire-spread-simulation-latlon_12h_5members.json` | `c948ef24`, elmfire, 5 members, 12 h | 22 features: per hour several disjoint bands with `burn_probability` in multiples of 1/5 (p = 1.0 core plus fringes) |

Both are REAL COMPLETED point-ignition runs seeded now (createdAt 2026-09-19 13:49Z) at the July
incident centroid 41.89134, 3.02283 ("Cruïlles, Girona"), recorded as `{"collection", "received_at",
"body", "note"}` with no credentials inside. Body: `{id, status, model, durationHours, ensembleMembers,
latitude, longitude, locationName, ignitionPointCount, ignition, createdAt, summary{burnedAreaM2,
edgeReached, windSpeedAvgMs, windDirectionAvg}, result, links}`. Polygons are nested/monotone over hours.
The response states no simulation start time, so **t0 = createdAt is an assumption** recorded in every
forecast note. Some ensemble band polygons are not valid for GEOS; the adapter passes them through
`shapely.make_valid`.

`fireline.forecast_input.deepfire_spread_to_forecast(body, received_at, asset_points, min_burn_probability=)`
turns a run into a `forecast-input-1` dict: per facility point the first hour whose cumulative polygon
(ensemble: union of bands with `burn_probability >= min_burn_probability`, default 1/ensembleMembers)
covers it gives `arrival_at = createdAt + elapsed_seconds` (basis "hourly isochrone crossing, ...");
facilities not covered by the last hour get no estimate. `forecast_from_recorded_spread(path, ...)` does
the same from one of these files. `fixtures/snapshots/gavarres_real_0004.json` applies the 1-member run to
the real Gavarres facilities: its 12 h burned area is about 20 ha and the nearest facility is 5.3 km away,
so 0 of 99 located facilities get an arrival (the 5-member run at member fraction 0.2 also covers none).
That is the honest result of a small simulated ignition, not a tuned fixture.

## Top-level files — SYNTHETIC

Every file carries a top-level `note` saying it is synthetic. They describe an invented fire near
la Bisbal d'Empordà (Gavarres) on 2026-07-03, `cluster_id = "synthetic-gavarres"`, built with
`fireline.fire_input.geometry_to_wgs84` from `fixtures/synthetic_ignition.json`:

| file | collection | observed time | content |
|---|---|---|---|
| `20260703T080500Z_satellite-perimeters.json` | satellite-perimeters | `observed_watermark` 07:48Z (`computed_at` 08:00Z) | the 300 m ignition circle from `synthetic_ignition.json` (about 28 ha), 33 vertices |
| `20260703T091530Z_clusters.json` | clusters | `last_observed` 09:10Z | one Point cluster feature: the hotspot-centre path |
| `20260703T100500Z_satellite-perimeters.json` | satellite-perimeters | `computed_at` 10:00Z (`observed_watermark` null: the fallback path) | the fire grown about 1.5 km towards the south-southeast (about 244 ha), 20 vertices |

`load_recorded()` on this directory yields three updates in `received_at` order: perimeter
(07:48Z), hotspot centre (09:10Z), perimeter (10:00Z). The fixtures were generated once with a
throwaway script and are committed as data; they are not regenerated by the test suite.

This directory demonstrates update handling with recorded inputs. It is a recorded-input demo
with synthetic coverage, not historical as-of replay of a real incident (readme section 4).
