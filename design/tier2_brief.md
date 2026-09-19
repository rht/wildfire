# SponsAra Tier 2 — escalation engineering brief

Self-contained: paste this into a fresh Claude Code session, or a TASKS.md entry, without
needing the rest of this conversation. It supersedes `araspons-tier2-brief.md` and
`tier2_architecture.md` in this same `design/` folder — those are kept for history, this is
the current version. Product name shown as **SponsAra** per the latest diagram; if that
changes, it's a find-and-replace across `design/*.md` and `design/ui-mockup.html`, nothing
structural.

## Context for a cold start

This repo (`rht/wildfire`) is a hackathon project for Hackbarna AI Summit 26, track 4
("Values at risk," see `challenge.md`). `readme.md` is the canonical scope/interface
reference — read it before touching anything; `AGENTS.md` is the workflow contract
(worktrees under `.worktrees/`, push every task branch). **Tier 1 is the existing MVP**:
Deepfire feed → risk assessment (`fireline/snapshot.py`, `fireline/fire_input.py`) → analyst
coordination (`fireline/priority.py`, `fireline/contact_priority.py`, `fireline/tasks.py`) →
Streamlit dashboard (`fireline/app.py`). It is unchanged by this brief.

**Tier 2 is new, proposed, not yet built.** It's what happens after the fire analyst decides
a location needs escalating, on two channels that run in parallel rather than one replacing
the other.

## Diagram (as agreed)

```text
TIER 1 — OPS (current build, unchanged)
Deepfire (fire signal)
   -> SponsAra risk engine (fireline/snapshot.py + fireline/contact_priority.py:
      exposure, forecast-vs-evacuation-window ranking — README section 6)
   -> Fire analyst (Streamlit dashboard, fireline/app.py)
   -> Dashboard logs

TIER 2 — ESCALATION (proposal), triggered when the analyst escalates a location
                              |
              +---------------+---------------+
              |                               |
   SPONSARA — PUBLIC RISK LAYER      OFFICIAL / GOVERNMENT CHANNEL
              |                               |
   Analyst gives preliminary OK    Escalate: firefighters / police /
   (go-ahead to start public        Generalitat (physical response chain)
    check-in — independent of                 |
    whether ES-Alert has fired yet)  ES-Alert cell broadcast (govt-owned,
              |                       triggered by Generalitat — we do not
   SponsAra voice agent calls         build or control this; log that it
   affected households (check-in /   fired, do not simulate its payload)
   confirm safety, persona de risc            |
   — reuse the existing              +--------+--------+
   CallRequest/CallResult                      |
   contract, not a new script)                 |
              |                                |
              +----------------+---------------+
                                v
        SponsAra relocation/confinement engine
        Runs once BOTH are true: ES-Alert has fired AND check-in
        calls are underway. Gated: proceeds only with Generalitat
        confirmation, not the fire analyst alone.
                                |
              +-----------------+-----------------+
              |                                   |
    Relocation guidance                 Confinement guidance
    (asset has a reachable route        (shelter-in-place — the
     to a building with spare           ALTERNATIVE to relocation,
     capacity within the remaining      never both for the same asset)
     window: hospital/school/other,
     specific destination, not a
     direction)
              |                                   |
              +-----------------+-----------------+
                                v
                  Residents informed early
        (lead time to prepare/seek help/move ahead of a formal
         order; firefighters redirected to higher-impact sites
         instead of one residence at a time)
                                v
                  Dashboard / sync layer
        One shared picture across Generalitat, fire analyst,
        firefighters, public entities (schools, hospitals,
        police, Creu Roja)

[OUT OF SCOPE] citizen-facing mobile app
```

## Reuse before building anything new

Everything below already exists and should be extended, not duplicated:

- **Relocate-vs-confine is not a new decision rule.** `fireline/decide.py`'s `decide(asset,
  route, cfg=config)` and `shelter_viable_for(asset)` already implement a window-based
  confine/evacuate rule aligned with INFOCAT practice (from `PLAN.md`'s v3 scope). It's
  currently gated off behind `config.FEATURES` per `V4_GAPS.md` item 6. **Turn this on and
  adapt it for Tier 2's per-asset relocate-XOR-confine output** rather than writing new
  decision logic — the "never both" rule in the diagram is exactly what this function
  already encodes.
- **The voice-agent interview is already fully specced.** `readme.md`'s "Voice-agent
  delivery plan" section (owned by `@mirrdj`, active on `codex/slng-voice-agent` and related
  branches) defines the `CallRequest`/`CallResult` schema and a 5-question interview
  (confirm location/household → self-evacuate ability → transport → wants-human →
  read-back/acknowledge). **Use that contract for the "SponsAra voice agent" box above.**
  Do not write a second, different call script — check in with `@mirrdj` before touching
  those files, since this is her active workstream.
- **Relocation-to-a-specific-building extends `fireline/response_priority.py`.** Its
  `Planner`/`plan_response(scenario)` already model prerequisites, deadlines, capability
  checks and declared effects on other locations (`readme.md` section 16, all items
  checked). Model a receiving building as a location with capacity, and "has room and is
  reachable within the remaining window" as the match condition — this is closer to that
  module's prerequisite/effects model than to `contact_priority.py`'s ranking-only one.
- **Contact ranking for who to call first is `fireline/contact_priority.py`'s
  `rank_contacts(locations, policy=None)`** (README section 6) — the remaining-evacuation-
  window logic already there is the right input to "who does the voice agent call first."
- **Dashboard logging** goes through the existing `fireline/snapshot.py` /
  `fireline/tasks.py` `TaskStore` pattern — add task types/statuses for the Tier 2 flow
  rather than a parallel logging path.

## What's genuinely new to build

1. An escalation trigger on an asset (analyst action) that fans out to both channels
   independently — the official-channel escalation and the "give preliminary OK" action are
   two separate analyst-initiated events, not one button.
2. A `Generalitat-confirmed` flag gating the relocation/confinement engine — this is a
   different, higher authorization bar than the fire analyst's own preliminary OK for
   starting check-in calls. Model these as two distinct gates, not one.
3. The "ES-Alert has fired" signal itself — **we do not build or simulate the actual
   ES-Alert broadcast.** For the demo, this is a manually-toggled boolean or a logged
   timestamp the analyst enters ("Generalitat confirmed ES-Alert issued at HH:MM"), not a
   real integration. Do not build a fake ES-Alert payload.
4. The relocation-guidance output needs a `destination_asset_id` + `capacity_remaining` +
   `route_reachable_within_window` triple per relocated asset — not just a category like
   "hospital."

## Explicitly out of scope / unresolved — do not silently assume these

- **Citizen-facing app**: out of scope, per the diagram. Don't build UI for residents.
- **Public phone directory as the voice-agent's contact source**: unverified. Spain's old
  universal residential directory ("Páginas Blancas") was discontinued years ago; what
  remains is low-coverage opt-in registers. Use a synthetic/fixture contact list for the
  demo (same principle `readme.md`'s voice-agent plan already uses) and say so on stage if
  asked — do not claim a working directory integration.
- **Real ES-Alert integration**: out of scope; see point 3 above.
- **Simultaneous relocation and confinement for the same asset**: explicitly disallowed by
  the diagram — enforce this as a hard constraint (an assertion or validation check), not
  just a convention.

## Open before this goes further

1. Final product name (this brief uses SponsAra; earlier docs used Araspons, Respons'Ara,
   ReponsAra — pick one).
2. Whether this brief itself gets folded into the shared `readme.md`/`AGENTS.md`, or stays
   a `design/` proposal until `@rht` and `@mirrdj` have seen it — it hasn't been agreed with
   either of them yet, and `readme.md`'s "Voice-agent delivery plan" section is already
   `@mirrdj`'s active, in-progress work, so this needs her sign-off before merging, not just
   yours.
