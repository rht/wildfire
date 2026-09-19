"""Priority consumer (CONTRACTS 4, readme 6 and 11): window arithmetic, ordering (farther outranks nearer,
exhausted first, stable ties), missing inputs, distance never blocks, overrides, now_at default, sequence
guard and agreement with the static contact_priority prototype. Constructed assets only, no fixture files."""

from datetime import datetime, timedelta

import pytest

from fireline import config, snapshot
from fireline.contact_priority import ContactPolicy, rank_contacts
from fireline.priority import (SnapshotSequence, apply_overrides, input_age, minutes_between, rank_asset,
                               rank_snapshot)
from fireline.priority_models import Location
from tests.helpers import AS_OF, make_asset, make_snapshot

BUFFER = config.CONTACT_POLICY["buffer_min"]
EVAC_SOURCE = config.EVACUATION_POLICY["version"]
FORECAST = "fixture:test-spread (synthetic)"
NOW = datetime.fromisoformat(AS_OF)


def at(minutes: float) -> str:
    """ISO timestamp `minutes` after the snapshot as_of."""
    return (NOW + timedelta(minutes=minutes)).isoformat()


def timed(asset_id="fixture:t", arrival_min=180.0, evacuation_min=90.0, **kw) -> dict:
    fields = dict(
        asset_id=asset_id,
        fire_arrival_at=None if arrival_min is None else at(arrival_min),
        fire_arrival_basis=None if arrival_min is None else "p10",
        forecast_source=None if arrival_min is None else FORECAST,
        forecast_horizon_at=None if arrival_min is None else at(720),
        evacuation_min=evacuation_min,
        evacuation_source=None if evacuation_min is None else EVAC_SOURCE,
    )
    fields.update(kw)
    return make_asset(**fields)


def ids(assets):
    return [a["asset_id"] for a in assets]


# -- arithmetic ---------------------------------------------------------------------------------

def test_window_arithmetic_and_keys():
    asset = timed(arrival_min=180.0, evacuation_min=90.0)
    out = rank_asset(asset, AS_OF)
    assert out["time_to_impact_min"] == 180.0
    assert out["latest_start_min"] == 180.0 - 90.0 - BUFFER
    assert out["slack_min"] == 180.0 - 90.0 - BUFFER
    assert out["priority_status"] == "window_open" and out["queue"] == "ranked" and out["priority_rank"] is None
    assert out["priority_policy_version"] == config.CONTACT_POLICY["version"]
    comps = out["window_components"]
    assert set(comps) == {"fire_arrival_at", "fire_arrival_basis", "forecast_source", "forecast_horizon_at", "now_at",
                          "evacuation_min", "evacuation_source", "buffer_min", "distance_to_fire_m"}
    assert comps["now_at"] == AS_OF and comps["buffer_min"] == BUFFER and comps["evacuation_min"] == 90.0
    assert comps["fire_arrival_basis"] == "p10" and comps["forecast_source"] == FORECAST
    # readable explanation lines an analyst can follow, in order
    reasons = out["priority_reasons"]
    assert reasons[0].startswith(f"arrival {at(180)} (p10; forecast {FORECAST}")
    assert "180 min after now" in reasons[0]
    assert reasons[1] == f"evacuation 90 min ({EVAC_SOURCE})"
    assert reasons[2] == f"buffer {BUFFER} min ({config.CONTACT_POLICY['version']})"
    assert reasons[3].startswith("latest start 60 min after now = arrival 180 min - evacuation 90 min - buffer")
    assert reasons[4] == "remaining window 60 min: window_open"
    assert "fire_arrival_at" not in {k for k in asset if k == "slack_min"}      # input untouched
    assert "slack_min" not in asset


def test_elapsed_time_shrinks_window_and_now_at_default_is_as_of():
    asset = timed()
    assert rank_asset(asset, "2026-07-03T08:30:00+00:00")["slack_min"] == 60.0 - 30.0
    assert rank_asset(asset, datetime.fromisoformat("2026-07-03T09:00:00+00:00"))["slack_min"] == 0.0
    result = rank_snapshot(make_snapshot([asset]))
    assert result["now_at"] == AS_OF and result["ranked"][0]["slack_min"] == 60.0
    later = rank_snapshot(make_snapshot([asset]), now_at="2026-07-03T09:30:00+00:00")
    assert later["now_at"] == "2026-07-03T09:30:00+00:00" and later["ranked"][0]["slack_min"] == -30.0
    assert later["policy"]["version"] == config.CONTACT_POLICY["version"]
    assert later["policy"]["buffer_min"] == BUFFER
    with pytest.raises(ValueError):
        rank_snapshot(make_snapshot([asset], as_of=None))
    with pytest.raises(ValueError):
        rank_asset(asset, "not a time")


def test_fractional_minutes_are_rounded_for_noise_only():
    asset = timed(arrival_min=0.3, evacuation_min=0.1)
    out = rank_asset(asset, AS_OF)
    assert out["slack_min"] == round(0.3 - 0.1 - BUFFER, 9)
    assert out["time_to_impact_min"] == pytest.approx(0.3)
    assert minutes_between(at(90), AS_OF) == 90.0 and minutes_between(None, AS_OF) is None


# -- ordering -----------------------------------------------------------------------------------

def test_farther_asset_with_earlier_arrival_outranks_nearer():
    near = timed("fixture:near", arrival_min=300.0, evacuation_min=90.0, distance_to_fire_m=800.0)
    far = timed("fixture:far", arrival_min=150.0, evacuation_min=90.0, distance_to_fire_m=4000.0)
    result = rank_snapshot(make_snapshot([near, far]))
    assert ids(result["ranked"]) == ["fixture:far", "fixture:near"]
    assert [a["priority_rank"] for a in result["ranked"]] == [1, 2]
    assert result["ranked"][0]["slack_min"] < result["ranked"][1]["slack_min"]


def test_longer_evacuation_outranks_earlier_arrival():
    care_home = timed("fixture:care", arrival_min=400.0, evacuation_min=180.0, distance_to_fire_m=1500.0,
                      asset_type="care_home")
    masia = timed("fixture:masia", arrival_min=360.0, evacuation_min=60.0, distance_to_fire_m=600.0,
                  asset_type="masia")
    result = rank_snapshot(make_snapshot([masia, care_home]))
    assert ids(result["ranked"]) == ["fixture:care", "fixture:masia"]


def test_exhausted_windows_rank_first_and_stay_ranked():
    open_ = timed("fixture:open", arrival_min=240.0)
    zero = timed("fixture:zero", arrival_min=90.0 + BUFFER)
    negative = timed("fixture:neg", arrival_min=60.0)
    result = rank_snapshot(make_snapshot([open_, zero, negative]))
    assert ids(result["ranked"]) == ["fixture:neg", "fixture:zero", "fixture:open"]
    by_id = {a["asset_id"]: a for a in result["ranked"]}
    assert by_id["fixture:neg"]["slack_min"] == 60.0 - 90.0 - BUFFER < 0
    assert by_id["fixture:neg"]["priority_status"] == "window_exhausted"
    assert by_id["fixture:zero"]["slack_min"] == 0.0 and by_id["fixture:zero"]["priority_status"] == "window_exhausted"
    assert by_id["fixture:open"]["priority_status"] == "window_open"
    assert result["needs_review"] == []
    assert any("window_exhausted" in r and "not an evacuation instruction" in r
               for r in by_id["fixture:neg"]["priority_reasons"])


def test_ties_by_arrival_then_distance_then_asset_id():
    # same window, different arrival: the earlier arrival first
    a = timed("fixture:a", arrival_min=200.0, evacuation_min=100.0)
    b = timed("fixture:b", arrival_min=150.0, evacuation_min=50.0)
    assert ids(rank_snapshot(make_snapshot([a, b]))["ranked"]) == ["fixture:b", "fixture:a"]
    # identical timing: nearer distance first, then asset_id; null distance last
    c = timed("fixture:c", distance_to_fire_m=None, intersects_fire=None)
    d = timed("fixture:d", distance_to_fire_m=900.0)
    e = timed("fixture:e", distance_to_fire_m=900.0)
    f = timed("fixture:f", distance_to_fire_m=2500.0)
    result = rank_snapshot(make_snapshot([f, c, e, d]))
    assert ids(result["ranked"]) == ["fixture:d", "fixture:e", "fixture:f", "fixture:c"]
    assert len({a["slack_min"] for a in result["ranked"]}) == 1


# -- missing inputs -------------------------------------------------------------------------------

def test_missing_forecast_or_evacuation_goes_to_needs_review_with_reason():
    no_forecast = rank_asset(timed(arrival_min=None), AS_OF)
    assert no_forecast["queue"] == "needs_review" and no_forecast["priority_status"] == "needs_review"
    assert no_forecast["slack_min"] is None and no_forecast["latest_start_min"] is None
    assert no_forecast["time_to_impact_min"] is None and no_forecast["priority_rank"] is None
    assert no_forecast["review_reasons"] == ["forecast_unavailable"] and no_forecast["needs_review"] is True
    assert "needs review: forecast_unavailable; not ranked" in no_forecast["priority_reasons"]
    assert "review flag: forecast_unavailable" in no_forecast["priority_reasons"]

    no_evac = rank_asset(timed(evacuation_min=None), AS_OF)
    assert no_evac["queue"] == "needs_review" and no_evac["review_reasons"] == ["evacuation_unknown"]
    assert any("confirm the total evacuation duration" in r for r in no_evac["priority_reasons"])

    neither = rank_asset(timed(arrival_min=None, evacuation_min=None), AS_OF)
    assert neither["review_reasons"] == ["forecast_unavailable", "evacuation_unknown"]

    # provenance is required: a timestamp without forecast_source, or a duration without its source
    no_src = rank_asset(timed(forecast_source=None), AS_OF)
    assert no_src["queue"] == "needs_review" and no_src["review_reasons"] == ["forecast_unavailable"]
    no_evac_src = rank_asset(timed(evacuation_source="  "), AS_OF)
    assert no_evac_src["queue"] == "needs_review" and no_evac_src["review_reasons"] == ["evacuation_unknown"]

    # the producer's own reason is not duplicated
    flagged = rank_asset(timed(arrival_min=None, review_reasons=["forecast_unavailable"]), AS_OF)
    assert flagged["review_reasons"] == ["forecast_unavailable"]


def test_missing_distance_does_not_block_ranking():
    asset = timed(distance_to_fire_m=None, intersects_fire=None, review_reasons=["exposure_unknown"])
    out = rank_asset(asset, AS_OF)
    assert out["queue"] == "ranked" and out["slack_min"] == 60.0
    assert out["window_components"]["distance_to_fire_m"] is None
    assert "review flag: exposure_unknown" in out["priority_reasons"]
    assert any("distance to fire unknown" in r and "does not block ranking" in r for r in out["priority_reasons"])
    result = rank_snapshot(make_snapshot([asset, timed("fixture:known", distance_to_fire_m=100.0)]))
    assert ids(result["ranked"]) == ["fixture:known", "fixture:t"] and result["flagged"] == [result["ranked"][1]]


def test_needs_review_ordering_unknown_exposure_first_then_distance():
    far = timed("fixture:far", arrival_min=None, distance_to_fire_m=4000.0)
    near = timed("fixture:near", arrival_min=None, distance_to_fire_m=300.0)
    lost_b = timed("fixture:lost_b", arrival_min=None, latitude=None, longitude=None, distance_to_fire_m=None,
                   intersects_fire=None, review_reasons=["location_unknown", "exposure_unknown"])
    lost_a = timed("fixture:lost_a", arrival_min=None, latitude=None, longitude=None, distance_to_fire_m=None,
                   intersects_fire=None, review_reasons=["location_unknown", "exposure_unknown"])
    ok = timed("fixture:ok")
    result = rank_snapshot(make_snapshot([far, near, lost_b, ok, lost_a]))
    assert ids(result["needs_review"]) == ["fixture:lost_a", "fixture:lost_b", "fixture:near", "fixture:far"]
    assert all(a["priority_rank"] is None for a in result["needs_review"])
    assert ids(result["ranked"]) == ["fixture:ok"] and len(result["all"]) == 5
    assert result["all"][0] is result["ranked"][0]


def test_flagged_assets_are_ranked_and_listed_as_flagged():
    seasonal = timed("fixture:seasonal", review_reasons=["occupancy_seasonal"])
    plain = timed("fixture:plain")
    result = rank_snapshot(make_snapshot([plain, seasonal]))
    assert {a["asset_id"] for a in result["ranked"]} == {"fixture:plain", "fixture:seasonal"}
    assert ids(result["flagged"]) == ["fixture:seasonal"] and result["needs_review"] == []
    flagged = result["flagged"][0]
    assert flagged["queue"] == "ranked" and flagged["slack_min"] is not None
    assert "review flag: occupancy_seasonal" in flagged["priority_reasons"]


# -- agreement with the static prototype (readme 16) -------------------------------------------

def to_location(asset: dict) -> Location:
    arrival = asset["fire_arrival_at"]
    return Location(asset_id=asset["asset_id"], name=asset["name"], x_m=0.0, y_m=0.0,
                    distance_m=asset["distance_to_fire_m"], people=None, assisted=None, value=None, deadline_min=None,
                    fire_arrival_min=None if arrival is None else minutes_between(arrival, AS_OF),
                    evacuation_min=asset["evacuation_min"], forecast_source=asset["forecast_source"],
                    evacuation_source=asset["evacuation_source"])


def test_agrees_with_contact_priority_rank_contacts():
    scenario = [
        timed("fixture:near", arrival_min=300.0, evacuation_min=90.0, distance_to_fire_m=800.0),
        timed("fixture:far", arrival_min=150.0, evacuation_min=90.0, distance_to_fire_m=4000.0),
        timed("fixture:care", arrival_min=400.0, evacuation_min=180.0, distance_to_fire_m=1500.0),
        timed("fixture:neg", arrival_min=60.0, evacuation_min=90.0, distance_to_fire_m=200.0),
        timed("fixture:tie_x", arrival_min=200.0, evacuation_min=60.0, distance_to_fire_m=900.0),
        timed("fixture:tie_w", arrival_min=200.0, evacuation_min=60.0, distance_to_fire_m=900.0),
        timed("fixture:tie_none", arrival_min=200.0, evacuation_min=60.0, distance_to_fire_m=None),
        timed("fixture:frac", arrival_min=123.456789, evacuation_min=7.7, distance_to_fire_m=10.0),
        timed("fixture:no_forecast", arrival_min=None, evacuation_min=90.0),
        timed("fixture:no_evac", arrival_min=180.0, evacuation_min=None),
    ]
    static = rank_contacts([to_location(a) for a in scenario], ContactPolicy(now_min=0.0, buffer_min=BUFFER))
    live = rank_snapshot(make_snapshot(scenario))
    assert [(r["asset_id"], r["slack_min"]) for r in static["ranked"]] == \
        [(a["asset_id"], a["slack_min"]) for a in live["ranked"]]
    assert [r["rank"] for r in static["ranked"]] == [a["priority_rank"] for a in live["ranked"]]
    assert [r["status"] for r in static["ranked"]] == [a["priority_status"] for a in live["ranked"]]
    assert [r["time_to_impact_min"] for r in static["ranked"]] == pytest.approx(
        [a["time_to_impact_min"] for a in live["ranked"]])
    assert {r["asset_id"] for r in static["review"]} == {a["asset_id"] for a in live["needs_review"]}
    # a nonzero elapsed time: the static policy moves now_min, the consumer moves now_at
    static30 = rank_contacts([to_location(a) for a in scenario], ContactPolicy(now_min=30.0, buffer_min=BUFFER))
    live30 = rank_snapshot(make_snapshot(scenario), now_at=at(30))
    assert [(r["asset_id"], r["slack_min"]) for r in static30["ranked"]] == \
        [(a["asset_id"], a["slack_min"]) for a in live30["ranked"]]


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
    assert guard.accept(make_snapshot([], scenario_id="t", sequence=1)) is True   # scenarios are independent


# -- overrides ---------------------------------------------------------------------------------

def override(asset_id, field, value, confirmed_at="2026-07-03T09:00:00+00:00", **kw):
    o = {"override_id": "ovr-0001", "asset_id": asset_id, "field": field, "value": value, "previous": None,
         "source": "phone call with director", "snippet": "about 40 people on site", "url": None,
         "observed_at": "2026-07-03T08:45:00+00:00", "confidence": 0.8, "confirmed_at": confirmed_at}
    o.update(kw)
    return o


def test_apply_overrides_sets_value_basis_and_provenance():
    asset = timed("fixture:x", estimated_occupancy=None, review_reasons=["occupancy_unknown"])
    out = apply_overrides([asset], [override("fixture:x", "estimated_occupancy", 40)])
    assert asset["estimated_occupancy"] is None                                   # input untouched
    a = out[0]
    assert a["estimated_occupancy"] == 40 and a["occupancy_basis"] == "analyst override"
    assert a["sources"][-1]["fields"] == ["estimated_occupancy"]
    assert a["sources"][-1]["source"] == "analyst override: phone call with director"
    assert "occupancy_unknown" not in a["review_reasons"] and "override_conflict" not in a["review_reasons"]
    assert a["needs_review"] is False
    assert rank_asset(a, AS_OF)["queue"] == "ranked"


def test_apply_overrides_flags_conflict_with_newer_provider_value():
    asset = timed("fixture:x", estimated_occupancy=120, sources=[{
        "fields": ["estimated_occupancy"], "source": "register headcount",
        "observed_at": "2026-07-03T10:00:00+00:00", "available_at": None, "fetched_at": None, "notes": None}])
    a = apply_overrides([asset], [override("fixture:x", "estimated_occupancy", 40,
                                           confirmed_at="2026-07-03T09:00:00+00:00")])[0]
    assert a["estimated_occupancy"] == 40 and "override_conflict" in a["review_reasons"] and a["needs_review"]
    b = apply_overrides([asset], [override("fixture:x", "estimated_occupancy", 40,
                                           confirmed_at="2026-07-03T11:00:00+00:00")])[0]
    assert "override_conflict" not in b["review_reasons"]


def test_apply_overrides_asset_type_rederives_value():
    asset = timed("fixture:x", asset_type="unknown", value_score=None, value_basis=None,
                  review_reasons=["class_ambiguous", "value_unknown"])
    a = apply_overrides([asset], [override("fixture:x", "asset_type", "care_home")])[0]
    assert a["value_score"] == config.VALUE_POLICY["by_type"]["care_home"] and a["review_reasons"] == []


def test_apply_overrides_evacuation_min_sets_source_and_clears_unknown():
    asset = timed("fixture:x", evacuation_min=None, review_reasons=["evacuation_unknown"])
    assert rank_asset(asset, AS_OF)["queue"] == "needs_review"
    a = apply_overrides([asset], [override("fixture:x", "evacuation_min", 150, source="phone call with director",
                                           snippet="a full evacuation takes about two and a half hours")])[0]
    assert a["evacuation_min"] == 150.0 and a["evacuation_source"] == "analyst override: phone call with director"
    assert a["review_reasons"] == [] and a["needs_review"] is False
    assert a["sources"][-1]["fields"] == ["evacuation_min"]
    ranked = rank_asset(a, AS_OF)
    assert ranked["queue"] == "ranked" and ranked["slack_min"] == 180.0 - 150.0 - BUFFER
    assert ranked["window_components"]["evacuation_source"] == "analyst override: phone call with director"
    assert asset["evacuation_min"] is None                                        # input untouched
    # through rank_snapshot with the store-shaped overrides list
    result = rank_snapshot(make_snapshot([asset]), overrides=[override("fixture:x", "evacuation_min", 150)])
    assert result["needs_review"] == [] and result["ranked"][0]["priority_rank"] == 1


def test_override_on_fire_arrival_at_is_rejected():
    asset = timed("fixture:x")
    with pytest.raises(ValueError, match="fire_arrival_at"):
        apply_overrides([asset], [override("fixture:x", "fire_arrival_at", at(30))])
    with pytest.raises(ValueError, match="override field"):
        apply_overrides([asset], [override("fixture:x", "latitude", 41.0)])
    assert asset["fire_arrival_at"] == at(180)


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


def test_apply_overrides_asset_type_rederives_policy_evacuation():
    version = config.EVACUATION_POLICY["version"]
    asset = timed("fixture:x", asset_type="campsite", evacuation_min=70.0, evacuation_source=version)
    a = apply_overrides([asset], [override("fixture:x", "asset_type", "care_home")])[0]
    assert a["evacuation_min"] == 180.0 and a["evacuation_source"] == version
    policy_entry = next(s for s in a["sources"] if "after asset_type override" in (s["notes"] or ""))
    assert "class care_home" in policy_entry["notes"] and policy_entry["fields"] == ["evacuation_min", "evacuation_source"]
    assert a["sources"][-1]["source"].startswith("analyst override")           # the override entry stays last
    assert rank_asset(a, AS_OF)["slack_min"] == 180.0 - 180.0 - BUFFER
    # an analyst-confirmed duration is kept when the class changes
    confirmed = timed("fixture:y", asset_type="campsite", evacuation_min=45.0, evacuation_source="analyst override: phone")
    b = apply_overrides([confirmed], [override("fixture:y", "asset_type", "care_home")])[0]
    assert b["evacuation_min"] == 45.0 and b["evacuation_source"] == "analyst override: phone"
    # a class without a policy row leaves the duration unknown
    c = apply_overrides([asset], [override("fixture:x", "asset_type", "warehouse")])[0]
    assert c["evacuation_min"] is None and c["evacuation_source"] is None and "evacuation_unknown" in c["review_reasons"]
    assert rank_asset(c, AS_OF)["queue"] == "needs_review"


@pytest.mark.parametrize("score", [None, 0])
def test_asset_type_override_retains_explicit_policy_values(monkeypatch, score):
    monkeypatch.setitem(config.VALUE_POLICY["by_type"], "care_home", score)
    asset = timed("fixture:x", asset_type="unknown", review_reasons=["class_ambiguous", "value_unknown"])
    updated = apply_overrides([asset], [override("fixture:x", "asset_type", "care_home")])[0]
    assert updated["value_score"] == score
    assert updated["value_basis"] == config.VALUE_POLICY["version"]
    assert "value_unknown" not in updated["review_reasons"]
    assert "class_ambiguous" not in updated["review_reasons"]


# --------------------------------------------------------------------- value at risk (handoff 002)
def valued(asset_id="fixture:v", asset_type="hospital", estimated_occupancy=120, burn_probability=0.5,
           evacuation_min=180.0, p10_min=60.0, p50_min=200.0, **kw) -> dict:
    """An asset carrying the optional value-at-risk fields, derived the way the producer derives them."""
    asset = timed(asset_id, asset_type=asset_type, evacuation_min=evacuation_min,
                  estimated_occupancy=estimated_occupancy, burn_probability=burn_probability,
                  arrival_p10_at=at(p10_min), arrival_p50_at=at(p50_min),
                  value_score=config.VALUE_POLICY["by_type"].get(asset_type), **kw)
    snapshot.derive_value_at_risk(asset, AS_OF)
    return asset


def test_valued_asset_starts_from_the_producer_derivation():
    a = valued()
    assert a["replacement_value_eur"] == 25_000_000 and a["people_exposed"] == 60.0
    assert a["expected_loss_eur_mid"] == 3_125_000
    assert a["people_at_risk_p10"] == 120 and a["people_at_risk_p50"] == 120      # both windows exhausted


def test_apply_overrides_asset_type_rederives_value_at_risk():
    a = apply_overrides([valued()], [override("fixture:v", "asset_type", "school")], now_at=AS_OF)[0]
    assert a["replacement_value_eur"] == 4_000_000                                # class value follows the class
    assert (a["expected_loss_eur_low"], a["expected_loss_eur_mid"], a["expected_loss_eur_high"]) == \
           (300_000, 800_000, 1_600_000)                                          # 0.5 x (0.15, 0.40, 0.80) x 4 M
    assert a["evacuation_min"] == 90.0                                            # re-derived from the class ...
    assert a["people_at_risk_p10"] == 120 and a["people_at_risk_p50"] == 0        # ... so the p50 window reopens
    assert a["people_exposed"] == 60.0                                            # headcount and probability unchanged
    assert sum("people_exposed" in s["fields"] for s in a["sources"]) == 1        # provenance replaced, not stacked
    assert a["sources"][-1]["source"].startswith("analyst override")              # the override entry stays last


def test_apply_overrides_occupancy_rederives_the_people_fields():
    a = apply_overrides([valued()], [override("fixture:v", "estimated_occupancy", 40)], now_at=AS_OF)[0]
    assert a["people_exposed"] == 20.0 and a["people_at_risk_p10"] == 40 and a["people_at_risk_p50"] == 40
    assert a["expected_loss_eur_mid"] == 3_125_000                                # euros do not depend on headcount
    # rank_snapshot supplies the epoch itself
    ranked = rank_snapshot(make_snapshot([valued()]), overrides=[override("fixture:v", "estimated_occupancy", 40)])
    assert ranked["ranked"][0]["people_at_risk_p10"] == 40


def test_apply_overrides_without_an_epoch_leaves_people_at_risk_alone():
    a = apply_overrides([valued()], [override("fixture:v", "estimated_occupancy", 40)])[0]
    assert a["people_exposed"] == 20.0                                            # time-independent fields move
    assert a["people_at_risk_p10"] == 120 and a["people_at_risk_p50"] == 120      # these keep the producer's epoch


def test_apply_overrides_leaves_an_asset_without_the_layer_untouched():
    asset = timed("fixture:x", estimated_occupancy=40)
    a = apply_overrides([asset], [override("fixture:x", "asset_type", "care_home")], now_at=AS_OF)[0]
    assert not any(k in a for k in snapshot.VALUE_AT_RISK_KEYS)
    assert not any(set(s.get("fields") or []) & set(snapshot.VALUE_AT_RISK_KEYS) for s in a["sources"])
