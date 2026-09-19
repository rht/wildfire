"""Scenario-level tests: ranking -> interview evidence -> durable UI state."""

import pytest
import fcntl
import os

from fireline.voice_replay import MockReplay, load_cases


def test_lock_descriptor_is_closed_when_unlock_fails(tmp_path, monkeypatch):
    session = MockReplay(tmp_path / 'lock-failure.sqlite', 'baseline')
    descriptor = None
    real_flock = fcntl.flock

    def fail_unlock(fd, operation):
        nonlocal descriptor
        descriptor = fd if isinstance(fd, int) else fd.fileno()
        if operation == fcntl.LOCK_UN:
            raise OSError('unlock failed')
        real_flock(fd, operation)

    monkeypatch.setattr(fcntl, 'flock', fail_unlock)
    try:
        with pytest.raises(OSError, match='unlock failed'):
            session.state()
        with pytest.raises(OSError):
            os.fstat(descriptor)
    finally:
        session.close()
        try:
            os.close(descriptor)
        except OSError:
            pass


def row(state, aid='B'):
    return next(r for r in state['plan']['locations'] if r['asset_id'] == aid)


def replay(tmp_path, name):
    return MockReplay(tmp_path / (name + '.sqlite'), name)


def finish(session):
    while session.state()['pending_events']:
        session.advance()
    return session.state()


def test_initial_algorithms_and_incremental_update_are_visible(tmp_path):
    s = replay(tmp_path, 'baseline')
    before = s.state()
    assert [r['asset_id'] for r in before['plan']['contacts']['ranked']] == ['A', 'B', 'C']
    assert [r['slack_min'] for r in before['plan']['contacts']['ranked']] == [2, 8, 10]
    assert before['plan']['response']['sequence'] == ['act_C', 'act_A']
    assert all(r['mode'] == 'undetermined' for r in before['plan']['locations'])
    s.advance()
    assert row(s.state(), 'A')['mode'] == 'assisted_evacuation'
    assert s.state()['revision'] == before['revision'] + 1
    s.advance()
    assert row(s.state())['mode'] == 'self_evacuate'
    assert row(s.state())['destination_id'] == 'community_centre'
    assert all(r['evacuation_status'] == 'not_confirmed' for r in s.state()['plan']['locations'])
    s.close()


@pytest.mark.parametrize('low', [False, True])
@pytest.mark.parametrize('help_needed', [False, True])
@pytest.mark.parametrize('human', [False, True])
def test_all_eight_combinations_keep_requests_and_uncertainty(tmp_path, low, help_needed, human):
    name = f'matrix_low{int(low)}_help{int(help_needed)}_human{int(human)}'
    s = replay(tmp_path, name)
    state = finish(s)
    b = row(state)
    assert b['mode'] == ('undetermined' if low or human else
                         'assisted_evacuation' if help_needed else 'self_evacuate')
    call = state['calls'][0]
    assert call['reported_needs_assistance'] is help_needed
    assert call['wants_human'] is human
    assert ('low_confidence' in call['human_followup_reasons']) is low
    assert ('human_requested' in call['human_followup_reasons']) is human
    reasons = {t['reason'] for t in state['tasks'] if t['asset_id'] == 'B'}
    if low or human:
        assert 'voice:human_callback' in reasons
    elif help_needed:
        assert 'voice:arrange_assistance' in reasons
    assert b['evacuation_status'] == 'not_confirmed'
    assert state['dispatch'] is False and state['live_validation'] is False
    s.close()


@pytest.mark.parametrize('name,expected_mode', [
    ('confidence_at_threshold', 'self_evacuate'),
    ('confidence_below_threshold', 'undetermined'),
    ('unknown_confidence', 'undetermined'), ('missing_evidence', 'undetermined'),
    ('contradictory', 'undetermined'), ('no_answer', 'undetermined'),
    ('bad_audio', 'undetermined'), ('failed_transfer', 'undetermined'),
    ('failed_call', 'undetermined'), ('declined', 'undetermined'),
    ('partial_human_request', 'undetermined'), ('no_transport', 'assisted_evacuation'),
    ('centre_full', 'undetermined'), ('route_unconfirmed', 'undetermined'),
])
def test_adverse_and_boundary_scenarios(tmp_path, name, expected_mode):
    s = replay(tmp_path, name)
    state = finish(s)
    assert row(state)['mode'] == expected_mode
    if expected_mode == 'undetermined':
        assert row(state)['human_followup']
    s.close()


def test_restart_replay_and_other_cases_do_not_lose_or_duplicate_work(tmp_path):
    s = replay(tmp_path, 'baseline')
    state = finish(s)
    task = state['tasks'][0]
    s.store.tasks.set_status(task['task_id'], 'in_progress')
    # Duplicate delivery of a processed event must not create another update or task.
    s.apply_event(0)
    assert s.state()['revision'] == state['revision']
    assert len(s.state()['tasks']) == len(state['tasks'])
    s.close()
    s = replay(tmp_path, 'baseline')
    assert s.state()['revision'] == state['revision']
    assert next(t for t in s.state()['tasks'] if t['task_id'] == task['task_id'])['status'] == 'in_progress'
    s.close()
    with pytest.raises(ValueError, match='case'):
        MockReplay(tmp_path / 'baseline.sqlite', 'no_answer')


def test_updates_change_rank_and_resource_plan_without_claiming_dispatch(tmp_path):
    s = replay(tmp_path, 'fire_changes_priority')
    state = finish(s)
    assert [r['asset_id'] for r in state['plan']['contacts']['ranked']] == ['B', 'A', 'C']
    assert state['plan']['contacts']['ranked'][0]['status'] == 'window_exhausted'
    assert row(state)['mode'] == 'undetermined'
    assert row(state)['destination_id'] is None
    s.close()
    s = replay(tmp_path, 'crew_capability_lost')
    state = finish(s)
    assert state['plan']['response']['sequence'] == ['act_B', 'act_C']
    assert 'act_A' in state['plan']['response']['blocked_actions']
    assert state['response_review_required'] is True
    s.close()


def test_durable_update_feed_is_ordered_and_another_reader_sees_latest(tmp_path):
    writer = replay(tmp_path, 'baseline')
    reader = replay(tmp_path, 'baseline')
    writer.advance()
    assert reader.state()['revision'] == 1
    updates = reader.updates(after_revision=0)
    assert [e['revision'] for e in updates] == [1]
    assert updates[0]['changed_asset_ids'] == ['A']
    assert reader.updates(after_revision=1) == []
    with pytest.raises(ValueError, match='order'):
        writer.apply_event(2)
    writer.close()
    reader.close()


def test_all_cases_have_expected_outcomes_and_are_network_free(tmp_path, monkeypatch):
    import requests
    def forbidden(*args, **kwargs):
        raise AssertionError('mock replay must not use the network')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    for case in load_cases():
        s = replay(tmp_path, case['name'])
        state = finish(s)
        for aid, mode in case['expected_modes'].items():
            assert row(state, aid)['mode'] == mode, case['name']
        s.close()


def test_interrupted_event_rolls_back_facts_tasks_revision_and_feed(tmp_path, monkeypatch):
    s = replay(tmp_path, 'baseline')
    before = s.state()
    original = s.store.record_plan
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('simulated worker crash before publishing revision')
    monkeypatch.setattr(s.store, 'record_plan', interrupted)
    with pytest.raises(RuntimeError, match='simulated worker crash'):
        s.advance()
    s.close()
    s = replay(tmp_path, 'baseline')
    assert s.state() == before
    assert s.updates() == []
    s.advance()
    assert row(s.state(), 'A')['mode'] == 'assisted_evacuation'
    s.close()


def test_forecast_event_invalidates_indirectly_changed_ranks(tmp_path):
    s = replay(tmp_path, 'fire_changes_priority')
    finish(s)
    assert {'A', 'B'} <= set(s.updates()[-1]['changed_asset_ids'])
    assert s.updates()[-1]['refresh'] == 'full_state'
    s.close()


def test_existing_incident_database_is_rejected_without_modification(tmp_path):
    import sqlite3
    from fireline.tasks import TaskStore
    path = tmp_path / 'actual-incident.sqlite'
    store = TaskStore(path)
    store.create_task('real-facility', 'contact_facility', 'actual incident', snapshot_id='real-snapshot')
    store.close()
    def dump():
        with sqlite3.connect(path) as conn:
            return '\n'.join(conn.iterdump())
    before = dump()
    with pytest.raises(ValueError, match='dedicated mock'):
        MockReplay(path, 'baseline')
    assert dump() == before
