"""Behavioural checks for static contact ranking and action-sequence planning."""
from dataclasses import replace
import itertools
import math

import pytest

from fireline.priority_models import Location, Benefit, Action, Travel, StaticScenario
from fireline.contact_priority import rank_contacts
from fireline.response_priority import plan_response


def loc(key, value=10, people=0, assisted=0, distance=200, deadline=20):
    return Location(key, key, 0, 0, distance, people, assisted, value, deadline)


def act(key, site, benefits=None, requires=(), duration=2, capabilities=()):
    return Action(key, site, duration, 20, tuple(benefits or [Benefit(site, 1, True, 'fixture')]),
                  tuple(requires), tuple(capabilities))


def scene(locations, actions, legs=None, horizon=10, capabilities=()):
    nodes = ['START'] + [a.asset_id for a in locations]
    if legs is None:
        legs = {(a, b): Travel(1) for a in nodes for b in nodes if a != b}
    return StaticScenario('test', tuple(locations), tuple(actions), legs, 'START',
                          horizon, tuple(capabilities), 0)


def ids(result):
    return [s['action_id'] for s in result['steps']]


def test_contacts_value_breaks_equal_distance_but_assisted_people_remain_visible():
    result = rank_contacts([loc('B', 80), loc('C', 20), loc('A', 100, 80, 60, 316)])
    assert [r['asset_id'] for r in result['ranked']] == ['A', 'B', 'C']
    assert 'assisted' in result['ranked'][0]['components']


def test_unknown_is_not_zero_and_ties_are_stable():
    result = rank_contacts([loc('Z'), loc('B'), loc('unknown', people=None, assisted=None)])
    assert [r['asset_id'] for r in result['ranked']] == ['B', 'Z']
    assert result['review'][0]['asset_id'] == 'unknown'
    assert result['review'][0]['score'] is None


def test_lookahead_takes_zero_benefit_prerequisite_before_high_value_detour():
    a, b, c = loc('A', 100, 80, 60, deadline=8), loc('B', 80), loc('C', 0)
    actions = [act('B', 'B'), act('C', 'C', [Benefit('C', 0, True, 'access only')]),
               act('A', 'A', requires=['C'], duration=3)]
    result = plan_response(scene([a,b,c], actions, horizon=7))
    assert ids(result) == ['C', 'A']
    assert result['objective']['assisted_units'] == 60
    assert result['optimal'] is True


def test_explicit_protection_prioritises_c_and_does_not_double_count_a():
    a, b, c = loc('A', 100, 80, 60), loc('B', 80), loc('C', 20)
    actions = [act('B','B'), act('C','C',[Benefit('C',1,True,'fixture'), Benefit('A',1,True,'fixture')]),
               act('A','A')]
    result = plan_response(scene([a,b,c], actions, horizon=6))
    assert ids(result) == ['C','B']
    assert result['objective']['people_units'] == 80
    assert result['coverage']['A'] == 1


def test_nearby_does_not_imply_protection_or_prerequisite():
    result = plan_response(scene([loc('A',100,80,60),loc('C',20)], [act('A','A'),act('C','C')], horizon=3))
    assert ids(result) == ['A']


def test_blocked_direction_and_missing_capability_are_not_traversed():
    s = scene([loc('A',100,80,60),loc('B',80)],
              [act('A','A',capabilities=['medical']),act('B','B')],
              {('START','B'):Travel(1), ('B','A'):Travel(1)})
    result = plan_response(s)
    assert ids(result) == ['B']
    assert result['unserved']['A']
    s = replace(s, capabilities=('medical',), travel={('A','START'):Travel(1)})
    assert ids(plan_response(s)) == []


def test_deadline_includes_service_time_and_buffer():
    s = scene([loc('A',100,10,5,deadline=3)], [act('A','A')], horizon=10)
    assert ids(plan_response(s)) == ['A']  # completes exactly at 3
    assert ids(plan_response(replace(s, buffer_min=0.01))) == []
    s = replace(s, travel={('START','A'):Travel(1,0.5)})
    assert ids(plan_response(s)) == []


def test_unknown_and_unconfirmed_effects_do_not_gain_benefit():
    s = scene([loc('A',100,None,None),loc('C',20)],
              [act('C','C',[Benefit('A',1,False,'unverified'), Benefit('C',1,True,'fixture')])])
    result = plan_response(s)
    assert result['coverage']['A'] == 0
    assert result['review']
    assert result['objective']['people_units'] == 0


def test_partial_overlap_uses_maximum_not_sum():
    s = scene([loc('A',100,100,50),loc('B'),loc('C')],
              [act('B','B',[Benefit('A',0.6,True,'fixture')]),
               act('C','C',[Benefit('A',0.8,True,'fixture')])],horizon=6)
    result = plan_response(s)
    assert result['objective']['people_units'] == 80
    assert result['objective']['assisted_units'] == 40


def test_property_cannot_outweigh_assisted_people_and_input_order_does_not_matter():
    locations=[loc('A',1,1,1),loc('B',1_000_000)]
    actions=[act('B','B'),act('A','A')]
    s=scene(locations,actions,horizon=3)
    assert ids(plan_response(s))==['A']
    assert plan_response(s)==plan_response(replace(s,locations=tuple(reversed(locations)),actions=tuple(reversed(actions))))


def test_empty_scenario_and_unreachable_deadline_return_explicit_results():
    assert ids(plan_response(scene([],[])))==[]
    s=scene([loc('A',deadline=0)],[act('A','A')])
    assert plan_response(s)['unserved']['A']


@pytest.mark.parametrize('changes',[{'people':-1},{'assisted':3,'people':2},{'value':float('nan')},
                                    {'distance_m':float('inf')},{'deadline_min':-1}])
def test_invalid_location_fields_rejected(changes):
    with pytest.raises(ValueError):
        plan_response(scene([replace(loc('A'),**changes)],[act('A','A')]))


def test_cycles_duplicate_ids_and_large_search_rejected():
    with pytest.raises(ValueError,match='cycl'):
        plan_response(scene([loc('A'),loc('B')],[act('A','A',requires=['B']),act('B','B',requires=['A'])]))
    with pytest.raises(ValueError,match='duplicate'):
        rank_contacts([loc('A'),loc('A')])
    with pytest.raises(ValueError,match='eight|8'):
        plan_response(scene([loc(str(i)) for i in range(9)],[act(str(i),str(i)) for i in range(9)]))


def test_small_random_problems_match_independent_permutation_oracle():
    import random
    rng=random.Random(741)
    for trial in range(12):
        locations=[loc(str(i), rng.randint(1,100), rng.randint(1,20), 0,
                       deadline=rng.randint(3,12)) for i in range(4)]
        locations=[replace(a,assisted=rng.randint(0,a.people)) for a in locations]
        actions=[act(a.asset_id,a.asset_id,duration=rng.randint(1,3)) for a in locations]
        s=scene(locations,actions,horizon=9)
        best=(0,0,0)
        for count in range(5):
            for order in itertools.permutations(range(4),count):
                t=0; objective=[0,0,0]; valid=True
                for i in order:
                    t+=1+actions[i].duration_min
                    a=locations[i]
                    if t>min(a.deadline_min,s.horizon_min): valid=False; break
                    objective[0]+=a.assisted; objective[1]+=a.people; objective[2]+=a.value
                if valid: best=max(best,tuple(objective))
        result=plan_response(s)['objective']
        assert tuple(result[k] for k in ['assisted_units','people_units','value_units'])==best


def test_all_documented_edge_cases_match_their_expected_behaviour():
    from fireline.priority_examples import load_scenario, edge_cases, evaluate_case
    from pathlib import Path
    base=load_scenario(Path(__file__).resolve().parents[1]/'fixtures/static_priority.json')
    for case in edge_cases(base):
        outcome=evaluate_case(case)
        assert outcome['passed'], (case['case_id'],outcome['checks'],ids(outcome['response']))


def test_json_loader_rejects_duplicate_legs_and_wrong_schema():
    import json
    from pathlib import Path
    from fireline.priority_models import scenario_from_dict
    data=json.loads((Path(__file__).resolve().parents[1]/'fixtures/static_priority.json').read_text())
    data['travel'].append(dict(data['travel'][0]))
    with pytest.raises(ValueError,match='duplicate'):
        scenario_from_dict(data)
    data['schema_version']='wrong'
    with pytest.raises(ValueError,match='schema_version'):
        scenario_from_dict(data)
