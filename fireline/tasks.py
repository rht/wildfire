"""SQLite task store: tasks, roster, confirmed overrides, snapshot bookkeeping and the change log.

CONTRACTS.md section 5 / readme.md section 7. Tasks and confirmed overrides persist separately from
incoming exposure: `apply_snapshot` refreshes the per-asset exposure bookkeeping and flags affected
work, but never changes owners or status. Only the analyst marks a task done or reopens it.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fireline import config, priority
from fireline.priority import SnapshotSequence

STATUSES = ("open", "assigned", "in_progress", "blocked", "done")
EVACUATION_UNKNOWN = priority.EVACUATION_UNKNOWN
EVACUATION_TASK_REASON = ("evacuation duration unknown: the total evacuation duration must be confirmed with the "
                          "facility (mobilisation, preparation/loading, movement)")
ACTIVE_STATUSES = ("assigned", "in_progress", "blocked")   # a team holding one of these is busy
ACTIONS = tuple(config.TASK_ACTIONS)

TASK_COLUMNS = (
    "task_id", "asset_id", "action", "reason", "status", "required_capabilities", "assigned_team_id",
    "deadline_at", "deadline_basis", "blocking_questions", "evidence", "notes", "created_at",
    "updated_at", "based_on_snapshot_id", "affected_by_snapshot_id", "suggested",
)
TASK_JSON = ("required_capabilities", "blocking_questions", "evidence")
OVERRIDE_COLUMNS = (
    "override_id", "asset_id", "field", "value", "previous", "source", "snippet", "url",
    "observed_at", "confidence", "confirmed_at", "proposal_id",
)
OVERRIDE_JSON = ("value", "previous")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    asset_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    required_capabilities TEXT NOT NULL,
    assigned_team_id TEXT,
    deadline_at TEXT,
    deadline_basis TEXT,
    blocking_questions TEXT NOT NULL,
    evidence TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    based_on_snapshot_id TEXT,
    affected_by_snapshot_id TEXT,
    suggested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    available INTEGER NOT NULL,
    source TEXT
);
CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    override_id TEXT UNIQUE NOT NULL,
    asset_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT,
    previous TEXT,
    source TEXT,
    snippet TEXT,
    url TEXT,
    observed_at TEXT,
    confidence REAL,
    confirmed_at TEXT NOT NULL,
    proposal_id TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    as_of TEXT,
    computed_at TEXT,
    input_mode TEXT,
    data_status TEXT,
    accepted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenario_sequence (
    scenario_id TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL,
    last_snapshot_id TEXT
);
CREATE TABLE IF NOT EXISTS asset_exposure (
    scenario_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    distance_to_fire_m REAL,
    intersects_fire INTEGER,
    needs_review INTEGER,
    review_reasons TEXT NOT NULL,
    fire_arrival_at TEXT,
    priority_status TEXT,
    slack_min REAL,
    snapshot_id TEXT,
    present INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (scenario_id, asset_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    asset_id TEXT,
    task_id TEXT,
    message TEXT NOT NULL
);
"""


class AssignmentError(ValueError):
    """Raised when a team cannot take a task; the message is the explanation shown to the analyst."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return str(value)


def _fmt_window(value) -> str:
    return "unranked" if value is None else f"{float(value):.0f} min"


def _row_to_task(row: sqlite3.Row) -> dict:
    task = {k: row[k] for k in TASK_COLUMNS}
    for k in TASK_JSON:
        task[k] = json.loads(task[k]) if task[k] is not None else []
    task["suggested"] = bool(task["suggested"])
    return task


def _row_to_override(row: sqlite3.Row) -> dict:
    o = {k: row[k] for k in OVERRIDE_COLUMNS}
    for k in OVERRIDE_JSON:
        o[k] = json.loads(o[k]) if o[k] is not None else None
    return o


def _row_to_team(row: sqlite3.Row) -> dict:
    return {"team_id": row["team_id"], "name": row["name"], "capabilities": json.loads(row["capabilities"]),
            "available": bool(row["available"]), "source": row["source"]}


class TaskStore:
    """Persistent coordination state. `path` is ":memory:" or a sqlite file; schema created on open."""

    def __init__(self, path=":memory:", clock=None, cfg=config):
        self.path = str(path)
        self.cfg = cfg
        self._clock = clock or _utcnow
        # check_same_thread=False: Streamlit reruns on different threads; the store is used from one at a time.
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        self.sequence = SnapshotSequence()
        for row in self.conn.execute("SELECT scenario_id, last_sequence, last_snapshot_id FROM scenario_sequence"):
            self.sequence.record(row["scenario_id"], row["last_sequence"], row["last_snapshot_id"])
        for row in self.conn.execute("SELECT snapshot_id, scenario_id, sequence FROM snapshots"):
            # all accepted ids stay known so a replayed id is rejected even after a reload
            self.sequence.mark_seen(row["scenario_id"], row["snapshot_id"])

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        """Add the v1.1 window columns to an asset_exposure table created by an earlier version."""
        have = {row["name"] for row in self.conn.execute("PRAGMA table_info(asset_exposure)")}
        migrations = (
            ("fire_arrival_at", "ALTER TABLE asset_exposure ADD COLUMN fire_arrival_at TEXT"),
            ("priority_status", "ALTER TABLE asset_exposure ADD COLUMN priority_status TEXT"),
            ("slack_min", "ALTER TABLE asset_exposure ADD COLUMN slack_min REAL"),
        )
        for column, statement in migrations:
            if column not in have:
                self.conn.execute(statement)

    # -- internals ---------------------------------------------------------------------------

    def _now(self) -> str:
        return _iso(self._clock())

    def _event(self, kind: str, message: str, asset_id: str | None = None, task_id: str | None = None) -> None:
        self.conn.execute("INSERT INTO events (at, kind, asset_id, task_id, message) VALUES (?, ?, ?, ?, ?)",
                          (self._now(), kind, asset_id, task_id, message))

    def _next_id(self, table: str, prefix: str) -> str:
        queries = {
            "tasks": "SELECT COALESCE(MAX(id), 0) + 1 FROM tasks",
            "overrides": "SELECT COALESCE(MAX(id), 0) + 1 FROM overrides",
        }
        try:
            query = queries[table]
        except KeyError:
            raise ValueError(f"Unsupported ID table: {table!r}") from None
        n = self.conn.execute(query).fetchone()[0]
        return f"{prefix}-{n:04d}"

    def _update_task(self, task_id: str, **fields) -> dict:
        fields["updated_at"] = self._now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [json.dumps(v) if k in TASK_JSON else v for k, v in fields.items()]
        self.conn.execute(f"UPDATE tasks SET {sets} WHERE task_id = ?", (*values, task_id))
        return self.get(task_id)

    # -- roster ------------------------------------------------------------------------------

    def load_roster(self, path) -> list[dict]:
        teams = json.loads(Path(path).read_text(encoding="utf-8"))
        for t in teams:
            self.conn.execute(
                "INSERT OR REPLACE INTO teams (team_id, name, capabilities, available, source) VALUES (?, ?, ?, ?, ?)",
                (t["team_id"], t["name"], json.dumps(list(t["capabilities"])), int(bool(t["available"])),
                 t.get("source")))
        self.conn.commit()
        return self.teams()

    def teams(self) -> list[dict]:
        return [_row_to_team(r) for r in self.conn.execute("SELECT * FROM teams ORDER BY team_id")]

    def team(self, team_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchone()
        return _row_to_team(row) if row else None

    def active_task_for_team(self, team_id: str) -> dict | None:
        row = self.conn.execute(
            f"SELECT * FROM tasks WHERE assigned_team_id = ? AND status IN ({','.join('?' * len(ACTIVE_STATUSES))}) "
            "ORDER BY id LIMIT 1", (team_id, *ACTIVE_STATUSES)).fetchone()
        return _row_to_task(row) if row else None

    # -- tasks -------------------------------------------------------------------------------

    def create_task(self, asset_id: str, action: str, reason: str, *, required_capabilities=None,
                    snapshot_id: str | None, notes: str = "", suggested: bool = False) -> dict:
        if action not in self.cfg.TASK_ACTIONS:
            raise ValueError(f"unknown action {action!r}; expected one of {list(self.cfg.TASK_ACTIONS)}")
        caps = list(required_capabilities) if required_capabilities is not None else list(self.cfg.TASK_ACTIONS[action])
        task_id = self._next_id("tasks", "task")
        now = self._now()
        self.conn.execute(
            "INSERT INTO tasks (task_id, asset_id, action, reason, status, required_capabilities, assigned_team_id, "
            "deadline_at, deadline_basis, blocking_questions, evidence, notes, created_at, updated_at, "
            "based_on_snapshot_id, affected_by_snapshot_id, suggested) "
            "VALUES (?, ?, ?, ?, 'open', ?, NULL, NULL, NULL, '[]', '[]', ?, ?, ?, ?, NULL, ?)",
            (task_id, asset_id, action, reason, json.dumps(caps), notes, now, now, snapshot_id, int(bool(suggested))))
        self._event("task_created", f"{'suggested' if suggested else 'created'} {action} task: {reason}",
                    asset_id=asset_id, task_id=task_id)
        self.conn.commit()
        return self.get(task_id)

    def get(self, task_id: str) -> dict:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown task {task_id}")
        return _row_to_task(row)

    def tasks(self, asset_id: str | None = None, status: str | None = None) -> list[dict]:
        clauses, params = [], []
        if asset_id is not None:
            clauses.append("asset_id = ?")
            params.append(asset_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return [_row_to_task(r) for r in self.conn.execute(f"SELECT * FROM tasks{where} ORDER BY id", params)]

    def suggest_tasks(self, scored_assets: list[dict], snapshot_id: str | None) -> list[dict]:
        """Create follow-up work from review reasons; skip keys with an open (not done) task."""
        open_keys = {(t["asset_id"], t["action"], t["reason"])
                     for t in self.tasks() if t["status"] != "done"}
        created = []
        for asset in scored_assets:
            asset_id = asset["asset_id"]
            reasons = list(asset.get("review_reasons") or [])
            wanted: list[tuple[str, str]] = []
            for r in ("occupancy_unknown", "occupancy_seasonal"):
                if r in reasons:
                    wanted.append(("confirm_occupancy", r.replace("_", " ")))
            if "class_ambiguous" in reasons:
                wanted.append(("contact_facility", f"class ambiguous: asset_type {asset.get('asset_type')!r}"))
            if "location_unknown" in reasons:
                wanted.append(("contact_facility", "location unknown"))
            elif "exposure_unknown" in reasons and asset.get("latitude") is not None:
                wanted.append(("contact_facility", "exposure unknown"))
            if EVACUATION_UNKNOWN in reasons:
                wanted.append(("contact_facility", EVACUATION_TASK_REASON))
            # forecast_unavailable is a producer gap shown in the review queue; no team task can resolve it
            if asset.get("intersects_fire") is True:
                wanted.append(("check_access", "facility intersects fire footprint"))
            for action, reason in wanted:
                key = (asset_id, action, reason)
                if key in open_keys:
                    continue
                open_keys.add(key)
                created.append(self.create_task(asset_id, action, reason, snapshot_id=snapshot_id, suggested=True))
        return created

    def assign(self, task_id: str, team_id: str) -> dict:
        task = self.get(task_id)
        team = self.team(team_id)
        if team is None:
            raise AssignmentError(f"unknown team: {team_id}")
        if not team["available"]:
            raise AssignmentError(f"team unavailable: {team_id}")
        active = self.active_task_for_team(team_id)
        if active is not None and active["task_id"] != task_id:
            raise AssignmentError(f"team busy: {team_id} has active task {active['task_id']}")
        missing = [c for c in task["required_capabilities"] if c not in team["capabilities"]]
        if missing:
            raise AssignmentError(
                f"capability mismatch: task needs {task['required_capabilities']}, team {team_id} has {team['capabilities']}")
        task = self._update_task(task_id, assigned_team_id=team_id, status="assigned")
        self._event("task_assigned", f"assigned to {team_id} ({team['name']})", asset_id=task["asset_id"], task_id=task_id)
        self.conn.commit()
        return task

    def release(self, task_id: str) -> dict:
        before = self.get(task_id)
        task = self._update_task(task_id, assigned_team_id=None, status="open")
        self._event("task_released", f"released from {before['assigned_team_id']}; back to open",
                    asset_id=task["asset_id"], task_id=task_id)
        self.conn.commit()
        return task

    def set_status(self, task_id: str, status: str, note: str = "") -> dict:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
        before = self.get(task_id)
        fields = {"status": status}
        if status == "assigned" and before["assigned_team_id"] is None:
            raise ValueError("status 'assigned' needs a team; use assign(task_id, team_id)")
        if status == "open" and before["assigned_team_id"] is not None:
            fields["assigned_team_id"] = None       # open means unassigned; the team is freed
        if note:
            fields["notes"] = (before["notes"] + "\n" if before["notes"] else "") + note
        task = self._update_task(task_id, **fields)
        if before["status"] == "done" and status != "done":
            kind, msg = "task_reopened", f"reopened by analyst: done -> {status}"
        elif status == "done":
            kind, msg = "task_done", "marked done by analyst"
        else:
            kind, msg = "task_status", f"status {before['status']} -> {status}"
        if note:
            msg += f" ({note})"
        self._event(kind, msg, asset_id=task["asset_id"], task_id=task_id)
        self.conn.commit()
        return task

    def add_question(self, task_id: str, question: str) -> dict:
        task = self.get(task_id)
        questions = task["blocking_questions"] + [{"question": question, "answer": None}]
        task = self._update_task(task_id, blocking_questions=questions)
        self._event("task_question", f"question added: {question}", asset_id=task["asset_id"], task_id=task_id)
        self.conn.commit()
        return task

    def answer_question(self, task_id: str, index: int, answer: str) -> dict:
        task = self.get(task_id)
        questions = task["blocking_questions"]
        questions[index]["answer"] = answer
        task = self._update_task(task_id, blocking_questions=questions)
        self._event("task_answer", f"question {index} answered: {answer}", asset_id=task["asset_id"], task_id=task_id)
        self.conn.commit()
        return task

    def add_evidence(self, task_id: str, evidence) -> dict:
        task = self.get(task_id)
        task = self._update_task(task_id, evidence=task["evidence"] + [evidence])
        self._event("task_evidence", f"evidence added: {evidence}", asset_id=task["asset_id"], task_id=task_id)
        self.conn.commit()
        return task

    def set_deadline(self, task_id: str, deadline_at, basis: str) -> dict:
        task = self._update_task(task_id, deadline_at=_iso(deadline_at), deadline_basis=basis)
        self._event("task_deadline", f"deadline {_iso(deadline_at)}: {basis}", asset_id=task["asset_id"], task_id=task_id)
        self.conn.commit()
        return task

    # -- confirmed overrides -----------------------------------------------------------------

    def confirm_override(self, asset_id: str, field: str, value, *, source: str, snippet: str, url=None,
                         observed_at=None, confidence, proposal_id=None, previous=None) -> dict:
        if field not in priority.OVERRIDE_FIELDS:
            raise ValueError(f"override field must be one of {list(priority.OVERRIDE_FIELDS)}, got {field!r}"
                             + ("; the forecast arrival comes from the producer" if field == "fire_arrival_at" else ""))
        if field == "evacuation_min":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"evacuation_min must be a finite number of minutes >= 0, got {value!r}")
            if not (source or "").strip():
                raise ValueError("an evacuation duration needs a source (who confirmed it)")
            value = float(value)
        override_id = self._next_id("overrides", "ovr")
        confirmed_at = self._now()
        self.conn.execute(
            "INSERT INTO overrides (override_id, asset_id, field, value, previous, source, snippet, url, observed_at, "
            "confidence, confirmed_at, proposal_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (override_id, asset_id, field, json.dumps(value), json.dumps(previous), source, snippet, url,
             _iso(observed_at), confidence, confirmed_at, proposal_id))
        self._event("override_confirmed",
                    f"{field} = {value!r} (was {previous!r}) from {source}" + (f", proposal {proposal_id}" if proposal_id else ""),
                    asset_id=asset_id)
        self.conn.commit()
        return self.override(override_id)

    def override(self, override_id: str) -> dict:
        row = self.conn.execute("SELECT * FROM overrides WHERE override_id = ?", (override_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown override {override_id}")
        return _row_to_override(row)

    def overrides(self, asset_id: str | None = None) -> list[dict]:
        if asset_id is None:
            rows = self.conn.execute("SELECT * FROM overrides ORDER BY confirmed_at, id")
        else:
            rows = self.conn.execute("SELECT * FROM overrides WHERE asset_id = ? ORDER BY confirmed_at, id", (asset_id,))
        return [_row_to_override(r) for r in rows]

    # -- snapshots ---------------------------------------------------------------------------

    def snapshots(self, scenario_id: str | None = None) -> list[dict]:
        if scenario_id is None:
            rows = self.conn.execute("SELECT * FROM snapshots ORDER BY scenario_id, sequence")
        else:
            rows = self.conn.execute("SELECT * FROM snapshots WHERE scenario_id = ? ORDER BY sequence", (scenario_id,))
        return [dict(r) for r in rows]

    def last_sequence(self, scenario_id: str) -> dict | None:
        return self.sequence.last(scenario_id)

    def exposure(self, scenario_id: str) -> dict[str, dict]:
        rows = self.conn.execute("SELECT * FROM asset_exposure WHERE scenario_id = ?", (scenario_id,))
        out = {}
        for r in rows:
            d = dict(r)
            d["intersects_fire"] = None if d["intersects_fire"] is None else bool(d["intersects_fire"])
            d["needs_review"] = None if d["needs_review"] is None else bool(d["needs_review"])
            d["review_reasons"] = json.loads(d["review_reasons"])
            d["present"] = bool(d["present"])
            out[d["asset_id"]] = d
        return out

    def _windows(self, snap: dict) -> dict[str, dict]:
        """priority_status and slack_min per asset for the snapshot with this store's confirmed overrides
        applied (the same view the UI ranks); empty when the snapshot carries no usable as_of."""
        if not snap.get("as_of"):
            return {}
        try:
            assets = priority.apply_overrides(snap.get("assets") or [], self.overrides(), self.cfg)
        except ValueError:
            return {}
        out = {}
        for a in assets:
            try:
                out[a["asset_id"]] = priority.rank_asset(a, snap["as_of"], self.cfg)
            except ValueError:
                continue     # one malformed asset (e.g. a non-numeric evacuation_min) does not blank the others
        return out

    def apply_snapshot(self, snap: dict) -> dict:
        """Accept or reject a snapshot, refresh exposure bookkeeping and flag affected open tasks.

        Flags open tasks whose asset's distance, intersection, needs_review, forecast arrival or
        window status (window_open / window_exhausted / needs_review) changed, or the remaining window
        crossed an attention bucket (priority.window_bucket: open / small / exhausted, threshold
        CONTACT_POLICY["attention_min"]); the event names the remaining window before and after. Never changes a task's owner or status. Assets present
        before and missing now keep their tasks, get an event and are flagged; their exposure row is
        kept with present = 0.
        """
        scenario_id, snapshot_id, sequence = snap["scenario_id"], snap["snapshot_id"], snap["sequence"]
        reason = self.sequence.reject_reason(snap)
        if reason is not None:
            self._event("snapshot_rejected", f"{snapshot_id} rejected: {reason}")
            self.conn.commit()
            return {"accepted": False, "affected_task_ids": [], "missing_asset_ids": [], "changed": [], "reason": reason}
        self.sequence.accept(snap)
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots (snapshot_id, scenario_id, sequence, as_of, computed_at, input_mode, "
            "data_status, accepted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (snapshot_id, scenario_id, sequence, snap.get("as_of"), snap.get("computed_at"),
             snap.get("input_mode"), snap.get("data_status"), self._now()))
        self.conn.execute(
            "INSERT OR REPLACE INTO scenario_sequence (scenario_id, last_sequence, last_snapshot_id) VALUES (?, ?, ?)",
            (scenario_id, sequence, snapshot_id))

        previous = self.exposure(scenario_id)
        changed, affected, missing = [], [], []
        seen = set()
        windows = self._windows(snap)
        for asset in snap.get("assets") or []:
            asset_id = asset["asset_id"]
            seen.add(asset_id)
            window = windows.get(asset_id) or {}
            current = {
                "distance_to_fire_m": asset.get("distance_to_fire_m"),
                "intersects_fire": asset.get("intersects_fire"),
                "needs_review": bool(asset.get("needs_review")),
                "fire_arrival_at": asset.get("fire_arrival_at"),
                "priority_status": window.get("priority_status"),
            }
            slack = window.get("slack_min")
            before = previous.get(asset_id)
            if before is not None:
                diffs = {k: [before.get(k), v] for k, v in current.items() if before.get(k) != v}
                # a window that crosses an attention bucket (open -> small -> exhausted) flags the task; one that
                # merely shrinks with elapsed time inside the same bucket does not
                buckets = [priority.window_bucket(before.get("slack_min"), self.cfg), priority.window_bucket(slack, self.cfg)]
                if buckets[0] != buckets[1]:
                    diffs["window_bucket"] = buckets
                if not before["present"]:
                    diffs["present"] = [False, True]
                if diffs:
                    entry = {"asset_id": asset_id, "changes": diffs}
                    if "fire_arrival_at" in diffs or "priority_status" in diffs or before.get("slack_min") != slack:
                        entry["slack_min"] = [before.get("slack_min"), slack]
                    changed.append(entry)
                    message = f"exposure changed in {snapshot_id}: " + ", ".join(
                        f"{k} {a!r} -> {b!r}" for k, (a, b) in diffs.items())
                    if "slack_min" in entry:
                        message += (f"; remaining window {_fmt_window(before.get('slack_min'))} -> "
                                    f"{_fmt_window(slack)}")
                    for t in self.tasks(asset_id=asset_id):
                        if t["status"] != "done":
                            self._update_task(t["task_id"], affected_by_snapshot_id=snapshot_id)
                            affected.append(t["task_id"])
                            self._event("task_affected", message, asset_id=asset_id, task_id=t["task_id"])
            self.conn.execute(
                "INSERT OR REPLACE INTO asset_exposure (scenario_id, asset_id, distance_to_fire_m, intersects_fire, "
                "needs_review, review_reasons, fire_arrival_at, priority_status, slack_min, snapshot_id, present) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (scenario_id, asset_id, current["distance_to_fire_m"],
                 None if current["intersects_fire"] is None else int(current["intersects_fire"]),
                 int(current["needs_review"]), json.dumps(list(asset.get("review_reasons") or [])),
                 current["fire_arrival_at"], current["priority_status"], slack, snapshot_id))
        for asset_id, before in previous.items():
            if asset_id in seen or not before["present"]:
                continue
            missing.append(asset_id)
            self.conn.execute("UPDATE asset_exposure SET present = 0 WHERE scenario_id = ? AND asset_id = ?",
                              (scenario_id, asset_id))
            self._event("asset_missing", f"asset missing from snapshot {snapshot_id}; tasks kept", asset_id=asset_id)
            for t in self.tasks(asset_id=asset_id):
                if t["status"] != "done":
                    self._update_task(t["task_id"], affected_by_snapshot_id=snapshot_id)
                    affected.append(t["task_id"])
                    self._event("task_affected", f"asset missing from snapshot {snapshot_id}; task kept",
                                asset_id=asset_id, task_id=t["task_id"])
        self._event("snapshot_accepted",
                    f"{snapshot_id} (sequence {sequence}) accepted: {len(changed)} changed, "
                    f"{len(missing)} missing, {len(affected)} tasks flagged")
        self.conn.commit()
        return {"accepted": True, "affected_task_ids": affected, "missing_asset_ids": missing, "changed": changed}

    # -- change log --------------------------------------------------------------------------

    def events(self, limit: int | None = None) -> list[dict]:
        """Chronological change log; with `limit`, the most recent entries (still in order)."""
        if limit is None:
            rows = self.conn.execute("SELECT at, kind, asset_id, task_id, message FROM events ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT at, kind, asset_id, task_id, message FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()[::-1]
        return [dict(r) for r in rows]
