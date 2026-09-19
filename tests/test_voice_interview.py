"""Synthetic interviews must not turn uncertainty into evacuation claims."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import importlib
import pytest
from fireline.evacuation_readiness import coordinate_evacuation, ReceptionCentre, EvacuationRoute
from fireline.priority_examples import load_scenario

EPOCH = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
FIELDS = ('identity_confirmed', 'whole_household_confirmed', 'can_self_evacuate',
          'transport_available', 'wants_human', 'acknowledged')


def api():
    from fireline.voice_models import CallRequest, CallResult
    from fireline.voice_interview import normalize_result, to_assessment
    return CallRequest, CallResult, normalize_result, to_assessment


def request(**changes):
    CallRequest, *_ = api()
    return CallRequest(**(dict(request_id='req-B', asset_id='B', snapshot_id='snapshot-demo',
        contact_number='+12025550123', language='en', incident_brief='SIMULATION: analyst brief only.',
        human_callback_number=None, input_mode='synthetic') | changes))


def result(**changes):
    _, CallResult, *_ = api()
    return CallResult(**(dict(request_id='req-B', asset_id='B', snapshot_id='snapshot-demo',
        provider_call_id='call-B', status='completed', observed_at=EPOCH.isoformat(),
        source='synthetic interview', identity_confirmed=True, whole_household_confirmed=True,
        can_self_evacuate=True, transport_available=True, wants_human=False, acknowledged=True,
        confidence=.95, confidence_basis='synthetic_review',
        evidence={f: 'Synthetic respondent: confirmed answer.' for f in FIELDS}) | changes))


def proposal(r, req=None, **kwargs):
    _, _, normalize, adapt = api()
    req = req or request()
    normalized = normalize(req, r, **kwargs)
    call = adapt(req, normalized, provider_call_id='call-B', epoch=EPOCH, now=EPOCH)
    scenario = load_scenario('fixtures/static_priority.json')
    plan = coordinate_evacuation(scenario, [call],
        [ReceptionCentre('centre', 'Designated reception', 10, True, 60, 'synthetic approval')],
        [EvacuationRoute('B', 'centre', 2, 4, True, 30, 'synthetic checked route')])
    return normalized, next(x for x in plan['locations'] if x['asset_id'] == 'B')


def test_confirmed_interview_proposes_self_evacuation_but_never_arrival():
    normalized, row = proposal(result())
    assert row['mode'] == 'self_evacuate'
    assert row['evacuation_status'] == 'not_confirmed'
    assert 'confirm_arrival' in row['tasks']
    assert not normalized.human_followup_required


def test_assistance_needed():
    _, row = proposal(result(can_self_evacuate=False))
    assert row['mode'] == 'assisted_evacuation'
    assert 'arrange_assistance' in row['tasks']


@pytest.mark.parametrize('changes,reason', [
    ({'wants_human': True}, 'human_requested'),
    ({'confidence': .2}, 'low_confidence'),
    ({'confidence': None}, 'unknown_confidence'),
    ({'confidence_basis': 'llm_self_rating'}, 'unverified_confidence'),
    ({'evidence': {}}, 'missing_evidence'),
    ({'contradictory': True}, 'contradictory_answers'),
    ({'status': 'no_answer'}, 'no_answer'),
    ({'status': 'failed'}, 'failed'),
    ({'status': 'declined'}, 'declined'),
    ({'status': 'ringing'}, 'incomplete_interview'),
    ({'acknowledged': None}, 'readback_unconfirmed'),
    ({'transfer_status': 'failed'}, 'transfer_failed'),
    ({'transfer_status': 'connected'}, 'transfer_unverified'),
    ({'bad_audio': True}, 'bad_audio'),
])
def test_uncertain_results_keep_human_path(changes, reason):
    normalized, row = proposal(result(**changes))
    assert normalized.human_followup_required
    assert reason in normalized.human_followup_reasons
    assert row['mode'] == 'undetermined'
    assert row['evacuation_status'] == 'not_confirmed'
    assert 'human_callback' in row['tasks']


def test_live_cannot_claim_synthetic_confidence_basis():
    _, row = proposal(result(), request(input_mode='live'))
    assert row['mode'] == 'undetermined'


@pytest.mark.parametrize('field', FIELDS)
def test_only_evidenced_answers_survive(field):
    evidence = asdict(result())['evidence']
    evidence.pop(field)
    normalized, row = proposal(result(evidence=evidence))
    assert getattr(normalized, field) is None
    assert row['mode'] == 'undetermined'


@pytest.mark.parametrize('changes', [
    {'contact_number': '123'}, {'request_id': ''}, {'input_mode': 'magic'},
    {'human_callback_number': 'not a number'}, {'language': ''}, {'incident_brief': ''},
])
def test_invalid_request_rejected(changes):
    with pytest.raises(ValueError):
        request(**changes)


@pytest.mark.parametrize('changes', [
    {'confidence': float('nan')}, {'confidence': True}, {'confidence': 1.1},
    {'can_self_evacuate': 'yes'}, {'observed_at': '2026-09-19T12:00:00'},
    {'status': 'evacuated'}, {'contradictory': 'false'}, {'evidence': {'x': 3}},
])
def test_invalid_result_rejected(changes):
    with pytest.raises(ValueError):
        result(**changes)


@pytest.mark.parametrize('changes', [
    {'asset_id': 'A'}, {'request_id': 'wrong'}, {'snapshot_id': 'wrong'},
    {'provider_call_id': 'wrong'}, {'observed_at': '2026-09-19T12:01:00Z'},
    {'observed_at': '2026-09-19T11:59:00Z'},
])
def test_adapter_rejects_mismatched_or_future_evidence(changes):
    with pytest.raises(ValueError):
        proposal(result(**changes))


def test_prompt_is_bounded_and_explicitly_simulated():
    interview = importlib.import_module('fireline.voice_interview')
    prompt = interview.interview_prompt(request())
    for text in ('AI', 'SIMULATION', 'one question', 'person', 'departure', 'arrival'):
        assert text in prompt
    assert request().incident_brief in prompt
