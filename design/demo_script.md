# Respons'Ara — 3-minute demo script

Rehearse this against `design/ui-mockup.html`. Timings sum to 3:00, matching readme.md
section 12's structure but filled in with this project's actual name, scenario and screens.

Tagline (challenge.md track 4, verbatim): "identifies the infrastructure, people, and assets
in danger when a fire breaks out, and helps make evacuation calls (which hospital, which
school, etc.)."

---

## 1. Problem (20s)

> "On 3 July 2026, a fire broke out near la Bisbal d'Empordà. Within hours, seven
> municipalities were confined, including a children's camp of about 150 kids. But here's
> what actually happened on the ground: residents self-evacuated against official orders
> because they didn't trust the confinement call. The mayor of one confined municipality
> said publicly that resources were lacking. A Generalitat official called it 'a learning
> experience' for coordinating between municipalities and the region.
>
> Deepfire already tells you where a fire is. Respons'Ara tells the analyst which hospital,
> which school, which care home needs a call **first** — and shows exactly why."

*(No screen yet, or the title screen with the tagline visible.)*

## 2. Real incident, one location's breakdown (35s)

- Open the map. Point at the fire perimeter, then the coloured pins.
- Click **Vall Repòs** in the ranked list (#1, score 0.63).
- Narrate the score breakdown panel live: *"This isn't a black box. Proximity 0.68, size
  0.28, value 1.00 — care homes are weighted highest because they're slowest to move.
  Every number traces to a source: capacity 78, from the Registre d'entitats i serveis
  socials, dated 1 June."*

## 3. Missing information → investigation (40s)

- Scroll to the **needs-review queue**: point at **Pou del Glaç**, flagged
  `occupancy_seasonal` — register says "casa de colònies," no occupancy field, and July is
  camp season.
- *"This is where most values-at-risk tools quietly guess. We don't. This gets escalated:
  is there a summer programme running right now? Evidence says yes, based on the
  operator's page — but until an analyst confirms it, it stays flagged, not silently
  assumed."*
- Click through to show the snapshot-2 state where it's resolved: occupancy confirmed at
  150, sourced to "agent investigation, confirmed by analyst," review flag cleared.

## 4. Task assignment with availability check (30s)

- On Vall Repòs, click **contact facility**.
- Open the team dropdown: point out **Bombers unit 7 greyed out ("unavailable")** — *"the
  system won't let you double-book a team that's already committed elsewhere."*
- Select **Bombers unit 3** instead — task flips to `assigned`, visible immediately in the
  ranked list next to Vall Repòs's name.

## 5. The update (35s) — the actual point of the whole project

- Click **Next update →**.
- Let the reorder happen, then narrate: *"Two hours later. Pou del Glaç jumps to #1 — the
  fire closed to 310 metres. Urbanització Sant Pol jumps from #4 to #2 — the front moved
  toward it. But look at Vall Repòs: still assigned to Bombers unit 3. The update didn't
  wipe out the analyst's work — it just told them what changed."*
- Open the change log to show the exact entry recording both the reorder and the
  preserved assignment.

## 6. Honest close (20s)

> "Respons'Ara doesn't issue evacuation orders and it doesn't calculate routes — that's a
> human decision, and we say so on screen. What it does do: rank every facility in the
> path of a fire by an explainable score, surface exactly what's missing before someone
> assumes it's safe, and keep an analyst's work intact as the fire moves. Which
> infrastructure, which people, which assets — and which hospital, which school needs the
> call first."

---

### Notes for whoever presents

- The mockup's "Next update" button only fires once (`snap-001 → snap-002`) — it's a
  scripted two-step demo, not a live feed. Say "recorded" or "synthetic" if asked, per
  readme.md's `input_mode` field — don't imply it's live Deepfire polling unless that's
  actually wired up by demo day.
- If Miruna's real Streamlit implementation (scoring, tasks, agent) is ready by showtime,
  prefer demoing that — this mockup is the fallback/rehearsal tool and the design
  reference, not a competing product.
- Backup: screen-record a run-through of this exact script before the final rehearsal,
  per readme.md section 12's own advice.
