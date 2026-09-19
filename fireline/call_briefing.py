"""Pure, snapshot-bound household instructions for the existing voice queue.

Approval is supplied by the analyst boundary, never inferred from a proposal.
No provider I/O, contact discovery, route planning or call-fact mutation occurs here.
"""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json

from .route_guidance import road_warning_version, validate_road_ids, validate_road_warnings
from .voice_models import CallRequest, identifier, text


@dataclass(frozen=True)
class CallBriefing:
    asset_id: str
    snapshot_id: str
    instruction_version: str
    road_warning_version: str
    guidance_status: str
    human_followup_reasons: tuple[str, ...]
    incident_brief: str = field(repr=False)
    road_warnings: list[dict] = field(repr=False)
    destination: str | None = field(repr=False)
    route_instructions: str | None = field(repr=False)
    plan_id: str | None = None
    plan_revision: int | None = None
    source: str | None = field(default=None, repr=False)


def _mapping(value, name):
    if not isinstance(value, Mapping):
        raise ValueError('invalid ' + name)
    return value


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def _normalized(value):
    return ' '.join(value.casefold().split())


def _warnings(asset, recommendation):
    current = asset.get('road_warnings', [])
    supplied = recommendation.get('road_warnings', [])
    validate_road_warnings(current)
    validate_road_warnings(supplied)
    by_id = {w['road_id']: deepcopy(w) for w in supplied}
    by_id.update({w['road_id']: deepcopy(w) for w in current})
    merged = [by_id[key] for key in sorted(by_id)]
    validate_road_warnings(merged)
    return merged


def _guidance(recommendation, warnings):
    """Validate approval and complete route details before exposing any directions."""
    reasons = []
    revision = recommendation.get('revision')
    if (recommendation.get('approved') is not True
            or not _nonblank(recommendation.get('plan_id'))
            or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
            or not _nonblank(recommendation.get('source'))):
        reasons.append('approval_missing')
    destination = recommendation.get('destination')
    name = destination.get('name') if isinstance(destination, Mapping) else None
    if not _nonblank(name):
        reasons.append('destination_missing')
    route = recommendation.get('route')
    route = route if isinstance(route, Mapping) else {}
    instructions = route.get('instructions')
    road_ids = route.get('road_ids')
    road_names = route.get('road_names')
    try:
        validate_road_ids(road_ids)
        valid_roads = bool(road_ids) and isinstance(road_names, (list, tuple)) and bool(road_names)
        valid_roads = valid_roads and all(_nonblank(n) for n in road_names)
    except ValueError:
        valid_roads = False
    if route.get('feasible') is not True or not _nonblank(instructions) or not valid_roads:
        reasons.append('route_unavailable')
    elif any(w['road_id'] in road_ids
             or _normalized(w['road_name']) in {_normalized(n) for n in road_names}
             or _normalized(w['road_name']) in _normalized(instructions) for w in warnings):
        reasons.append('road_conflict')
    if recommendation.get('conflicts') not in (None, []):
        reasons.append('conflicting_guidance')
    return name, instructions, reasons


def build_call_briefing(asset, recommendation, *, snapshot_id):
    """Build private content and version metadata; missing approvals fail closed.

    See readme.md's Household call briefings contract. Warnings are merged by
    road ID with current asset records taking precedence. The content hash binds
    identity, approved details, route membership, approval revision and warnings.
    """
    asset = _mapping(asset, 'asset')
    asset_id = asset.get('asset_id')
    identifier(asset_id, 'asset_id')
    identifier(snapshot_id, 'snapshot_id')
    recommendation = {} if recommendation is None else _mapping(recommendation, 'recommendation')
    matches = (recommendation.get('asset_id') == asset_id
               and recommendation.get('snapshot_id') == snapshot_id)
    warnings = _warnings(asset, recommendation if matches else {})
    destination, instructions, reasons = _guidance(recommendation, warnings)
    if recommendation and not matches:
        reasons.append('recommendation_identity_mismatch')
    status = 'readiness_only' if reasons else 'approved_instructions'
    if reasons:
        destination = instructions = None
    name = asset.get('name')
    name = name if _nonblank(name) else asset_id
    warning_version = road_warning_version(warnings)
    # Do not hash incidental attributes (contacts, confidence, past call facts).
    content = dict(schema_version='call-briefing-1', asset_id=asset_id,
        snapshot_id=snapshot_id, name=name, guidance_status=status,
        reasons=sorted(reasons), destination=destination, instructions=instructions,
        approval=({key: recommendation[key] for key in ('plan_id', 'revision', 'source')}
                  if not reasons else None),
        route=({key: recommendation['route'][key] for key in ('road_ids', 'road_names')}
               if not reasons else None), road_warning_version=warning_version)
    version = hashlib.sha256(json.dumps(content, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    lines = [f'Household: {name}. Snapshot: {snapshot_id}.',
             f'Instruction version: {version}.',
             'Ask for a new acknowledgement of this message and any approved instructions; '
             'an earlier acknowledgement does not apply.']
    if reasons:
        lines.append('No approved destination and route can be relayed. Request human help '
                     'for analyst resolution and assess readiness; do not invent directions.')
    else:
        lines.extend([f'Approved destination: {destination}.', f'Approved route: {instructions}',
                      f'Approval source: {recommendation["source"]}.'])
    brief = '\n'.join(lines)
    text(brief, 'incident_brief')  # Keep CallRequest's 1024-character limit; never truncate.
    return CallBriefing(asset_id=asset_id, snapshot_id=snapshot_id,
        instruction_version=version, road_warning_version=warning_version,
        guidance_status=status, human_followup_reasons=tuple(sorted(reasons)),
        incident_brief=brief, road_warnings=warnings, destination=destination,
        route_instructions=instructions, plan_id=recommendation['plan_id'] if not reasons else None,
        plan_revision=recommendation['revision'] if not reasons else None,
        source=recommendation['source'] if not reasons else None)


def build_call_request(asset, recommendation, contact, *, snapshot_id, request_id,
                       input_mode='synthetic'):
    """Return an existing CallRequest; caller owns approval, storage and enqueueing."""
    contact = _mapping(contact, 'contact')
    briefing = build_call_briefing(asset, recommendation, snapshot_id=snapshot_id)
    if contact.get('asset_id', briefing.asset_id) != briefing.asset_id:
        raise ValueError('contact asset mismatch')
    return CallRequest(request_id=request_id, asset_id=briefing.asset_id,
        snapshot_id=snapshot_id, contact_number=contact.get('contact_number'),
        language=contact.get('language', 'en'), incident_brief=briefing.incident_brief,
        human_callback_number=contact.get('human_callback_number'), input_mode=input_mode,
        road_warnings=deepcopy(briefing.road_warnings))


def briefing_acknowledgement(briefing, request, result=None):
    """Return version-bound receipt facts, never readiness/departure/arrival.

    Use the request/result association from VoiceStore (including its provider
    binding). A changed briefing needs a new immutable request ID. Explicit
    transcript verification is required regardless of any model confidence.
    The original request and result remain untouched.
    """
    from .voice_interview import normalize_result

    if (request.asset_id != briefing.asset_id or request.snapshot_id != briefing.snapshot_id
            or request.incident_brief != briefing.incident_brief
            or road_warning_version(request.road_warnings) != briefing.road_warning_version):
        raise ValueError('briefing request mismatch')
    state = dict(instruction_version=briefing.instruction_version,
        road_warning_version=briefing.road_warning_version, instruction_acknowledged=None,
        road_warning_acknowledged=None, requires_new_acknowledgement=True,
        human_followup_required=True)
    if result is None or any(getattr(result, key) != getattr(request, key)
                            for key in ('request_id', 'asset_id', 'snapshot_id')):
        return state
    normalized = normalize_result(request, result)
    reliable = (result.status == 'completed' and not result.contradictory
                and not result.bad_audio and result.evidence_time_basis == 'source_observation'
                and normalized.identity_confirmed is True
                and normalized.whole_household_confirmed is True)

    def verified(key):
        return (bool(result.evidence.get(key, '').strip())
                and result.evidence_verification.get(key) == 'transcript_verified')

    # Identity, household authority and receipt all need their own verified evidence.
    reliable = reliable and verified('identity_confirmed') and verified('whole_household_confirmed')
    if reliable and verified('acknowledged') and verified('instruction_received'):
        state['instruction_acknowledged'] = normalized.acknowledged
    if reliable and verified('road_warning_acknowledged'):
        state['road_warning_acknowledged'] = normalized.road_warning_acknowledged
    state['requires_new_acknowledgement'] = (state['instruction_acknowledged'] is not True
        or bool(briefing.road_warnings) and state['road_warning_acknowledged'] is not True)
    state['human_followup_required'] = (normalized.human_followup_required
        or bool(briefing.human_followup_reasons) or state['requires_new_acknowledgement'])
    return state
