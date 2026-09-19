# Values-at-Risk Project Plan (v3)

Track 4, "Values at risk": Norrsken x Deepfire "AI for Wildfire" challenge, Hackbarna 2026.

Working name: **FireLine** (rename freely).

Implementation split: **risk assessment** discovers and evaluates affected locations from incoming fire updates; **analyst coordination** turns those assessments into an ordered work queue, recommended actions and proposed team assignments. The shared location contract is in section 6.3.1; ownership is in section 11.

Development workflow: use [Superpowers](https://github.com/obra/superpowers), with each task in its own branch and worktree under this repository's `.worktrees/` directory. Publish every task branch to `origin` and push progress so colleagues can review it. See [AGENTS.md](AGENTS.md) for the persistent project instructions.

v3 changes versus v2, from a four-way review on 2026-09-19: the confine-versus-evacuate rule is rewritten to match INFOCAT practice (evacuate early while a road is open, confine when the window has closed, care homes moved last); receiving facilities split into reception centres and medical destinations; deterministic edge cases moved out of the agent into the engine, and the agent given asymmetric autonomy (pessimistic moves alone, optimistic moves only via escalation); the timeline turned from a 24-hour serial chain into four parallel lanes with phase 0 budgeted; ELMFIRE, Cell2Fire, cadastre and INE dropped from day one; validation made leak-free (as-of-t replay, honest baselines, impact-relative lead time as the headline); Gavarres facts and data-source details corrected after checking the sources. Anything still marked *unverified* needs a human with an account.

## 1. One-line pitch

When a fire starts anywhere in Catalonia, an agent pulls the live satellite perimeter and spread forecast, ranks every care home, school, campsite, hospital and urbanisation in its path with a time-to-impact, recommends confine or evacuate per INFOCAT practice with the exit window shown, names the reception centre and the route, escalates what it cannot resolve as one-line questions for the coordinator, and refreshes as the fire moves. Every number is traceable to a tool call.

## 2. How the plan maps to the judging criteria

| Criterion | What we show |
|---|---|
| AI that helps first responders | The output is the coordinator queue the INFOCAT director and CECAT work from: which nuclei to confine, which to evacuate, to where, by which road, by when, and which open questions block a decision. Sections 6.4 to 6.6. |
| Technical implementation and accuracy on real data | Replay of the Les Gavarres fire (3 July 2026, 2,198 ha, 7 municipalities confined) using only data available at each replay time, scored against the real ES-Alert sequence and the observed impact times. Section 10. |
| Creative use of the provided datasets and APIs | Deepfire perimeters, fire-spread simulations and MCP server; MTG FRP Pixel feed as trigger and layer; Gencat Equipaments, INFOCAT vulnerability, Pla Alfa, Bombers live incident feed, Trànsit road closures. Section 5. |
| Demo quality | Two modes in one UI: live Catalonia (whatever is burning that day, or a synthetic ignition) and Gavarres replay with a time slider. One live triage answered by the presenter with a click. Backup video. Section 12. |

The three "strong submission" points (real data, near real time, clear operator use) are sections 5, 4 and 6.4 to 6.6 respectively.

## 3. Design principle

**Deterministic engine for the common case, agent for the edge cases, human decides.** Spread, exposure, timing, routing and the decision rule are reproducible code. The Deepfire cofounder's guidance is that values-at-risk calculation hits edge cases constantly, and that is what the agent is for: when the pipeline cannot trust its own answer for a specific asset because it needs outside evidence or a human, the agent investigates with tools, resolves what it can with cited evidence, and escalates the rest as a named question the coordinator can answer in one click. Edge cases that have a deterministic answer (take the pessimistic one) are handled in code, not by the agent.

**Autonomy is asymmetric.** The agent may move an asset's tier or occupancy in the pessimistic direction on its own. Any optimistic move (lower occupancy, "camp not in session", "track is passable") must go through `escalate` and wait for a human. It never invents a number and never issues an order.

Say this on stage, because Deepfire's own app already lists values at risk; our contribution is the decision layer on top (confine versus evacuate with the exit window, reception centre, route, latest departure, edge-case resolution with evidence, refresh on change) and it is validated against a real ES-Alert timeline.

## 4. Scope

In scope, day one:
- **Replay mode:** Les Gavarres, 3 to 5 July 2026, replayed from the first detections with a time slider, using only information available at time t, with the real confinement and evacuation timeline overlaid.
- **Live mode:** poll Deepfire clusters and perimeters for Catalonia; for any active cluster, run the full pipeline and show it on the map. If nothing is burning during the demo, inject a synthetic ignition at a pre-warmed point. Kept thin (Deepfire poll plus synthetic ignition) because "near real time" is a named strong-submission point.
- Spread forecast as arrival-time rasters from Deepfire's fire-spread simulation (primary) and from our own fast model for what-if runs.
- Exposure: buildings, population and the facility classes named in the challenge (hospitals, schools, care homes, campsites) plus urbanisations and roads.
- Confine-versus-evacuate recommendation per nucleus using a window-based rule aligned with INFOCAT practice, with the three checks and their evidence shown.
- Reception centres and medical destinations, routes, latest safe departure, first road to be cut.
- Agent: edge-case triage on the assets the pipeline flags as needing outside evidence, what-if reruns, alert drafting in CA/ES/EN, change log, coordinator queue.
- Validation against what actually happened at Gavarres.

Out of scope (say so if asked):
- Fire detection from cameras (track 1; we consume Deepfire and MTG detections).
- A research-grade spread model. ELMFIRE and Cell2Fire are not started; Deepfire's ELMFIRE run is our physics-based source and our own model is a calibrated cellular automaton for what-if only. The engine is swappable.
- Traffic simulation, suppression tactics, live operational use.

Deferred to stretch (section 14): MTG alpha-shape perimeter derivation, cadastre building footprints, INE census population, free-text ingestion, staggered departures, INFOCAT choropleth.

## 5. Data sources (provided resources first)

Everything below was checked against the live endpoint on 2026-09-19 unless marked *unverified*.

| Need | Primary (provided) | Fallback | Notes |
|---|---|---|---|
| Live fire clusters and perimeters | **Deepfire OGC API** `/ogc/features/v1/collections/<id>/items`: `deepfire:clusters` (**Point** geometry: `id`, `first_observed`, `last_observed`, `active`), `deepfire:hotspots`, `deepfire:satellite-perimeters` (MultiPolygon, `n_hotspots`, `area_m2`, `active`, algorithm `circle-union-v2`), `deepfire:static-heat-sources`; bbox + CQL2 filters, GeoJSON. Hotspots since Jan 2025, perimeters since June 2026 | NASA FIRMS area API (needs MAP_KEY) | Bearer token from app.deepfire.co Settings, API clients. `Cache-Control: max-age=60`, so poll once a minute. Shared concurrency cap returns 503 `{"code":"ogc-busy"}` with Retry-After; 30 s per-query limit; `limit` clamped at 10,000. Perimeters are hotspot hulls, not surveyed boundaries. Hotspot `source` codes include `MTG_I1`. |
| 10-minute fire activity over Spain | **LSA SAF MTG FRP Pixel** (LSA-509, MTG-I1 FCI, 10 min). Per slot `NATIVE/YYYY/MM/DD/LSA-509_MTG_MTFRPPIXEL-ListProduct_MTG-FD_YYYYMMDDhhmm.csv.gz` (about 144 per day, plus NetCDF). Archive since Jan 2025 confirmed (2026-07-03 directory listed) | Deepfire hotspots with `source = 'MTG_I1'` | Directory listings are public; file GET returns 401 without a free account. Columns *unverified* until one file is downloaded. Latency about 20 min, demonstration status: outages possible. |
| Spread forecast | **Deepfire fire-spread** `POST /v1/fire-spread/simulations` with `clusterId` or `latitude`/`longitude`, `durationHours` 1 to 24, `model` elmfire or forefire, `ensembleMembers` 1 to 50, optional `sources` and `lookbackHours`. Returns one feature per hour (MultiPolygon, `hour`, `elapsed_seconds`) plus wind summary. Status QUEUED, COMPLETED, NO_SPREAD, FAILED; auto-FAILED after 60 min queued. 2 runs in flight per client | Own CA model (section 6.2) | Poll `GET .../{id}` honouring the `Retry-After` header. Handle NO_SPREAD and FAILED. Error "the cluster is outside the modelled regions" exists and regions are not enumerated: **test on a Spanish cluster in phase 0**. Per-member ensemble output *unverified*. Cannot pass custom wind or fuel, so what-if needs our model. |
| Wind and weather | **Open-Meteo** forecast API (hourly 10 m wind speed, direction, gusts, 2 m temperature, RH; no key) and Previous Runs API (`<var>_previous_dayN`, N 0 to 7, archived since Jan 2024) | Meteocat XEMA stations via Socrata (`nzvn-apee`) for observed wind at validation time | For the July 2026 replay use `ecmwf_ifs025` or `best_match`: **AROME returns all nulls** for that period. Previous Runs gives fixed lead-time slices (previous_day1 is the run about 24 h before valid time), not a full as-issued run; say so on the validation slide. Sample a 0.1 deg grid over the bbox. WeatherNext dropped (allow-list approval takes 5 to 7 business days). |
| Fuel (CA only) | ZAFM-DW 2026 Scott and Burgan FBFM40, 10 m, `ESP_3035_ZAFM_DYNAMIC_WORLD_2026.tif` 455 MB, **EPSG:3035** (Zenodo 10.5281/zenodo.21978709), with `burgan_models_table.csv` | ICGC land cover 2024 mapped to a burnability table | Window-crop to the bbox before warping to 25831, or the reprojection eats an hour. |
| Elevation | Copernicus DEM GLO-30 | ICGC 5 m DEM | Slope and aspect once with gdaldem, cached. |
| Facilities | **Gencat Equipaments** (Socrata `8gmd-gz7i`, weekly: `nom`, `categoria` pipe-separated hierarchy, `codi_municipi`, `comarca`, `longitud`/`latitud`) | OSM POIs | One dataset covers most facility classes. Class counts (65 hospitals etc.) *unverified*. |
| Care homes with capacity | Registre d'entitats i serveis socials (`ivft-vegh`: `capacitat`, `tipologia`, `adreca`, `municipi`, `cp`; no coordinates) | OSM `social_facility` | Join to Equipaments by name and municipality; geocode the rest with the ICGC geocoder. Failed joins are edge cases (section 6.3). |
| Campsites with places | Registre de Turisme (`t2h3-cgys`): **whole accommodation register**, filter `tipus_establiment` for càmpings; `total_places`, `nom_de_la_via`, `numero`, `municipi`. **No cadastral reference field** | OSM `tourism=camp_site` | Geocode by address, or join to OSM campsites by name. |
| Schools | Directori de centres docents (`kvmv-ahh4`): use `coordenades_geo_x/y`; the `geo_1` Point field is corrupt | Equipaments | |
| Buildings and nuclei | OSM buildings via osmnx, DBSCAN on centroids into nuclei; OSM place polygons where present | Cadastre INSPIRE BU ATOM (stretch) | Cadastre GML per municipality is deferred to stretch. |
| Population | Nucleus population from Idescat municipal totals allocated by building count | INE census sections 2025 + Idescat padró (stretch) | Coarse, and shown as such. Idescat CSV endpoint *unverified*. |
| Municipal risk context | **INFOCAT map WMS** (`infocat_perill`, `infocat_vulnerabilitat`, `zones_risc_greu_incendis`); **Pla Alfa** daily level per municipality (ArcGIS FeatureServer, no key, polygon layer, field `PERIL_M` 0 to 4, native WKID 25831 so request `outSR=4326`); plan activations `wj9c-j6vf` (`plaacronim`, `plafase`, `fasedatahora`; **empty array when nothing is active**) | none | Context columns in the asset table; Pla Alfa level is a pre-alert trigger. Municipal plan register `eqag-gzjs` is all-risks; filter `risc`. |
| Live ignitions | **Bombers actuacions urgents** ArcGIS FeatureServer (point: `TAL_DESC_ALARMA1/2`, `MUNICIPI_SIG`, `ACT_DAT_INICI`, `ACT_NUM_VEH`, `ACT_X_UTM`/`ACT_Y_UTM`, `ACT_SITUACIO`; no key) | Deepfire clusters alone | The "a fire has started" trigger for live mode; cross-match with Deepfire clusters. |
| Road graph and closures | osmnx drive network, **GraphML cached in phase 0**; **Servei Català de Trànsit** live incidents (RSS 2.0 with geo/gml namespaces, no key) | none | SCT closures remove edges in live mode. In replay only closures issued before t are applied; later closures are validation ground truth (GI-660, GIV-6612, C-66). |
| Protection strips | none for Girona (DIBA franges cover Barcelona province only) | buildings within N m of the burn-probability envelope | State this limitation in the UI. |
| Historical perimeters | Bombers "cercador d'incendis" KML (23.7 MB); Agricultura per-year SHP (1986 to 2024); Agents Rurals 2026 perimeter for Gavarres (request SHP) | Copernicus EMSR888 delineation | Scorer only, never an input to the pipeline. |
| Fire event ground truth | Copernicus EMS **EMSR888** (Les Gavarres, activated 3 July 2026: first estimate, delineation, grading; StoryMap exists) | EFFIS burnt area | Activation page is JS-rendered: download vectors in a browser in phase 0. |

Everything static is downloaded into `data/` in phase 0. Live feeds are wrapped in one `feeds.py` with a cache so the demo works offline on the last snapshot. `feeds.py` takes an `as_of` timestamp and asserts nothing newer is returned; this is the replay's leak guard. Set a browser User-Agent for ICGC and Catastro downloads (406 otherwise).

Deepfire also exposes an unauthenticated **MCP server** at `https://api.deepfire.co/mcp` (POST only) with `deepfire_search_fires` and `deepfire_get_fire`. Register it with the agent so judges see the agent querying Deepfire directly. Deepfire's docs list "Values at risk: coming soon"; ask the mentors what it will return so we can show a column-level diff.

## 6. Architecture

```
TRIGGERS                       ENGINE (deterministic, no LLM)                     AGENT (LLM + tools)
Bombers actuacions feed ─┐
Deepfire clusters (1 min)┼─> fire_state.py ──> perimeter(t), FROS vector ─┐
MTG FRP pixels (10 min) ─┘   (Deepfire perimeter + hotspots; MTG as layer) │
                                                                           v
Deepfire fire-spread sim ──> spread.py ──> arrival-time rasters ───────────┤
own CA (what-if only)        (p10/p50/p90, burn probability)               │
Open-Meteo wind ─────────────┘                                             v
                                                          exposure.py ──> location assessments
Equipaments, care homes,  ───────────────────────────────>  (identity, geometry, size, value,
campsites, schools, OSM                                      exposure, sources, needs_review)
buildings, INFOCAT vuln.                                     CONTRACT: section 6.3.1
                                                                           │
                                              ANALYST COORDINATION         v
osmnx graph + SCT closures ─────────────────────────────> routing.py ──> route feasibility,
Equipaments (receiving facilities)                          destinations, exit deadlines
                                                                           v
                                                          decide.py ──> recommendations
                                                                           v
                                                          prioritisation ──> ranked assets,
Team availability + analyst input ──────────────────────> coordination       tasks, dependencies,
                                                                             proposed assignments
                                                                           v
                                              agent.py: tools over all of the above + Deepfire MCP
                                              - watch loop: on new/changed fire, run, summarise diff
                                              - triage needs_review assets: investigate, resolve
                                                pessimistically with evidence, or escalate
                                              - what-if: rerun CA with new wind, diff, re-triage
                                              - alerts in CA/ES/EN per audience
                                                                           v
                                              Streamlit: map, slider, asset table, coordinator queue
```

Everything left of the agent runs and is testable without an LLM. Every replay slot is precomputed to disk; Streamlit only reads.

### 6.1 Fire state

- Inputs per tick: active Deepfire hotspots and the latest satellite perimeter for the cluster; the newest MTG FRP Pixel slots for the bbox, applied with their real latency (a slot timestamped t is available at t + 20 min).
- Perimeter(t) is the Deepfire satellite perimeter, unioned with a buffered hull of hotspots detected at or before t. MTG pixels are shown as a layer and used as a trigger. The alpha-shape perimeter derivation from the Fire Event Tracker paper (DBSCAN 2 km, concave hull with alpha = mean nearest-neighbour distance) is stretch: at 30 ha the fire is one or two MTG pixels and the hull is degenerate.
- Forward rate of spread and direction from the displacement of FRP-weighted hotspot centroids over the last three slots. Used for the smoke-direction check, the persistence baseline, and the fire-front arrow.
- Static false-positive mask from `deepfire:static-heat-sources`.
- Replay: the same code fed from archived hotspots with detection time at or before t and MTG slots at or before t minus 20 min, advanced by the time slider. Deepfire archived perimeters may have been recomputed with later hotspots, so replay rebuilds the hull from hotspots rather than trusting the stored perimeter's timestamp.

### 6.2 Spread forecast

Two sources, both normalised to an arrival-time raster on the common grid:

1. **Deepfire simulation** (ELMFIRE, primary) for the cluster, 12 to 24 h, up to 50 ensemble members. Hourly MultiPolygons become arrival time by taking, per cell, the first hour whose polygon covers it. If the API returns per-member results, ensemble members give p10/p50/p90; if not, treat it as p50 and derive the spread from the CA. A 50-member run may take longer than the refresh tick; measure in phase 0 and pick the member count accordingly.
2. **Own model**, for what-if runs and as fallback if the API does not cover Spain: numpy cellular automaton in the style of Alexandridis et al. (2008), `p = p0 * fuel * wind * slope`, 50 to 100 m cells, 100 to 200 runs perturbing wind speed ±30 %, direction ±20 °, p0 and seed. ELMFIRE and Cell2Fire are not attempted.
- Calibration, leak-free: `p0` and minutes-per-step are fixed from the literature or calibrated on one 2025 Catalan fire from the Deepfire and MTG archives, never on Gavarres. Gavarres checkpoints are used only for scoring.
- Output contract for everything downstream: `arrival_p10`, `arrival_p50`, `arrival_p90`, `burn_prob` arrays on `grid.py`'s grid, plus a `source` string.
- Gavarres observed progression (Bombers figures relayed by Ràdio Capital and 3cat, whose posting times run 10 to 40 min later): 250 ha at 14:56, 750 ha at 16:04, 1,280 ha at 18:13, 1,800 ha at 20:45, over 2,000 ha by about 22:20. "30 ha at 13:20" *unverified*; "2,300 ha at 08:00 next day" is a stale provisional figure and is dropped. Rate of spread quoted at 2.2 km/h is the afternoon head-fire rate under tramuntana, not a constant from ignition.

### 6.3 Exposure

- Sample arrival rasters at every building, facility and road segment; aggregate buildings into nuclei (OSM place polygons, else DBSCAN on building centroids).
- Per asset: the location assessment in section 6.3.1, including class, size, capacity and estimated occupancy, value and its basis, proximity, burn probability, forecast arrival, INFOCAT municipal vulnerability and Pla Alfa level.
- Risk assessment produces exposure measurements; analyst coordination owns the final ranking. Ranking incorporates proximity, size and value, with forecast arrival and class-specific preparation time informing urgency. Section 6.3.2 defines the proposed scoring approach.
- Coordination assigns tiers **act now**, **prepare**, **monitor** using configured thresholds on lead-adjusted p10 and burn probability, then ranks within tiers. The lead-time table and thresholds are shown in the UI.
- **Deterministic edge cases, resolved in code with the pessimistic answer and a note on the asset:**
  - straddling nucleus: sample per building, cluster buildings by tier, report the nucleus as sub-nuclei.
  - location conflict between Gencat coordinates and OSM: use the location with the earlier arrival.
  - spread disagreement between Deepfire simulation and CA: use the earlier arrival, flag the disagreement.
  - perimeter conflict (Deepfire hotspot inside a nucleus, hull says outside): check `static-heat-sources` and hotspot count; single low-confidence hotspot keeps the deterministic tier with a note.
- **`needs_review` flag**, for cases that need outside evidence or a human. This is the hand-off to the agent (section 6.6). Day-one reason codes:
  - `occupancy_unknown`: no capacity, places or population field matched (geocode or join failed).
  - `occupancy_seasonal`: school in July, campsite or children's camp, second-home urbanisation; register and calendar disagree.
  - `class_ambiguous`: Equipaments category does not say whether a "centre" has beds, whether a hospital is a day clinic, whether a "residència" is elderly care or student housing.
  - `no_exit`: routing found no road, or only a track OSM classifies inconsistently.
  - Later: `not_in_any_dataset` (large building beside a sports field, a masia used as a casa de colònies).
- Unmatched geocodes and joins are edge cases, not blockers; the ingestion owner writes each one down as it happens (section 10, edge-case set).

### 6.3.1 Shared location assessment contract

One record represents **one asset in one scenario snapshot**, not an arbitrary GPS point. An asset can be a facility, campsite, building or residential nucleus. Use a stable `asset_id`; coordinates are attributes, not identity.

The colleague's risk-assessment algorithm consumes incoming fire and weather updates plus preloaded asset datasets, discovers candidate locations in the scenario area, calculates their exposure, and emits these records. The initial stream is implemented by polling APIs. The coordination algorithm consumes the resulting snapshots; it does not need to parse the raw satellite feeds.

Each snapshot has `schema_version` (string), `scenario_id` (string), `snapshot_id` (string), `as_of` (UTC timestamp: information cutoff), `computed_at` (UTC timestamp: output creation), and `assets` (array). For the MVP, each snapshot is a complete replacement for that scenario's current asset set, including assets whose exposure decreased; absence is not an explicit all-clear. Preserve snapshots for replay. Reject older snapshots for the same scenario and ignore duplicate snapshot IDs.

Each item in `assets` has the following fields. All keys are present; unavailable measurements are `null`, never silently zero. Times are ISO 8601 UTC timestamps, distances are metres, and probabilities and normalised scores are in [0, 1].

| Fields | Type | Meaning |
|---|---|---|
| `asset_id`, `name`, `asset_type` | string | Stable identity, display name and class; use `unknown` when the class is unresolved. |
| `latitude`, `longitude` | number or null | Representative location in WGS84; both null if unresolved. |
| `geometry` | GeoJSON geometry or null | Footprint where available; GeoJSON coordinates are longitude, latitude. |
| `area_m2` | nonnegative number or null | Physical footprint area. |
| `capacity`, `estimated_occupancy` | nonnegative integer or null | Maximum people versus estimated people present; capacity is not measured occupancy. |
| `occupancy_basis` | string or null | How occupancy was obtained, such as observed count, capacity used as a proxy, or allocated population. |
| `value_score`, `value_basis` | number or null; string or null | Normalised value and the versioned policy used to derive it. Economic value, vulnerability and critical-service importance must be distinguished in that policy. Until agreed, leave these null and flag review. |
| `distance_to_fire_m` | nonnegative number or null | Minimum distance between asset footprint and the scenario's current fire footprint; zero for overlap. Use the representative point if no footprint exists and record that approximation. |
| `burn_probability` | number or null | Estimated probability of asset exposure over the stated forecast horizon. |
| `arrival_p10_at`, `arrival_p50_at` | timestamp or null | Forecast arrival quantiles, when supported by the model. A null arrival does not establish safety. |
| `forecast_horizon_at`, `forecast_source` | timestamp or null; string or null | End of the forecast horizon and model/source identifier. |
| `infocat_vulnerability`, `pla_alfa_level` | string or null; integer 0–4 or null | Municipal context, with its source and timestamp recorded below. |
| `needs_review`, `review_reasons` | boolean; array of strings | Unresolved data issues using the reason codes in 6.3, plus `location_unknown` and `value_unknown`. Coordination may append routing issues such as `no_exit`. |
| `sources` | array of objects | Field-level provenance: each entry includes `fields`, `source`, `observed_at`, `available_at`, `fetched_at` and `notes`; unknown source times are null. Notes identify estimates and fallback geometry. |

The producer uses one documented asset-sampling policy for arrival and probability (including footprints and sub-nuclei), records it with the forecast source, and flags unsupported outputs. `as_of` is the information cutoff, distinct from the future forecast horizon. Replay must exclude evidence unavailable at `as_of`; a missing availability timestamp cannot establish historical availability.

### 6.3.2 Priority and analyst coordination output

[@mirrdj](https://github.com/mirrdj)'s coordination algorithm consumes the location assessments and adds routing constraints, analyst input and available team resources. Its output answers: **what needs attention, why, by when, which team could handle it, and what information or action is blocking progress?**

Start with an asset table and a sorted work queue. A graph is used for road connectivity and shared exits; traversal alone does not define priority. Road nodes have stable IDs and coordinates, and edges represent actual traversable road segments. Attach assets through their access points. Route feasibility and deadlines feed the decision and final ranking, rather than being calculated after a final decision has already been made.

Proposed within-tier scoring:

```text
priority_score = w_proximity * proximity_score
               + w_size      * size_score
               + w_value     * value_score
```

All component scores are normalised to [0, 1], higher means more priority, and nonnegative weights sum to one. Use fixed, versioned normalisation scales so scores remain comparable across updates. The proximity component initially increases as distance decreases; a forecast-based policy may use time until impact when available, recording the method and fallback. Define which size measure is used (area or people), and avoid counting size twice if value already includes it. Size semantics, the value policy, weights, missing-input handling and thresholds remain kickoff decisions, not validated operational rules. Until a complete score is available, retain the asset in the queue using its urgency tier and known deadlines, with review flags; never treat unknown value as zero.

Coordination enriches each assessment with:

| Fields | Meaning |
|---|---|
| `priority_score`, `priority_rank`, `tier` | Nullable score, position within the current scenario queue, and action tier. Rank by tier, then known action deadline (earliest first), then score (highest first), with `asset_id` as a stable tie-breaker. Unknown deadlines require review. |
| `score_components`, `priority_policy_version` | Component values, weights, input fields and fallback/missing-data notes, so the analyst can inspect the ranking. |
| `recommendation`, `decision_reasons` | Output of 6.4 and its supporting checks; unresolved checks remain visible. |
| `destination_id`, `route_id`, `latest_departure_at`, `route_status` | Nullable routing results from 6.5; distinguish feasible, infeasible and unknown routes. |
| `tasks` | Analyst work items: `task_id`, `action`, `status`, `deadline_at`, `required_capabilities`, `proposed_team_id`, `depends_on`, `blocking_questions`, and `evidence`. A proposal is not a dispatch order. |

For the MVP, team availability and capabilities are analyst-entered or fixture data with their source labelled. If no suitable team is known, leave the proposed assignment null and surface the missing resource. Task identity and analyst-confirmed status persist across risk updates. New assessments recompute priorities and flag affected tasks for review; they do not silently replace confirmed assignments. Shared-exit capacity is checked as in 6.5; optimising multi-team schedules and staggered departures remains stretch scope. This supports analyst coordination without expanding day one into suppression tactics or traffic simulation.

The two algorithms can be developed independently against a fixture snapshot using this contract. Acceptance: a new snapshot updates the ranked queue, exposes the reasons for changed priorities, and preserves task state and unresolved questions.

### 6.4 Decision rule: confine or evacuate

INFOCAT practice, as applied at Gavarres: evacuate early while there is time and an open road; confine when the exit window has closed or when moving people is worse than sheltering them; care homes and hospitals are the last to be moved and are confined with Bombers protection unless clearly threatened. The rule is window-based, per nucleus or facility:

- **Exposed:** inside the burn-probability envelope above the config threshold, or lead-adjusted p10 arrival under the config horizon.
- **Exit window:** for the best exit route, `cut_time - now - load_time(class, occupancy) - travel_time - buffer`. Load time is a config table (campsite and school short, residential medium, care home long). Route must not be in SCT closures, the smoke direction from the FROS vector must not lie over it, and vehicles needed must fit one lane in the window.
- **Outputs:**
  - `evacuate`: exposed and window open. Shown with the latest departure time and the destination.
  - `confine`: exposed and window closed, or not yet exposed but inside the p90 envelope; shelter is viable (built nucleus, not tents).
  - `confine, request protection`: exposed, window closed, and shelter not viable (tents, isolated masia, only a track). This is the headline case when it exists.
  - `monitor`: otherwise.
- Care homes and hospitals: default is `confine` even with the window open, unless burn probability at the building exceeds the high threshold; then `evacuate` with the medical-destination escalation from 6.5. This matches practice and avoids the demo recommending an evacuation the real director would not order.
- The rule can also output a staged sequence: `confine now, evacuate when bus and route confirmed`, which is what actually happened at Pou del Glaç (ES-Alert confinement at 10:30, bus later). The demo shows this sequence, not a bare "evacuate".
- Factors a coordinator will raise and the UI states as not modelled: smoke and visibility inside the nucleus, night-time evacuation, and ES-Alert drafting lag. Who issues is shown on every output: the director del pla or alcalde orders, CECAT sends the ES-Alert, we recommend.
- Output carries the three checks and their evidence. The UI labels every output "recommendation for the INFOCAT director".

### 6.5 Reception centres, medical destinations and routing

This is the "which hospital, which school" half of the track.

- Road graph from osmnx (cached GraphML); each edge gets a cut time = earliest p10 arrival along it; SCT closures issued before t remove edges.
- Destination type by class, shown as two UI columns, **reception centre** and **medical destination**:

| Evacuee class | Reception centre | Medical destination |
|---|---|---|
| Residential nucleus, urbanisation | pavelló, alberg, school as centre d'acollida | none by default |
| Campsite, children's camp, school | pavelló, alberg, school | none by default |
| Care home (residència, sociosanitari) | none | another residència or sociosanitari with free beds, else pavelló with Creu Roja support; **escalate "beds confirmed?"** |
| Hospital, CAP with inpatients | none | hospital; **escalate "beds confirmed with CatSalut?"**; no dataset has bed availability |
| Isolated masia, no shelter | nearest pavelló | none |

- Candidates from Equipaments, filtered to outside the ensemble envelope **at the departure time plus a margin**, not outside p90 for all time: Espai Ridaura, the real Vall Repòs destination, sits in Santa Cristina d'Aro, which was itself confined at 15:41; a strict filter rejects the true answer. Log this as a validation nuance.
- For each evacuated group: destination by class match and shortest safe travel time, route, latest safe departure, first road to be cut. Nuclei whose only exit is cut early are flagged; at Gavarres GI-660 km 1 to 12 was closed while nuclei south of the C-66 were being confined.
- Shared-exit capacity: vehicles needed for all nuclei on the same exit versus one lane in the window. Staggering departures is stretch.

### 6.6 Agent

The agent exists because the values-at-risk calculation has edge cases on almost every fire, and a rule-based pipeline either silently gets them wrong or stalls. The agent's core job is **edge-case triage**: take each `needs_review` asset, investigate with tools, and either resolve it with cited evidence (pessimistic direction only) or escalate it as a specific question the coordinator can answer in one click. Everything else (watch loop, what-if, alerts) hangs off the same tool layer.

Day-one tools (seven, every number the agent states comes from one of these):
- `get_assets(scenario_id, tier?, needs_review?)`, `get_decision(scenario_id, nucleus)`, `get_route(scenario_id, nucleus)`
- `lookup_facility(name | id)` across Equipaments, care-home, campsite and school registers, returning all candidate matches with their fields
- `sample_raster(layer, point)` for arrival time and burn probability at any point
- `add_override(asset_id, field, value, evidence)` and `escalate(asset_id, question, options, default)`; `set_tier` is `add_override` on the tier field
- `draft_alert(scenario_id, nucleus, audience, lang)`

Later tools: `list_active_fires(bbox)`, `get_fire(cluster_id)` (Deepfire OGC and MCP), `run_pipeline`, `run_whatif(scenario_id, wind_speed, wind_dir, wind_shift_time)`, `diff_scenarios`, `check_road(edge_id)`, and `web_search` restricted to gencat, municipal sites and the facility's own site. In replay, `web_search` is disabled or date-capped at t; otherwise it finds July news and the triage recites the ground truth. For the demo's seeded edge cases, a fixture of pre-fetched pages dated before the fire stands in for live search.

The override record stores `{value, source, quoted_snippet, fetched_at, confidence, direction}` and renders in the UI with a reject button. A post-check on every agent message verifies that each number in it appears in a tool result of that turn.

Behaviour, in build order:
1. **Edge-case triage:** for each `needs_review` asset, in tier order, an investigate-resolve-or-escalate loop with a step cap. The honest examples on Gavarres:
   - Can Xic (masia): routing finds only a track. The agent checks OSM tags, cannot determine passability, and escalates: "Only exit is an unpaved track (tracktype unknown). Passable by car? yes / no. Default: no, tier act now, confine and request protection." The question sits in the coordinator queue with the pessimistic default applied until answered.
   - A "residència" in la Bisbal matched by name has no capacity in Equipaments. The agent finds it in the social-services register under a slightly different name, takes its `capacitat`, records the join with both names as evidence. Occupancy goes up, so no escalation needed.
   - Pou del Glaç is in the school register as a casa de colònies with no occupancy field. The register says casa de colònies; the operator's page (fixture) says July programmes. The agent sets occupancy pessimistically to the site's stated capacity, raises the tier (children have a longer lead time), and escalates: "Children on site today? yes / no. Default: yes, about N." It does not claim "150 children"; that number came from the press after the fact.
   - A campsite whose `total_places` matched but whose class is ambiguous (bungalows or tents): the agent cannot tell from the registers, keeps the pessimistic "tents, shelter not viable" and escalates with the two options.
   Every resolution writes an override with evidence; every escalation is one question with a default. Both appear in the coordinator queue, and the validation (section 10) counts how many the agent resolved correctly, escalated, or got wrong with confidence.
2. **Watch loop:** every tick, if a new cluster appears in Catalonia, a Bombers actuació of type vegetation fire appears, or a tracked cluster's perimeter changed materially, run the pipeline and post a summary of what changed. Tier and decision changes only; no spam.
3. **What-if chat:** "what if the wind swings east at 18:00?" becomes a tool call, a CA rerun and a diff. Assets that change tier are re-triaged. Wind only on day one.
4. **Alert drafting:** per nucleus and audience (residents, care-home director, camp director, INFOCAT director) in Catalan, Spanish and English, ES-Alert style, citing the numbers, the route and the departure time from the tool results.
5. **Free-text ingestion** (stretch): paste a municipal notice; the agent extracts an override with evidence and reruns.

Guardrails: tool results only, visible tool calls, step cap per asset, pessimistic-only autonomy, evidence-carrying reversible overrides, number post-check, every message prefixed "recommendation, not an order". Do not pitch "low temperature" as a guardrail.

### 6.7 UI

Streamlit with pydeck or folium. Map layers: Deepfire perimeter, hotspot hull with fire-front arrow, MTG pixels, arrival isochrones, burn-probability shading, assets coloured by tier with decision icon, routes, cut roads, SCT closures. Time slider (replay) or auto-refresh (live). Side panel: asset table with the two destination columns. Bottom panel: the **coordinator queue** (open escalations, one question each, answerable with a click that re-tiers the asset on screen), the change log, and the agent chat with visible tool calls. A mode switch between live and replay. Every replay slot and the scripted what-if are precomputed and keyed by scenario hash; `st.cache_data` on everything; rasters as pre-rendered tiles or downsampled images, not live pydeck rasters.

## 7. Tech stack

Python, numpy, rasterio, geopandas, shapely, scikit-learn (DBSCAN), osmnx, networkx, requests, Streamlit, an LLM API with tool calling. No Docker. One repo, `make demo`, `data/` pre-populated.

## 8. Phase 0: hours 0 to 3, in parallel, budgeted in section 9

Registrations and requests, all fired in the first 15 minutes, then treated as upgrades that land when they land:
1. Register at LSA SAF (mokey.lsasvcs.ipma.pt). When approved, download the MTG FRP Pixel slots for 2026-07-03 to 2026-07-05 and record the ListProduct column names. Until then, replay runs on Deepfire hotspots with `source = 'MTG_I1'`.
2. Create a Deepfire account and API client. Confirm the Gavarres cluster exists (hotspots and perimeters, July 2026). **Run one fire-spread simulation on a Spanish cluster** to confirm Spain is a modelled region, time a 1-member and a 10-member run, and see what `ensembleMembers > 1` returns. Ask the mentors about rate limits for the event and what the values-at-risk endpoint will return.
3. Get a FIRMS MAP_KEY as a backup detection source.
4. Open EMSR888 in a browser and download delineation and grading vectors; note acquisition timestamps. Ask Agents Rurals or DACC for the 2026 perimeter SHP.
5. Ask the organisers the exact duration and submission format; ask the Deepfire mentors for their list of common values-at-risk edge cases; ask whether a Bombers or Protecció Civil mentor can sanity-check section 6.4.

Downloads and scaffolding, hours 0 to 3:
6. Pull the Gencat datasets in section 5 for the Baix Empordà and neighbouring comarques into `data/`; join care homes and campsites once and cache. **Write down every asset where the join or geocode fails** with the true answer if it can be found in five minutes: this list is the edge-case test set in section 10.
7. Download ZAFM-DW Spain (crop before warp), GLO-30 tiles and ICGC land cover for the Gavarres bbox; build `grid.py` (EPSG:25831) and check that all rasters align.
8. Cache the osmnx drive graph for the Gavarres bbox and for a Catalonia-wide coarse graph as GraphML.
9. Pull Open-Meteo Previous Runs (`ecmwf_ifs025`) for 3 to 5 July 2026 at a 0.1 deg grid over the bbox and cache it.
10. Reconstruct the Gavarres ground-truth timeline into `data/gavarres_truth.json` (section 10), with a source URL per entry and a `verified` flag.
11. Agree the contracts in section 11 and commit stubs: fixture asset table, fixture arrival raster from a synthetic ignition, scenario id.

## 9. Timeline (about 24 working hours, four lanes; scale proportionally)

Sync points: **S1 (hour 3)** contracts and stubs committed, `grid.py` and `feeds.py` return data for the Gavarres bbox. **S2 (hour 10)** real asset table and at least one real arrival raster. **S3 (hour 17)** end-to-end replay at 10:00, 12:00 and 14:00 with decisions, routes and triage. **S4 (hour 22)** freeze; rehearsal only.

| Lane | 0 to 3 | 3 to 10 | 10 to 17 | 17 to 22 | 22 to 24 |
|---|---|---|---|---|---|
| 1. Feeds and fire state | Phase 0 items 1 to 3, 7, 8; `grid.py`, `feeds.py` with `as_of` | Fire state from Deepfire hotspots and perimeters; MTG layer when access lands; replay slot precompute | Live mode: Deepfire poll, Bombers trigger, synthetic ignition, pre-warmed point | Latency measurement; live-mode hardening; cached snapshots | Rehearsal |
| 2. Spread | Phase 0 item 2 and 9; CA skeleton on the fixture grid | Deepfire simulation ingested to arrival rasters; CA ensemble running; CA calibrated on a 2025 fire | Scripted what-if precomputed; ensemble member count tuned to run time | Spatial and timing validation numbers | Rehearsal |
| 3. Assets, decision, routing | Phase 0 items 4, 6, 10; edge-case list started | Asset table on real data; deterministic edge cases; tiers | Decision rule 6.4; routing and destinations 6.5; edge-case set labelled and half held out | Asset-level, decision and road validation numbers; validation slide | Rehearsal |
| 4. Agent and UI | LLM tool harness and Streamlit skeleton against fixtures; item 5 and 11 | Seven tools against fixtures; triage loop; coordinator queue | Triage on real assets; watch loop; alerts; what-if wiring | Demo script, backup video, polish | Rehearsal |

Done-when per sync point: S1, one plot with all layers and the Gavarres perimeter at 14:00 on 3 July from `feeds.py`. S2, Vall Repòs and Pou del Glaç appear at the top of the asset table with plausible times. S3, GI-660 shows as cut early, routes avoid it, Can Xic escalation appears in the queue, scripted demo questions work five times in a row. S4, two full dry runs.

Cut lines if behind, in order: free-text ingestion, what-if, alert languages beyond Catalan, live mode (keep replay plus a screenshot of the live poll working), INFOCAT context columns. Never cut validation or triage on the four day-one reason codes; triage is the agentic part of the track. ELMFIRE, Cell2Fire, cadastre, INE and MTG alpha shapes are not started, not cut.

## 10. Validation (Les Gavarres, 3 to 5 July 2026)

Ground truth, checked on 2026-09-19 against ca.wikipedia, 3cat live blogs, Vilaweb, ElNacional, Crònica Global, labisbal.cat and the Copernicus EMSR888 news post. Items marked *unverified* keep the source's value but are not used as scored targets.

- Ignition **shortly after 09:45** on 3 July (not 09:15), GI-660 km 3 south of la Bisbal d'Empordà near barri Sant Pol (about 41.95 N, 3.03 E). The Wikipedia infobox coordinate (41.9158 N, 2.9794 E) is the burn centroid, not the ignition.
- ES-Alert confinements: 10:30 Sant Pol and the Pou del Glaç children's camp (about 150 children) confirmed. Vall Repòs confinement time (13:16) *unverified*. A 3cat entry at 14:14 already reports six municipalities confined, so "14:45 to 14:52 nuclei south of the C-66" and "15:41 seven municipalities" are *unverified* as times; the seven-municipality list (la Bisbal, Cruïlles-Monells-Sant Sadurní, Calonge-Sant Antoni, Castell-Platja d'Aro, Forallac, Llagostera, Santa Cristina d'Aro) is confirmed.
- Evacuations: Vall Repòs at 12:54, about 70 residents plus about 70 children and 10 monitors to Espai Ridaura, smoke-driven; about 150 residents evacuated in total, most to friends, 18 to a hotel, later Casa Santa Elena in Solius. About 80 children from the Romanyà de la Selva camp (Santa Cristina pavilion or Espai Ridaura, sources differ). Pou del Glaç: about 50 bussed out on 3 July, about 60 stayed confined overnight.
- Roads: GI-660 km 1 to 12 closed; GIV-6612 km 0 to 15 closed; GI-664 (Cassà to Cruïlles) residents only; C-66 temporarily closed, reopened about 21:56 on 4 July.
- Damage: **11 homes with severe structural damage, 14 partial, 25 total** (not 8 and 7), in Les Cabanyes (Calonge), Vall Repòs (Santa Cristina d'Aro), Mas Ambròs, Cruïlles and la Bisbal. Les Cabanyes impact about 23:46 on 3 July. Final area 2,197.57 ha (2,128.97 forest, 53.14 agricultural, 15.46 urban), 40 km perimeter. Stabilised the night of 4 July (22:03 *unverified*; announcements logged 22:13 and 23:56). Confinements lifted late morning 5 July (08:47 *unverified*; residents home by about 11:45).
- Rate of spread 2.2 km/h confirmed as the afternoon rate under tramuntana.

Leak rules for the replay, asserted in `feeds.py` and stated on the slide: the pipeline at time t sees only hotspots detected at or before t, MTG slots timestamped at or before t minus 20 min, SCT closures issued before t, and the Previous Runs forecast slice for t. EMSR888, the Agents Rurals perimeter and the truth file are read only by the scorer. `web_search` is off in replay.

Metrics, reported honestly including misses:
- **Headline, impact-relative lead time.** For each nucleus that was actually damaged (Les Cabanyes, Vall Repòs, Mas Ambròs) and each nucleus that was actually evacuated: the replay time at which we first put it in `act now`, the real ES-Alert or evacuation time, and the observed impact time from MTG or EMSR888. "Using only data available at HH:MM, we put Les Cabanyes in act-now X hours before homes burned there." The ES-Alert comparison is context, because it includes human decision latency.
- **Precision and recall at nucleus level.** Candidate universe: every nucleus and facility in the seven confined municipalities plus a 2 km buffer. A nucleus put in `act now` that ended more than 1 km outside the final perimeter and was never confined or evacuated is a false alarm.
- **Decision:** agreement of our output sequence with what was actually done (Vall Repòs evacuated, Pou del Glaç confined then bussed, most others confined). Count the cases where we say `evacuate` and the real order was `confine`.
- **Spatial:** IoU of the burn-probability > 0.5 envelope (Deepfire simulation and CA, separately) against the EMSR888 or Agents Rurals final perimeter, from the 12:00 and 14:00 states. Reliability on the 70 to 80 % bin.
- **Timing:** predicted versus observed front position at the confirmed area checkpoints in 6.2.
- **Baselines:** persistence (extrapolate the FROS vector at t) and Deepfire's own simulation. Not a 2.2 km/h buffer, which is this fire's observed rate.
- **Roads:** whether GI-660 and GIV-6612 are the roads we predict cut first, and when, scored against the closure times.
- **Edge cases:** the list from phase 0 item 6 plus cases found during lane 3, target 10 to 15, labelled by the lane-3 owner before the agent prompt is written, with half held back from the agent developer. Report on the held-out half: resolved correctly, escalated, wrong with confidence. The last number is the one we say out loud; the target is zero.
- **Real-time claim, two clocks:** data age (MTG about 20 min, Deepfire 60 s cache, not ours) and processing latency from feed publication to updated asset table (ours, target under one minute). Lead with the Deepfire clock for live mode.

## 11. Ownership and workstreams

The current implementation is split between two owners:

| Owner | Algorithm and responsibility | Handoff |
|---|---|---|
| Colleague — risk assessment | Consume incoming updates, maintain fire state, run spread/exposure calculations, discover affected locations, join asset attributes, and resolve deterministic data issues. Own the location assessment producer and exposure validation. | Versioned scenario snapshots from 6.3.1, including size/value inputs, exposure and provenance. |
| [@mirrdj](https://github.com/mirrdj) — analyst coordination | Consume assessments, calculate final priority, evaluate routes and decisions, and build the analyst's practical work queue with tasks, proposed team assignments, deadlines, dependencies and escalation questions. Own coordination validation, agent integration and analyst UI. | Enriched assessments and coordination output from 6.3.2. |

The four lanes below remain workstreams for planning; they do not assume four available people. The colleague owns lanes 1–2 and the asset-ingestion/exposure part of lane 3; [@mirrdj](https://github.com/mirrdj) owns decision/routing in lane 3 and lane 4. Re-estimate the section 9 schedule for two people before committing to its milestones.

| Lane | Owns |
|---|---|
| 1. Feeds and fire state | `grid.py`, `feeds.py` with `as_of` (Deepfire, MTG, Bombers, SCT), fire state, replay slot precompute, live mode, latency numbers |
| 2. Spread | Deepfire simulation ingestion, CA, calibration on a 2025 fire, what-if precompute, spatial and timing validation |
| 3. Assets, decision, routing | Gencat ingestion and joins, edge-case list and labels, asset table, deterministic edge cases, decision rule, routing and destinations, ground-truth file, asset-level and decision validation |
| 4. Agent and UI | Tool harness from hour 0, seven tools, triage loop, coordinator queue, watch loop, what-if wiring, alerts, Streamlit, demo script, video |

Agree by S1 on: grid CRS (EPSG:25831) and shape, the arrival-raster contract, the shared snapshot/location schema in 6.3.1, size/value definitions and priority policy in 6.3.2, the decision/task output, and the seven tool signatures. Commit a fixture snapshot so coordination can start independently of live feeds. Everyone works against stubs after that. Lane 4 never waits for real data before S2.

## 12. Demo script (3 minutes)

1. (20 s) Problem: at Gavarres, seven municipalities were confined and a camp with 150 children was in the path within an hour of ignition. Deepfire tells you where the fire is; we tell the INFOCAT director what to decide, and what is still unresolved.
2. (25 s) Live mode: the agent is watching Deepfire over Catalonia. Drop the synthetic ignition at the pre-warmed point; the precomputed scenario appears with tool calls visible.
3. (60 s) Replay, the coordinator queue at 10:00 on 3 July, using only data available then: three decisions and two questions. "Pou del Glaç: act now, confine now and evacuate when the bus and GI-664 are confirmed; capacity about N children; reception centre Espai Ridaura; GI-660 cut in about 90 minutes." "Vall Repòs: prepare." The two open questions are Can Xic's track and whether the camp is in session. The presenter answers Can Xic with a click: the tier changes on screen. Advance the slider to 12:00: Vall Repòs flips to evacuate; the change log shows why.
4. (25 s) What-if: "wind swings to tramuntana at 15:00". Precomputed rerun, diff, two nuclei change tier.
5. (20 s) Alert for the camp director in Catalan and for campsite tourists in English, numbers traced to tool calls.
6. (20 s) Validation slide: hours before impact at Les Cabanyes and Vall Repòs, agreement with the real order sequence, precision and recall, and the one thing we got wrong.
7. (10 s) Close with section 3.

Only one LLM call runs live (the Can Xic triage). Everything else is precomputed and keyed by scenario hash. Backup video recorded before the final rehearsal.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Deepfire fire-spread does not cover Spain, or ensemble semantics unclear | Test in phase 0 item 2. CA is always built; Deepfire polygons become an optional overlay. |
| Deepfire rate limits, 503 `ogc-busy` or slow ensemble runs during demo | 60 s cache, honour Retry-After, cached last snapshot, replay and demo scenarios fully precomputed. |
| LSA SAF approval does not land in time | Replay and live both run on Deepfire hotspots with `source = 'MTG_I1'`; MTG layer is an upgrade. |
| Geocoding care homes and campsites fails for some | They become edge cases with the pessimistic default; OSM POIs fill locations. |
| Decision rule contradicts what a Bombers or Protecció Civil mentor expects | Rule is window-based and confines by default for care homes; get a mentor to read section 6.4 in phase 0; decision metric in section 10 counts disagreements with the real sequence. |
| Judges ask "Deepfire already has values at risk" | Two things an API cannot ship: lead time validated against a real ES-Alert timeline, and counted edge-case triage. Show the column-level diff with their endpoint. |
| Agent states a number it made up | Tool results only, visible tool calls, number post-check, "recommendation" prefix. |
| Agent resolves an edge case optimistically from a stale page | Optimistic moves are escalations by construction; every override carries source, snippet and fetched-at with a reject button. |
| Triage loops or burns time on one asset | Step cap per asset, tier-ordered queue, pessimistic default while unresolved. |
| Replay accidentally uses future information | `as_of` assertion in `feeds.py`; truth file and final perimeters readable only by the scorer; web search off in replay. |
| Raster or CRS bugs eat the first morning | One shared `grid.py` built in phase 0 that everything imports; crop before warp; ZAFM is EPSG:3035, Pla Alfa is 25831. |
| Streamlit too slow with rasters and many layers | Precomputed slots, cached data, pre-rendered raster images. |
| Lane 4 blocked on real data | Harness and UI built against fixtures from hour 0; real data only at S2. |
| Live demo fails | Precomputed scenario, backup video. |

## 14. Stretch goals

- Consume Deepfire's values-at-risk endpoint when available and show the diff with ours.
- MTG alpha-shape perimeters and FROS from successive shapes, per the Fire Event Tracker method, once the fire is large enough.
- Cadastre INSPIRE building footprints with use codes; INE census sections with Idescat padró for real nucleus population.
- Staggered departures for nuclei sharing an exit road.
- Free-text ingestion of municipal notices as overrides.
- Open-Meteo multi-model spread (IFS, ICON, AROME where available) as wind perturbations instead of fixed ±30 %.
- Pla Alfa pre-alert: when a municipality goes to level 3 or 4, precompute exposure for its PPP zones so the first live tick is instant.
- Public view: "your nucleus: decision, route, latest departure" in CA/ES/EN.

## 15. Open questions for kickoff

- Deepfire: rate limits for the event, fire-spread coverage of Spain, ensemble output format and run time, what the values-at-risk endpoint will return, whether the hackbarna page (deepfire.co/hackbarna) has extra datasets (it rendered empty on fetch).
- Exact duration and submission format.
- Deepfire's own list of frequent values-at-risk edge cases, to extend the `needs_review` reason codes.
- Whether a mentor from Bombers or Protecció Civil can read section 6.4 and the destination table in 6.5.
- MTG ListProduct column names (needs the LSA SAF account).

## Appendix: URLs

- Deepfire docs: https://docs.deepfire.co/api/hotspots, /api/clusters, /api/satellite-perimeters, /api/fire-spread, /guides/authentication, /guides/performance-and-limits, /guides/paging-and-bulk-export, /ai/connect-to-ai. Token: `POST https://api.deepfire.co/v1/token` (client_id, client_secret). MCP: https://api.deepfire.co/mcp
- MTG FRP Pixel: https://datalsasaf.lsasvcs.ipma.pt/PRODUCTS/MTG/MTFRPPixel/NATIVE/YYYY/MM/DD/ (files `LSA-509_MTG_MTFRPPIXEL-ListProduct_MTG-FD_YYYYMMDDhhmm.csv.gz`); signup https://mokey.lsasvcs.ipma.pt/auth/signup; access wiki https://gitlab.com/helpdesk.landsaf/lsasaf_data_access/-/wikis
- Fire Event Tracker method (Paugam et al.): https://arxiv.org/abs/2606.06016; FCI-FireDyn: https://arxiv.org/abs/2510.26677
- Open-Meteo forecast: https://api.open-meteo.com/v1/forecast ; previous runs: https://previous-runs-api.open-meteo.com/v1/forecast (use `ecmwf_ifs025` or `best_match` for July 2026)
- CA reference: Alexandridis et al. (2008), "A cellular automata model for forest fire spread prediction", Applied Mathematics and Computation
- ZAFM-DW fuels: https://zenodo.org/records/21978709 (EPSG:3035)
- Gencat Equipaments: https://analisi.transparenciacatalunya.cat/resource/8gmd-gz7i.geojson; care homes `ivft-vegh`; tourism register `t2h3-cgys` (filter `tipus_establiment`); schools `kvmv-ahh4` (use `coordenades_geo_x/y`); plan activations `wj9c-j6vf`; municipal plan register `eqag-gzjs` (filter `risc`)
- INFOCAT WMS: https://pcivil.icgc.cat/ogc/geoservei?map=/opt/idec/dades/pcivil/risc_incendis.map&service=WMS&version=1.3.0&request=GetCapabilities ; viewer https://pcivil.icgc.cat/pcivil/v2/index.html
- Pla Alfa: https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/Pla_Alfa_Municipal_Avui_FL_2_view/FeatureServer/0 (request `outSR=4326`)
- Bombers live actuacions: https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/ACTUACIONS_URGENTS_online_PRO_VW/FeatureServer/0
- Bombers historical perimeters KML: https://interior.gencat.cat/web/.content/home/serveis/bases_cartografiques/perimetres_cercador_incendis/pericat_qgis.kml ; Agricultura per-year SHP: http://www.gencat.cat/agricultura/sig/bases/incendisYY.zip ; PPP: https://www.gencat.cat/agricultura/sig/bases/perprot.zip
- Trànsit incidents: http://www.gencat.cat/transit/opendata/incidenciesRSS.xml and incidenciesGML.xml
- ICGC land cover: https://datacloud.icgc.cat/datacloud/cobertes-sol/gpkg/cobertes-sol-v1r0-2024.zip ; census sections https://datacloud.icgc.cat/datacloud/bseccen_etrs89/shp/ (browser User-Agent required)
- Cadastre buildings ATOM (stretch): https://www.catastro.hacienda.gob.es/INSPIRE/buildings/ES.SDGC.bu.atom.xml (browser User-Agent required)
- INE sections 2025 (stretch): https://www.ine.es/prodyser/cartografia/seccionado_2025.zip
- INFOCAT plan PDF: https://interior.gencat.cat/web/.content/home/030_arees_dactuacio/proteccio_civil/plans_de_proteccio_civil/plans_de_proteccio_civil_a_catalunya/02-plans-especials/infocat/document_pla_infocat.pdf
- Copernicus EMSR888: https://mapping.emergency.copernicus.eu/activations/EMSR888/
- Gavarres sources: https://ca.wikipedia.org/wiki/Incendi_de_les_Gavarres_de_2026 ; 3cat live blogs (directe/10630, directe/10650, noticia/3418433, 3418609, 3418696); https://www.radiocapital.cat/incendi-a-la-bisbal-demporda/ ; Vilaweb, ElNacional, Crònica Global coverage of 3 to 6 July 2026; labisbal.cat
- DIBA (Barcelona province only, not Gavarres): water points https://incendis.diba.cat/fitxers/DadesObertes/json/PuntsAigua.json ; urbanisations https://incendis.diba.cat/fitxers/DadesObertes/shp/UrbanitzacionsNuclis_shp.zip
