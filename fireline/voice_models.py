"""Strict, transport-neutral interview records. Errors never echo supplied values."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
import re
from .route_guidance import validate_road_warnings

ANSWER_FIELDS = ('identity_confirmed', 'whole_household_confirmed', 'can_self_evacuate',
                 'transport_available', 'wants_human', 'acknowledged')
TERMINAL = frozenset({'completed', 'no_answer', 'failed', 'declined'})
STATUSES = TERMINAL | {'queued', 'ringing', 'in_progress'}


def text(value, name, limit=1024):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'invalid {name}')


def identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
        raise ValueError(f'invalid {name}')


def utc(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() != timedelta(0):
            raise ValueError()
        return parsed
    except (AttributeError, TypeError, ValueError):
        raise ValueError('timestamp must be UTC ISO format') from None


def phone(value):
    if not isinstance(value, str) or not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise ValueError('invalid E.164 contact number')


@dataclass(frozen=True)
class CallRequest:
    request_id: str
    asset_id: str
    snapshot_id: str
    contact_number: str = field(repr=False)
    language: str
    incident_brief: str = field(repr=False)
    human_callback_number: str | None = field(default=None, repr=False)
    input_mode: str = 'synthetic'
    road_warnings: list[dict] = field(default_factory=list, repr=False)

    def __post_init__(self):
        for key in ('request_id', 'asset_id', 'snapshot_id'):
            identifier(getattr(self, key), key)
        phone(self.contact_number)
        if self.human_callback_number is not None:
            phone(self.human_callback_number)
        if self.input_mode not in ('synthetic', 'recorded', 'live'):
            raise ValueError('invalid input_mode')
        if not isinstance(self.language, str) or not re.fullmatch(r'[a-z]{2,3}(?:-[A-Z]{2})?', self.language):
            raise ValueError('invalid language')
        text(self.incident_brief, 'incident_brief')
        validate_road_warnings(self.road_warnings)


@dataclass(frozen=True)
class CallResult:
    request_id: str
    asset_id: str
    snapshot_id: str
    provider_call_id: str
    status: str
    observed_at: str
    source: str
    identity_confirmed: bool | None = None
    whole_household_confirmed: bool | None = None
    can_self_evacuate: bool | None = None
    transport_available: bool | None = None
    wants_human: bool | None = None
    acknowledged: bool | None = None
    confidence: float | None = None
    confidence_basis: str | None = None
    evidence: dict[str, str] = field(default_factory=dict, repr=False)
    contradictory: bool = False
    human_followup_required: bool = False
    human_followup_reasons: list[str] = field(default_factory=list)
    transfer_status: str | None = None
    bad_audio: bool = False
    evidence_time_basis: str = 'source_observation'
    road_warning_acknowledged: bool | None = None

    def __post_init__(self):
        for key in ('request_id', 'asset_id', 'snapshot_id', 'provider_call_id'):
            identifier(getattr(self, key), key)
        if self.status not in STATUSES:
            raise ValueError('invalid call status')
        utc(self.observed_at)
        if self.evidence_time_basis not in ('source_observation', 'receipt_only'):
            raise ValueError('invalid evidence_time_basis')
        text(self.source, 'source', 256)
        for key in ANSWER_FIELDS + ('road_warning_acknowledged',):
            value = getattr(self, key)
            if value is not None and type(value) is not bool:
                raise ValueError(f'invalid {key}')
        for key in ('contradictory', 'human_followup_required', 'bad_audio'):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f'invalid {key}')
        if self.confidence is not None and (type(self.confidence) not in (int, float)
                or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1):
            raise ValueError('invalid confidence')
        if self.confidence_basis is not None:
            text(self.confidence_basis, 'confidence_basis', 256)
        if not isinstance(self.evidence, dict) or len(self.evidence) > 16:
            raise ValueError('invalid evidence')
        for key, value in self.evidence.items():
            if key not in ANSWER_FIELDS + ('help_needs', 'preparation_remaining', 'instruction_received', 'road_warning_acknowledged'):
                raise ValueError('invalid evidence field')
            text(value, 'evidence excerpt', 2048)
        if not isinstance(self.human_followup_reasons, list):
            raise ValueError('invalid followup reasons')
        for reason in self.human_followup_reasons:
            identifier(reason, 'followup reason')
        if self.transfer_status not in (None, 'requested', 'connected', 'failed'):
            raise ValueError('invalid transfer status')


def association(request, result, provider_call_id):
    if any(getattr(request, k) != getattr(result, k)
           for k in ('request_id', 'asset_id', 'snapshot_id')) or result.provider_call_id != provider_call_id:
        raise ValueError('call association mismatch')
