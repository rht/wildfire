# FireLine contracts (v1.1, readme.md v4 MVP)

Binding module interfaces for the v4 MVP in `readme.md`. Section 5 of the readme defines the shared
location-assessment snapshot; this file restates it as code contracts and adds the module APIs on
both sides of it. `readme.md` owns scope; this file owns shapes and signatures. Extend, do not break.
The v0 engine (spread, routing, decisions, tiers) is kept behind feature flags (section 8) and its
old contract is preserved at the end for reference.

## Package layout

```
fireline/
  config.py      Policies and flags: VALUE_POLICY, EVACUATION_POLICY, CONTACT_POLICY, FRESHNESS, FEATURES (+ v0 params)
  forecast_input.py  Per-location fire arrival estimates: Deepfire fire-spread or a labelled recorded/synthetic file
  snapshot.py    Producer: build_snapshot(), asset_exposure(), validate_snapshot(), read/write
  fire_input.py  Fire updates: Deepfire poll or recorded responses -> FireUpdate; dedupe; data_status; latency
  priority.py    Consumer: SnapshotSequence guard, apply_overrides(), rank_snapshot(), queues (evacuation window)
  tasks.py       SQLite TaskStore: tasks, roster, confirmed overrides, snapshot bookkeeping, events
  agent.py       Four tools (get_asset, lookup_facility, propose_update, escalate) + investigate loop
  llm.py         AnthropicLLM and FakeLLM (same .create interface)
  app.py         Streamlit: map, ranked table, review queue, details + timing breakdown, tasks, change log
  feeds.py       HTTP/cache layer, Gencat registers, Open-Meteo, DeepfireClient (unchanged API)
  --- v0 modules, gated by config.FEATURES, not used by the v4 path by default ---
  grid.py spread.py exposure.py decide.py routing.py fire_state.py scenario.py
fixtures/
  assets.json               12 synthetic assets_in rows (v0 shape, still the synthetic scenario input)
  snapshots/                Committed v4 snapshots: synthetic_gavarres_0001.json, _0002.json (+ real-area ones)
  fire/                     Recorded provider responses used by fire_input.load_recorded()
  teams.json                Fixture roster (section 5)
  evidence.json             Cached facility pages / register rows for lookup_facility (section 6)
  real_area/                Cached Equipaments/schools extract for the fixed Gavarres area (section 2.4)
scripts/
  make_snapshots.py         Build fixture + real-area snapshots (no network)
  fetch_data.py             Real data into data/ (network)
  investigate.py            One agent investigation: live call if ANTHROPIC_API_KEY, else labelled prerecorded
tests/                      pytest, no network, no LLM
```

## 1. Conventions

- Times: ISO 8601 UTC strings in JSON (`2026-07-03T08:00:00+00:00`), tz-aware `datetime` in Python.
- Coordinates exchanged as WGS84 (`longitude`, `latitude`; GeoJSON is lon/lat order). Distances in
  metres computed in EPSG:25831 (`grid.lonlat_to_xy`).
- Unknown is `null`, never zero. Every key listed for a record is always present.
- Records are plain dicts; pandas only in the UI.
- Stable ids: `asset_id = f"{source}:{source_id}"` (e.g. `fixture:can_xic`, `equipaments:3620043`,
  `schools:17001234`). Coordinates are attributes, not identity.

## 2. Snapshot (readme 5) — producer `fireline/snapshot.py`

### 2.1 Envelope

```python
{
  "schema_version": "1.1",       # 1.0 files still load; their v1.1 keys read as null
  "scenario_id": str,          # e.g. "synthetic_gavarres", "gavarres_2026-07"
  "incident_id": str,          # provider incident/cluster id or fixture id
  "snapshot_id": str,          # f"{scenario_id}-{sequence:04d}"
  "sequence": int,             # monotonically increasing within scenario_id, starts at 1
  "as_of": str,                # information cutoff (UTC)
  "computed_at": str,          # calculation completion (UTC)
  "input_mode": "live" | "recorded" | "synthetic",
  "fire_observed_at": str | None,
  "fire_source": str | None,   # e.g. "deepfire:satellite-perimeters", "fixture:synthetic_ignition"
  "fire_geometry": GeoJSON geometry | None,   # Polygon/MultiPolygon footprint, or Point when only a hotspot centre
  "fire_geometry_kind": "perimeter" | "hotspot_centre" | "simulated" | None,   # a hotspot centre is never shown as a
                               # perimeter; "simulated" = a model burned area (fire-spread run), not an observed perimeter
  "data_status": "current" | "stale" | "unavailable",
  "metrics": {"source_age_s": float | None, "processing_s": float | None},   # readme 11 latency, separate numbers
  "assets": [ <asset record> ... ]           # complete matching set for the fixed area, stable across updates
}
```

### 2.2 Asset record (all keys present)

```python
{
  "asset_id": str, "name": str, "asset_type": str,     # asset_type in VALUE_POLICY["by_type"] or "unknown"
  "latitude": float | None, "longitude": float | None,  # both null when unresolved
  "geometry": GeoJSON | None, "area_m2": float | None,
  "capacity": int | None, "estimated_occupancy": int | None,
  "occupancy_basis": str | None,           # "register capacity as proxy", "register headcount", "analyst override", ...
  "value_score": float | None, "value_basis": str | None,   # from config.VALUE_POLICY; basis = policy version string
  "distance_to_fire_m": float | None, "intersects_fire": bool | None,   # min distance to fire_geometry; 0 on overlap
  "burn_probability": float | None,
  "arrival_p10_at": str | None, "arrival_p50_at": str | None,
  "forecast_horizon_at": str | None, "forecast_source": str | None,
  "fire_arrival_at": str | None, "fire_arrival_basis": str | None,   # v1.1: selected arrival estimate + its semantics
  "evacuation_min": float | None, "evacuation_source": str | None,   # v1.1: total evacuation duration (minutes) + basis
  "needs_review": bool, "review_reasons": [str],   # location_unknown, occupancy_unknown, occupancy_seasonal,
                                                   # class_ambiguous, value_unknown, exposure_unknown,
                                                   # forecast_unavailable, evacuation_unknown (v1.1)
  "sources": [ {"fields": [str], "source": str, "observed_at": str | None, "available_at": str | None,
                "fetched_at": str | None, "notes": str | None} ],
  "municipality": str | None,             # convenience, not in readme; may be null
}
```

Rules: `needs_review` is true iff `review_reasons` is non-empty. `exposure_unknown` when
`distance_to_fire_m` is null (no location or no fire geometry). `value_unknown` when `asset_type` is not
in the value policy. `estimated_occupancy` null with `capacity` set is allowed; the basis string then
says capacity is a proxy only where the producer chose to fill `estimated_occupancy` from it (it does
not by default). Forecast fields are filled by `forecast_input.attach_forecast` when `build_snapshot` is
given a `forecast=` (section 2.5), or by the CA raster when `config.FEATURES["forecast_enrichment"]` is on
(`forecast_source` then names the method, e.g. `"ca_ensemble (labelled enrichment, not validated)"`);
otherwise they are null.
Point fallback for distance is recorded in `sources` with `fields: ["distance_to_fire_m", "intersects_fire"]` and a note
`"point fallback: facility footprint missing"`.

v1.1 timing fields (readme 5.2 and 6). `fire_arrival_at` is the producer's selected spread-predicted
arrival for this location; `fire_arrival_basis` states its semantics (`"p10"` when the provider
supplies quantiles, otherwise the provider's single estimate name). It is never inferred from distance:
when no forecast covers the location it is null and `review_reasons` carries `forecast_unavailable`.
`forecast_source` and `forecast_horizon_at` are required provenance whenever `fire_arrival_at` is set,
and a `sources` entry lists the forecast fields. `evacuation_min` is the total estimated evacuation
duration (mobilisation + preparation/loading + movement to a receiving location) with
`evacuation_source` naming its basis: by default `config.EVACUATION_POLICY` by class (`"evacuation-proto-…"`,
a labelled prototype assumption with its component minutes and assumptions in the `sources` note), or
`"analyst override: …"` after confirmation. Unknown class -> null and `evacuation_unknown`.

### 2.3 API

```python
snapshot.asset_exposure(lon, lat, geometry, fire_geometry) -> (distance_m | None, intersects | None, note | None)
snapshot.build_snapshot(assets_in, fire, *, scenario_id, incident_id, sequence, as_of, input_mode,
                        computed_at=None, data_status=None, metrics=None, arrival=None, forecast=None,
                        cfg=config) -> dict
    # assets_in: v0 assets_in rows (asset_id, name, asset_class, lon, lat, municipality, occupancy, ...) or
    #            v4 records (asset_type, latitude, longitude, capacity...) — both accepted, v0 keys mapped.
    # fire: fire_input.FireUpdate dict or None (None -> fire fields null, data_status "unavailable").
    # arrival: optional spread.ArrivalRaster; used only when FEATURES["forecast_enrichment"].
    # forecast: optional forecast-input-1 dict (section 2.5); takes precedence over `arrival`.
snapshot.validate_snapshot(snap) -> list[str]     # [] when valid; messages otherwise
snapshot.write_snapshot(snap, path) / snapshot.read_snapshot(path) -> dict
```

Fixtures: `fixtures/snapshots/synthetic_gavarres_0001.json` (sequence 1, fire at 08:00) and
`synthetic_gavarres_0002.json` (sequence 2, fire advanced; the window order changes and some windows are
exhausted; one asset with `location_unknown`, one with `occupancy_unknown`, one with `class_ambiguous`,
one located asset without a forecast). Both `input_mode: "synthetic"` with the synthetic forecasts of
`fixtures/forecast/`. `gavarres_real_0001..0003.json` are the real facilities with real recorded July
perimeters and no forecast (every asset `forecast_unavailable`); `gavarres_real_0004.json` is the real
facilities with a real recorded Deepfire fire-spread run of 2026-09-19 seeded at the July incident
centroid (`fire_geometry_kind: "simulated"`), which covers no facility within its 12 h horizon.

### 2.5 Forecast input — `fireline/forecast_input.py`

Per-location fire arrival estimates in the `forecast-input-1` shape (module docstring has the full
format): `forecast_source`, `input_mode` (`synthetic` | `recorded` | `live`), `issued_at`,
`forecast_horizon_at`, `basis`, `note` and `estimates: {asset_id: {arrival_p10_at, arrival_p50_at,
burn_probability, arrival_at?}}`. Synthetic files must say "synthetic, not a provider forecast" in
their note.

```python
forecast_input.load_forecast(path) -> dict                      # validated; ValueError otherwise
forecast_input.attach_forecast(assets, forecast) -> None        # in place: forecast fields, fire_arrival_at/basis, sources entry;
                                                                # uncovered or unlocated assets get forecast_unavailable
forecast_input.deepfire_spread_to_forecast(body, received_at, asset_points, *, min_burn_probability=None,
                                           input_mode="recorded") -> dict
    # body: a COMPLETED /v1/fire-spread/simulations/{id} response (hourly cumulative burned-area polygons;
    # ensemble bands with burn_probability). asset_points: {asset_id: (lon, lat)}. arrival_at = createdAt +
    # elapsed_seconds of the first hour whose polygon (union of bands >= min_burn_probability, default 1/N)
    # covers the point; horizon = createdAt + durationHours. t0 = createdAt is an assumption (undocumented).
forecast_input.forecast_from_recorded_spread(path, asset_points, *, min_burn_probability=None) -> dict
```

Selection order for `fire_arrival_at`: p10 (`fire_arrival_basis` `"p10"`), then p50 (`"p50"`), then
`arrival_at` (basis = the forecast's `basis` string, e.g. the Deepfire isochrone-crossing label), else
null. Deepfire fire-spread runs are seeded from hotspots observed within the last `lookbackHours` of
the request time, so they exist only for current fires; the July incident cannot be re-run
(`fixtures/fire/deepfire/README.md`).

### 2.4 Real-area assets

`fixtures/real_area/assets_gavarres.json`: envelope `{area, bbox, extracted_on, registers, counts, assets}` whose
`assets` hold every `data/assets_in.json` row (Gencat Equipaments +
schools extract, 2026-09-19) inside `feeds.GAVARRES_BBOX`, in v4 asset-record shape with `sources`
carrying `fetched_at` = extraction time and `source` = the register. Unlocated care homes and
campsites from `data/unlocated.json` inside the area's municipalities are included with null
coordinates and `location_unknown`. `fixtures/real_area/README.md` records counts and the limitation
(care homes and campsites carry no coordinates in the registers).

## 3. Fire input — `fireline/fire_input.py`

```python
FireUpdate = {
  "provider": "deepfire" | "fixture",
  "incident_id": str,
  "observed_at": str | None,       # provider observation time
  "received_at": str,              # when we got the response (UTC)
  "geometry": GeoJSON | None,      # Polygon/MultiPolygon, or Point for a hotspot centre
  "geometry_kind": "perimeter" | "hotspot_centre" | None,
  "source": str,                   # "deepfire:satellite-perimeters", "deepfire:clusters", "fixture:..."
  "raw_ref": str | None,           # path of the cached raw response
  "notes": str,                    # e.g. "observed_at from observed_watermark"; informational
}

fire_input.parse_deepfire(body: dict, received_at: datetime, collection: str) -> FireUpdate | None
fire_input.poll_deepfire(client, bbox, as_of, *, incident_id=None) -> FireUpdate | None   # live path
fire_input.load_recorded(dir_or_files) -> list[FireUpdate]      # recorded responses through parse_deepfire
fire_input.UpdateCache().accept(update) -> bool                 # False for duplicate (incident_id, observed_at, geometry hash)
                                                                #   and for an update observed earlier than the latest accepted
fire_input.record_response(collection, body, received_at, out_dir) -> Path   # writes the recorded shape
fire_input.measure_update(update, started_at, finished_at, now=None) -> {"source_age_s", "processing_s"}
fire_input.data_status(observed_at, now, cfg=config) -> "current" | "stale" | "unavailable"
fire_input.source_age_s(observed_at, now) -> float | None
fire_input.Stopwatch() -> .start(); .stop() -> processing_s     # receipt-to-snapshot processing time
fire_input.synthetic_update(fire_state) -> FireUpdate           # v0 FireState -> FireUpdate (perimeter -> WGS84 GeoJSON)
```

Thresholds in `config.FRESHNESS = {"stale_after_s": 3600, "unavailable_after_s": 21600}`. Recorded
responses live in `fixtures/fire/deepfire/*.json` as `{"collection", "received_at", "body"}`.

## 4. Priority — consumer `fireline/priority.py`

Contact priority is the remaining evacuation window (readme 6), policy `config.CONTACT_POLICY`
(`forecast-evacuation-window-v2`). The weighted proximity/size/value score of v1.0 is removed.

```python
priority.SnapshotSequence().accept(snap) -> bool   # False on duplicate snapshot_id or sequence <= last for scenario_id
priority.apply_overrides(assets, overrides) -> list[dict]   # copies; overrides from tasks.TaskStore.overrides()
priority.rank_asset(asset, now_at, cfg=config) -> dict      # adds the coordination keys below (priority_rank stays None)
priority.rank_snapshot(snap, cfg=config, overrides=None, now_at=None) -> {"ranked": [...], "needs_review": [...], "flagged": [...], "all": [...], "now_at": str, "policy": {...}}
    # now_at defaults to snap["as_of"] (CONTACT_POLICY["now"]); flagged = ranked assets that still carry review reasons
priority.input_age(asset, now=None) -> {"oldest_observed_at", "newest_fetched_at", ...ages when now given}
```

Added keys per asset:

| Key | Type | Meaning |
|---|---|---|
| `priority_rank` | int or null | 1-based position in `ranked`; null in `needs_review`. |
| `queue` | `"ranked"` or `"needs_review"` | Ranked only when every timing input is known. |
| `priority_status` | `"window_open"`, `"window_exhausted"` or `"needs_review"` | Exhausted = `slack_min <= 0`: immediate analyst review, not an evacuation instruction. |
| `time_to_impact_min` | float or null | `fire_arrival_at - now_at` in minutes. |
| `latest_start_min` | float or null | `fire_arrival_at - evacuation_min - buffer_min - now_at` in minutes. |
| `slack_min` | float or null | Remaining window `latest_start_min - 0` (i.e. relative to `now_at`); negative allowed. |
| `window_components` | dict | `{"fire_arrival_at", "fire_arrival_basis", "forecast_source", "forecast_horizon_at", "now_at", "evacuation_min", "evacuation_source", "buffer_min", "distance_to_fire_m"}` as used. |
| `priority_policy_version` | str | `CONTACT_POLICY["version"]`. |
| `priority_reasons` | [str] | Human-readable explanation lines: each timing component, the window arithmetic, `"needs review: …"` when unranked, and `"review flag: <reason>"` for each producer review reason. |

Ranked order: `slack_min` ascending, then `fire_arrival_at` ascending, then `distance_to_fire_m`
ascending (null last), then `asset_id`. Missing `fire_arrival_at`, `evacuation_min`,
`forecast_source` or `evacuation_source` -> `queue = "needs_review"`, `priority_status =
"needs_review"`, timing keys null, and `forecast_unavailable` / `evacuation_unknown` added to
`priority_reasons` (and to `review_reasons` if the producer did not already set them). A missing
distance does not prevent ranking. Needs-review order is unchanged from v1.0: exposure unknown first,
then ascending known distance, then `asset_id`. Assets with review reasons but complete timing stay
in `ranked` and also carry their reasons (the UI shows them in both views).

The ordering and window arithmetic are the same as `contact_priority.rank_contacts` (section 16 of
the readme) with timestamps converted to minutes from `now_at`; a test asserts agreement.

`apply_overrides` behaviour from v1.0 is kept (occupancy/capacity, `asset_type` re-derives
`value_score`, `override_conflict`). v1.1 adds field `evacuation_min`: sets `evacuation_source =
"analyst override: <source>"` and clears `evacuation_unknown`. Overrides on `fire_arrival_at` are not
accepted (forecasts come from the producer).

## 5. Tasks and roster — `fireline/tasks.py` (SQLite)

Roster fixture `fixtures/teams.json`: `[{"team_id", "name", "capabilities": [str], "available": bool,
"source": "fixture"}]`. Capability tags: `occupancy_check`, `facility_contact`, `access_check`,
`resource_request`, `medical`, `transport`.

Actions (readme 7): `confirm_occupancy`, `contact_facility`, `check_access`, `request_resources`.
Default required capabilities: `{"confirm_occupancy": ["occupancy_check"], "contact_facility":
["facility_contact"], "check_access": ["access_check"], "request_resources": ["resource_request"]}`.

```python
class TaskStore:                       # tasks.TaskStore(path=":memory:" | Path)
    load_roster(path) / teams() -> list[dict]
    suggest_tasks(scored_assets, snapshot_id) -> list[task]   # from review reasons; dedupes open (asset_id, action, reason)
    create_task(asset_id, action, reason, *, required_capabilities=None, snapshot_id, notes="") -> task
    get(task_id) / tasks(asset_id=None, status=None) -> list[task]
    assign(task_id, team_id) -> task          # raises AssignmentError("team busy: ...") / ("capability mismatch: ...")
    release(task_id) -> task                  # unassign, back to open
    set_status(task_id, status, note="") -> task     # open|assigned|in_progress|blocked|done; done only by analyst
    add_question(task_id, question) / answer_question(task_id, index, answer)
    set_deadline(task_id, deadline_at, basis)
    confirm_override(asset_id, field, value, *, source, snippet, url=None, observed_at=None, confidence, proposal_id=None) -> override
    overrides(asset_id=None) -> list[override]      # {override_id, asset_id, field, value, previous, source, snippet, url, observed_at, confidence, confirmed_at}
    apply_snapshot(snap) -> {"accepted": bool, "affected_task_ids": [...], "missing_asset_ids": [...], "changed": [...]}
        # records snapshot_id; flags open tasks whose asset's distance/intersects/needs_review changed
        # ("affected_by_snapshot" = snapshot_id on the task); missing assets keep their tasks and get an event
    events(limit=None) -> list[{"at", "kind", "asset_id", "task_id", "message"}]     # the change log
```

Task record: `task_id, asset_id, action, reason, status, required_capabilities, assigned_team_id,
deadline_at, deadline_basis, blocking_questions (list of {question, answer}), evidence (list),
notes, created_at, updated_at, based_on_snapshot_id, affected_by_snapshot_id (nullable), suggested (bool)`.
A team with any task in `assigned|in_progress|blocked` is busy. `request_resources` may stay
unassigned and blocked.

## 6. Agent — `fireline/agent.py`

Four tools, each a plain function with a JSON schema in `TOOLS`:

```python
get_asset(asset_id, workbench) -> dict            # scored asset record (trimmed, numbers rounded) + open tasks + overrides
lookup_facility(query, municipality=None) -> list[dict]   # fixtures/evidence.json + fixtures/registers.json + data/registers/*.json
    # candidate: {evidence_id, name, municipality, register|url, capacity, snippet, observed_at, fetched_at, score, fields}
propose_update(asset_id, field, value, source, quoted_snippet, confidence, url=None, observed_at=None, workbench) -> proposal
    # field in ("estimated_occupancy", "capacity", "asset_type"); NOT applied; status "pending"
escalate(asset_id, question, options, default, workbench) -> question record (status "open"); nothing applied
```

`Workbench` (in agent.py): `assets: dict[asset_id -> scored asset]`, `tasks: TaskStore | None`,
`proposals: list`, `questions: list`, `change_log: list[str]`. `agent.investigate(workbench, asset_id,
llm=None, max_steps=6) -> record` with `tool_calls, final_text, proposals_added, questions_added,
postcheck_ok, llm_mode: "live"|"fake"|"prerecorded"`. Number post-check kept. Confirmation is the
analyst's: `tasks.TaskStore.confirm_override(..., proposal_id=)` then rescoring; `agent.confirm_proposal
(workbench, proposal_id)` does that and marks the proposal `confirmed`; `reject_proposal` marks it
`rejected`. The agent never assigns teams or changes policy.

Evidence cache `fixtures/evidence.json`: `[{"evidence_id", "name", "municipality", "url", "snippet",
"observed_at", "fetched_at", "capacity", "asset_type", "notes", "source", "register"?, "address"?}]`,
labelled manual enrichment (`source: "manual enrichment"`) or copied register rows (`source:
"gencat:<register>"`). Candidates from `lookup_facility` always carry both `register` and `url` (one
null) plus `source` and `asset_type`. `propose_update` raises `CapacityAsOccupancyError` (surfaced by
`dispatch` as an error with a hint) when `estimated_occupancy` is proposed from capacity-worded evidence.
`fixtures/agent/prerecorded_investigation.json` holds one real-model transcript (or a labelled fake if
no key was available when recorded) that `scripts/investigate.py` replays when no key is present.

## 7. UI — `fireline/app.py`

Reads `fixtures/snapshots/` (and `data/snapshots/` when present) ordered by `sequence`; a "next update"
control advances the sequence through `SnapshotSequence` + `TaskStore.apply_snapshot`. Persists to
`data/fireline.sqlite` (path from `FIRELINE_DB`). Shows: input mode, `as_of`, `computed_at`,
`data_status`, source age and processing time; map with fire geometry (perimeter vs hotspot centre
styled differently) and assets coloured by queue/remaining window; ranked table; needs-review queue; selected
asset details with the timing breakdown (arrival, evacuation duration, buffer, window), input age and sources; proposals awaiting confirmation; task
controls (create, assign with roster check, progress, block, release); change log from `events()`.

## 8. Feature flags — `config.FEATURES`

`{"spread_ca": False, "routing": False, "decisions": False, "forecast_enrichment": False}`. The v0
scenario pipeline (`scripts/precompute.py`, `scenario.Scenario`) remains runnable when the flags are on;
the v4 path never imports the raster stack unless `forecast_enrichment` is set. Forecast-driven
tiers are optional context only.

---

## Appendix: v0 contract (engine behind the flags)

Kept verbatim from CONTRACTS v0 for the gated modules. See git history (`13e0b74`) for the full text:
Grid, FireState, ArrivalRaster, v0 asset table (`asset_class, lon, lat, occupancy, burn_prob,
arrival_p10_min, tier, needs_review: list[str]`), decide, routing, Scenario, seven-tool agent. The v4
agent replaces the seven tools; `scenario.py` gains `to_snapshot()` delegating to `snapshot.build_snapshot`.
