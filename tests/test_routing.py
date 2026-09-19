"""Road graph, cut times and safe routing on the Gavarres fixture."""

import importlib.util
import json
import math
from pathlib import Path

import pytest

from fireline import config
from fireline.exposure import build_asset_table
from fireline.routing import RoadGraph, best_route, destination_kind_for, valid_destinations
from tests.test_exposure import make_arrival, make_no_fire

FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def arrival():
    return make_arrival()


@pytest.fixture
def graph():
    return RoadGraph.from_fixture(FIX / "roads.json")


@pytest.fixture(scope="module")
def destinations():
    return json.load(open(FIX / "destinations.json"))


@pytest.fixture(scope="module")
def assets_in():
    return json.load(open(FIX / "assets.json"))


def asset_row(assets_in, arrival, aid):
    return build_asset_table([a for a in assets_in if a["asset_id"] == aid], arrival)[0]


def test_fixture_graph_loads(graph):
    g = graph.g
    assert g.number_of_nodes() >= 25 and g.number_of_edges() >= 30
    names = {d["name"] for _, _, d in g.edges(data=True)}
    for road in ("GI-660", "C-66", "GIV-6612", "GI-664", "C-31"):
        assert road in names
    for _, _, d in g.edges(data=True):
        assert d["travel_min"] > 0 and d["closed"] is False and math.isinf(d["cut_min"])
    for n, d in g.nodes(data=True):
        assert {"lon", "lat", "x", "y"} <= set(d)
    track = [d for _, _, d in g.edges(data=True) if d["highway"] == "track"]
    assert len(track) == 1 and track[0]["name"] == "Camí de Can Xic"
    assert graph.nearest_node(3.030, 41.923) == "n_can_xic"
    assert graph.nearest_node(3.130, 41.850) == "n_hospital"


def test_cut_times_min_of_endpoints_and_midpoint(graph, arrival):
    cuts = graph.cut_times(arrival)
    assert set(cuts) == {tuple(sorted((u, v))) for u, v in graph.g.edges}
    track = cuts[("gi660_k4", "n_can_xic")]
    assert 30 < track < 90
    # the cut equals the min of the three samples
    du, dv = graph.g.nodes["n_can_xic"], graph.g.nodes["gi660_k4"]
    ends = [arrival.sample("arrival_p10", du["lon"], du["lat"]), arrival.sample("arrival_p10", dv["lon"], dv["lat"])]
    assert track <= min(ends)
    assert math.isinf(cuts[("cassa", "llagostera")])   # C-65, far west of the fire
    assert 200 < cuts[("bisbal", "corca")] < 720      # C-66 at la Bisbal, upwind but not far
    assert graph.g["gi660_k4"]["n_can_xic"]["cut_min"] == track


def test_route_avoids_cut_edge(graph, arrival, destinations, assets_in):
    camping = asset_row(assets_in, arrival, "fixture:camping_gavarres")
    # No fire: fastest way to a pavelló (Espai Ridaura left out, it is the nearest when nothing burns)
    # is north along the GI-660 to la Bisbal.
    pavellons = [d for d in destinations if d["name"] != "Espai Ridaura"]
    quiet = make_no_fire()
    r0 = best_route(graph, camping, pavellons, quiet, 0.0, config)
    assert r0 is not None and r0["destination_name"] == "Pavelló de la Bisbal"
    assert "gi660_k8" in r0["path_nodes"] and math.isinf(r0["first_cut_min"]) and r0["first_cut_road"] is None
    # Fire spreading SSE from la Bisbal cuts the GI-660 in about an hour: route goes south to Llagostera.
    r1 = best_route(graph, camping, destinations, arrival, 0.0, config)
    assert r1 is not None
    assert "gi660_k8" not in r1["path_nodes"] and "gi660_k4" not in r1["path_nodes"]
    assert r1["destination_name"] == "Pavelló de Llagostera" and r1["destination_kind"] == "reception"
    assert r1["first_cut_min"] > config.LOAD_TIME_MIN["campsite"] + config.ROUTE_BUFFER_MIN + r1["travel_min"]
    assert r1["first_cut_road"] is not None and r1["travel_min"] > r0["travel_min"]
    assert r1["path_lonlat"][0] == (camping["lon"], camping["lat"])
    assert r1["path_lonlat"][-1] == (2.894, 41.830)
    assert r1["via_track"] is False


def test_closures_remove_edges(graph, arrival, destinations, assets_in):
    cabanyes = asset_row(assets_in, arrival, "fixture:les_cabanyes")
    pavellons = [d for d in destinations if d["name"] != "Espai Ridaura"]
    quiet = make_no_fire()
    r0 = best_route(graph, cabanyes, pavellons, quiet, 0.0, config)
    roads0 = {graph.g[u][v]["name"] for u, v in zip(r0["path_nodes"][:-1], r0["path_nodes"][1:])}
    assert "C-31" in roads0 and r0["destination_name"] == "Pavelló de Llagostera"
    assert graph.apply_closures(["C-31"]) == 5
    assert graph.closed_names == ["C-31"]
    r1 = best_route(graph, cabanyes, pavellons, quiet, 0.0, config)
    assert r1 is not None and r1["travel_min"] > r0["travel_min"]
    roads1 = {graph.g[u][v]["name"] for u, v in zip(r1["path_nodes"][:-1], r1["path_nodes"][1:])}
    assert "C-31" not in roads1 and "GI-660" in roads1 and r1["destination_name"] == "Pavelló de la Bisbal"
    # closing the whole GI-660 as well leaves Les Cabanyes without any exit
    graph.apply_closures(["GI-660"])
    assert best_route(graph, cabanyes, pavellons, quiet, 0.0, config) is None
    graph.clear_closures()
    assert graph.closed_names == []
    assert best_route(graph, cabanyes, pavellons, quiet, 0.0, config)["path_nodes"] == r0["path_nodes"]


def test_can_xic_only_exit_is_the_track(graph, arrival, destinations, assets_in):
    can_xic = asset_row(assets_in, arrival, "fixture:can_xic")
    r = best_route(graph, can_xic, destinations, arrival, 0.0, config)
    assert r is not None and r["via_track"] is True and r["first_cut_road"] == "Camí de Can Xic"
    # track closed (coordinator says impassable) -> no route
    graph.apply_closures(["Camí de Can Xic"])
    assert best_route(graph, can_xic, destinations, arrival, 0.0, config) is None
    graph.clear_closures()
    # track cut by the fire: ignition right on the track
    on_track = make_arrival(ign_lon=3.037, ign_lat=41.924)
    graph.cut_times(on_track)
    assert graph.cut_of("n_can_xic", "gi660_k4") < 5
    assert best_route(graph, can_xic, destinations, on_track, 0.0, config) is None
    # no valid destination: only medical ones, or only a reception centre inside the envelope
    medical_only = [d for d in destinations if d["kind"] == "medical"]
    assert best_route(graph, can_xic, medical_only, arrival, 0.0, config) is None
    ridaura = [d for d in destinations if d["name"] == "Espai Ridaura"]
    assert arrival.sample("burn_prob", ridaura[0]["lon"], ridaura[0]["lat"]) >= config.DEST_BURN_PROB_MAX
    assert best_route(graph, can_xic, ridaura, arrival, 0.0, config) is None
    assert best_route(graph, can_xic, [], arrival, 0.0, config) is None


def test_destination_kind_and_validity(arrival, destinations):
    assert destination_kind_for("care_home") == "medical" and destination_kind_for("hospital") == "medical"
    for cls in ("camp", "school", "campsite", "nucleus", "masia"):
        assert destination_kind_for(cls) == "reception"
    ok = {d["name"] for d in valid_destinations(destinations, "reception", arrival)}
    assert ok == {"Pavelló de la Bisbal", "Pavelló de Llagostera"}       # Espai Ridaura inside envelope
    med = valid_destinations(destinations, "medical", arrival, exclude_id="fixture:hospital_palamos")
    assert [d["name"] for d in med] == ["Residència Palamós"]


def test_hospital_is_not_its_own_destination(graph, arrival, destinations, assets_in):
    hosp = asset_row(assets_in, arrival, "fixture:hospital_palamos")
    r = best_route(graph, hosp, destinations, arrival, 0.0, config)
    assert r is not None and r["destination_name"] == "Residència Palamós"


def test_from_osmnx_without_osmnx_raises_clear_import_error():
    if importlib.util.find_spec("osmnx") is not None:
        pytest.skip("osmnx installed")
    with pytest.raises(ImportError, match="osmnx"):
        RoadGraph.from_osmnx((2.85, 41.80, 3.20, 42.05))
