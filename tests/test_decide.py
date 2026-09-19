"""Decision rule on constructed inputs: the four outputs, care-home default, staged camp case."""

import math

import pytest

from fireline import config
from fireline.decide import ISSUER_NOTE, STAGED_BEDS, STAGED_BUS, decide


def asset(cls="nucleus", bp=0.9, lap10=60.0, **kw):
    d = {"asset_id": f"test:{cls}", "name": cls, "asset_class": cls, "burn_prob": bp,
         "arrival_p10_min": lap10 + config.LEAD_TIME_MIN[cls], "lead_adjusted_p10_min": lap10,
         "lead_time_min": config.LEAD_TIME_MIN[cls], "occupancy": 10}
    d.update(kw)
    return d


def route(cut=300.0, travel=20.0, kind="reception", name="Pavelló de la Bisbal"):
    return {"asset_id": "x", "destination_id": "d", "destination_name": name, "destination_kind": kind,
            "travel_min": travel, "first_cut_road": "GI-660" if math.isfinite(cut) else None,
            "first_cut_min": cut, "path_lonlat": []}


def test_evacuate_when_exposed_and_window_open():
    d = decide(asset(), route(cut=300, travel=20))
    assert d["decision"] == "evacuate" and d["staged"] is None
    assert d["exit_window_min"] == pytest.approx(300 - 45 - 20 - 20)
    assert d["latest_departure_min"] == pytest.approx(300 - 20 - 20)
    assert d["checks"]["exposed"]["ok"] and d["checks"]["window_open"]["ok"] and d["checks"]["shelter_viable"]["ok"]
    assert d["reception_centre"] == "Pavelló de la Bisbal" and d["medical_destination"] is None
    assert d["route"]["first_cut_min"] == 300
    assert d["issuer_note"] == ISSUER_NOTE


def test_confine_when_window_closed_and_shelter_viable():
    d = decide(asset(), route(cut=60, travel=20))
    assert d["decision"] == "confine"
    assert d["exit_window_min"] == pytest.approx(60 - 45 - 20 - 20)
    assert not d["checks"]["window_open"]["ok"]


def test_confine_request_protection_when_no_shelter():
    # campsite: tents, class default not viable
    d = decide(asset("campsite", bp=0.8, lap10=30), route(cut=40, travel=15))
    assert d["decision"] == "confine_request_protection"
    assert not d["checks"]["shelter_viable"]["ok"]
    assert "campsite" in d["checks"]["shelter_viable"]["evidence"]
    # isolated masia with no route at all
    d = decide(asset("masia", bp=1.0, lap10=-10, shelter_viable=False), None)
    assert d["decision"] == "confine_request_protection"
    assert d["exit_window_min"] is None and d["latest_departure_min"] is None
    assert d["reception_centre"] is None and d["route"] is None
    assert "no viable route" in d["checks"]["window_open"]["evidence"]
    # explicit shelter_viable True on a campsite overrides the class default
    d = decide(asset("campsite", bp=0.8, lap10=30, shelter_viable=True), route(cut=40, travel=15))
    assert d["decision"] == "confine"


def test_monitor_when_not_exposed():
    d = decide(asset(bp=0.05, lap10=math.inf), route(cut=math.inf, travel=20))
    assert d["decision"] == "monitor"
    assert not d["checks"]["exposed"]["ok"]
    assert math.isinf(d["exit_window_min"])
    # exposed by lead-adjusted horizon alone (low burn_prob) is still exposed
    d = decide(asset(bp=0.05, lap10=config.PREPARE_MIN), route())
    assert d["decision"] == "evacuate"


def test_care_home_and_hospital_default_to_confine():
    d = decide(asset("care_home", bp=0.5, lap10=100), route(cut=400, travel=15, kind="medical", name="Hospital de Palamós"))
    assert d["decision"] == "confine" and d["staged"] is None
    assert d["checks"]["window_open"]["ok"]           # window open, still confine
    assert d["medical_destination"] == "Hospital de Palamós" and d["reception_centre"] is None
    assert d["exit_window_min"] == pytest.approx(400 - 90 - 15 - 20)
    # clearly threatened: evacuate, staged on beds
    d = decide(asset("hospital", bp=config.BURN_PROB_HIGH, lap10=100), route(cut=400, travel=15, kind="medical"))
    assert d["decision"] == "evacuate" and d["staged"] == STAGED_BEDS
    # clearly threatened but window closed: confine
    d = decide(asset("care_home", bp=0.9, lap10=10), route(cut=60, travel=15, kind="medical"))
    assert d["decision"] == "confine" and d["staged"] is not None
    # not exposed
    d = decide(asset("care_home", bp=0.0, lap10=math.inf), route(cut=math.inf, kind="medical"))
    assert d["decision"] == "monitor"


def test_camp_and_school_are_staged_when_window_open():
    d = decide(asset("camp", bp=1.0, lap10=-20), route(cut=200, travel=10))
    assert d["decision"] == "evacuate" and d["staged"] == STAGED_BUS
    d = decide(asset("school", bp=0.5, lap10=200), route(cut=400, travel=10))
    assert d["decision"] == "evacuate" and d["staged"] == STAGED_BUS
    # window closed: plain confine, no staging
    d = decide(asset("camp", bp=1.0, lap10=-20), route(cut=50, travel=10))
    assert d["decision"] == "confine" and d["staged"] is None


def test_checks_carry_the_numbers():
    d = decide(asset(bp=0.83, lap10=77), route(cut=310, travel=23))
    ev = d["checks"]["exposed"]["evidence"]
    assert "0.83" in ev and "77" in ev and str(config.BURN_PROB_MIN) in ev and str(config.PREPARE_MIN) in ev
    ev = d["checks"]["window_open"]["evidence"]
    assert "310" in ev and "23" in ev and str(config.LOAD_TIME_MIN["nucleus"]) in ev
    assert str(config.ROUTE_BUFFER_MIN) in ev and "GI-660" in ev and "Pavelló de la Bisbal" in ev
