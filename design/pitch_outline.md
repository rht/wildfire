# FocTriage — pitch deck outline

For the Norrsken x Deepfire "AI for Wildfire" track, sub-track 4 (Values at risk), Hackbarna
AI Summit 26. Built to be delivered alongside `demo_script.md`'s 3-minute live walkthrough —
this deck brackets the demo (open on the problem, close on validation/team), it doesn't
replace it. Each slide notes which judging criterion from `challenge.md` it's answering,
so it's visibly covered rather than left implicit.

Target: 9 slides, about 60–75 seconds of talking per slide outside the live demo, so the
whole pitch (deck + demo) fits comfortably inside a 5-minute slot.

---

## Slide 1 — Title

**FocTriage**
*Which infrastructure, people and assets are in danger — and which hospital, which school,
needs a call first.*

Track 4: Values at Risk · Norrsken x Deepfire · Hackbarna AI Summit 26
Team: [rht], [Miruna-Diana Jarda], [Michella Warren]

Visual: the mockup's title bar screenshot, or the fire-perimeter map alone, unlabeled, as a
cold open before you say anything.

## Slide 2 — Problem (judging criterion 1: helping first responders)

Lead with Les Gavarres, 3 July 2026, because it's real, recent, and Catalan:

- Fire broke out near la Bisbal d'Empordà; **seven municipalities confined** within hours,
  including a children's camp of ~150 kids.
- **Residents self-evacuated against official confinement orders** because they didn't
  trust the call — they left by car, which is exactly the higher-risk behavior the order
  was meant to prevent. ([Vilaweb](https://www.vilaweb.cat/noticies/incendi-bisbal-demporda-confinament/))
- The mayor of a confined municipality said publicly that **resources were lacking**.
- A Generalitat official called it **"a learning experience"** for coordinating between
  municipalities and the region.

One line: *"The fire wasn't the only failure. Trust and coordination were too."*

## Slide 3 — Why this, why now

- Spain just had a record-breaking wildfire year (challenge.md's own framing).
- Satellite, camera, and weather data are abundant — Deepfire alone fuses seven satellite
  sources. The bottleneck isn't data, it's **turning that data into a decision an analyst
  can act on in the next five minutes.**
- Existing Catalan/Spanish tools solve pieces of this, not the whole thing: `focs.cat`
  shows you *where* fires are; ES-Alert *pushes* alerts; AlertCops takes citizen reports.
  **Nothing ranks who needs attention first, tracks whether anyone's already on it, and
  tells you what's still unknown.**

## Slide 4 — Solution, one line + one diagram

*"FocTriage turns a live fire feed into a ranked, explainable, continuously-updating queue
of who needs help first — and a task board that survives the fire moving."*

Diagram (reuse/simplify the one in `readme.md` section 3):

```
Deepfire fire feed  +  cached facility data
            |
            v
   risk assessment  →  shared location snapshot  →  analyst coordination
   (exposure, gaps)                                  (priority, tasks, agent)
                                                              |
                                                              v
                                                  map + ranked queue + tasks
```

## Slide 5 — How the ranking actually works (judging criterion 3: creative use of data)

Show the score-breakdown panel from the mockup (Vall Repòs example: proximity 0.68 / size
0.28 / value 1.00 → 0.63).

- **Proximity** from Deepfire's satellite perimeter — distance to the actual fire footprint,
  not a fixed radius.
- **Size** from Gencat's Equipaments and social-services registers — real capacity data,
  not guesses.
- **Value** from an explicit, versioned policy table — shown on screen, not hidden in code.
- Every number in the row traces to a named source with a timestamp. **Nothing is silently
  assumed** — missing data puts the asset in a visible review queue instead of a false
  "safe" score.

## Slide 6 — Live demo marker

*(No content slide — this is where you hand off to the live demo. See `demo_script.md`.)*

Suggested single line on screen during the demo: **"Live: FocTriage on the Les Gavarres
incident."**

## Slide 7 — What makes this different from "Deepfire already does this" (pre-empt the
obvious judge question)

- Deepfire's own docs list "values at risk: coming soon" — even when it ships, an API
  returns numbers, not a workflow.
- FocTriage's actual contribution is the **decision layer**: task assignment with team
  availability checks, an update loop that **preserves an analyst's work** when the fire
  moves, and an agent that investigates gaps instead of guessing.
- Show the "Next update" moment again as a still: ranking reordered, Vall Repòs still
  assigned to Bombers unit 3.

## Slide 8 — Honesty slide (judging criterion 2: technical accuracy; also just good practice)

State plainly what this is *not*, so nobody discovers the limits by asking a hard question:

- Distance-to-fire is **not** burn probability or time-to-impact — it's a proxy, labelled
  as one.
- The system does **not** issue evacuation or confinement orders, or calculate routes —
  those stay a human, INFOCAT-director decision.
- The value-weighting policy is an **explicit, versioned prototype**, not a validated
  emergency-services standard — shown on screen for exactly this reason.
- Current demo runs on a recorded/synthetic Deepfire snapshot, not a live poll — say so if
  asked, per `readme.md`'s own `input_mode` field.

## Slide 9 — Team, ask, close

- Three roles: risk assessment (data/exposure), analyst coordination (scoring/tasks/agent),
  product & design (workflow, UI/UX, this pitch).
- Close on the tagline: *"Which infrastructure, which people, which assets — and which
  hospital, which school needs the call first."*
- If there's an ask slot: what you'd build with more time (from `readme.md` section 14 —
  route constraints, more incidents, automated scheduling) — keep it to one line so it
  doesn't undercut the "we know our scope" message from slide 8.

---

### Design notes

- Reuse the mockup's actual color system (deep ink top bar, act-now red / prepare amber /
  monitor grey) across the deck so the slides and the live demo feel like one product, not
  a slide deck bolted onto a screen recording.
- Screenshot sources: `design/ui-mockup.html`, sections already exercised in
  `demo_script.md` — reuse those exact screenshots rather than re-staging new ones, so the
  deck and the demo show the identical states.
- Keep slide 8 in the deck even under time pressure — cutting it is how a judge's question
  turns into a "gotcha" instead of a line you already said.
