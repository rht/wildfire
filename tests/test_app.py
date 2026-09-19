"""Guards on the Streamlit page source itself (fireline/app.py).

Streamlit's "magic" renders every bare expression statement in the script, so a statement written
only for its side effects (`a.append(x), b.append(y)` evaluates to a tuple) is printed to the page.
That once filled the analyst screen with "None None" lines, one per map point."""

from __future__ import annotations

import ast
from pathlib import Path

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
