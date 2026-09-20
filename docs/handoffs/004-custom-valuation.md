# Handoff 004: per-asset custom valuation from the investigation agent

Written 2026-09-20 on branch `claude/approve-all-toggle` (worktree `.worktrees/approve-all`), the
sibling of handoff 003 on the money side. This records what was built, the decisions the build
forced, what was measured and what is not known — not a plan.

## Why

`VALUE_AT_RISK_POLICY` prices an average building of a class: an assumed replacement cost per square
metre times an assumed built area times 1.4 for fees, VAT and contents. Handoff 002 wrote the caveat
into the policy itself ("rounded placeholders standing in for a per-asset figure; the band they give
is the damage-ratio band only, so the value uncertainty is at least as large"), and handoff 003 left
`CRITICALITY_POLICY["loss_multiplier"]` defined and read by nothing, with "there is no euro figure"
as its first known limit.

Four classes make the per-class shape not merely rough but wrong, and they are exactly the four the
table has no row for at all: `research_facility`, `university`, `aerodrome`, `fire_station`. Within
them one building is not like another. A national supercomputing centre and a university annexe are
both `research_facility`; a euros-per-square-metre table prices them the same, because the machines,
the data and the service the building carries are the value and the shell is a rounding error. No
amount of tuning a class row fixes that: the question is per asset, so the answer has to be.

The agent is already the part of this system that answers per-asset questions from quoted evidence,
under guards that make its answers traceable (`postcheck_numbers`, and `_supported` in
`scripts/validate.py`). Criticality proved the shape works. This applies the same shape to money.

## Decisions the build forced

1. **A bespoke figure is a band, never a point.** The class table carries one
   `replacement_value_eur` per class, and copying that shape per asset would claim a precision the
   evidence cannot carry: a reference saying "about 800,000 EUR per rack" plus a rack count is not a
   valuation to the euro. So the override payload is three amounts and `min_band_ratio` (1.5)
   refuses a high that is not meaningfully above the low, and the width of what is unknown is stated
   rather than rounded away.
2. **`not_valued` is a method, not a failure.** Without it the only way to say "the corpus has
   nothing for this building" is to propose nothing, which is indistinguishable from never having
   looked. It carries no amounts and no components by construction, and the guards refuse it
   carrying either.
3. **The inflation guard is a component count, not a ceiling on judgement.** `min_components` is
   the analogue of `CRITICALITY_POLICY["min_factors"]`: `component_replacement` needs two priced
   parts and they must sum to the mid within 5%, so a bespoke figure cannot rest on one round
   number. `max_eur` is separate and is a misplaced-decimal-point guard, not an opinion about what
   a building is worth.
4. **The euro maths lives in the producer, not in priority.** `snapshot.derive_value_at_risk` takes
   the confirmed mid as `replacement_value_eur` and multiplies each end of the valuation band by the
   matching damage ratio. `fireline/priority.py` still contains no euro arithmetic at all, which is
   what keeps the "no euro reaches the contact queue" claim structural rather than a matter of
   discipline.
5. **The classes with no class row need a damage ratio too**, so `CUSTOM_VALUATION_POLICY` carries
   its own `damage_ratio` (0.10 / 0.35 / 0.75), deliberately wider than any class row. It is an
   assumption, like every other damage ratio in this repo.
6. **A valuation orders the strategic view and nothing else.** readme 6 keeps property value out of
   contact urgency; a euro figure that reordered the contact queue would mean a building outranking
   a care home. `strategic_queue` orders by tier, then by the confirmed figure, then by the same
   remaining window the contact queue uses. Both rules stand, and readme 5.2 and 6 now say which
   one place a euro figure does order.

## What was built

Behind `FEATURES["custom_valuation"]`, currently on.

**Policy.** `CUSTOM_VALUATION_POLICY`: five methods as a closed enum (`component_replacement`,
`service_continuity`, `irreplaceable_holdings`, `parent_institution_scaled`, `not_valued`),
`min_components` per method, `min_band_ratio` 1.5, `component_sum_tolerance` 5%, `max_eur`
5,000,000,000, a `damage_ratio` band, and `assess_classes` — exactly the four classes
`VALUE_AT_RISK_POLICY` prices no row for.

**Contract.** Six keys, `custom_value_eur_low` / `_mid` / `_high`, `custom_value_method`,
`custom_value_components`, `custom_value_basis`, plus the review reason `valuation_unassessed`. The
producer never asserts a figure: it arrives only through an analyst-confirmed `custom_valuation`
override, validated by `priority.custom_valuation_value` and fanned out into the six keys, exactly as
`criticality_tier` arrives.

**Euros.** A confirmed mid replaces the class `replacement_value_eur` (with
`replacement_value_basis` naming the per-asset valuation and its method), and
`expected_loss_eur_<level>` becomes `burn_probability x d_<level> x custom_value_eur_<level>`. On a
50 M EUR mid with a 30–80 M band and `burn_probability` 0.4 the loss band goes from
2.0 / 7.0 / 15.0 M EUR (damage band alone) to 1.2 / 7.0 / 24.0 M EUR: the spread widens from 7.5x to
20x, which is the value uncertainty finally being shown instead of assumed away.

**Agent.** A sixth tool `lookup_valuation_reference` (offline, in `STATELESS_TOOLS`) over
`fixtures/valuation_references.json`, and a sixth proposal field `custom_valuation` whose value is a
payload, not a number, so a figure can never be confirmed without the method and the priced
components that justify it. `DEFAULT_MAX_STEPS` is 8, not 6: one asset can carry
`occupancy_unknown`, `criticality_unassessed` and `valuation_unassessed` at once.

**Corpus.** Nine references, five `published` (the ATC 2025 docent and sanitari modules and the
fees/VAT/contents multiplier from `docs/VALUE_AT_RISK.md` section 6, an IRTA scaling line, a fire
station relocation line) and four `assumed` (the project's own placeholders: a worked example, a
per-rack HPC cost, a laboratory fit-out rate, a rebuild-time line). Every `assumed` statement carries
the literal marker `[assumed]` inside the quotable sentence, so an agent quoting it verbatim carries
the caveat with it and an analyst reading the proposal sees it.

## Verification

Measured on 2026-09-20 in this worktree, offline (no `--live` run: it costs money and needs a key).

- `scripts/validate.py` grew `check_valuation`, registered in `CHECKS`. It measures rather than
  asserts, in the style of `check_criticality`:
  - the policy guards through `priority.custom_valuation_value`, eleven accept/refuse cases covering
    the method enum, the band guard, the band ordering, `min_components`, the component-sum rule,
    the ceiling and `not_valued` carrying amounts;
  - the flag gating on a constructed `research_facility` row through two explicit `cfg` shims: all
    six keys null with the flag off and on, the review reason only with it on;
  - the expected-loss compounding, against the same record priced as a class-table school;
  - `strategic_queue` ordering three same-tier assets by the confirmed figure ahead of the window;
  - **the euro-neutrality probe**: every one of the 180 assets of `gavarres_real_0002` given a
    4,000,000,000 EUR confirmed valuation leaves the 72-asset ranked contact order byte-identical,
    and neither `priority.ranked_sort_key` nor `fireline/contact_priority.py` mentions any of the six
    key names, `custom_valuation`, `amount_eur`, `replacement_value_eur` or `expected_loss`;
  - the classes admitted (12 assets in the committed extract: 6 `fire_station`, 5 `aerodrome`,
    1 `research_facility`; no `university` in this area) and how many carry `valuation_unassessed`.
- `check_criticality`'s assertion that `loss_multiplier` appears nowhere in `fireline/priority.py`
  still holds: the euro arithmetic went into `snapshot.derive_value_at_risk`, and nothing anywhere
  reads `loss_multiplier` yet. The criticality multiplier is still defined and unused.
- `scripts/validate.py` (offline): **9 pass, 1 fail, 1 not verified**. `check_valuation` is green.
  The failure is `Coverage/matching`, and it is not about this layer's logic: the six keys were added
  to `snapshot.ASSET_KEYS`, but `fixtures/real_area/assets_gavarres.json` and
  `fixtures/snapshots/*.json` have not been rebuilt, so every record is reported as missing them.
  `tests/test_validate.py::test_coverage_counts` and `::test_write_regenerates_validation_markdown`
  fail for that same reason (the second only because it calls `main()`, which returns 1 while any
  check is red), as does `tests/test_snapshot.py::test_committed_real_area_fixtures`. Rebuilding the
  fixtures with the flag on is the fix, and it is a step this worktree cannot finish: the two
  synthetic snapshots have been rebuilt and do carry the six keys, while the real-area extract and
  snapshots have not, and rebuilding those needs the gitignored `data/` register extract.
- `tests/test_custom_valuation.py`: 32 passed (the policy shape, the guards, the flag gating,
  validation, the override fan-out and the euro maths).
- `tests/test_validate.py`: 14 passed, 2 failed — the two above. The new
  `test_valuation_check_keeps_euros_out_of_the_contact_queue` passes.
- Whole suite: 1202 passed, 4 failed, 2 skipped. Three failures are the stale-fixture one above; the
  fourth (`tests/test_ui_preview.py::test_report_carries_every_key`) passes on its own and was a
  concurrent edit elsewhere in the worktree, not this layer.

## Known limits

- **A figure is exactly as good as the reference quoted for it.** The guards make a proposal
  traceable, banded and arithmetically consistent. They cannot make it right. Nothing here is a
  market valuation, an insurer's figure, an appraisal or a measured number, and no valuation in this
  repo has been checked against a real transaction or a real rebuild cost.
- **The `assumed` corpus rows are this project's placeholders, not published figures.** Four of the
  nine references — the worked example, the per-rack HPC cost, the laboratory fit-out rate and the
  rebuild-time line — were written for this repo. They are marked `basis: "assumed"` with `[assumed]`
  inside the statement so the caveat survives being quoted, but a proposal built on them is built on
  an assumption of ours, not on an external source.
- **The `published` rows are published *class* tables, used per asset.** The ATC modules are
  EUR/m² by building typology; quoting one for a particular research building is still the class
  answer wearing a per-asset coat, unless the components dominate the shell (which is the case the
  method `component_replacement` exists for).
- **The offline FakeLLM replays a committed worked example; it does not compose one.**
  `_propose_valuation` restates the numbers of a corpus record that carries `components` and a
  low/high band, so the demo proposal states no number absent from a tool result. That makes the
  offline path deterministic and the guards exercised, and it means the offline run measures the
  plumbing, not the model's valuation judgement. Only a `--live` run measures that, and none was
  made for this layer.
- **No live run.** Unlike handoff 003, this layer has not been put in front of the real model.
  Whether `deepseek-ai/DeepSeek-V4.1-Flash` picks sensible methods, holds to the band rule and
  reaches for `not_valued` when it should is unmeasured. Expect the same variance handoff 003 found
  in borderline criticality tiers, and expect it to be wider here: a number has more room to vary
  than a four-value tier.
- **The damage ratio for these classes is assumed**, and is a single band for four very different
  classes. A fire station is not a laboratory. It is deliberately wider than any class row, which is
  the right direction, not a measurement.
- **`assess_classes` is a cost filter, like criticality's.** Schools, campsites, care homes, camps,
  masies and nuclei never enter the valuation queue automatically: the registers hold hundreds of
  near-identical rows and the class table already prices them. A genuinely unusual school would be
  missed unless an analyst points the agent at it.
- **`validate_snapshot` does not yet treat the six keys as optional in practice.** The intent, stated
  in `snapshot.py` and in `_custom_valuation_errors`, is that a snapshot written before the layer
  stays valid. But the missing-key check exempts `CRITICALITY_KEYS` only, so every pre-layer snapshot
  is reported as missing all six and the all-absent branch is unreachable. This is the same defect
  behind the red `Coverage/matching` check. Either exempt `CUSTOM_VALUATION_KEYS` the way
  `CRITICALITY_KEYS` are exempted, or rebuild the fixtures — the first is what the docstrings
  promise.
- **The strategic view mixes an ordinal and a cardinal answer.** Tier first, then euros: an
  `elevated` asset with a 100 M EUR bespoke figure sorts below a `high` asset with none. That is
  deliberate (the tier is the judgement about whether the loss outlasts the incident), but it means
  the view is not a euro ranking and should not be read as one.
- **`criticality_tier`'s `loss_multiplier` is still read by nothing.** This layer deliberately did
  not wire it: multiplying a bespoke valuation by an assumed tier multiplier would compound two
  assumptions into a number that looks like a result. The follow-up from handoff 003 stands, and
  should be taken only with a held-out evaluation behind it.

## Follow-ups worth taking

- A `--live` run over the four assessed classes of `gavarres_real_0002`, recorded in `VALIDATION.md`
  the way the criticality block is: methods proposed, band widths, how often `not_valued` comes back,
  and how far two runs of the same asset differ.
- Rebuild the fixtures with the flag on (or make the six keys optional in `validate_snapshot`) and
  record what changes; nothing but the added review reason and six null keys should.
- Grow the `published` half of the corpus. Each `assumed` row replaced by a cited one directly
  improves every proposal built on it.
- A UI column and a strategic-view caption for the bespoke figure that shows its band, its method and
  its basis string, so an analyst never sees the mid alone.

## Files

- `fireline/config.py` (`FEATURES["custom_valuation"]`, `CUSTOM_VALUATION_POLICY`)
- `fireline/snapshot.py` (`CUSTOM_VALUATION_KEYS`, `VALUATION_UNASSESSED`, `_valuation_wanted`,
  `_custom_valuation_errors`, `custom_valuation_band`, the custom path in `derive_value_at_risk`)
- `fireline/priority.py` (`custom_valuation_value`, the `custom_valuation` branch of `_apply_one`,
  `strategic_queue`), `fireline/valuation_reference.py`
- `fireline/agent.py` (`PROPOSAL_FIELDS`, `lookup_valuation_reference`, the `valuation_unassessed`
  playbook in `SYSTEM_PROMPT`, `DEFAULT_MAX_STEPS`), `fireline/llm.py` (`_propose_valuation`)
- `fixtures/valuation_references.json`
- `scripts/validate.py` (`check_valuation`), `tests/test_custom_valuation.py`, `tests/test_validate.py`
- `readme.md` sections 5.2, 6, 8; `CONTRACTS.md` sections 2.2 and 6; `docs/VALUE_AT_RISK.md`
  sections 6 and 8
