# Values-at-Risk Project Plan (v2)

Track 4, "Values at risk": Norrsken x Deepfire "AI for Wildfire" challenge, Hackbarna 2026.

Working name: **FireLine** (rename freely).

v2 changes versus v1: built on the challenge's provided resources (Deepfire API, MTG FRP Pixel feed, WeatherNext, ELMFIRE, Gencat datasets) instead of generic substitutes; adds a live mode to satisfy "results in or near real time"; makes the agent loop explicit; adds receiving facilities (which hospital, which shelter), the INFOCAT confine-versus-evacuate rule, and a verified replay fire with a real evacuation timeline to validate against. Facts below come from reading the docs on 2026-09-19; anything marked *unverified* must be checked in phase 0.

## 1. One-line pitch

When a fire starts anywhere in Catalonia, an agent pulls the live satellite perimeter and spread forecast, ranks every care home, school, campsite, hospital and urbanisation in its path with a time-to-impact, recommends confine or evacuate per the official INFOCAT criteria, names the receiving facility and the route, and refreshes every ten minutes as the fire moves. Every number is traceable to a tool call.

## 2. How the plan maps to the judging criteria

| Criterion | What we show |
|---|---|
| AI that helps first responders | The output is the exact decision the INFOCAT director and CECAT must make: which nuclei to confine, which to evacuate, to where, by which road, by when. Section 6.5. |
| Technical implementation and accuracy on real data | Replay of the Les Gavarres fire (3 July 2026, 2,198 ha, 7 municipalities confined) with our recommendations compared against the real ES-Alert timeline. Section 9. |
| Creative use of the provided datasets and APIs | Deepfire perimeters + fire-spread simulations + MCP server; MTG FRP Pixel 10-minute feed turned into a live front; Gencat Equipaments, INFOCAT vulnerability, Pla Alfa, Bombers live incident feed, Trànsit road closures; ELMFIRE-class spread; WeatherNext ensemble wind if access lands in time. Section 5. |
| Demo quality | Two modes in one UI: live Catalonia (whatever is burning that day, or a synthetic ignition) and Gavarres replay with a time slider. Backup video. Section 11. |

The three "strong submission" points (real data, near real time, clear operator use) are sections 5, 4 and 6.5 respectively.

## 3. Design principle

**Deterministic engine, agent on top, human decides.** Spread, exposure, timing and routing are reproducible code. The agent orchestrates: it notices a new fire, calls the tools in the right order, explains the result in plain language, answers what-if questions by re-running the engine, and drafts alerts. It never invents a number and never issues an order. Say this sentence on stage, because Deepfire's own app already lists values at risk; our contribution is the decision layer on top (confine vs evacuate, receiving facility, route, latest departure, refresh on change).

## 4. Scope

In scope:
- **Live mode:** poll Deepfire clusters/perimeters and the MTG FRP Pixel feed for Catalonia; for any active cluster, run the full pipeline and show it on the map. If nothing is burning during the demo, inject a synthetic ignition at a chosen point (the pipeline is identical from that point on).
- **Replay mode:** Les Gavarres, 3 to 5 July 2026, replayed from the first detections with a time slider, with the real confinement and evacuation timeline overlaid.
- Spread forecast as arrival-time rasters with uncertainty (from Deepfire's fire-spread simulation and from our own model for what-if runs).
- Exposure: buildings, population, and the specific facility classes named in the challenge (hospitals, schools, care homes, campsites) plus urbanisations and roads.
- Confine-versus-evacuate recommendation per nucleus using the INFOCAT criteria, with the justification shown.
- Receiving facilities and routes: which hospital or reception centre takes each evacuated group, which road, latest safe departure.
- Agent interface: what-if reruns, alert drafting in CA/ES/EN, and a change log ("since the last update, these two nuclei changed tier").
- Validation against what actually happened at Gavarres.

Out of scope (say so if asked):
- Fire detection from cameras (track 1; we consume Deepfire and MTG detections).
- A research-grade spread model (we use Deepfire's ELMFIRE run and a calibrated fast model of our own; the engine is swappable).
- Traffic simulation, suppression tactics, live operational use.

## 5. Data sources (provided resources first)

| Need | Primary (provided) | Fallback | Notes |
|---|---|---|---|
| Live fire clusters and perimeters | **Deepfire OGC API**: `deepfire:clusters`, `deepfire:hotspots`, `deepfire:satellite-perimeters` (MultiPolygon, `n_hotspots`, `area_m2`, `active`), bbox + CQL2 filters, GeoJSON. History: hotspots since Jan 2025, perimeters since June 2026 | NASA FIRMS area API (needs MAP_KEY) | Bearer token from app.deepfire.co API clients. Responses cached 60 s, so poll once a minute. No webhooks. Perimeters are "circle-union" hulls of hotspots, not surveyed boundaries. |
| 10-minute fire activity over Spain | **LSA SAF MTG FRP Pixel** (LSA-509, MTG-I1 FCI, 10 min, ~1 to 2 km over Iberia, ~20 min latency). Per slot a `.csv.gz` ListProduct with lat/lon/time/FRP. Archive since Jan 2025, so July 2026 is there | Deepfire hotspots with `source = 'MTG_I1'` | Free registration required; downloads return 401 without it. Demonstration status: outages possible. Perimeter recipe from the UPC Fire Event Tracker: DBSCAN eps 2 km, alpha shape, match to previous slot, forward rate of spread from successive shapes. |
| Spread forecast | **Deepfire fire-spread** `POST /v1/fire-spread/simulations` with `clusterId` or lat/lon, `durationHours` 1 to 24, `model` elmfire or forefire, `ensembleMembers` 1 to 50. Returns one MultiPolygon per hour plus wind summary. Poll `GET .../{id}` every 10 s; 2 runs in flight per client | Our own model (section 6.2): ELMFIRE or Cell2Fire if it runs, numpy CA otherwise | Cannot pass custom wind or fuel, so what-if scenarios need our model. "Cluster outside modelled regions" is a possible failure: **test on a Spanish cluster in phase 0**. |
| Wind and weather | **Google WeatherNext 3** (0.1 deg, 6-hourly inits, p10/p50/p90 of 64 members via Earth Engine or BigQuery, ~8 h latency) | **Open-Meteo** forecast API (hourly 10 m wind, no key) and Previous Runs API (as-issued forecasts archived since 2024, so July 2026 replay is covered) | WeatherNext needs an allow-list request with 5 to 7 business day approval: **submit the form today**. Build on Open-Meteo; swap in WeatherNext percentiles if approved. |
| Fuel | ZAFM-DW 2026 Scott and Burgan FBFM40, 10 m, Spain file 455 MB (Zenodo 10.5281/zenodo.21978709) | ICGC land cover 2024 (GPKG/GeoTIFF) mapped to a burnability table | FBFM40 is what ELMFIRE and Cell2Fire expect natively. |
| Elevation | Copernicus DEM GLO-30 | ICGC 5 m DEM | Slope and aspect once with gdaldem, cached. |
| Facilities | **Gencat Equipaments** (Socrata `8gmd-gz7i`, weekly, lat/lon: 65 hospitals, 636 CAPs, 122 sociosanitaris, 146 fire stations, schools, town halls, albergs) | OSM POIs | One dataset covers most of the challenge's facility classes. |
| Care homes with capacity | Registre d'entitats i serveis socials (`ivft-vegh`, `capacitat` field, address only) | OSM `social_facility` | Geocode with the ICGC geocoder or join to Equipaments by name. |
| Campsites with places | Registre de Turisme càmpings (`t2h3-cgys`, 352 sites, `total_places`, cadastral reference) | OSM `tourism=camp_site` | Geocode via cadastral reference. |
| Schools | Directori de centres docents (`kvmv-ahh4`, coords) | Equipaments | |
| Buildings | Cadastre INSPIRE BU ATOM feed, per municipality GML (province 17 for Girona) | OSM buildings via osmnx | |
| Population | INE census sections 2025 (polygons) + Idescat padró per section | WorldPop / GHSL | Idescat CSV endpoint *unverified*. |
| Municipal risk context | **INFOCAT map WMS** (`infocat_perill`, `infocat_vulnerabilitat`, `zones_risc_greu_incendis`); **Pla Alfa** daily danger level per municipality (public ArcGIS FeatureServer, no key); municipal INFOCAT plan status (`eqag-gzjs`) | none | Shown as context in the asset table; Pla Alfa level is an agent trigger for pre-alert. |
| Live ignitions | **Bombers actuacions urgents** ArcGIS FeatureServer (`ACTUACIONS_URGENTS_online_PRO_VW`, point, incident type, municipality, vehicles) | Deepfire clusters alone | The "a fire has started" trigger for live mode; cross-match with Deepfire clusters. |
| Road graph and closures | osmnx drive network; **Servei Català de Trànsit** live incidents (RSS and GML, no key) | none | SCT closures become hard edge removals in routing, and are validation ground truth for replay (GI-660, GIV-6612, C-66 at Gavarres). |
| Historical perimeters | Bombers "cercador d'incendis" KML; Agricultura per-year SHP (1986 to 2024); Agents Rurals 2026 perimeter for Gavarres (request SHP) | Copernicus EMSR888 delineation | Ground truth for section 9. |
| Fire event ground truth | Copernicus EMS **EMSR888** (Les Gavarres, activated 3 July 2026: first estimate, delineation, grading) | EFFIS burnt area | Activation page is JS-rendered: open in a browser and download the vector packages in phase 0. |

Everything static is pre-downloaded into `data/` before the event. Live feeds are wrapped in one `feeds.py` with a cache so the demo works offline on the last snapshot.

Deepfire also exposes an unauthenticated **MCP server** at `https://api.deepfire.co/mcp` with `deepfire_search_fires` and `deepfire_get_fire`. Register it with the agent so judges see the agent querying Deepfire directly.

## 6. Architecture

```
TRIGGERS                       ENGINE (deterministic, no LLM)                     AGENT (LLM + tools)
Bombers actuacions feed ─┐
Deepfire clusters (1 min)┼─> fire_state.py ──> perimeter(t), FROS vector ─┐
MTG FRP pixels (10 min) ─┘   (cluster, alpha shape, match to previous)     │
                                                                           v
Deepfire fire-spread sim ──> spread.py ──> arrival-time rasters ───────────┤
own model (ELMFIRE / CA)     (p10/p50/p90, burn probability)               │
Open-Meteo / WeatherNext ────┘                                             v
                                                          exposure.py ──> asset table
Equipaments, care homes,  ───────────────────────────────>  (class, occupancy, t_impact, tier)
campsites, schools, cadastre                                               │
buildings, INE population,                                                 v
INFOCAT vulnerability                                     decide.py ──> confine | evacuate | monitor
                                                            (INFOCAT criteria, explicit rules)
                                                                           │
osmnx graph + SCT closures ─────────────────────────────> routing.py ──> receiving facility, route,
Equipaments (hospitals, pavellons, albergs)                  road cut times, latest departure
                                                                           │
                                                                           v
                                              agent.py: tools over all of the above + Deepfire MCP
                                              - on new/changed fire: run pipeline, summarise diff
                                              - what-if: rerun own model with new wind, diff
                                              - alerts in CA/ES/EN per audience
                                              - free-text overrides (stretch)
                                                                           │
                                                                           v
                                              Streamlit: map, time slider, asset table, agent panel
```

Everything left of the agent runs and is testable without an LLM.

### 6.1 Fire state (live front from detections)

- Inputs per tick: active Deepfire hotspots and the latest satellite perimeter for the cluster; the newest MTG FRP Pixel slots for the bbox.
- Build our own perimeter from FRP pixels the way the UPC Fire Event Tracker does: DBSCAN (eps 2 km), alpha shape (alpha about the mean nearest-neighbour distance), match to the previous tick, union with Deepfire's perimeter. Keep both on the map with their source labelled.
- Spread direction and forward rate of spread from the displacement of successive alpha shapes and of FRP-weighted centroids. Quantised to 1 pixel per 10 min, so smooth over 3 slots.
- Static false-positive mask from `deepfire:static-heat-sources`.
- Replay: the same code fed from archived MTG slots and historical Deepfire hotspots for 3 to 5 July 2026, advanced by the time slider.

### 6.2 Spread forecast

Two sources, both normalised to an arrival-time raster on the common grid:

1. **Deepfire simulation** (ELMFIRE by default) for the cluster, 12 to 24 h, up to 50 ensemble members. Hourly MultiPolygons become arrival time by taking, per cell, the first hour whose polygon covers it; ensemble members give p10/p50/p90 if the API returns per-member results (*semantics unverified*; if not, treat it as the p50 only).
2. **Own model**, needed for what-if runs and as fallback if the API does not cover Spain:
   - Preferred: ELMFIRE from the repo Dockerfile (10 to 20 min build) with ZAFM FBFM40 fuels, GLO-30 slope/aspect, zero canopy, hourly Open-Meteo wind stacked as multi-band `ws`/`wd` rasters, ignition = observed perimeter. Tutorial 03's run script is the template. Or Cell2Fire (C2F-W), which takes FBFM40 ASCII grids and has built-in stochastic burn probability with no MPI.
   - Guaranteed fallback: numpy cellular automaton in the style of Alexandridis et al. (2008), `p = p0 * fuel * wind * slope`, 50 to 100 m cells, 100 to 200 runs perturbing wind speed ±30 %, direction ±20 °, p0 and seed. Minutes-per-step calibrated on the first two hours of observed MTG progression, holding the rest out.
   - Build the CA first (about two hours, guaranteed). Try ELMFIRE in parallel and use its time-of-arrival raster if it runs.
- Output contract for everything downstream: `arrival_p10`, `arrival_p50`, `arrival_p90`, `burn_prob` arrays on `grid.py`'s grid, plus a `source` string.
- Calibration data for Gavarres (Ràdio Capital live blog): 30 ha at 13:20, 250 ha at 14:56, 750 ha at 16:04, 1,280 ha at 18:13, 1,800 ha at 20:45, 2,300 ha at 08:00 next day; rate of spread quoted at 2.2 km/h.

### 6.3 Exposure

- Sample arrival rasters at every building, facility and road segment; aggregate buildings into nuclei (cadastre or OSM place polygons, else DBSCAN on building centroids).
- Per asset: class, occupancy (care-home `capacitat`, campsite `total_places`, school enrolment if available, census population for nuclei), burn probability, p10 and p50 arrival, INFOCAT municipal vulnerability level, Pla Alfa level today.
- Ranking: p10 arrival minus a class-specific lead time (care home and hospital longest, campsite and school next, residential nuclei shortest). The lead-time table is a config file shown in the UI.
- Tiers: **act now**, **prepare**, **monitor**, from thresholds on lead-adjusted p10 and burn probability. Config values, shown in the UI.

### 6.4 Decision rule: confine or evacuate

The INFOCAT plan (2023 revision) makes confinement the default and allows evacuation only when danger is imminent, confinement is unsafe, and evacuation is viable. Encode it as explicit rules per nucleus or facility:

- **Imminent:** lead-adjusted p10 arrival under the config threshold and burn probability above it.
- **Confinement unsafe:** any of: no protection strip (from DIBA franges data where available, else buildings within N m of the burn-probability envelope), high-vulnerability occupancy (care home, hospital, camp with tents), or the nucleus is inside the p90 envelope with high intensity (from ELMFIRE flame length if available).
- **Evacuation viable:** at least one exit route whose cut time exceeds departure plus travel time plus buffer, road not in SCT closures, capacity sanity check (vehicles needed versus one lane), smoke direction not over the route (from FROS vector).
- Output: `confine`, `evacuate`, or `monitor` with the three checks and their evidence listed. The agent phrases this as a recommendation for the INFOCAT director; the UI labels it as such.

### 6.5 Receiving facilities and routing

This is the "which hospital, which school" half of the track that v1 missed.

- Road graph from osmnx; each edge gets a cut time = earliest p10 arrival along it; SCT closures remove edges.
- Candidate destinations from Equipaments: hospitals and sociosanitaris for care-home and medical evacuees; pavellons, schools and albergs outside the p90 envelope for general population; must be outside the ensemble envelope with a margin and reachable before cut time.
- For each evacuated group: destination by class match and shortest safe travel time, route, latest safe departure, and the first road to be cut. Flag nuclei whose only exit is cut early; that is the headline if one exists (at Gavarres, GI-660 km 1 to 12 was closed while nuclei south of the C-66 were being confined).
- Stretch: stagger departures for nuclei sharing an exit.

### 6.6 Agent

Tools (every number the agent states comes from one of these):
- `list_active_fires(bbox)` and `get_fire(cluster_id)` (Deepfire OGC; also the Deepfire MCP tools)
- `run_pipeline(cluster_id | ignition_point, t)` returns a scenario id
- `run_whatif(scenario_id, wind_speed, wind_dir, wind_shift_time, closed_roads)` reruns the own model
- `get_assets(scenario_id, tier?)`, `get_decision(scenario_id, nucleus)`, `get_route(scenario_id, nucleus)`
- `diff_scenarios(a, b)` returns assets whose tier, decision or latest departure changed
- `add_override(name, location, attributes)` for facts from free text
- `draft_alert(scenario_id, nucleus, audience, lang)`

Behaviour, in build order:
1. **Watch loop:** every tick, if a new cluster appears in Catalonia, or a Bombers actuació of type vegetation fire appears, or a tracked cluster's perimeter changed materially, run the pipeline and post a summary of what changed. Tier and decision changes only; no spam.
2. **What-if chat:** "what if the wind swings east at 18:00?" becomes a tool call, a rerun and a diff.
3. **Alert drafting:** per nucleus and audience (residents, care-home director, camp director, INFOCAT director) in Catalan, Spanish and English, using the ES-Alert style, citing the numbers and route.
4. **Free-text ingestion** (stretch): paste a municipal notice; the agent extracts an override and reruns.

Guardrails: tool results only, temperature low, tool calls visible in the UI, every message prefixed "recommendation, not an order".

### 6.7 UI

Streamlit with pydeck or folium. Map layers: Deepfire perimeter, our MTG-derived perimeter with FROS arrow, arrival isochrones, burn-probability shading, assets coloured by tier with decision icon, routes, cut roads, SCT closures, INFOCAT vulnerability as a faint choropleth. Time slider (replay) or auto-refresh (live). Side panel: asset table. Bottom panel: agent chat with visible tool calls and the change log. A mode switch between live and replay.

## 7. Tech stack

Python, numpy, rasterio, geopandas, shapely, scikit-learn (DBSCAN), alphashape, osmnx, networkx, requests, Streamlit, an LLM API with tool calling. Optional Docker for ELMFIRE. One repo, `make demo`, `data/` pre-populated.

## 8. Before the event (do this week, in this order)

1. **Submit the WeatherNext access form today** (5 to 7 business days). Assume it will not arrive; treat it as a bonus.
2. Register at LSA SAF (mokey.lsasvcs.ipma.pt) and download the MTG FRP Pixel slots for 2026-07-03 to 2026-07-05 (144 files per day). Read one `.csv.gz` and record the column names.
3. Create a Deepfire account and API client. Confirm the Gavarres cluster exists (hotspots and perimeters, July 2026). **Run one fire-spread simulation on a Spanish cluster** to confirm Spain is a modelled region and to see what `ensembleMembers > 1` returns. Ask the mentors about rate limits for the event and whether the values-at-risk endpoint can be previewed.
4. Get a FIRMS MAP_KEY as a backup detection source.
5. Open EMSR888 in a browser and download delineation and grading vectors; note acquisition timestamps. Ask Agents Rurals or DACC for the 2026 perimeter SHP.
6. Pull the Gencat datasets listed in section 5 for the Baix Empordà and neighbouring comarques into `data/`; geocode care homes and campsites once and cache.
7. Download ZAFM-DW Spain, GLO-30 tiles and ICGC land cover for the Gavarres bbox; build `grid.py` and check that all rasters align.
8. Try the ELMFIRE Docker build once. If it does not run in an hour, note it and plan on Cell2Fire or the CA.
9. Pull Open-Meteo Previous Runs data for 3 to 5 July 2026 at a 0.1 deg grid over the bbox and cache it.
10. Reconstruct the Gavarres ground-truth timeline into `data/gavarres_truth.json` (section 9).
11. Confirm with organisers whether pre-event data preparation and repo skeletons are allowed, the exact duration, and the submission format.

## 9. Timeline (about 24 working hours; scale proportionally)

| Phase | Hours | Deliverable | Done when |
|---|---|---|---|
| 1. Common grid and feeds | 0 to 3 | Rasters aligned; `feeds.py` returns Deepfire clusters/perimeters and MTG slots for a bbox and time | One plot with all layers and the Gavarres perimeter at 14:00 on 3 July |
| 2. Fire state | 3 to 5 | Alpha-shape perimeter and FROS from MTG pixels; matches Deepfire perimeter roughly | Replay animation of 3 July looks right |
| 3. Spread | 5 to 9 | Deepfire simulation ingested; CA ensemble running; ELMFIRE attempt in parallel | Arrival rasters from at least one source; CA front roughly matches 13:20 to 16:04 progression |
| 4. Exposure and decision | 9 to 13 | Asset table with tiers and confine/evacuate rule | Vall Repòs and Pou del Glaç appear at the top with plausible times |
| 5. Routing and destinations | 13 to 16 | Routes, cut times, receiving facilities | GI-660 shows as cut early; routes avoid it |
| 6. Agent | 16 to 19 | Watch loop, what-if, alerts | Scripted demo questions work 5 times in a row |
| 7. Validation | 19 to 21 | Section 10 numbers | One slide |
| 8. Live mode, polish, rehearsal | 21 to 24 | Live Catalonia view with synthetic ignition, backup video, pitch | Two full dry runs |

Cut lines if behind, in order: free-text ingestion, ELMFIRE (keep CA), staggered departures, INFOCAT choropleth, live mode (keep replay plus a screenshot of the live feed working). Never cut validation.

## 10. Validation (Les Gavarres, 3 to 5 July 2026)

Ground truth to reconstruct in phase 0 (sources: ca.wikipedia "Incendi de les Gavarres de 2026", Ràdio Capital live blog, govern.cat press notes 843820, 844100, 844400, 844440, 3cat, labisbal.cat):

- Ignition about 09:15 on 3 July, GI-660 south of la Bisbal d'Empordà near barri Sant Pol (about 41.95 N, 3.03 E; verify against EMSR888).
- ES-Alert confinements: 10:30 Sant Pol and the Pou del Glaç children's camp (150 kids); 13:16 Vall Repòs; 14:45 to 14:52 dispersed nuclei south of the C-66; 15:41 seven municipalities (la Bisbal, Cruïlles-Monells-Sant Sadurní, Calonge-Sant Antoni, Castell-Platja d'Aro, Forallac, Llagostera, Santa Cristina d'Aro).
- Evacuations: Vall Repòs at 12:54 (about 70 people to Espai Ridaura); 80 children from the Romanyà de la Selva camp; Pou del Glaç by bus.
- Roads: GI-660 km 1 to 12 closed, GIV-6612 closed, GI-664 residents only, C-66 temporarily closed.
- Damage: 8 homes destroyed and 7 damaged in Les Cabanyes (Calonge) and Vall Repòs (Santa Cristina d'Aro). Final area 2,197.57 ha, 40 km perimeter. Stabilised 4 July 22:03. Confinements lifted 5 July 08:47.

Metrics, reported honestly including misses:
- **Spatial:** IoU of the burn-probability > 0.5 envelope (from Deepfire's simulation and from ours, separately) against the Agents Rurals or EMSR888 final perimeter, from the 10:00, 12:00 and 14:00 states. Reliability check on the 70 to 80 % bin.
- **Asset-level:** for each real confinement or evacuation, at what replay time did we first put that nucleus in "act now", and what lead time does that give versus the real ES-Alert time. For Vall Repòs and Les Cabanyes, whether the destroyed-homes area was inside our envelope. False alarms: nuclei we flagged that were never affected.
- **Decision:** whether our confine/evacuate rule agrees with what was actually done (Vall Repòs evacuated, most others confined).
- **Roads:** whether GI-660 and GIV-6612 are the roads we predict cut first, and when.
- **Timing:** predicted versus observed front position at the six area checkpoints in section 6.2.
- **Baseline:** a circular buffer at 2.2 km/h. If neither model beats it, say so.
- **Real-time claim:** measured end-to-end latency from an MTG slot timestamp to an updated asset table.

## 11. Roles (team of 4; merge for fewer)

| Role | Owns |
|---|---|
| Feeds and geo | `feeds.py` (Deepfire, MTG, Bombers, SCT), `grid.py`, fire state, Gencat asset ingestion, ground-truth file |
| Spread | Deepfire simulation ingestion, CA, ELMFIRE/Cell2Fire attempt, calibration |
| Decision and routing | Exposure, INFOCAT rule, routing, receiving facilities, validation metrics |
| Agent and UI | Tools, watch loop, what-if, alerts, Streamlit, demo script, video |

Agree in the first hour on: grid CRS (EPSG:25831) and shape, the arrival-raster contract, the asset table schema, the scenario id, and tool signatures. Everyone works against stubs after that.

## 12. Demo script (3 minutes)

1. (20 s) Problem: at Gavarres, seven municipalities were confined and a camp with 150 children was in the path within an hour of ignition. Deepfire tells you where the fire is; we tell the INFOCAT director what to do about it.
2. (30 s) Live mode: the agent is watching Deepfire and MTG over Catalonia. Drop a synthetic ignition; watch the pipeline run, tool calls visible.
3. (50 s) Replay at 10:00 on 3 July: MTG-derived front, Deepfire spread, asset table. "Pou del Glaç camp: act now, evacuate, 150 children, receiving centre Espai Ridaura, via GI-664, leave by 10:40; GI-660 cut in about 90 minutes." Advance the slider to 12:00: Vall Repòs flips from confine to evacuate; the change log shows why.
4. (30 s) What-if: "wind swings to tramuntana at 15:00". Rerun, diff, two nuclei change tier.
5. (20 s) Alert for the camp director in Catalan and for campsite tourists in English.
6. (20 s) Validation slide: lead time we would have given for each real ES-Alert, IoU, and the one thing we got wrong.
7. (10 s) Close with section 3.

Backup video recorded before the final rehearsal.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Deepfire fire-spread does not cover Spain, or ensemble semantics unclear | Test in phase 0. Own model is always built; Deepfire polygons become an optional overlay. |
| Deepfire rate limits or 503 during demo | 60 s cache, cached last snapshot, replay mode is fully offline. |
| MTG feed outage (demonstration product) | Archive already downloaded for replay; live mode falls back to Deepfire hotspots with `source = 'MTG_I1'`. |
| WeatherNext access not approved in time | Open-Meteo from day one; WeatherNext is a bonus swap. |
| ELMFIRE does not build or run | CA is built first; Cell2Fire as middle option. |
| Geocoding care homes and campsites fails for some | Fall back to OSM POIs for the missing ones; keep the Gencat occupancy fields where matched. |
| Judges ask "Deepfire already has values at risk" | Answer: ours is the decision layer (confine vs evacuate per INFOCAT, receiving facility, route, latest departure, change log) and it can consume their endpoint when it ships. |
| Agent states a number it made up | Tool results only, visible tool calls, low temperature, "recommendation" prefix. |
| Raster or CRS bugs eat the first morning | Grid built and checked before the event. |
| Live demo fails | Cached scenario, backup video. |

## 14. Stretch goals

- Consume Deepfire's values-at-risk endpoint when available and show the diff with ours.
- WeatherNext p10/p90 wind driving the ensemble spread instead of fixed perturbations.
- Staged departures for nuclei sharing an exit road.
- Pla Alfa pre-alert: when a municipality goes to level 3 or 4, precompute exposure for its PPP zones so the first live tick is instant.
- Public view: "your nucleus: decision, route, latest departure" in CA/ES/EN.

## 15. Open questions for kickoff

- Deepfire: rate limits for the event, fire-spread coverage of Spain, ensemble output format, values-at-risk preview, whether the hackbarna page (deepfire.co/hackbarna) has extra datasets (it rendered empty on fetch).
- Whether pre-event preparation is allowed and the exact duration and submission format.
- Whether a mentor from Bombers or Protecció Civil can sanity-check the confine/evacuate rule.

## Appendix: URLs

- Deepfire docs: https://docs.deepfire.co/api/hotspots, /api/clusters, /api/satellite-perimeters, /api/fire-spread, /guides/authentication, /guides/paging-and-bulk-export, /ai/connect-to-ai. Token: `POST https://api.deepfire.co/v1/token`. MCP: https://api.deepfire.co/mcp
- MTG FRP Pixel: https://datalsasaf.lsasvcs.ipma.pt/PRODUCTS/MTG/MTFRPPixel/NATIVE/YYYY/MM/DD/ (files `LSA-509_MTG_MTFRPPIXEL-ListProduct_MTG-FD_YYYYMMDDhhmm.csv.gz`); signup https://mokey.lsasvcs.ipma.pt/auth/signup; access wiki https://gitlab.com/helpdesk.landsaf/lsasaf_data_access/-/wikis
- Fire Event Tracker method: https://arxiv.org/abs/2606.06016; FCI-FireDyn: https://arxiv.org/abs/2510.26677
- WeatherNext: https://developers.google.com/weathernext (access-forecast guide has the request form); EE asset `projects/gcp-public-data-weathernext/assets/weathernext_3_0_0_0p1deg`
- Open-Meteo previous runs: https://previous-runs-api.open-meteo.com
- ELMFIRE: https://github.com/lautenberger/elmfire (Dockerfile, tutorials/03-real-fuels); Cell2Fire: https://github.com/fire2a/C2F-W
- ZAFM-DW fuels: https://zenodo.org/records/21978709
- Gencat Equipaments: https://analisi.transparenciacatalunya.cat/resource/8gmd-gz7i.geojson; care homes `ivft-vegh`; campsites `t2h3-cgys`; schools `kvmv-ahh4`; plan activations `wj9c-j6vf`; INFOCAT municipal plans `eqag-gzjs`
- INFOCAT WMS: https://pcivil.icgc.cat/ogc/geoservei?map=/opt/idec/dades/pcivil/risc_incendis.map&service=WMS&version=1.3.0&request=GetCapabilities ; viewer https://pcivil.icgc.cat/pcivil/v2/index.html
- Pla Alfa: https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/Pla_Alfa_Municipal_Avui_FL_2_view/FeatureServer/0
- Bombers live actuacions: https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/ACTUACIONS_URGENTS_online_PRO_VW/FeatureServer/0
- Bombers historical perimeters KML: https://interior.gencat.cat/web/.content/home/serveis/bases_cartografiques/perimetres_cercador_incendis/pericat_qgis.kml ; Agricultura per-year SHP: http://www.gencat.cat/agricultura/sig/bases/incendisYY.zip ; PPP: https://www.gencat.cat/agricultura/sig/bases/perprot.zip
- Trànsit incidents: http://www.gencat.cat/transit/opendata/incidenciesRSS.xml and incidenciesGML.xml
- ICGC land cover: https://datacloud.icgc.cat/datacloud/cobertes-sol/gpkg/cobertes-sol-v1r0-2024.zip ; census sections https://datacloud.icgc.cat/datacloud/bseccen_etrs89/shp/
- Cadastre buildings ATOM: https://www.catastro.hacienda.gob.es/INSPIRE/buildings/ES.SDGC.bu.atom.xml
- INE sections 2025: https://www.ine.es/prodyser/cartografia/seccionado_2025.zip
- INFOCAT plan PDF: https://interior.gencat.cat/web/.content/home/030_arees_dactuacio/proteccio_civil/plans_de_proteccio_civil/plans_de_proteccio_civil_a_catalunya/02-plans-especials/infocat/document_pla_infocat.pdf
- Copernicus EMSR888: https://mapping.emergency.copernicus.eu/activations/EMSR888/
- Gavarres sources: https://ca.wikipedia.org/wiki/Incendi_de_les_Gavarres_de_2026 ; https://www.radiocapital.cat/incendi-a-la-bisbal-demporda/
- DIBA (Barcelona province only): water points https://incendis.diba.cat/fitxers/DadesObertes/json/PuntsAigua.json ; urbanisations https://incendis.diba.cat/fitxers/DadesObertes/shp/UrbanitzacionsNuclis_shp.zip
