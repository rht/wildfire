# SLNG result database connection implementation plan

**Goal:** Persist completed SLNG call answers and exact provider evidence against an explicitly associated FireLine request.

**Architecture:** A read-only authenticated GET feeds a bounded memory-variable normalizer. VoiceStore atomically binds the provider agent/call, writes lifecycle and normalized results, and deduplicates replay. A CLI sync command explicitly associates historical calls lacking request arguments.

**Constraints:** No outbound calls or provider mutations. Provider memory evidence remains distinguishable from transcript-verified evidence. No inferred confidence, evacuation, dispatch, or transfer. Existing review tasks remain pending. Synthetic fixtures only.

- [x] Add failing tests for real provider-shaped memory arrays, redacted transcripts, unknown values, unique call/agent association, persistence, duplicates, partial updates, and CLI sync.
- [x] Implement `fireline/slng_results.py` normalizer and evidence provenance in CallResult.
- [x] Add transactional `VoiceStore.sync(client, request_id, provider_call_id=None)` with explicit historical association and durable provider agent binding.
- [x] Preserve adverse assistance/human requests across later partial imports.
- [x] Expose `scripts/voice_demo.py --mode sync --provider-call-id UUID`, with private persistent DB and request file.
- [x] Run focused and full voice tests, review diff for private data, commit and push task branch; parent integrates and documents. Independent review runs before parent merge.
