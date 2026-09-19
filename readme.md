# FireLine — Practical Values-at-Risk MVP (v4)

Track 4, "Values at risk": Norrsken x Deepfire "AI for Wildfire" challenge, Hackbarna 2026.

**One incident, one bounded area, one analyst workflow:** identify priority locations, assign follow-up tasks, and update the queue as the fire changes.

This README is the current MVP scope and interface reference. Maintain project updates here; it supersedes the broader scope in `PLAN.md`.

The colleague builds **risk assessment**, which consumes fire updates, discovers facilities and emits location assessments. [@mirrdj](https://github.com/mirrdj) builds **analyst coordination**, which ranks those assessments and turns them into tasks, assignments and questions for the fire analyst. The shared contract is in section 5.

Use [Superpowers](https://github.com/obra/superpowers) for development. Work in a dedicated branch and worktree under this repository's `.worktrees/` directory, publish every task branch to `origin`, and push progress so colleagues can review it. See [AGENTS.md](AGENTS.md) for the persistent workflow.

## 0. Quickstart and repository layout

```sh
make setup        # uv venv + uv pip install -e ".[dev]"
make test         # pytest, no network, no LLM key
make snapshots    # rebuild fixtures/snapshots/ (synthetic fire; real-area facilities) with scripts/make_snapshots.py
make demo         # streamlit run fireline/app.py: map, ranked table, review queue, tasks, change log
make investigate  # one agent investigation; live with ANTHROPIC_API_KEY, else a labelled prerecorded replay
make fetch        # pull real Gencat registers and Open-Meteo wind into data/ (network)
make precompute   # v0 engine demo (spread CA, routing, decisions); labelled enrichment behind config.FEATURES
```

`CONTRACTS.md` holds the module APIs that implement section 5 and the coordination side. `fireline/`
has `snapshot.py` (producer), `fire_input.py` (Deepfire poll or recorded responses, stale status,
latency), `priority.py` and `tasks.py` (consumer: scoring, SQLite tasks, roster, confirmed overrides),
`agent.py` and `llm.py` (four-tool investigation), `app.py` (Streamlit) and `config.py` (policies and
feature flags). The v0 engine (`spread.py`, `routing.py`, `decide.py`, `scenario.py`, `grid.py`,
`fire_state.py`) stays in the tree behind `config.FEATURES`, off by default.

What is real and what is synthetic: the facilities in `fixtures/real_area/` are a real Gencat
Equipaments and schools extract for the Gavarres area (2026-09-19); care homes and campsites carry no
coordinates in their registers and are listed with `location_unknown`. The fire in every committed
snapshot and recorded provider response is **synthetic** (built to the Deepfire schema; live
authentication has not been verified because no token was available). `fixtures/evidence.json` is
labelled manual enrichment. No located register row carries a capacity, so the real-area ranked
table is empty until capacities are sourced or confirmed; every real asset sits in the review queue.
Tasks and overrides persist in `data/fireline.sqlite` (`FIRELINE_DB`). Recorded check outcomes are in
`VALIDATION.md` (`scripts/validate.py --write`).

## 1. Product and first milestone

"A fire update arrives. These locations need attention first, here is why, and here is what each team needs to investigate or confirm."

The analyst sees ranked facilities, investigates missing information, assigns follow-up work, and sees what changed on the next update. This supports decisions about facilities that may need evacuation by surfacing exposure, occupancy, access concerns and resource needs. The MVP does not calculate evacuation orders or safe routes.

**First milestone: the entire loop works against a fixture snapshot.** A second snapshot changes the ranking while preserving an existing assignment. Connecting real data must not block coordination development.

The [challenge brief](https://github.com/rht/wildfire/blob/main/challenge.md) emphasises real data, results in or near real time, and clear operator use. Demonstrate these through one real incident feed, measured update processing, and an analyst completing a concrete workflow. The agent investigates missing facility information and escalates what it cannot establish.

## 2. Scope

| Area | MVP commitment |
|---|---|
| Incident | One selected fire and a fixed bounding box, chosen based on accessible real data. |
| Facilities | Two or three classes from one cached dataset. Start with schools and care homes if coverage permits; add campsites only if readily available. Include every matching facility in the area. |
| Fire input | One provider, Deepfire, polled for updates. Recorded responses feed the same pipeline. |
| Exposure | Distance to the fire footprint and overlap, with source and age visible. Provided forecasts are optional enrichment. |
| Ranking | Explainable proximity–size–value score; missing information remains visible. |
| Coordination | Follow-up tasks, manual team assignment, capability/availability checks, status and blocking questions. |
| Agent | Investigate unknown occupancy or ambiguous class against cached evidence; propose a sourced update or escalate. |
| Interface | Map, ranked location table, selected-location details, task queue and change log. |
| Validation | Matching, geometry, scoring, missing-data behaviour, update latency and task persistence. |

Deferred from day one: custom spread models, fuel/elevation rasters, wind what-ifs, multiple satellite feeds, Catalonia-wide discovery, road graphs, route calculation, road-cut forecasts, automatic confine/evacuate rules, receiving-centre optimisation, population allocation, all-building discovery, automatic team scheduling, free-form chat and multilingual alerts.

## 3. Architecture and update flow

```text
Deepfire poll OR recorded provider responses
                      |
                      v
             Current fire snapshot       Cached facility dataset
                      |                           |
                      +-------------+-------------+
                                    v
                    RISK ASSESSMENT — colleague
                    Find facilities in the area
                    Attach size/value information
                    Calculate exposure and data gaps
                                    |
                        Shared location snapshot
                              (section 5)
                                    |
                                    v
                    ANALYST COORDINATION — @mirrdj
                    Calculate explainable priorities
                    Create/update follow-up tasks
                    Preserve analyst assignments
                         |                    |
                 Team roster          Focused agent triage
                 Analyst input        Evidence or question
                         +---------+----------+
                                   v
                         Map and analyst queue
```

Use one processing path for live and recorded inputs. The initial stream is periodic API polling; no message broker is needed. Cache the latest valid response, deduplicate updates and display stale-data status when polling fails. Honour provider rate limits and retry guidance.

Keep all matching facilities in the fixed area across updates, including those whose exposure decreases. Missing measurements must not look like a location becoming safe. Use stable asset IDs. Updating exposure must not erase analyst decisions, evidence or task history.

Use a sorted asset table for ranking. A graph is deferred until route connectivity or shared exits are actually calculated.

## 4. Minimal data preparation

| Need | Source and treatment |
|---|---|
| Fire footprint | Deepfire for one incident. Cache original responses and timestamps. Confirm authentication and one usable response at kickoff. |
| Facility location/class | One Gencat Equipaments extract for the selected area. Cache it, record extraction time and preserve source IDs. |
| Size | Estimated people present where sourced. Capacity may be a clearly labelled proxy; it is not a confirmed headcount. Missing values remain null. |
| Value | Analyst-configured operational importance by facility class: a prototype policy, not monetary valuation or an established emergency-service rule. |
| Investigation evidence | Small cache of registry records or facility pages with URLs, snippets and dates. Label manual enrichment; avoid several ingestion pipelines. |
| Teams | Manually entered or fixture roster with team IDs, capabilities, availability and source labels. |
| Optional forecast | Provided Deepfire forecast only if access and output meaning are confirmed early. No custom simulator fallback. |

Select classes after inspecting coverage. Include the complete matching set within the area instead of selecting only interesting facilities. If a desired class lacks usable coverage, select another covered class and disclose the limitation.

Calculate minimum distance between asset and fire footprints; overlap gives zero. Fall back to the representative asset point and label the approximation when its footprint is missing. Use an appropriate metric CRS (EPSG:25831 for the selected Catalan area), while exchanging WGS84 coordinates. A hotspot centre without a usable footprint is not silently treated as a surveyed fire perimeter.

Distance-based exposure is not burn probability or time to impact. Keep unsupported forecast fields null and display "forecast unavailable". Do not infer arrival quantiles or probability from distance.

Recorded responses demonstrate update handling. Only claim historical as-of replay when availability times for all inputs, including facility evidence, are established. Otherwise label it a recorded-input demo; do not claim lead time against historical evacuation decisions.

## 5. Shared location assessment contract

One record represents **one facility in one scenario snapshot**. Coordinates are attributes, not identity. The colleague owns the producer; @mirrdj owns its consumer. Both develop against the same fixture from the first milestone.

### 5.1 Snapshot envelope

| Field | Type and meaning |
|---|---|
| `schema_version` | String; start with `1.0` for this MVP contract. |
| `scenario_id`, `incident_id` | Strings identifying the scenario and source incident. |
| `snapshot_id`, `sequence` | Unique snapshot string and monotonically increasing integer within the scenario. |
| `as_of`, `computed_at` | UTC timestamps: information cutoff and calculation completion. |
| `input_mode` | `live`, `recorded` or `synthetic`; fixtures are explicitly synthetic. |
| `fire_observed_at`, `fire_source` | Nullable observation timestamp and source identifier. |
| `fire_geometry` | Nullable GeoJSON footprint for display and distance calculation. |
| `data_status` | `current`, `stale` or `unavailable`, with freshness thresholds in configuration. |
| `assets` | Complete array of matching facilities in the scenario's fixed area. |

Ignore duplicate snapshot IDs and lower/equal sequences within a scenario. Preserve snapshots for debugging and replay. A complete snapshot replaces exposure data, not the separate task store. Unexpectedly absent facilities trigger review, not task closure or an all-clear.

### 5.2 Per-location fields

All keys are present. Unknown measurements are `null`, not zero. Times are ISO 8601 UTC; distances are metres; normalised scores and probabilities are in [0, 1]. GeoJSON coordinates use longitude, latitude order.

| Fields | Type | Meaning |
|---|---|---|
| `asset_id`, `name`, `asset_type` | Strings | Stable identity, name and class; `unknown` for unresolved class. |
| `latitude`, `longitude` | Numbers or null | WGS84 representative point; both null when unresolved. |
| `geometry` | GeoJSON or null | Facility footprint when available. |
| `area_m2` | Nonnegative number or null | Optional footprint area; not the MVP size-score input. |
| `capacity`, `estimated_occupancy` | Nonnegative integers or null | Maximum people versus estimated people present. |
| `occupancy_basis` | String or null | Evidence or estimation method; capacity used as a proxy is explicit. |
| `value_score`, `value_basis` | Number or null; string or null | Class-based operational importance and versioned analyst policy. |
| `distance_to_fire_m`, `intersects_fire` | Nonnegative number or null; boolean or null | Geometric exposure; record point/footprint approximation in provenance. |
| `burn_probability` | Number or null | Optional provider estimate over its documented horizon; null when unsupported. |
| `arrival_p10_at`, `arrival_p50_at` | Timestamps or null | Optional arrival quantiles only when supported by the provider. Null does not establish safety. |
| `forecast_horizon_at`, `forecast_source` | Timestamp or null; string or null | Optional forecast horizon and source/method. |
| `needs_review`, `review_reasons` | Boolean; array of strings | Such as `location_unknown`, `occupancy_unknown`, `occupancy_seasonal`, `class_ambiguous`, `value_unknown`, `exposure_unknown`. |
| `sources` | Array of objects | Field-level provenance: `fields`, `source`, `observed_at`, `available_at`, `fetched_at`, `notes`. Unknown source times remain null. |

For historical replay, evidence must have been available by `as_of`; missing availability information cannot establish that. Document any forecast-to-asset aggregation method. Optional forecast enrichment must not block the distance-based pipeline.

## 6. Explainable priority calculation

Risk assessment describes the facility and its exposure. Coordination calculates priority for **analyst attention**.

```text
priority_score = w_proximity * proximity_score
               + w_size      * size_score
               + w_value     * value_score
```

- **Proximity:** higher when distance to the fire footprint is smaller.
- **Size:** estimated people present, or capacity as a labelled proxy when no estimate exists.
- **Value:** operational importance from the analyst-configured class table, distinct from people count to avoid double-counting size.
- Normalise components to [0, 1] with fixed configured scales; nonnegative weights sum to one. Record the policy version. Freeze a prototype policy at kickoff and expose it in the UI; do not tune weights to manufacture a desired demo order.
- Sort fully scored assets by descending score, then `asset_id` for stable ties. Recalculate when inputs or confirmed overrides change.
- If a required component is unknown, leave the score null and put the asset in a visible **needs-review queue**, ordered by known proximity with unknown exposure first. This is an investigation queue, not an assertion of highest physical risk.
- Show contributions and input age. Recalculation never makes stale source data fresh.

Coordination adds `priority_score`, nullable `priority_rank` (within scored assets), `queue` (primary placement: `ranked` or `needs_review`), `score_components`, `priority_policy_version`, and `priority_reasons`. Each component records its value, weight, input and proxy. Assets with review flags also appear in the review view even if a proxy permits scoring. Optional forecasts appear as context; forecast-driven urgency tiers are deferred.

## 7. Analyst tasks and team coordination

Use four actions: **confirm occupancy**, **contact facility**, **check access**, and **request resources**. Access is an analyst-entered concern or question, not a computed safe route. Destinations may be analyst notes, without automated suitability or route calculations.

Suggest tasks from data gaps and let the analyst add follow-up work for any asset. Deduplicate open suggestions by asset and action/reason across snapshots. Only an analyst reopens a completed task; materially changed evidence can raise a new review flag.

| Task fields | Meaning |
|---|---|
| `task_id`, `asset_id`, `action`, `reason` | Stable work item, facility, concrete action and explanation. |
| `status` | `open`, `assigned`, `in_progress`, `blocked` or `done`. |
| `required_capabilities`, `assigned_team_id` | Capability tags and nullable team assignment; the analyst selects the team. |
| `deadline_at`, `deadline_basis` | Nullable analyst-entered target time and rationale. Distance alone never generates a deadline. |
| `blocking_questions`, `evidence`, `notes` | Outstanding information, evidence references and analyst notes. |
| `created_at`, `updated_at`, `based_on_snapshot_id` | Audit context and latest exposure review. |

A team with an active assignment is unavailable for another in the MVP. Reject unavailable or capability-mismatched selections with an explanation; do not infer capability from names. A blocked assigned task keeps the team reserved until the analyst releases or reassigns it. Resource requests may remain unassigned and blocked. This is an availability check, not travel-time or multi-team scheduling.

Persist tasks and confirmed overrides separately from incoming exposure. New snapshots refresh priority and flag affected tasks without changing owners or status. Preserve tasks for missing assets and show the missing assessment. Keep override provenance and surface conflicts with new provider data.

## 8. Focused agent workflow

Implement one bounded loop for unknown occupancy or ambiguous class:

1. Read the selected asset and its review reasons.
2. Look up evidence in cached registry/page records.
3. Propose a sourced field update or produce a concrete question for the analyst.
4. On analyst confirmation, persist the override, recalculate priority and update the existing task.

Four tools suffice: `get_asset`, `lookup_facility`, `propose_update`, and `escalate`. Keep evidence and tool calls visible, cap investigation steps, and leave unsupported questions unresolved. All field updates require analyst confirmation in this MVP. The agent does not directly assign teams or change scoring policy; calculation stays in code.

No general chat, live web research, alert drafting or wind what-if tools are needed. An LLM failure leaves the question answerable manually. Demonstrate one actual investigation call; label any prerecorded fallback output.

## 9. Interface and implementation

One screen contains the fire/facility map, ranked table, visible review queue, selected-facility evidence and score breakdown, team/task controls, and change log. Show input mode, timestamps and stale-data state. Live and recorded inputs use the same screen; a "next update" control suffices for the demo.

Use Python for ingestion/calculations, GeoPandas/Shapely for geometry, Streamlit with one map component, and an LLM API with tool calling. Cache responses and snapshots as JSON/GeoJSON; use SQLite for tasks and analyst overrides. The core MVP needs no road-network library, raster-processing stack, message broker or custom simulation service.

## 10. Two-person ownership and build sequence

| Owner | Responsibility | Deliverable |
|---|---|---|
| Colleague — risk assessment | One provider adapter, incident/area selection, cached facilities, matching, geometric exposure, provenance and optional provided forecast. | Section 5 snapshots and ingestion/exposure checks. |
| [@mirrdj](https://github.com/mirrdj) — analyst coordination | Scoring, persistent tasks, roster checks, manual assignment, agent investigation, UI and change log. | Ranked/review queues and the analyst workflow against fixtures and real snapshots. |
| Both | Contract/policy agreement, integration, labelled validation examples and rehearsal. | Complete demo, measured checks and explicit limitations. |

Assume about 24 working hours for two people, subject to the event's actual duration. Cut optional enrichment before extending the core workflow.

| Time | Colleague | @mirrdj | Milestone |
|---|---|---|---|
| 0–2 h | Verify provider response and facility coverage; agree contract. | Shared fixture, roster and UI skeleton; agree policy. | Two snapshots and one missing-information case agreed. |
| 2–6 h | Cached facility loading and fire-response adapter. | Fixture ranking, tasks, assignment and persistence. | **Entire fixture loop works.** |
| 6–12 h | Real snapshots; check matches and distances. | Connect snapshots; evidence lookup and confirmation. | Real data and one agent investigation work. |
| 12–18 h | Poll/record updates; stale/missing-data handling; forecast only if already accessible. | Assignment checks, change log and error handling. | Workflow survives update, missing data and reload. |
| 18–22 h | Validate exposure and source timing. | Validate scoring, agent cases and task persistence. | Results recorded; feature freeze. |
| 22–24 h | Rehearse together. | Rehearse together. | Two dry runs and backup recording. |

If behind: drop forecast enrichment, then the third class, then visual polish. Retain real data, the update loop, one agent investigation, manual assignment and validation. If live polling is unavailable, use recorded real responses and state that live integration was not demonstrated.

## 11. Validation and completion criteria

Select a small labelled set before prompt tuning. Include valid matches, ambiguous class, unknown occupancy, missing geometry and a case that must escalate. Hold some investigation examples back from prompt development.

- **Coverage/matching:** count all facilities matching the chosen area/classes, inspect a sample, and report unmatched or ambiguous records.
- **Geometry:** verify overlap gives zero, a known separated example gives the expected metric distance, missing geometry stays unknown, and the point fallback is labelled.
- **Priority:** verify normalisation, stable ties and directional changes as proximity/size/value change. Unknown required inputs stay visible. These checks verify implementation, not operational validity of the weights.
- **Updates:** duplicates and older snapshots cannot regress state. New snapshots change affected priorities, preserve source age, and flag unexpected disappearance without task closure.
- **Tasks:** assignments/progress survive updates and reload. Suggestions do not duplicate. Incompatible/unavailable teams cannot be assigned; missing resources remain explicit.
- **Agent:** report supported proposals, correct escalations and unsupported claims on held-out examples. Capacity must not become a claim about actual occupancy.
- **Latency:** report source age separately from response-receipt-to-queue-update processing time. Prototype target: under one minute for the selected area; measure it rather than claim it in advance.

Done means fixture and real-data loops work, an investigation is demonstrated, an analyst can assign/progress a task, the next update preserves it, and these checks have recorded outcomes. Do not claim validated evacuation decisions, forecast accuracy or superiority over historical emergency response.

## 12. Three-minute demo

1. **20 s:** explain the problem and the two algorithms: exposure updates and practical coordination.
2. **35 s:** show a real incident, the area's facilities and one location's proximity/size/value breakdown.
3. **40 s:** open missing occupancy or ambiguous class; run evidence lookup and confirm a proposal or answer the question.
4. **30 s:** assign a follow-up task to an available capable team; show status and any blocker.
5. **35 s:** process the next live or recorded fire update; show changed priorities and the preserved assignment.
6. **20 s:** show measured validation, source age, processing latency and one unresolved case.

Use labelled synthetic fixtures for development and failure cases. Present real provider data in the final incident demonstration. Record a backup before the final rehearsal.

## 13. Risks and fallbacks

| Risk | Response |
|---|---|
| Provider access or usable incident unavailable | Test first. Use available recorded real responses; disclose synthetic-only coverage if real data remains unavailable. |
| No usable forecast | Run distance-based exposure; forecast fields remain null. |
| Missing occupancy or facility class | Label proxies or investigate; reduce to covered classes. |
| Disputed priority policy | Show components/configuration and seek analyst feedback; do not claim validated weights. |
| Agent cannot resolve a case | Escalate one question and allow manual resolution. |
| Update changes/removes an asset | Refresh exposure, preserve tasks and flag affected work. |
| No suitable team | Leave task unassigned/blocked and expose the resource need. |
| Live demo fails | Recorded responses through the same pipeline, labelled mode, backup video. |

## 14. After the MVP

Only expand after completion criteria pass: provided-forecast urgency, road graphs and route constraints, shared exits, receiving-centre suitability, expert-reviewed evacuation/confinement recommendations, more incidents/classes/providers, automated resource scheduling and multilingual alerts. Custom spread modelling and wind what-ifs are separate future work.

## 15. Kickoff decisions and references

Resolve in the first two hours: accessible incident and area, supported classes, scoring scales/weights and class-value table, review handling, team capabilities, snapshot fixture, event duration and submission requirements. If a mentor is available, ask whether the task queue and score explanations help their coordination workflow.

Primary integration references; verify access and response semantics during implementation:

- [Deepfire clusters](https://docs.deepfire.co/api/clusters), [satellite perimeters](https://docs.deepfire.co/api/satellite-perimeters), [authentication](https://docs.deepfire.co/guides/authentication) and [optional fire spread](https://docs.deepfire.co/api/fire-spread).
- [Gencat Equipaments](https://analisi.transparenciacatalunya.cat/resource/8gmd-gz7i.geojson) for the initial facility extract.
- [Catalan geographic resources provided by the challenge](https://interior.gencat.cat/ca/serveis/informacio-geografica/).
- [Superpowers](https://github.com/obra/superpowers) and [project workflow](AGENTS.md).
