"""scripts/validate.py check functions (readme 11 recorded outcomes). Offline, FakeLLM, no key."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("validate", ROOT / "scripts" / "validate.py")
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)


def _run(fn):
    r = fn()
    assert set(r) >= {"name", "outcome", "details", "measured"}
    assert r["outcome"] in (validate.PASS, validate.FAIL, validate.NOT_VERIFIED)
    assert all(isinstance(d, str) for d in r["details"])
    return r


@pytest.mark.parametrize("fn", [validate.check_geometry, validate.check_priority, validate.check_updates,
                                validate.check_tasks], ids=lambda f: f.__name__)
def test_check_passes(fn):
    r = _run(fn)
    assert r["outcome"] == validate.PASS, "\n".join(r["details"])


def test_priority_check_measures_the_window_and_ordering():
    r = _run(validate.check_priority)
    assert r["outcome"] == validate.PASS, "\n".join(r["details"])
    m = r["measured"]
    assert m["window_single"] == m["expected_single"] == 180.0 - 90.0 - validate.config.CONTACT_POLICY["buffer_min"]
    assert m["order"][0] == "far_downwind" and m["exhausted_order"][:2] == ["negative", "zero"]
    assert m["ties"] == ["tie_b", "tie_d", "tie_a", "tie_c"] and m["static_agreement"] is True
    assert m["fixture_ranked"] + m["fixture_review"] == 13
    assert any("farther outranks nearer" in d for d in r["details"])
    assert not any("score" in d.split("forecast")[0].lower() and "weights" in d for d in r["details"])


def test_updates_check_tolerates_an_unranked_fixture():
    r = _run(validate.check_updates)
    assert r["outcome"] == validate.PASS, "\n".join(r["details"])
    m = r["measured"]
    assert m["changed_assets_seq2"] > 0 and "ranked_any" in m
    if not m["ranked_any"]:
        assert any("no asset ranked" in d for d in r["details"])


def test_agent_check_confirms_an_evacuation_duration():
    r = _run(validate.check_agent)
    assert any("evacuation_min proposal" in d and "confirmed" in d for d in r["details"])
    assert not any("priority_score" in d or " score " in d for d in r["details"])


def test_write_regenerates_validation_markdown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(validate, "OUTPUT", tmp_path / "VALIDATION.md")
    monkeypatch.setattr(validate, "CHECKS", [validate.check_stale, validate.check_agent_live])
    assert validate.main(["--write", "--quiet"]) == 0
    text = (tmp_path / "VALIDATION.md").read_text(encoding="utf-8")
    assert "## Not verified" in text and "forecast-evacuation-window-v2" in text
    assert "priority-proto" not in text and "weights" not in text.split("## Not verified")[0]


def test_geometry_numbers():
    m = _run(validate.check_geometry)["measured"]
    assert m["overlap_distance_m"] == 0.0
    assert abs(m["separated_distance_m"] - 2000.0) <= 5.0


def test_coverage_counts():
    r = _run(validate.check_coverage)
    assert r["outcome"] == validate.PASS, "\n".join(r["details"])
    m = r["measured"]
    assert m["total"] == m["located"] + m["unlocated"] > 0


def test_agent_fake_llm_never_proposes_occupancy_from_capacity():
    r = _run(validate.check_agent)
    assert r["outcome"] == validate.PASS, "\n".join(r["details"])
    assert r["measured"]["estimated_occupancy_proposals"] == 0
    assert r["name"].endswith("(FakeLLM)")


def test_agent_live_is_not_verified():
    assert _run(validate.check_agent_live)["outcome"] == validate.NOT_VERIFIED


def test_latency_returns_a_number():
    r = _run(lambda: validate.check_latency(repeats=1))
    m = r["measured"]
    assert isinstance(m["median_s"], float) and m["median_s"] >= 0.0
    assert m["source_age_s"] == 300.0        # 10:00Z observation, 10:05Z receipt of the recorded update
    assert m["assets"] == 180


def test_stale_boundaries():
    assert _run(validate.check_stale)["outcome"] == validate.PASS


def test_markdown_has_not_verified_section():
    results = [validate.check_stale(), validate.check_agent_live()]
    md = validate.render_markdown(results)
    assert validate.VALIDATION_DATE in md
    assert "## Not verified" in md
    assert "Stale data | pass" in md and "Agent (live model) | not verified" in md


def test_valuation_check_keeps_euros_out_of_the_contact_queue():
    r = _run(validate.check_valuation)
    assert r["outcome"] == validate.PASS, "\n".join(r["details"])
    m = r["measured"]
    assert m["contact_order_unchanged"] is True
    assert m["expected_loss"] != m["damage_band_only"]        # the valuation band compounds with the damage band
    assert set(m["assess_classes"]) == set(validate.config.CUSTOM_VALUATION_POLICY["assess_classes"])
    assert any("byte-identical to the unvalued order" in d for d in r["details"])
    assert any("refused" in d and "ceiling" in d for d in r["details"])
