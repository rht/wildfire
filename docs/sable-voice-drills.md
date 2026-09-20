# Sable readiness test calls

`fireline.sable_voice.sable_drill_configuration` builds the dedicated English-language drill agent. It keeps telephone transport (`CallRequest.input_mode='live'`) separate from scenario truth: a live phone connection still uses the test-call disclosure and never authorizes real travel or responder dispatch. Ordinary readiness normalization is unchanged.

The opening is a literal greeting, including explicit inbound/outbound overrides. The internal prompt provides direct questions, one at a time, and forbids reading instructions or describing what the assistant should say. Unrelated requests receive one brief redirection and the pending question. A second consecutive unrelated turn ends the check. Help, corrections, bad audio, wrong recipients, human requests, refusal and stop requests take precedence.

Pass the region, selected models, published end-call tool attachment and outbound connection explicitly. The deployed repair used Deepgram Nova 3, Deepgram Aura 2 with `aura-2-amalthea-en`, and `bedrock-mantle/nvidia.nemotron-super-3-120b:latest` with temperature 0.1 and a 2048-token reply budget. Model availability must be checked against the actual account: the bare Groq alias was rejected; its versioned alias was accepted in `us-east` but failed actual managed sessions with HTTP 429, and the smaller Nemotron model failed a refusal probe. The deployment therefore retains the working larger Nemotron route in `eu-central`. Do not assume model alternatives are equivalent replacements.

Deploy only to a dedicated test agent. Retrieve the saved configuration and run `validate_agent_templates(configuration, call_arguments(request))` before dispatch. All seven existing bindings remain present: request, asset, snapshot, language, incident brief, road warning brief and authorization notice. An optional `location_display_name` carries a trusted readable name/address. Old requests omit it; new packages advertise an empty optional default. Internal IDs are never spoken. The provider receives call-specific values through `arguments`; raw placeholders in its configuration-history record alone do not prove that runtime substitution failed.

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

The proactive revision shares `voice_dialogue.READINESS_DIALOGUE` with the generic dynamic
agent package. It replaces abstract household-coverage questions with "Is anyone else with
you?" and "Does anyone there need help leaving the building?" Immobility prompts concrete
help and transport questions; known answers are not asked again. A wheelchair does not by
itself establish inability. Unknown information about others does not prevent collecting the
caller's own needs. An uncertain answer prompts exactly "Would you like an emergency
responder to call you to further assist you?" Offers, accepted requests and confirmed
backend saves are separate; a test call never arranges a real responder callback.

Reusable managed-dialogue inputs are in `fixtures/voice/sable_proactive_dialogues.json`.
Check their received speech for display names, direct assistance/transport branches, exact
callback wording, declined-offer handling and off-topic redirection. Keep raw transcripts
private and check saved evidence independently. This revision authorizes no additional
telephone retries.

Do not set `models.llm_kwargs.extra_body.slng_pure_proxy=true` for Sable: that diagnostic
mode bypasses provider output filtering and a managed test exposed spoken reasoning with
it enabled. The reusable builder rejects it. Keep the internal prompt private and test
received speech after any model or prompt change; an accepted configuration is not proof
of correct conversation. Use a fresh dedicated deployment identity for a materially new
prompt to avoid retaining the old prompt's response cache, while preserving old call-to-agent
associations for result polling.

The proactive revision was verified with managed audio/transcript sessions covering immobility,
transport already available or unknown, unknown other occupants, the exact callback offer,
accepted and declined callbacks, missing place names and off-topic redirection. Generation
stop sequences for `<think>` and `</think>` belong in `llm_kwargs.extra_body.stop`, not
directly in `llm_kwargs` (the managed runtime rejected the latter at startup). The builder
adds them while preserving caller model choices and stop sequences, without mutating inputs.
A final managed missing-location/stop check passed with no reasoning delimiter.
