"""Scenario end-to-end on the Gavarres fixtures with a synthetic distance-based arrival raster."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point

from fireline import config
from fireline.exposure import OptimisticMoveError, TIER_RANK
from fireline.fire_state import FireState
from fireline.grid import lonlat_to_xy
from fireline.routing import RoadGraph
from fireline.scenario import Scenario
from tests.test_exposure import IGNITION, make_arrival

FIX = Path(__file__).resolve().parents[1] / "fixtures"
T0 = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)


def fire_state():
    x, y = lonlat_to_xy(*IGNITION)
    return FireState("test-cluster", T0, Point(x, y).buffer(200), hotspots=[], fros_dir_deg=175,
                     fros_speed_mps=0.5, wind_dir_deg=340, wind_speed_mps=8)


@pytest.fixture(scope="module")
def arrival():
    return make_arrival()


def run(arrival, closures=None):
    assets = json.load(open(FIX / "assets.json"))
    dests = json.load(open(FIX / "destinations.json"))
    rg = RoadGraph.from_fixture(FIX / "roads.json")
    return Scenario.run(fire_state(), arrival, assets, dests, rg, config, closures=closures)


@pytest.fixture(scope="module")
def sc(arrival):
    return run(arrival)


def by_id(scn, aid):
    return scn.asset(aid)


def test_scenario_id_and_shape(sc, arrival):
    assert re.fullmatch(r"[0-9a-f]{12}", sc.id)
    assert sc.id == run(arrival).id                    # deterministic
    assert sc.cluster_id == "test-cluster" and sc.t == T0 and sc.config_hash == config.config_hash()
    assert len(sc.assets) == 12
    for a in sc.assets:
        assert "decision" in a and "route" in a and a["decision"]["asset_id"] == a["asset_id"]
        assert a["decision"]["route"] is a["route"]
    ranks = [TIER_RANK[a["tier"]] for a in sc.assets]
    assert ranks == sorted(ranks)
    laps = [a["lead_adjusted_p10_min"] for a in sc.assets if a["tier"] == "act_now"]
    assert laps == sorted(laps)
    assert sc.change_log and "12 assets" in sc.change_log[0]
    assert sc.queue == []


def test_fixture_story(sc):
    pou = by_id(sc, "fixture:pou_del_glac")
    assert pou["tier"] == "act_now"
    assert {"occupancy_unknown", "occupancy_seasonal"} <= set(pou["needs_review"])
    assert pou["decision"]["decision"] in ("confine", "evacuate")
    assert by_id(sc, "fixture:sant_pol")["tier"] == "act_now"
    vall = by_id(sc, "fixture:vall_repos")
    assert vall["tier"] in ("prepare", "act_now")
    assert vall["decision"]["decision"] == "confine"                       # care-home default
    assert vall["decision"]["medical_destination"] in ("Hospital de Palamós", "Residència Palamós")
    assert vall["decision"]["reception_centre"] is None
    can_xic = by_id(sc, "fixture:can_xic")
    assert can_xic["tier"] == "act_now" and "no_exit" not in can_xic["needs_review"]
    assert can_xic["route"]["via_track"] and any("track" in n for n in can_xic["notes"])
    camping = by_id(sc, "fixture:camping_gavarres")
    assert "class_ambiguous" in camping["needs_review"]
    assert camping["route"]["destination_name"] == "Pavelló de Llagostera"   # not north through the fire
    assert "occupancy_unknown" in by_id(sc, "fixture:residencia_la_bisbal")["needs_review"]
    escola = by_id(sc, "fixture:escola_cruilles")
    assert escola["decision"]["staged"] == "confine now, evacuate when bus and route confirmed"
    hosp = by_id(sc, "fixture:hospital_palamos")
    assert hosp["tier"] == "monitor" and hosp["route"]["destination_name"] == "Residència Palamós"
    for aid in ("fixture:sant_sadurni", "fixture:monells"):
        assert by_id(sc, aid)["tier"] == "monitor" and by_id(sc, aid)["decision"]["decision"] == "monitor"
    assert all("no_exit" not in a["needs_review"] for a in sc.assets)


def test_can_xic_no_exit_when_track_cut(arrival):
    sc2 = run(arrival, closures=["Camí de Can Xic"])
    can_xic = by_id(sc2, "fixture:can_xic")
    assert "no_exit" in can_xic["needs_review"] and can_xic["route"] is None
    assert can_xic["decision"]["decision"] == "confine_request_protection"
    assert sc2.closures == ["Camí de Can Xic"]
    d = run(arrival).diff(sc2)
    assert len(d) == 1 and d[0].startswith("fixture:can_xic") and "decision" in d[0]


def test_escalation_applies_pessimistic_default_and_can_be_answered(arrival):
    sc = run(arrival)
    camping = by_id(sc, "fixture:camping_gavarres")
    assert camping["decision"]["decision"] == "evacuate"
    esc = sc.add_escalation("fixture:camping_gavarres", "Places are tents or bungalows?", ["tents", "bungalows"],
                            "tents", field="shelter_viable", default_value=False)
    assert esc["status"] == "open" and esc["answer"] is None and esc["default"] == "tents"
    assert sc.queue == [esc] and esc["asset_id"] == "fixture:camping_gavarres"
    assert camping["shelter_viable"] is False
    assert camping["overrides"][-1]["source"] == "escalation default"
    assert camping["decision"]["checks"]["shelter_viable"]["ok"] is False
    assert any("escalated" in line for line in sc.change_log)
    # occupancy default on the camp, pessimistic and re-decided
    sc.add_escalation("fixture:pou_del_glac", "Children on site today?", ["yes", "no"], "yes",
                      field="occupancy", default_value=80)
    pou = by_id(sc, "fixture:pou_del_glac")
    assert pou["occupancy"] == 80 and "occupancy_unknown" not in pou["needs_review"]
    # an optimistic default is refused
    with pytest.raises(OptimisticMoveError):
        sc.add_escalation("fixture:pou_del_glac", "Fewer?", ["yes", "no"], "yes", field="occupancy", default_value=10)
    with pytest.raises(ValueError):
        sc.add_escalation("fixture:pou_del_glac", "?", ["yes", "no"], "maybe")
    assert len(sc.queue) == 2
    answered = sc.answer_escalation(esc["escalation_id"], "bungalows", value=True)
    assert answered["status"] == "answered" and answered["answer"] == "bungalows"
    assert camping["shelter_viable"] is True and camping["overrides"][-1]["direction"] == "human"
    with pytest.raises(ValueError):
        sc.answer_escalation(esc["escalation_id"], "igloo")
    with pytest.raises(KeyError):
        sc.answer_escalation("esc-999", "yes")


def test_json_round_trip(arrival, tmp_path):
    sc = run(arrival)
    esc = sc.add_escalation("fixture:can_xic", "Track passable by car?", ["yes", "no"], "no")
    path = sc.to_json(tmp_path / "scenario.json")
    assert path.exists() and (tmp_path / "scenario.npz").exists()
    raw = json.loads(path.read_text())
    assert raw["arrival_npz"] == "scenario.npz"
    back = Scenario.from_json(path)
    assert back.id == sc.id and back.cluster_id == sc.cluster_id and back.t == sc.t
    assert [a["asset_id"] for a in back.assets] == [a["asset_id"] for a in sc.assets]
    for a, b in zip(sc.assets, back.assets):
        assert a["tier"] == b["tier"] and a["decision"]["decision"] == b["decision"]["decision"]
        assert a["lead_adjusted_p10_min"] == b["lead_adjusted_p10_min"]   # inf survives
        assert (a["route"] is None) == (b["route"] is None)
        if a["route"]:
            assert a["route"]["path_lonlat"] == b["route"]["path_lonlat"]
    assert back.queue == sc.queue and back.queue[0]["escalation_id"] == esc["escalation_id"]
    assert back.change_log == sc.change_log
    assert back.arrival is not None and back.arrival.grid == arrival.grid
    np.testing.assert_array_equal(back.arrival.arrival_p10, arrival.arrival_p10)
    np.testing.assert_array_equal(back.arrival.burn_prob, arrival.burn_prob)
    assert back.diff(sc) == []
