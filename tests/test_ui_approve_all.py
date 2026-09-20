"""The approve-all preview on the analyst screen (fireline/app.py).

The toggle applies every pending agent proposal in memory so the contact queue can be read with and
without them. On every shipped snapshot that changes nothing about the order: the fields the offline
agent proposes (criticality_tier, capacity, an asset_type it already has) are absent from
`contact_priority.contact_sort_key`, so `rows_moved` is 0. These tests hold the page to saying that
plainly - a column of zeros with a sentence explaining why, never an empty state or an implied move.

`fireline.ui_state.Session` is deliberately not imported: the page's share of this feature is pure
string and frame building, tested here against plain dicts and a stub session.
"""

from __future__ import annotations

import pytest

from fireline.app import (APPROVE_ALL_HELP, APPROVE_ALL_LABEL, RANK_DELTA_COLUMN, SORT_KEY_NOTE,
                          approve_all_caption, by_field_text, rank_delta, ranked_frame,
                          render_approve_all_toggle)


def asset(asset_id="fixture:1", rank=1, **extra) -> dict:
    """A ranked row, with only the keys `ranked_frame` reads (no Session, no snapshot loading)."""
    a = {"asset_id": asset_id, "name": f"Asset {asset_id}", "asset_type": "school",
         "municipality": "Cassa de la Selva", "distance_to_fire_m": 1400.0,
         "fire_arrival_at": "2026-07-03T14:00:00+00:00", "forecast_source": "fixture:test",
         "evacuation_min": 90.0, "latest_start_min": 30.0, "slack_min": 62.0,
         "priority_status": "window_open", "priority_rank": rank, "review_reasons": [],
         "estimated_occupancy": 120, "queue": "ranked"}
    a.update(extra)
    return a


def report(**extra) -> dict:
    """A `Session.preview_report`: every key always present (the module contract)."""
    r = {"on": True, "llm_label": "FakeLLM", "investigated": 4, "proposals": 13,
         "by_field": {"criticality_tier": 13}, "ranked_before": 72, "ranked_after": 72,
         "rows_moved": 0, "entered_ranked": [], "left_ranked": [], "flags_cleared": 0, "tiers_set": 13}
    r.update(extra)
    return r


# --------------------------------------------------------------- the delta column appears only in preview
def test_the_ranked_table_is_untouched_while_the_preview_is_off():
    """The normal table keeps exactly its columns: no rank-movement column, in the old column order."""
    off = ranked_frame([asset()], {})
    assert RANK_DELTA_COLUMN not in off.columns
    assert list(off.columns)[0] == "rank" and list(off.columns)[1] == "name"
    assert list(ranked_frame([asset()], {}, False).columns) == list(off.columns)


def test_the_preview_adds_one_rank_movement_column_next_to_the_rank():
    on = ranked_frame([asset(preview_rank_delta=0, preview_baseline_rank=1)], {}, True)
    assert list(on.columns)[:3] == ["rank", RANK_DELTA_COLUMN, "name"]
    assert set(ranked_frame([asset()], {}).columns) | {RANK_DELTA_COLUMN} == set(on.columns)
    assert on[RANK_DELTA_COLUMN].iloc[0] == "0 (no move)"


def test_the_column_header_says_which_direction_a_plus_sign_means():
    assert "+" in RANK_DELTA_COLUMN and "up" in RANK_DELTA_COLUMN


def test_an_empty_ranked_queue_still_builds_a_frame():
    assert ranked_frame([], {}, True).empty and ranked_frame([], {}).empty


# --------------------------------------------------------------- the signed formatting
def test_rank_movement_is_signed_and_zero_is_written_as_no_move():
    """Up is positive: a smaller rank number is nearer the top, so the sign is flipped for the reader."""
    assert rank_delta(asset(preview_rank_delta=3, preview_baseline_rank=9)) == "+3 (up)"
    assert rank_delta(asset(preview_rank_delta=1, preview_baseline_rank=2)) == "+1 (up)"
    assert rank_delta(asset(preview_rank_delta=0, preview_baseline_rank=4)) == "0 (no move)"
    assert rank_delta(asset(preview_rank_delta=-2, preview_baseline_rank=1)) == "-2 (down)"
    assert rank_delta(asset(preview_rank_delta=-11, preview_baseline_rank=1)) == "-11 (down)"


def test_a_row_that_had_no_rank_before_says_so_instead_of_claiming_a_move():
    assert rank_delta(asset(preview_rank_delta=None, preview_baseline_rank=None)) == "new (not ranked before)"
    assert rank_delta(asset(preview_rank_delta=None, preview_baseline_rank=7)) == "-"
    assert rank_delta(asset()) == "new (not ranked before)"     # preview off: the column is not rendered


# --------------------------------------------------------------- the summary line
def test_no_caption_at_all_while_the_preview_is_off():
    assert approve_all_caption(None) == "" and approve_all_caption({"on": False}) == ""
    assert approve_all_caption(report(on=False)) == ""
    assert approve_all_caption(None, brief=True) == ""


def test_zero_rows_moved_is_explained_by_the_sort_key_not_shown_as_a_failure():
    """The measured case on every shipped snapshot: 13 criticality_tier proposals, identical order."""
    text = approve_all_caption(report())
    assert "13 agent proposal(s) from 4 investigated location(s)" in text
    assert "criticality_tier 13" in text and "llm mode `FakeLLM`" in text
    assert "13 criticality tier(s) set" in text and "0 review flag(s) cleared" in text
    assert "store holds no new overrides" in text
    assert "Contact order: unchanged - 0 of 72 ranked rows moved" in text
    assert "the designed separation, not a failed button" in text
    assert SORT_KEY_NOTE in text
    assert "contact_priority.contact_sort_key" in text and "readme 6" in text
    assert "only asset_type and evacuation_min can move a row" in text
    assert "VALIDATION.md" in text
    for wrong in ("no rows", "nothing changed", "failed", "empty", "error"):
        assert wrong not in text.replace("not a failed button", "")


def test_rows_that_did_move_are_counted_without_the_sort_key_lecture():
    text = approve_all_caption(report(rows_moved=5, ranked_before=70, ranked_after=72,
                                      entered_ranked=["a", "b"], left_ranked=[],
                                      by_field={"evacuation_min": 5}, flags_cleared=5, tiers_set=0))
    assert "Contact order: 5 of 72 ranked row(s) moved" in text
    assert "2 entered the ranked queue and 0 left it" in text
    assert "70 ranked before, 72 after" in text
    assert "evacuation_min 5" in text and "5 review flag(s) cleared" in text
    assert SORT_KEY_NOTE not in text                       # the explanation belongs to the zero case
    assert "unchanged" not in text


def test_nothing_to_approve_is_stated_rather_than_shown_as_an_empty_result():
    text = approve_all_caption(report(proposals=0, investigated=0, by_field={}, tiers_set=0,
                                      rows_moved=0, ranked_after=72))
    assert "there is nothing to approve" in text
    assert "no proposals on this snapshot" in text and "0 location(s) investigated" in text
    assert "llm mode `FakeLLM`" in text
    assert "72 ranked row(s) below are the confirmed ranking, unchanged" in text
    assert "store holds no new overrides" in text
    assert "rows moved" not in text                        # no movement claim, none was possible
    assert "0 of 72" not in text


def test_the_brief_card_line_says_preview_and_store_in_one_sentence():
    """The right-hand card is narrow: a short line there, the full account above the table."""
    zero = approve_all_caption(report(), brief=True)
    assert zero.startswith("Preview: 13 proposal(s) applied in memory")
    assert "nothing written to the store" in zero
    assert "contact order unchanged, 0 of 72 rows moved" in zero
    assert "not in the sort key" in zero
    assert len(zero) < len(approve_all_caption(report()))

    moved = approve_all_caption(report(rows_moved=5), brief=True)
    assert "5 of 72 row(s) moved." in moved and "unchanged" not in moved

    none = approve_all_caption(report(proposals=0), brief=True)
    assert "the agent has proposed nothing on this snapshot" in none


def test_by_field_text_lists_every_field_the_proposals_would_write():
    assert by_field_text({"criticality_tier": 13}) == "criticality_tier 13"
    assert by_field_text({"capacity": 2, "asset_type": 1}) == "asset_type 1, capacity 2"
    assert by_field_text({}) == "no field" and by_field_text(None) == "no field"


# --------------------------------------------------------------- the control itself
class StubSession:
    """Only what the toggle touches: the flag, and the call that rescores in memory."""

    def __init__(self, approve_all=False):
        self.approve_all = approve_all
        self.preview_report = None
        self.calls: list[bool] = []

    def set_approve_all(self, on: bool) -> dict:
        self.calls.append(on)
        self.approve_all = on
        self.preview_report = report(on=on)
        return self.preview_report


class Reran(Exception):
    pass


@pytest.fixture
def stub_streamlit(monkeypatch):
    """`app.st.toggle` returns what the analyst clicked; `st.rerun` records that the page reran."""
    from fireline import app

    seen: dict = {}

    def fake_toggle(label, value=False, key=None, help=None):
        seen.update(label=label, value=value, key=key, help=help)
        return seen["click"]

    def fake_rerun():
        raise Reran

    monkeypatch.setattr(app.st, "toggle", fake_toggle)
    monkeypatch.setattr(app.st, "rerun", fake_rerun)
    return seen


def test_the_toggle_previews_and_reruns_when_it_is_switched_on(stub_streamlit):
    sess = StubSession()
    stub_streamlit["click"] = True
    with pytest.raises(Reran):
        render_approve_all_toggle(sess)
    assert sess.calls == [True] and sess.preview_report["on"] is True
    assert stub_streamlit["value"] is False and stub_streamlit["key"] == "ra-approve-all"


def test_switching_it_off_previews_the_confirmed_data_again(stub_streamlit):
    sess = StubSession(approve_all=True)
    stub_streamlit["click"] = False
    with pytest.raises(Reran):
        render_approve_all_toggle(sess)
    assert sess.calls == [False]


def test_an_unchanged_toggle_neither_rescores_nor_reruns(stub_streamlit):
    sess = StubSession(approve_all=True)
    stub_streamlit["click"] = True
    render_approve_all_toggle(sess)                       # no Reran: the page is already in this state
    assert sess.calls == []


def test_the_control_cannot_be_read_as_a_real_approval(stub_streamlit):
    sess = StubSession()
    stub_streamlit["click"] = False
    render_approve_all_toggle(sess)
    assert "Preview" in APPROVE_ALL_LABEL and stub_streamlit["label"] == APPROVE_ALL_LABEL
    assert stub_streamlit["help"] == APPROVE_ALL_HELP
    for phrase in ("not an approval", "in memory", "SQLite store is untouched", "no override is confirmed",
                   "no analyst has checked these proposals"):
        assert phrase in APPROVE_ALL_HELP, phrase
