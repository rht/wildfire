# FireLine v4 MVP validation (readme.md section 11)

Validation date: 2026-09-19. Written by `scripts/validate.py --write`; rerun it to refresh. Every check ran offline on the committed fixtures (no network, no Deepfire credentials, no LLM key). Outcomes and numbers below are what the script measured on that run; nothing here is a claim beyond those measurements. Fixture content is synthetic except the Gencat register extract in `fixtures/real_area/`.

## Summary

| check | outcome | measured |
|---|---|---|
| Coverage/matching | pass | 180 facilities (111 located, 69 unlocated), {'aerodrome': 5, 'campsite': 42, 'care_home': 28, 'fire_station': 6, 'hospital': 1, 'research_facility': 1, 'school': 97} |
| Geometry | pass | overlap 0.0 m; separated 2000.0 m (error 0.00 m) |
| Priority | pass | window 60.0 = expected 60.0 min; order ['far_downwind', 'near', 'slow_evac', 'quick_evac']; exhausted first ['negative', 'zero', 'open']; fixture 11 ranked / 2 review |
| Updates | pass | 12 assets changed on seq 2; missing ['fixture:pou_del_glac'], 2 tasks kept |
| Tasks | pass | suggestions 7 then 0; 8 tasks survive reload |
| Agent (FakeLLM) | pass | 8 runs: 4 proposals {'capacity': 2, 'asset_type': 1, 'criticality_tier': 1}, 6 escalations, 0 occupancy-from-capacity |
| Agent (live model) | not verified |  |
| Criticality | pass |  |
| Latency | pass | median 0.172 s for 180 assets (target < 60 s); source age 300 s |
| Stale data | pass | None->unavailable, 0->current, 3599->current, 3600->stale, 21599->stale, 21600->unavailable, -60->current |

9 pass, 0 fail, 1 not verified.

## Recorded outcomes

### Coverage/matching: pass

- file fixtures/real_area/assets_gavarres.json: area Gavarres, bbox [2.85, 41.8, 3.2, 42.05], extracted 2026-09-19
- facilities per asset_type: {'aerodrome': 5, 'campsite': 42, 'care_home': 28, 'fire_station': 6, 'hospital': 1, 'research_facility': 1, 'school': 97}
- by register: {'gencat:campsites (t2h3-cgys)': 42, 'gencat:care_homes (ivft-vegh)': 27, 'gencat:equipaments (8gmd-gz7i)': 14, 'gencat:schools (kvmv-ahh4)': 97}
- located 111, unlocated (null coordinates, location_unknown) 69, total 180; envelope counts consistent: True
- class_ambiguous: 1; unresolved class (asset_type unknown / value_unknown): 0
- records with a register capacity: 69 (unlocated care homes / campsites); records with a headcount (estimated_occupancy): 87 (schools, enrolled pupils from gencat:schools_enrolment); located rows with occupancy_unknown: 24
- footprint geometries: 0 (every distance is a labelled point fallback)
- municipalities: 30
- sample of 5 located names: Parc de Bombers de la Vall d'Aro (fire_station, Castell d'Aro, Platja d'Aro i s'Agaró); Escola Vall d'Aro (school, Castell d'Aro, Platja d'Aro i s'Agaró); Escola La Pedra Dreta - ZER Empordanet-Gavarres (school, Cruïlles, Monells i Sant Sadurní de l'He); Escola Carrilet (school, Palafrugell); Escola Bressol El Petit Montgrí (school, Torroella de Montgrí)
- unmatched / ambiguous records: equipaments:3620472 'Palamós Gent Gran. Serveis de Salut Integrats Baix Empordà AIE': register category: Salut|Centres sanitaris|4. Centres sociosanitaris|
- register limitation: the care-home (ivft-vegh) and campsite (t2h3-cgys) registers carry no coordinates, so those rows are matched by municipality name, not geometry; located rows are almost all schools and carry no capacity (fixtures/real_area/README.md)

### Geometry: pass

- overlap (asset point inside a 1 km square fire, EPSG:25831): distance 0.0, intersects True
- separated (asset point 2000 m east of the square, exchanged as WGS84): distance 2000.0 m, error 0.00 m (tolerance 5 m), intersects False
- footprint asset (100 m square, west edge 2000 m from the fire): distance 2000.0 m, fallback note: None
- missing location: (None, None, 'asset location unknown'); missing fire geometry: (None, None, 'fire geometry unavailable')
- build_snapshot with null coordinates: distance None, intersects None, review_reasons ['location_unknown', 'exposure_unknown', 'forecast_unavailable']
- point fallback label in sources[fields ∋ distance_to_fire_m].notes: point-only asset ['point fallback: facility footprint missing'], footprint asset ['minimum distance in EPSG:25831 to the fire footprint']
- committed fixture synthetic_gavarres_0001: 12/12 located assets carry 'point fallback: facility footprint missing'
- validate_snapshot errors: none

### Priority: pass

- policy forecast-evacuation-window-v2: buffer 30 min, now = snapshot as_of, arrival basis: p10, else p50, else the provider's single estimate (its declared basis); evacuation durations from evacuation-proto-2026-09-19 unless overridden
- arithmetic (arrival +180 min, evacuation 90 min, buffer 30): time_to_impact 180.0, latest start 60.0, remaining window 60.0 (expected 60.0), status window_open; with now 30 min later the window is 30.0: True
- farther outranks nearer: order ['far_downwind', 'near', 'slow_evac', 'quick_evac'] with windows {'far_downwind': 30.0, 'near': 180.0, 'slow_evac': 190.0, 'quick_evac': 270.0} (far_downwind at 4000 m arrives at +150 min and outranks near at 800 m arriving at +300 min; slow_evac at 1500 m with a 180 min care-home evacuation outranks quick_evac at 600 m): True
- zero/negative windows: order ['negative', 'zero', 'open'], (window, status) {'negative': (-60.0, 'window_exhausted'), 'zero': (0.0, 'window_exhausted'), 'open': (120.0, 'window_open')}; needs_review empty: True
- stable ties (identical timing): ['tie_b', 'tie_d', 'tie_a', 'tie_c'] = nearer distance first, then asset_id, null distance last: True
- missing estimates: no forecast -> needs_review ['forecast_unavailable']; no evacuation -> needs_review ['evacuation_unknown']; forecast without provenance -> needs_review ['forecast_unavailable']: True
- missing distance with known timing: queue ranked, window 60.0 (does not block ranking): True
- agreement with contact_priority.rank_contacts (readme 16) on 8 assets: ranked (id, window) pairs identical True, review sets identical: True
- committed fixture synthetic_gavarres_0001: 11 ranked, 2 needs_review (reasons {'forecast_unavailable': 2}); ranks contiguous and windows non-decreasing: True
- these checks verify the implementation, not the accuracy of supplied forecasts or evacuation estimates

### Updates: pass

- seq 1 accepted: True; duplicate seq 1: accepted False (duplicate snapshot_id synthetic_gavarres-0001)
- seq 2 accepted: True, 12 assets changed, 6 open tasks flagged; replay of seq 1 under a new id: accepted False (sequence 1 <= last accepted 2 (synthetic_gavarres-0002)); last sequence after the replay {'sequence': 2, 'snapshot_id': 'synthetic_gavarres-0002'}
- top-3 before (seq 1): ['sant_pol 5 min', 'pou_del_glac 60 min', 'escola_cruilles 120 min']
- top-3 after (seq 2): ['sant_pol -135 min', 'pou_del_glac -120 min', 'can_xic -45 min']; ranked order changed: True
- source age preserved (synthetic, escola_cruilles unchanged at 1228.7 m): non-fire, non-forecast sources identical True; fire source observed_at 2026-07-03T08:00:00+00:00 -> 2026-07-03T10:00:00+00:00
- source age preserved (real-area equipaments:10213412): newest_fetched_at 2026-09-19T11:46:36+00:00 and the register provenance identical in both snapshots while as_of advances 2026-07-03T13:20:01.981000+00:00 -> 2026-07-03T15:32:24.822000+00:00: True (the register extract of 2026-09-19 postdates the recorded real fire of 2026-07-03/04, so these fixtures are not an as-of replay)
- missing asset (seq 3 = seq 2 without fixture:pou_del_glac): missing_asset_ids ['fixture:pou_del_glac']; tasks {'task-0001': ('open', None), 'task-0002': ('open', None)} -> {'task-0001': ('open', None), 'task-0002': ('open', None)}; flagged ['task-0001', 'task-0002']; exposure present False; asset_missing events 1
- 7 tasks suggested from seq 1 review reasons

### Tasks: pass

- suggestions from seq 1: first call 7 tasks, second call 0; max tasks per (asset, action, reason) key 1
- assigned task-0001 (fixture:pou_del_glac, confirm_occupancy) to team_bisbal_1 -> status assigned, then in_progress
- after seq 2 (accepted True, 7 tasks flagged): team team_bisbal_1, status in_progress, affected_by synthetic_gavarres-0002
- after reload from sqlite file: record identical True; 8 tasks, 21 events persisted; replay of seq 2 after reload accepted False
- busy team rejected: 'team busy: team_bisbal_1 has active task task-0001'
- capability mismatch rejected: "capability mismatch: task needs ['occupancy_check'], team team_medical_1 has ['medical']"
- unavailable team rejected: 'team unavailable: team_palafrugell_1'
- request_resources task task-0008: status blocked, assigned_team_id None, blocking question 'how many vehicles are available?'

### Agent (FakeLLM): pass

- LLM: FakeLLM (offline, scripted; llm_mode ['fake']). A live-model run is pending an ANTHROPIC_API_KEY; scripts/investigate.py replays fixtures/agent/prerecorded_investigation.json until then
- 8 investigations over 7 flagged fixture assets of synthetic_gavarres_0001 plus the no-evidence case fixture:mas_nou; evidence cache fixtures/evidence.json (8 entries, plus data/registers/*.json present locally); 0 assets flagged only for ('forecast_unavailable', 'evacuation_unknown') left to the review queue (no FakeLLM script; forecast is the producer's, evacuation duration is the analyst's evacuation control)
- proposals 4 by field {'capacity': 2, 'asset_type': 1, 'criticality_tier': 1}; escalations 6; per asset {id: (reasons, proposals, questions)}: {'residencia_sense_coordenades': (['location_unknown', 'exposure_unknown', 'forecast_unavailable'], 0, 1), 'mas_nou': (['occupancy_unknown'], 0, 1), 'pou_del_glac': (['occupancy_unknown', 'occupancy_seasonal'], 1, 1), 'escola_cruilles': (['occupancy_seasonal'], 0, 1), 'mas_pla': (['occupancy_seasonal'], 0, 1), 'camping_gavarres': (['class_ambiguous'], 1, 0), 'residencia_la_bisbal': (['occupancy_unknown'], 1, 1), 'hospital_palamos': (['criticality_unassessed'], 1, 0)}
- estimated_occupancy proposals: 0 (evidence carries a headcount field: False); every occupancy proposal is field 'capacity': True
- post-check ok for all: True; steps <= 6 for all: True; max steps 5
- must-escalate cases {id: (proposals, questions)}: {'mas_nou': (0, 1), 'escola_cruilles': (0, 1), 'mas_pla': (0, 1)}; assets left with neither proposal nor question: none
- capacity-as-occupancy guard: refused: the evidence states a capacity (places), not a headcount; capacity mus...
- confirmation of prop-003-residencia_la_bisbal (capacity 48): overrides persisted 1, re-ranked queue ranked, occupancy_basis 'analyst override', remaining window 240.0, timing known True
- evacuation_min proposal prop-005-residencia_la_bisbal confirmed: evacuation 150.0 min from 'analyst override: phone call with the director', review_reasons ['occupancy_unknown'], window 240.0: True
- held-out examples: no investigation examples were held back from prompt development; the FakeLLM is a script, so a held-out check is only meaningful on the live model and remains not verified

### Agent (live model): not verified

- not run: --live was not passed, so no tokens were spent; supported proposals, correct escalations and unsupported claims are unmeasured for the real model

### Criticality: pass

- policy criticality-proto-2026-09-19: tiers routine (rank 0, x1.0, min 0 factor(s)), elevated (rank 1, x3.0, min 1 factor(s)), high (rank 2, x10.0, min 1 factor(s)), exceptional (rank 3, x30.0, min 2 factor(s))
- factors (closed enum): irreplaceable_holdings, national_research_infrastructure, sole_regional_service, emergency_response_capability, hazardous_materials, network_single_point_of_failure
- assessed classes: hospital, research_facility, university, fire_station, aerodrome; FEATURES['asset_criticality'] = True
- inflation guard: exceptional+1 -> refused; exceptional+2 -> accepted; high+0 -> refused; routine+0 -> accepted; high+1 -> refused
- producer: tier is null with the flag off and on (True); the reason appears only with the flag on (off ['occupancy_unknown'], on ['occupancy_unknown', 'criticality_unassessed'])
- contact order with every asset at the top tier is identical to the untiered order: True (72 ranked); strategic view holds 180, untiered snapshot holds 0
- ranked_sort_key mentions no criticality field, and no code reads loss_multiplier (the euro ledger is not implemented): True
- strategic assets in the committed extract: 13 {'aerodrome': 5, 'fire_station': 6, 'hospital': 1, 'research_facility': 1}; nearest to the fire in gavarres_real_0002: Parc de Bombers de Calonge i Sant Antoni 450 m; Parc de Bombers de la Vall d'Aro 4998 m; IRTA Monells (IRTA-Monells) 5004 m

### Latency: pass

- input: 180 real-area assets (fixtures/real_area/assets_gavarres.json) + recorded update 20260703T100500Z_satellite-perimeters.json (deepfire:satellite-perimeters, SYNTHETIC content, observed 2026-07-03T10:00:00+00:00, received 2026-07-03T10:05:00+00:00) through fire_input.load_recorded
- processing time (receipt -> snapshot built -> scored -> tasks suggested), 3 runs: [0.174, 0.145, 0.172] s; median 0.172 s
- stages of run 1: build 0.094 s, score 0.030 s, apply+suggest 0.050 s; validate errors 0
- result: 0 ranked, 180 needs_review; 103 assets changed vs seq 1, 0 new suggestions on the update (ranked queue empty: no fire-spread run exists for the recorded July incident, so these inputs carry no per-location forecast arrival and every asset is a review item until a forecast covers it)
- source age (observation -> snapshot as_of 2026-07-03T10:05:00+00:00): 300 s, data_status current; metrics {'source_age_s': 300.0, 'processing_s': 0.17211915797088295} (separate numbers, never combined)
- target < 60 s processing for the selected area: met on x86_64, Python 3.14.7

### Stale data: pass

- config.FRESHNESS {'stale_after_s': 3600, 'unavailable_after_s': 21600}
- age s -> status: None -> unavailable, 0 -> current, 3599 -> current, 3600 -> stale, 21599 -> stale, 21600 -> unavailable, -60 -> current
- all boundaries as configured: True; snapshot.freshness (producer) agrees with fire_input.data_status (consumer): True

## Not verified

- Live Deepfire authentication and a real response: no credentials were available; fixtures/fire/deepfire is synthetic content in the documented response shape (fixtures/fire/deepfire/README.md).
- Live LLM investigation: the live agent check did not run (no key, or --live not passed), so the agent check ran the scripted FakeLLM and the prerecorded fixture is a labelled FakeLLM transcript. Supported proposals, correct escalations and unsupported claims are unmeasured for the real model.
- Forecast accuracy and evacuation estimates: no provider forecast was validated; the ranking checks use constructed synthetic arrivals and policy evacuation durations, and distance-based exposure is not time to impact.
- Evacuation decisions: nothing here validates an evacuation or confinement decision, lead time against historical response, or superiority over historical emergency response.
- Operational validity of the contact policy: the priority checks verify the window arithmetic, ordering, ties and missing-input handling of the implementation, not that forecast-evacuation-window-v2 with the prototype evacuation durations (evacuation-proto-2026-09-19) orders contacts correctly.
- Historical as-of replay: the recorded-input run is a recorded-input demo with synthetic coverage; input availability times were not established for all inputs.
