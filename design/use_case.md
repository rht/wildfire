# Use case: an INFOCAT analyst at la Bisbal d'Empordà, 3 July 2026

A narrative walkthrough of FocTriage against the Les Gavarres scenario built into
`design/ui-mockup.html`. Written for judges/mentors as a leave-behind, or to lift directly
into a deck slide. This is a **concept walkthrough against a recorded/synthetic scenario**,
not a claim that this system ran during the actual 2026 incident — the real event is used
because it's documented, real, and lets every claim below be checked against what actually
happened.

---

## 10:00 — the queue

An analyst at the coordination desk opens FocTriage. A fire has been burning near la Bisbal
d'Empordà since shortly after 09:45. The screen shows:

- **Vall Repòs**, a care home, ranked #1 — proximity 0.68, size 0.28, value 1.00 (care homes
  are weighted highest: they're slowest to move). 78 beds, 70 residents, sourced to the
  region's social-services register, dated weeks earlier, not guessed.
- **Escola la Bisbal**, a school, ranked #2.
- **Can Xic**, an isolated farmhouse, flagged **needs review**: its only exit is an
  unpaved track, and the system can't determine from open data whether it's passable by
  car. Rather than assume yes (dangerous) or no (over-cautious), it's escalated as an open
  question with a labelled default.
- **Pou del Glaç**, a children's summer camp, *also* flagged needs review: the facility
  register lists it as a "casa de colònies" with no occupancy field, and July is exactly
  the season those run programmes. Historically, this was the site with ~150 children —
  a fact FocTriage does not yet know, and does not pretend to know.

The analyst opens Vall Repòs, sees "contact facility" needs doing, and assigns it. The
system offers five teams; one — Bombers unit 7 — is greyed out, already committed
elsewhere. The analyst picks Bombers unit 3 instead. The assignment is logged.

## 12:00 — the fire moves, the queue updates

Two hours later, a new snapshot arrives. Two things happen at once, and this is the part
that matters most:

1. **The ranking changes.** Pou del Glaç jumps to #1 — its occupancy has since been
   confirmed at 150 through an investigation (evidence, not assumption), and the fire has
   closed to 310 metres. Urbanització Sant Pol, a residential nucleus that barely
   registered two hours ago, jumps from #4 to #2 as the front advances toward it.
2. **Nothing the analyst already did gets lost.** Vall Repòs drops to #4 in raw priority —
   the fire moved away from it, relatively — but it's still visibly **assigned to Bombers
   unit 3**, still `in_progress`. The update refreshed exposure without erasing work.

This is the exact failure mode the real incident exposed: a fast-moving, multi-nucleus fire
where confirmed information (the actual seven-municipality confinement list) built up over
hours, and where a resource once committed needed to stay tracked rather than be
recalculated from scratch on every new piece of information.

## What the analyst does *not* get from FocTriage

- No auto-generated evacuation order. The screen's own labelling makes this explicit:
  distance-to-fire is not burn probability, and a ranked list is not an instruction.
- No computed evacuation route. "Check access" is a task an analyst assigns to a team,
  not a route FocTriage calculates itself.
- No claim of superiority over what Bombers, CECAT or Protecció Civil actually did that
  day — the goal is a better-informed analyst, not a replacement decision-maker.

## Why this is the right use case for judging

It's checkable. Every fact quoted above about the real Gavarres incident — the
seven-municipality confinement, the ~150 children, the self-evacuation against orders, the
mayor's "resources were lacking" quote — has a public source (see `PLAN.md` section 10 and
`design/pitch_outline.md` slide 2). The system's behavior in the mockup is reproducible by
opening `design/ui-mockup.html` and clicking "Next update." Nothing here has to be taken on
faith.
