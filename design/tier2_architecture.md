# Tier 2 escalation architecture (specific diagram + narrative)

Supersedes the flow description in `araspons-tier2-brief.md` with the more specific version
dictated 2026-09-19. Keeps that brief's two genuinely new contributions — the ES-Alert/GDPR
positioning and INFOCAT phase alignment — but changes *when* the voice-agent channel runs
(see "What changed from the original brief" below). Product name shown as **ReponsAra**
pending final spelling confirmation (four variants have been used so far: Araspons,
Respons'Ara, ReponsAra, Spons'Ara — pick one and I'll do a single global rename across
`design/`).

## Diagram

```text
                         FIRE DATA (Deepfire, satellite, weather)
                                        |
                                        v
                    TIER 1 — risk assessment + analyst coordination
                    (ranked locations, review queue, tasks — existing MVP)
                                        |
                                        v
                        Fire Analyst dashboard (ReponsAra logs)
                                        |
                         Fire Analyst reviews and decides severity
                                        |
                 +--------------------------------------------------+
                 |                                                  |
          CONFINEMENT PATH                                  ESCALATION PATH (Tier 2)
        (INFOCAT: shelter in place,                    (INFOCAT: alerta / emergencia)
         window has closed)                                         |
                 |                                                  v
                 v                                    Fire Analyst escalates to
      Confinement guidance                        Generalitat / Police / Firefighters
      logged and tracked                                            |
                                          +---------------------------+---------------------------+
                                          |                                                       |
                              OFFICIAL CHANNEL                                     OUR PARALLEL CHANNEL
                              (government-owned)                                   (ReponsAra, public tier)
                                          |                                                       |
                              ES-Alert cell broadcast                     Analyst approves preliminary
                              (reaches every phone in                     check-in -> AI voice agent calls
                               the zone, no directory                    households in the affected area
                               needed, GDPR-clean)                       (public directory — FEASIBILITY
                                          |                               UNVERIFIED, see note below)
                                          |                                                       |
                                          |                              Preliminary check-in script:
                                          |                              "have you seen a notice? do you
                                          |                               need help checking in?"
                                          |                                                       |
                                          +-------------------------+-----------------------------+
                                                                    v
                                            Once ES-Alert is broadcasting, relocation
                                            guidance runs in parallel:
                                            -> match household to nearest safe building
                                               with spare capacity AND a reachable route
                                               (hospital / school / other designated site)
                                            -> delivered back through the same voice channel
                                            -> logged to the Fire Analyst dashboard
                                                                    |
                                                                    v
                                    SYNC LAYER (ReponsAra's actual differentiator)
                     Generalitat <-> Fire Analyst <-> Firefighters <-> public entities
                            (schools, hospitals, police, Creu Roja)
                     — one shared, continuously-updated status, not five separate ones —
                                                                    |
                                                                    v
                                      [OUT OF SCOPE for hackathon] citizen-facing app
```

## What problem this actually solves

Without this, a firefighter crew's time gets consumed one household at a time: someone with
no advance warning and no clear destination calls for help at the last minute, and a crew
that could be protecting a hospital or a school full of children is instead pulled to a single
home. Les Gavarres showed this exact failure mode — residents self-evacuated against orders
because they had no trusted channel telling them what to do or where to go.

ReponsAra's Tier 2 doesn't replace ES-Alert or firefighter dispatch. It buys **lead time**:
a preliminary, analyst-approved check-in reaches people before or alongside the official
broadcast, and once evacuation is advised, they're told a *specific* building with room and a
reachable route — not just "leave." People who can self-evacuate do so earlier and with a
destination; people who genuinely need assistance are identified earlier, while there's still
time to help them, instead of being discovered in the middle of the emergency. The net effect
is fewer last-minute individual rescues, so firefighter capacity concentrates on the highest-
stakes assets.

## What changed from the original brief

`araspons-tier2-brief.md` positioned the voice-agent channel as **downstream of and following**
an ES-Alert broadcast, specifically to avoid needing any contact-directory logic at all. This
description moves it **earlier**: an analyst-approved preliminary check-in that can run before
or parallel to ES-Alert, using a public directory as the contact source. That's a meaningfully
different design, not a refinement — worth flagging plainly:

- It reintroduces the exact problem the original brief was designed to dodge: **where do the
  phone numbers come from, and is that lawful.** Spain does not have a live, comprehensive
  public residential phone directory — the old universal "Páginas Blancas" white-pages system
  was discontinued years ago; what remains are low-coverage opt-in registers. "Public
  directory" is not yet a verified, real data source for this. **This needs research before
  it goes in the pitch as a solved input, not after.**
- For the hackathon demo itself this doesn't block anything — `readme.md`'s existing voice-agent
  plan already uses a synthetic/fixture contact list for exactly this reason, and that's
  still the right approach here regardless of which framing (before/after ES-Alert) wins.
- The relocation-guidance piece (nearest building with capacity + reachable route) is new
  and valuable, and extends `fireline/response_priority.py`'s prerequisite/capacity model
  (readme.md §16) in the direction the original brief already pointed at — no conflict there.

## Still open

1. **Name.** Pick one of the four variants seen so far and I'll do a single find-and-replace
   across `design/ui-mockup.html`, `demo_script.md`, `pitch_outline.md`, `use_case.md`, and
   this file.
2. **Pre- vs. post-ES-Alert timing for the voice agent** — this doc assumes the newer
   (pre/parallel) framing per the latest description; say if that's not final.
3. **Contact-source research** — whether a real, lawful public directory exists for this at
   all, or whether the honest pitch line is "fixture-only for the demo, sourcing is
   unresolved" (which is a fine thing to say on stage, per the project's own honesty
   principle already used in `demo_script.md`'s closing beat).
