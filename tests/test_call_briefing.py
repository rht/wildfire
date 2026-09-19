"""Offline synthetic content tests: no provider or operational contacts."""
from copy import deepcopy
from dataclasses import asdict, replace
import importlib

import pytest

from fireline.slng_voice import call_arguments
from fireline.voice_interview import interview_prompt, normalize_result
from tests.test_voice_interview import request as existing_request, result

WARNING = dict(road_id='mill', road_name='Mill Road', reason='bridge threatened by fire',
               source='synthetic road report')


def api():
    return importlib.import_module('fireline.call_briefing')


def asset(**changes):
    return dict(asset_id='B', name='Orchard House', road_warnings=[]) | changes


def approved(**changes):
    return dict(asset_id='B', snapshot_id='snapshot-demo', approved=True,
                plan_id='plan-B', revision=1, source='synthetic analyst approval',
                destination={'name': 'North Hall'},
                route={'instructions': 'Use Ridge Road to the north entrance.',
                       'road_ids': ['ridge'], 'road_names': ['Ridge Road'], 'feasible': True}) | changes


def contact(**changes):
    # Reuse the repository's explicitly synthetic fixture, never operational input.
    return dict(contact_number=existing_request().contact_number, language='en') | changes


def build(row=None, plan=None, **kwargs):
    return api().build_call_request(row or asset(), approved() if plan is None else plan,
        contact(), snapshot_id='snapshot-demo', request_id='req-B', **kwargs)


@pytest.mark.parametrize('name,destination,road', [
    ('Orchard House', 'North Hall', 'Ridge Road'),
    ('Harbour Flats', 'West School', 'Coast Road'),
])
def test_actual_household_destination_and_directions_reach_existing_template(name, destination, road):
    req = build(asset(name=name), approved(destination={'name': destination},
        route={'instructions': f'Use {road} to the entrance.', 'road_ids': ['route'],
               'road_names': [road], 'feasible': True}))
    args = call_arguments(req)
    assert name in args['incident_brief']
    assert destination in args['incident_brief']
    assert f'Use {road} to the entrance.' in args['incident_brief']
    assert 'Willow House' not in args['incident_brief']
    assert args['snapshot_id'] == 'snapshot-demo'
    assert args['asset_id'] == 'B'
    assert set(args) == {'request_id', 'asset_id', 'snapshot_id', 'incident_brief',
                         'scenario_notice', 'language', 'road_warning_brief'}
    assert req.input_mode == 'synthetic'


@pytest.mark.parametrize('changes,reason', [
    ({'approved': False}, 'approval_missing'),
    ({'approved': 'true'}, 'approval_missing'),
    ({'approved': 1}, 'approval_missing'),
    ({'destination': None}, 'destination_missing'),
    ({'destination': {'name': ''}}, 'destination_missing'),
    ({'route': None}, 'route_unavailable'),
    ({'route': {'instructions': 'Use Ridge Road.', 'feasible': False}}, 'route_unavailable'),
    ({'asset_id': 'other'}, 'recommendation_identity_mismatch'),
    ({'snapshot_id': 'old'}, 'recommendation_identity_mismatch'),
    ({'source': None}, 'approval_missing'),
    ({'revision': None}, 'approval_missing'),
    ({'conflicts': ['another destination was ordered']}, 'conflicting_guidance'),
])
def test_incomplete_unapproved_or_conflicting_guidance_is_withheld(changes, reason):
    plan = approved(**changes)
    briefing = api().build_call_briefing(asset(), plan, snapshot_id='snapshot-demo')
    req = build(plan=plan)
    assert briefing.guidance_status == 'readiness_only'
    assert reason in briefing.human_followup_reasons
    assert 'North Hall' not in req.incident_brief
    assert 'Use Ridge Road' not in req.incident_brief
    assert 'human' in req.incident_brief.lower()
    assert 'stay in place' not in req.incident_brief.lower()
    assert 'remain at' not in req.incident_brief.lower()


def test_absent_plan_preserves_unknowns_and_asks_readiness():
    briefing = api().build_call_briefing(asset(name=None), None, snapshot_id='snapshot-demo')
    assert briefing.guidance_status == 'readiness_only'
    assert briefing.destination is None
    assert briefing.route_instructions is None
    req = api().build_call_request(asset(name=None), None, contact(),
                                   snapshot_id='snapshot-demo', request_id='req-B')
    prompt = interview_prompt(req)
    assert 'everyone can leave without emergency assistance' in prompt
    assert 'suitable transport' in prompt
    assert 'want a person' in prompt
    assert 'acknowledgement is not departure or arrival' in prompt


@pytest.mark.parametrize('route', [
    {'instructions': 'Use the signed bridge.', 'road_ids': ['mill'], 'road_names': ['Bridge']},
    {'instructions': 'Use the signed bridge.', 'road_ids': ['other'], 'road_names': ['Mill Road']},
    {'instructions': 'Turn onto MILL road.', 'road_ids': ['other'], 'road_names': ['Other']},
])
def test_named_dangerous_road_overrides_conflicting_route_and_keeps_reason(route):
    row = asset(road_warnings=[WARNING])
    plan = approved(route=dict(route, feasible=True))
    req = build(row, plan)
    briefing = api().build_call_briefing(row, plan, snapshot_id='snapshot-demo')
    assert briefing.guidance_status == 'readiness_only'
    assert 'road_conflict' in briefing.human_followup_reasons
    assert 'North Hall' not in req.incident_brief
    args = call_arguments(req)
    assert 'Do not take Mill Road' in args['road_warning_brief']
    assert WARNING['reason'] in args['road_warning_brief']
    assert WARNING['source'] in args['road_warning_brief']


def test_current_warnings_take_precedence_without_dropping_other_restrictions():
    old = dict(WARNING, reason='old reported danger')
    second = dict(WARNING, road_id='east', road_name='East Road', reason='smoke')
    row = asset(road_warnings=[WARNING])
    plan = approved(road_warnings=[old, second])
    original = deepcopy((row, plan))
    req = build(row, plan)
    assert {w['road_id']: w['reason'] for w in req.road_warnings} == {
        'mill': 'bridge threatened by fire', 'east': 'smoke'}
    assert 'North Hall' in req.incident_brief
    req.road_warnings[0]['reason'] = 'changed output'
    assert (row, plan) == original


def test_contact_identity_mismatch_is_rejected_without_echoing_private_data():
    private = contact(asset_id='other')
    with pytest.raises(ValueError, match='contact asset mismatch') as exc:
        api().build_call_request(asset(), approved(), private,
                                 snapshot_id='snapshot-demo', request_id='req-B')
    assert private['contact_number'] not in str(exc.value)


def test_oversized_approved_instructions_are_rejected_not_truncated():
    plan = approved(route=dict(approved()['route'], instructions='Ridge Road ' * 200))
    with pytest.raises(ValueError, match='incident_brief'):
        build(plan=plan)


def test_live_content_retains_simulation_boundary_and_private_contact():
    req = build(input_mode='live')
    assert call_arguments(req)['scenario_notice'] == 'Analyst-authorized contact'
    assert req.contact_number not in repr(req)
    assert req.contact_number not in req.incident_brief


@pytest.mark.parametrize('field,reason', [('can_self_evacuate', None),
                                          ('transport_available', None),
                                          ('wants_human', 'human_requested')])
def test_readiness_answers_remain_evidenced_reports_with_a_human_path(field, reason):
    req = build(input_mode='live')
    value = field == 'wants_human'
    outcome = normalize_result(req, result(**{field: value}, confidence=.99,
                                          confidence_basis='llm_self_rating'))
    assert getattr(outcome, field) is value
    assert outcome.human_followup_required
    assert 'unverified_confidence' in outcome.human_followup_reasons
    if reason:
        assert reason in outcome.human_followup_reasons
    assert 'departed' not in asdict(outcome)
    assert 'arrived' not in asdict(outcome)


def acknowledged_result(**changes):
    evidence = dict(result().evidence, instruction_received='I understand the destination and route.',
                    road_warning_acknowledged='I must avoid Mill Road because of the bridge fire.')
    return result(evidence=evidence, road_warning_acknowledged=True,
                  evidence_verification={key: 'transcript_verified' for key in evidence}, **changes)


@pytest.mark.parametrize('change', ['instructions', 'destination', 'revision', 'warnings', 'snapshot'])
def test_changed_instructions_require_new_ack_without_erasing_past_call_facts(change):
    from tests.test_voice_store import store
    row, plan = asset(), approved()
    before = api().build_call_briefing(row, plan, snapshot_id='snapshot-demo')
    old_request = build(row, plan)
    old_result = acknowledged_result()
    original = deepcopy(asdict(old_result))
    with_store = store()
    try:
        with_store.register(old_request)
        with_store.bind('req-B', 'call-B')
        with_store.record_result(old_result)
        old_record = deepcopy(with_store.get('req-B'))
        snapshot = 'snapshot-demo'
        if change == 'instructions':
            plan['route']['instructions'] = 'Use Ridge Road to the south entrance.'
        elif change == 'destination':
            plan['destination']['name'] = 'East Annex'
        elif change == 'revision':
            plan['revision'] = 2
        elif change == 'warnings':
            row['road_warnings'] = [WARNING]
        else:
            snapshot = plan['snapshot_id'] = 'snapshot-new'
        after = api().build_call_briefing(row, plan, snapshot_id=snapshot)
        current = api().build_call_request(row, plan, contact(), snapshot_id=snapshot,
                                           request_id='req-new')
        with_store.register(current)
        assert before.instruction_version != after.instruction_version
        state = api().briefing_acknowledgement(after, current, old_result)
        assert state['requires_new_acknowledgement'] is True
        assert state['instruction_acknowledged'] is None
        assert state['instruction_version'] == after.instruction_version
        assert asdict(old_result) == original
        assert with_store.get('req-B') == old_record
        assert with_store.get('req-new')['result'] is None
        assert 'new acknowledgement' in current.incident_brief
    finally:
        with_store.close()


def test_warning_order_and_private_contact_do_not_invalidate_same_instruction_version():
    second = dict(WARNING, road_id='east', road_name='East Road')
    first = api().build_call_briefing(asset(road_warnings=[WARNING, second]), approved(),
                                     snapshot_id='snapshot-demo')
    second_brief = api().build_call_briefing(asset(road_warnings=[second, WARNING]),
        approved(confidence=.99, previous_call={'acknowledged': True}), snapshot_id='snapshot-demo')
    assert first.instruction_version == second_brief.instruction_version
    assert first.road_warning_version == second_brief.road_warning_version


@pytest.mark.parametrize('result_changes', [
    {'evidence_verification': {}},
    {'evidence_verification': {'instruction_received': 'provider_reported'}},
    {'acknowledged': None},
    {'acknowledged': False},
    {'status': 'in_progress'},
    {'contradictory': True},
    {'bad_audio': True},
    {'identity_confirmed': False},
    {'whole_household_confirmed': None},
    {'evidence_time_basis': 'receipt_only'},
])
def test_uncertain_or_unverified_receipt_cannot_acknowledge_current_instruction(result_changes):
    briefing = api().build_call_briefing(asset(), approved(), snapshot_id='snapshot-demo')
    live_result = replace(acknowledged_result(), confidence=1.0,
                          confidence_basis='llm_self_rating', **result_changes)
    status = api().briefing_acknowledgement(briefing, build(input_mode='live'), live_result)
    assert status['instruction_acknowledged'] is not True
    assert status['requires_new_acknowledgement'] is True
    assert status['human_followup_required'] is True


def test_verified_receipt_is_independent_of_confidence_and_never_means_evacuation():
    row = asset(road_warnings=[WARNING])
    briefing = api().build_call_briefing(row, approved(), snapshot_id='snapshot-demo')
    req = build(row, input_mode='live')
    status = api().briefing_acknowledgement(briefing, req,
        acknowledged_result(confidence=None, confidence_basis=None))
    assert status['instruction_acknowledged'] is True
    assert status['road_warning_acknowledged'] is True
    assert status['requires_new_acknowledgement'] is False
    assert status['human_followup_required'] is True  # existing live review gate
    assert 'departed' not in status
    assert 'arrived' not in status


def test_general_readback_is_not_receipt_of_instructions_or_road_warnings():
    row = asset(road_warnings=[WARNING])
    briefing = api().build_call_briefing(row, approved(), snapshot_id='snapshot-demo')
    general = result(evidence_verification={key: 'transcript_verified' for key in result().evidence})
    status = api().briefing_acknowledgement(briefing, build(row), general)
    assert status['instruction_acknowledged'] is None
    assert status['road_warning_acknowledged'] is None
    assert status['requires_new_acknowledgement'] is True


def test_changed_content_cannot_reuse_old_request_association():
    briefing = api().build_call_briefing(asset(), approved(revision=2), snapshot_id='snapshot-demo')
    with pytest.raises(ValueError, match='briefing request mismatch'):
        api().briefing_acknowledgement(briefing, build(), acknowledged_result())


def test_new_instruction_same_request_id_is_rejected_by_existing_store():
    from tests.test_voice_store import store
    s = store()
    try:
        s.register(build())
        with pytest.raises(ValueError):
            s.register(build(plan=approved(revision=2)))
    finally:
        s.close()


def test_plan_warnings_from_another_household_are_not_relayed():
    req = build(plan=approved(asset_id='other', road_warnings=[WARNING]))
    assert req.road_warnings == []


@pytest.mark.parametrize('route', [
    {'instructions': 'Use Ridge Road.', 'feasible': True},
    {'instructions': 'Use Ridge Road.', 'road_ids': [], 'road_names': [], 'feasible': True},
    {'instructions': 'Use Ridge Road.', 'road_ids': ['ridge'], 'road_names': [None], 'feasible': True},
])
def test_route_without_identified_roads_cannot_be_spoken_as_approved(route):
    assert 'North Hall' not in build(plan=approved(route=route)).incident_brief


def test_plan_revision_accepts_integer_subtypes_but_not_boolean_approval_versions():
    from enum import IntEnum

    class Revision(IntEnum):
        FIRST = 1

    assert 'North Hall' in build(plan=approved(revision=Revision.FIRST)).incident_brief
    assert 'North Hall' not in build(plan=approved(revision=True)).incident_brief
