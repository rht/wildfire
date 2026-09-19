# Gaps between the first-draft code and the v4 MVP (readme.md)

Status on 2026-09-19, branch `claude/close-v4-gaps`. The first draft (`13e0b74`) was built against
PLAN.md v3; this pass closes the v4 gaps listed below against `readme.md` and `CONTRACTS.md` v1.0.
Every row now carries a status. Recorded check outcomes are in `VALIDATION.md`
(`scripts/validate.py --write`).

Legend: **Closed** = implemented and tested. **Partial** = implemented, but a stated part could not
be done in this environment. **Deferred** = kept out of the default path on purpose, behind
`config.FEATURES`. **Open** = still to do.

## 1. Snapshot contract (readme section 5)

| Item | Status | Where |
|---|---|---|
| Snapshot envelope | Closed | `snapshot.build_snapshot`, `Scenario.to_snapshot`; fixtures `fixtures/snapshots/*.json` |
| Sequence and duplicate handling | Closed | `priority.SnapshotSequence`; persisted in `tasks.TaskStore.apply_snapshot` |
| `asset_type`, `latitude`, `longitude` | Closed | v0 rows mapped in `snapshot.asset_record` |
| `geometry`, `area_m2` | Closed | accepted when present; registers carry none, so null with point fallback labelled in `sources` |
| `capacity`, `estimated_occupancy`, `occupancy_basis` | Closed | register occupancy becomes capacity; allocated headcounts become estimates |
| `value_score`, `value_basis` | Closed | `config.VALUE_POLICY` (versioned prototype policy) |
| `distance_to_fire_m`, `intersects_fire` | Closed | shapely in EPSG:25831, overlap gives 0, fallback labelled |
| `burn_probability`, `arrival_*_at`, `forecast_*` | Closed | null by default; CA enrichment only behind `FEATURES["forecast_enrichment"]` and labelled |
| `needs_review`, `review_reasons` | Closed | boolean plus reasons incl. `location_unknown`, `value_unknown`, `exposure_unknown` |
| `sources` | Closed | field-level provenance with observed/available/fetched times |
| Complete asset set | Closed | `fixtures/real_area/assets_gavarres.json`: 168 facilities (99 located, 69 unlocated) in the Gavarres bbox |

## 2. Priority and coordination (readme sections 6 and 7)

| Item | Status | Where |
|---|---|---|
| Priority score | Closed, then superseded by `readme.md` | `priority.score_snapshot`, `config.PRIORITY_POLICY`, components shown in the UI. Implements the weighted proximity–size–value score of the pre-merge readme section 6; the merged readme (`origin/main` d0473c1) now specifies ranking by remaining evacuation window. See "Divergence from readme" below. |
| Needs-review queue ordering | Closed | exposure unknown first, then known distance |
| Tasks | Closed | `tasks.TaskStore`, four actions, task fields per readme 7, suggestions deduplicated |
| Team roster and availability checks | Closed | `fixtures/teams.json`; busy, capability and availability checks with explanations |
| Persistence | Closed | SQLite (`data/fireline.sqlite`, `FIRELINE_DB`), separate from snapshots |
| Update flow | Closed | "Next update" in the UI; priorities refresh, tasks preserved, affected tasks flagged, missing assets flagged |
| Tier column | Deferred | v0 tiers stay in `exposure.py` behind the flags; not shown in the v4 UI |

## 3. Agent (readme section 8)

| Item | Status | Where |
|---|---|---|
| Tool set | Closed | `get_asset, lookup_facility, propose_update, escalate` in `agent.py` |
| Autonomy | Closed | every update is a pending proposal; `confirm_proposal` persists it as an analyst override |
| Evidence cache | Closed | `fixtures/evidence.json` (labelled manual enrichment plus copied register rows) |
| Live LLM demo | Partial | `scripts/investigate.py` runs live with `ANTHROPIC_API_KEY`; no key was available, so the committed `fixtures/agent/prerecorded_investigation.json` is a labelled FakeLLM record. Rerun with `--record` and a key. |

## 4. Fire input and exposure (readme sections 3 and 4)

| Item | Status | Where |
|---|---|---|
| Deepfire polling | Closed | `fire_input.poll_deepfire` and `load_recorded` share one parse path. Live auth verified on 2026-09-19 with the `.env` credentials (`fireline/env.py`); the real responses are recorded under `fixtures/fire/deepfire/real/`. Continuous polling is not run in the demo; the UI steps through recorded snapshots. |
| Fire footprint | Closed | GeoJSON `fire_geometry` with `fire_observed_at`, `fire_source`, `fire_geometry_kind`; a cluster is always a hotspot centre |
| Stale-data status | Closed | `fire_input.data_status`, `config.FRESHNESS`; shown recorded and recomputed in the UI |
| Latency measurement | Closed | `fire_input.measure_update`; source age and processing time reported separately (VALIDATION.md) |
| CA spread ensemble | Deferred | `FEATURES["spread_ca"]`, `["forecast_enrichment"]` off |
| Routing, cut roads, destinations | Deferred | `FEATURES["routing"]` off |
| Confine / evacuate rule | Deferred | `FEATURES["decisions"]` off |
| Wind what-if | Deferred | only in the v0 `scripts/precompute.py` demo |
| Real facilities in scenarios | Closed | `fixtures/snapshots/gavarres_real_0001..0003.json` (real facilities, real recorded perimeters, `input_mode: recorded`) |

## 5. UI (readme section 9)

| Item | Status | Where |
|---|---|---|
| Score breakdown panel | Closed | selected-asset components, reasons, input age, sources |
| Team and task controls | Closed | create, assign with roster checks, progress, block, deadline, release |
| Input mode and timestamps | Closed | sidebar: mode, `as_of`, `computed_at`, status badge, source age, processing time |
| Map, ranked table, review queue, change log | Closed | `fireline/app.py`, logic in `fireline/ui_state.py` |

## 6. Process (AGENTS.md)

| Item | Status | Where |
|---|---|---|
| Branching | Closed | this work is on `claude/close-v4-gaps` in `.worktrees/close-v4-gaps`, pushed with upstream tracking |
| Superpowers workflow | Open | not installed in this session either; the brainstorm, plan, verify sequence was followed manually |
| Reference document | Closed | `readme.md` owns scope; `CONTRACTS.md` v1.0 restates its section 5; AGENTS.md points at both |
| Two readmes | Closed | `QUICKSTART.md` merged into `readme.md` section 0 |

## Divergence from readme after merging `origin/main` (d0473c1, 2026-09-19)

Marked here rather than silently reconciled. `readme.md` owns scope; the code on this branch has not
been changed to match the new scope yet.

| readme (main) now says | Code on this branch still does |
|---|---|
| Section 6: contact order by `remaining_window = (predicted_fire_arrival - total_evacuation_duration - buffer) - now`, smallest first; a forecast is **required**; missing forecast/evacuation duration -> unranked review; "this replaces the weighted contact score". | `priority.score_snapshot` ranks by `w_proximity * proximity + w_size * size + w_value * value` (`config.PRIORITY_POLICY`), review queue when a component is unknown. |
| Section 5: asset keys `fire_arrival_at`, `fire_arrival_basis`, `evacuation_min`, `evacuation_source`; `forecast_horizon_at`/`forecast_source` are required provenance for ranking. | `CONTRACTS.md` v1.0 and `snapshot.asset_record` carry none of these four keys; forecast fields are null unless `FEATURES["forecast_enrichment"]` is on. |
| Sections 8–9, 11–12: "recalculate the remaining evacuation window", "timing breakdown", validate "forecast arrival minus elapsed time". | Overrides recalculate the score; the UI shows the score breakdown; `scripts/validate.py` / `VALIDATION.md` "Priority" checks weight normalisation and monotonicity. |
| Section 16: `forecast-evacuation-window-v2` is implemented in `fireline/contact_priority.py` (`rank_contacts`, `ContactPolicy`) over `priority_models.Location` in minutes from a scenario epoch. | That module is a standalone API/CLI prototype (`scripts/static_priorities.py`, `fixtures/static_priority.json`); it is not wired to snapshots, `tasks.py`, `agent.py` or `app.py`, as section 16 itself states. |

## Still open after this pass

1. Run one live investigation (`scripts/investigate.py --record --asset fixture:pou_del_glac`) and
   replace the FakeLLM fixture; hold out examples before tuning the prompt.
2. Real-area ranking is empty: no located register row carries a capacity, so every real asset sits
   in the review queue until capacities are sourced or confirmed. Disclosed in `VALIDATION.md`.
3. Install Superpowers for the next session.
4. Bring the snapshot pipeline onto the merged readme section 6: extend the contract (v1.1) with
   `fire_arrival_at`, `fire_arrival_basis`, `evacuation_min`, `evacuation_source` and their sources;
   port `contact_priority.rank_contacts` onto snapshot assets (timestamps converted to one epoch);
   replace the weighted ranking in `priority.score_snapshot`, the UI breakdown and the `validate.py`
   Priority check; keep the review queue for missing forecast or evacuation estimates.
