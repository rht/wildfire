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
    assert m["assets"] == 168


def test_stale_boundaries():
    assert _run(validate.check_stale)["outcome"] == validate.PASS


def test_markdown_has_not_verified_section():
    results = [validate.check_stale(), validate.check_agent_live()]
    md = validate.render_markdown(results)
    assert validate.VALIDATION_DATE in md
    assert "## Not verified" in md
    assert "Stale data | pass" in md and "Agent (live model) | not verified" in md
