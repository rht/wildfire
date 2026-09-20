"""Cached, quotable cost references for the custom-valuation layer.

`config.VALUE_AT_RISK_POLICY` prices an average building of a class. Four classes have no row there,
because within them one building is not like another (`config.CUSTOM_VALUATION_POLICY`). For those,
the investigation agent may propose a bespoke figure - but it may only state numbers that appear in
one of its own tool results (`agent.postcheck_numbers`) and may only quote evidence verbatim
(`scripts/validate.py` `_supported`). So it needs an offline corpus of quotable cost sentences, and
this module serves it.

Deliberately offline and deliberately small. Unlike `fireline.notability`, there is no network half:
these are published cost tables and labelled project assumptions, committed to
`fixtures/valuation_references.json` and reviewed by a human, not scraped at runtime.

Record shape (CONTRACTS.md, Conventions: every key always present, unknown is `null`, never zero)::

    {"reference_id", "applies_to", "title", "statement", "unit", "amount_eur", "basis",
     "source", "url", "observed_at"}

`basis` is the honesty field. `"published"` means the figure comes from a cited external table;
`"assumed"` means it is this project's own placeholder, and the `statement` of such a record carries
an `[assumed]` marker inside the text, so an agent quoting it verbatim carries the caveat with it and
an analyst reading the proposal sees it. Nothing in the corpus values any particular building.
"""

from __future__ import annotations

import json
from pathlib import Path

from .notability import fold

ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = ROOT / "fixtures" / "valuation_references.json"
MIN_TOKEN_LEN = 4        # shorter tokens ("de", "la", "sant") match everything


def _corpus() -> dict:
    if not CORPUS_PATH.exists():
        return {"version": None, "source": None, "references": []}
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def references() -> list[dict]:
    """Every committed reference, in file order."""
    return list(_corpus().get("references") or [])


def _tokens(text: str) -> set[str]:
    return {t for t in fold(text).replace("-", " ").split() if len(t) >= MIN_TOKEN_LEN}


def lookup(asset_type: str | None = None, query: str = "", limit: int = 4) -> list[dict]:
    """References usable for this class, best match first; never touches the network.

    `asset_type` filters on `applies_to` - a reference for teaching buildings is not evidence about an
    aerodrome. `query` is an optional free-text nudge scored over the title and statement, so an agent
    investigating a computing centre sees the per-rack reference before the per-square-metre one. An
    empty result is an ordinary answer and means the corpus has nothing for this class: the agent
    should then propose `not_valued` rather than invent a figure.
    """
    wanted = _tokens(query)
    out = []
    for i, ref in enumerate(references()):
        applies = ref.get("applies_to") or []
        if asset_type and applies and asset_type not in applies:
            continue
        overlap = len(wanted & _tokens(f"{ref.get('title') or ''} {ref.get('statement') or ''}"))
        out.append((-overlap, i, ref))
    out.sort(key=lambda t: (t[0], t[1]))
    return [ref for _, _, ref in out[:max(0, int(limit))]]
