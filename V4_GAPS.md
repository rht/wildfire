# Gaps between the first-draft code and the v4 MVP (readme.md)

Status on 2026-09-19, branches `claude/close-v4-gaps` and `claude/window-ranking`. The first draft (`13e0b74`) was built against
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
| `burn_probability`, `arrival_*_at`, `forecast_*` | Closed | filled from a `forecast-input-1` file or a Deepfire fire-spread run by `forecast_input.attach_forecast`; null with `forecast_unavailable` otherwise; CA enrichment only behind `FEATURES["forecast_enrichment"]` and labelled |
| `fire_arrival_at`, `fire_arrival_basis`, `evacuation_min`, `evacuation_source` (v1.1) | Closed | `snapshot.asset_record` + `forecast_input`; evacuation from `config.EVACUATION_POLICY` by class with component minutes and assumptions in `sources`; unknown class -> `evacuation_unknown` |
| `needs_review`, `review_reasons` | Closed | boolean plus reasons incl. `location_unknown`, `value_unknown`, `exposure_unknown` |
| `sources` | Closed | field-level provenance with observed/available/fetched times |
| Complete asset set | Closed | `fixtures/real_area/assets_gavarres.json`: 168 facilities (99 located, 69 unlocated) in the Gavarres bbox |

## 2. Priority and coordination (readme sections 6 and 7)

| Item | Status | Where |
|---|---|---|
| Contact priority by remaining evacuation window | Closed | `priority.rank_snapshot` / `rank_asset`, `config.CONTACT_POLICY` (now = snapshot `as_of`, buffer 30 min); shares arithmetic and ordering with `contact_priority.rank_contacts`; timing breakdown in the UI. The weighted proximity–size–value score and `PRIORITY_POLICY` are removed. |
| Needs-review queue ordering | Closed | exposure unknown first, then known distance; missing forecast or evacuation estimate keeps an asset here |
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
| Deepfire fire-spread | Partial | `forecast_input.deepfire_spread_to_forecast` verified on two real recorded runs (2026-09-19). Runs are seeded from current hotspots only: the July incident cannot be re-run (422), so the recorded July snapshots carry no forecast. `gavarres_real_0004` uses a real run seeded at the July centroid; it reaches no facility in 12 h. |
| CA spread ensemble | Deferred | `FEATURES["spread_ca"]`, `["forecast_enrichment"]` off |
| Routing, cut roads, destinations | Deferred | `FEATURES["routing"]` off |
| Confine / evacuate rule | Deferred | `FEATURES["decisions"]` off |
| Wind what-if | Deferred | only in the v0 `scripts/precompute.py` demo |
| Real facilities in scenarios | Closed | `fixtures/snapshots/gavarres_real_0001..0003.json` (real facilities, real recorded perimeters, `input_mode: recorded`) |

## 5. UI (readme section 9)

| Item | Status | Where |
|---|---|---|
| Timing breakdown panel | Closed | selected-asset arrival, evacuation duration, buffer, latest start, remaining window, reasons, input age, sources; analyst evacuation-duration override |
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

## Divergence from readme after merging `origin/main` (d0473c1) — resolved on `claude/window-ranking`

The merged readme section 6 (contact order by remaining evacuation window) is now what the snapshot
pipeline implements. Decisions taken on 2026-09-19: forecast from Deepfire fire-spread where a run
exists, else a labelled recorded/synthetic forecast file; evacuation duration from a class-based,
analyst-overridable prototype policy; "now" = snapshot `as_of` with a 30 min buffer; the weighted
score removed rather than kept behind a flag. `CONTRACTS.md` is v1.1; `scripts/validate.py` Priority
checks the window arithmetic; `VALIDATION.md` is regenerated.

## Still open after this pass

1. Run one live investigation (`scripts/investigate.py --record --asset fixture:pou_del_glac`) and
   replace the FakeLLM fixture; hold out examples before tuning the prompt.
2. Real-area occupancy: no located register row carries a capacity or headcount, so occupancy stays
   an investigation item even where a forecast exists. Disclosed in `VALIDATION.md`.
3. Install Superpowers for the next session.
4. Real-area ranking also needs a forecast: a live Deepfire fire-spread run on a current cluster in
   the bbox (or Deepfire's own `auto` runs, listed every few hours) fed through
   `forecast_input.deepfire_spread_to_forecast` would rank real facilities; only recorded runs seeded
   at the July centroid exist, and they reach no facility.
