"""Normalize SLNG memory without treating agent extraction as verified testimony."""
from .voice_models import ANSWER_FIELDS, CallResult


def normalize_call(request, body):
    """Keep exact evidence and strict yes/no answers; all other answers are unknown.

    Only literal matches in user transcript text earn transcript_verified. Redacted
    or unavailable transcripts retain provider-reported evidence for human review.
    Neither form supplies a confidence score or releases the review gate.
    """
    memory = body.get('memory_variables') or []
    if not isinstance(memory, list):
        raise ValueError('invalid provider memory')
    values = {}
    for item in memory:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            raise ValueError('invalid provider memory')
        name = item['name']
        if name in values:
            raise ValueError('duplicate provider memory variable')
        values[name] = item.get('value') if item.get('status') == 'set' else None
    report = body.get('livekit_session_report')
    report = {} if report is None else report
    if not isinstance(report, dict):
        raise ValueError('invalid provider transcript')
    history = report.get('chat_history')
    history = {} if history is None else history
    if not isinstance(history, dict) or not isinstance(history.get('items', []), list):
        raise ValueError('invalid provider transcript')
    turns = []
    for item in history.get('items', []):
        if not isinstance(item, dict):
            raise ValueError('invalid provider transcript')
        if item.get('role') == 'user':
            content = item.get('content', [])
            if isinstance(content, list):
                turns.extend(part for part in content if isinstance(part, str))
    answers, evidence, verification = {}, {}, {}
    fields = ANSWER_FIELDS + ('road_warning_acknowledged',)
    sources = {key: key for key in fields + ('help_needs', 'preparation_remaining', 'instruction_received')}
    sources['instruction_received'] = 'message_received'
    for key, source in sources.items():
        quote = values.get(source + '_evidence')
        if isinstance(quote, str) and quote.strip():
            evidence[key] = quote
            verification[key] = ('transcript_verified' if any(quote in turn for turn in turns)
                                 else 'provider_reported')
        if key in fields:
            value = values.get(source)
            answers[key] = {'yes': True, 'no': False}.get(value.strip().lower()) if isinstance(value, str) else None
    reasons = ['provider_memory_requires_review']
    if 'provider_reported' in verification.values():
        reasons.append('provider_evidence_unverified')
    return CallResult(request_id=request.request_id, asset_id=request.asset_id,
        snapshot_id=request.snapshot_id, provider_call_id=body['id'], status=body['status'],
        observed_at=body['updated_at'], source='SLNG memory variables', **answers,
        evidence=evidence, evidence_verification=verification,
        evidence_observed_at={key: body['updated_at'] for key in evidence},
        evidence_time_basis='provider_record_update',
        human_followup_required=True, human_followup_reasons=reasons)
