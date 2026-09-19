"""Task store (CONTRACTS 5, readme 7 and 11): suggestions, roster checks, status flow, snapshot
bookkeeping that preserves assignments, overrides, persistence across reopen, change log."""

from pathlib import Path

import pytest

from fireline import config
from fireline.priority import rank_snapshot
from fireline.tasks import EVACUATION_TASK_REASON, AssignmentError, TaskStore
from tests.helpers import AS_OF, make_asset, make_snapshot

FORECAST = "fixture:test-spread (synthetic)"
EVAC = config.EVACUATION_POLICY["version"]


def timed(asset_id, arrival_at, evacuation_min=90.0, **kw):
    """Asset with the v1.1 timing fields (arrival ISO timestamp or None)."""
    fields = dict(asset_id=asset_id, fire_arrival_at=arrival_at, fire_arrival_basis="p10" if arrival_at else None,
                  forecast_source=FORECAST if arrival_at else None, evacuation_min=evacuation_min,
                  evacuation_source=EVAC if evacuation_min is not None else None)
    fields.update(kw)
    return make_asset(**fields)

ROSTER = Path(__file__).resolve().parents[1] / "fixtures" / "teams.json"
OUTREACH = "team_bisbal_1"          # occupancy_check, facility_contact
ACCESS = "team_access_1"            # access_check, transport
LOGISTICS = "team_logistics_1"      # resource_request
MEDICAL = "team_medical_1"          # medical
OFF_SHIFT = "team_palafrugell_1"    # available: false


@pytest.fixture
def store():
    s = TaskStore(":memory:")
    s.load_roster(ROSTER)
    return s


def scored_assets(assets):
    return rank_snapshot(make_snapshot(assets))["all"]


# -- roster ------------------------------------------------------------------------------------

def test_roster_fixture_shape():
    teams = TaskStore(":memory:").load_roster(ROSTER)
    assert len(teams) == 5
    tags = {"occupancy_check", "facility_contact", "access_check", "resource_request", "medical", "transport"}
    for t in teams:
        assert set(t) == {"team_id", "name", "capabilities", "available", "source"}
        assert set(t["capabilities"]) <= tags and t["source"] == "fixture"
    assert sum(not t["available"] for t in teams) == 1
    caps = {t["team_id"]: t["capabilities"] for t in teams}
    assert caps[LOGISTICS] == ["resource_request"]
    assert caps[MEDICAL] == ["medical"]


# -- suggestions -------------------------------------------------------------------------------

def test_suggestions_from_review_reasons_and_dedupe(store):
    arrival = "2026-07-03T11:00:00+00:00"
    assets = [
        timed("fixture:occ", arrival, capacity=None, review_reasons=["occupancy_unknown"]),
        timed("fixture:cls", arrival, asset_type="unknown", value_score=None,
              review_reasons=["class_ambiguous", "value_unknown"]),
        timed("fixture:loc", arrival, latitude=None, longitude=None, distance_to_fire_m=None,
              intersects_fire=None, review_reasons=["location_unknown", "exposure_unknown"]),
        timed("fixture:exp", arrival, distance_to_fire_m=None, intersects_fire=None, review_reasons=["exposure_unknown"]),
        timed("fixture:hot", arrival, distance_to_fire_m=0.0, intersects_fire=True),
        timed("fixture:fine", arrival),
        timed("fixture:noevac", arrival, evacuation_min=None, review_reasons=["evacuation_unknown"]),
        timed("fixture:nofc", None),                     # forecast_unavailable added by the ranking: no task
    ]
    scored = scored_assets(assets)
    assert next(a for a in scored if a["asset_id"] == "fixture:nofc")["review_reasons"] == ["forecast_unavailable"]
    created = store.suggest_tasks(scored, "test-0001")
    by_asset = {(t["asset_id"], t["action"]): t for t in created}
    assert set(by_asset) == {
        ("fixture:occ", "confirm_occupancy"), ("fixture:cls", "contact_facility"),
        ("fixture:loc", "contact_facility"), ("fixture:exp", "contact_facility"), ("fixture:hot", "check_access"),
        ("fixture:noevac", "contact_facility")}
    assert by_asset[("fixture:noevac", "contact_facility")]["reason"] == EVACUATION_TASK_REASON
    assert "confirmed with the facility" in EVACUATION_TASK_REASON
    assert not any("forecast" in t["reason"] for t in created)
    # an untimed asset (no evacuation estimate at all) gets the same contact_facility suggestion
    plain = store.suggest_tasks(scored_assets([make_asset(asset_id="fixture:plain")]), "test-0001")
    assert [(t["asset_id"], t["action"], t["reason"]) for t in plain] == \
        [("fixture:plain", "contact_facility", EVACUATION_TASK_REASON)]
    store.set_status(plain[0]["task_id"], "done")
    assert by_asset[("fixture:occ", "confirm_occupancy")]["reason"] == "occupancy unknown"
    assert by_asset[("fixture:cls", "contact_facility")]["reason"].startswith("class ambiguous: ")
    assert by_asset[("fixture:loc", "contact_facility")]["reason"] == "location unknown"
    assert by_asset[("fixture:exp", "contact_facility")]["reason"] == "exposure unknown"
    assert by_asset[("fixture:hot", "check_access")]["reason"] == "facility intersects fire footprint"
    for t in created:
        assert t["suggested"] is True and t["status"] == "open" and t["assigned_team_id"] is None
        assert t["required_capabilities"] == config.TASK_ACTIONS[t["action"]]
        assert t["based_on_snapshot_id"] == "test-0001" and t["affected_by_snapshot_id"] is None
    # second call and a later snapshot create nothing new
    assert store.suggest_tasks(scored_assets(assets), "test-0001") == []
    assert store.suggest_tasks(scored_assets(assets), "test-0002") == []
    assert len(store.tasks()) == 7
    # once done, a persisting gap is suggested again (analyst closed it; new snapshot still flags it)
    store.set_status(by_asset[("fixture:occ", "confirm_occupancy")]["task_id"], "done")
    again = store.suggest_tasks(scored_assets(assets), "test-0003")
    assert [(t["asset_id"], t["action"]) for t in again] == [("fixture:occ", "confirm_occupancy")]


# -- assignment ---------------------------------------------------------------------------------

def test_create_and_assign_capable_available_team(store):
    task = store.create_task("fixture:a", "confirm_occupancy", "check headcount", snapshot_id="test-0001")
    assert task["status"] == "open" and task["suggested"] is False
    assert task["required_capabilities"] == ["occupancy_check"]
    task = store.assign(task["task_id"], OUTREACH)
    assert task["status"] == "assigned" and task["assigned_team_id"] == OUTREACH
    assert any(e["kind"] == "task_assigned" and e["task_id"] == task["task_id"] and OUTREACH in e["message"]
               for e in store.events())


def test_busy_team_rejected_with_explanation(store):
    t1 = store.create_task("fixture:a", "confirm_occupancy", "r1", snapshot_id="s")
    t2 = store.create_task("fixture:b", "confirm_occupancy", "r2", snapshot_id="s")
    store.assign(t1["task_id"], OUTREACH)
    with pytest.raises(AssignmentError, match=f"team busy: {OUTREACH} has active task {t1['task_id']}"):
        store.assign(t2["task_id"], OUTREACH)
    assert store.get(t2["task_id"])["status"] == "open"


def test_capability_mismatch_rejected(store):
    t = store.create_task("fixture:a", "check_access", "road blocked?", snapshot_id="s")
    with pytest.raises(AssignmentError, match=r"capability mismatch: task needs \['access_check'\]"):
        store.assign(t["task_id"], OUTREACH)
    with pytest.raises(AssignmentError, match="capability mismatch"):
        store.assign(t["task_id"], MEDICAL)
    assert store.assign(t["task_id"], ACCESS)["assigned_team_id"] == ACCESS


def test_unavailable_team_rejected(store):
    t = store.create_task("fixture:a", "confirm_occupancy", "r", snapshot_id="s")
    with pytest.raises(AssignmentError, match=f"team unavailable: {OFF_SHIFT}"):
        store.assign(t["task_id"], OFF_SHIFT)


def test_unknown_team_and_action(store):
    t = store.create_task("fixture:a", "confirm_occupancy", "r", snapshot_id="s")
    with pytest.raises(AssignmentError, match="unknown team"):
        store.assign(t["task_id"], "team_nope")
    with pytest.raises(ValueError):
        store.create_task("fixture:a", "evacuate", "r", snapshot_id="s")


def test_blocked_task_keeps_team_reserved_until_release(store):
    t1 = store.create_task("fixture:a", "contact_facility", "r1", snapshot_id="s")
    t2 = store.create_task("fixture:b", "contact_facility", "r2", snapshot_id="s")
    store.assign(t1["task_id"], OUTREACH)
    blocked = store.set_status(t1["task_id"], "blocked", note="no answer on the phone")
    assert blocked["status"] == "blocked" and blocked["assigned_team_id"] == OUTREACH
    assert "no answer on the phone" in blocked["notes"]
    with pytest.raises(AssignmentError, match="team busy"):
        store.assign(t2["task_id"], OUTREACH)
    released = store.release(t1["task_id"])
    assert released["status"] == "open" and released["assigned_team_id"] is None
    assert store.assign(t2["task_id"], OUTREACH)["status"] == "assigned"


def test_request_resources_may_stay_unassigned_and_blocked(store):
    t = store.create_task("fixture:a", "request_resources", "need two buses", snapshot_id="s")
    t = store.set_status(t["task_id"], "blocked", note="no transport available")
    assert t["status"] == "blocked" and t["assigned_team_id"] is None
    assert store.tasks(status="blocked")[0]["task_id"] == t["task_id"]


# -- status flow --------------------------------------------------------------------------------

def test_status_flow_and_reopen(store):
    t = store.create_task("fixture:a", "confirm_occupancy", "r", snapshot_id="s")
    tid = t["task_id"]
    with pytest.raises(ValueError):
        store.set_status(tid, "assigned")            # needs a team
    with pytest.raises(ValueError):
        store.set_status(tid, "cancelled")
    store.assign(tid, OUTREACH)
    assert store.set_status(tid, "in_progress")["status"] == "in_progress"
    assert store.active_task_for_team(OUTREACH)["task_id"] == tid
    done = store.set_status(tid, "done", note="confirmed 35 residents")
    assert done["status"] == "done" and done["assigned_team_id"] == OUTREACH
    assert store.active_task_for_team(OUTREACH) is None      # done frees the team
    reopened = store.set_status(tid, "open")
    assert reopened["status"] == "open" and reopened["assigned_team_id"] is None
    kinds = [e["kind"] for e in store.events()]
    assert "task_done" in kinds and "task_reopened" in kinds


def test_questions_evidence_and_deadline(store):
    t = store.create_task("fixture:a", "contact_facility", "r", snapshot_id="s")
    tid = t["task_id"]
    t = store.add_question(tid, "Is the summer camp running this week?")
    assert t["blocking_questions"] == [{"question": "Is the summer camp running this week?", "answer": None}]
    t = store.answer_question(tid, 0, "yes, 60 children")
    assert t["blocking_questions"][0]["answer"] == "yes, 60 children"
    t = store.add_evidence(tid, {"url": "https://example.org", "snippet": "60 places"})
    assert t["evidence"] == [{"url": "https://example.org", "snippet": "60 places"}]
    t = store.set_deadline(tid, "2026-07-03T12:00:00+00:00", "analyst: before afternoon wind shift")
    assert t["deadline_at"] == "2026-07-03T12:00:00+00:00"
    assert t["deadline_basis"] == "analyst: before afternoon wind shift"


# -- snapshots -----------------------------------------------------------------------------------

def test_apply_snapshot_sequence_and_flagging(store):
    a = make_asset(asset_id="fixture:a", distance_to_fire_m=2000.0)
    b = make_asset(asset_id="fixture:b", distance_to_fire_m=3000.0)
    c = make_asset(asset_id="fixture:c", distance_to_fire_m=4000.0)
    s1 = make_snapshot([a, b, c], scenario_id="sc", sequence=1)
    r1 = store.apply_snapshot(s1)
    assert r1 == {"accepted": True, "affected_task_ids": [], "missing_asset_ids": [], "changed": []}

    ta = store.create_task("fixture:a", "confirm_occupancy", "ra", snapshot_id=s1["snapshot_id"])
    tb = store.create_task("fixture:b", "contact_facility", "rb", snapshot_id=s1["snapshot_id"])
    tc = store.create_task("fixture:c", "check_access", "rc", snapshot_id=s1["snapshot_id"])
    store.assign(ta["task_id"], OUTREACH)
    store.set_status(ta["task_id"], "in_progress")
    store.assign(tc["task_id"], ACCESS)

    assert store.apply_snapshot(s1)["accepted"] is False                       # duplicate id
    assert store.apply_snapshot(make_snapshot([a, b, c], scenario_id="sc", sequence=1,
                                              snapshot_id="sc-again"))["accepted"] is False   # seq <= last
    assert store.apply_snapshot(make_snapshot([a, b, c], scenario_id="sc", sequence=0,
                                              snapshot_id="sc-zero"))["accepted"] is False
    assert store.last_sequence("sc") == {"sequence": 1, "snapshot_id": "sc-0001"}

    a2 = make_asset(asset_id="fixture:a", distance_to_fire_m=500.0)               # moved closer
    c2 = make_asset(asset_id="fixture:c", distance_to_fire_m=4000.0)              # unchanged; b missing
    s2 = make_snapshot([a2, c2], scenario_id="sc", sequence=2)
    r2 = store.apply_snapshot(s2)
    assert r2["accepted"] is True
    assert set(r2["affected_task_ids"]) == {ta["task_id"], tb["task_id"]}
    assert r2["missing_asset_ids"] == ["fixture:b"]
    assert [c["asset_id"] for c in r2["changed"]] == ["fixture:a"]
    assert r2["changed"][0]["changes"]["distance_to_fire_m"] == [2000.0, 500.0]

    ta2, tb2, tc2 = (store.get(t["task_id"]) for t in (ta, tb, tc))
    assert ta2["affected_by_snapshot_id"] == "sc-0002"
    assert ta2["status"] == "in_progress" and ta2["assigned_team_id"] == OUTREACH   # owner and status kept
    assert tb2["affected_by_snapshot_id"] == "sc-0002" and tb2["status"] == "open"  # kept, flagged
    assert tc2["affected_by_snapshot_id"] is None and tc2["assigned_team_id"] == ACCESS
    assert any(e["kind"] == "asset_missing" and e["asset_id"] == "fixture:b" for e in store.events())
    assert store.exposure("sc")["fixture:b"]["present"] is False
    assert len(store.snapshots("sc")) == 2


def test_apply_snapshot_flags_window_changes_and_names_the_window(store):
    arrive = lambda h, m=0: f"2026-07-03T{h:02d}:{m:02d}:00+00:00"  # noqa: E731
    a = timed("fixture:a", arrive(11))          # 180 - 90 - 30 = 60 min window
    b = timed("fixture:b", arrive(13))          # unchanged in seq 2 apart from the elapsed time (stays "open")
    c = timed("fixture:c", None)                # no forecast: needs_review
    store.apply_snapshot(make_snapshot([a, b, c], scenario_id="sc", sequence=1))
    ta = store.create_task("fixture:a", "contact_facility", "ra", snapshot_id="sc-0001")
    tb = store.create_task("fixture:b", "contact_facility", "rb", snapshot_id="sc-0001")
    tc = store.create_task("fixture:c", "contact_facility", "rc", snapshot_id="sc-0001")
    exp = store.exposure("sc")
    assert exp["fixture:a"]["priority_status"] == "window_open" and exp["fixture:a"]["slack_min"] == 60.0
    assert exp["fixture:c"]["priority_status"] == "needs_review" and exp["fixture:c"]["slack_min"] is None
    # seq 2, one hour later: a's arrival moves earlier (window exhausted); b only loses elapsed time (not a
    # flagged change, the window shrinks for everyone); c gains a forecast (needs_review -> ranked)
    a2 = timed("fixture:a", arrive(10))         # at 09:00: 60 - 90 - 30 = -60
    c2 = timed("fixture:c", arrive(14))
    r = store.apply_snapshot(make_snapshot([a2, b, c2], scenario_id="sc", sequence=2, as_of=arrive(9)))
    changes = {ch["asset_id"]: ch for ch in r["changed"]}
    assert set(changes) == {"fixture:a", "fixture:c"}
    assert changes["fixture:a"]["changes"]["fire_arrival_at"] == [arrive(11), arrive(10)]
    assert changes["fixture:a"]["changes"]["priority_status"] == ["window_open", "window_exhausted"]
    assert changes["fixture:a"]["slack_min"] == [60.0, -60.0]
    assert changes["fixture:c"]["changes"]["priority_status"] == ["needs_review", "window_open"]
    assert changes["fixture:c"]["slack_min"] == [None, 180.0]                     # 14:00 at 09:00: 300 - 90 - 30
    assert set(r["affected_task_ids"]) == {ta["task_id"], tc["task_id"]}
    assert store.get(tb["task_id"])["affected_by_snapshot_id"] is None
    msg = next(e["message"] for e in store.events() if e["kind"] == "task_affected" and e["task_id"] == ta["task_id"])
    assert "remaining window 60 min -> -60 min" in msg and "score" not in msg
    assert store.exposure("sc")["fixture:b"]["slack_min"] == 120.0           # bookkeeping follows the new as_of
    assert store.exposure("sc")["fixture:c"]["priority_status"] == "window_open"


def test_apply_snapshot_status_uses_confirmed_evacuation_override(store):
    a = timed("fixture:a", "2026-07-03T11:00:00+00:00", evacuation_min=None)
    store.apply_snapshot(make_snapshot([a], scenario_id="sc", sequence=1))
    assert store.exposure("sc")["fixture:a"]["priority_status"] == "needs_review"
    store.confirm_override("fixture:a", "evacuation_min", 100, source="phone call", snippet="100 min", confidence=0.7)
    store.apply_snapshot(make_snapshot([a], scenario_id="sc", sequence=2))
    row = store.exposure("sc")["fixture:a"]
    assert row["priority_status"] == "window_open" and row["slack_min"] == 180.0 - 100.0 - 30.0


def test_apply_snapshot_flags_needs_review_and_intersection_changes(store):
    a = make_asset(asset_id="fixture:a")
    store.apply_snapshot(make_snapshot([a], scenario_id="sc", sequence=1))
    t = store.create_task("fixture:a", "confirm_occupancy", "r", snapshot_id="sc-0001")
    a2 = make_asset(asset_id="fixture:a", distance_to_fire_m=0.0, intersects_fire=True,
                    review_reasons=["occupancy_seasonal"])
    r = store.apply_snapshot(make_snapshot([a2], scenario_id="sc", sequence=2))
    changes = r["changed"][0]["changes"]
    assert set(changes) == {"distance_to_fire_m", "intersects_fire", "needs_review"}   # status stays needs_review
    assert r["affected_task_ids"] == [t["task_id"]]
    # a done task is not flagged
    store.set_status(t["task_id"], "done")
    r3 = store.apply_snapshot(make_snapshot([make_asset(asset_id="fixture:a")], scenario_id="sc", sequence=3))
    assert r3["affected_task_ids"] == []


# -- overrides -----------------------------------------------------------------------------------

def test_confirm_override_persisted_and_returned(store):
    o = store.confirm_override("fixture:a", "estimated_occupancy", 40, source="phone call", snippet="40 on site",
                               url="https://example.org", observed_at="2026-07-03T08:45:00+00:00",
                               confidence=0.8, proposal_id="prop-1", previous=None)
    keys = {"override_id", "asset_id", "field", "value", "previous", "source", "snippet", "url", "observed_at",
            "confidence", "confirmed_at", "proposal_id"}
    assert set(o) == keys
    assert o["value"] == 40 and o["confidence"] == 0.8 and o["confirmed_at"]
    o2 = store.confirm_override("fixture:a", "asset_type", "care_home", source="register", snippet="residència",
                                confidence=0.9, previous="unknown")
    o3 = store.confirm_override("fixture:a", "evacuation_min", 150, source="phone call with director",
                                snippet="about two and a half hours", confidence=0.8, previous=None)
    assert o3["value"] == 150.0 and o3["field"] == "evacuation_min"
    assert [x["override_id"] for x in store.overrides("fixture:a")] == [o["override_id"], o2["override_id"], o3["override_id"]]
    assert store.overrides("fixture:zzz") == []
    assert len(store.overrides()) == 3
    with pytest.raises(ValueError, match="fire_arrival_at"):
        store.confirm_override("fixture:a", "fire_arrival_at", "2026-07-03T12:00:00+00:00", source="guess",
                               snippet="", confidence=0.1)
    with pytest.raises(ValueError):
        store.confirm_override("fixture:a", "evacuation_min", -5, source="s", snippet="", confidence=0.1)
    with pytest.raises(ValueError):
        store.confirm_override("fixture:a", "evacuation_min", 30, source="", snippet="", confidence=0.1)
    assert len(store.overrides()) == 3
    assert any(e["kind"] == "override_confirmed" and e["asset_id"] == "fixture:a" for e in store.events())


# -- persistence ---------------------------------------------------------------------------------

def test_store_migrates_an_older_exposure_table(tmp_path):
    import sqlite3

    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE asset_exposure (scenario_id TEXT NOT NULL, asset_id TEXT NOT NULL, distance_to_fire_m REAL, "
                 "intersects_fire INTEGER, needs_review INTEGER, review_reasons TEXT NOT NULL, snapshot_id TEXT, "
                 "present INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (scenario_id, asset_id))")
    conn.execute("INSERT INTO asset_exposure VALUES ('sc', 'fixture:a', 100.0, 0, 0, '[]', 'sc-0000', 1)")
    conn.commit()
    conn.close()
    store = TaskStore(db)
    row = store.exposure("sc")["fixture:a"]
    assert row["priority_status"] is None and row["slack_min"] is None and row["fire_arrival_at"] is None
    r = store.apply_snapshot(make_snapshot([timed("fixture:a", "2026-07-03T11:00:00+00:00")], scenario_id="sc", sequence=1))
    assert r["accepted"] and r["changed"][0]["changes"]["priority_status"] == [None, "window_open"]


def test_store_survives_reopen(tmp_path):
    db = tmp_path / "fireline.sqlite"
    s1 = make_snapshot([make_asset(asset_id="fixture:a")], scenario_id="sc", sequence=1)
    s2 = make_snapshot([make_asset(asset_id="fixture:a", distance_to_fire_m=100.0)], scenario_id="sc", sequence=2)

    first = TaskStore(db)
    first.load_roster(ROSTER)
    assert first.apply_snapshot(s1)["accepted"] is True
    t = first.create_task("fixture:a", "confirm_occupancy", "r", snapshot_id="sc-0001")
    first.assign(t["task_id"], OUTREACH)
    first.confirm_override("fixture:a", "capacity", 120, source="register", snippet="120 places", confidence=0.7)
    first.confirm_override("fixture:a", "evacuation_min", 75, source="director", snippet="75 min", confidence=0.7)
    assert first.apply_snapshot(s2)["accepted"] is True
    first.close()

    second = TaskStore(db)
    task = second.get(t["task_id"])
    assert task["status"] == "assigned" and task["assigned_team_id"] == OUTREACH
    assert task["affected_by_snapshot_id"] == "sc-0002"
    assert second.last_sequence("sc") == {"sequence": 2, "snapshot_id": "sc-0002"}
    assert second.apply_snapshot(s2)["accepted"] is False                      # replayed after reload
    assert second.apply_snapshot(s1)["accepted"] is False
    assert second.apply_snapshot(make_snapshot([make_asset(asset_id="fixture:a")],
                                               scenario_id="sc", sequence=3))["accepted"] is True
    assert [o["value"] for o in second.overrides("fixture:a")] == [120, 75.0]
    assert [tm["team_id"] for tm in second.teams()]                              # roster persisted
    with pytest.raises(AssignmentError, match="team busy"):
        second.assign(second.create_task("fixture:b", "confirm_occupancy", "r", snapshot_id="sc-0003")["task_id"],
                      OUTREACH)
    kinds = [e["kind"] for e in second.events()]
    assert "task_assigned" in kinds and "snapshot_rejected" in kinds and "snapshot_accepted" in kinds
    assert len(second.events(limit=2)) == 2 and second.events(limit=2)[-1] == second.events()[-1]


def test_apply_snapshot_flags_when_the_window_bucket_changes(store):
    a = timed("fixture:a", "2026-07-03T12:00:00+00:00")       # at 08:00: 240 - 90 - 30 = 120 min (open)
    store.apply_snapshot(make_snapshot([a], scenario_id="sc", sequence=1))
    t = store.create_task("fixture:a", "contact_facility", "r", snapshot_id="sc-0001")
    r = store.apply_snapshot(make_snapshot([a], scenario_id="sc", sequence=2, as_of="2026-07-03T08:30:00+00:00"))
    assert r["affected_task_ids"] == [] and r["changed"] == []            # 90 min: still open, only time elapsed
    r = store.apply_snapshot(make_snapshot([a], scenario_id="sc", sequence=3, as_of="2026-07-03T09:30:00+00:00"))
    assert r["affected_task_ids"] == [t["task_id"]]                       # 30 min: small window, flagged
    assert r["changed"][0]["changes"]["window_bucket"] == ["open", "small"] and r["changed"][0]["slack_min"] == [90.0, 30.0]


def test_confirm_override_rejects_an_infinite_evacuation_duration(store):
    with pytest.raises(ValueError, match="finite"):
        store.confirm_override("fixture:a", "evacuation_min", float("inf"), source="s", snippet="", confidence=0.5)


def test_one_malformed_asset_does_not_blank_the_other_windows(store):
    a = timed("fixture:a", "2026-07-03T11:00:00+00:00")
    bad = timed("fixture:bad", "2026-07-03T11:00:00+00:00", evacuation_min="abc")
    store.apply_snapshot(make_snapshot([a, bad], scenario_id="sc", sequence=1))
    exp = store.exposure("sc")
    assert exp["fixture:a"]["priority_status"] == "window_open" and exp["fixture:bad"]["priority_status"] is None


def test_id_queries_are_limited_to_task_and_override_tables(store):
    assert store._next_id("tasks", "task") == "task-0001"
    assert store._next_id("overrides", "ovr") == "ovr-0001"
    with pytest.raises(ValueError, match="Unsupported ID table"):
        store._next_id("events", "event")
    with pytest.raises(ValueError, match="Unsupported ID table"):
        store._next_id("tasks; DROP TABLE tasks", "task")
    assert store.create_task("fixture:a", "confirm_occupancy", "r", snapshot_id=None)["task_id"] == "task-0001"
    assert store.create_task("fixture:b", "confirm_occupancy", "r", snapshot_id=None)["task_id"] == "task-0002"
