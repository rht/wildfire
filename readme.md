# FireLine — Practical Values-at-Risk MVP (v4)

Track 4, "Values at risk": Norrsken x Deepfire "AI for Wildfire" challenge, Hackbarna 2026.

**One incident, one bounded area, one analyst workflow:** identify priority locations, assign follow-up tasks, and update the queue as the fire changes.

This README is the current MVP scope and interface reference. Maintain project updates here; it supersedes the broader scope in `PLAN.md`.

The colleague builds **risk assessment**, which consumes fire updates, discovers facilities and emits location assessments. [@mirrdj](https://github.com/mirrdj) builds **analyst coordination**, which ranks those assessments and turns them into tasks, assignments and questions for the fire analyst. The shared contract is in section 5.

Use [Superpowers](https://github.com/obra/superpowers) for development. Work in a dedicated branch and worktree under this repository's `.worktrees/` directory, publish every task branch to `origin`, and push progress so colleagues can review it. See [AGENTS.md](AGENTS.md) for the persistent workflow.

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
| Exposure | Distance, overlap and per-location spread-predicted arrival, with source and age visible. A forecast is required for the contact order. |
| Ranking | Predicted time to impact minus total evacuation duration and buffer; missing inputs remain in review. |
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
| Spread forecast | Provided per-location fire arrival estimates, with source and estimate semantics. Without a usable forecast, show an unranked review queue; do not substitute distance for arrival time. |
| Evacuation duration | Sourced estimate including mobilisation, preparation/loading and movement to a receiving location. Record assistance/transport assumptions; do not guess duration from headcount alone. |

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
| `area_m2` | Nonnegative number or null | Optional footprint area; not an input to contact urgency. |
| `capacity`, `estimated_occupancy` | Nonnegative integers or null | Maximum people versus estimated people present. |
| `occupancy_basis` | String or null | Evidence or estimation method; capacity used as a proxy is explicit. |
| `value_score`, `value_basis` | Number or null; string or null | Class-based operational importance and versioned analyst policy. |
| `distance_to_fire_m`, `intersects_fire` | Nonnegative number or null; boolean or null | Geometric exposure; record point/footprint approximation in provenance. |
| `burn_probability` | Number or null | Optional provider estimate over its documented horizon; null when unsupported. |
| `arrival_p10_at`, `arrival_p50_at` | Timestamps or null | Optional arrival quantiles only when supported by the provider. Null does not establish safety. |
| `forecast_horizon_at`, `forecast_source` | Timestamp or null; string or null | Forecast horizon and source/method; required provenance for forecast-based contact ranking. |
| `fire_arrival_at`, `fire_arrival_basis` | Timestamp or null; string or null | Selected spread-predicted arrival estimate and meaning, e.g. p10 when supported. Never infer it from distance alone. |
| `evacuation_min`, `evacuation_source` | Nonnegative number or null; string or null | Total estimated evacuation duration in minutes, including mobilisation, preparation/loading and onward movement, with its basis. |
| `needs_review`, `review_reasons` | Boolean; array of strings | Such as `location_unknown`, `occupancy_unknown`, `occupancy_seasonal`, `class_ambiguous`, `value_unknown`, `exposure_unknown`. |
| `sources` | Array of objects | Field-level provenance: `fields`, `source`, `observed_at`, `available_at`, `fetched_at`, `notes`. Unknown source times remain null. |

For historical replay, evidence must have been available by `as_of`; missing availability information cannot establish that. Document any forecast-to-asset aggregation method. Missing forecasts do not block ingestion or manual tasks, but they do block automatic contact ranking.

## 6. Contact priority from the evacuation window

Use fire-spread prediction to express proximity in time: how long until the fire reaches each location. The contact order depends on how much of that time is needed to evacuate.

```text
time_to_impact = predicted_fire_arrival - current_time
latest_start = predicted_fire_arrival - total_evacuation_duration - buffer
remaining_window = latest_start - current_time
```

Rank by **smallest remaining window first**, then earlier predicted arrival, nearer geographic distance, and stable asset ID. A farther asset may be more urgent because the fire is spreading toward it or its evacuation takes longer. Geographic distance does not replace a forecast or receive an arbitrary weight.

The evacuation estimate includes mobilisation, preparation/loading and onward movement to the receiving location. Assistance needs, transport availability and occupancy inform this duration rather than acting as separate ranking weights. Property value does not override contact urgency. Estimates must have a source; the static prototype uses explicitly synthetic forecast and duration inputs.

A zero or negative window stays at the top with `window_exhausted`: immediate analyst review is needed, not an automatic evacuation instruction. Missing forecast, evacuation duration or provenance produces an unranked review item. No probability, arrival time or duration is fabricated from distance or headcount. A missing distance does not prevent ranking when the forecast and evacuation estimate are known.

Coordination emits `rank`, `slack_min` (remaining window), `latest_start_min`, `time_to_impact_min`, `status`, `components`, forecast/evacuation sources and `review_reasons`. This replaces the weighted contact score. The static API uses `fire_arrival_min` relative to a common scenario epoch and `ContactPolicy(now_min, buffer_min)`; live timestamps must be converted to that same epoch. The selected arrival estimate must state its semantics; use a conservative quantile only when the provider actually supports it.

Response sequencing remains a separate constrained calculation using action deadlines, capabilities and explicit benefits/prerequisites (section 16). Static action durations are not automatically treated as total evacuation durations.

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
4. On analyst confirmation, persist the override, recalculate the remaining evacuation window and update the existing task.

Four tools suffice: `get_asset`, `lookup_facility`, `propose_update`, and `escalate`. Keep evidence and tool calls visible, cap investigation steps, and leave unsupported questions unresolved. All field updates require analyst confirmation in this MVP. The agent does not directly assign teams or change scoring policy; calculation stays in code.

No general chat, live web research, alert drafting or wind what-if tools are needed. An LLM failure leaves the question answerable manually. Demonstrate one actual investigation call; label any prerecorded fallback output.

## 9. Interface and implementation

One screen contains the fire/facility map, ranked table, visible review queue, selected-facility evidence and timing breakdown, team/task controls, and change log. Show input mode, timestamps and stale-data state. Live and recorded inputs use the same screen; a "next update" control suffices for the demo.

Use Python for ingestion/calculations, GeoPandas/Shapely for geometry, Streamlit with one map component, and an LLM API with tool calling. Cache responses and snapshots as JSON/GeoJSON; use SQLite for tasks and analyst overrides. The core MVP needs no road-network library, raster-processing stack, message broker or custom simulation service.

## 10. Two-person ownership and build sequence

| Owner | Responsibility | Deliverable |
|---|---|---|
| Colleague — risk assessment | One provider adapter, incident/area selection, cached facilities, matching, geometric exposure, provenance and per-location spread predictions (or explicit missing-forecast status). | Section 5 snapshots and ingestion/exposure checks. |
| [@mirrdj](https://github.com/mirrdj) — analyst coordination | Scoring, persistent tasks, roster checks, manual assignment, agent investigation, UI and change log. | Ranked/review queues and the analyst workflow against fixtures and real snapshots. |
| Both | Contract/policy agreement, integration, labelled validation examples and rehearsal. | Complete demo, measured checks and explicit limitations. |

Assume about 24 working hours for two people, subject to the event's actual duration. Cut optional enrichment before extending the core workflow.

| Time | Colleague | @mirrdj | Milestone |
|---|---|---|---|
| 0–2 h | Verify provider response and facility coverage; agree contract. | Shared fixture, roster and UI skeleton; agree policy. | Two snapshots and one missing-information case agreed. |
| 2–6 h | Cached facility loading and fire-response adapter. | Fixture ranking, tasks, assignment and persistence. | **Entire fixture loop works.** |
| 6–12 h | Real snapshots; check matches and distances. | Connect snapshots; evidence lookup and confirmation. | Real data and one agent investigation work. |
| 12–18 h | Poll/record updates; stale/missing-data handling; forecast integration or explicit missing-forecast review. | Assignment checks, change log and error handling. | Workflow survives update, missing data and reload. |
| 18–22 h | Validate exposure and source timing. | Validate evacuation-window ordering, agent cases and task persistence. | Results recorded; feature freeze. |
| 22–24 h | Rehearse together. | Rehearse together. | Two dry runs and backup recording. |

If behind: drop the third class, then visual polish. Use recorded forecast inputs if live forecast access is unavailable. Retain real data, the update loop, one agent investigation, manual assignment and validation. If live polling is unavailable, use recorded real responses and state that live integration was not demonstrated.

## 11. Validation and completion criteria

Select a small labelled set before prompt tuning. Include valid matches, ambiguous class, unknown occupancy, missing geometry and a case that must escalate. Hold some investigation examples back from prompt development.

- **Coverage/matching:** count all facilities matching the chosen area/classes, inspect a sample, and report unmatched or ambiguous records.
- **Geometry:** verify overlap gives zero, a known separated example gives the expected metric distance, missing geometry stays unknown, and the point fallback is labelled.
- **Priority:** verify forecast arrival minus elapsed time, evacuation duration and buffer; a farther downwind location can outrank a nearby one. Check negative/zero windows, stable ties and missing estimates. These checks verify implementation, not the accuracy of supplied forecasts or evacuation estimates.
- **Updates:** duplicates and older snapshots cannot regress state. New snapshots change affected priorities, preserve source age, and flag unexpected disappearance without task closure.
- **Tasks:** assignments/progress survive updates and reload. Suggestions do not duplicate. Incompatible/unavailable teams cannot be assigned; missing resources remain explicit.
- **Agent:** report supported proposals, correct escalations and unsupported claims on held-out examples. Capacity must not become a claim about actual occupancy.
- **Latency:** report source age separately from response-receipt-to-queue-update processing time. Prototype target: under one minute for the selected area; measure it rather than claim it in advance.

Done means fixture and real-data loops work, an investigation is demonstrated, an analyst can assign/progress a task, the next update preserves it, and these checks have recorded outcomes. Do not claim validated evacuation decisions, forecast accuracy or superiority over historical emergency response.

## 12. Three-minute demo

1. **20 s:** explain the problem and the two algorithms: exposure updates and practical coordination.
2. **35 s:** show a real incident, the area's facilities and one location's predicted arrival and evacuation-window breakdown.
3. **40 s:** open missing occupancy or ambiguous class; run evidence lookup and confirm a proposal or answer the question.
4. **30 s:** assign a follow-up task to an available capable team; show status and any blocker.
5. **35 s:** process the next live or recorded fire update; show changed priorities and the preserved assignment.
6. **20 s:** show measured validation, source age, processing latency and one unresolved case.

Use labelled synthetic fixtures for development and failure cases. Present real provider data in the final incident demonstration. Record a backup before the final rehearsal.

## 13. Risks and fallbacks

| Risk | Response |
|---|---|
| Provider access or usable incident unavailable | Test first. Use available recorded real responses; disclose synthetic-only coverage if real data remains unavailable. |
| No usable forecast | Display exposure context and an unranked review queue; forecast fields remain null. |
| Missing occupancy or facility class | Label proxies or investigate; reduce to covered classes. |
| Disputed timing policy | Show arrival/evacuation assumptions and buffer; seek analyst feedback on the estimates. |
| Agent cannot resolve a case | Escalate one question and allow manual resolution. |
| Update changes/removes an asset | Refresh exposure, preserve tasks and flag affected work. |
| No suitable team | Leave task unassigned/blocked and expose the resource need. |
| Live demo fails | Recorded responses through the same pipeline, labelled mode, backup video. |

## 14. After the MVP

Only expand after completion criteria pass: road graphs and route constraints, shared exits, receiving-centre suitability, expert-reviewed evacuation/confinement recommendations, more incidents/classes/providers, automated resource scheduling and multilingual alerts. Custom spread modelling and wind what-ifs are separate future work.

## 15. Kickoff decisions and references

Resolve in the first two hours: accessible incident and area, supported classes, arrival-estimate semantics, evacuation-duration sources and time buffer, review handling, team capabilities, snapshot fixture, event duration and submission requirements. If a mentor is available, ask whether the task queue and score explanations help their coordination workflow.

Primary integration references; verify access and response semantics during implementation:

- [Deepfire clusters](https://docs.deepfire.co/api/clusters), [satellite perimeters](https://docs.deepfire.co/api/satellite-perimeters), [authentication](https://docs.deepfire.co/guides/authentication) and [optional fire spread](https://docs.deepfire.co/api/fire-spread).
- [Gencat Equipaments](https://analisi.transparenciacatalunya.cat/resource/8gmd-gz7i.geojson) for the initial facility extract.
- [Catalan geographic resources provided by the challenge](https://interior.gencat.cat/ca/serveis/informacio-geografica/).
- [Superpowers](https://github.com/obra/superpowers) and [project workflow](AGENTS.md).

## 16. Static contact and response-order prototype

This iteration adds two separate calculations over a fixed collection of locations. Contact priority uses spread-predicted arrival and total estimated evacuation time. Geographic proximity breaks timing ties; people and value do not directly determine contact order. Response order compares feasible sequences for one crew, including explicit action prerequisites and declared effects on other locations. This is a static decision-support experiment, not operational dispatch or a fire-spread simulator.

In the O/B/C/A example, B and C have equal distance to O and B has higher direct value. C may nevertheless come first if it unlocks timely assistance at A or has an explicitly supplied protective effect on A. Coordinates alone never establish that effect. Synthetic scenarios cover both interpretations and counterexamples.

### Design and implementation plan

- [x] Add typed static locations, actions, benefit assumptions and directed travel-time inputs in `fireline/priority_models.py`; reject invalid counts, times, references, cycles and non-finite numbers.
- [x] Test then implement independent contact ranking in `fireline/contact_priority.py`, with visible missing-data review and transparent timing components.
- [x] Test then implement exact one-crew sequence search in `fireline/response_priority.py`: travel/action duration, completion deadlines, capability checks, prerequisites, unique coverage, deterministic ties and explicit unserved/review results. Limit exhaustive search to eight actions.
- [x] Add a reproducible O/B/C/A fixture and edge-case variants, a CLI, and machine-readable results. Compare look-ahead planning with a direct-value greedy baseline.
- [x] Validate against an independent small-instance enumeration oracle and run the existing suite. Request independent code review.
- [x] Generate and visually inspect `reports/static-priority-report.pdf`, explaining assumptions, equations, edge cases, results and limits; document commands here and push the task branch.

The response objective compares covered assisted-person units, total-person units and asset-value units in that order, then earlier benefit and shorter completion time. These are model accounting quantities under supplied benefit assumptions, not predictions of people saved. Asset coverage is the maximum of completed action effects, not their sum, so overlapping actions do not double-count people. A zero-immediate-benefit prerequisite must remain a candidate. Unknown benefit inputs and unconfirmed effects produce review items; absent travel legs are unavailable, never straight-line shortcuts. All times are minutes from the scenario start, and the fire-direction arrow is illustrative; deadlines are supplied independently.

### Run the static prototype

```sh
uv venv
uv pip install -e ".[dev,report]"
.venv/bin/python scripts/static_priorities.py
make static-priorities
.venv/bin/python -m pytest -q --junitxml=data/priority-tests.xml
make priority-report
```

- Input: [fixtures/static_priority.json](fixtures/static_priority.json). It is a synthetic local-metre layout; the directed travel matrix is supplied separately and is not calculated from straight-line distance. For a different static collection, pass `--input path/to/scenario.json` to the CLI using the same `static-priority-1` schema.
- Output: [reports/static-priority-results.json](reports/static-priority-results.json), including contact-window components, selected action timings, covered/unserved locations, review issues, and the best sequence for each possible first action.
- Report: [reports/static-priority-report.pdf](reports/static-priority-report.pdf), generated by `scripts/build_priority_report.py` from the O/B/C/A fixture and its 14 variants. The report is specific to this demonstration, not an arbitrary-input report template.

For the base fixture, contacts are **A → B → C** because the remaining windows are **2, 8 and 10 minutes** respectively (A: 9 minus 7; B: 12 minus 4; C: 12 minus 2, at minute zero with no buffer); the response sequence is **C → A**. The direct-value greedy baseline chooses **B → C** and misses A's supplied deadline. With an explicit protective effect from C to A, the alternative sequence is **C → B**; with direct access to A, it is **A → C**. No automatic preference for C is hard-coded.

Review questions do not disappear when another action is selected. Partial coverage uses the maximum declared on-time effect per asset. Inclusive time windows use a `1e-9` minute tolerance for floating-point arithmetic noise, not an operational buffer; `buffer_min` is a separate input. Capability and prerequisite fields must be arrays of complete tags/IDs, never scalar strings.

Validation includes 14 labelled scenario variants, independent enumeration of 12 simple and 30 constrained four-action instances, boundary and malformed-input tests, CLI checks, and the pre-existing suite. The PDF includes the test count from the latest supplied JUnit file. Independent code review found and prompted regression fixes for fractional-minute window boundaries and scalar capability parsing.

This implementation is a standalone Python API/CLI experiment alongside the existing engine, not yet integrated into its Streamlit UI or live snapshot loop. It supports one crew, at most eight actions, a fixed horizon and supplied deadlines/effects. It does not infer suppression success, provide safe routes, check crew return/egress, allocate partial evacuation capacity, or dispatch teams. Real snapshot adaptation, preservation of completed/assigned work during re-planning, and multi-crew scheduling remain subsequent work.

### Contact policy revision: forecast and evacuation time

`forecast-evacuation-window-v2` replaces the earlier weighted proximity/people/value contact score. Static locations now include nullable `fire_arrival_min`, `evacuation_min`, `forecast_source` and `evacuation_source`. Older collections still load; missing timing evidence places their contacts in review. Times must share the scenario epoch. For a nonzero elapsed time or extra contact buffer, call `rank_contacts(locations, ContactPolicy(now_min=..., buffer_min=...))`. The response-action planner's feasibility buffer remains a separate scenario input.

The example's contact order happens to remain A → B → C, but its justification is now the remaining window, not A's value. Live spread prediction remains the upstream engine's responsibility; this module consumes per-location predictions.
