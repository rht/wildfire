"""Operational evidence stays explicit when adapted to the fleet planner."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from fireline.incident_planning import response_from_snapshot, scenario_from_snapshot
from fireline.voice_models import CallRequest, CallResult
from fireline.voice_store import VoiceStore

EPOCH = datetime(2026, 9, 20, 10, tzinfo=UTC)


def snapshot():
    return {"scenario_id": "s", "snapshot_id": "s-0001", "assets": [{
        "asset_id": "A", "name": "House", "longitude": 3.0, "latitude": 42.0,
        "distance_to_fire_m": 100, "estimated_occupancy": 2, "value_score": 10,
        "fire_arrival_at": (EPOCH + timedelta(minutes=120)).isoformat(),
        "evacuation_min": 10, "forecast_source": "provider", "evacuation_source": "policy"}]}


def operations():
    return {"snapshot_id": "s-0001", "assisted": {"A": 1}, "horizon_min": 180,
            "teams": [{"team_id": "T", "start_node_id": "BASE", "available": True,
                       "available_from_min": 0, "available_until_min": 180,
                       "transport_capacity": 2, "capabilities": []}],
            "actions": [{"action_id": "help-A", "asset_id": "A", "duration_min": 5,
                         "deadline_min": 120, "requires": [], "capabilities": [],
                         "transport_people": 0, "readiness_required": False,
                         "effects": [{"asset_id": "A", "coverage": 1,
                                      "confirmed": True, "source": "inspected"}]}],
            "routes": [{"from_node": "BASE", "to_node": "A", "minutes": 5,
                        "available_until_min": 180, "confirmed": True,
                        "safe": True, "source": "road desk"}],
            "road_nodes": {"BASE": [3.01, 42.01], "A": [3.0, 42.0]}}


@pytest.fixture
def voice():
    store = VoiceStore(epoch=EPOCH, clock=lambda: EPOCH)
    yield store
    store.close()


def plan(voice, ops=None, snap=None, now=EPOCH):
    ops = operations() if ops is None else ops
    snap = snapshot() if snap is None else snap
    scenario = scenario_from_snapshot(snap, ops, EPOCH)
    return response_from_snapshot(snap, scenario, ops, voice, now, EPOCH)


def tasks(result):
    return [task for team in result["teams"] for task in team["tasks"]]


def test_observed_crew_location_is_preserved_without_mutating_operations(voice):
    ops = operations()
    location = {"latitude": 41.99, "longitude": 3.02, "observed_at": EPOCH.isoformat(),
                "source": "crew radio report"}
    ops["teams"][0]["current_location"] = location
    before = deepcopy(ops)
    result = plan(voice, ops)
    assert result["teams"][0]["current_location"] == location
    assert ops == before
    assert len(tasks(result)) == 1


def test_start_node_coordinates_do_not_imply_crew_current_location(voice):
    result = plan(voice)
    start = result['teams'][0].get('planning_context', {}).get('start', {})
    assert start.get('latitude') == 42.01
    assert start.get('longitude') == 3.01
    assert start.get('source') == 'supplied operational start node'
    assert result["teams"][0]["current_location"] is None
    assert len(tasks(result)) == 1


@pytest.mark.parametrize("change", [
    {"latitude": 91}, {"longitude": -181}, {"latitude": float("nan")},
    {"longitude": True}, {"latitude": None}, {"source": ""},
    {"observed_at": "2026-09-20T10:00:00"},
    {"observed_at": "2026-09-20T10:01:00+00:00"},
])
def test_invalid_or_future_crew_location_is_rejected(voice, change):
    ops = operations()
    ops["teams"][0]["current_location"] = {
        "latitude": 42.0, "longitude": 3.0, "observed_at": EPOCH.isoformat(),
        "source": "radio", **change}
    with pytest.raises(ValueError):
        plan(voice, ops)


def test_stale_operational_snapshot_withholds_needs_and_work_but_keeps_reported_position(voice):
    ops = operations()
    ops["snapshot_id"] = "s-old"
    ops["teams"][0]["current_location"] = {
        "latitude": 42.0, "longitude": 3.0, "observed_at": EPOCH.isoformat(), "source": "radio"}
    scenario = scenario_from_snapshot(snapshot(), ops, EPOCH)
    assert scenario.locations[0].assisted is None
    result = plan(voice, ops)
    assert tasks(result) == []
    assert result["teams"][0]["current_location"] == ops["teams"][0]["current_location"]
    assert {"asset_id": "A", "reason": "operational_inputs_missing_or_stale"} in result["review"]


def test_unknown_facility_coordinates_block_crew_proposal(voice):
    snap = snapshot()
    snap["assets"][0].update(longitude=None, latitude=None)
    result = plan(voice, snap=snap)
    assert tasks(result) == []
    assert any("unknown_deadline" in row.get("reasons", []) for row in result["review"])


def test_removed_location_does_not_relax_action_prerequisites(voice):
    ops = operations()
    ops["actions"][0]["requires"] = ["access-removed"]
    ops["actions"].append({**deepcopy(ops["actions"][0]), "action_id": "access-removed",
                           "asset_id": "removed", "requires": [], "effects": []})
    assert tasks(plan(voice, ops)) == []


def assistance_call(voice):
    voice.register(CallRequest("request-A", "A", "s-0001", "+12025550123", "en", "test"))
    voice.bind("request-A", "provider-A")
    voice.record_result(CallResult("request-A", "A", "s-0001", "provider-A", "completed",
                                   EPOCH.isoformat(), "call", identity_confirmed=True,
                                   can_self_evacuate=False,
                                   evidence={"identity_confirmed": "yes", "can_self_evacuate": "need help"}))


def test_fresh_call_covers_intervention_start_without_fire_buffer(voice):
    assistance_call(voice)
    ops = operations()
    ops["actions"][0].update(transport_people=1, readiness_required=True)
    result = plan(voice, ops)
    assert len(tasks(result)) == 1
    assert tasks(result)[0]["readiness"]["valid_until_min"] == 15
    assert tasks(plan(voice, ops, now=EPOCH + timedelta(minutes=11))) == []


@pytest.mark.parametrize("duration", [0, -1, True, float("nan"), "60"])
def test_invalid_readiness_duration_is_rejected_even_without_calls(voice, duration):
    ops = operations()
    ops["readiness_validity_min"] = duration
    with pytest.raises(ValueError, match="readiness_validity_min"):
        plan(voice, ops)


def graph_operations():
    ops = operations()
    ops["routes"][0]["path_lonlat"] = [[3.01, 42.01], [3.005, 42.0], [3.0, 42.0]]
    return ops


def test_duplicate_graph_routes_are_rejected_instead_of_overwriting_evidence(voice):
    ops = graph_operations()
    ops["routes"].append({**deepcopy(ops["routes"][0]), "safe": False})
    with pytest.raises(ValueError, match="duplicate directed route"):
        plan(voice, ops)


@pytest.mark.parametrize("path", [
    None, [], [[3.01, 42.01]], [[3.01, 42.01], [3.0, 91]],
    [[3.01, 42.01], [float("nan"), 42.0]],
    [[3.01, 42.01], [True, 42.0]],
    [[3.01, 42.01], [3.0, 42.0, 5]],
    [[3.01, 42.01], [3.005, 42.005]],
])
def test_invalid_graph_geometry_is_rejected_even_when_team_unavailable(voice, path):
    ops = graph_operations()
    ops["routes"][0]["path_lonlat"] = path
    ops["teams"][0]["available"] = False
    with pytest.raises(ValueError):
        plan(voice, ops)


def test_graph_route_requires_declared_endpoint_nodes(voice):
    ops = graph_operations()
    del ops["road_nodes"]["A"]
    with pytest.raises(ValueError, match="road node"):
        plan(voice, ops)


def test_reversed_valid_route_is_oriented_without_mutating_source_geometry(voice):
    ops = graph_operations()
    ops["routes"][0]["path_lonlat"].reverse()
    before = deepcopy(ops)
    result = plan(voice, ops)
    assert tasks(result)[0]["path_lonlat"] == [[3.01, 42.01], [3.005, 42.0], [3.0, 42.0]]
    assert ops == before


@pytest.mark.parametrize("point", [[181, 42], [3, 91], [3, None], [3], [True, 42]])
def test_invalid_declared_road_node_coordinates_are_rejected(voice, point):
    ops = graph_operations()
    ops["road_nodes"]["BASE"] = point
    with pytest.raises(ValueError):
        plan(voice, ops)


def test_call_from_another_snapshot_does_not_authorize_current_rescue(voice):
    assistance_call(voice)
    ops = operations()
    ops["actions"][0].update(transport_people=1, readiness_required=True)
    ops["readiness_validity_min"] = 60
    ops["snapshot_id"] = "s-0002"
    snap = snapshot()
    snap["snapshot_id"] = "s-0002"
    assert tasks(plan(voice, ops, snap=snap)) == []


def test_voice_epoch_mismatch_cannot_authorize_readiness(voice):
    assistance_call(voice)
    scenario = scenario_from_snapshot(snapshot(), operations(), EPOCH)
    with pytest.raises(ValueError, match="epoch"):
        response_from_snapshot(snapshot(), scenario, operations(), voice, EPOCH,
                               EPOCH - timedelta(minutes=1))


def test_supplied_early_forecast_and_long_duration_drive_fragility_without_invented_bounds(voice):
    ops = operations()
    ops['actions'][0]['duration_high_min'] = 20
    snap = snapshot()
    snap['assets'][0]['arrival_p10_at'] = (EPOCH + timedelta(minutes=45)).isoformat()
    result = plan(voice, ops, snap=snap)
    sensitivity = tasks(result)[0]['sensitivity']
    assert sensitivity['status'] == 'fragile'
    assert sensitivity['deadline_early_min'] == 45
    assert sensitivity['duration_high_min'] == 20
    assert tasks(plan(voice))[0]['sensitivity']['status'] == 'unknown'


def test_configured_search_budget_is_applied_to_planner(voice):
    ops = operations()
    ops['search'] = {'depth': 1, 'beam_width': 2, 'max_expansions': 3}
    result = plan(voice, ops)
    assert result['search_limits'] == {'depth': 1, 'beam_width': 2, 'max_expansions': 3}
    ops['search']['max_expansions'] = 0
    with pytest.raises(ValueError, match='search'):
        plan(voice, ops)


def test_future_call_fact_does_not_authorize_a_past_planning_time(voice):
    voice.clock = lambda: EPOCH + timedelta(minutes=1)
    voice.register(CallRequest('future', 'A', 's-0001', '+12025550123', 'en', 'test'))
    voice.bind('future', 'provider-future')
    voice.record_result(CallResult('future', 'A', 's-0001', 'provider-future', 'completed',
                                   (EPOCH + timedelta(minutes=1)).isoformat(), 'call',
                                   identity_confirmed=True, can_self_evacuate=False,
                                   evidence={'identity_confirmed': 'yes', 'can_self_evacuate': 'need help'}))
    ops = operations()
    ops['actions'][0].update(transport_people=1, readiness_required=True)
    assert tasks(plan(voice, ops)) == []
