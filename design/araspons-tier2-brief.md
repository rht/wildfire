# Araspons — Tier 2 escalation proposal (brief for Claude Code)

Context: this extends the existing FireLine/Araspons repo (risk assessment + analyst
coordination, evacuation-window contact priority — see README §3–6). Tier 1 (current
build) is unchanged: Deepfire → Araspons triangulation → fire analyst is the sole first
escalation point.

## Product name
Renamed to **Araspons** (Catalan *ara* "now" + *respons/resposta* "response"). Runner-up
considered: Respons'Ara.

## New scope: Tier 2 — public escalation & relocation (proposal, not yet built)

Stage severity against the analyst's existing workflow rather than a new detection path:

| Stage | Trigger | Action |
|---|---|---|
| Initial | Detected | Escalate to fire analyst only (= existing Tier 1 scope) |
| Intermediate | Analyst confirms | Trigger outward public notification |
| Severe | Active threat confirmed | Evacuation guidance + provisional relocation |

Align these three stages to Catalunya's real INFOCAT phases (prealerta → alerta →
emergència) rather than inventing new labels — cite this directly in the pitch.

### Public notification — do NOT build a phone-directory ingestion path
Spain already runs **ES-Alert** (cell-broadcast, used by 112 / CECAT in Catalunya): it
reaches every phone in a danger zone's antenna coverage with no phone-number database
and no personal-data resolution, so it sidesteps GDPR entirely. Design Araspons's
voice-agent layer as the **follow-up/confirmation channel downstream of an ES-Alert-style
broadcast** (an inbound number the alert references), not as the initial mass-notification
mechanism. For the hackathon demo, this means: fixture/mock contact list is fine — do not
spend time trying to source a real directory.

### Relocation logic
Extend the evacuation-window math already in `fireline/contact_priority.py` (README §6),
applied to people instead of assets: a hospital or other receiver becomes a location with
capacity, and relocation is decided by "does it have room and is it reachable within the
remaining window", not by ranking "save the home" vs. "save the hospital" as competing
assets. This is closer to `fireline/response_priority.py`'s prerequisite/effects model
(README §16) than to the contact-ranking one — implement it there or adapt that module.

### Voice-agent confirmation flow
Agent calls people in the zone (Catalan/Spanish), asks: (1) have you seen the notice,
(2) are you a *persona de risc* / do you need help. Every outcome — confirmed / no
answer / needs help — logs to a new dashboard tab so the fire analyst can follow up on
anyone unconfirmed. This is a self-contained, demoable slice independent of the real
contact-directory problem.

## Open decision
Lock in **Araspons** vs **Respons'Ara** before the deck is built.
