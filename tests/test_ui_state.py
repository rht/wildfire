"""Session workflow (fireline/ui_state.py), no Streamlit. Ranking behaviour runs on a constructed
two-snapshot scenario with the v1.1 timing fields; the committed fixture snapshots are used for
discovery and for the fixture loop (which tolerates an empty ranked queue until the producer adds
fire_arrival_at / evacuation_min to them)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from fireline import config, tasks, ui_state
from fireline.ui_state import Session
from tests.helpers import AS_OF, make_asset, make_snapshot

SYNTHETIC = "synthetic_gavarres"
SCENARIO = "window_test"
POU = "fixture:pou_del_glac"          # occupancy_unknown; the FakeLLM finds its evidence page (capacity 120)
FAR = "fixture:far_downwind"
NEAR = "fixture:near"
NO_EVAC = "fixture:no_evacuation"
NOW = datetime(2026, 7, 3, 8, 30, tzinfo=timezone.utc)
BUFFER = config.CONTACT_POLICY["buffer_min"]
FORECAST = "fixture:test-spread (synthetic)"
EVAC = config.EVACUATION_POLICY["version"]


def timed(asset_id, arrival_at, evacuation_min, **kw):
    fields = dict(asset_id=asset_id, fire_arrival_at=arrival_at, fire_arrival_basis="p10" if arrival_at else None,
                  forecast_source=FORECAST if arrival_at else None,
                  forecast_horizon_at="2026-07-03T20:00:00+00:00" if arrival_at else None,
                  evacuation_min=evacuation_min, evacuation_source=EVAC if evacuation_min is not None else None)
    fields.update(kw)
    return make_asset(**fields)


def scenario_files(tmp_path):
    """Two snapshots: on the update the far asset's forecast arrival moves earlier and its window closes."""
    d = tmp_path / "snapshots"
    d.mkdir()
    s1 = make_snapshot([
        timed(NEAR, "2026-07-03T13:00:00+00:00", 90.0, name="Near school", distance_to_fire_m=800.0),
        timed(FAR, "2026-07-03T11:00:00+00:00", 90.0, name="Far camp", asset_type="camp", distance_to_fire_m=4000.0),
        timed(POU, "2026-07-03T12:00:00+00:00", 90.0, name="Pou del Glaç", asset_type="camp", capacity=None,
              occupancy_basis=None, municipality="la Bisbal d'Empordà", distance_to_fire_m=1762.0,
              review_reasons=["occupancy_unknown"]),
        timed(NO_EVAC, "2026-07-03T12:00:00+00:00", None, name="Unknown class", asset_type="unknown",
              value_score=None, value_basis=None, review_reasons=["class_ambiguous", "value_unknown", "evacuation_unknown"]),
    ], scenario_id=SCENARIO, sequence=1, fire_geometry_kind="perimeter",
        fire_geometry={"type": "Polygon", "coordinates": [[[3.0, 41.9], [3.01, 41.9], [3.01, 41.91], [3.0, 41.91], [3.0, 41.9]]]})
    s2 = json.loads(json.dumps(s1))
    s2.update(sequence=2, snapshot_id=f"{SCENARIO}-0002", as_of="2026-07-03T09:00:00+00:00",
              computed_at="2026-07-03T09:00:00+00:00", fire_observed_at="2026-07-03T09:00:00+00:00")
    for a in s2["assets"]:
        if a["asset_id"] == FAR:
            a["fire_arrival_at"] = "2026-07-03T10:30:00+00:00"        # 90 min: window = 90 - 90 - 30 < 0
            a["distance_to_fire_m"] = 3000.0
    (d / "window_test_0001.json").write_text(json.dumps(s1), encoding="utf-8")
    (d / "window_test_0002.json").write_text(json.dumps(s2), encoding="utf-8")
    return (d,)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "nested" / "fireline.sqlite"


@pytest.fixture
def dirs(tmp_path):
    return scenario_files(tmp_path)


@pytest.fixture
def session(db, dirs):
    s = Session(db_path=db, snapshot_dirs=dirs, clock=lambda: NOW)
    s.select_scenario(SCENARIO)
    return s


def test_discovery_groups_by_scenario_and_sorts_by_sequence():
    scenarios, warnings = ui_state.discover_snapshots()
    assert {SYNTHETIC, "gavarres_real"} <= set(scenarios)
    for entries in scenarios.values():
        seqs = [e["sequence"] for e in entries]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert all(e["as_of"] for e in entries), "the sequence control labels its steps with as_of"
    assert warnings == []


def test_real_scenario_schools_carry_enrolled_pupils():
    """The page's people column reads `estimated_occupancy` off the scored assets, so the real-area
    schools must arrive with the Gencat enrolment count (the register lists no music, dance or
    adult-education centres, which keep occupancy_unknown)."""
    session = Session(db_path=":memory:", clock=lambda: NOW)
    session.select_scenario("gavarres_real")
    schools = [a for a in session.assets_in_order() if a["asset_type"] == "school"]
    enrolled = [a for a in schools if a.get("estimated_occupancy") is not None]
    assert len(enrolled) >= 0.8 * len(schools) > 0
    assert all(a["estimated_occupancy"] > 0 and "enrolled pupils" in a["occupancy_basis"] for a in enrolled)


def test_select_scenario_ranks_by_window_and_suggests(session, db):
    assert db.exists()                                     # parent dir created, sqlite opened
    assert session.snapshot["sequence"] == 1 and session.index == 0 and session.has_next
    assert session.last_update["accepted"] is True and session.last_update["advanced"] is True
    ranked = [a["asset_id"] for a in session.scored["ranked"]]
    assert ranked == [FAR, POU, NEAR]                      # farther asset with the earlier arrival first
    far = session.asset(FAR)
    assert far["slack_min"] == 180.0 - 90.0 - BUFFER and far["priority_status"] == "window_open"
    assert [a["asset_id"] for a in session.scored["needs_review"]] == [NO_EVAC]
    assert session.asset(NO_EVAC)["priority_status"] == "needs_review"
    assert [a["asset_id"] for a in session.scored["flagged"]] == [POU]
    assert session.last_suggested, "review reasons produce suggested tasks"
    assert {t["action"] for t in session.open_tasks(POU)} == {"confirm_occupancy"}
    evac_tasks = [t for t in session.open_tasks(NO_EVAC) if t["action"] == "contact_facility"]
    assert any("evacuation duration" in t["reason"] for t in evac_tasks)
    assert session.workbench.snapshot is session.snapshot


def test_next_update_flags_assigned_task_and_keeps_owner(session):
    task = session.create_task(FAR, "check_access", "verify track from GI-660", notes="analyst note")
    assigned = session.store.assign(task["task_id"], "team_access_1")
    assert assigned["status"] == "assigned"
    other = session.create_task(NEAR, "check_access", "unchanged asset")
    result = session.next_update()
    assert result["accepted"] is True and result["advanced"] is True and result["sequence"] == 2
    assert task["task_id"] in result["affected_task_ids"] and other["task_id"] not in result["affected_task_ids"]
    change = next(c for c in result["changed"] if c["asset_id"] == FAR)
    assert change["changes"]["fire_arrival_at"] == ["2026-07-03T11:00:00+00:00", "2026-07-03T10:30:00+00:00"]
    assert change["changes"]["priority_status"] == ["window_open", "window_exhausted"]
    assert change["slack_min"] == [60.0, -30.0]
    after = session.store.get(task["task_id"])
    assert after["assigned_team_id"] == "team_access_1" and after["status"] == "assigned"
    assert after["affected_by_snapshot_id"] == f"{SCENARIO}-0002"
    assert session.snapshot["sequence"] == 2 and not session.has_next
    far = session.asset(FAR)
    assert far["priority_status"] == "window_exhausted" and far["priority_rank"] == 1 and far["slack_min"] == -30.0
    assert session.status()["counts"]["window_exhausted"] == 1
    events = [e for e in session.store.events() if e["kind"] == "task_affected" and e["task_id"] == task["task_id"]]
    assert events and "remaining window 60 min -> -30 min" in events[0]["message"]


def test_previous_update_views_an_earlier_snapshot_without_rewinding_the_store(session):
    """Going back in time re-ranks the earlier snapshot with the confirmed overrides and shows it; the
    store keeps its accepted sequence, its tasks and its change log (readme 9)."""
    task = session.create_task(FAR, "check_access", "verify track from GI-660")
    session.store.assign(task["task_id"], "team_access_1")
    session.next_update()
    assert session.asset(FAR)["priority_status"] == "window_exhausted" and session.applied_sequence == 2
    events_before, tasks_before = len(session.store.events()), len(session.store.tasks())

    back = session.previous_update()
    assert back["view_only"] is True and back["accepted"] is False and back["advanced"] is False
    assert back["sequence"] == 1 and session.index == 0 and session.reviewing_earlier is True
    assert session.snapshot["as_of"] == AS_OF                                  # the earlier moment is displayed
    far = session.asset(FAR)
    assert far["fire_arrival_at"] == "2026-07-03T11:00:00+00:00" and far["priority_status"] == "window_open"
    assert far["slack_min"] == 60.0                                            # ranking recomputed for that moment
    status = session.status()
    assert status["sequence"] == 1 and status["applied_sequence"] == 2 and status["reviewing_earlier"] is True
    assert session.applied_sequence == 2, "the store's high-water sequence never moves back"
    assert len(session.store.events()) == events_before and len(session.store.tasks()) == tasks_before
    assert session.store.get(task["task_id"])["affected_by_snapshot_id"] == f"{SCENARIO}-0002"
    assert session.last_suggested == [] and back["suggested_task_ids"] == []

    forward = session.next_update()            # returning is a view too: nothing is re-applied or re-flagged
    assert forward["view_only"] is True and session.index == 1 and not session.reviewing_earlier
    assert session.asset(FAR)["priority_status"] == "window_exhausted"
    assert len(session.store.events()) == events_before and len(session.store.tasks()) == tasks_before


def test_go_to_applies_only_past_the_stores_high_water_sequence(session):
    assert session.applied_sequence == 1 and not session.reviewing_earlier
    with pytest.raises(IndexError):
        session.go_to(5)
    assert session.previous_update()["reason"].startswith("already at the first snapshot")
    assert session.index == 0 and session.snapshot["sequence"] == 1
    applied = session.go_to(1)
    assert applied["accepted"] is True and applied["advanced"] is True and applied["view_only"] is False
    assert session.applied_sequence == 2 and not session.reviewing_earlier
    assert session.go_to(0)["view_only"] is True and session.reviewing_earlier is True


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


def test_tasks_and_overrides_survive_a_new_session_on_the_same_db(db, dirs):
    first = Session(db_path=db, snapshot_dirs=dirs, clock=lambda: NOW)
    first.select_scenario(SCENARIO)
    task = first.create_task(POU, "contact_facility", "call the site")
    first.store.assign(task["task_id"], "team_bisbal_1")
    first.set_evacuation(NO_EVAC, 120, "phone call with the owner")
    first.store.close()

    second = Session(db_path=db, snapshot_dirs=dirs, clock=lambda: NOW)
    reload = second.select_scenario(SCENARIO)
    # store already holds seq 1, so resuming shows it from the store instead of re-applying it
    assert reload["accepted"] is False and reload["view_only"] is True
    assert "already applied" in reload["reason"] and not second.reviewing_earlier
    kept = second.store.get(task["task_id"])
    assert kept["assigned_team_id"] == "team_bisbal_1" and kept["status"] == "assigned"
    assert second.asset(NO_EVAC)["queue"] == "ranked"                              # override re-applied on reload
    assert second.asset(NO_EVAC)["evacuation_source"] == "analyst override: phone call with the owner"
    open_keys = [(t["asset_id"], t["action"], t["reason"]) for t in second.open_tasks()]
    assert len(open_keys) == len(set(open_keys))


def test_set_evacuation_moves_asset_to_ranked_and_attaches_evidence(session):
    assert session.asset(NO_EVAC)["queue"] == "needs_review"
    with pytest.raises(ValueError, match="source"):
        session.set_evacuation(NO_EVAC, 120, "  ")
    with pytest.raises(ValueError):
        session.set_evacuation(NO_EVAC, "soon", "phone call")
    with pytest.raises(ValueError):
        session.set_evacuation(NO_EVAC, -5, "phone call")
    override = session.set_evacuation(NO_EVAC, 120, "phone call with the owner", snippet="two hours to clear the site",
                                      confidence="high")
    assert override["field"] == "evacuation_min" and override["value"] == 120.0 and override["previous"] is None
    a = session.asset(NO_EVAC)
    assert a["queue"] == "ranked" and a["evacuation_min"] == 120.0
    assert a["evacuation_source"] == "analyst override: phone call with the owner"
    assert a["slack_min"] == 240.0 - 120.0 - BUFFER and "evacuation_unknown" not in a["review_reasons"]
    assert "class_ambiguous" in a["review_reasons"]                                 # other flags stay
    assert [x["asset_id"] for x in session.scored["ranked"]] == [FAR, NO_EVAC, POU, NEAR]
    assert any(e["field"] == "evacuation_min" for t in session.open_tasks(NO_EVAC) for e in t["evidence"])
    assert session.status()["counts"]["needs_review"] == 0


def test_investigate_live_uses_the_provider_back_end_and_labels_its_model(session, monkeypatch):
    """With a key set, `live=True` runs the provider back-end; the label names the model the
    analyst's proposals came from."""
    from fireline import env
    from fireline import llm as llm_mod
    from fireline.llm import NebiusLLM

    monkeypatch.setattr(env, "_loaded", ["already-loaded"])
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    calls = []

    class Stub(NebiusLLM):
        def create(self, system, messages, tools):
            calls.append(messages)
            from fireline.llm import TextBlock, Response

            return Response([TextBlock("Recommendation, not an order. Nothing to add.")], "end_turn")

    monkeypatch.setattr(llm_mod, "live_llm", lambda: Stub(api_key="k"))
    record, label = session.investigate(POU, live=True)
    assert calls and record["llm_mode"] == "live"
    assert label == f"live ({llm_mod.DEFAULT_NEBIUS_MODEL})"
    assert ui_state.llm_available() is True


def test_investigate_fake_then_confirm_clears_flag_and_reranks(session):
    # conftest.no_llm_keys strips NEBIUS_API_KEY and ANTHROPIC_API_KEY, so live=True still falls back
    record, label = session.investigate(POU, live=True)
    assert label == ui_state.FAKE_LABEL_NO_KEY and record["llm_mode"] == "fake"
    assert record["tool_calls"] and record["final_text"]
    got = record["tool_calls"][0]["result"]
    assert got["slack_min"] == 120 and got["priority_status"] == "window_open" and got["priority_rank"] == 2
    assert "priority_score" not in got
    pending = session.pending_proposals(POU)
    assert pending and pending[0]["field"] == "capacity"
    assert session.open_tasks(POU)

    confirmed = session.confirm(pending[0]["proposal_id"])
    assert confirmed["status"] == "confirmed" and confirmed.get("override_id")
    assert session.store.overrides(POU)[0]["field"] == "capacity"
    asset = session.workbench.asset(POU)
    assert asset["capacity"] == 120 and asset["occupancy_basis"] == "analyst override"
    assert asset["queue"] == "ranked" and asset["slack_min"] == 120.0 and asset["priority_rank"] == 2
    assert not session.pending_proposals(POU)
    assert any(e["proposal_id"] == pending[0]["proposal_id"]
               for t in session.open_tasks(POU) for e in t["evidence"])
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
                "sequence", "n_sequences", "db_path", "policy_version", "buffer_min", "now_at",
                "evacuation_policy_version"):
        assert key in s, key
    assert s["policy_version"] == config.CONTACT_POLICY["version"] and s["buffer_min"] == BUFFER
    assert s["now_at"] == AS_OF and s["input_mode"] == "synthetic" and s["data_status_recorded"] == "current"
    assert s["data_status_now"] == "current" and s["source_age_now_s"] == 1800.0      # 30 min after observation
    c = s["counts"]
    assert c["assets"] == 4 and c["ranked"] == 3 and c["needs_review"] == 1 and c["window_exhausted"] == 0
    assert c["evacuation_unknown"] == 1 and c["forecast_unavailable"] == 0
    assert c["open_tasks"] >= 1 and c["pending_proposals"] == 0 and c["open_questions"] == 0


def test_fixture_loop_runs_with_or_without_producer_timing(db):
    """The committed fixtures: every asset lands in one queue; the ranked queue may be empty until the
    producer adds fire_arrival_at / evacuation_min (then it must be ordered by window)."""
    s = Session(db_path=db, clock=lambda: NOW)
    s.select_scenario(SYNTHETIC)
    st = s.status()
    c = st["counts"]
    assert c["assets"] == 13 and c["unlocated"] == 1 and c["ranked"] + c["needs_review"] == 13
    assert st["fire_geometry_kind"] == "perimeter"
    windows = [a["slack_min"] for a in s.scored["ranked"]]
    assert windows == sorted(windows)
    for a in s.scored["needs_review"]:
        assert {"forecast_unavailable", "evacuation_unknown"} & set(a["review_reasons"]) or \
            a["distance_to_fire_m"] is None
    assert s.open_tasks("fixture:pou_del_glac"), "occupancy_unknown suggests confirm_occupancy"
    assert s.next_update()["accepted"] is True and s.snapshot["sequence"] == 2
    stale = Session(db_path=":memory:", clock=lambda: datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc))
    stale.select_scenario(SYNTHETIC)
    assert stale.status()["data_status_now"] == "stale" and stale.status()["data_status_recorded"] == "current"


def test_unknown_scenario_and_next_before_select(db):
    s = Session(db_path=db)
    with pytest.raises(KeyError):
        s.select_scenario("nope")
    with pytest.raises(RuntimeError):
        s.next_update()


# --------------------------------------------------------------- value at risk (handoff 002)
def var_asset(asset_id, **kw):
    """A located asset carrying the eight value-at-risk keys, derived from the policy like the producer."""
    fields = dict(asset_id=asset_id, forecast_source=FORECAST, forecast_horizon_at="2026-07-03T20:00:00+00:00",
                  evacuation_min=90.0, evacuation_source=EVAC, capacity=None, value_at_risk=True)
    fields.update(kw)
    return make_asset(**fields)


def test_value_at_risk_totals_sum_located_assets_and_count_the_excluded():
    """Located assets only; a null field is left out of its sum and counted, never read as zero. The
    people and euro exclusions differ: occupancy_unknown loses the people, an unvalued class the euros."""
    assets = [
        var_asset("a:school", estimated_occupancy=200, burn_probability=0.5),          # school: 200 x 0.5
        var_asset("a:nucleus", asset_type="nucleus", estimated_occupancy=50, burn_probability=0.4),
        var_asset("a:no_occupancy", estimated_occupancy=None, burn_probability=0.5,
                  review_reasons=["occupancy_unknown"]),
        var_asset("a:unlocated", latitude=None, longitude=None, estimated_occupancy=999,
                  burn_probability=0.9, review_reasons=["location_unknown"]),
    ]
    v = ui_state.value_at_risk_totals(assets)
    assert v["layer"] is True and v["located"] == 3
    assert v["people_exposed"] == 200 * 0.5 + 50 * 0.4                 # the unlocated 999 is not summed
    assert v["excluded_people"] == 1 and v["excluded_eur"] == 1        # occupancy_unknown / nucleus
    assert v["expected_loss_eur_mid"] == 0.5 * 0.40 * 4_000_000 + 0.5 * 0.40 * 4_000_000
    assert v["expected_loss_eur_low"] < v["expected_loss_eur_mid"] < v["expected_loss_eur_high"]
    assert v["policy_version"] == config.VALUE_AT_RISK_POLICY["version"]


def test_value_at_risk_totals_say_the_layer_is_off_instead_of_reporting_zeros():
    v = ui_state.value_at_risk_totals([make_asset(), make_asset(asset_id="fixture:b")])
    assert v["layer"] is False and v["people_exposed"] == 0 and v["located"] == 2


def test_value_at_risk_totals_separate_a_forecast_that_reaches_nothing_from_a_missing_one():
    """burn_probability 0.0 on a covered asset is a statement: covered without reached, zeros not nulls."""
    reaches_nothing = [var_asset("a:school", estimated_occupancy=200, burn_probability=0.0)]
    v = ui_state.value_at_risk_totals(reaches_nothing)
    assert v["covered"] == 1 and v["reached"] == 0
    assert v["people_exposed"] == 0 and v["expected_loss_eur_mid"] == 0 and v["excluded_people"] == 0
    no_forecast = [var_asset("a:school", estimated_occupancy=200, burn_probability=None,
                             forecast_source=None, forecast_horizon_at=None)]
    w = ui_state.value_at_risk_totals(no_forecast)
    assert w["covered"] == 0 and w["reached"] == 0 and w["excluded_people"] == 1
    assert w["horizon_at"] is None and w["enrichment"] is False


def test_value_at_risk_totals_flag_the_uncalibrated_ca_enrichment():
    ca = var_asset("a:school", estimated_occupancy=10, burn_probability=0.9,
                   forecast_source="ca_ensemble (labelled enrichment, not validated)")
    assert ui_state.value_at_risk_totals([ca])["enrichment"] is True


def test_status_value_at_risk_totals_follow_a_confirmed_override(tmp_path, db):
    """The aggregate is taken over the assets the session shows, so a confirmed headcount moves it."""
    d = tmp_path / "var_snapshots"
    d.mkdir()
    snap = make_snapshot([var_asset("a:school", name="School", estimated_occupancy=100, burn_probability=0.5)],
                         scenario_id="var_test", sequence=1)
    (d / "var_0001.json").write_text(json.dumps(snap), encoding="utf-8")
    s = Session(db_path=db, snapshot_dirs=(d,), clock=lambda: NOW)
    s.select_scenario("var_test")
    assert s.status()["value_at_risk"]["people_exposed"] == 50.0
    s.store.confirm_override("a:school", "estimated_occupancy", 300, source="phone call",
                             snippet="director confirmed 300 pupils", confidence="high", previous=100)
    s.rescore()
    assert s.status()["value_at_risk"]["people_exposed"] == 150.0      # 300 x 0.5, re-derived by the override


VALUATION = {"method": "component_replacement",
             "components": [{"label": "computing installation", "amount_eur": 9_000_000},
                            {"label": "building shell", "amount_eur": 3_000_000}],
             "amount_eur_low": 8_000_000, "amount_eur_mid": 12_000_000, "amount_eur_high": 20_000_000,
             "note": "the machines dominate the shell"}


def test_a_confirmed_bespoke_valuation_leaves_excluded_eur_and_is_counted():
    """The four classes the class table cannot price are `excluded_eur` until an analyst confirms a
    bespoke figure; confirming one re-derives the euros from it, so the asset leaves that count and is
    reported separately - the totals say how much of them rests on a per-asset assumption."""
    from fireline import priority

    asset = var_asset("a:rf", asset_type="research_facility", estimated_occupancy=120,
                      burn_probability=0.4, review_reasons=["valuation_unassessed"])
    before = ui_state.value_at_risk_totals([asset])
    assert before["excluded_eur"] == 1 and before["custom_valued"] == 0
    assert before["expected_loss_eur_mid"] == 0          # nothing to sum: the class table prices no
                                                        # building of this class at all

    confirmed = priority.apply_overrides([asset], [{
        "asset_id": "a:rf", "field": "custom_valuation", "value": VALUATION, "source": "ca.wikipedia.org",
        "snippet": "x", "confidence": "medium", "confirmed_at": "2026-07-03T12:00:00+00:00"}],
        config, now_at=AS_OF)[0]
    after = ui_state.value_at_risk_totals([confirmed])
    assert after["excluded_eur"] == 0 and after["custom_valued"] == 1
    assert after["custom_valuation_policy_version"] == config.CUSTOM_VALUATION_POLICY["version"]
    # the loss band compounds the valuation band with the assumed damage band, not the damage band alone
    ratio = config.CUSTOM_VALUATION_POLICY["damage_ratio"]
    assert after["expected_loss_eur_low"] == round(0.4 * ratio["d_low"] * 8_000_000)
    assert after["expected_loss_eur_high"] == round(0.4 * ratio["d_high"] * 20_000_000)


def test_a_confirmed_not_valued_answer_is_not_counted_as_a_bespoke_figure():
    """`not_valued` records that the agent looked and found nothing: the review flag clears, no euro
    figure lands, and the asset stays in `excluded_eur` where it belongs."""
    from fireline import priority

    asset = var_asset("a:rf", asset_type="research_facility", estimated_occupancy=120,
                      burn_probability=0.4, review_reasons=["valuation_unassessed"])
    confirmed = priority.apply_overrides([asset], [{
        "asset_id": "a:rf", "field": "custom_valuation", "source": "s", "snippet": "x",
        "confidence": "low", "confirmed_at": "2026-07-03T12:00:00+00:00",
        "value": {"method": "not_valued"}}], config, now_at=AS_OF)[0]
    totals = ui_state.value_at_risk_totals([confirmed])
    assert totals["custom_valued"] == 0 and totals["excluded_eur"] == 1
    assert "valuation_unassessed" not in confirmed["review_reasons"]


def test_status_counts_the_assets_still_waiting_on_a_valuation(tmp_path, db):
    """The strategic caption names both unassessed counts, so the status has to carry both."""
    d = tmp_path / "val_snapshots"
    d.mkdir()
    snap = make_snapshot([var_asset("a:rf", name="Facility", asset_type="research_facility",
                                    estimated_occupancy=120, burn_probability=0.4,
                                    review_reasons=["valuation_unassessed", "criticality_unassessed"]),
                          var_asset("a:school", name="School", estimated_occupancy=100,
                                    burn_probability=0.5)],
                         scenario_id="val_test", sequence=1)
    (d / "val_0001.json").write_text(json.dumps(snap), encoding="utf-8")
    s = Session(db_path=db, snapshot_dirs=(d,), clock=lambda: NOW)
    s.select_scenario("val_test")
    assert s.status()["counts"]["valuation_unassessed"] == 1
    assert s.status()["value_at_risk"]["custom_valued"] == 0

    s.store.confirm_override("a:rf", "custom_valuation", VALUATION, source="ca.wikipedia.org",
                             snippet="el centre allotja el supercomputador", confidence="medium",
                             previous=None)
    s.rescore()
    assert s.status()["counts"]["valuation_unassessed"] == 0
    assert s.status()["value_at_risk"]["custom_valued"] == 1


COMMITTED_TOTALS = {
    "synthetic_gavarres-0001": (438, 0, 0, 6_519_500, 2_711_000, 12_268_000, 6, 6, 12),
    "synthetic_gavarres-0002": (505.8, 89, 89, 7_623_000, 3_169_000, 14_367_000, 6, 6, 12),
    # The four strategic classes (fire_station, university, research_facility, aerodrome) add 12 located
    # assets to the real extract: 99 -> 111. They carry no headcount and no assumed replacement value, so
    # every people and euro total is unchanged and the 12 only raise the excluded counts (12 -> 24 people,
    # 0 -> 12 euro), which is the "not valued" path config.VALUE_AT_RISK_POLICY documents for nucleus too.
    "gavarres_real-0001": (1866.7, 0, 0, 13_280_000, 4_980_000, 26_560_000, 24, 12, 111),
    "gavarres_real-0002": (6632.5, 0, 45, 43_390_000, 16_290_000, 86_780_000, 24, 12, 111),
    "gavarres_real-0003": (18433.9, 1680, 3040, 137_420_000, 51_720_000, 274_840_000, 24, 12, 111),
    "gavarres_real-0004": (0, 0, 0, 0, 0, 0, 24, 12, 111),
}


@pytest.mark.parametrize("scenario", [SYNTHETIC, "gavarres_real"])
def test_committed_fixtures_report_the_recorded_value_at_risk_totals(scenario):
    """The header totals of every committed snapshot, the same rule as make_snapshots.value_totals."""
    s = Session(db_path=":memory:", clock=lambda: NOW)
    s.select_scenario(scenario)
    seen = 0
    while True:
        v = s.status()["value_at_risk"]
        expected = COMMITTED_TOTALS[s.snapshot["snapshot_id"]]
        assert v["layer"] is True
        assert (v["people_exposed"], v["people_at_risk_p50"], v["people_at_risk_p10"],
                v["expected_loss_eur_mid"], v["expected_loss_eur_low"], v["expected_loss_eur_high"],
                v["excluded_people"], v["excluded_eur"], v["located"]) == expected
        seen += 1
        if not s.has_next:
            break
        s.next_update()
    assert seen == len([e for e in s.sequence_entries])
