# Handoff 003: per-asset criticality from the investigation agent

Written 2026-09-19 on branch `claude/asset-criticality` (from `origin/main` at `1b34370`).
Implemented, tested and validated live; this records the decisions and the limits, not a plan.

## Why

The value model was per-class and nothing else. `VALUE_POLICY` gives every school 0.8 and every
hospital 1.0, and the euro ledger proposed in handoff 002 keeps that shape: one replacement value
per class, with the caveat written into it that these are "per-class assumptions with no per-asset
basis". Nothing in the repo could say that one particular building is worth more than its class —
the Barcelona Supercomputing Center, a nuclear reactor, a biobank, the fire brigade's own station.

Three facts decided the design.

1. **The pipeline could not see such a building at all.** `feeds.ASSET_CLASS_RULES` mapped five
   substrings of the `equipaments` category column to classes and dropped every other row. That
   discarded all 15 `Recerca`, 15 `Parcs de Bombers`, 8 aerodrome and 16 `Universitats` rows the
   register already held.
2. **The LLM is outside the snapshot pipeline**, which is why `make snapshots` is deterministic —
   structurally, not by discipline. `scripts/make_snapshots.py` has no LLM import.
3. **The agent's grounding rules are strict.** `postcheck_numbers` replaces the model's message if
   any number in it is absent from a tool result, and `validate.py _supported` requires every
   proposal's `quoted_snippet` to appear verbatim in the loop's own tool results. "The BSC is
   extremely valuable" is world knowledge, not a quotable register field.

## Decisions taken with the user on 2026-09-19

1. **Criticality drives a separate strategic view, never the contact queue.** readme 6 says
   "Property value does not override contact urgency"; a criticality that reordered the queue would
   mean a building outranking a care home. The contact queue answers "who do I phone first to get
   people out"; the strategic view answers "where would the loss outlast the incident". Both rules
   stand, and readme 6 now names criticality explicitly.
2. **The evidence corpus is cached Wikipedia + Wikidata** (`fixtures/notability.json`), fetched by
   `scripts/fetch_data.py notability` into the existing `data/cache/` shape. This keeps the
   verbatim-snippet rule intact instead of weakening it, and it discriminates the way the feature
   needs: BSC, CEAB, ICO and IRTA return substantive intros, `Escola Joan de Margarit` returns
   nothing. Rejected: register fields only (cannot tell a regional cancer centre from a clinic) and
   a `model_judgement` provenance class exempt from the snippet rule (weakens the one guarantee
   that makes the agent trustworthy).
3. **A tier reaches an asset the way occupancy does**: agent proposal → analyst confirmation →
   SQLite override → `priority.apply_overrides`. The producer never asserts a tier. So a snapshot
   built with the flag off is unchanged, and determinism stays structural.

## What was built

Behind `FEATURES["asset_criticality"]`, off by default.

**Admission.** Five substring rules admit `research_facility`, `fire_station`, `aerodrome` and
`university`, with matching rows in `VALUE_POLICY`, `EVACUATION_POLICY`, `LEAD_TIME_MIN` and
`LOAD_TIME_MIN` (`exposure.build_asset_table` raises on a class missing from the last). The Gavarres
extract goes from 168 assets (99 located) to 180 (111 located). `Parc de Bombers de Calonge i Sant
Antoni` is now the nearest asset to the fire in sequences 2 and 3, at 450 m, with a remaining window
of −35 min.

**Contract.** `criticality_tier`, `criticality_factors`, `criticality_basis` and the review reason
`criticality_unassessed`. The three keys are optional in `validate_snapshot` like the v1.1 timing
keys, so older snapshots stay valid. All three are null together.

**Policy.** `CRITICALITY_POLICY` holds four ordered tiers with a `loss_multiplier` for the euro
ledger, a closed six-factor enum, `min_factors` per tier, and `assess_classes`.

**Agent.** A fifth tool `lookup_notability` (offline, `STATELESS_TOOLS`) and a fifth proposal field
`criticality_tier`, whose value is `{"tier", "factors"}` so a tier can never be confirmed without
the factors that justify it. Both the tool and an analyst confirmation validate through the same
`priority.criticality_value`.

**UI.** A criticality column on both tables and a Strategic exposure panel, captioned with why it is
not a contact order and how many assets remain unassessed.

## Turning the flag on (2026-09-20)

`FEATURES["asset_criticality"]` is now `True`, at the user's request, and the committed snapshots
were rebuilt with it. The only change to them is the added `criticality_unassessed` review reason on
the 13 assessed-class assets of the real-area extract; no other field moved.

Two checks had written "the flag is off" where they meant "with the flag off". Both now name each
state explicitly (`tests/test_criticality.py` and `check_criticality`'s producer probe build an
off-cfg the way they already built an on-cfg), so they measure the gate whichever way it is set.
`check_agent_live`'s general loop now skips `criticality_unassessed` alongside the timing pair: it
has its own block on the real-area snapshot, and the general loop should cover the same assets
whichever way the flag is set.

## The truncation bug the demo found

Turning the flag on made the live check fail, on a real defect that had been hiding as a model
failure. Two of four criticality investigations ended with the loop reading its evidence and then
returning an empty assistant message, proposing nothing.

The cause was `finish_reason`, which `from_openai_message` discarded. A response truncated at
`max_tokens` became `stop_reason="end_turn"` — indistinguishable from a considered finish. And
`MAX_TOKENS` was 2048 while DeepSeek spends its budget on reasoning before it writes anything: the
ordinary investigation steps use 9-20 reasoning tokens, but the criticality judgement was measured at
1226 and then at the full 2048, at which point the turn carried no text and no tool call.

This is the worst shape a bug can take here. A truncation reaching the analyst as silence reads as
"the agent found nothing to say about this hospital", which is exactly what a considered `routine`
looks like. Both halves are fixed. A truncated turn keeps `stop_reason="max_tokens"`, and
`NebiusLLM.create` raises when the truncation produced nothing usable. `MAX_TOKENS` is 8192, set from
a measurement rather than a guess: over 26 criticality investigations (the 13 assessed assets of
`gavarres_real_0002`, twice, with the cap raised out of the way) the ordinary steps used 9-20
reasoning tokens, the two judgement cases 1600-2400, and the largest single turn 2899 completion
tokens. 4096 was not enough — the first live run after that change failed on exactly those two
assets, this time saying so. `check_agent_live` now also counts empty final messages and missing tiers in
the criticality block, so the behaviour stays measured rather than disappearing with the fix.

## The user's own example, probed

`Barcelona Supercomputing Center` at its real address, against three peers of the same class, the
same class `value_score` and the same 2500 m: BSC `high/national_research_infrastructure` quoting the
ca.wikipedia intro, CEAB `high/national_research_infrastructure`, ICRA
`high/national_research_infrastructure`, and `Escola Joan de Margarit` `routine` from its class line.

The layer separates a facility with a substantive public record from one without. It did not separate
BSC from a mid-size water-research institute, and reached for no `exceptional` tier. Read it as
"this building is more than its class", not as a ranking among notable institutions.

A first probe put BSC at a Baix Empordà village's coordinates. The model refused the record, saying
it describes the Torre Girona installation in Barcelona, "a different place, so it is not evidence
about this site", and returned `routine`. The geographic-consistency rule working, on a badly built
probe.

## Two things the build changed about the design

**`dispatch` had a hardcoded `name != "lookup_facility"`** deciding which tools receive a workbench.
The new tool therefore got a `TypeError` returned into the model's error channel, which surfaced in
the first live run as an unexplained "no notability record found". Now `STATELESS_TOOLS`. Worth
remembering: the error channel is deliberately soft, so a wiring bug looks like a model failure.

**The prompt was calibrated against the live model, not written blind.** The first DeepSeek run
returned `routine` for everything, reasoning each time that the notability record described the
parent body rather than this site. That is the safe failure and the wrong answer: a site of a
national institute inherits its character, and a fire station is emergency response capability by
class with no record needed at all. The playbook now states both, and says to take one tier lower
when relying on a parent record. Second run: the fire station came back
`high/emergency_response_capability` quoting its own class line, IRTA Monells
`elevated/national_research_infrastructure`.

## Verification

- 379 tests pass; `tests/test_criticality.py` covers the policy shape, the inflation guard, the flag
  gating, validation, the override and the strategic queue; `tests/test_notability.py` covers the
  corpus.
- `make snapshots` byte-identical across two runs.
- `scripts/validate.py`: 10 pass, 0 fail, 0 not verified, including a live Nebius run.
  `check_criticality` measures the guards rather than asserting them: tiering all 180 assets at the
  top tier leaves the 72-asset contact order identical, `ranked_sort_key` mentions no criticality
  field, and nothing reads `loss_multiplier`.
- Live `deepseek-ai/DeepSeek-V4.1-Flash`: 4/4 investigations, 4/4 proposals valid against the
  policy, 4/4 supported by a verbatim snippet.

## Known limits

- **There is no euro figure.** The handoff 002 ledger is not implemented, so `loss_multiplier` is
  defined and read by nothing. Criticality today is an ordinal tier and a set of named factors.
- **The tiers are the model's judgement, not measured accuracy.** This repo holds no ground truth
  for "how critical is IRTA Monells", and `VALIDATION.md` says so. Every tier is a proposal awaiting
  an analyst.
- **The live check is intermittently red on this model, and both causes are the guards working.**
  Across six `--live` runs on 2026-09-20 the committed `VALIDATION.md` run is green, but two others
  were not: once the number post-check replaced a final message about IRTA Monells (a number in the
  prose that was in no tool result — the analyst saw the replacement, not the number), and once a
  criticality snippet was not verbatim in its own tool results. Neither reproduced in four repeats of
  the same asset. Treat a red live check as a run to read, not a build to roll back: the detail lines
  now name the asset and the failing measure in every case.
- **Borderline tiers vary between runs, at temperature 0.** Seen again on 2026-09-20: over the two
  seeding runs and two live validations, Hospital de Palamós came back `high/sole_regional_service`
  three times and `routine` once. Over three live runs the two ends were
  stable — the fire station came back `high/emergency_response_capability` every time (it is derived
  from the class line, not from a record) and the Heliport `routine` every time (its only near
  record is a different airport). The judgement cases moved: IRTA Monells was `elevated` twice and
  `routine` once, Hospital de Palamós escalated twice and came back `high/sole_regional_service`
  once. The variation is within one tier and always downward-safe, but a demo should not promise a
  specific tier for those two. This is an argument for the analyst-confirmation gate, not against
  the layer.
- **Notability lookup is containment-scored, which cuts both ways.** A match now requires one name
  to contain the other, so `lookup("Heliport de Costa Brava Centre")` returns nothing rather than
  the Girona airport record 30 km away. The same rule means `lookup("Parc de Bombers de la Pera")`
  returns nothing too: the regional `Bombers de la Generalitat` record is legitimate parent evidence
  for a local station, and it is no longer reachable. Fire stations are unaffected because their
  factor comes from the class line, but the parent-evidence path only works when the parent's name
  is a token of the site's ("IRTA Monells" finds "IRTA"), not when it is a common noun.
  `lookup("ICO Girona")` likewise misses the ICO record; a per-record alias list would fix it, and
  the corpus has no alias field today.
- **A record about a parent body is weaker evidence than a record about the site**, and the corpus
  usually only has the parent. The prompt asks for one tier lower in that case, which is a
  convention, not a measurement.
- **Wikipedia notability is a proxy for importance, not importance.** A facility can matter greatly
  and have no article; the layer will call it `routine`. It fails towards the class average, which
  is the right direction but is a real blind spot for private industrial sites.
- **`assess_classes` is a cost filter.** Schools, campsites, care homes, camps, masies and nuclei
  never enter the criticality queue automatically, because the registers hold hundreds of
  near-identical rows. An analyst can still point the agent at any asset by hand. A specialised
  regional care unit would be missed by default.
- **The class policy rows for the four new classes are assumptions**, like every other row in those
  tables. A fire station's 35-minute "evacuation" is a relocation time for a crew that is mobile by
  definition, not an evacuation of occupants; the `assumptions` string says so.

## What the corpus build forced

- **The fetch must batch.** An anonymous caller gets ~10 requests before Wikimedia returns
  `429 x-envoy-ratelimited`; 283 queries x 3 languages would have taken hours one title at a time.
  `prop=extracts` takes 20 titles per request and `wbgetentities` 50, bringing the corpus to ~45
  requests. Consequence: a cache entry covers a whole batch, so `--cached-only` only replays a run
  whose query list yields the same batches.
- **`--cached-only` could silently write a worse corpus.** `_is_institution` keeps an untyped
  record, so a run whose Wikidata pass never fired resolved *more* queries and wrote a larger,
  unfiltered corpus with the junk back in ("Barrufets" the Smurfs, "ETG"). It did not error. Seen in
  practice against a cold entity cache. `scripts/fetch_data.py notability` now refuses to write when
  no record in the run carries a Wikidata entity.
- **An acronym must spell itself out.** Requiring the article to contain the acronym verbatim is not
  enough: `IRE` (Institut de Recerca Educativa) lands on the Institute of Radio Engineers, whose
  article is *titled* `IRE`, so the acronym matched itself. The rule is now that an acronym query
  must resolve to a title that is not the acronym. This costs the CERN/NASA shape, whose common name
  is its initials; none appears in this corpus, and the full register name stays a query of its own.

## Follow-ups worth taking

- Implement the handoff 002 euro ledger and have it read `loss_multiplier`. That is the only change
  that makes the multiplier mean anything.
- A `not_reached_in_horizon` review reason, so the UI can distinguish it from
  `forecast_unavailable` (already noted in handoff 002).
- Per-site notability: Wikidata has items for some individual research stations. Resolving the site
  rather than the parent would remove the "one tier lower" convention.
- A held-out evaluation of the tiers. Twenty facilities scored by a domain expert would turn "the
  model's judgement" into a measurement, and would be the honest basis for raising any tier's
  `loss_multiplier`.

## Files

- `fireline/config.py` (`FEATURES`, four class tables, new `CRITICALITY_POLICY`)
- `fireline/feeds.py` (`ASSET_CLASS_RULES`), `fireline/snapshot.py` (keys, producer, validation),
  `fireline/priority.py` (`criticality_value`, `_apply_one`, `strategic_queue`)
- `fireline/agent.py` (`lookup_notability`, `STATELESS_TOOLS`, `_check_value`, `SYSTEM_PROMPT`),
  `fireline/llm.py` (FakeLLM branch), `fireline/notability.py`
- `fireline/app.py`, `fireline/ui_state.py`
- `scripts/fetch_data.py` (`notability`), `scripts/validate.py` (`check_criticality`, live coverage)
- `fixtures/notability.json`, `fixtures/snapshots/*`, `fixtures/real_area/*`
- `tests/test_criticality.py`, `tests/test_notability.py`
- `readme.md` sections 2, 4, 5.2, 6, 8; `CONTRACTS.md`; `VALIDATION.md`
