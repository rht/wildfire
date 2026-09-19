"""Session glue between the Streamlit page and the coordination modules (no Streamlit imports).

One `Session` owns the discovered snapshot files, the current scenario/sequence position, the
`tasks.TaskStore`, the ranked queues (remaining evacuation window) and the agent `Workbench`. `app.py` keeps one instance in
`st.session_state` and only renders what these methods return, so the workflow is testable here.

The sequence position moves both ways (`go_to`, `next_update`, `previous_update`). Moving past the
store's high-water sequence applies the snapshot; moving back to one the store has already accepted
is a **view**: the snapshot is re-ranked with the analyst's confirmed overrides and shown, while
tasks, events, exposure and the accepted sequence stay where they are. The store's log is
append-only (`tasks.SnapshotSequence`), so an earlier snapshot is reviewable but never replayed.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from fireline import env, agent, config, fire_input, priority, tasks

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIRS = (ROOT / "fixtures" / "snapshots", ROOT / "data" / "snapshots")
ROSTER_PATH = ROOT / "fixtures" / "teams.json"
DEFAULT_DB = ROOT / "data" / "fireline.sqlite"
FAKE_LABEL_NO_KEY = "fake (no NEBIUS_API_KEY or ANTHROPIC_API_KEY)"


def discover_snapshots(dirs=SNAPSHOT_DIRS) -> tuple[dict[str, list[dict]], list[str]]:
    """Snapshot files grouped by scenario_id, each list sorted by sequence.

    Returns `(scenarios, warnings)`; an entry is `{"path", "scenario_id", "snapshot_id", "sequence",
    "as_of"}` (`as_of` labels the sequence control, so the files are read once here, not per rerun).
    Unreadable files or ones without the envelope keys are skipped with a warning."""
    scenarios: dict[str, list[dict]] = {}
    warnings: list[str] = []
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.json")):
            try:
                snap = json.loads(path.read_text(encoding="utf-8"))
                entry = {"path": str(path), "scenario_id": snap["scenario_id"],
                         "snapshot_id": snap["snapshot_id"], "sequence": int(snap["sequence"]),
                         "as_of": snap.get("as_of")}
            except (OSError, ValueError, KeyError, TypeError) as e:
                warnings.append(f"{path}: skipped ({type(e).__name__}: {e})")
                continue
            scenarios.setdefault(entry["scenario_id"], []).append(entry)
    for entries in scenarios.values():
        entries.sort(key=lambda e: (e["sequence"], e["snapshot_id"]))
    return dict(sorted(scenarios.items())), warnings


def db_path_from_env() -> Path:
    return Path(os.environ.get("FIRELINE_DB") or DEFAULT_DB)


def llm_available() -> bool:
    return env.live_llm_provider() is not None   # loads .env first


class Session:
    """Analyst session: scenario position, store, ranked queues and workbench (readme 9, CONTRACTS 7)."""

    def __init__(self, db_path=None, snapshot_dirs=SNAPSHOT_DIRS, roster_path=ROSTER_PATH, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.scenarios, self.discovery_warnings = discover_snapshots(snapshot_dirs)
        self.db_path = Path(db_path) if db_path is not None else db_path_from_env()
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.store = tasks.TaskStore(self.db_path)
        self._thread = threading.get_ident()
        if roster_path is not None and Path(roster_path).exists():
            self.store.load_roster(roster_path)
        self.scenario_id: str | None = None
        self.index: int = -1
        self.snapshot: dict | None = None
        self.scored: dict | None = None
        self.workbench: agent.Workbench | None = None
        self.last_update: dict | None = None
        self.last_suggested: list[dict] = []
        self.investigations: dict[str, dict] = {}     # asset_id -> last investigate record

    def ensure_open(self) -> "Session":
        """Reopen the store when called from another thread: sqlite3 connections are thread-bound and
        Streamlit may rerun the script on a new thread. Store state lives entirely in the database,
        so reopening loses nothing (":memory:" stores are never reopened)."""
        if threading.get_ident() != self._thread and str(self.db_path) != ":memory:":
            self.store = tasks.TaskStore(self.db_path)      # old connection is dropped, not closed cross-thread
            self._thread = threading.get_ident()
            if self.workbench is not None:
                self.workbench.tasks = self.store
        return self

    # -- scenario position --------------------------------------------------------------

    @property
    def scenario_ids(self) -> list[str]:
        return list(self.scenarios)

    @property
    def sequence_entries(self) -> list[dict]:
        return self.scenarios.get(self.scenario_id, []) if self.scenario_id else []

    @property
    def has_next(self) -> bool:
        return 0 <= self.index < len(self.sequence_entries) - 1

    @property
    def has_previous(self) -> bool:
        return self.index > 0

    @property
    def applied_sequence(self) -> int | None:
        """Highest sequence the store has accepted for this scenario (its high-water mark), or None."""
        return ((self.store.last_sequence(self.scenario_id) or {}).get("sequence")
                if self.scenario_id else None)

    @property
    def reviewing_earlier(self) -> bool:
        """True when the displayed snapshot is older than the store's high-water mark: an earlier
        moment under review, not the state the tasks and the change log describe."""
        applied = self.applied_sequence
        return bool(self.snapshot is not None and applied is not None and self.snapshot["sequence"] < applied)

    def _load(self, index: int) -> dict:
        entry = self.sequence_entries[index]
        snap = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
        self.index = index
        self.snapshot = snap
        return snap

    def _view(self, index: int) -> dict:
        """Show a snapshot the store has already accepted: re-rank it with the confirmed overrides and
        leave the store alone (no apply, no task suggestions from an earlier moment)."""
        snap = self._load(index)
        self.rescore()
        self.last_suggested = []
        applied = self.applied_sequence
        reason = (f"reviewing {snap['snapshot_id']} (sequence {snap['sequence']}); tasks, change log and the "
                  f"accepted sequence stay at {applied}") if self.reviewing_earlier else (
            f"{snap['snapshot_id']} already applied; shown from the store (sequence {applied})")
        result = {"accepted": False, "advanced": False, "view_only": True, "affected_task_ids": [],
                  "missing_asset_ids": [], "changed": [], "suggested_task_ids": [],
                  "snapshot_id": snap["snapshot_id"], "sequence": snap["sequence"], "reason": reason}
        self.last_update = result
        return result

    def _apply(self, snap: dict) -> dict:
        result = self.store.apply_snapshot(snap)
        result = dict(result, advanced=True, view_only=False, snapshot_id=snap["snapshot_id"],
                      sequence=snap["sequence"])
        self.rescore()
        self.last_suggested = self.suggest()
        result["suggested_task_ids"] = [t["task_id"] for t in self.last_suggested]
        self.last_update = result
        return result

    def select_scenario(self, scenario_id: str) -> dict:
        """Load the scenario's first snapshot, apply it to the store, score and suggest tasks."""
        if scenario_id not in self.scenarios:
            raise KeyError(f"unknown scenario {scenario_id!r}; have {self.scenario_ids}")
        self.scenario_id = scenario_id
        self.workbench = None          # proposals and questions belong to the previous scenario
        self.investigations = {}
        self.index = -1
        return self.go_to(self._resume_index())

    def _resume_index(self) -> int:
        """Index of the newest sequence file the store has already accepted for this scenario (so a
        restarted session resumes where the analyst left off), else 0."""
        last = self.store.last_sequence(self.scenario_id) or {}
        last_seq = last.get("sequence")
        if last_seq is None:
            return 0
        idx = 0
        for i, e in enumerate(self.sequence_entries):
            if e["sequence"] <= last_seq:
                idx = i
        return idx

    def go_to(self, index: int) -> dict:
        """Move the sequence position to `index` and return the update result.

        A snapshot past the store's high-water sequence is applied (exposure bookkeeping, affected
        tasks, suggestions); one at or below it is only displayed (`view_only`), so stepping back and
        forward again never re-flags a task, re-suggests work or rejects a duplicate."""
        entries = self.sequence_entries
        if not entries:
            raise RuntimeError("select a scenario first")
        if not 0 <= index < len(entries):
            raise IndexError(f"sequence index {index} outside 0..{len(entries) - 1} for {self.scenario_id}")
        applied = self.applied_sequence
        if applied is not None and entries[index]["sequence"] <= applied:
            return self._view(index)
        return self._apply(self._load(index))

    def next_update(self) -> dict:
        """Advance to the next sequence file (see `go_to`). Returns the store's apply result plus
        `advanced`, `view_only`, `snapshot_id`, `sequence` and `suggested_task_ids`; at the end of the
        sequence `advanced` is False and `reason` says so while the last file stays displayed."""
        if self.snapshot is None:
            raise RuntimeError("select a scenario first")
        if not self.has_next:
            last = self.sequence_entries[self.index]["snapshot_id"] if self.sequence_entries else None
            result = {"accepted": False, "advanced": False, "view_only": False,   # nothing moved
                      "affected_task_ids": [], "missing_asset_ids": [], "changed": [],
                      "reason": f"no further snapshot for {self.scenario_id}; at {last}",
                      "snapshot_id": last, "sequence": self.snapshot["sequence"], "suggested_task_ids": []}
            self.last_update = result
            return result
        return self.go_to(self.index + 1)

    def previous_update(self) -> dict:
        """Step back to the previous sequence file: a view of an earlier moment, never a rewind of the
        store (see `go_to`). At the first file nothing moves and `reason` says so."""
        if self.snapshot is None:
            raise RuntimeError("select a scenario first")
        if not self.has_previous:
            result = {"accepted": False, "advanced": False, "view_only": False,   # nothing moved
                      "affected_task_ids": [], "missing_asset_ids": [], "changed": [],
                      "reason": f"already at the first snapshot for {self.scenario_id}",
                      "snapshot_id": self.snapshot["snapshot_id"], "sequence": self.snapshot["sequence"],
                      "suggested_task_ids": []}
            self.last_update = result
            return result
        return self.go_to(self.index - 1)

    # -- scoring, suggestions, workbench ---------------------------------------------------

    def rescore(self) -> dict:
        """Re-rank the current snapshot by remaining evacuation window with the store's confirmed
        overrides; keeps the workbench's pending proposals and open questions while replacing its
        asset records."""
        self.scored = priority.rank_snapshot(self.snapshot, overrides=self.store.overrides())
        if self.workbench is None:
            self.workbench = agent.Workbench.from_scored(self.scored, tasks=self.store, snapshot=self.snapshot)
        else:
            self.workbench.snapshot = self.snapshot
            self.workbench.assets = {a["asset_id"]: a for a in self.scored["all"]}
        return self.scored

    rerank = rescore

    def suggest(self) -> list[dict]:
        return self.store.suggest_tasks(self.scored["all"], self.snapshot["snapshot_id"])

    def asset(self, asset_id: str) -> dict:
        return self.workbench.asset(asset_id)

    def assets_in_order(self) -> list[dict]:
        return list(self.scored["all"]) if self.scored else []

    # -- agent ------------------------------------------------------------------------------

    def investigate(self, asset_id: str, live: bool) -> tuple[dict, str]:
        """Run one bounded investigation. Live uses the provider back-end for the configured key
        (Nebius by default) only when asked; otherwise the deterministic FakeLLM. Returns
        `(record, llm_mode_label)`."""
        from fireline.llm import live_llm

        backend = live_llm() if live else None
        if backend is not None:
            llm, label = backend, f"live ({backend.model})"
        else:
            llm, label = None, (FAKE_LABEL_NO_KEY if not llm_available() else "fake (live disabled)")
        record = agent.investigate(self.workbench, asset_id, llm=llm)
        record["llm_label"] = label
        self.investigations[asset_id] = record
        return record, label

    def confirm(self, proposal_id: str) -> dict:
        """Persist the override, rescore (review reasons cleared by apply_overrides) and attach the
        evidence to the asset's open tasks (readme 8 step 4)."""
        p = agent.confirm_proposal(self.workbench, proposal_id, rescore=lambda wb: self.rescore())
        evidence = {"override_id": p.get("override_id"), "proposal_id": proposal_id, "field": p["field"],
                    "value": p["value"], "source": p["source"], "url": p.get("url")}
        for t in self.store.tasks(asset_id=p["asset_id"]):
            if t["status"] != "done":
                self.store.add_evidence(t["task_id"], evidence)
        return p

    def reject(self, proposal_id: str, note: str = "") -> dict:
        return agent.reject_proposal(self.workbench, proposal_id, note)

    def answer(self, question_id: str, answer: str) -> dict:
        return agent.answer_question(self.workbench, question_id, answer)

    def set_evacuation(self, asset_id: str, minutes, source: str, snippet: str = "", confidence="medium") -> dict:
        """Analyst-entered total evacuation duration: persist it as a confirmed `evacuation_min` override
        (needs a source), re-rank and attach it as evidence to the asset's open tasks."""
        asset = self.asset(asset_id)
        if not (source or "").strip():
            raise ValueError("an evacuation duration needs a source (who confirmed it and how)")
        try:
            value = float(minutes)
        except (TypeError, ValueError):
            raise ValueError(f"evacuation duration must be a number of minutes, got {minutes!r}") from None
        override = self.store.confirm_override(
            asset_id, "evacuation_min", value, source=source.strip(),
            snippet=(snippet or "").strip() or f"analyst entered {value:g} min total evacuation duration",
            confidence=confidence, previous=asset.get("evacuation_min"))
        self.rescore()
        evidence = {"override_id": override["override_id"], "field": "evacuation_min", "value": value,
                    "source": override["source"]}
        for t in self.store.tasks(asset_id=asset_id):
            if t["status"] != "done":
                self.store.add_evidence(t["task_id"], evidence)
        return override

    def pending_proposals(self, asset_id: str | None = None) -> list[dict]:
        return [p for p in (self.workbench.proposals if self.workbench else [])
                if p["status"] == "pending" and (asset_id is None or p["asset_id"] == asset_id)]

    def open_questions(self, asset_id: str | None = None) -> list[dict]:
        return [q for q in (self.workbench.questions if self.workbench else [])
                if q["status"] == "open" and (asset_id is None or q["asset_id"] == asset_id)]

    # -- tasks ------------------------------------------------------------------------------

    def create_task(self, asset_id: str, action: str, reason: str, notes: str = "") -> dict:
        return self.store.create_task(asset_id, action, reason, snapshot_id=self.snapshot["snapshot_id"], notes=notes)

    def block(self, task_id: str, question: str = "") -> dict:
        if question.strip():
            self.store.add_question(task_id, question.strip())
        return self.store.set_status(task_id, "blocked")

    def set_deadline(self, task_id: str, deadline_text: str, basis: str) -> dict:
        deadline = datetime.fromisoformat(deadline_text.strip().replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if not basis.strip():
            raise ValueError("a deadline needs a basis (distance alone never generates one)")
        return self.store.set_deadline(task_id, deadline, basis.strip())

    def open_tasks(self, asset_id: str | None = None) -> list[dict]:
        return [t for t in self.store.tasks(asset_id=asset_id) if t["status"] != "done"]

    # -- status -------------------------------------------------------------------------------

    def status(self) -> dict:
        """Envelope facts for the header: recorded data_status alongside one recomputed now."""
        snap = self.snapshot or {}
        now = self.clock()
        metrics = snap.get("metrics") or {}
        observed = snap.get("fire_observed_at")
        counts = {
            "assets": len(snap.get("assets") or []),
            "unlocated": sum(1 for a in snap.get("assets") or [] if a.get("latitude") is None or a.get("longitude") is None),
            "ranked": len(self.scored["ranked"]) if self.scored else 0,
            "window_exhausted": sum(1 for a in self.scored["ranked"] if a["priority_status"] == "window_exhausted")
            if self.scored else 0,
            "needs_review": len(self.scored["needs_review"]) if self.scored else 0,
            "forecast_unavailable": sum(1 for a in self.scored["needs_review"]
                                        if "forecast_unavailable" in a["review_reasons"]) if self.scored else 0,
            "evacuation_unknown": sum(1 for a in self.scored["needs_review"]
                                      if "evacuation_unknown" in a["review_reasons"]) if self.scored else 0,
            "flagged": len(self.scored["flagged"]) if self.scored else 0,
            "open_tasks": len(self.open_tasks()),
            "pending_proposals": len(self.pending_proposals()),
            "open_questions": len(self.open_questions()),
        }
        return {
            "scenario_id": self.scenario_id,
            "snapshot_id": snap.get("snapshot_id"),
            "sequence": snap.get("sequence"),
            "n_sequences": len(self.sequence_entries),
            "applied_sequence": self.applied_sequence,
            "reviewing_earlier": self.reviewing_earlier,
            "input_mode": snap.get("input_mode"),
            "as_of": snap.get("as_of"),
            "computed_at": snap.get("computed_at"),
            "fire_observed_at": observed,
            "data_status_recorded": snap.get("data_status"),
            "data_status_now": fire_input.data_status(observed, now),
            "source_age_s": metrics.get("source_age_s"),
            "source_age_now_s": fire_input.source_age_s(observed, now),
            "processing_s": metrics.get("processing_s"),
            "fire_geometry_kind": snap.get("fire_geometry_kind"),
            "fire_source": snap.get("fire_source"),
            "policy_version": config.CONTACT_POLICY["version"],
            "buffer_min": config.CONTACT_POLICY["buffer_min"],
            "now_at": self.scored["now_at"] if self.scored else snap.get("as_of"),
            "evacuation_policy_version": config.EVACUATION_POLICY["version"],
            "value_policy_version": config.VALUE_POLICY["version"],
            "db_path": str(self.db_path),
            "now": now.isoformat(timespec="seconds"),
            "counts": counts,
        }
