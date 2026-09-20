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
- [ ] Trace actual agent configuration, generated speech and provider runtime; inspect third-call failure diagnostics.
- [ ] Add `tests/test_sable_voice.py` coverage for drill greeting separation, all template arguments, scope constraints, answer evidence and safe terminal paths. Run failing tests first.
- [ ] Add `fireline/sable_voice.py` reusable drill package without altering production readiness assessment. Keep model choice explicit and separate from the spoken identity.
- [ ] Deploy only the dedicated exercise agent, verify saved configuration, and exercise actual web runtime: normal turns, off-topic trivia, prompt injection, correction/uncertainty, human request, refusal and stop. Check runtime rendering and speech, not just strings.
- [ ] Verify old calls terminal; run one retry for each approved contact, persist accepted/ringing/respondent/terminal evidence separately. Stop further calls if meta narration recurs.
- [ ] Run focused regression suite; review diff; commit, push, open PR without merge. Write sanitized `data/sable-session/result.md` with limitations and retry outcomes.
