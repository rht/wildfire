"""Exact one-crew planning over <=8 declared actions, directed legs and time windows.

Benefits are accounting assumptions, not predictions of saved lives or suppression success.
Coverage is maximum-per-asset, with no transitive or proximity-inferred effects.
"""
from dataclasses import dataclass
from .priority_models import Travel, validate_scenario


@dataclass
class State:
    site: str
    time: float
    done: frozenset
    coverage: dict
    benefit_time: tuple
    steps: list


class Planner:
    def __init__(self, scenario):
        validate_scenario(scenario)
        self.s = scenario
        self.locations = {a.asset_id:a for a in sorted(scenario.locations,key=lambda a:a.asset_id)}
        self.actions = sorted(scenario.actions,key=lambda a:a.action_id)
        self.review = []
        self.blocked = {}
        self.effects = {}
        for action in self.actions:
            reasons=[]
            site=self.locations[action.site_id]
            if site.deadline_min is None or action.deadline_min is None:
                reasons.append('unknown action or work-site deadline')
            missing=set(action.capabilities)-set(scenario.capabilities)
            if missing:
                reasons.append('missing capabilities: '+', '.join(sorted(missing)))
            if reasons:
                self.blocked[action.action_id]=reasons
            eligible=[]
            for effect in sorted(action.benefits,key=lambda b:b.asset_id):
                target=self.locations[effect.asset_id]
                missing=[key for key in ('people','assisted','value','deadline_min') if getattr(target,key) is None]
                if not effect.confirmed or missing:
                    self.review.append({'action_id':action.action_id,'asset_id':effect.asset_id,
                                        'reason':'unconfirmed effect' if not effect.confirmed else 'unknown '+', '.join(missing)})
                else:
                    eligible.append(effect)
            self.effects[action.action_id]=eligible
        for key,reasons in self.blocked.items():
            self.review.append({'action_id':key,'reason':'; '.join(reasons)})

    def initial(self):
        return State(self.s.start_id,0,frozenset(),dict.fromkeys(self.locations,0.0),(0.,0.,0.),[])

    def extend(self,state,action):
        if action.action_id in state.done or action.action_id in self.blocked:
            return None
        if not set(action.requires)<=state.done:
            return None
        leg=Travel(0) if state.site==action.site_id else self.s.travel.get((state.site,action.site_id))
        if leg is None:
            return None
        arrive=state.time+leg.minutes
        if leg.available_until_min is not None and arrive+self.s.buffer_min>leg.available_until_min:
            return None
        finish=arrive+action.duration_min
        site=self.locations[action.site_id]
        if finish+self.s.buffer_min>min(self.s.horizon_min,action.deadline_min,site.deadline_min):
            return None
        coverage=dict(state.coverage)
        timing=list(state.benefit_time)
        gained={}
        for effect in self.effects[action.action_id]:
            target=self.locations[effect.asset_id]
            if finish+self.s.buffer_min>target.deadline_min:
                continue
            gain=max(0,effect.coverage-coverage[effect.asset_id])
            coverage[effect.asset_id]+=gain
            if gain:
                gained[effect.asset_id]=gain
                for i,amount in enumerate((target.assisted,target.people,target.value)):
                    timing[i]+=gain*amount*finish
        step={'action_id':action.action_id,'site_id':action.site_id,'depart_min':state.time,
              'travel_min':leg.minutes,'start_min':arrive,'finish_min':finish,
              'prerequisites':list(action.requires),'coverage_gained':gained,
              'benefit_evidence':{e.asset_id:e.evidence for e in self.effects[action.action_id] if e.asset_id in gained}}
        return State(action.site_id,finish,state.done|{action.action_id},coverage,tuple(timing),state.steps+[step])

    def totals(self,state):
        return tuple(sum(state.coverage[key]*getattr(a,field) for key,a in self.locations.items()
                         if getattr(a,field) is not None) for field in ('assisted','people','value'))

    def key(self,state):
        # Preserve life-benefit dimensions before property; then prefer benefits delivered earlier.
        return (*self.totals(state),*(-v for v in state.benefit_time),-state.time,-len(state.steps))

    def better(self,candidate,current):
        ck,bk=self.key(candidate),self.key(current)
        if ck!=bk:
            return ck>bk
        return tuple(step['action_id'] for step in candidate.steps)<tuple(step['action_id'] for step in current.steps)

    def result(self,state,optimal,visited):
        totals=self.totals(state)
        return {'scenario_id':self.s.scenario_id,'optimal':optimal,'method':'exact enumeration' if optimal else 'direct-value greedy baseline',
                'states_evaluated':visited,'steps':state.steps,'finish_min':state.time,
                'objective':dict(zip(('assisted_units','people_units','value_units'),totals)),
                'coverage':state.coverage,
                'unserved':{key:'not fully covered within the chosen feasible plan; review resources, deadlines and assumptions'
                            for key,fraction in state.coverage.items() if fraction<1},
                'review':self.review,'blocked_actions':self.blocked,
                'assumptions':['one crew; no return-to-base requirement',
                               'directed end-to-end travel times and their availability are supplied, not inferred from GPS',
                               'benefits are declared scenario assumptions; no suppression or spread simulation',
                               'coverage units are not a prediction of lives saved']}


def plan_response(scenario):
    planner=Planner(scenario)
    best=planner.initial()
    first_best={}
    visited=0
    def search(state):
        nonlocal best,visited
        visited+=1
        if planner.better(state,best):
            best=state
        if state.steps:
            first=state.steps[0]['action_id']
            if first not in first_best or planner.better(state,first_best[first]):
                first_best[first]=state
        for action in planner.actions:
            next_state=planner.extend(state,action)
            if next_state is not None:
                search(next_state)
    search(planner.initial())
    result=planner.result(best,True,visited)
    result['first_action_alternatives']=[{'first_action':first,'sequence':[step['action_id'] for step in state.steps],
                                         'objective':dict(zip(('assisted_units','people_units','value_units'),planner.totals(state))),
                                         'finish_min':state.time} for first,state in sorted(first_best.items())]
    return result


def greedy_response(scenario):
    """Deliberately myopic comparator; choose feasible action with highest own-site value."""
    planner=Planner(scenario)
    state=planner.initial()
    visited=1
    while True:
        feasible=[(a,planner.extend(state,a)) for a in planner.actions]
        feasible=[(a,n) for a,n in feasible if n is not None]
        if not feasible:
            break
        _,state=min(feasible,key=lambda pair:(-(planner.locations[pair[0].site_id].value or 0),pair[0].action_id))
        visited+=1
    return planner.result(state,False,visited)
