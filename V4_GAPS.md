# Gaps between the first-draft code and the v4 MVP (readme.md)

Status on 2026-09-19 after the first draft was pushed to `main`. The code was built against
`CONTRACTS.md`, which follows PLAN.md v3. `readme.md` (v4) narrows the scope and defines a different
snapshot contract. This file lists what still differs, so the next pass can close it deliberately.

Legend: **Missing** = v4 needs it and the code has nothing. **Rename** = same concept, different
field name or shape. **Extra** = the code has it, v4 defers it. **Policy** = a decision to make.

## 1. Snapshot contract (readme section 5, PLAN 6.3.1)

| Item | Kind | Code today | v4 wants |
|---|---|---|---|
| Snapshot envelope | Missing | `Scenario.to_json` writes `{id, cluster_id, t, assets, queue, change_log}` | `schema_version, scenario_id, incident_id, snapshot_id, sequence, as_of, computed_at, input_mode, fire_observed_at, fire_source, fire_geometry, data_status, assets` |
| Sequence and duplicate handling | Missing | none | consumer ignores duplicate `snapshot_id` and lower/equal `sequence` |
| `asset_type` | Rename | `asset_class` | `asset_type`, `unknown` when unresolved |
| `latitude`, `longitude` | Rename | `lat`, `lon` | full names, both null when unresolved |
| `geometry`, `area_m2` | Missing | points only | GeoJSON footprint or null |
| `capacity`, `estimated_occupancy`, `occupancy_basis` | Rename | single `occupancy` plus `occupancy_source` | capacity and estimate kept apart; basis string |
| `value_score`, `value_basis` | Missing | none | class-based operational importance, versioned policy |
| `distance_to_fire_m`, `intersects_fire` | Missing | none; exposure is raster-based | minimum distance to the fire footprint, zero on overlap, point fallback labelled |
| `burn_probability` | Rename | `burn_prob` from the CA | nullable provider estimate; the CA is not a provider in v4 |
| `arrival_p10_at`, `arrival_p50_at` | Rename | `arrival_p10_min`, `arrival_p50_min` (minutes after t) | absolute timestamps or null |
| `forecast_horizon_at`, `forecast_source` | Rename | `ArrivalRaster.horizon_min`, `source` on the raster, not the asset | per-asset fields |
| `needs_review`, `review_reasons` | Rename | `needs_review` is a list of codes | boolean plus `review_reasons` array; adds `location_unknown`, `value_unknown`, `exposure_unknown` |
| `sources` | Missing | none | field-level provenance: `fields, source, observed_at, available_at, fetched_at, notes` |
| Complete asset set | Policy | fixtures are 12 hand-picked assets | every matching facility in the fixed area, kept across updates |

Closing this is one function, `Scenario.to_snapshot()`, plus a fixture pair of snapshots.

## 2. Priority and coordination (readme sections 6 and 7)

| Item | Kind | Code today | v4 wants |
|---|---|---|---|
| Priority score | Missing | tiers from lead-adjusted p10 and burn probability | `w_proximity * proximity + w_size * size + w_value * value`, normalised, versioned policy, shown in the UI |
| Needs-review queue ordering | Missing | escalation queue only | unscored assets in a visible queue ordered by known proximity, unknown exposure first |
| Tasks | Missing | none | four actions (confirm occupancy, contact facility, check access, request resources) with the task fields in readme 7 |
| Team roster and availability checks | Missing | none | fixture roster with capabilities; one active assignment per team; reject mismatches |
| Persistence | Missing | scenario JSON on disk, escalation answers only in Streamlit session state | SQLite for tasks and confirmed overrides, separate from exposure snapshots |
| Update flow | Missing | three precomputed scenarios and a `diff` | "next update" control; new snapshot refreshes priority, preserves tasks, flags affected work |
| Tier column | Extra | `act_now / prepare / monitor` | forecast-driven tiers are deferred; keep as optional context |

## 3. Agent (readme section 8)

| Item | Kind | Code today | v4 wants |
|---|---|---|---|
| Tool set | Rename | seven tools: `get_assets, get_decision, get_route, lookup_facility, sample_raster, add_override, escalate` | four: `get_asset, lookup_facility, propose_update, escalate` |
| Autonomy | Policy | agent applies pessimistic overrides itself (PLAN 6.6) | every field update needs analyst confirmation; `propose_update` instead of `add_override` |
| Evidence cache | Partial | `fixtures/registers.json` and `data/registers/*.json` | also cached facility pages with URLs, snippets and dates |
| Live LLM demo | Missing | `FakeLLM` by default; `AnthropicLLM` exists but has not been run | one real investigation call, prerecorded fallback labelled |

## 4. Fire input and exposure (readme sections 3 and 4)

| Item | Kind | Code today | v4 wants |
|---|---|---|---|
| Deepfire polling | Partial | client written to the documented schema, never run (no token) | verified auth and one usable response at kickoff; recorded responses through the same path |
| Fire footprint | Rename | shapely perimeter in EPSG:25831 inside `FireState` | GeoJSON `fire_geometry` in the snapshot with `fire_observed_at` and `fire_source`; a hotspot centre is never presented as a surveyed perimeter |
| Stale-data status | Missing | none | `data_status` current / stale / unavailable with configured thresholds |
| Latency measurement | Missing | none | source age and receipt-to-queue processing time reported separately |
| CA spread ensemble | Extra | `spread.run_ca`, calibrated to about 1.8 km/h downwind | "no custom simulator fallback"; keep off by default as labelled enrichment feeding nullable fields |
| Routing, cut roads, destinations | Extra | `routing.py`, fixture road graph | deferred |
| Confine / evacuate rule | Extra | `decide.py` | deferred; 6.3.2 keeps `recommendation` nullable, so keep the code but do not populate by default |
| Wind what-if | Extra | `whatif_east_1000` scenario | deferred |
| Real facilities in scenarios | Partial | `make fetch` writes `data/assets_in.json` (387 located) but scenarios use fixtures | one cached Equipaments extract for the area, all matching facilities |

## 5. UI (readme section 9)

| Item | Kind | Code today | v4 wants |
|---|---|---|---|
| Score breakdown panel | Missing | decision checks shown | per-asset score components and input age |
| Team and task controls | Missing | escalation buttons only | assign, progress, block, with roster checks |
| Input mode and timestamps | Missing | scenario name only | mode label, `as_of`, `computed_at`, stale state |
| Map, ranked table, review queue, change log | Present | present | present |

## 6. Process (AGENTS.md)

| Item | Kind | Code today | v4 wants |
|---|---|---|---|
| Branching | Policy | first draft pushed straight to `main` at the owner's request | task branches in `.worktrees/`, pushed with upstream tracking |
| Superpowers workflow | Missing | not installed in the session that built this | brainstorm, plan, debug and verify skills |
| Two documents claim to be the reference | Policy | `CONTRACTS.md` follows PLAN.md v3; `readme.md` v4 says it supersedes PLAN.md; `AGENTS.md` points at PLAN.md | pick one; the suggestion is `readme.md` for scope and `CONTRACTS.md` updated to its section 5 |
| Two readmes | Rename | the first draft's quickstart was renamed to `QUICKSTART.md` to avoid a case collision with `readme.md` | one entry point |

## Suggested order to close the gaps

1. `Scenario.to_snapshot()` with the envelope, renames, `distance_to_fire_m` and `sources`; commit two fixture snapshots.
2. Priority score and needs-review ordering as a new `priority.py`, policy in `config.py`.
3. Tasks and roster in SQLite, `tasks.py`, with the four actions and availability check.
4. Rename the agent tools to the four in v4 and switch `add_override` to `propose_update` pending analyst confirmation.
5. Wire `data/assets_in.json` into a real-area scenario; add `data_status` and latency measurement to `feeds.py`.
6. Gate spread, routing and decisions behind a config flag, off by default.
