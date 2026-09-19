"""Guards on the Streamlit page source itself (fireline/app.py).

Streamlit's "magic" renders every bare expression statement in the script, so a statement written
only for its side effects (`a.append(x), b.append(y)` evaluates to a tuple) is printed to the page.
That once filled the analyst screen with "None None" lines, one per map point."""

from __future__ import annotations

import ast
from pathlib import Path

from fireline import ui_state

APP = Path(__file__).resolve().parent.parent / "fireline" / "app.py"


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
    assert any("store stays at 2" in w.value for w in at.warning)
    assert any("Earlier moment under review" in w.value for w in at.warning)

    at = at.sidebar.select_slider[0].set_value(second).run()         # the slider scrubs back to the update
    assert not at.exception and at.sidebar.select_slider[0].value == second
    assert not at.warning
