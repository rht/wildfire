"""Per-asset criticality: the policy enum and its inflation guard, the producer's review reason, the
analyst-confirmed override and the separate strategic queue (config.CRITICALITY_POLICY). No network,
no key: the agent side runs on the offline FakeLLM like the rest of tests/test_agent.py.
"""

import copy
import json
from types import SimpleNamespace

import pytest

from fireline import agent, config, priority, snapshot as snap_mod


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


TIERS = tuple(config.CRITICALITY_POLICY["tiers"])
FACTORS = tuple(config.CRITICALITY_POLICY["factors"])


# ---------------------------------------------------------------------------
# policy shape
# ---------------------------------------------------------------------------
def test_policy_tiers_are_ordered_and_every_assessed_class_is_a_real_class():
    ranks = [config.CRITICALITY_POLICY["tiers"][t]["rank"] for t in TIERS]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    multipliers = [config.CRITICALITY_POLICY["tiers"][t]["loss_multiplier"] for t in TIERS]
    assert multipliers == sorted(multipliers)
    assert config.CRITICALITY_POLICY["default_tier"] in TIERS
    for cls in config.CRITICALITY_POLICY["assess_classes"]:
        assert cls in config.VALUE_POLICY["by_type"], cls
    # Every class the ingestion can produce must be priced by all four class tables, or
    # exposure.build_asset_table raises on it.
    for table in (config.EVACUATION_POLICY["by_type"], config.LEAD_TIME_MIN, config.LOAD_TIME_MIN):
        assert set(table) == set(config.VALUE_POLICY["by_type"])


# ---------------------------------------------------------------------------
# criticality_value: the enum and the inflation guard
# ---------------------------------------------------------------------------
def test_criticality_value_accepts_a_supported_tier_and_dedupes_factors():
    tier, factors = priority.criticality_value(
        {"tier": "high", "factors": ["irreplaceable_holdings", "irreplaceable_holdings"]})
    assert tier == "high" and factors == ["irreplaceable_holdings"]


def test_criticality_value_rejects_unknown_tiers_factors_and_shapes():
    for bad in ("high", 3, None, ["high"]):
        with pytest.raises(ValueError, match="needs"):
            priority.criticality_value(bad)
    with pytest.raises(ValueError, match="tier must be one of"):
        priority.criticality_value({"tier": "catastrophic", "factors": list(FACTORS)})
    with pytest.raises(ValueError, match="unknown criticality factors"):
        priority.criticality_value({"tier": "elevated", "factors": ["it_is_expensive"]})
    with pytest.raises(ValueError, match="list of strings"):
        priority.criticality_value({"tier": "elevated", "factors": "irreplaceable_holdings"})


def test_exceptional_cannot_rest_on_prose_alone():
    """The guard that stops the model talking its way to the top tier."""
    with pytest.raises(ValueError, match="needs at least 2"):
        priority.criticality_value({"tier": "exceptional", "factors": ["irreplaceable_holdings"]})
    with pytest.raises(ValueError, match="needs at least 1"):
        priority.criticality_value({"tier": "high", "factors": []})
    tier, factors = priority.criticality_value(
        {"tier": "exceptional", "factors": ["irreplaceable_holdings", "national_research_infrastructure"]})
    assert tier == "exceptional" and len(factors) == 2
    # routine needs nothing, which is what "not special" has to look like.
    assert priority.criticality_value({"tier": "routine", "factors": []}) == ("routine", [])


# ---------------------------------------------------------------------------
# producer: the flag gates everything
# ---------------------------------------------------------------------------
def test_producer_never_asserts_a_tier_and_is_inert_while_the_flag_is_off():
    # Both states are named explicitly: the flag is deployment policy, and this asserts the gate.
    off = snap_mod.asset_record(_row(), _cfg(asset_criticality=False))
    assert off["criticality_tier"] is None
    assert off["criticality_factors"] is None and off["criticality_basis"] is None
    assert "criticality_unassessed" not in off["review_reasons"]

    on = snap_mod.asset_record(_row(), _cfg(asset_criticality=True))
    assert on["criticality_tier"] is None                      # still never asserted by the producer
    assert "criticality_unassessed" in on["review_reasons"] and on["needs_review"]


def test_only_the_assessed_classes_enter_the_queue():
    cfg = _cfg(asset_criticality=True)
    assessed = snap_mod.asset_record(_row("fire_station"), cfg)
    ordinary = snap_mod.asset_record(_row("school", occupancy=120, occupancy_source="enrolment"), cfg)
    assert "criticality_unassessed" in assessed["review_reasons"]
    assert "criticality_unassessed" not in ordinary["review_reasons"]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def test_validate_rejects_inconsistent_criticality_and_accepts_absent_keys():
    base = snap_mod.asset_record(_row(), config)

    def errs(**over):
        return snap_mod._criticality_errors("t", dict(base, **over))

    assert errs() == []
    assert errs(criticality_factors=[]) == ["t: criticality_factors must be null without criticality_tier"]
    assert any("not in" in e for e in errs(criticality_tier="enormous", criticality_basis="v1"))
    assert any("at least 2" in e for e in
               errs(criticality_tier="exceptional", criticality_factors=["irreplaceable_holdings"],
                    criticality_basis="v1"))
    assert any("non-empty criticality_basis" in e for e in
               errs(criticality_tier="routine", criticality_factors=[], criticality_basis=None))
    # A snapshot written before the layer existed has none of the three keys and stays valid.
    older = {k: v for k, v in base.items() if k not in snap_mod.CRITICALITY_KEYS}
    assert snap_mod._criticality_errors("t", older) == []


def test_a_tier_and_the_unassessed_reason_cannot_coexist_in_a_snapshot():
    snap = json.loads(open("fixtures/snapshots/synthetic_gavarres_0001.json").read())
    assert snap_mod.validate_snapshot(snap) == []
    a = snap["assets"][0]
    a["criticality_tier"] = "elevated"
    a["criticality_factors"] = ["sole_regional_service"]
    a["criticality_basis"] = config.CRITICALITY_POLICY["version"]
    a["review_reasons"] = sorted(set(a["review_reasons"]) | {"criticality_unassessed"})
    a["needs_review"] = True
    assert any("criticality_unassessed must be absent" in e for e in snap_mod.validate_snapshot(snap))


# ---------------------------------------------------------------------------
# analyst-confirmed override
# ---------------------------------------------------------------------------
def _ranked(asset_type="research_facility", reasons=("criticality_unassessed",)):
    rec = snap_mod.asset_record(_row(asset_type), _cfg(asset_criticality=True))
    rec.update(slack_min=45.0, priority_rank=1, queue="ranked", review_reasons=list(reasons))
    return rec


def test_confirming_a_tier_sets_all_three_fields_and_clears_the_reason():
    asset = _ranked()
    out = priority.apply_overrides([asset], [{
        "asset_id": asset["asset_id"], "field": "criticality_tier",
        "value": {"tier": "high", "factors": ["national_research_infrastructure"]},
        "source": "ca.wikipedia.org", "snippet": "és un centre de recerca", "confidence": "medium",
        "confirmed_at": "2026-07-03T12:00:00+00:00", "override_id": "ovr-0001",
    }], config)[0]
    assert out["criticality_tier"] == "high"
    assert out["criticality_factors"] == ["national_research_infrastructure"]
    assert config.CRITICALITY_POLICY["version"] in out["criticality_basis"]
    assert "analyst override" in out["criticality_basis"]
    assert "criticality_unassessed" not in out["review_reasons"]
    entry = next(s for s in out["sources"] if "criticality_tier" in (s.get("fields") or []))
    assert entry["source"].startswith("analyst override") and "ovr-0001" in entry["notes"]


def test_an_override_that_breaks_the_guard_is_refused_and_nothing_lands_on_the_asset():
    asset = _ranked()
    with pytest.raises(ValueError, match="needs at least 2"):
        priority.apply_overrides([asset], [{
            "asset_id": asset["asset_id"], "field": "criticality_tier",
            "value": {"tier": "exceptional", "factors": ["hazardous_materials"]},
            "source": "s", "snippet": "x", "confidence": "low",
            "confirmed_at": "2026-07-03T12:00:00+00:00",
        }], config)
    assert asset["criticality_tier"] is None


def test_a_reclassification_keeps_an_existing_tier_but_re_opens_an_unassessed_one():
    cfg = _cfg(asset_criticality=True)
    reclassify = {"field": "asset_type", "value": "research_facility", "source": "s", "snippet": "x",
                  "confidence": "high", "confirmed_at": "2026-07-03T12:00:00+00:00"}

    unassessed = _ranked("school", reasons=("class_ambiguous",))
    out = priority.apply_overrides([unassessed], [dict(reclassify, asset_id=unassessed["asset_id"])], cfg)[0]
    assert "criticality_unassessed" in out["review_reasons"]      # new class is one we assess

    assessed = _ranked("school", reasons=("class_ambiguous",))
    assessed.update(criticality_tier="elevated", criticality_factors=["irreplaceable_holdings"],
                    criticality_basis="criticality-proto; analyst override: x")
    out = priority.apply_overrides([assessed], [dict(reclassify, asset_id=assessed["asset_id"])], cfg)[0]
    assert out["criticality_tier"] == "elevated"                  # the building did not change
    assert "criticality_unassessed" not in out["review_reasons"]


# ---------------------------------------------------------------------------
# the strategic queue is separate from the contact queue
# ---------------------------------------------------------------------------
def _tiered(asset_id, tier, slack):
    a = _ranked()
    a.update(asset_id=asset_id, criticality_tier=tier, slack_min=slack,
             criticality_factors=[] if tier == "routine" else ["hazardous_materials"],
             criticality_basis="criticality-proto-2026-09-19; analyst override: test")
    return a


def test_strategic_queue_orders_by_tier_then_window_and_drops_routine():
    assets = [_tiered("a:routine", "routine", 5.0), _tiered("b:elevated", "elevated", 900.0),
              _tiered("c:high", "high", 120.0), _tiered("d:high", "high", 10.0),
              _tiered("e:none", None, 1.0)]
    out = priority.strategic_queue(assets, config)
    assert [a["asset_id"] for a in out] == ["d:high", "c:high", "b:elevated"]


def test_strategic_queue_puts_an_unknown_window_last_within_its_tier():
    assets = [_tiered("a:high", "high", None), _tiered("b:high", "high", 400.0)]
    assert [a["asset_id"] for a in priority.strategic_queue(assets, config)] == ["b:high", "a:high"]


def test_criticality_never_reorders_the_contact_queue():
    """readme 6: property value does not override contact urgency."""
    snap = json.loads(open("fixtures/snapshots/gavarres_real_0002.json").read())
    before = [a["asset_id"] for a in priority.rank_snapshot(snap, config)["ranked"]]

    tiered = copy.deepcopy(snap)
    for a in tiered["assets"]:                       # make the LAST-ranked assets the most critical
        if a["asset_type"] in ("school", "campsite"):
            a["criticality_tier"] = "exceptional"
            a["criticality_factors"] = ["irreplaceable_holdings", "national_research_infrastructure"]
            a["criticality_basis"] = "test"
    ranked = priority.rank_snapshot(tiered, config)
    assert [a["asset_id"] for a in ranked["ranked"]] == before
    # The strategic view is its own list: everything in it carries a tier above the default, and it
    # is not just the contact queue relabelled.
    strategic = ranked["strategic"]
    assert strategic
    assert all(a["criticality_tier"] == "exceptional" for a in strategic)
    assert [a["asset_id"] for a in strategic] != [a["asset_id"] for a in ranked["ranked"]]
    assert priority.strategic_queue(snap["assets"], config) == []   # nothing tiered, nothing strategic


# ---------------------------------------------------------------------------
# the agent side
# ---------------------------------------------------------------------------
def test_lookup_notability_is_stateless_and_dispatches_without_a_workbench():
    assert "lookup_notability" in agent.STATELESS_TOOLS
    out = agent.dispatch("lookup_notability", {"query": "Barcelona Supercomputing Center"}, workbench=None)
    assert isinstance(out, list), out
    assert out and isinstance(out[0], dict) and out[0]["summary"]


def test_lookup_notability_has_nothing_for_an_ordinary_school():
    assert agent.dispatch("lookup_notability", {"query": "Escola Joan de Margarit"}, workbench=None) == []


def test_the_agent_can_only_propose_policy_tiers_and_policy_factors():
    assert agent._check_value("criticality_tier", {"tier": "elevated", "factors": ["hazardous_materials"]}) == \
        {"tier": "elevated", "factors": ["hazardous_materials"]}
    for bad in ({"tier": "priceless", "factors": []},
                {"tier": "high", "factors": ["it_is_famous"]},
                {"tier": "exceptional", "factors": ["hazardous_materials"]}):
        with pytest.raises(ValueError):
            agent._check_value("criticality_tier", bad)


def test_the_criticality_tool_and_field_are_declared_to_the_model():
    names = [t["name"] for t in agent.TOOLS]
    assert "lookup_notability" in names
    propose = next(t for t in agent.TOOLS if t["name"] == "propose_update")
    assert "criticality_tier" in propose["input_schema"]["properties"]["field"]["enum"]
    assert "object" in propose["input_schema"]["properties"]["value"]["type"]
    assert "criticality_unassessed" in agent.SYSTEM_PROMPT


def test_fake_llm_investigates_criticality_and_stays_grounded():
    """The offline path must produce a tier the analyst can act on, quoting its own tool result."""
    asset = _ranked()
    wb = agent.Workbench.from_scored([asset])
    wb.assets[asset["asset_id"]]["name"] = "Institut Català de Recerca de l'Aigua"
    rec = agent.investigate(wb, asset["asset_id"])
    props = [p for p in wb.proposals if p["field"] == "criticality_tier"]
    assert len(props) == 1
    tier, factors = priority.criticality_value(props[0]["value"])
    assert tier in TIERS and all(f in FACTORS for f in factors)
    assert props[0]["quoted_snippet"]
    assert rec["postcheck_ok"] is True


def test_fake_llm_calls_no_criticality_tool_for_an_asset_that_was_not_flagged():
    asset = _ranked(reasons=("occupancy_unknown",))
    wb = agent.Workbench.from_scored([asset])
    rec = agent.investigate(wb, asset["asset_id"])
    assert "lookup_notability" not in [c["name"] for c in rec["tool_calls"]]
    assert [p for p in wb.proposals if p["field"] == "criticality_tier"] == []
