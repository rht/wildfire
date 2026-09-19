# FireLine v4 MVP validation (readme.md section 11)

Validation date: 2026-09-19. Written by `scripts/validate.py --write`; rerun it to refresh. Every check ran offline on the committed fixtures (no network, no Deepfire credentials, no LLM key). Outcomes and numbers below are what the script measured on that run; nothing here is a claim beyond those measurements. Fixture content is synthetic except the Gencat register extract in `fixtures/real_area/`.

## Summary

| check | outcome | measured |
|---|---|---|
| Coverage/matching | pass | 168 facilities (99 located, 69 unlocated), {'campsite': 42, 'care_home': 28, 'hospital': 1, 'school': 97} |
| Geometry | pass | overlap 0.0 m; separated 2000.0 m (error 0.00 m) |
| Priority | pass | weights sum 1.0, 10 ranked / 3 review, scores [0.260, 0.653] |
| Updates | pass | 11 assets changed on seq 2; missing ['fixture:pou_del_glac'], 2 tasks kept |
| Tasks | pass | suggestions 7 then 0; 8 tasks survive reload |
| Agent (FakeLLM) | pass | 7 runs: 3 proposals {'capacity': 2, 'asset_type': 1}, 6 escalations, 0 occupancy-from-capacity |
| Agent (live model) | not verified |  |
| Latency | pass | median 0.061 s for 168 assets (target < 60 s); source age 300 s |
| Stale data | pass | None->unavailable, 0->current, 3599->current, 3600->stale, 21599->stale, 21600->unavailable, -60->current |

8 pass, 0 fail, 1 not verified.

## Recorded outcomes

### Coverage/matching: pass

- file fixtures/real_area/assets_gavarres.json: area Gavarres, bbox [2.85, 41.8, 3.2, 42.05], extracted 2026-09-19
- facilities per asset_type: {'campsite': 42, 'care_home': 28, 'hospital': 1, 'school': 97}
- by register: {'gencat:campsites (t2h3-cgys)': 42, 'gencat:care_homes (ivft-vegh)': 27, 'gencat:equipaments (8gmd-gz7i)': 2, 'gencat:schools (kvmv-ahh4)': 97}
- located 99, unlocated (null coordinates, location_unknown) 69, total 168; envelope counts consistent: True
- class_ambiguous: 1; unresolved class (asset_type unknown / value_unknown): 0
- records with a register capacity or headcount: 69 (all unlocated care homes / campsites); located rows with occupancy_unknown: 99
- footprint geometries: 0 (every distance is a labelled point fallback)
- municipalities: 26
- sample of 5 located names: Hospital de Palamós (hospital, PALAMÓS); Escola Torres Jonama (school, Palafrugell); CEE Els Àngels (school, Palamós); CFA Torroella de Montgrí (school, Torroella de Montgrí); Escola Bressol El Petit Montgrí (school, Torroella de Montgrí)
- unmatched / ambiguous records: equipaments:3620472 'Palamós Gent Gran. Serveis de Salut Integrats Baix Empordà AIE': register category: Salut|Centres sanitaris|4. Centres sociosanitaris|
- register limitation: the care-home (ivft-vegh) and campsite (t2h3-cgys) registers carry no coordinates, so those rows are matched by municipality name, not geometry; located rows are almost all schools and carry no capacity (fixtures/real_area/README.md)

### Geometry: pass

- overlap (asset point inside a 1 km square fire, EPSG:25831): distance 0.0, intersects True
- separated (asset point 2000 m east of the square, exchanged as WGS84): distance 2000.0 m, error 0.00 m (tolerance 5 m), intersects False
- footprint asset (100 m square, west edge 2000 m from the fire): distance 2000.0 m, fallback note: None
- missing location: (None, None, 'asset location unknown'); missing fire geometry: (None, None, 'fire geometry unavailable')
- build_snapshot with null coordinates: distance None, intersects None, review_reasons ['location_unknown', 'exposure_unknown']
- point fallback label in sources[fields ∋ distance_to_fire_m].notes: point-only asset ['point fallback: facility footprint missing'], footprint asset ['minimum distance in EPSG:25831 to the fire footprint']
- committed fixture synthetic_gavarres_0001: 12/12 located assets carry 'point fallback: facility footprint missing'
- validate_snapshot errors: none

### Priority: pass

- policy priority-proto-2026-09-19: weights {'proximity': 0.5, 'size': 0.3, 'value': 0.2} (sum 1.0), proximity scale 5000 m, size scale 300 people, capacity proxy True
- synthetic_gavarres_0001: 10 ranked, 3 needs_review, 3 flagged-but-scored; scores in [0.2600, 0.6532], 36 component values all in [0, 1]: True
- ranks 1..10 contiguous and scores non-increasing: True
- stable ties: equal inputs given as [tie_b, tie_a] rank as ['fixture:tie_a', 'fixture:tie_b'] with scores [0.66, 0.66]
- directional: base 0.6600; 2000->1000 m 0.7600; intersecting 0.8600; capacity 200->260 0.7200; headcount 260 0.7200; school->hospital value 0.7000
- needs-review queue: ['residencia_sense_coordenades', 'residencia_la_bisbal', 'pou_del_glac'] distances [None, 1188.3, 1761.5] (exposure-unknown first, then ascending)
- size from capacity proxy (labelled in score_components.size.proxy) for: ['escola_cruilles', 'hospital_palamos', 'camping_gavarres', 'vall_repos']
- these checks verify the implementation, not the operational validity of the weights

### Updates: pass

- seq 1 accepted: True; duplicate seq 1: accepted False (duplicate snapshot_id synthetic_gavarres-0001)
- seq 2 accepted: True, 11 assets changed, 5 open tasks flagged; replay of seq 1 under a new id: accepted False (sequence 1 <= last accepted 2 (synthetic_gavarres-0002)); last sequence after the replay {'sequence': 2, 'snapshot_id': 'synthetic_gavarres-0002'}
- top-3 before (seq 1): ['sant_pol 0.653', 'escola_cruilles 0.597', 'sant_sadurni 0.511']
- top-3 after (seq 2): ['sant_pol 0.725', 'escola_cruilles 0.597', 'can_xic 0.516']
- source age preserved (synthetic, escola_cruilles unchanged at 1228.7 m): non-fire sources identical True; fire source observed_at 2026-07-03T08:00:00+00:00 -> 2026-07-03T10:00:00+00:00
- source age preserved (real-area equipaments:3620470): newest_fetched_at 2026-09-19T11:46:36+00:00 and the register provenance identical in both snapshots while as_of advances 2026-07-03T13:20:01.981000+00:00 -> 2026-07-03T15:32:24.822000+00:00: True (the register extract of 2026-09-19 postdates the recorded real fire of 2026-07-03/04, so these fixtures are not an as-of replay)
- missing asset (seq 3 = seq 2 without fixture:pou_del_glac): missing_asset_ids ['fixture:pou_del_glac']; tasks {'task-0006': ('open', None), 'task-0007': ('open', None)} -> {'task-0006': ('open', None), 'task-0007': ('open', None)}; flagged ['task-0006', 'task-0007']; exposure present False; asset_missing events 1
- 7 tasks suggested from seq 1 review reasons

### Tasks: pass

- suggestions from seq 1: first call 7 tasks, second call 0; max tasks per (asset, action, reason) key 1
- assigned task-0006 (fixture:pou_del_glac, confirm_occupancy) to team_bisbal_1 -> status assigned, then in_progress
- after seq 2 (accepted True, 6 tasks flagged): team team_bisbal_1, status in_progress, affected_by synthetic_gavarres-0002
- after reload from sqlite file: record identical True; 8 tasks, 20 events persisted; replay of seq 2 after reload accepted False
- busy team rejected: 'team busy: team_bisbal_1 has active task task-0006'
- capability mismatch rejected: "capability mismatch: task needs ['occupancy_check'], team team_medical_1 has ['medical']"
- unavailable team rejected: 'team unavailable: team_palafrugell_1'
- request_resources task task-0008: status blocked, assigned_team_id None, blocking question 'how many vehicles are available?'

### Agent (FakeLLM): pass

- LLM: FakeLLM (offline, scripted; llm_mode ['fake']). A live-model run is pending an ANTHROPIC_API_KEY; scripts/investigate.py replays fixtures/agent/prerecorded_investigation.json until then
- 7 investigations over 6 flagged fixture assets of synthetic_gavarres_0001 plus the no-evidence case fixture:mas_nou; evidence cache fixtures/evidence.json (8 entries, plus data/registers/*.json present locally)
- proposals 3 by field {'capacity': 2, 'asset_type': 1}; escalations 6; per asset {id: (reasons, proposals, questions)}: {'residencia_sense_coordenades': (['location_unknown', 'exposure_unknown'], 0, 1), 'residencia_la_bisbal': (['occupancy_unknown'], 1, 1), 'pou_del_glac': (['occupancy_unknown', 'occupancy_seasonal'], 1, 1), 'mas_nou': (['occupancy_unknown'], 0, 1), 'escola_cruilles': (['occupancy_seasonal'], 0, 1), 'mas_pla': (['occupancy_seasonal'], 0, 1), 'camping_gavarres': (['class_ambiguous'], 1, 0)}
- estimated_occupancy proposals: 0 (evidence carries a headcount field: False); every occupancy proposal is field 'capacity': True
- post-check ok for all: True; steps <= 6 for all: True; max steps 5
- must-escalate cases {id: (proposals, questions)}: {'mas_nou': (0, 1), 'escola_cruilles': (0, 1), 'mas_pla': (0, 1)}; assets left with neither proposal nor question: none
- capacity-as-occupancy guard: refused: the evidence states a capacity (places), not a headcount; capacity mus...
- confirmation of prop-001-residencia_la_bisbal (capacity 48): overrides persisted 1, rescored queue ranked, occupancy_basis 'analyst override', score 0.6292
- held-out examples: no investigation examples were held back from prompt development; the FakeLLM is a script, so a held-out check is only meaningful on the live model and remains not verified

### Agent (live model): not verified

- not run: no ANTHROPIC_API_KEY in this environment; supported proposals, correct escalations and unsupported claims on held-out examples are unmeasured for the real model

### Latency: pass

- input: 168 real-area assets (fixtures/real_area/assets_gavarres.json) + recorded update 20260703T100500Z_satellite-perimeters.json (deepfire:satellite-perimeters, SYNTHETIC content, observed 2026-07-03T10:00:00+00:00, received 2026-07-03T10:05:00+00:00) through fire_input.load_recorded
- processing time (receipt -> snapshot built -> scored -> tasks suggested), 3 runs: [0.061, 0.063, 0.06] s; median 0.061 s
- stages of run 1: build 0.032 s, score 0.012 s, apply+suggest 0.017 s; validate errors 0
- result: 0 ranked, 168 needs_review; 91 assets changed vs seq 1, 0 new suggestions on the update (ranked queue empty on real coverage: located register rows carry no capacity, so the size component is unknown and every asset is an investigation-queue item)
- source age (observation -> snapshot as_of 2026-07-03T10:05:00+00:00): 300 s, data_status current; metrics {'source_age_s': 300.0, 'processing_s': 0.06092156301019713} (separate numbers, never combined)
- target < 60 s processing for the selected area: met on x86_64, Python 3.14.7

### Stale data: pass

- config.FRESHNESS {'stale_after_s': 3600, 'unavailable_after_s': 21600}
- age s -> status: None -> unavailable, 0 -> current, 3599 -> current, 3600 -> stale, 21599 -> stale, 21600 -> unavailable, -60 -> current
- all boundaries as configured: True; snapshot.freshness (producer) agrees with fire_input.data_status (consumer): True

## Not verified

- Live Deepfire authentication and a real response: no credentials were available; fixtures/fire/deepfire is synthetic content in the documented response shape (fixtures/fire/deepfire/README.md).
- Live LLM investigation: no ANTHROPIC_API_KEY; the agent check ran the scripted FakeLLM and the prerecorded fixture is a labelled FakeLLM transcript. Supported proposals, correct escalations and unsupported claims on held-out examples are unmeasured for the real model.
- Forecast accuracy: no provider forecast was consumed; burn_probability and arrival fields are null in every snapshot and distance-based exposure is not time to impact.
- Evacuation decisions: nothing here validates an evacuation or confinement decision, lead time against historical response, or superiority over historical emergency response.
- Operational validity of the weights: the priority checks verify normalisation, ties and direction of the implementation, not that the prototype policy (priority-proto-2026-09-19) orders analyst attention correctly.
- Historical as-of replay: the recorded-input run is a recorded-input demo with synthetic coverage; input availability times were not established for all inputs.
