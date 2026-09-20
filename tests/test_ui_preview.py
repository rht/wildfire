"""Approve-all preview (`ui_state.Session.set_approve_all`): the ranking with every pending agent
recommendation approved, in memory only.

Two kinds of test here. The constructed two-snapshot scenario checks the mechanics (reversibility,
the report shape, the per-asset delta, the cache, and that the store is never written to). The
shipped snapshots check the honest result: approving every offline proposal leaves the contact order
byte-identical, because none of the fields the offline agent proposes is in the sort key.

The shipped-snapshot fixture runs one `agent.investigate_all` sweep per snapshot (93 flagged assets
on each `gavarres_real`), so it costs about a minute; it is module-scoped and shared by every test
that reads it.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fireline import agent, config, ui_state
from fireline.ui_state import Session
from tests.helpers import make_asset, make_snapshot

SCENARIO = "preview_test"
A, B, C = "fixture:a_near", "fixture:b_far", "fixture:c_no_evacuation"
FORECAST = "fixture:test-spread (synthetic)"
EVAC = config.EVACUATION_POLICY["version"]
SHIPPED = Path(__file__).resolve().parent.parent / "fixtures" / "snapshots"
PREVIEW_KEYS = ("preview_rank_delta", "preview_baseline_rank")


def timed(asset_id, arrival_at, evacuation_min, **kw):
    fields = dict(asset_id=asset_id, fire_arrival_at=arrival_at, fire_arrival_basis="p10",
                  forecast_source=FORECAST, forecast_horizon_at="2026-07-03T20:00:00+00:00",
                  evacuation_min=evacuation_min, evacuation_source=EVAC if evacuation_min is not None else None)
    fields.update(kw)
    return make_asset(**fields)


def scenario_files(tmp_path):
    """Two snapshots, as_of 08:00. Baseline windows: A 60 min, B 120 min, C unranked (no duration)."""
    d = tmp_path / "snapshots"
    d.mkdir()
    assets = [
        timed(A, "2026-07-03T11:00:00+00:00", 90.0, name="A near", distance_to_fire_m=800.0),
        timed(B, "2026-07-03T12:00:00+00:00", 90.0, name="B far", asset_type="camp", distance_to_fire_m=4000.0),
        timed(C, "2026-07-03T11:30:00+00:00", None, name="C without a duration", asset_type="camp",
              distance_to_fire_m=2500.0, review_reasons=["evacuation_unknown"]),
    ]
    s1 = make_snapshot(assets, scenario_id=SCENARIO, sequence=1)
    s2 = json.loads(json.dumps(s1))
    s2.update(sequence=2, snapshot_id=f"{SCENARIO}-0002", as_of="2026-07-03T08:30:00+00:00",
              computed_at="2026-07-03T08:30:00+00:00")
    (d / "preview_test_0001.json").write_text(json.dumps(s1), encoding="utf-8")
    (d / "preview_test_0002.json").write_text(json.dumps(s2), encoding="utf-8")
    return (d,)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "store" / "fireline.sqlite"


@pytest.fixture
def session(db, tmp_path):
    s = Session(db_path=db, snapshot_dirs=scenario_files(tmp_path))
    s.select_scenario(SCENARIO)
    return s


def order(session) -> list[str]:
    return [a["asset_id"] for a in session.scored["ranked"]]


def fake_sweep(*proposals):
    """A stand-in for `agent.investigate_all` that records the given (asset_id, field, value) triples
    as pending proposals through the real `propose_update`, so the validation still runs."""

    def sweep(workbench, llm=None, max_steps=6):
        for asset_id, field, value in proposals:
            agent.propose_update(asset_id, field, value, source="test evidence page",
                                 quoted_snippet=f"the drill took {value} minutes end to end",
                                 confidence="medium", workbench=workbench)
        return []

    return sweep


# ---------------------------------------------------------------------------------------------
# mechanics
# ---------------------------------------------------------------------------------------------

def test_preview_is_off_by_default(session):
    assert session.approve_all is False
    assert session.preview_report is None
    assert not any(k in a for a in session.scored["all"] for k in PREVIEW_KEYS)


def test_set_approve_all_is_reversible_and_annotates_only_while_on(session):
    before = order(session)
    report = session.set_approve_all(True)
    assert session.approve_all is True and report is session.preview_report
    assert all(k in a for a in session.scored["all"] for k in PREVIEW_KEYS)

    assert session.set_approve_all(False) is None
    assert session.approve_all is False and session.preview_report is None
    assert order(session) == before
    assert not any(k in a for a in session.scored["all"] for k in PREVIEW_KEYS)


def test_report_carries_every_key(session):
    report = session.set_approve_all(True)
    assert set(report) == {"on", "llm_label", "investigated", "proposals", "by_field", "ranked_before",
                           "ranked_after", "rows_moved", "entered_ranked", "left_ranked", "flags_cleared",
                           "tiers_set"}
    assert report["on"] is True
    assert report["llm_label"] == ui_state.FAKE_LABEL_NO_KEY      # conftest strips the API keys
    assert report["investigated"] == 1                            # only C carries an actionable review reason
    assert report["ranked_before"] == 2


def test_preview_moves_rows_when_a_proposal_touches_a_sort_key_input(session, monkeypatch):
    """The delta column has to work when the agent proposes something the order depends on.

    `evacuation_min` is in the window arithmetic, so approving it re-sorts the queue. This is the
    regression guard for the shipped-snapshot result below: if a future change silently dropped the
    preview overrides, `rows_moved` would be 0 here too and this test would fail."""
    monkeypatch.setattr(agent, "investigate_all",
                        fake_sweep((B, "evacuation_min", 200.0), (C, "evacuation_min", 60.0)))
    report = session.set_approve_all(True)
    assert report["by_field"] == {"evacuation_min": 2}
    assert order(session) == [B, A, C]            # windows become B 10, A 60, C 120
    assert report["rows_moved"] == 2              # A and B swap; C entering is counted separately
    assert report["ranked_before"] == 2 and report["ranked_after"] == 3
    assert report["entered_ranked"] == [C] and report["left_ranked"] == []
    assert report["flags_cleared"] == 1           # C's evacuation_unknown

    by_id = {a["asset_id"]: a for a in session.scored["all"]}
    assert (by_id[B]["preview_baseline_rank"], by_id[B]["preview_rank_delta"]) == (2, 1)    # up one
    assert (by_id[A]["preview_baseline_rank"], by_id[A]["preview_rank_delta"]) == (1, -1)   # down one
    assert (by_id[C]["preview_baseline_rank"], by_id[C]["preview_rank_delta"]) == (None, None)

    session.set_approve_all(False)
    assert order(session) == [A, B]               # the store never saw any of it


def test_preview_never_shadows_a_confirmed_override(session, monkeypatch):
    """Rule: the store's own confirmed overrides always still apply; the preview is additive."""
    session.set_evacuation(B, 200.0, source="the site manager, by phone")
    assert order(session) == [B, A]
    monkeypatch.setattr(agent, "investigate_all", fake_sweep((B, "evacuation_min", 5.0)))
    report = session.set_approve_all(True)
    assert report["proposals"] == 0 and report["by_field"] == {}
    by_id = {a["asset_id"]: a for a in session.scored["all"]}
    assert by_id[B]["evacuation_min"] == 200.0
    assert by_id[B]["evacuation_source"].startswith("analyst override: the site manager")
    assert order(session) == [B, A]


def test_preview_writes_nothing_to_the_store(session, db, monkeypatch):
    monkeypatch.setattr(agent, "investigate_all", fake_sweep((C, "evacuation_min", 60.0)))

    def rows():
        with sqlite3.connect(db) as conn:
            overrides = conn.execute("SELECT count(*) FROM overrides").fetchone()[0]
            events = conn.execute("SELECT count(*) FROM events WHERE kind = 'override_confirmed'").fetchone()[0]
        return overrides, events

    assert rows() == (0, 0)
    session.set_approve_all(True)
    assert C in order(session)                                 # the preview really did apply
    session.set_approve_all(False)
    assert rows() == (0, 0)
    assert session.store.overrides() == []


def test_the_sweep_is_cached_per_snapshot(session, monkeypatch):
    calls: list[str] = []
    real = agent.investigate_all

    def counting(workbench, llm=None, max_steps=6):
        calls.append(workbench.snapshot["snapshot_id"])
        return real(workbench, llm=llm, max_steps=max_steps)

    monkeypatch.setattr(agent, "investigate_all", counting)
    session.set_approve_all(True)
    assert calls == [f"{SCENARIO}-0001"]
    session.set_approve_all(False)
    session.set_approve_all(True)
    assert calls == [f"{SCENARIO}-0001"]            # flipping the toggle must not re-run 15 s of agent

    session.next_update()                            # a different snapshot is a different sweep
    assert calls == [f"{SCENARIO}-0001", f"{SCENARIO}-0002"]
    session.previous_update()                        # and back is a cache hit, not a third sweep
    assert calls == [f"{SCENARIO}-0001", f"{SCENARIO}-0002"]


def test_the_sweep_workbench_cannot_persist_or_leak(session, monkeypatch):
    seen: list[agent.Workbench] = []

    def capturing(workbench, llm=None, max_steps=6):
        seen.append(workbench)
        agent.escalate(A, "who is on site today?", ["nobody", "staff"], "staff", workbench=workbench)
        return []

    monkeypatch.setattr(agent, "investigate_all", capturing)
    session.set_approve_all(True)
    assert seen and seen[0].tasks is None            # no store attached: nothing can be written
    assert seen[0] is not session.workbench
    assert seen[0].questions and session.workbench.questions == []    # the sweep's state stays there
    assert session.workbench.proposals == []
    assert session.investigations == {}
    assert set(session.workbench.assets) == {a["asset_id"] for a in session.scored["all"]}


def test_selecting_a_scenario_resets_the_preview(session, monkeypatch):
    monkeypatch.setattr(agent, "investigate_all", fake_sweep((C, "evacuation_min", 60.0)))
    session.set_approve_all(True)
    session.select_scenario(SCENARIO)
    assert session.approve_all is False and session.preview_report is None
    assert session._preview_cache == {}
    assert order(session) == [A, B]


# ---------------------------------------------------------------------------------------------
# the shipped snapshots: the honest comparison result
# ---------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def shipped():
    """`{snapshot_id: (report, scored)}` for every committed snapshot, one sweep each (~1 min)."""
    out = {}
    for scenario in ("synthetic_gavarres", "gavarres_real"):
        s = Session(db_path=":memory:", snapshot_dirs=(SHIPPED,))
        s.select_scenario(scenario)
        for i in range(len(s.sequence_entries)):
            s.go_to(i)
            report = s.set_approve_all(True)
            out[s.snapshot["snapshot_id"]] = (report, s.scored)
    return out


def test_approving_everything_moves_no_shipped_row(shipped):
    """Measured, not hoped for: on every shipped snapshot the contact order is unchanged.

    The offline agent proposes only `capacity`, `estimated_occupancy`, `asset_type` and
    `criticality_tier`, and none of those is in `contact_priority.contact_sort_key`: the order is
    the remaining evacuation window, then arrival, then distance, then asset_id. Criticality is kept
    out of it on purpose (readme 6, "property value does not override contact urgency"; VALIDATION.md
    records the same guard as "ranked_sort_key mentions no criticality field"), occupancy and capacity
    never enter the arithmetic, and the single `asset_type` proposal on the synthetic snapshots
    proposes `campsite` for an asset already typed `campsite`. So this is a real comparison with a
    null result, not a broken preview - `test_preview_moves_rows_when_a_proposal_touches_a_sort_key_input`
    is the same machinery moving rows when a proposal does reach the sort key.
    """
    for snapshot_id, (report, scored) in shipped.items():
        assert report["rows_moved"] == 0, snapshot_id
        assert report["entered_ranked"] == [] and report["left_ranked"] == [], snapshot_id
        assert report["ranked_before"] == report["ranked_after"], snapshot_id
        # every ranked asset kept its rank (gavarres_real-0004 ranks nothing: no arrival at all)
        assert {a["preview_rank_delta"] for a in scored["ranked"]} <= {0}, snapshot_id
        assert set(report["by_field"]) <= {"capacity", "estimated_occupancy", "asset_type",
                                           "criticality_tier"}, snapshot_id
        # and yet the sweep is not a no-op: it clears review flags on every shipped snapshot
        assert report["flags_cleared"] > 0, snapshot_id


def test_shipped_counts_are_the_measured_ones(shipped):
    for snapshot_id, (report, _) in shipped.items():
        assert report["llm_label"].startswith("fake")
        if snapshot_id.startswith("synthetic_gavarres"):
            # 7 flagged assets -> 4 proposals; the criticality one is `routine`, the default tier,
            # so nothing enters the strategic view (`tiers_set` counts non-default tiers only).
            assert report["investigated"] == 7
            assert report["proposals"] == 4
            assert report["by_field"] == {"capacity": 2, "asset_type": 1, "criticality_tier": 1}
            assert (report["flags_cleared"], report["tiers_set"]) == (2, 0)
        else:
            # 93 flagged assets -> 13 proposals, every one a criticality tier: 12 `routine` (the
            # default) and one `elevated` for the research facility, so 13 `criticality_unassessed`
            # flags clear and exactly one asset reaches the strategic view.
            assert report["investigated"] == 93
            assert report["proposals"] == 13
            assert report["by_field"] == {"criticality_tier": 13}
            assert (report["flags_cleared"], report["tiers_set"]) == (13, 1)


def test_preview_manufactures_no_override_conflict(shipped):
    """A preview override is timestamped so it never looks older than the provider's own observation:
    `override_conflict` means an analyst confirmed before the register was re-read, which a preview
    has not done."""
    for snapshot_id, (_, scored) in shipped.items():
        offenders = [a["asset_id"] for a in scored["all"] if "override_conflict" in (a["review_reasons"] or [])]
        assert offenders == [], snapshot_id
