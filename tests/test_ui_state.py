"""Session workflow over the committed fixture snapshots (fireline/ui_state.py), no Streamlit."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fireline import tasks, ui_state
from fireline.ui_state import Session

SYNTHETIC = "synthetic_gavarres"
POU = "fixture:pou_del_glac"          # occupancy_unknown; capacity null -> needs_review; distance changes in 0002
NOW = datetime(2026, 7, 3, 8, 30, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "nested" / "fireline.sqlite"


@pytest.fixture
def session(db):
    s = Session(db_path=db, clock=lambda: NOW)
    s.select_scenario(SYNTHETIC)
    return s


def test_discovery_groups_by_scenario_and_sorts_by_sequence():
    scenarios, warnings = ui_state.discover_snapshots()
    assert {SYNTHETIC, "gavarres_real"} <= set(scenarios)
    for entries in scenarios.values():
        seqs = [e["sequence"] for e in entries]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert warnings == []


def test_select_scenario_scores_and_suggests(session, db):
    assert db.exists()                                     # parent dir created, sqlite opened
    assert session.snapshot["sequence"] == 1 and session.index == 0 and session.has_next
    assert session.last_update["accepted"] is True and session.last_update["advanced"] is True
    assert session.scored["ranked"], "ranked queue must not be empty"
    assert any(a["asset_id"] == POU for a in session.scored["needs_review"])
    assert session.last_suggested, "review reasons produce suggested tasks"
    assert session.open_tasks(POU), "occupancy_unknown suggests confirm_occupancy"
    assert session.workbench.asset(POU)["queue"] == "needs_review"


def test_next_update_flags_assigned_task_and_keeps_owner(session):
    task = session.create_task(POU, "check_access", "verify track from GI-660", notes="analyst note")
    assigned = session.store.assign(task["task_id"], "team_access_1")
    assert assigned["status"] == "assigned"
    result = session.next_update()
    assert result["accepted"] is True and result["advanced"] is True and result["sequence"] == 2
    assert task["task_id"] in result["affected_task_ids"]
    after = session.store.get(task["task_id"])
    assert after["assigned_team_id"] == "team_access_1" and after["status"] == "assigned"
    assert after["affected_by_snapshot_id"] == "synthetic_gavarres-0002"
    assert session.snapshot["sequence"] == 2 and not session.has_next
    # the intersecting asset now gets a suggested check_access task and the ranking follows the new exposure
    assert session.workbench.asset(POU)["intersects_fire"] is True


def test_third_next_update_reports_not_advanced(session):
    assert session.next_update()["advanced"] is True
    third = session.next_update()
    assert third["advanced"] is False and third["accepted"] is False
    assert "no further snapshot" in third["reason"]
    assert session.snapshot["sequence"] == 2


def test_assignment_error_surfaces_from_store(session):
    task = session.create_task(POU, "confirm_occupancy", "headcount")
    with pytest.raises(tasks.AssignmentError, match="capability mismatch"):
        session.store.assign(task["task_id"], "team_medical_1")


def test_tasks_survive_a_new_session_on_the_same_db(db):
    first = Session(db_path=db, clock=lambda: NOW)
    first.select_scenario(SYNTHETIC)
    task = first.create_task(POU, "contact_facility", "call the site")
    first.store.assign(task["task_id"], "team_bisbal_1")
    first.store.close()

    second = Session(db_path=db, clock=lambda: NOW)
    reload = second.select_scenario(SYNTHETIC)
    assert reload["accepted"] is False and "duplicate" in reload["reason"]     # store already holds seq 1
    kept = second.store.get(task["task_id"])
    assert kept["assigned_team_id"] == "team_bisbal_1" and kept["status"] == "assigned"
    assert second.scored["ranked"]
    # suggestions are not duplicated across sessions
    open_keys = [(t["asset_id"], t["action"], t["reason"]) for t in second.open_tasks()]
    assert len(open_keys) == len(set(open_keys))


def test_investigate_fake_then_confirm_moves_asset_to_ranked(session, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    record, label = session.investigate(POU, live=True)
    assert label == ui_state.FAKE_LABEL_NO_KEY and record["llm_mode"] == "fake"
    assert record["tool_calls"] and record["final_text"]
    pending = session.pending_proposals(POU)
    assert pending and pending[0]["field"] == "capacity"
    before_tasks = session.open_tasks(POU)
    assert before_tasks

    confirmed = session.confirm(pending[0]["proposal_id"])
    assert confirmed["status"] == "confirmed" and confirmed.get("override_id")
    assert session.store.overrides(POU)[0]["field"] == "capacity"
    asset = session.workbench.asset(POU)
    assert asset["queue"] == "ranked" and asset["priority_score"] is not None
    assert asset["score_components"]["size"]["proxy"] == "capacity as proxy"
    assert not session.pending_proposals(POU)
    assert any(e["proposal_id"] == pending[0]["proposal_id"]
               for t in session.open_tasks(POU) for e in t["evidence"])
    # the question is still open and can be answered manually
    qs = session.open_questions(POU)
    assert qs
    answered = session.answer(qs[0]["question_id"], qs[0]["options"][0])
    assert answered["status"] == "answered" and not session.open_questions(POU)


def test_reject_leaves_asset_untouched(session):
    session.investigate(POU, live=False)
    p = session.pending_proposals(POU)[0]
    session.reject(p["proposal_id"], "not this facility")
    assert session.workbench.asset(POU)["capacity"] is None
    assert session.store.overrides(POU) == []


def test_block_and_deadline(session):
    task = session.create_task(POU, "request_resources", "need a bus")
    blocked = session.block(task["task_id"], "how many buses?")
    assert blocked["status"] == "blocked" and blocked["blocking_questions"][0]["question"] == "how many buses?"
    with pytest.raises(ValueError):
        session.set_deadline(task["task_id"], "2026-07-03T12:00:00+00:00", "")
    updated = session.set_deadline(task["task_id"], "2026-07-03T12:00Z", "shift change")
    assert updated["deadline_at"].startswith("2026-07-03T12:00:00") and updated["deadline_basis"] == "shift change"


def test_status_keys_and_recomputed_freshness(session):
    s = session.status()
    for key in ("input_mode", "as_of", "computed_at", "data_status_recorded", "data_status_now", "source_age_s",
                "source_age_now_s", "processing_s", "fire_geometry_kind", "fire_source", "counts", "snapshot_id",
                "sequence", "n_sequences", "db_path", "policy_version"):
        assert key in s, key
    assert s["input_mode"] == "synthetic" and s["data_status_recorded"] == "current"
    assert s["data_status_now"] == "current" and s["source_age_now_s"] == 1800.0      # 30 min after observation
    assert s["fire_geometry_kind"] == "perimeter"
    c = s["counts"]
    assert c["assets"] == 13 and c["unlocated"] == 1
    assert c["ranked"] + c["needs_review"] == c["assets"]
    assert c["open_tasks"] >= 1 and c["pending_proposals"] == 0 and c["open_questions"] == 0

    stale = Session(db_path=":memory:", clock=lambda: datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc))
    stale.select_scenario(SYNTHETIC)
    assert stale.status()["data_status_now"] == "stale" and stale.status()["data_status_recorded"] == "current"


def test_unknown_scenario_and_next_before_select(db):
    s = Session(db_path=db)
    with pytest.raises(KeyError):
        s.select_scenario("nope")
    with pytest.raises(RuntimeError):
        s.next_update()
