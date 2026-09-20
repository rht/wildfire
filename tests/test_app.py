"""Guards on the Streamlit page source itself (fireline/app.py).

Streamlit's "magic" renders every bare expression statement in the script, so a statement written
only for its side effects (`a.append(x), b.append(y)` evaluates to a tuple) is printed to the page.
That once filled the analyst screen with "None None" lines, one per map point."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

from tests.helpers import make_asset

from fireline import config, ui_state

APP = Path(__file__).resolve().parent.parent / "fireline" / "app.py"


def test_importing_display_helpers_does_not_start_a_streamlit_session():
    code = '''
from unittest.mock import patch
with patch('streamlit.set_page_config', side_effect=AssertionError('page rendered during import')), \\
     patch('fireline.ui_state.Session', side_effect=AssertionError('session opened during import')):
    from fireline.app import fmt
    assert fmt(None) == '-'
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_no_bare_expressions_streamlit_magic_would_render():
    source = APP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rendered = [
        (node.lineno, ast.get_source_segment(source, node).splitlines()[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and not isinstance(node.value, ast.Call)
        and not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))  # docstrings
    ]
    assert not rendered, f"bare expressions Streamlit magic would print: {rendered}"


def test_default_scenario_is_the_real_area_one_and_is_discoverable():
    """`make demo` opens on the real Gavarres scenario; the synthetic one stays selectable."""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    default = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                   and getattr(node.targets[0], "id", None) == "DEFAULT_SCENARIO")
    scenarios, _ = ui_state.discover_snapshots()
    assert default == "gavarres_real" and default in scenarios
    assert "synthetic_gavarres" in scenarios


def test_sequence_controls_step_back_to_an_earlier_snapshot_without_rewinding_the_store(tmp_path, monkeypatch):
    """The page's own widgets: Next update advances, Previous steps back to the earlier moment as a
    labelled view, and the slider scrubs to any snapshot. Streamlit keeps widget state by key, so this
    also guards the sequence slider against overriding a Previous/Next click on the next rerun."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("FIRELINE_DB", str(tmp_path / "app.sqlite"))
    at = AppTest.from_file(str(APP), default_timeout=180).run()
    assert not at.exception
    previous, next_update = at.sidebar.button[0], at.sidebar.button[1]
    assert (previous.label, next_update.label) == ("Previous", "Next update")
    assert previous.disabled and not next_update.disabled            # first snapshot: nowhere to go back to
    first, second = at.sidebar.select_slider[0].options[:2]
    assert first.startswith("1 - ") and second.startswith("2 - ")    # sequence and as_of label each step
    opening_title = at.title[0].value
    assert not at.warning

    at = at.sidebar.button[1].click().run()                          # Next update: applied, no review banner
    assert not at.exception and at.sidebar.select_slider[0].value == second and not at.warning

    at = at.sidebar.button[0].click().run()                          # Previous: an earlier moment, view only
    assert not at.exception and at.title[0].value == opening_title
    assert at.sidebar.select_slider[0].value == first
    assert any("store stays at 2" in w.value for w in at.warning)          # sidebar notice, no body banner
    assert any("Earlier moment under review" in e.label for e in at.expander)

    at = at.sidebar.select_slider[0].set_value(second).run()         # the slider scrubs back to the update
    assert not at.exception and at.sidebar.select_slider[0].value == second
    assert not at.warning


def test_the_header_carries_the_same_step_pair_as_the_sidebar(tmp_path, monkeypatch):
    """The page header holds Previous beside Next update: dead on the first snapshot, and after a
    forward step it takes the session back to the earlier moment. The sidebar pair is untouched."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("FIRELINE_DB", str(tmp_path / "header.sqlite"))
    at = AppTest.from_file(str(APP), default_timeout=180).run()
    assert not at.exception
    assert at.button(key="ra-prev-btn").label == "← Previous"
    assert at.button(key="ra-prev-btn").disabled            # sequence 1: nowhere to step back to
    assert not at.button(key="ra-next-btn").disabled
    first, second = at.sidebar.select_slider[0].options[:2]

    at = at.button(key="ra-next-btn").click().run()          # forward, from the header
    assert not at.exception and at.sidebar.select_slider[0].value == second
    assert not at.button(key="ra-prev-btn").disabled

    at = at.button(key="ra-prev-btn").click().run()          # back, from the header
    assert not at.exception and at.sidebar.select_slider[0].value == first
    assert any("store stays at 2" in w.value for w in at.warning)   # an earlier moment, view only
    assert [b.label for b in at.sidebar.button[:2]] == ["Previous", "Next update"]


# --------------------------------------------------------------- dashboard display helpers
def test_window_segments_are_the_window_components_not_a_new_score():
    """The ranked row's bar re-draws the snapshot's own arithmetic: evacuation + buffer + window."""
    from fireline.app import window_segments

    a = make_asset(window_components={"evacuation_min": 90.0, "buffer_min": 30.0}, evacuation_min=90.0)
    a.update(slack_min=62.0, time_to_impact_min=182.0)
    assert window_segments(a) == [90.0, 30.0, 62.0]
    assert sum(window_segments(a)) == a["time_to_impact_min"]
    assert window_segments(make_asset()) == [None, None, None]     # no components: an empty bar


def test_window_tone_matches_the_three_bands_the_map_paints():
    from fireline.app import SMALL_WINDOW_MIN, window_tone

    ranked = dict(queue="ranked", priority_status="window_open")
    assert window_tone({**ranked, "slack_min": -5.0, "priority_status": "window_exhausted"}) == "red"
    assert window_tone({**ranked, "slack_min": SMALL_WINDOW_MIN - 1}) == "orange"
    assert window_tone({**ranked, "slack_min": SMALL_WINDOW_MIN + 1}) == "ink"
    assert window_tone({"queue": "needs_review", "slack_min": None}) == "grey"


def test_location_subtitle_reads_type_and_distance_and_says_when_there_is_none():
    from fireline.app import location_subtitle

    assert location_subtitle({"asset_type": "school", "distance_to_fire_m": 1400.4}) == "school - 1,400 m to fire"
    assert location_subtitle({"asset_type": "masia"}) == "masia - distance unknown"


def test_stamp_shortens_a_timestamp_and_keeps_anything_it_cannot_parse():
    from fireline.app import stamp

    assert stamp("2026-07-03T13:20:01.981000+00:00") == "13:20Z 2026-07-03"
    assert stamp(None) == "-" and stamp("not a time") == "not a time"


def test_escalation_counts_split_the_task_pipeline():
    from fireline.app import escalation_counts

    rows = [{"status": s} for s in ("open", "assigned", "in_progress", "blocked", "done", "done")]
    assert escalation_counts(rows) == {"done": 2, "blocked": 1, "open": 3}
    assert escalation_counts([]) == {"done": 0, "blocked": 0, "open": 0}


# --------------------------------------------------------------- value at risk (handoff 002)
def var_status(**totals) -> dict:
    """A `Session.status()` value_at_risk block with the layer on."""
    v = {"layer": True, "people_exposed": 100.0, "people_at_risk_p50": 0, "people_at_risk_p10": 0,
         "expected_loss_eur_low": 300_000, "expected_loss_eur_mid": 800_000, "expected_loss_eur_high": 1_600_000,
         "located": 10, "excluded_people": 2, "excluded_eur": 1, "covered": 10, "reached": 10,
         "horizon_at": "2026-07-03T20:00:00+00:00", "enrichment": False,
         "policy_version": config.VALUE_AT_RISK_POLICY["version"]}
    v.update(totals)
    return v


def test_display_columns_show_the_layer_and_fall_back_to_a_dash_without_it():
    from fireline.app import at_risk, exposed, loss_eur

    with_layer = make_asset(value_at_risk=True, estimated_occupancy=200, burn_probability=0.5, capacity=None,
                            forecast_source="fixture:test", evacuation_min=90.0, evacuation_source="policy",
                            arrival_p10_at="2026-07-03T09:00:00+00:00")
    assert exposed(with_layer) == "100"
    assert at_risk(with_layer) == "0 / 200"       # p50 not reached inside the horizon, p10 exhausted
    assert loss_eur(with_layer) == "EUR 800,000 (300,000 - 1,600,000)"
    without = make_asset()
    assert exposed(without) == "-" and at_risk(without) == "-" and loss_eur(without) == "-"
    nucleus = make_asset(value_at_risk=True, asset_type="nucleus", estimated_occupancy=50, burn_probability=0.4,
                         capacity=None, forecast_source="fixture:test")
    assert loss_eur(nucleus) == "not valued" and exposed(nucleus) == "20"


def test_euro_column_is_a_display_string_so_it_cannot_order_the_table():
    """The ranked frame keeps its window order and the euro column is text, not a number to sort on."""
    from fireline.app import LOSS_COLUMN, ranked_frame

    rows = []
    for i, (occ, bp) in enumerate(((10, 0.9), (400, 0.1)), start=1):
        a = make_asset(value_at_risk=True, asset_id=f"fixture:{i}", name=f"Asset {i}", capacity=None,
                       estimated_occupancy=occ, burn_probability=bp, forecast_source="fixture:test",
                       evacuation_min=90.0, evacuation_source="policy")
        a.update(priority_rank=i, priority_status="window_open", slack_min=float(i), latest_start_min=float(i),
                 time_to_impact_min=float(i), queue="ranked", priority_reasons=[])
        rows.append(a)
    frame = ranked_frame(rows, {})
    assert list(frame["rank"]) == [1, 2]                                   # window order, untouched
    assert frame[LOSS_COLUMN].map(type).eq(str).all()
    assert "estimate" in LOSS_COLUMN and "assumed replacement cost" in LOSS_COLUMN
    assert {"people exposed", "people at risk p50 / p10"} <= set(frame.columns)


def test_review_frame_carries_the_layer_columns_too():
    from fireline.app import LOSS_COLUMN, review_frame

    a = make_asset(value_at_risk=True, estimated_occupancy=200, burn_probability=0.5, capacity=None,
                   forecast_source="fixture:test", review_reasons=["forecast_unavailable"])
    a.update(priority_reasons=["needs review: forecast_unavailable; not ranked"])
    frame = review_frame([a], {})
    assert {"people exposed", "people at risk p50 / p10", LOSS_COLUMN} <= set(frame.columns)


def test_value_frame_shows_the_arithmetic_or_nothing_at_all():
    from fireline.app import value_frame

    assert value_frame(make_asset()) is None                               # layer off: no panel
    a = make_asset(value_at_risk=True, estimated_occupancy=200, burn_probability=0.5, capacity=None,
                   forecast_source="fixture:test", evacuation_min=90.0, evacuation_source="policy",
                   arrival_p10_at="2026-07-03T09:00:00+00:00")
    frame = value_frame(a)
    components = list(frame["component"])
    assert components == ["people exposed", "replacement value", "damage ratio low / mid / high",
                          "expected loss", "people at risk (p50)", "people at risk (p10)"]
    text = " ".join(frame["basis / source"])
    assert "occupancy 200 x burn probability 0.5" in text
    assert "window exhausted at arrival 2026-07-03T09:00:00+00:00" in text   # p10 fired, with its quantile
    assert "does not reach it inside its horizon" in text                    # p50 is null: covered, not reached


def test_header_reads_a_forecast_that_reaches_nothing_as_a_zero_not_a_gap():
    from fireline.app import value_summary

    zero = value_summary(var_status(people_exposed=0, expected_loss_eur_low=0, expected_loss_eur_mid=0,
                                    expected_loss_eur_high=0, reached=0, covered=99, located=99),
                         "2026-07-03T08:00:00+00:00")
    assert zero.startswith("0 exposed within the 12 h forecast horizon")     # derived, not hardcoded
    assert "not a missing forecast" in zero and "forecast_unavailable" in zero
    gap = value_summary(var_status(), "2026-07-03T08:00:00+00:00")
    assert "people exposed within the 12 h forecast horizon" in gap
    assert "2 are left out of the people totals and 1 out of the euro totals" in gap
    off = value_summary({"layer": False}, "2026-07-03T08:00:00+00:00")
    assert "Value at risk is off for this scenario" in off


def test_header_limits_state_the_assumptions_and_the_ca_bias():
    from fireline.app import value_limits

    text = value_limits(var_status())
    for phrase in ("per-class assumptions", "damage-ratio band only", "total economic loss",
                   "does not model partial clearance", "never enter the ranking, the sort or a filter",
                   "no euro figure for lives"):
        assert phrase in text, phrase
    assert "uncalibrated CA ensemble" not in text                            # a provider forecast: no CA caveat
    assert "uncalibrated CA ensemble" in value_limits(var_status(enrichment=True))
    assert value_limits({"layer": False}) == ""
