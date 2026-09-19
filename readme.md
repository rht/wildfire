# FireLine — Practical Values-at-Risk MVP (v4)

Track 4, "Values at risk": Norrsken x Deepfire "AI for Wildfire" challenge, Hackbarna 2026.

**One incident, one bounded area, one analyst workflow:** identify priority locations, assign follow-up tasks, and update the queue as the fire changes.

This README is the current MVP scope and interface reference. Maintain project updates here; it supersedes the broader scope in `PLAN.md`.

The colleague builds **risk assessment**, which consumes fire updates, discovers facilities and emits location assessments. [@mirrdj](https://github.com/mirrdj) builds **analyst coordination**, which ranks those assessments and turns them into tasks, assignments and questions for the fire analyst. The shared contract is in section 5.

Use [Superpowers](https://github.com/obra/superpowers) for development. Work in a dedicated branch and worktree under this repository's `.worktrees/` directory, publish every task branch to `origin`, and push progress so colleagues can review it. See [AGENTS.md](AGENTS.md) for the persistent workflow.

## Voice-agent delivery plan

**Owner:** [@mirrdj](https://github.com/mirrdj). **Status:** planned; implementation and calling
feasibility proceed in separate branches below. The SLNG API key will be supplied later. This
section is the shared plan; adding it to main does not mean a live voice agent is deployed.

**Goal:** contact a household in priority order, collect evidenced evacuation-readiness answers,
offer a person, and return a structured result to analyst coordination. Every household remains
visible until its outcome is confirmed, including households the firefighter sequence cannot visit.

The household-readiness prototype already exists on `codex/evacuation-readiness` at `5652c90`
(`fireline/evacuation_readiness.py`, `fixtures/evacuation_readiness.json`). It proposes
`self_evacuate`, `assisted_evacuation` or `undetermined`; it does not place calls. The SLNG branch
will include that dependency. @rht's window producer/consumer/ranking branches own the separate
snapshot/UI timing integration; avoid duplicating those changes.

### Parallel work and ownership

| Work | Branch | Worktree | tmux session |
|---|---|---|---|
| SLNG interview implementation | `codex/slng-voice-agent` | `.worktrees/slng-voice-agent` | `wildfire-slng` |
| Calling feasibility, macOS first | `codex/calling-research` | `.worktrees/calling-research` | `wildfire-calling` |

Use Superpowers, test behavior before implementation, keep all project documentation in this
README, and push each branch at meaningful checkpoints. Only this plan is authorized for main in
this step; workers retain their implementation/research on their own branches. Missing credentials
must not block local implementation, fixtures, tests or documentation. Session logs are local and
ignored; they are not project documentation or a place to store credentials.

### Shared flow and boundaries

```text
ranked location + analyst-supplied incident brief + approved contact
        -> voice interview (SLNG or later an alternative audio transport)
        -> structured call evidence and lifecycle events
        -> deterministic readiness checks
        -> proposed self evacuation / assistance / human follow-up
        -> analyst task state, with departure and arrival tracked separately
```

The existing risk/contact algorithms remain deterministic. The agent collects information; it
cannot change the fire forecast, choose an arbitrary nearby building, invent a safe route, issue an
evacuation order, or mark somebody evacuated from a completed call. A reception site must be
explicitly designated and have capacity and a usable route. A hospital qualifies only when it has
been designated for that purpose. The readiness module provides the existing static checks.

### Interview and result contract

Use a brief, explicit AI introduction and a clearly labelled simulated scenario in the first demo.
Relay only the incident brief supplied by the analyst. Ask one question at a time:

1. Confirm the intended location and whether the respondent can answer for everyone there.
2. Ask whether everyone can leave without emergency assistance; capture mobility or other help
   needs without inferring ability from age, property value or facility class.
3. Ask whether suitable transport is available for everyone and what preparation remains.
4. Ask whether they want to speak with a person. Honour that request immediately rather than
   requiring a low-confidence score first.
5. Read back the critical answers and obtain acknowledgement. If an approved instruction exists,
   confirm its receipt separately; do not equate acknowledgement with departure or arrival.

Proposed transport-neutral records (stable IDs link the call to the asset and snapshot):

```python
CallRequest = {
    "request_id": str, "asset_id": str, "snapshot_id": str,
    "contact_number": str,                 # E.164; local/private storage only
    "language": str, "incident_brief": str,
    "human_callback_number": str | None,
    "input_mode": "synthetic" | "recorded" | "live",
}
CallResult = {
    "request_id": str, "asset_id": str, "snapshot_id": str,
    "provider_call_id": str, "status": str,
    # queued, ringing, in_progress, completed, no_answer, failed, declined
    "observed_at": str,                    # UTC ISO timestamp; preserve original evidence time
    "identity_confirmed": bool | None,
    "whole_household_confirmed": bool | None,
    "can_self_evacuate": bool | None,
    "transport_available": bool | None,
    "wants_human": bool | None,
    "acknowledged": bool | None,
    "confidence": float | None,            # review signal, NOT probability of safety
    "confidence_basis": str | None,
    "evidence": dict,                      # answer field -> supporting transcript excerpt
    "contradictory": bool,
    "source": str,
    "human_followup_required": bool,
    "human_followup_reasons": list[str],
    "transfer_status": str | None,         # requested/connected/failed; not merely tool-called
}
```

Unknown answers remain null. A transport adapter maps this record into `CallAssessment` using a
common scenario epoch; it must reject wrong asset/call associations and future evidence. Persist
provider lifecycle facts separately from extracted answers. Duplicate callbacks must not create
repeated tasks or repeat a call; later-arriving old events must not downgrade a terminal state.
Store credentials only in the ignored `.env`; use the existing `fireline.env.load_env()` loader.
Do not write numbers, transcripts or credentials to public fixtures or logs; fixtures use synthetic
contacts and conversations. Retain only evidence needed for the analyst workflow in local storage.

### Workstream A: SLNG implementation

**Suggested files:** `fireline/voice_models.py` (request/result validation),
`fireline/voice_interview.py` (prompt and result normalization), `fireline/slng_voice.py` (provider
adapter), `fireline/voice_store.py` (call lifecycle persistence and task linkage),
`scripts/voice_demo.py` (offline and explicit live modes), `tests/test_voice_*.py`, synthetic JSON
under `fixtures/voice/`. Keep provider details outside the readiness algorithm.

- [x] **A1 — Contract and offline interview.** Read the existing readiness module. Write failing
  tests for a confirmed self-evacuating household, assistance needed, requested person, low/unknown
  confidence, missing evidence, contradictory answers and no answer. Implement validation and
  normalization; demonstrate all outcomes without an API key. Assert that no-answer/low-confidence
  results cannot become `self_evacuate` and that every location retains a follow-up path.
- [x] **A2 — SLNG adapter.** Verify current official API schemas before writing the client.
  Implement explicit configuration, HTTP timeouts, sanitized errors, agent configuration and
  browser-session creation; separate outbound dispatch from both. Validate `SLNG_API_KEY`, agent ID
  and outbound-connection requirements. Use injectable HTTP transport in unit tests. Do not blindly
  retry an ambiguous call-creation timeout, since that may dial twice. A missing key produces a clear
  not-configured result while the offline demo continues to work.
- [x] **A3 — Results and human handoff.** Add authenticated result ingestion or a verified polling
  path (choose based on current SLNG support). Persist requests/results, enforce idempotency and
  ordering, and map only evidenced answers to the readiness input. Add a human callback task for a
  requested person, low/unknown confidence, incomplete interview, bad audio, no answer or failed
  transfer. Do not label a transfer successful before the provider confirms connection. Extend
  `TaskStore` only where needed, preserving assigned work across snapshots and restarts.
- [x] **A4 — Demonstrable local flow.** Provide an offline command that consumes the same location
  fixture and prints the call result, proposed mode and remaining tasks. Test restart/replay, wrong
  call identity, timeout, duplicate event and failed transfer. Keep any UI adapter separate from
  @rht's ongoing priority/UI work. Run the full Python suite, request code review, document exact
  commands and limitations here, commit and push the branch.
- [ ] **A5 — Credentialed verification, later.** After the API key and test destination are supplied,
  test one browser conversation, then one explicitly requested phone call to a consenting teammate,
  then human transfer. Record actual outcomes without publishing personal data. Credentials alone
  do not imply telephony is configured or authorize calling arbitrary asset contacts. Live behavior
  stays marked unverified until these checks have run.

**Acceptance:** an offline demonstration and tests work without credentials; provider code is ready
for a supplied key; structured answers reach the readiness checks; uncertain or requested human
contact creates a durable task; no household is silently treated as evacuated. Report live versus
simulated behavior explicitly. The initial demo language is configurable English; Spanish/Catalan
model availability and quality require explicit provider selection and a later spoken test.

### Workstream B: calling from macOS, Raspberry Pi fallback

**Priority:** reuse the available Mac and a regular mobile phone/SIM if feasible. A Pi is useful
only if Linux supplies a required bridge that macOS cannot provide. Existing modem: Huawei
E3372-325 HiLink. Its documented data/SMS support does not establish voice/audio support.

- [ ] **B1 — Verify macOS paths.** Research official Apple/CoreBluetooth/audio documentation and
  maintained primary-source projects. Distinguish initiating a call (including iPhone Continuity)
  from programmatically capturing AND injecting call audio. Check Android/iPhone Bluetooth HFP,
  USB or wired audio alternatives and whether human handoff is possible. Mark each claim as
  documented, locally tested, hardware-dependent or unsupported; no claim that pairing alone is
  sufficient. Inventory relevant local software read-only without probing personal communications.
- [ ] **B2 — Evaluate Linux/Pi fallback.** Check Asterisk `chan_mobile`/BlueZ requirements for the
  actual phone and current Linux setup. Separately assess a voice-capable modem with accessible
  audio. Do not assume E3372-325 compatibility from a different Huawei model, flash firmware, or
  modify the existing Vibecat dongle fleet. Read only narrowly relevant local model notes; never copy
  private fleet numbers, SIM identities, PINs or credentials into this repository.
- [ ] **B3 — Produce a concrete recommendation.** Update this README's calling-feasibility findings
  with a comparison of hardware, software, call control, two-way audio, expected setup effort,
  concurrency limits and still-needed tests. Prefer a runnable local diagnostic script such as
  `scripts/check_calling_host.py` if it helps establish capability without calling anyone. Explain
  how audio would reach SLNG (managed telephony/SIP versus a custom STT/LLM/TTS bridge); those are
  different integrations. Include exact next steps for the strongest macOS path and the Pi fallback.
- [ ] **B4 — Verify and publish.** Test any added script, attach direct primary-source links to the
  findings, record blockers requiring a phone/Pi/carrier detail, commit and push. Do not buy hardware,
  pair a personal phone, record conversations, place calls or change system services as part of this
  research task. Hardware-dependent validation is a subsequent explicitly targeted test.

**Acceptance:** a defensible macOS-first answer with a reproducible path or a specific blocker; a
Pi/Linux fallback that addresses both dialling and audio; a clear separation between SIM voice and
using a SIM only for internet. A documented negative result is useful; an untested workaround must
not be presented as working.

### Integration and verification checkpoints

Run focused tests after each component and the full suite before each handoff. Useful commands:
`uv venv`, `uv pip install -e ".[dev]"`, `.venv/bin/python -m pytest -q`, `git diff --check`.
The readiness dependency has 253 passing tests at its own commit; establish a fresh baseline in
whichever combined tree is used. Public documentation remains this README only. Both workers push
without merging to main; review the combined result before integration. Neither worker should wait
idle for an API key while offline work remains.

Sources checked on 19 September 2026:
[SLNG managed agents](https://docs.slng.ai/voice-agents),
[SLNG configuration](https://docs.slng.ai/examples/agents-config),
[SLNG API examples](https://docs.slng.ai/examples/agents-api),
[event challenges](https://www.hackbcn.com/en/events/aisummit26),
[Asterisk mobile-channel features](https://docs.asterisk.org/Configuration/Channel-Drivers/Mobile-Channel/Mobile-Channel-Features/),
[Asterisk requirements](https://docs.asterisk.org/Configuration/Channel-Drivers/Mobile-Channel/Mobile-Channel-Requirements/),
[E3372-325 specifications](https://brovi-tech.com/productshow.php?cid=2&id=248),
[SIM7600 voice/audio example](https://www.waveshare.com/wiki/SIM7600X_Raspberry_Pi_Raspbian_Voice_Call).

## 0. Quickstart and repository layout

```sh
make setup        # uv venv + uv pip install -e ".[dev]"
make test         # pytest, no network, no LLM key (tests/conftest.py strips the keys so .env cannot leak in)
make snapshots    # rebuild fixtures/snapshots/ (synthetic fire + synthetic forecasts; real facilities + real perimeters + labelled CA arrival enrichment) with scripts/make_snapshots.py
make demo         # streamlit run fireline/app.py: map, ranked table, review queue, tasks, change log
                  #   (the sidebar steps and scrubs through the snapshot sequence, back as well as forward)
make investigate  # one agent investigation; live with NEBIUS_API_KEY (or ANTHROPIC_API_KEY), else a labelled prerecorded replay
make fetch        # pull real Gencat registers and Open-Meteo wind into data/ (network)
make precompute   # v0 engine demo (spread CA, routing, decisions); the same uncalibrated CA enriches gavarres_real_0001..0003 (labelled, see below)
```

`CONTRACTS.md` (v1.1) holds the module APIs that implement section 5 and the coordination side.
`fireline/` has `snapshot.py` (producer), `fire_input.py` (Deepfire poll or recorded responses, stale
status, latency), `forecast_input.py` (per-location fire arrival estimates: a labelled forecast file or
a Deepfire fire-spread run), `priority.py` and `tasks.py` (consumer: evacuation-window ranking, SQLite
tasks, roster, confirmed overrides), `agent.py` and `llm.py` (four-tool investigation), `app.py`
(Streamlit) and `config.py` (policies and feature flags). The v0 engine (`spread.py`, `routing.py`,
`decide.py`, `scenario.py`, `grid.py`, `fire_state.py`) stays in the tree behind `config.FEATURES`,
off by default.

**Ranking as implemented (2026-09-19):** the snapshot pipeline ranks by the remaining evacuation
window of section 6 (`priority.rank_snapshot`, sharing the arithmetic and ordering of section 16's
`contact_priority.rank_contacts`); the earlier weighted proximity–size–value score is removed.
"Current time" is the snapshot `as_of`, the buffer is 30 minutes (`config.CONTACT_POLICY`). Evacuation
durations come from `config.EVACUATION_POLICY`, a versioned class-based prototype (mobilisation,
preparation/loading, movement, with the assistance and transport assumptions in each asset's
`sources`) that the analyst can override per asset; nothing is derived from headcount. Fire arrival
comes from a forecast input: the synthetic scenarios use hand-designed, labelled synthetic forecasts
(`fixtures/forecast/`); Deepfire fire-spread was verified live on 2026-09-19 but only runs forward from
the current hotspots, so no provider forecast covers the recorded July incident. The `gavarres_real_0001..0003`
snapshots therefore carry a **labelled model enrichment** instead: the v0 cellular-automaton spread
ensemble (`fireline/spread.py`, `config.CA`, Alexandridis et al. 2008 style, not calibrated on Gavarres)
seeded from each real recorded perimeter, driven by the real recorded Open-Meteo previous-runs wind for
the snapshot hour (`fixtures/wind/`), 20 runs, 12 h horizon, 100 m cells, no fuel or slope layers, one
wind sample per snapshot. Assets its burned area reaches within 12 h get `fire_arrival_at = arrival_p10_at`,
`fire_arrival_basis = "p10 (ca_ensemble, labelled enrichment, not validated)"`,
`forecast_source = "ca_ensemble (labelled enrichment, not validated)"`, a horizon, a `burn_probability`
and a `sources` entry, and are ranked (18, 65 and 94 of the 99
located facilities in snapshots 0001..0003); the rest keep a null arrival and stay unranked
(`forecast_unavailable`). The CA is a stand-in, not a provider forecast and not validated: it spreads much
faster than the real fire did (its p50 burned area at 12 h is about 10,000 ha, against the real growth from
1,263 to 3,867 ha over about 17 h), so its arrivals are early and its ranking is a demonstration of the
pipeline on real inputs, not an assessment of the July incident. Nothing is inferred from distance.
`gavarres_real_0004` holds a real recorded Deepfire fire-spread run seeded at the July centroid; its 12 h
burned area reaches no facility, so every asset there is `forecast_unavailable`.

What is real and what is synthetic: the facilities in `fixtures/real_area/` are a real Gencat
Equipaments and schools extract for the Gavarres area (2026-09-19), with real enrolled-pupil counts
for the schools from the Gencat enrolment register; care homes and campsites carry no
coordinates in their registers and are listed with `location_unknown`. The `gavarres_real` snapshots
use **real recorded Deepfire satellite perimeters** of the July 2026 incident in the bbox
(`fixtures/fire/deepfire/real/`, pulled on 2026-09-19 with the credentials in `.env`, loaded by
`fireline/env.py`); the `synthetic_gavarres` snapshots and the top-level recorded responses are
**synthetic**, built to the same schema, as are the forecasts in `fixtures/forecast/`.
`fixtures/evidence.json` is labelled manual enrichment. The wind in `fixtures/wind/` is **real recorded**
Open-Meteo previous-runs data (model `ecmwf_ifs025`, `_previous_day1` slice: the run initialised about 24 h
before the valid time, hour containing each snapshot `as_of`, 0.1 deg grid point nearest the perimeter
centroid). The **fire arrivals on the real scenario are a model output**, the uncalibrated CA ensemble
above, labelled as such in `forecast_source` / `fire_arrival_basis`; no located register row carries a
capacity, so occupancy on the real scenario is still an investigation item. Real assets the CA does not
reach in 12 h sit in the review queue.
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
| Exposure | Distance, overlap and per-location spread-predicted arrival, with source and age visible. A forecast is required for the contact order. |
| Ranking | Predicted time to impact minus total evacuation duration and buffer; missing inputs remain in review. |
| Coordination | Follow-up tasks, manual team assignment, capability/availability checks, status and blocking questions. |
| Agent | Investigate unknown occupancy or ambiguous class against cached evidence; propose a sourced update or escalate. |
| Interface | Map, ranked location table, selected-location details, task queue and change log. |
| Validation | Matching, geometry, scoring, missing-data behaviour, update latency and task persistence. |

Deferred from day one: custom spread models (the existing uncalibrated v0 CA is used only as a labelled enrichment on the real scenario, not developed further), fuel/elevation rasters, wind what-ifs, multiple satellite feeds, Catalonia-wide discovery, road graphs, route calculation, road-cut forecasts, automatic confine/evacuate rules, receiving-centre optimisation, population allocation, all-building discovery, automatic team scheduling, free-form chat and multilingual alerts.

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

Distance-based exposure is not burn probability or time to impact. Keep unsupported forecast fields null and display "forecast unavailable". Do not infer arrival quantiles or probability from distance. The CA enrichment on the `gavarres_real` snapshots satisfies this rule as a provided per-location arrival estimate: it is a spread simulation seeded from the observed perimeter and the recorded wind, its `forecast_source` and `fire_arrival_basis` name the method and its unvalidated status, and locations it does not reach keep null fields.

Recorded responses demonstrate update handling. Only claim historical as-of replay when availability times for all inputs, including facility evidence, are established. Otherwise label it a recorded-input demo; do not claim lead time against historical evacuation decisions.

## 5. Shared location assessment contract

One record represents **one facility in one scenario snapshot**. Coordinates are attributes, not identity. The colleague owns the producer; @mirrdj owns its consumer. Both develop against the same fixture from the first milestone.

### 5.1 Snapshot envelope

| Field | Type and meaning |
|---|---|
| `schema_version` | String; `1.1` for this MVP contract (`1.0` files still load; their timing keys read as null). |
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

**Model as implemented (2026-09-19):** the loop runs on `deepseek-ai/DeepSeek-V4.1-Flash` through
Nebius AI Studio's OpenAI-compatible endpoint (`fireline.llm.NebiusLLM`, key `NEBIUS_API_KEY` in the
ignored `.env`, model overridable with `FIRELINE_MODEL`); `fireline.llm.live_llm()` falls back to
`AnthropicLLM` when only an `ANTHROPIC_API_KEY` is present. It was chosen by running this same loop
over the flagged fixture assets on five open-weight models: it and Kimi-K3 were the only two that
handled all cases without stalling or returning an empty message, at a twelfth of Kimi's cost. The
offline `FakeLLM` remains the default when no key is configured, so the tests and `scripts/validate.py`
stay reproducible; `scripts/validate.py --live` records what the real model did in `VALIDATION.md`.

## 9. Interface and implementation

One screen contains the fire/facility map, ranked table, visible review queue, selected-facility evidence and timing breakdown, team/task controls, and change log. Show input mode, timestamps and stale-data state. Live and recorded inputs use the same screen; a "next update" control suffices for the demo.

**Moving through the sequence (2026-09-19):** the sidebar walks the scenario's snapshots in both directions - "Previous" / "Next update" and a slider labelled with each snapshot's `as_of` (`1 - 2026-07-03 13:20Z` ... `4 - 2026-09-19 13:49Z` on `gavarres_real`). Forward past the store's accepted sequence applies the update as before (exposure bookkeeping, affected tasks, suggested tasks). Anywhere at or below it is a **view**: the snapshot is re-ranked with the analyst's confirmed overrides and shown on the map, the ranked table and the review queue, while tasks, the change log and the store's accepted sequence stay where they are and nothing is suggested from the earlier moment. The store's snapshot log is append-only (`tasks.SnapshotSequence`), so an earlier moment is reviewable but never replayed; a banner says which sequence is under review and where the store stands.

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

Only expand after completion criteria pass: road graphs and route constraints, shared exits, receiving-centre suitability, expert-reviewed evacuation/confinement recommendations, more incidents/classes/providers, automated resource scheduling and multilingual alerts. Custom spread modelling and wind what-ifs are separate future work; the uncalibrated v0 CA that enriches the real scenario is a labelled stand-in for a provider forecast, not that work.

## 15. Kickoff decisions and references

Resolve in the first two hours: accessible incident and area, supported classes, arrival-estimate semantics, evacuation-duration sources and time buffer, review handling, team capabilities, snapshot fixture, event duration and submission requirements. If a mentor is available, ask whether the task queue and score explanations help their coordination workflow.

Primary integration references; verify access and response semantics during implementation:

- [Deepfire clusters](https://docs.deepfire.co/api/clusters), [satellite perimeters](https://docs.deepfire.co/api/satellite-perimeters), [authentication](https://docs.deepfire.co/guides/authentication) and [optional fire spread](https://docs.deepfire.co/api/fire-spread).
- [Gencat Equipaments](https://analisi.transparenciacatalunya.cat/resource/8gmd-gz7i.geojson) for the initial facility extract.
- [Gencat schools directory](https://analisi.transparenciacatalunya.cat/resource/kvmv-ahh4.json) for school locations and [enrolled pupils per centre](https://analisi.transparenciacatalunya.cat/resource/xvme-26kg.json) for their occupancy (the directory carries none).
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

The contact ranking of this prototype is now the snapshot pipeline's ranking too: `priority.rank_snapshot` converts snapshot timestamps to minutes from `as_of` and applies the same window arithmetic and ordering (a test asserts agreement). The response-order planner remains a standalone Python API/CLI experiment, not integrated into the Streamlit UI or live snapshot loop. It supports one crew, at most eight actions, a fixed horizon and supplied deadlines/effects. It does not infer suppression success, provide safe routes, check crew return/egress, allocate partial evacuation capacity, or dispatch teams. Real snapshot adaptation, preservation of completed/assigned work during re-planning, and multi-crew scheduling remain subsequent work.

### Contact policy revision: forecast and evacuation time

`forecast-evacuation-window-v2` replaces the earlier weighted proximity/people/value contact score. Static locations now include nullable `fire_arrival_min`, `evacuation_min`, `forecast_source` and `evacuation_source`. Older collections still load; missing timing evidence places their contacts in review. Times must share the scenario epoch. For a nonzero elapsed time or extra contact buffer, call `rank_contacts(locations, ContactPolicy(now_min=..., buffer_min=...))`. The response-action planner's feasibility buffer remains a separate scenario input.

The example's contact order happens to remain A → B → C, but its justification is now the remaining window, not A's value. Live spread prediction remains the upstream engine's responsibility; this module consumes per-location predictions.

## Norma audit remediation — 2026-09-19

Scope: resolve the 42 findings recorded at audit commit `92ae882`, using that branch's report as read-only input. The fix branch starts at freshly fetched main (`c5d74f2`); the audit commit is not included. All original snippets are still present at this baseline. No defenses, exceptions, suppressions, feature-branch scans, or risk/coordination contract changes are part of this work.

Implementation and verification plan:

- [x] Add explicit UTF-8 to the 31 flagged text I/O calls; verify non-ASCII snapshot I/O under an ASCII-default Python process and retain existing fixture/CLI coverage. Close the two precompute input files with context managers while touching those calls.
- [x] Replace two SQL f-strings with fixed migration and ID queries, preserving migration idempotence and task/override numbering; cover old databases and reject unsupported internal table selections.
- [x] Preserve timestamp key precedence and policy values with single lookups, cache the HTTP session without global rebinding, return search results/counts from recursion without `nonlocal`, iterate slope factors directly, and replace the three assigned lambdas with named local functions. Verify cache reuse, explicit null/zero values, deterministic planning and existing spread/validation behavior.
- [x] Harden JUnit XML parsing with `defusedxml` in the optional report dependencies; reject DTD/entity declarations and retain valid JUnit and failed-test behavior. Confirm ordinary ElementTree's internal-entity behavior rather than assuming the scanner's XXE label demonstrates external file access.
- [x] Record each original finding and its resolution in `reports/norma-remediation-2026-09-19.json`, separate from the original audit. Re-run Norma on every changed Python file, run the full applicable test suite, obtain independent code review, and push checkpoints.
- [ ] Create the PR, fetch/rebase onto the latest main, resolve any conflicts, repeat final verification and required checks, and merge only after they pass. Keep the fix and audit worktrees available.

The connected MCP currently exposes rules and Livechecks, but no account-tier or remaining-quota tool. Its ruleset response supplies availability flags, not the account's plan or quota; these values must not be inferred from those flags.

Remediation results: all 42 findings are fixed without suppressions (31 UTF-8 I/O, two SQL statements, two global/nonlocal uses, two double lookups, three assigned lambdas, one range loop and one XML parser). All 24 changed Python files, including regression tests, returned clean from Norma Livecheck with no reduced coverage. The [resolution ledger](reports/norma-remediation-2026-09-19.json) records every original finding; [Livecheck evidence](reports/norma-livecheck-2026-09-19.json) records exact file hashes. Neither file modifies or replaces the original audit.

Verification: the unchanged baseline passed 294 tests; the remediation passes 309 tests, including ASCII-locale snapshot read/write, fixed-query selection, timestamp null/zero precedence, policy null/zero values, repeated HTTP-session reuse, planner counts/alternatives, and JUnit parsing. Independent code review approved the changes. Offline validation has eight passes, zero failures and the existing live-model check unverified. After fetch/rebase on `c5d74f2` (no conflicts), final verification again passed all 309 tests, all 57 installed dependencies were compatible, and GitHub reported no status checks and a clean merge state for PR #3. Merge is authorized after the published head is confirmed. Norma refused applied-actions registration because the repository is unlinked; the committed ledger retains the evidence.

XML behavior: the local stdlib parser (Expat 2.6.3) expanded a small internal entity but rejected an external entity reference with `ParseError`; external-file disclosure was not demonstrated. The report extra now includes `defusedxml>=0.7.1`, and JUnit parsing explicitly forbids DTDs as well as the library's default entity restrictions. Plain JUnit still renders and failed tests still prevent a success report. This does not replace parser updates or impose general input-size/resource limits. See [Python XML security](https://docs.python.org/3/library/xml.html#xml-security) and [defusedxml](https://github.com/tiran/defusedxml).

UTF-8 is now explicit for the audited text formats. Legacy local files written in a different encoding require conversion; generated fixtures are UTF-8. The risk-assessment/location-snapshot/coordination boundary and snapshot schema remain unchanged.
## 17. Household evacuation readiness and voice contact

@mirrdj owns this coordination extension. Every location stays in the output even when the
one-crew planner cannot visit it. A structured contact assessment distinguishes self-evacuation
from assisted evacuation and from an unresolved contact. Ability is never inferred from property
value, facility class, a successful call connection, or lack of an assistance record.

Completed implementation (static inputs first):

- [x] Add `fireline/evacuation_readiness.py` and tests for explicit, evidenced call answers,
  low/unknown confidence, no answer, human requests, stale evidence and contradictory reports.
- [x] Allocate whole households, in contact-priority order, to supplied approved reception centres
  with sufficient remaining capacity and a confirmed usable route within the supplied time windows.
  Return an unresolved record when no destination qualifies; never use geographic proximity alone.
- [x] Add a combined CLI/fixture showing all locations, unchanged contact ranking and response
  sequence, proposed evacuation modes, reception assignments and outstanding follow-up tasks.
- [x] Verify the complete suite, document the provider choice and limitations here, and push the
  task branch. Voice transport selection is separate from the deterministic assessment logic.

The proposed contact interview introduces the automated assistant, relays only an analyst-supplied
situation message, confirms the location and whether the respondent speaks for the whole household,
asks whether everyone can leave without emergency assistance and has suitable transport, and asks
whether they want a person. Record evidence for each answer. Missing or contradictory answers,
uncertain transcription/extraction, a failed call, or a human request produce human follow-up.
A confidence threshold is a configurable prototype review rule, not a probability of safety.

Reception centres are explicit analyst-approved inputs (a hospital is not automatically a reception
centre). Self-evacuation readiness requires confirmed household ability and transport plus a
feasible assigned destination. Acknowledgement, departure and confirmed arrival are distinct from
readiness. This extension produces proposals and tasks; it does not issue evacuation orders or
place calls while evaluating the algorithm.

### Static readiness API and example

`coordinate_evacuation(scenario, assessments, centres, routes, contact_policy=...,
readiness_policy=...)` in `fireline/evacuation_readiness.py` combines the existing contact queue and
crew sequence with one readiness record per location. Inputs use the existing scenario epoch in
minutes. Dataclasses define the exact fields:

| Input | Required information |
|---|---|
| `CallAssessment` | Asset/call IDs, completed/no-answer/failed/declined status, observation time, source and supporting interview evidence; nullable booleans for confirmed identity, whole-household coverage, ability, transport and human request; nullable confidence and a contradiction flag |
| `ReceptionCentre` | Stable centre ID, name, remaining places, analyst approval, availability deadline and source |
| `EvacuationRoute` | Origin and centre IDs, travel time, total evacuation duration including preparation, confirmed usability, availability deadline and source |
| `ReadinessPolicy` | Confidence threshold (prototype default 0.85), evidence maximum age (prototype default 15 minutes), policy version |

Only the latest assessment per asset is accepted; duplicate assessments/IDs, unknown references,
invalid booleans and non-finite/out-of-range numbers are rejected. Evidence time must not be in the
future. Confidence is supplied by the caller/integration, not calculated or calibrated by this
module. A single number should conservatively represent uncertainty in the critical answers; a
high score cannot bypass missing evidence, incomplete answers or an explicit human request.

A positive call that conflicts with a known assistance count requires human reconciliation.
A trustworthy negative ability/transport answer produces `assisted_evacuation` and an
`arrange_assistance` task. Missing or uncertain evidence produces `undetermined` and a contact or
human-callback task. Assisted transport and its destination require an analyst/team plan; this
prototype only allocates reception capacity for self-evacuating households.

For confirmed self-evacuating households, select the eligible centre with the shortest supplied
travel time (then centre ID for ties). Reserve the whole household's known headcount in contact
priority order. This is a deterministic greedy allocation, not a global transport optimizer.
Use the greater of the route-specific total evacuation duration and the upstream total estimate;
never silently shorten the upstream estimate or count travel twice. A strictly positive window
must remain before the fire-arrival, route and reception deadlines, including the contact buffer.
An unapproved, full, unknown or expired destination never becomes the default just because it is
nearby. If nothing qualifies, keep the location unresolved with a human follow-up task.

Output modes are `self_evacuate`, `assisted_evacuation` and `undetermined`, with reasons, call
provenance, destination provenance, remaining window, suggested tasks and capacity remaining.
`self_evacuate` is a proposal, not an issued order. Every record starts with
`evacuation_status: not_confirmed`; confirming instructions, departure and arrival are separate
follow-up tasks. The crew's declared action coverage does not count as arrival confirmation.

```sh
.venv/bin/python scripts/static_priorities.py \
  --readiness-input fixtures/evacuation_readiness.json
.venv/bin/python -m pytest tests/test_evacuation_readiness.py -q
```

The synthetic example retains A, B and C: A needs assisted evacuation; B can self-evacuate to the
approved community centre despite not being visited in the C → A crew sequence; C's unanswered
call needs human follow-up. The closer hospital is rejected because it is not an approved reception
site. No real call was placed and the interview confidence values are synthetic.

Capacity reservations are local to one evaluation. Persist actual reservations, contact history,
confirmed arrivals and assigned tasks before enabling repeated/live execution; this module does
not update SQLite or the Streamlit UI. Existing crew inputs/objectives are unchanged; assistance
reports create coordination tasks rather than automatically rewriting counts or actions. At a
nonzero current time the result omits the old crew plan and sets `response_replanning_required`:
a time-zero sequence must not be presented as a fresh dispatch plan. The existing PDF continues
to describe the original 14 static priority scenarios; this extension is documented here.

### Voice-provider research (19 September 2026)

The event's **SLNG challenge** explicitly accepts speech-to-text, text-to-speech or a full voice
agent, with bonus points for unmute. SLNG's managed-agent documentation supports browser sessions,
outbound calls with telephony configured, and a transfer-call capability. This is the closest fit
for the proposed household interview. [Event challenges](https://www.hackbcn.com/en/events/aisummit26),
[SLNG managed agents](https://docs.slng.ai/voice-agents).

Vonage also supports outbound voice calls and audio bridges, but its listed hackathon challenge
specifically judges integration of the **Video API**; using Voice API alone does not establish
eligibility for that prize. Mastra's challenge is a possible messaging alternative. Neither prize
eligibility nor simultaneous entry should be inferred beyond the published event rules.
[Vonage call flow](https://developer.vonage.com/en/voice/voice-api/concepts/call-flow).

A Pi and SIM dongle can supply network connectivity for a cloud voice agent. Calling directly
through that SIM additionally requires a voice-capable modem and accessible two-way audio. The
Huawei E3372-325's documented data/SMS functionality does not establish voice/audio compatibility;
do not assume it works with a Huawei voice driver for a different model. Direct SIM calling with
that model has not been verified. [Manufacturer specifications](https://brovi-tech.com/productshow.php?cid=2&id=248).
The provider transport, interview extraction, authenticated callbacks and human-transfer path are
not implemented in this static extension; its input contract is ready to receive those outcomes.

Verification: 253 tests pass, including 36 readiness cases; targeted lint and whitespace checks
pass. Independent code review found no important issues within the static scope.

### Workstream A implementation notes

Implementation plan (19 September 2026; authorized scope A1–A4): use the existing
readiness algorithm unchanged, with strict transport-neutral records, a separate SLNG HTTP
adapter and a SQLite lifecycle store sharing the existing `TaskStore` database. No UI or
snapshot/window-ranking changes. Baseline at `736253b`: 253 tests passed.

1. A1: add `voice_models.py` and `voice_interview.py`; first exercise malformed records,
   evidence/identity/time gates and all readiness outcomes in `test_voice_interview.py`.
   Normalize unknowns to null; only labelled synthetic confidence may pass the
   automated readiness review gate. Real interviews require independent human review.
   Preserve acknowledgement separately from departure/arrival.
2. A2: add `slng_voice.py` with an injectable `requests` transport, fixed official API host,
   explicit timeouts and sanitized failures. Test documented create-agent, web-session,
   dispatch and GET-call schemas offline before implementing them. Outbound dispatch is a
   separate explicit operation, never retried after ambiguous creation failure.
3. A3: add `voice_store.py` and `voice_ingest.py`; test authenticated ingestion, immutable
   request/call association, restart/replay, event ordering, task linkage and failed handoff.
   Persist provider facts separately from interview answers. Use a bearer-authenticated API
   Request tool for answers and authenticated GET-call polling for lifecycle facts; never
   parse undocumented runtime diagnostics as confirmed answers or transfer connection.
4. A4: add synthetic `fixtures/voice/` cases and `scripts/voice_demo.py`, reusing
   `fixtures/static_priority.json` and reception/route inputs from the readiness fixture.
   Exercise no-key demo and persistent replay, run the full suite, request independent code
   review, fix findings, then commit and push. A5 remains deferred; no live calls or messages.

Each step uses failing behavior tests, implementation, focused verification and a pushed
checkpoint. Current primary references are the [SLNG OpenAPI schema](https://docs.slng.ai/api-reference/agents/agents.oas.yaml),
[API Request tool guide](https://docs.slng.ai/guides/agents/tools-and-mcp/api-request-tool),
[dispatch guide](https://docs.slng.ai/guides/agents/telephony/dispatch-calls), and
[browser integration guide](https://docs.slng.ai/guides/agents/production/embed-in-website).
Older `/voice-agents` and `/examples/agents-api` README links returned 404 during this check.


**Workstream A delivery status:** A1–A4 implemented and verified offline on
`codex/slng-voice-agent`; A5 remains deferred. No provider session, phone call, message,
transfer, hardware interaction or live credentialed API request was performed. Independent
code review found and verified fixes for follow-up after completed tasks, transfer event
ordering, callback timing, the outbound preflight endpoint and browser-token file reservation.

#### Running the offline flow

From `.worktrees/slng-voice-agent`:

```sh
uv --cache-dir data/uv-cache venv
uv --cache-dir data/uv-cache pip install -e ".[dev]"
.venv/bin/python scripts/voice_demo.py
.venv/bin/python scripts/voice_demo.py --case all --db data/voice-all.sqlite
.venv/bin/python scripts/voice_demo.py --case all --db data/voice-all.sqlite
.venv/bin/python scripts/voice_demo.py --mode check-config
.venv/bin/python -m pytest -q
git diff --check
```

The default demo uses `fixtures/static_priority.json`, reception/route data from
`fixtures/evacuation_readiness.json`, and entirely synthetic interviews in
`fixtures/voice/interviews.json`. It proposes assistance for A, self evacuation for B, and
human follow-up for unanswered C. All three evacuation outcomes remain `not_confirmed`.
`--case all` also demonstrates requested person, low/unknown confidence, missing evidence,
contradictions, bad audio, missed call and failed transfer. Each report prints normalized
synthetic call results, per-location proposals and durable remaining tasks. Repeating with
the same database does not duplicate tasks. Use a fresh database when changing scenario
inputs or language; immutable request identities deliberately reject altered replay inputs.
`--language en` is configurable, but the supplied conversations are English fixtures and do
not validate another language's spoken quality.

The offline mode does not load credentials or create an HTTP client. `check-config` uses
`fireline.env.load_env()` and prints only configuration status/missing variable names. During
verification it returned `not_configured`, missing `SLNG_API_KEY` and `SLNG_AGENT_ID`, with no
outbound connection configured. No key is required for the demo or tests.

#### Provider/backend integration

`SlngClient` accepts an injectable HTTP transport. Its documented operations use Bearer auth
at `https://api.agents.slng.ai`: `POST /v1/agents`,
`POST /v1/agents/{agent_id}/web-sessions`, `POST /v1/agents/{agent_id}/calls`,
`GET /v1/agents/{agent_id}`, and `GET /v1/agents/{agent_id}/calls/{call_id}`.
Outbound preflight checks the typed agent response's ID and `sip_outbound_trunk_id` against
configuration. HTTP connect/read timeouts default to 5/20 seconds; redirects and retries
are disabled. Errors omit provider response bodies, contacts, tokens and transcripts.
A POST timeout, malformed success or server failure has an ambiguous outcome. The store
claims the attempt durably **before** HTTP; both `attempting` after a crash and
`outcome_unknown` prohibit another automated dispatch. Reconcile against provider records
manually; `bind(request_id, verified_provider_call_id)` can link a verified existing call.
Do not create a fresh request ID merely to bypass an ambiguous attempt.

Generate a private agent configuration offline with explicitly selected provider model codes:

```sh
.venv/bin/python scripts/voice_demo.py --mode agent-config \
  --stt SELECTED_STT_CODE --llm SELECTED_LLM_CODE \
  --tts SELECTED_TTS_CODE --voice SELECTED_VOICE_CODE \
  --output data/voice-agent-config.json
```

Those names are placeholders to replace with the account's available models, not tested
model IDs. `agent_configuration()` accepts published `tool_refs` and an optional outbound
connection ID; `SlngClient.create_agent(configuration)` implements agent creation. Nothing
is provisioned by generating the JSON. Browser creation returns the documented LiveKit URL,
token and session duration; the CLI exclusively reserves a private output file before HTTP
and never prints the token. Joining its audio room still needs a browser client using the
[official LiveKit embedding flow](https://docs.slng.ai/guides/agents/production/embed-in-website).

Answer delivery uses `result_tool_configuration(https_url, vault_secret_name)` and
`result_tool_attachment(tool_id, published_version)` from `fireline/slng_voice.py`. Create,
test and publish that API Request tool in SLNG, store the dedicated result secret in its
Vault, then attach the published version. The attachment locks request, asset and snapshot
IDs from call arguments and the provider call ID from `{{@call_id}}`. It uses the documented
raw JSON/Bearer format; `fireline.voice_ingest.make_app(store_factory, token)` supplies the
WSGI `POST /voice/results` receiver. Deploy it behind HTTPS with a dedicated random token
of at least 32 characters, a per-request `VoiceStore` connection and the same scenario epoch.
No public endpoint or service was deployed during this task. The receiver has a 32 KiB body
limit and returns sanitized errors; it does not log request bodies or authorization headers.

The receiver accepts only answer fields, evidence excerpts, contradiction/bad-audio flags
and the pinned association IDs. Model-supplied lifecycle, confidence and transfer-success
fields are rejected. It records the receiver's first observation time as `observed_at`,
with the additional `evidence_time_basis="receipt_only"` field: this is **not** the time the
respondent spoke. `evidence_time_unknown` therefore requires human review. Replayed payloads
retain their first timestamp. Transport-neutral records with a genuine source observation
retain it unchanged (`evidence_time_basis="source_observation"`); future or pre-epoch evidence
is rejected. No transcript extraction from SLNG's undocumented internal runtime report is
attempted. Only minimal submitted excerpts and latest answers are retained locally.

Authenticated polling verifies call/agent IDs and all three request arguments, then records
provider lifecycle separately. Unknown statuses stay unresolved; terminal states cannot be
downgraded. A tool execution marked `succeeded` does not establish connection to a person.
Transfer remains `requested`/`failed`; the available stable schema has no explicit connection
proof used by this implementation. A request for a person creates human follow-up immediately,
regardless of confidence. An approved published transfer tool may be attached separately;
its destination and actual connection must be verified in A5. The stored
`human_callback_number` never triggers an automatic transfer or call.

Provider confidence is never calibrated safety certainty and cannot automatically clear the
readiness gate. Only explicitly synthetic scores exercise successful proposals in the demo.
Real/recorded interviews produce conservative readiness inputs and independent human tasks;
an analyst follows up and records the operational decision separately. Neither a completed
call nor acknowledgement closes tasks or establishes departure/arrival.

`VoiceStore` shares a database with `TaskStore`. A minimal `create_task(commit=False)` extension
lets call state and task linkage commit atomically. Voice work uses existing analyst actions
(`contact_facility`, `request_resources`) with stable `voice:` reasons. Assigned work is reused
across snapshots and restarts. A distinct adverse event after an analyst closed the prior task
creates fresh work without reopening the old task; exact replays create none. Initial callback
work remains for analyst disposition even after a successful synthetic proposal. Departure and
arrival checks are separate tasks. Uncontacted locations also retain tasks in the demo's plan.
New CLI databases/token files are created with private file modes; use ignored `data/` storage
and retain/delete local evidence under the incident's operational retention policy. Existing
files' permissions are not changed. SQLite storage is local, not encrypted by this feature.

#### Exact prerequisites for A5 (not executed)

1. Supply `SLNG_API_KEY` and a created/reviewed `SLNG_AGENT_ID` via the ignored `.env`;
   choose available STT, LLM, TTS, voice, region and language. Publish/attach the answer tool
   and provide its authenticated HTTPS receiver, matching Vault secret and scenario epoch.
2. Explicitly approve a browser test. Use a private `CallRequest` JSON and a separate
   persistent database; consume the returned credentials with the LiveKit browser client.
3. For a later phone test, supply an explicitly approved consenting test destination and
   an active outbound connection assigned to that agent. Set
   `SLNG_OUTBOUND_CONNECTION_ID`. A private approval JSON must contain the exact
   `request_id` and matching `approved_target`; credentials alone do not authorize dispatch.
4. For human transfer, configure the published transfer tool, approved human destination
   and outbound telephony; verify actual audio and connection. A tool success is insufficient.
   Spanish/Catalan model availability and spoken quality remain unverified.

Commands prepared for a subsequent authorized session (not run here):

```sh
.venv/bin/python scripts/voice_demo.py --mode browser \
  --request-file data/approved-browser-request.json --epoch SCENARIO_UTC_EPOCH \
  --db data/voice-live.sqlite --output data/private-browser-session.json
.venv/bin/python scripts/voice_demo.py --mode poll \
  --request-file data/approved-browser-request.json --epoch SCENARIO_UTC_EPOCH \
  --db data/voice-live.sqlite
.venv/bin/python scripts/voice_demo.py --mode outbound \
  --request-file data/approved-phone-request.json --approval-file data/phone-approval.json \
  --epoch SCENARIO_UTC_EPOCH --db data/voice-phone.sqlite
```

Replace `SCENARIO_UTC_EPOCH` with the incident's common UTC ISO epoch, never a fresh time on
restart. Browser and outbound creation are separate explicit commands. No background polling
or credential-wait loop runs. The original A1–A4 implementation left snapshot ranking, the crew sequence,
UI integration and Workstream B untouched; the mock UI integration is described below.


Original A1–A4 offline verification before the main-branch rebase: **344 Python tests passed** (253 baseline plus 91 voice tests),
`git diff --check` passed, and all nine demo cases replayed with **11 tasks before and after**.
The baseline modes were A=`assisted_evacuation`, B=`self_evacuate`, C=`undetermined`; every
case retained `evacuation_status="not_confirmed"`. Verification artifacts are ignored local
files under `data/agent-session/` (`voice-demo.json`, `voice-replay.json`, `voice-pytest.txt`).
The implementation was independently reviewed; all reported findings were fixed and verified.
These are offline software checks, not validation of provider audio, telephony or hardware.

### Mock scenario replay and dashboard updates

The expanded mock exercise on `codex/slng-voice-agent` runs **34 isolated scenarios** through the
existing contact ranking, one-crew response planner, interview normalization, readiness checks and
persistent task store. Fixtures are in `fixtures/voice/replay_scenarios.json` and
`fixtures/voice/neighbourhood.json`; the reproducible
outcome report is [reports/mock-voice-results.json](reports/mock-voice-results.json). This tests
structured synthetic answers, not SLNG speech recognition, generated conversation, real calls or
successful human transfer. The phone number is fictional and the runner has no provider calls.

Run all cases and write a report, or advance one case a single event at a time:

```sh
.venv/bin/python scripts/replay_voice_scenarios.py --case all \
  --db-dir data/voice-replay-validation --output reports/mock-voice-results.json
.venv/bin/python scripts/replay_voice_scenarios.py --case baseline --step
.venv/bin/streamlit run fireline/app.py --server.port 8511
```

Open `http://localhost:8511/?demo=voice`, or enable **Mock voice scenarios** in the sidebar. Select a case, then
**Apply next mock event** to inspect each result or **Apply all remaining mock events**. **Start a
fresh mock run** creates a new database and preserves previous runs. Each case has its own database
under `data/voice-replay/` (override with `FIRELINE_MOCK_DB_DIR`); the CLI and UI can use the same
case database. Reopening the page resumes its revision. Changing fixtures requires a fresh run.
An existing ordinary incident database is rejected without modifying its contents.

| Scenario group | Expected system behavior |
|---|---|
| All eight combinations of low/high synthetic confidence, assistance needed/not needed, and human requested/not requested | Retain each reported fact. Low confidence or a human request requires a callback and keeps the evacuation mode unresolved; reported assistance remains visible alongside the uncertainty. |
| Confirmed inability to self-evacuate, or no suitable transport, with sufficiently supported synthetic answers | Propose `assisted_evacuation`; create assistance, departure and arrival follow-up work. |
| Supported ability and transport, suitable approved centre and route | Propose `self_evacuate`; show destination and instruction/departure/arrival checks. Agreement is not proof of departure or arrival. |
| Confidence 0.85 / 0.849999 / unknown | Exercise the inclusive prototype threshold and review paths. These supplied synthetic scores do not calibrate an LLM or authorize a live decision. |
| Missing evidence, contradiction, bad audio, no answer, failed/declined call, partial human request, failed transfer | Keep unresolved and create or retain human follow-up. A request for a person is honoured before the interview is complete. |
| Centre lacks space or route is unconfirmed | Do not propose a destination; retain reception/assistance and human follow-up needs. |
| Forecast update reduces B's fire arrival to minute 3 | Contact order becomes B → A → C; B's window is -1 minute and its previous self-evacuation proposal is withdrawn for review. |
| Crew loses assisted-evacuation capability | Response proposal changes from C → A to B → C; A's action is explicitly blocked and its assistance need remains. |

The default exercise is now a **six-building village with 49 people**, rather than just the A/B/C
layout. It includes a care home (20 people), school (12), three households (6, 5 and 4), and an
equipment depot (2). Two approved reception centres have 10 and 5 available places. The dashboard
shows the local layout, the next event, per-building call outcomes and proposed remaining capacity.
The original A/B/C cases remain available in the selector.

| Village exercise | Interaction being tested |
|---|---|
| Shared capacity | Lower-priority households answer first. A later, more urgent answer moves a proposed destination to the second centre; no centre is overbooked and the depot cannot yet be accommodated. |
| Mixed escalations | Different buildings report low confidence plus assistance, missing transport, a human request, no answer, or supported self-evacuation. Each keeps its own evidence and follow-up work. |
| Road closure | Oak Lane closes after the interviews. Oak apartments loses both supplied routes; the incident-wide warning requires fresh acknowledgement from all buildings and appears in the next-call briefing. |
| Reception centre closes | The 10-place hall loses approval; only the five-person household fits in the annex. |
| Fire changes direction | Mill house's updated arrival forecast leaves a negative evacuation window. It moves to the front of the contact queue and loses its self-evacuation proposal. |
| Later assistance request | Oak apartments initially reports self-evacuation, then reports needing help. Both calls remain in history and the latest assessment changes its mode. |
| Crew capability loss | The crew loses transport and assisted-evacuation capabilities. Care-home and school actions become blocked, and the proposed sequence changes. |
| Named dangerous road | Mill Road is excluded even if a route was previously confirmed. Mill house uses the eligible annex route; calls carry the named warning and evidenced acknowledgement. |
| Warning not understood | Mill house cannot acknowledge the road restriction. Its mode remains unresolved and a human callback is required, even if its other interview answers were confirmed. |

The village contact order initially is **CARE → SCHOOL → H1 → H2 → H3 → DEPOT**; its one-crew
proposal is **CARE → SCHOOL**. Capacity is allocated only to proposed **self-evacuating** groups.
The care home and school still require an analyst to arrange assisted transport and receiving
capacity; the 15 places do not accommodate all 49 people. These exercises use six actions and
one crew, not multiple concurrent teams. Building positions are a schematic in local metres;
travel times and forecast arrivals are independent supplied inputs.

Allocations can change after a later answer because they are **uncommunicated proposals**. The
prototype does not model instructions already sent to residents or automatically preserve an
issued destination; changing such instructions needs an analyst decision.

The two algorithms remain separate:

1. **Whom to contact first:** `rank_contacts` orders the smallest remaining window first:
   `fire_arrival_min - now_min - evacuation_min - buffer_min`. In the baseline, A has 2 minutes,
   B has 8 and C has 10, so contacts are **A → B → C**. Ties use earlier fire arrival, nearer
   geographic distance and stable ID. Missing timing evidence goes to review; exhausted windows
   remain urgent. Property value does not override this ordering. The fixture uses zero buffer;
   the snapshot dashboard's separately configured policy uses 30 minutes.
2. **Which action the crew should take next:** `plan_response` enumerates feasible sequences for
   one crew (at most eight actions), checking travel, action time, deadlines, capabilities and
   prerequisites. It compares assisted-person benefit first, then total-person benefit, then
   asset-value benefit, with timing/tie rules. The baseline selects **C → A** because C explicitly
   unlocks assistance at A; a greedy B-first choice misses that opportunity. Geometry alone does
   not create a protective effect. The mock output includes sequence, timings, blocked actions,
   unserved locations and the declared benefit assumptions.

The interview/readiness policy updates household proposals and tasks alongside those algorithms.
A call reporting help does not supply a revised headcount, travel time or action duration, so it
cannot silently change the crew model's numerical inputs or remove a household from consideration.
`response_review_required` makes that gap visible. The mock scenarios do not simulate elapsed call
minutes or in-progress crew movements: event order advances at a fixed scenario epoch. A changed
forecast/capability recomputes an unexecuted static proposal, not a live dispatch plan. The current
readiness API also withholds the old crew proposal when supplied a nonzero elapsed time.

**Road restrictions in the escalation-to-voice handoff:** `EvacuationRoute.road_ids` lists the
roads used by each supplied route. `coordinate_evacuation(..., road_warnings=[...])` accepts the
current incident-scoped restrictions from the analyst/upstream feed. Each warning requires
`road_id`, `road_name`, `reason` and `source`, for example:

```json
{
  "road_id": "mill-road",
  "road_name": "Mill Road",
  "reason": "fire reported beside the bridge",
  "source": "Synthetic incident command update"
}
```

Every location output carries `road_warnings`, `road_warning_version` and
`current_road_warning_acknowledged`; a selected destination also carries
`route_road_ids`. A warning overrides a route's previous `confirmed` flag. While restrictions are
active, a route with no road IDs cannot establish avoidance and is rejected. A supplied alternative
must still meet the existing timing, capacity and confirmation checks. With no eligible option,
the household remains unresolved with human follow-up. The system does not generate detours or
interpret a missing destination as an instruction to remain in place. These road constraints apply
to household evacuation routes; the one-crew planner still uses its separately supplied travel
matrix, which must also be updated if crew travel is affected.

The caller copies the location's current `road_warnings` into its `CallRequest` alongside the
analyst-supplied `incident_brief`. This is automatic for mock replay; callers using the private JSON
request interface must supply the warnings for that snapshot. SLNG receives a per-call
`road_warning_brief`, such as **“Do not take Mill Road: fire reported beside the bridge.”** The
prompt requires the road name and reported reason to be relayed with any approved evacuation
instruction; a conflicting direction is withheld for human review. It asks residents to repeat
which roads to avoid and records `road_warning_acknowledged` plus the supporting excerpt in
`evidence.road_warning_acknowledged`. Missing, negative or unevidenced acknowledgement adds
`road_warning_unconfirmed` and prevents an automatic self-evacuation proposal. A generic readback
or agreement to evacuate does not satisfy this check.

Warnings create persistent `communicate_road_warning` tasks. They remain open until an analyst
handles them, consistent with the other follow-up tasks. A content version binds acknowledgement
to the exact road names, reasons and sources in the persisted call request. Changed restrictions
invalidate the current acknowledgement and create fresh communication/callback work if the old
work was completed; repeated delivery of the same version does not duplicate it. Earlier call
answers remain historical evidence, and reported assistance needs stay visible. All buildings in
these mock incidents receive the incident-wide restrictions; none silently inherits an earlier
warning's readback. The dashboard's **Road warnings for the
next call** panel and downloadable `voice_briefings` show the current restriction text; they are
not delivery receipts. Stored requests retain the warnings supplied at call creation. A later
closure refreshes the preview and follow-up work, but does not interrupt an active call or
redeliver instructions automatically. Feed operators must supply current restrictions and complete
route road IDs; this prototype does not discover road hazards or expire stale reports. Changed
village fixtures require **Start a fresh mock run** for previously saved exercises.

**What the UI receives:** `MockReplay.state()` returns a `mock-coordination-state-1` object with
`case_id`, `snapshot_id`, `revision`, `as_of`, `pending_events`, `calls`, `plan`, `tasks`,
`response_review_required`, `layout`, `reception_centres`, `voice_briefings`, `input_mode="synthetic"`, `dispatch=false`
and `live_validation=false`.
`plan` contains both algorithm outputs, household modes and proposed reception capacity. Calls
retain confidence/basis, evidence, reported assistance, human requests and escalation reasons.
The UI can download the complete state as JSON.

Each accepted event also appends a durable `mock-coordination-update-1` notification:

```json
{
  "schema_version": "mock-coordination-update-1",
  "case_id": "baseline",
  "revision": 1,
  "event_id": "mock-voice-baseline:0",
  "kind": "call_result",
  "changed_asset_ids": ["A"],
  "refresh": "full_state",
  "input_mode": "synthetic"
}
```

Voice facts, task changes, revision and notification commit in **one SQLite transaction**. A
crash rolls them back together; duplicate replay does not advance the revision or duplicate work.
`updates(after_revision=...)` supports ordered catch-up. Task status/ownership persist separately;
only analyst action closes existing work. In particular, initial callback/contact tasks remain
open even after a successful synthetic interview. Reception capacity remains a per-case proposal,
not a production reservation ledger.

**Transport choice:** the panel uses Streamlit's
[`st.fragment(run_every="2s")`](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)
to reread the durable state while open. This already exposes updates from another CLI/process
without adding a separate WebSocket server. The notification is an invalidation hint: refresh the
full state, including indirectly changed ranks. A future separate frontend can consume an
authenticated SSE stream for server-to-browser notifications, or WebSockets if bidirectional
interaction warrants them, and fetch current state after reconnecting. A socket is transport,
not the durable output or source of truth. No new SSE/WebSocket endpoint is deployed here.

Validation after integration: **479 tests passed**, including all eight combinations, additional
adverse cases, both algorithm update examples, cross-reader refresh/restart, duplicate delivery,
crash rollback, existing-database protection, CLI output and Streamlit interaction tests.
Independent review findings about partial commits and incomplete invalidation IDs were fixed and
covered by regression tests. These checks do not validate real fire predictions or live voice quality.

### Unmute / SLNG / Vonage mock-call test plan

**Goal:** exercise an explicitly simulated readiness conversation with Unmute's
SLNG-hosted target, then verify a Vonage phone connection when the account has
an active SIP trunk and the tester has supplied an authorized destination.
This follows the agreed conversation and Vonage architecture in this session.

The package in `voice-agent/` is a connectivity smoke test. It uses fictional
incident details and is configured to record conversation variables on SLNG; it does not yet
submit answers to FireLine's database, dispatch assistance, or transfer to a
real responder. The existing offline voice demo remains the separate test of
FireLine's validation and persistent follow-up. A working greeting alone does
not prove recognition, reasoning, answer persistence, or two-way phone audio.

- [x] Install checksum-verified Unmute 0.5.5 and VoiceAI CLI 0.1.19 locally
  under ignored `data/tools/`.
- [x] Author `voice-agent/agent.yaml`, `targets.yaml`, `instructions.md` and
  `tools/end_call.yaml`; keep generated builds and credentials ignored.
- [x] Run `unmute validate voice-agent --target slng` and
  `unmute compile voice-agent --target slng`; inspect the generated prompt,
  greeting and conversation variables for the simulated call.
- [x] Verify the SLNG key with read-only requests and preview deployment with
  `unmute deploy voice-agent --target slng --dry-run`. The account returned
  zero agents; no agent ID or outbound connection ID is configured locally.
  This does not establish whether an account-level SIP connection exists.
- [x] Run the regression suite: **492 tests passed**, including persistence
  of live-shaped assistance reports and follow-up tasks after database restart.
- [ ] When account/model prerequisites are met, deploy the dedicated mock
  agent and verify a browser conversation. A Vonage test additionally requires
  an active Manual outbound SIP connection and an explicitly authorized test
  number. Report every unavailable prerequisite without claiming live success.

`fixtures/voice/unmute_smoke.json` contains eight manual scripts for browser
testing: complete answers, assistance, human requests, interruptions, unknown
answers, wrong location, assistance-question polarity and corrections. These
are expected results, not recorded calls. The agent classifies each field as
`yes`, `no` or `inconclusive`, with exact tester quotes. Those values correspond
to FireLine's `true`, `false` and `null`. Initial message receipt and final
readback acknowledgement are separate. Hosted memory capture, model runtime
availability and actual audio interruptions still need a browser test.

Durable hosted answer capture still needs a verified provider result/tool
payload connected to FireLine's existing result ingestion endpoint. Polling
call lifecycle alone does not prove answers were saved. This smoke package has
no FireLine submission tool and no real human-transfer tool; requests for a
person are recorded and the tester is told that no transfer occurs.

Readiness now exposes `reported_can_self_evacuate`,
`reported_transport_available` and `reported_needs_assistance` separately from
operational `mode`. An evidenced assistance need for a confirmed household
creates an `arrange_assistance` proposal alongside human review. It does not
dispatch resources or reserve capacity. Non-synthetic results still require
review because provider confidence is unverified; even complete answers do
not automatically make operational readiness determined. Route, capacity and
timing checks remain separate. The capacity validation and UTF-8 fixes from
readiness PR #8 are included while retaining this branch's road warnings.

The read-only deployment preview proposes creating
`fireline-mock-interview-slng`. Deployment remains pending explicit approval;
no browser session, Vonage call or real human handoff has been verified.

The carrier route is configured in SLNG's Telephony dashboard using Vonage's
termination host and SIP credentials, not a `carrier: vonage` Unmute setting.
Local `VONAGE_API_KEY` alone does not establish a SIP connection. Use a dedicated
mock agent and recheck its outbound connection after deployment. References:
[Unmute hosted target](https://github.com/slng-ai/unmute/blob/main/docs-site/targets/slng.mdx),
[SLNG outbound setup](https://docs.slng.ai/guides/agents/telephony/outbound),
[Vonage SIP setup](https://developer.vonage.com/en/sip/sip-dashboard).
