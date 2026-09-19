"""Interactions between buildings, reception sites and changing operational inputs."""
import pytest

from fireline.voice_replay import MockReplay


def by_id(state):
    return {r['asset_id']: r for r in state['plan']['locations']}


def finish(replay):
    while replay.state()['pending_events']:
        replay.advance()
    return replay.state()


def assert_capacity(state):
    centres = {c['centre_id']: c for c in state['reception_centres']}
    people = {p['asset_id']: p['people'] for p in state['layout'] if p['kind'] == 'building'}
    allocated = {cid: 0 for cid in centres}
    for row in state['plan']['locations']:
        if row['destination_id']:
            cid = row['destination_id']
            assert centres[cid]['approved']
            allocated[cid] += people[row['asset_id']]
    for cid, centre in centres.items():
        assert allocated[cid] <= centre['remaining_places']
        assert state['plan']['remaining_capacity'][cid] == centre['remaining_places'] - allocated[cid]


def test_neighbourhood_has_real_interactions_and_late_priority_changes_allocation(tmp_path):
    s = MockReplay(tmp_path / 'village.sqlite', 'village_shared_capacity')
    initial = s.state()
    assert len(initial['plan']['locations']) == 6
    assert len(initial['reception_centres']) == 2
    assert [r['asset_id'] for r in initial['plan']['contacts']['ranked']] == ['CARE', 'SCHOOL', 'H1', 'H2', 'H3', 'DEPOT']
    assert len({e['interview']['asset_id'] for e in s.case['events']}) == 6
    # CARE, SCHOOL, then lower-priority H3/H2 answer before H1.
    for _ in range(4):
        s.advance()
        assert_capacity(s.state())
    assert by_id(s.state())['H2']['destination_id'] == 'HALL'
    s.advance()
    rows = by_id(s.state())
    assert rows['H1']['destination_id'] == 'HALL'
    assert rows['H2']['destination_id'] == 'ANNEX'
    assert rows['H3']['destination_id'] == 'HALL'
    assert {'H1', 'H2'} <= set(s.updates()[-1]['changed_asset_ids'])
    state = finish(s)
    assert_capacity(state)
    assert by_id(state)['DEPOT']['mode'] == 'undetermined'
    assert state['plan']['remaining_capacity'] == {'HALL': 0, 'ANNEX': 0}
    assert all(r['evacuation_status'] == 'not_confirmed' for r in state['plan']['locations'])
    s.close()


def test_many_calls_keep_each_households_answers_separate(tmp_path):
    s = MockReplay(tmp_path / 'mixed.sqlite', 'village_mixed_escalations')
    state = finish(s)
    rows = by_id(state)
    assert rows['CARE']['mode'] == rows['H2']['mode'] == rows['H3']['mode'] == 'undetermined'
    assert rows['SCHOOL']['mode'] == 'assisted_evacuation'
    assert rows['H1']['mode'] == rows['DEPOT']['mode'] == 'self_evacuate'
    calls = {c['asset_id']: c for c in state['calls']}
    assert calls['CARE']['reported_needs_assistance'] is True
    assert 'low_confidence' in calls['CARE']['human_followup_reasons']
    assert calls['H2']['wants_human'] is True
    assert calls['H3']['status'] == 'no_answer'
    assert calls['H1']['human_followup_reasons'] == []
    assert_capacity(state)
    s.close()


@pytest.mark.parametrize('case', ['village_road_closure', 'village_centre_closes', 'village_fire_turns',
                                  'village_new_help_report', 'village_resource_loss'])
def test_neighbourhood_updates_preserve_work_and_respect_constraints(tmp_path, case):
    s = MockReplay(tmp_path / (case + '.sqlite'), case)
    for _ in range(6):
        s.advance()
    before = s.state()
    task = next(t for t in before['tasks'] if t['asset_id'] == 'CARE')
    s.store.tasks.set_status(task['task_id'], 'in_progress')
    state = finish(s)
    rows = by_id(state)
    assert_capacity(state)
    assert next(t for t in state['tasks'] if t['task_id'] == task['task_id'])['status'] == 'in_progress'
    assert state['response_review_required']
    if case == 'village_road_closure':
        assert rows['H1']['destination_id'] is None
        assert rows['H2']['destination_id'] == 'HALL'
        assert rows['DEPOT']['destination_id'] == 'ANNEX'
    elif case == 'village_centre_closes':
        assert all(r['destination_id'] != 'HALL' for r in rows.values())
        assert rows['H2']['destination_id'] == 'ANNEX'
        assert rows['H1']['mode'] == rows['H3']['mode'] == 'undetermined'
    elif case == 'village_fire_turns':
        assert state['plan']['contacts']['ranked'][0]['asset_id'] == 'H2'
        assert state['plan']['contacts']['ranked'][0]['slack_min'] == -1
        assert rows['H2']['destination_id'] is None
    elif case == 'village_new_help_report':
        assert rows['H1']['mode'] == 'assisted_evacuation'
        assert len([c for c in state['calls'] if c['asset_id'] == 'H1']) == 2
        assert any(t['asset_id'] == 'H1' and t['reason'] == 'voice:arrange_assistance' for t in state['tasks'])
    else:
        assert {'act_CARE', 'act_SCHOOL'} <= set(state['plan']['response']['blocked_actions'])
        assert 'CARE' in state['plan']['response']['unserved']
    revision = state['revision']
    s.apply_event(0)
    assert s.state()['revision'] == revision
    s.close()
    s = MockReplay(tmp_path / (case + '.sqlite'), case)
    assert s.state()['revision'] == revision
    assert_capacity(s.state())
    s.close()
