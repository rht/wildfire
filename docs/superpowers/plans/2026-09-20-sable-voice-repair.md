# Sable voice repair implementation plan

**Goal:** Deploy the approved conversational Sable drill assistant and verify three specifically authorized retries.
**Architecture:** Keep ordinary readiness normalization unchanged. Add a reusable drill configuration builder with literal spoken greeting, direct interview questions, strict scope rules, and conservative runtime answer capture. Verify the managed runtime through non-telephony sessions before spending real retries.
**Tech stack:** Python, pytest, SLNG managed agents, LiveKit web sessions.
**Spec:** User-approved design in the task: Sable identifies itself as AI, briefly discloses a test call, asks one question at a time, honors human/stop requests, never invents help or real emergency authority. Off-topic answers redirect once to the pending question without filling answers or advancing; repeated refusal ends the interview.

## Constraints
- Work only in this task worktree; preserve frozen audit reports.
- Keep contacts, keys, session tokens, transcripts and payloads in ignored private data.
- Preserve request/asset/snapshot UUID associations and template preflight.
- Exactly one new attempt per authorized contact, maximum two simultaneous and one start per second; no automatic retry.

## Tasks
- [x] Trace actual agent configuration, generated speech and provider runtime; inspect third-call failure diagnostics.
- [x] Add `tests/test_sable_voice.py` coverage for drill greeting separation, all template arguments, scope constraints, answer evidence and safe terminal paths. Run failing tests first.
- [x] Add `fireline/sable_voice.py` reusable drill package without altering production readiness assessment. Keep model choice explicit and separate from the spoken identity.
- [x] Deploy only the dedicated exercise agent, verify saved configuration, and exercise actual web runtime: normal turns, off-topic trivia, prompt injection, correction/uncertainty, human request, refusal and stop. Check runtime rendering and speech, not just strings.
- [x] Verify old calls terminal; run one retry for each approved contact, persist accepted/ringing/respondent/terminal evidence separately. Stop further calls if meta narration recurs.
- [x] Run focused regression suite; review diff; commit, push, open PR without merge. Write sanitized `data/sable-session/result.md` with limitations and retry outcomes.

## Approved live-call revision

- [x] Add optional private `CallRequest.location_display_name`, populate it from trusted asset name/address in briefing and snapshot adapters; send it only when supplied. Keep original seven arguments compatible and advertise the new prompt variable as optional. Preserve immutable old stored requests with the new default.
- [x] Share direct readiness dialogue between generic and Sable prompts: no spoken internal IDs; concrete presence/help questions; immediate mobility assistance and transport branch; proactive callback offer for uncertainty; offers never count as acceptance. Keep human review on unknown/contradictory answers and collect individual needs when group coverage is unknown.
- [x] Add focused request/template/store regressions and behavioral fixtures for immobility, known/unknown transport, being alone, unknown others, accepted/declined callback and off-topic requests. Verify actual managed web dialogue before handoff.
- [x] Update the existing dedicated Sable deployment and PR39, preserve all prior phone results, and make no additional phone calls.
