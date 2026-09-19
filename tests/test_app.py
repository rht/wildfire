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
