"""Per-asset custom valuation: the policy method enum and its band/component guards, the producer's
review reason, the analyst-confirmed override, the bespoke figure in derive_value_at_risk, the
strategic-queue tie-break and the offline cost corpus (config.CUSTOM_VALUATION_POLICY). No network, no
key: the agent side runs on the offline FakeLLM like the rest of tests/test_agent.py.

The layer exists because `config.VALUE_AT_RISK_POLICY` prices an average building of a class and four
classes have no row there at all. Everything below is written from that justification: the figure is a
band, never a point; it is never asserted by the producer; and it never reaches the contact queue.
"""

import copy
import json
from types import SimpleNamespace

import pytest

from fireline import agent, config, priority, snapshot as snap_mod, valuation_reference


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _cfg(**features):
    """A config stand-in with FEATURES overridden, so the flag can be toggled without touching the
    real module. Only the uppercase policy constants are read by the code under test."""
    consts = {k: getattr(config, k) for k in dir(config) if k.isupper()}
    consts["FEATURES"] = dict(config.FEATURES, **features)
    return SimpleNamespace(**consts)


def _row(asset_class="research_facility", **over):
    row = {"asset_id": f"equipaments:{asset_class}", "name": "Institut de prova", "asset_class": asset_class,
           "lat": 41.98, "lon": 2.99, "occupancy": None, "occupancy_source": "unknown",
           "municipality": "Monells", "register": "gencat:equipaments (8gmd-gz7i)", "category": None,
           "seasonal": False, "class_ambiguous": False, "address": None}
    row.update(over)
    return row


POLICY = config.CUSTOM_VALUATION_POLICY
METHODS = tuple(POLICY["methods"])
ASSESSED = tuple(POLICY["assess_classes"])
DAMAGE = POLICY["damage_ratio"]
KEYS = snap_mod.CUSTOM_VALUATION_KEYS
UNASSESSED = snap_mod.VALUATION_UNASSESSED

# The committed worked example: three priced components summing to the mid, and a band around it.
COMPONENTS = [{"label": "compute and storage installation", "amount_eur": 24_000_000},
              {"label": "laboratory fit-out and specialised stores", "amount_eur": 18_000_000},
              {"label": "building shell and services", "amount_eur": 8_000_000}]


def _payload(**over):
    value = {"method": "component_replacement", "components": [dict(c) for c in COMPONENTS],
             "amount_eur_low": 30_000_000, "amount_eur_mid": 50_000_000, "amount_eur_high": 80_000_000,
             "note": None}
    value.update(over)
    return value


def _override(asset, value, **over):
    ovr = {"asset_id": asset["asset_id"], "field": "custom_valuation", "value": value,
           "source": "valuation reference corpus", "snippet": "compute and storage installation 24000000 EUR",
           "confidence": "low", "confirmed_at": "2026-07-03T12:00:00+00:00", "override_id": "ovr-0002"}
    ovr.update(over)
    return ovr


def _ranked(asset_type="research_facility", reasons=(UNASSESSED,), **over):
    rec = snap_mod.asset_record(_row(asset_type), _cfg(custom_valuation=True))
    rec.update(slack_min=45.0, priority_rank=1, queue="ranked", review_reasons=list(reasons))
    rec.update(over)
    return rec


def _exposed(asset, burn_probability=0.5, occupancy=200):
    """The forecast and headcount inputs `derive_value_at_risk` reads, on a ranked asset."""
    asset.update(estimated_occupancy=occupancy, occupancy_basis="staff register",
                 burn_probability=burn_probability, forecast_source="fixture:forecast",
                 evacuation_min=90.0, evacuation_source="fixture",
                 arrival_p10_at="2026-07-03T09:00:00+00:00", arrival_p50_at="2026-07-03T12:00:00+00:00")
    return asset


T0 = "2026-07-03T08:00:00+00:00"


# ---------------------------------------------------------------------------
# policy shape
# ---------------------------------------------------------------------------
def test_policy_methods_are_a_closed_enum_and_every_assessed_class_is_one_the_table_cannot_price():
    assert METHODS and all(isinstance(m, str) and POLICY["methods"][m] for m in METHODS)
    assert "not_valued" in METHODS                       # the ordinary answer must be sayable
    assert set(POLICY["min_components"]) == set(METHODS)  # every method declares its component floor
    assert POLICY["min_components"]["not_valued"] == 0
    assert float(POLICY["min_band_ratio"]) > 1.0
    assert 0 < float(POLICY["component_sum_tolerance"]) < 1
    assert float(POLICY["max_eur"]) > 0
    assert set(DAMAGE) == {"d_low", "d_mid", "d_high"}
    assert DAMAGE["d_low"] <= DAMAGE["d_mid"] <= DAMAGE["d_high"]
    for cls in ASSESSED:
        assert cls in config.VALUE_POLICY["by_type"], cls
        # The load-bearing one: the whole justification for a bespoke figure is that the class table
        # has no row for this class. A class it does price keeps the class answer.
        assert cls not in config.VALUE_AT_RISK_POLICY["by_type"], cls


# ---------------------------------------------------------------------------
# custom_valuation_value: the enum, the band guard and the component-sum rule
# ---------------------------------------------------------------------------
def test_custom_valuation_value_accepts_a_worked_payload_and_normalises_it():
    out = priority.custom_valuation_value(_payload(note="  replayed from the corpus  "))
    assert out["method"] == "component_replacement"
    assert out["amount_eur_low"] == 30_000_000.0 and out["amount_eur_mid"] == 50_000_000.0
    assert out["amount_eur_high"] == 80_000_000.0
    assert out["note"] == "replayed from the corpus"                  # stripped
    assert [c["label"] for c in out["components"]] == [c["label"] for c in COMPONENTS]
    assert all(isinstance(c["amount_eur"], float) for c in out["components"])
    assert set(out) == {"method", "components", "amount_eur_low", "amount_eur_mid", "amount_eur_high", "note"}


def test_custom_valuation_value_rejects_unknown_methods_and_non_objects():
    with pytest.raises(ValueError, match="method must be one of"):
        priority.custom_valuation_value(_payload(method="vibes"))
    with pytest.raises(ValueError, match="method must be one of"):
        priority.custom_valuation_value({"amount_eur_low": 1, "amount_eur_mid": 2, "amount_eur_high": 3})
    for bad in ("component_replacement", 50_000_000, None, [_payload()]):
        with pytest.raises(ValueError, match="needs"):
            priority.custom_valuation_value(bad)
    with pytest.raises(ValueError, match="components must be a list"):
        priority.custom_valuation_value(_payload(components={"label": "x", "amount_eur": 1}))


def test_a_bespoke_figure_is_a_band_never_a_point():
    """The guard that stops a quoted round number becoming a precise valuation."""
    with pytest.raises(ValueError, match="band, not a point"):
        priority.custom_valuation_value({
            "method": "service_continuity",
            "components": [{"label": "annual budget x 3 years", "amount_eur": 45_000_000}],
            "amount_eur_low": 40_000_000, "amount_eur_mid": 45_000_000, "amount_eur_high": 50_000_000})
    # exactly min_band_ratio is enough; anything narrower is not.
    ratio = float(POLICY["min_band_ratio"])
    ok = priority.custom_valuation_value({
        "method": "service_continuity", "components": [{"label": "budget", "amount_eur": 1_000_000}],
        "amount_eur_low": 1_000_000, "amount_eur_mid": 1_000_000, "amount_eur_high": 1_000_000 * ratio})
    assert ok["amount_eur_high"] == 1_000_000 * ratio


def test_custom_valuation_value_rejects_an_out_of_order_band():
    for over in ({"amount_eur_high": 40_000_000}, {"amount_eur_low": 60_000_000},
                 {"amount_eur_mid": 10_000_000}):
        with pytest.raises(ValueError, match="amount_eur_low <= amount_eur_mid <= amount_eur_high"):
            priority.custom_valuation_value(_payload(**over))


def test_not_valued_records_that_the_agent_looked_and_carries_nothing():
    out = priority.custom_valuation_value({"method": "not_valued", "note": "corpus has nothing for this class"})
    assert out["components"] == []
    assert out["amount_eur_low"] is None and out["amount_eur_mid"] is None and out["amount_eur_high"] is None
    assert out["note"] == "corpus has nothing for this class"
    with pytest.raises(ValueError, match="carries no amounts"):
        priority.custom_valuation_value(_payload(method="not_valued"))
    with pytest.raises(ValueError, match="carries no amounts"):
        priority.custom_valuation_value({"method": "not_valued", "components": [dict(COMPONENTS[0])],
                                         "amount_eur_low": None, "amount_eur_mid": None,
                                         "amount_eur_high": None})


def test_a_method_that_adds_components_up_needs_enough_of_them_and_they_must_add_up():
    need = POLICY["min_components"]["component_replacement"]
    assert need >= 2
    with pytest.raises(ValueError, match=f"needs at least {need} priced"):
        priority.custom_valuation_value(_payload(components=[dict(COMPONENTS[0])],
                                                 amount_eur_mid=24_000_000, amount_eur_low=20_000_000,
                                                 amount_eur_high=40_000_000))
    with pytest.raises(ValueError, match="must sum to amount_eur_mid"):
        priority.custom_valuation_value(_payload(amount_eur_low=8_000_000, amount_eur_mid=10_000_000,
                                                 amount_eur_high=20_000_000))
    # a method that scales or substitutes states no such thing, so its components need not sum
    out = priority.custom_valuation_value({
        "method": "parent_institution_scaled", "components": [{"label": "1 of 10 sites", "amount_eur": 5_000_000}],
        "amount_eur_low": 20_000_000, "amount_eur_mid": 40_000_000, "amount_eur_high": 60_000_000})
    assert out["amount_eur_mid"] == 40_000_000.0


def test_custom_valuation_value_rejects_amounts_that_are_not_money():
    with pytest.raises(ValueError, match="above the policy ceiling"):
        priority.custom_valuation_value(_payload(components=[{"label": "a", "amount_eur": 6e9},
                                                             {"label": "b", "amount_eur": 6e9}],
                                                 amount_eur_low=6e9, amount_eur_mid=12e9, amount_eur_high=20e9))
    for over in ({"amount_eur_low": -1}, {"amount_eur_mid": float("nan")}, {"amount_eur_high": float("inf")}):
        with pytest.raises(ValueError, match="must be finite and >= 0"):
            priority.custom_valuation_value(_payload(**over))
    for over in ({"amount_eur_mid": True}, {"amount_eur_low": None}, {"amount_eur_high": "eighty million"}):
        with pytest.raises(ValueError, match="must be a number"):
            priority.custom_valuation_value(_payload(**over))
    with pytest.raises(ValueError, match="amount_eur must be a number"):
        priority.custom_valuation_value(_payload(components=[{"label": "a", "amount_eur": None},
                                                             {"label": "b", "amount_eur": 1}]))


def test_a_priced_component_needs_a_label_an_analyst_can_read():
    for bad in ([{"label": "  ", "amount_eur": 1}, {"label": "b", "amount_eur": 1}],
                [{"amount_eur": 1}, {"label": "b", "amount_eur": 1}]):
        with pytest.raises(ValueError, match="needs a non-empty 'label'"):
            priority.custom_valuation_value(_payload(components=bad))
    with pytest.raises(ValueError, match="must be an object"):
        priority.custom_valuation_value(_payload(components=["24000000 EUR of compute"]))


# ---------------------------------------------------------------------------
# producer: the flag gates everything
# ---------------------------------------------------------------------------
def test_producer_never_asserts_a_figure_and_is_inert_while_the_flag_is_off():
    off = snap_mod.asset_record(_row(), _cfg(custom_valuation=False))
    assert all(off[k] is None for k in KEYS)
    assert UNASSESSED not in off["review_reasons"]

    on = snap_mod.asset_record(_row(), _cfg(custom_valuation=True))
    assert all(on[k] is None for k in KEYS)                  # still never asserted by the producer
    assert UNASSESSED in on["review_reasons"] and on["needs_review"]


def test_only_the_classes_the_table_cannot_price_enter_the_valuation_queue():
    cfg = _cfg(custom_valuation=True)
    for cls in ASSESSED:
        assert UNASSESSED in snap_mod.asset_record(_row(cls), cfg)["review_reasons"], cls
    ordinary = snap_mod.asset_record(_row("school", occupancy=120, occupancy_source="enrolment"), cfg)
    assert UNASSESSED not in ordinary["review_reasons"]
    assert snap_mod._valuation_wanted("school", cfg) is False
    assert snap_mod._valuation_wanted("research_facility", cfg) is True
    assert snap_mod._valuation_wanted("research_facility", _cfg(custom_valuation=False)) is False


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def test_validate_rejects_inconsistent_custom_valuation_and_accepts_absent_keys():
    base = dict(snap_mod.asset_record(_row(), config),
                custom_value_method="component_replacement",
                custom_value_components=[dict(c) for c in COMPONENTS],
                custom_value_eur_low=30_000_000, custom_value_eur_mid=50_000_000,
                custom_value_eur_high=80_000_000, custom_value_basis="test basis")

    def errs(**over):
        return snap_mod._custom_valuation_errors("t", dict(base, **over))

    assert errs() == []
    assert any("not in" in e for e in errs(custom_value_method="guesswork"))
    assert any("non-empty custom_value_basis" in e for e in errs(custom_value_basis=None))
    assert any("is required for method" in e for e in errs(custom_value_eur_mid=None))
    assert any("custom_value_eur_low <= _mid <= _high" in e for e in errs(custom_value_eur_low=60_000_000))
    assert any("nonnegative finite" in e for e in errs(custom_value_eur_high=float("inf")))
    assert any("needs a non-empty label" in e
               for e in errs(custom_value_components=[{"label": " ", "amount_eur": 1}]))
    assert any("amount_eur must be a nonnegative" in e
               for e in errs(custom_value_components=[{"label": "a", "amount_eur": -1}]))
    # `not_valued` carries nothing, in the snapshot as in the payload
    assert any("must be null for method 'not_valued'" in e for e in errs(custom_value_method="not_valued"))
    # no method at all means no figures either
    assert errs(custom_value_method=None, custom_value_components=None, custom_value_eur_low=None,
                custom_value_eur_mid=None, custom_value_eur_high=None, custom_value_basis=None) == []
    assert any("must be null without custom_value_method" in e for e in errs(custom_value_method=None))
    # half the keys is neither a pre-layer record nor a valuation
    half = {k: v for k, v in base.items() if k != "custom_value_basis"}
    assert any("all present or all absent" in e for e in snap_mod._custom_valuation_errors("t", half))
    # A snapshot written before the layer existed has none of the six keys and stays valid.
    older = {k: v for k, v in base.items() if k not in KEYS}
    assert snap_mod._custom_valuation_errors("t", older) == []


def test_validate_snapshot_accepts_a_snapshot_written_before_the_layer_existed():
    """The six keys are optional exactly as CRITICALITY_KEYS are: a committed snapshot written before
    the layer stays valid without them, and `_custom_valuation_errors` says so.

    KNOWN SPINE BUG (fireline/snapshot.py): the keys were added to ASSET_KEYS, but the required-key
    check at the top of `validate_snapshot` exempts only CRITICALITY_KEYS -
    `[k for k in ASSET_KEYS if k not in a and k not in CRITICALITY_KEYS and ...]` - so every snapshot
    without them now fails with "missing keys". The same cause fails four tests in
    tests/test_snapshot.py against the committed gavarres_real_000*.json fixtures. Fix: exempt
    CUSTOM_VALUATION_KEYS there too (or regenerate the real snapshots).
    """
    snap = json.loads(open("fixtures/snapshots/synthetic_gavarres_0001.json").read())
    older = copy.deepcopy(snap)
    for a in older["assets"]:
        for k in KEYS:
            a.pop(k, None)
    assert snap_mod.validate_snapshot(snap) == []
    assert snap_mod.validate_snapshot(older) == []


def test_a_confirmed_method_and_the_unassessed_reason_cannot_coexist_in_a_snapshot():
    snap = json.loads(open("fixtures/snapshots/synthetic_gavarres_0001.json").read())
    assert snap_mod.validate_snapshot(snap) == []
    a = snap["assets"][0]
    a["custom_value_method"] = "component_replacement"
    a["custom_value_components"] = [dict(c) for c in COMPONENTS]
    a["custom_value_eur_low"] = 30_000_000
    a["custom_value_eur_mid"] = 50_000_000
    a["custom_value_eur_high"] = 80_000_000
    a["custom_value_basis"] = POLICY["version"]
    a["review_reasons"] = sorted(set(a["review_reasons"]) | {UNASSESSED})
    a["needs_review"] = True
    assert any("valuation_unassessed must be absent" in e for e in snap_mod.validate_snapshot(snap))


# ---------------------------------------------------------------------------
# analyst-confirmed override
# ---------------------------------------------------------------------------
def test_confirming_a_valuation_fans_the_payload_out_and_clears_the_reason():
    asset = _ranked()
    out = priority.apply_overrides([asset], [_override(asset, _payload(note="worked example"))], config)[0]
    assert out["custom_value_method"] == "component_replacement"
    assert (out["custom_value_eur_low"], out["custom_value_eur_mid"], out["custom_value_eur_high"]) == \
           (30_000_000.0, 50_000_000.0, 80_000_000.0)
    assert [c["label"] for c in out["custom_value_components"]] == [c["label"] for c in COMPONENTS]
    assert POLICY["version"] in out["custom_value_basis"]
    assert "analyst override" in out["custom_value_basis"]
    assert "component_replacement" in out["custom_value_basis"] and "worked example" in out["custom_value_basis"]
    assert UNASSESSED not in out["review_reasons"]
    # `custom_valuation` is the name of the override, not of a snapshot field
    assert "custom_valuation" not in out
    assert all(k in out for k in KEYS)
    entry = next(s for s in out["sources"] if "custom_valuation" in (s.get("fields") or []))
    assert entry["source"].startswith("analyst override") and "ovr-0002" in entry["notes"]


def test_an_override_that_breaks_a_guard_is_refused_and_nothing_lands_on_the_asset():
    asset = _ranked()
    with pytest.raises(ValueError, match="band, not a point"):
        priority.apply_overrides([asset], [_override(asset, _payload(
            components=[{"label": "a", "amount_eur": 25_000_000}, {"label": "b", "amount_eur": 25_000_000}],
            amount_eur_low=45_000_000, amount_eur_mid=50_000_000, amount_eur_high=55_000_000))], config)
    assert all(asset[k] is None for k in KEYS)
    assert asset["review_reasons"] == [UNASSESSED]
    assert "custom_valuation" not in asset


# ---------------------------------------------------------------------------
# the bespoke figure in derive_value_at_risk
# ---------------------------------------------------------------------------
def _valued(value=None, **over):
    asset = _exposed(_ranked(**over))
    return priority.apply_overrides([asset], [_override(asset, value or _payload())], config)[0]


def test_a_confirmed_valuation_replaces_the_class_replacement_cost():
    out = _valued()
    snap_mod.derive_value_at_risk(out, T0, config)
    assert out["replacement_value_eur"] == 50_000_000                       # the bespoke mid, not a class row
    assert "component_replacement" in out["replacement_value_basis"]
    assert "per-asset custom valuation" in out["replacement_value_basis"]
    assert POLICY["version"] in out["replacement_value_basis"]
    assert config.VALUE_AT_RISK_POLICY["by_type"].get("research_facility") is None
    src = [s for s in out["sources"] if "replacement_value_eur" in (s.get("fields") or [])]
    assert len(src) == 1 and src[0]["source"] == "config.CUSTOM_VALUATION_POLICY"
    assert "not for class research_facility" in src[0]["notes"]


def test_expected_loss_compounds_the_valuation_band_with_the_damage_band():
    out = _valued()
    snap_mod.derive_value_at_risk(out, T0, config)
    burn = out["burn_probability"]
    for level in ("low", "mid", "high"):
        assert out[f"expected_loss_eur_{level}"] == \
            round(burn * DAMAGE[f"d_{level}"] * out[f"custom_value_eur_{level}"]), level
    assert out["expected_loss_eur_low"] < out["expected_loss_eur_mid"] < out["expected_loss_eur_high"]
    # the band is wider than the damage band alone would make it
    damage_only = round(burn * DAMAGE["d_high"] * out["custom_value_eur_mid"])
    assert out["expected_loss_eur_high"] > damage_only
    assert out["people_exposed"] == round(out["estimated_occupancy"] * burn, 1)


def test_not_valued_leaves_the_asset_unvalued():
    out = _valued({"method": "not_valued", "note": "the corpus has nothing for this class"})
    assert out["custom_value_method"] == "not_valued"
    assert snap_mod.custom_valuation_band(out, config) is None
    snap_mod.derive_value_at_risk(out, T0, config)
    assert out["replacement_value_eur"] is None and out["replacement_value_basis"] is None
    assert out["expected_loss_eur_mid"] is None
    assert out["people_exposed"] is not None          # the headcount half is unaffected
    assert UNASSESSED not in out["review_reasons"]    # the agent looked, which is the point of the record


def test_a_confirmed_valuation_survives_a_later_unrelated_override():
    """`_rederive_value_at_risk` runs after every override; the bespoke figure must not fall back to
    the class table (which prices this class not at all) on the way through."""
    out = _valued()
    snap_mod.derive_value_at_risk(out, T0, config)
    before = out["replacement_value_eur"], out["expected_loss_eur_mid"]

    after = priority.apply_overrides([out], [{
        "asset_id": out["asset_id"], "field": "estimated_occupancy", "value": 300,
        "source": "facility", "snippet": "300 staff on site", "confidence": "high",
        "confirmed_at": "2026-07-03T12:30:00+00:00"}], config, now_at=T0)[0]
    assert (after["replacement_value_eur"], after["expected_loss_eur_mid"]) == before
    assert after["custom_value_eur_mid"] == 50_000_000.0
    assert "per-asset custom valuation" in after["replacement_value_basis"]
    assert after["people_exposed"] == round(300 * after["burn_probability"], 1)   # the override did land
    assert sum("replacement_value_eur" in (s.get("fields") or []) for s in after["sources"]) == 1


# ---------------------------------------------------------------------------
# the contact queue is untouched
# ---------------------------------------------------------------------------
def test_a_custom_valuation_never_reorders_the_contact_queue():
    """readme 6: property value does not override contact urgency."""
    snap = json.loads(open("fixtures/snapshots/gavarres_real_0002.json").read())
    before = [a["asset_id"] for a in priority.rank_snapshot(snap, config)["ranked"]]
    assert before

    valued = copy.deepcopy(snap)
    for a in valued["assets"]:                       # make the LAST-ranked assets the most valuable
        if a["asset_type"] in ("school", "campsite"):
            a["custom_value_method"] = "component_replacement"
            a["custom_value_components"] = [{"label": "a", "amount_eur": 2_000_000_000},
                                            {"label": "b", "amount_eur": 2_000_000_000}]
            a["custom_value_eur_low"] = 2_000_000_000
            a["custom_value_eur_mid"] = 4_000_000_000
            a["custom_value_eur_high"] = 4_500_000_000
            a["custom_value_basis"] = "test"
    ranked = priority.rank_snapshot(valued, config)
    assert [a["asset_id"] for a in ranked["ranked"]] == before
    assert [a["asset_id"] for a in ranked["needs_review"]] == \
           [a["asset_id"] for a in priority.rank_snapshot(snap, config)["needs_review"]]
    # a euro figure alone does not put an asset in the strategic view either: that is the tier's job
    assert ranked["strategic"] == []


# ---------------------------------------------------------------------------
# the strategic queue: value is the cardinal refinement of the tier
# ---------------------------------------------------------------------------
def _tiered(asset_id, tier="high", slack=100.0, value=None):
    a = _ranked()
    a.update(asset_id=asset_id, criticality_tier=tier, slack_min=slack,
             criticality_factors=["hazardous_materials"],
             criticality_basis="criticality-proto-2026-09-19; analyst override: test")
    if value is not None:
        a.update(custom_value_method="component_replacement", custom_value_eur_low=value / 2,
                 custom_value_eur_mid=value, custom_value_eur_high=value * 2,
                 custom_value_components=[dict(c) for c in COMPONENTS], custom_value_basis="test")
    return a


def test_strategic_queue_puts_the_larger_bespoke_figure_first_within_a_tier():
    assets = [_tiered("a:small", value=1_000_000), _tiered("b:large", value=90_000_000),
              _tiered("c:medium", value=5_000_000)]
    assert [a["asset_id"] for a in priority.strategic_queue(assets, config)] == \
           ["b:large", "c:medium", "a:small"]
    # and never above the tier: an elevated fortune still sorts under a high asset with no figure
    mixed = [_tiered("a:elevated", tier="elevated", value=900_000_000), _tiered("b:high", tier="high")]
    assert [a["asset_id"] for a in priority.strategic_queue(mixed, config)] == ["b:high", "a:elevated"]


def test_strategic_queue_puts_an_unvalued_asset_after_a_valued_one():
    assets = [_tiered("a:unvalued", slack=1.0), _tiered("b:valued", slack=900.0, value=1_000_000)]
    assert [a["asset_id"] for a in priority.strategic_queue(assets, config)] == ["b:valued", "a:unvalued"]
    # `not_valued` is not a figure, so it sorts with the unvalued
    nv = _tiered("c:not_valued", slack=2.0)
    nv.update(custom_value_method="not_valued", custom_value_basis="test", custom_value_components=[])
    out = priority.strategic_queue([nv, assets[1]], config)
    assert [a["asset_id"] for a in out] == ["b:valued", "c:not_valued"]


def test_strategic_queue_still_orders_by_tier_then_window_when_nothing_is_valued():
    assets = [_tiered("a:elevated", tier="elevated", slack=900.0), _tiered("b:high", tier="high", slack=120.0),
              _tiered("c:high", tier="high", slack=10.0), _tiered("d:high", tier="high", slack=None)]
    assert [a["asset_id"] for a in priority.strategic_queue(assets, config)] == \
           ["c:high", "b:high", "d:high", "a:elevated"]


# ---------------------------------------------------------------------------
# the agent side
# ---------------------------------------------------------------------------
def test_the_valuation_tool_and_field_are_declared_to_the_model():
    names = [t["name"] for t in agent.TOOLS]
    assert "lookup_valuation_reference" in names
    propose = next(t for t in agent.TOOLS if t["name"] == "propose_update")
    assert "custom_valuation" in propose["input_schema"]["properties"]["field"]["enum"]
    assert "object" in propose["input_schema"]["properties"]["value"]["type"]
    assert "custom_valuation" in agent.PROPOSAL_FIELDS and "custom_valuation" in priority.OVERRIDE_FIELDS
    assert UNASSESSED in agent.REVIEW_REASONS
    assert UNASSESSED in agent.SYSTEM_PROMPT
    assert "not_valued" in agent.SYSTEM_PROMPT


def test_lookup_valuation_reference_is_stateless_and_dispatches_without_a_workbench():
    assert "lookup_valuation_reference" in agent.STATELESS_TOOLS
    out = agent.dispatch("lookup_valuation_reference",
                         {"asset_type": "research_facility", "query": "compute racks laboratory"},
                         workbench=None)
    assert isinstance(out, list), out
    assert out and isinstance(out[0], dict) and out[0]["statement"]
    assert agent.dispatch("lookup_valuation_reference", {"asset_type": "campsite"}, workbench=None) == []


def test_the_agent_can_only_propose_policy_methods_and_supported_figures():
    good = agent._check_value("custom_valuation", _payload())
    assert good["method"] == "component_replacement" and good["amount_eur_mid"] == 50_000_000.0
    assert agent._check_value("custom_valuation", {"method": "not_valued"})["amount_eur_mid"] is None
    for bad in (_payload(method="market_value"),
                _payload(amount_eur_high=40_000_000),
                _payload(amount_eur_low=8_000_000, amount_eur_mid=10_000_000, amount_eur_high=20_000_000),
                _payload(components=[dict(COMPONENTS[0])]),
                "50000000"):
        with pytest.raises(ValueError):
            agent._check_value("custom_valuation", bad)


def test_fake_llm_investigates_the_valuation_and_stays_grounded():
    """The offline path must produce a figure the analyst can act on, quoting its own tool result."""
    asset = _ranked()
    wb = agent.Workbench.from_scored([asset])
    wb.assets[asset["asset_id"]]["name"] = "Institut Català de Recerca de l'Aigua"
    rec = agent.investigate(wb, asset["asset_id"])
    assert "lookup_valuation_reference" in [c["name"] for c in rec["tool_calls"]]
    props = [p for p in wb.proposals if p["field"] == "custom_valuation"]
    assert len(props) == 1
    value = priority.custom_valuation_value(props[0]["value"])
    assert value["method"] in METHODS
    snippet = props[0]["quoted_snippet"]
    assert snippet
    results = [json.dumps(c["result"], ensure_ascii=False) for c in rec["tool_calls"]]
    assert any(snippet in r for r in results), snippet      # quoted verbatim from its own tool results
    assert rec["postcheck_ok"] is True
    assert wb.asset(asset["asset_id"])["custom_value_method"] is None     # nothing applied


def test_fake_llm_proposes_no_valuation_for_an_asset_that_was_not_flagged():
    asset = _ranked(reasons=("occupancy_unknown",))
    wb = agent.Workbench.from_scored([asset])
    rec = agent.investigate(wb, asset["asset_id"])
    assert "lookup_valuation_reference" not in [c["name"] for c in rec["tool_calls"]]
    assert [p for p in wb.proposals if p["field"] == "custom_valuation"] == []


# ---------------------------------------------------------------------------
# the offline cost corpus
# ---------------------------------------------------------------------------
REFERENCE_KEYS = {"reference_id", "applies_to", "title", "statement", "unit", "amount_eur", "components",
                  "amount_eur_low", "amount_eur_high", "basis", "source", "url", "observed_at"}


def test_every_reference_carries_the_full_key_set_and_marks_its_assumptions():
    refs = valuation_reference.references()
    assert refs
    ids = [r["reference_id"] for r in refs]
    assert len(set(ids)) == len(ids)
    for r in refs:
        # CONTRACTS convention: every key always present, unknown is null, never absent.
        assert set(r) == REFERENCE_KEYS, r["reference_id"]
        assert r["basis"] in ("published", "assumed"), r["reference_id"]
        assert r["statement"] and r["title"] and r["applies_to"]
        assert all(c in config.VALUE_POLICY["by_type"] for c in r["applies_to"]), r["reference_id"]
        if r["basis"] == "assumed":
            # the caveat travels inside the text, so an agent quoting it verbatim carries it along
            assert "[assumed]" in r["statement"], r["reference_id"]


def test_the_worked_example_satisfies_the_guards_it_will_be_replayed_through():
    worked = [r for r in valuation_reference.references()
              if r["components"] and r["amount_eur_low"] is not None and r["amount_eur_high"] is not None]
    assert worked
    for r in worked:
        total = sum(c["amount_eur"] for c in r["components"])
        assert abs(total - r["amount_eur"]) <= float(POLICY["component_sum_tolerance"]) * r["amount_eur"]
        assert r["amount_eur_low"] <= r["amount_eur"] <= r["amount_eur_high"]
        assert r["amount_eur_high"] >= r["amount_eur_low"] * float(POLICY["min_band_ratio"])
        assert len(r["components"]) >= POLICY["min_components"]["component_replacement"]
        # and it is therefore a payload the shared guard accepts
        priority.custom_valuation_value({
            "method": "component_replacement", "components": [dict(c) for c in r["components"]],
            "amount_eur_low": r["amount_eur_low"], "amount_eur_mid": r["amount_eur"],
            "amount_eur_high": r["amount_eur_high"]})


def test_lookup_filters_by_class_and_says_nothing_rather_than_something_wrong():
    for cls in ASSESSED:
        for r in valuation_reference.lookup(cls, "", limit=10):
            assert cls in r["applies_to"], (cls, r["reference_id"])
    # a class the corpus has nothing for gets an empty answer, which tells the agent to say not_valued
    assert valuation_reference.lookup("campsite") == []
    assert valuation_reference.lookup("masia", "rural housing") == []
    # the query is a nudge over title and statement, not a filter
    hits = valuation_reference.lookup("research_facility", "compute racks machine room", limit=3)
    assert hits and hits[0]["reference_id"] == "hpc-installation-per-rack"
    assert len(valuation_reference.lookup("research_facility", "", limit=2)) == 2
