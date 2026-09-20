# Sable readiness test calls

`fireline.sable_voice.sable_drill_configuration` builds the dedicated English-language drill agent. It keeps telephone transport (`CallRequest.input_mode='live'`) separate from scenario truth: a live phone connection still uses the test-call disclosure and never authorizes real travel or responder dispatch. Ordinary readiness normalization is unchanged.

The opening is a literal greeting, including explicit inbound/outbound overrides. The internal prompt provides direct questions, one at a time, and forbids reading instructions or describing what the assistant should say. Unrelated requests receive one brief redirection and the pending question. A second consecutive unrelated turn ends the check. Help, corrections, bad audio, wrong recipients, human requests, refusal and stop requests take precedence.

Pass the region, selected models, published end-call tool attachment and outbound connection explicitly. The deployed repair used Deepgram Nova 3, Deepgram Aura 2 with `aura-2-amalthea-en`, and `bedrock-mantle/nvidia.nemotron-super-3-120b:latest` with temperature 0.1 and a 2048-token reply budget. Model availability must be checked against the actual account: the documented Groq model was rejected, and the smaller Nemotron model failed a refusal probe. Do not assume either is an equivalent replacement.

Deploy only to a dedicated test agent. Retrieve the saved configuration and run `validate_agent_templates(configuration, call_arguments(request))` before dispatch. All seven existing bindings remain present: request, asset, snapshot, language, incident brief, road warning brief and authorization notice. The provider receives call-specific values through `arguments`; raw placeholders in its configuration-history record alone do not prove that runtime substitution failed.

A prompt deployment is not behavioral verification. Before real retries, create explicit non-telephony web sessions, join using LiveKit, receive the audio and transcripts, and check:

- Greeting interruption resumes a question without reciting internal instructions.
- Trivia, banter and instruction-disclosure attacks leave the pending question unanswered.
- Refusal and stop requests end questioning, including when combined with an unrelated request.
- Human and assistance requests take priority; no connection or dispatch is invented.
- Corrections to uncertainty clear prior evidence; unasked or unsupported answers stay unknown.
- Tool execution and saved answer evidence are checked separately from fluent speech.

The 2026-09-20 repair verified Sable's spoken greeting, location-specific question, off-topic redirection and stop handling in actual managed sessions with received audio. Direct provider-model probes also covered refusal, instruction disclosure, human requests, wrong recipients, bad audio and early assistance. These are sampled behavioral observations, not a guarantee for every possible utterance.

Provider limitations observed during testing: runtime answer variables were not reliably saved, despite fluent dialogue, and the managed API did not expose the documented `llm_router_enabled` field. Never claim a routing bypass or successful evidence capture solely because a requested setting was accepted. Keep the normal human-review gate and retain missing answers as unknown. Inspect each finalized call report; if meta narration recurs, withhold further retries.

For call outcomes, distinguish API acceptance, carrier ringing evidence, respondent speech and terminal status. `in_progress` does not prove ringing or an answered phone. Preserve a genuine SIP failure as `failed`; do not relabel it `no_answer`. A provider report without a downstream SIP status cannot establish the precise carrier rejection reason.
