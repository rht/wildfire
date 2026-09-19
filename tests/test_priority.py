"""Priority consumer (CONTRACTS 4, readme 6 and 11): normalisation, stable ties, directional changes,
needs-review queue, sequence guard and overrides. Uses constructed snapshots, no fixture files."""

import math

import pytest

from fireline import config
from fireline.priority import SnapshotSequence, apply_overrides, input_age, score_asset, score_snapshot
from tests.helpers import make_asset, make_snapshot

SCALE_M = config.PRIORITY_POLICY["proximity_scale_m"]
SCALE_PEOPLE = config.PRIORITY_POLICY["size_scale_people"]


def component(asset, name):
    return score_asset(asset)["score_components"][name]["value"]


# -- policy and normalisation -----------------------------------------------------------------

def test_weights_sum_to_one():
    assert math.isclose(sum(config.PRIORITY_POLICY["weights"].values()), 1.0)
    assert set(config.PRIORITY_POLICY["weights"]) == {"proximity", "size", "value"}


def test_proximity_normalisation():
    assert component(make_asset(distance_to_fire_m=0.0), "proximity") == 1.0
    assert component(make_asset(distance_to_fire_m=SCALE_M), "proximity") == 0.0
    assert component(make_asset(distance_to_fire_m=SCALE_M * 3), "proximity") == 0.0
    assert component(make_asset(distance_to_fire_m=SCALE_M / 2), "proximity") == pytest.approx(0.5)
    assert component(make_asset(distance_to_fire_m=0.0, intersects_fire=True), "proximity") == 1.0
    # intersection wins even when a distance is reported
    assert component(make_asset(distance_to_fire_m=250.0, intersects_fire=True), "proximity") == 1.0


def test_size_normalisation_and_proxy():
    scored = score_asset(make_asset(capacity=200, estimated_occupancy=None))
    size = scored["score_components"]["size"]
    assert size["value"] == pytest.approx(200 / SCALE_PEOPLE)
    assert size["proxy"] == "capacity as proxy"
    assert any("from capacity 200 (proxy)" in r for r in scored["priority_reasons"])

    scored = score_asset(make_asset(capacity=200, estimated_occupancy=150))
    assert scored["score_components"]["size"]["value"] == pytest.approx(0.5)
    assert scored["score_components"]["size"]["proxy"] is None
    assert component(make_asset(estimated_occupancy=SCALE_PEOPLE * 2), "size") == 1.0
    assert component(make_asset(estimated_occupancy=0), "size") == 0.0


def test_score_is_weighted_sum_with_explanations():
    asset = make_asset(distance_to_fire_m=0.0, intersects_fire=True, capacity=200, asset_type="school")
    scored = score_asset(asset)
    w = config.PRIORITY_POLICY["weights"]
    expected = round(w["proximity"] * 1.0 + w["size"] * (200 / SCALE_PEOPLE) + w["value"] * 0.8, 4)
    assert scored["priority_score"] == expected
    assert scored["queue"] == "ranked"
    assert scored["priority_rank"] is None
    assert scored["priority_policy_version"] == config.PRIORITY_POLICY["version"]
    reasons = scored["priority_reasons"]
    assert "proximity 1.00 (intersects fire)" in reasons
    assert "size 0.67 from capacity 200 (proxy)" in reasons
    assert f"value 0.80 (school, {config.VALUE_POLICY['version']})" in reasons
    for name in ("proximity", "size", "value"):
        comp = scored["score_components"][name]
        assert set(comp) == {"value", "weight", "input", "proxy"}
        assert comp["weight"] == w[name]
    # input is untouched
    assert "priority_score" not in asset


# -- ordering ---------------------------------------------------------------------------------

def test_stable_ties_by_asset_id():
    b = make_asset(asset_id="fixture:b")
    a = make_asset(asset_id="fixture:a")
    c = make_asset(asset_id="fixture:c")
    result = score_snapshot(make_snapshot([b, c, a]))
    ranked = result["ranked"]
    assert [x["asset_id"] for x in ranked] == ["fixture:a", "fixture:b", "fixture:c"]
    assert [x["priority_rank"] for x in ranked] == [1, 2, 3]
    assert len({x["priority_score"] for x in ranked}) == 1


def test_directional_changes():
    base = make_asset(asset_id="fixture:base", distance_to_fire_m=2000.0, capacity=100, asset_type="masia")
    closer = make_asset(asset_id="fixture:closer", distance_to_fire_m=500.0, capacity=100, asset_type="masia")
    bigger = make_asset(asset_id="fixture:bigger", distance_to_fire_m=2000.0, capacity=250, asset_type="masia")
    valued = make_asset(asset_id="fixture:valued", distance_to_fire_m=2000.0, capacity=100, asset_type="hospital")
    s = {a["asset_id"]: score_asset(a)["priority_score"] for a in (base, closer, bigger, valued)}
    assert s["fixture:closer"] > s["fixture:base"]
    assert s["fixture:bigger"] > s["fixture:base"]
    assert s["fixture:valued"] > s["fixture:base"]


def test_unknown_required_input_goes_to_needs_review():
    no_exposure = score_asset(make_asset(distance_to_fire_m=None, intersects_fire=None,
                                         review_reasons=["exposure_unknown"]))
    assert no_exposure["priority_score"] is None
    assert no_exposure["queue"] == "needs_review"
    assert no_exposure["score_components"]["proximity"]["value"] is None
    assert any("proximity unknown" in r for r in no_exposure["priority_reasons"])

    no_people = score_asset(make_asset(capacity=None, estimated_occupancy=None, review_reasons=["occupancy_unknown"]))
    assert no_people["priority_score"] is None and no_people["queue"] == "needs_review"

    no_value = score_asset(make_asset(asset_type="unknown", value_score=None, value_basis=None,
                                      review_reasons=["class_ambiguous", "value_unknown"]))
    assert no_value["priority_score"] is None and no_value["queue"] == "needs_review"


def test_needs_review_ordering_unknown_exposure_first_then_distance():
    far = make_asset(asset_id="fixture:far", distance_to_fire_m=4000.0, capacity=None, review_reasons=["occupancy_unknown"])
    near = make_asset(asset_id="fixture:near", distance_to_fire_m=300.0, capacity=None, review_reasons=["occupancy_unknown"])
    lost_b = make_asset(asset_id="fixture:lost_b", latitude=None, longitude=None, distance_to_fire_m=None,
                        intersects_fire=None, review_reasons=["location_unknown", "exposure_unknown"])
    lost_a = make_asset(asset_id="fixture:lost_a", latitude=None, longitude=None, distance_to_fire_m=None,
                        intersects_fire=None, review_reasons=["location_unknown", "exposure_unknown"])
    scored = make_asset(asset_id="fixture:ok")
    result = score_snapshot(make_snapshot([far, near, lost_b, scored, lost_a]))
    assert [a["asset_id"] for a in result["needs_review"]] == [
        "fixture:lost_a", "fixture:lost_b", "fixture:near", "fixture:far"]
    assert all(a["priority_rank"] is None for a in result["needs_review"])
    assert [a["asset_id"] for a in result["ranked"]] == ["fixture:ok"]
    assert len(result["all"]) == 5


def test_flagged_assets_are_ranked_and_listed_as_flagged():
    seasonal = make_asset(asset_id="fixture:seasonal", review_reasons=["occupancy_seasonal"])
    plain = make_asset(asset_id="fixture:plain")
    result = score_snapshot(make_snapshot([plain, seasonal]))
    assert {a["asset_id"] for a in result["ranked"]} == {"fixture:plain", "fixture:seasonal"}
    assert [a["asset_id"] for a in result["flagged"]] == ["fixture:seasonal"]
    assert result["needs_review"] == []
    flagged = result["flagged"][0]
    assert flagged["queue"] == "ranked"
    assert flagged["priority_score"] is not None
    assert "review flag: occupancy_seasonal" in flagged["priority_reasons"]


# -- sequence guard ----------------------------------------------------------------------------

def test_snapshot_sequence_guard():
    guard = SnapshotSequence()
    s1 = make_snapshot([], scenario_id="s", sequence=1)
    assert guard.accept(s1) is True
    assert guard.accept(s1) is False                                              # duplicate id
    assert guard.accept(make_snapshot([], scenario_id="s", sequence=1, snapshot_id="s-other")) is False
    assert guard.accept(make_snapshot([], scenario_id="s", sequence=0, snapshot_id="s-zero")) is False
    assert guard.accept(make_snapshot([], scenario_id="s", sequence=2)) is True
    assert guard.accept(make_snapshot([], scenario_id="s", sequence=2, snapshot_id="s-dup2")) is False
    assert guard.last("s") == {"sequence": 2, "snapshot_id": "s-0002"}
    assert guard.last("other") is None
    # scenarios are independent
    assert guard.accept(make_snapshot([], scenario_id="t", sequence=1)) is True


# -- overrides ---------------------------------------------------------------------------------

def override(asset_id, field, value, confirmed_at="2026-07-03T09:00:00+00:00", **kw):
    o = {"override_id": "ovr-0001", "asset_id": asset_id, "field": field, "value": value, "previous": None,
         "source": "phone call with director", "snippet": "about 40 people on site", "url": None,
         "observed_at": "2026-07-03T08:45:00+00:00", "confidence": 0.8, "confirmed_at": confirmed_at}
    o.update(kw)
    return o


def test_apply_overrides_sets_value_basis_and_provenance():
    asset = make_asset(asset_id="fixture:x", estimated_occupancy=None, review_reasons=["occupancy_unknown"])
    out = apply_overrides([asset], [override("fixture:x", "estimated_occupancy", 40)])
    assert asset["estimated_occupancy"] is None                                   # input untouched
    a = out[0]
    assert a["estimated_occupancy"] == 40
    assert a["occupancy_basis"] == "analyst override"
    assert a["sources"][-1]["fields"] == ["estimated_occupancy"]
    assert a["sources"][-1]["source"] == "analyst override: phone call with director"
    assert "occupancy_unknown" not in a["review_reasons"]
    assert "override_conflict" not in a["review_reasons"]
    assert a["needs_review"] is False
    scored = score_asset(a)
    assert scored["score_components"]["size"]["proxy"] is None
    assert scored["score_components"]["size"]["value"] == pytest.approx(40 / SCALE_PEOPLE)


def test_apply_overrides_flags_conflict_with_newer_provider_value():
    asset = make_asset(asset_id="fixture:x", estimated_occupancy=120, sources=[{
        "fields": ["estimated_occupancy"], "source": "register headcount",
        "observed_at": "2026-07-03T10:00:00+00:00", "available_at": None, "fetched_at": None, "notes": None}])
    a = apply_overrides([asset], [override("fixture:x", "estimated_occupancy", 40,
                                           confirmed_at="2026-07-03T09:00:00+00:00")])[0]
    assert a["estimated_occupancy"] == 40                                          # override kept
    assert "override_conflict" in a["review_reasons"]
    assert a["needs_review"] is True
    # no conflict when the override is newer than the provider observation
    b = apply_overrides([asset], [override("fixture:x", "estimated_occupancy", 40,
                                           confirmed_at="2026-07-03T11:00:00+00:00")])[0]
    assert "override_conflict" not in b["review_reasons"]


def test_apply_overrides_asset_type_rederives_value():
    asset = make_asset(asset_id="fixture:x", asset_type="unknown", value_score=None, value_basis=None,
                       review_reasons=["class_ambiguous", "value_unknown"])
    a = apply_overrides([asset], [override("fixture:x", "asset_type", "care_home")])[0]
    assert a["value_score"] == config.VALUE_POLICY["by_type"]["care_home"]
    assert a["review_reasons"] == []
    assert score_asset(a)["queue"] == "ranked"


def test_score_snapshot_applies_overrides():
    asset = make_asset(asset_id="fixture:x", capacity=None, review_reasons=["occupancy_unknown"])
    assert score_snapshot(make_snapshot([asset]))["needs_review"]
    result = score_snapshot(make_snapshot([asset]), overrides=[override("fixture:x", "estimated_occupancy", 40)])
    assert result["needs_review"] == []
    assert result["ranked"][0]["priority_rank"] == 1


def test_input_age():
    asset = make_asset(sources=[
        {"fields": ["capacity"], "source": "register", "observed_at": "2026-07-01T00:00:00+00:00",
         "available_at": None, "fetched_at": "2026-07-03T07:00:00+00:00", "notes": None},
        {"fields": ["distance_to_fire_m"], "source": "deepfire", "observed_at": "2026-07-03T07:30:00+00:00",
         "available_at": None, "fetched_at": "2026-07-03T07:45:00+00:00", "notes": None},
    ])
    age = input_age(asset, now="2026-07-03T08:00:00+00:00")
    assert age["oldest_observed_at"] == "2026-07-01T00:00:00+00:00"
    assert age["newest_fetched_at"] == "2026-07-03T07:45:00+00:00"
    assert age["newest_fetched_age_s"] == 15 * 60
    assert input_age(make_asset(sources=[])) == {"oldest_observed_at": None, "newest_fetched_at": None,
                                                 "oldest_observed_age_s": None, "newest_fetched_age_s": None}
