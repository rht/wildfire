"""Persistent one-incident coordinator. No phone starts without explicit live mode."""

import json
import sqlite3
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .call_briefing import snapshot_request_data
from .coordination import CoordinationStore
from .evacuation_allocations import AllocationStore
from .evacuation_plan_adapter import build_approved_recommendation
from .evacuation_plans import (
    AnalystApproval,
    EvacuationGroup,
    PlanningContext,
    RoadEvidence,
    candidate_from_record,
)
from .evacuation_readiness import EvacuationRoute
from .fire_assessment import assess_fire
from .incident_planning import response_from_snapshot, scenario_from_snapshot
from .snapshot_contacts import enqueue_snapshot_contacts
from .voice_ingest import ingest
from .voice_models import ANSWER_FIELDS, CallResult, identifier, utc
from .voice_queue import CallQueueConfig, VoiceCallQueue
from .voice_store import digest, encoded

SQLITE_BUSY_TIMEOUT_SECONDS = 10


class IncidentRuntime:
    """Create per operation/thread. The server serializes writes with one worker lock."""

    def __init__(self, directory, settings, *, clock=None, client=None, fetcher=None, assessment_backend=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.settings = deepcopy(settings)
        self.mode = settings.get("input_mode", "synthetic")
        self.call_mode = settings.get("call_mode", "disabled")
        if self.mode not in ("live", "recorded", "synthetic") or self.call_mode not in (
            "disabled",
            "live",
            "sync_only",
            "simulate_no_answer",
        ):
            raise ValueError("invalid runtime mode")
        if (
            self.call_mode == "live"
            and self.mode != "live"
            or self.call_mode == "simulate_no_answer"
            and self.mode != "synthetic"
        ):
            raise ValueError("call mode does not match input mode")
        self.clock = clock or (lambda: datetime.now(UTC))
        self.client, self.fetcher = client, fetcher
        self.assessment_backend = assessment_backend
        from .llm_assessment import options
        self.assessment_options = options(settings.get("llm_assessment", {
            "mode": "live" if self.mode == "live" else "disabled"}))
        self.database = self.directory / "coordination.sqlite"
        self.control = self.directory / "runtime.sqlite"
        with self._db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS runtime (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS triggers (trigger_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stages (id INTEGER PRIMARY KEY, body TEXT NOT NULL);""")
        saved = self._load()
        if saved and saved["snapshot"]["input_mode"] != self.mode:
            raise ValueError("runtime input mode cannot change")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.control, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _load(self):
        with self._db() as db:
            row = db.execute("SELECT body FROM runtime WHERE id=1").fetchone()
            return json.loads(row[0]) if row else None

    def _save(self, saved):
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO runtime VALUES(1,?)", (encoded(saved),))

    def _event(self, kind, as_of, notes):
        with self._db() as db:
            db.execute(
                "INSERT INTO stages(body) VALUES(?)",
                (encoded({"kind": kind, "as_of": as_of, "notes": notes}),),
            )

    def _now(self, saved):
        return (
            utc(self.clock().isoformat())
            if self.mode == "live"
            else utc(saved["clock_at"])
        )

    def _coordinator(self, saved, now=None):
        observed = now or self._now(saved)
        return CoordinationStore(
            self.database, epoch=utc(saved["epoch"]), clock=lambda: observed
        )

    def state(self):
        if not self._load():
            return None
        from .dashboard_server import CoordinationDatabase

        try:
            return CoordinationDatabase(self.database).state()
        except sqlite3.OperationalError:
            return None

    def system(self):
        saved = self._load()
        with self._db() as db:
            events = [
                json.loads(r[0])
                for r in db.execute("SELECT body FROM stages ORDER BY id DESC LIMIT 40")
            ]
        return {
            "events": events,
            "discovery": saved.get("discovery") if saved else None,
            "enqueue": saved.get("enqueue") if saved else None,
            "errors": saved.get("errors", []) if saved else [],
            "call_mode": self.call_mode,
        }

    def trigger(self, trigger):
        return self.activate_trigger(self.prepare_trigger(trigger))

    def prepare_trigger(self, trigger):
        """Provider work only; the server runs this outside its mutation lock."""
        if trigger.get("input_mode", self.mode) != self.mode:
            raise ValueError("trigger input mode does not match runtime")
        identifier(trigger["trigger_id"], "trigger_id")
        identifier(trigger["scenario_id"], "scenario_id")
        fingerprint = digest(trigger)
        saved = self._load()
        with self._db() as db:
            existing = db.execute(
                "SELECT fingerprint FROM triggers WHERE trigger_id=?",
                (trigger["trigger_id"],),
            ).fetchone()
        if existing:
            if existing[0] != fingerprint:
                raise ValueError("trigger identity reused")
            return {"replay": True}
        fire = trigger["fire"]
        as_of = (
            utc(self.clock().isoformat())
            if self.mode == "live"
            else utc(trigger["as_of"])
        )
        if saved:
            if (
                trigger["scenario_id"] != saved["snapshot"]["scenario_id"]
                or fire["incident_id"] != saved["snapshot"]["incident_id"]
            ):
                raise ValueError("use a separate directory for another incident")
            previous_observed = saved["snapshot"]["fire_observed_at"]
            observed = fire.get("observed_at")
            if as_of < utc(saved["clock_at"]) or (
                previous_observed is not None
                and (observed is None or utc(observed) < utc(previous_observed))
            ):
                raise ValueError("fire update time cannot regress")
        sequence = saved["snapshot"]["sequence"] + 1 if saved else 1
        catalog = self.settings.get("catalog_file")
        rows = (
            json.loads(Path(catalog).read_text(encoding="utf-8")) if catalog else None
        )
        result = assess_fire(
            fire,
            scenario_id=trigger["scenario_id"],
            sequence=sequence,
            as_of=as_of,
            input_mode=self.mode,
            search_radius_m=self.settings.get("search_radius_m", 10000),
            facility_rows=rows,
            forecast=trigger.get("forecast"),
            spread=trigger.get("spread"),
            fetcher=self.fetcher,
        )
        operations = deepcopy(
            trigger.get("operations", self.settings.get("operations", {}))
        )
        from .llm_assessment import assess_snapshot
        result["snapshot"] = assess_snapshot(
            result["snapshot"], operations, self.directory / "llm_assessments.sqlite",
            self.assessment_options, backend=self.assessment_backend)
        next_saved = {
            "snapshot": result["snapshot"],
            "discovery": result["discovery"],
            "epoch": saved["epoch"] if saved else as_of.isoformat(),
            "clock_at": as_of.isoformat(),
            "operations": operations,
            "errors": [],
            "enqueue": None,
        }
        # Validate planner inputs before activating the trigger. No provider I/O here.
        scenario = scenario_from_snapshot(
            next_saved["snapshot"], operations, utc(next_saved["epoch"])
        )
        from .priority_models import validate_scenario

        validate_scenario(scenario)
        return {"saved": next_saved, "trigger_id": trigger["trigger_id"],
                "fingerprint": fingerprint,
                "previous_snapshot_id": saved["snapshot"]["snapshot_id"] if saved else None}

    def activate_trigger(self, prepared):
        """Activate under the coordinator lock; reject stale prepared assessments."""
        if prepared.get("replay"):
            return self.refresh()
        next_saved = prepared["saved"]
        saved = self._load()
        current_id = saved["snapshot"]["snapshot_id"] if saved else None
        if current_id != prepared["previous_snapshot_id"]:
            raise ValueError("incident changed while the LLM assessment was running")
        if saved and utc(next_saved["clock_at"]) < utc(saved["clock_at"]):
            raise ValueError("incident clock changed while the LLM assessment was running")
        as_of = self._now(next_saved)
        # Validate the full projection against disposable copies before accepting it.
        # Once accepted, trigger + assessment are one durable checkpoint. Refresh is
        # idempotent and resumes cross-database projections after interruption.
        with tempfile.TemporaryDirectory(prefix="fireline-preflight-") as directory:
            for source in self.directory.glob("*.sqlite"):
                source_db = sqlite3.connect(source)
                target_db = sqlite3.connect(Path(directory) / source.name)
                try:
                    source_db.backup(target_db)
                finally:
                    source_db.close()
                    target_db.close()
            staged = IncidentRuntime(directory, self.settings, clock=lambda: as_of)
            staged._save(next_saved)
            staged.refresh()
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO runtime VALUES(1,?)", (encoded(next_saved),)
            )
            db.execute(
                "INSERT INTO triggers VALUES(?,?)", (prepared["trigger_id"], prepared["fingerprint"])
            )
            event = {
                "kind": "fire_assessed",
                "as_of": as_of.isoformat(),
                "notes": f"Identified {len(next_saved['snapshot']['assets'])} locations; snapshot {next_saved['snapshot']['snapshot_id']}",
            }
            db.execute("INSERT INTO stages(body) VALUES(?)", (encoded(event),))
            for asset in next_saved["snapshot"]["assets"]:
                assessment = asset.get("llm_assessment")
                if assessment:
                    event = {"kind": "location_assessed", "as_of": as_of.isoformat(),
                             "notes": f"LLM assessment {assessment['status']} for {asset['asset_id']}"}
                    db.execute("INSERT INTO stages(body) VALUES(?)", (encoded(event),))
        return self.refresh()

    def _allocation_inputs(self, saved, scenario):
        ops = saved["operations"]
        snapshot = saved["snapshot"]
        ledger_path = self.directory / "allocations.sqlite"
        if not ops.get("candidates") and not ledger_path.exists():
            return []
        ops = deepcopy(ops)
        # A partial update cannot erase reception budgets or known destinations.
        # Retain omitted facilities as unavailable historical evidence.
        supplied = [candidate_from_record(r) for r in ops.get("candidates", [])]
        if ledger_path.exists():
            _, previous = AllocationStore(ledger_path).view(as_of=snapshot["as_of"])
            supplied_ids = {c.centre.centre_id for c in supplied}
            for candidate in previous[1]:
                if candidate.centre.centre_id not in supplied_ids:
                    record = asdict(candidate)
                    record["closed"] = True
                    supplied.append(candidate_from_record(record))
        bound = ops.get("snapshot_id") == snapshot["snapshot_id"]
        context = PlanningContext(
            snapshot["scenario_id"],
            snapshot["incident_id"],
            snapshot["snapshot_id"],
            saved["epoch"],
            snapshot["as_of"],
            ops.get("forecast_observed_min") if bound else None,
            buffer_min=30,
        )
        candidates = supplied
        groups = [
            EvacuationGroup(
                "group-" + a.asset_id,
                a.asset_id,
                a.people if a.people else None,
                None if a.assisted is None else bool(a.assisted),
                None
                if a.assisted is None
                else ("assisted_evacuation",)
                if a.assisted
                else (),
                a.fire_arrival_min,
                a.evacuation_min,
                a.forecast_source,
            )
            for a in scenario.locations
        ]
        store = AllocationStore(self.directory / "allocations.sqlite")
        routes = [
            EvacuationRoute(**(r if bound else dict(r, confirmed=False)))
            for r in ops.get("evacuation_routes", [])
        ]
        store.update_inputs(
            context,
            candidates,
            groups,
            routes,
            [RoadEvidence(**r) for r in ops.get("roads", [])],
        )
        if not bound:
            return []
        errors = []
        for command in ops.get("reservations", []):
            try:
                store.reserve(
                    command["command_id"],
                    command["group_id"],
                    command["centre_id"],
                    AnalystApproval(**command["approval"]),
                    as_of=snapshot["as_of"],
                    snapshot_id=snapshot["snapshot_id"],
                )
            except ValueError:
                errors.append(
                    {
                        "code": "destination_reservation_requires_review",
                        "group_id": command["group_id"],
                    }
                )
        return errors

    def _queue(self, coordinator):
        return VoiceCallQueue(
            coordinator.voice, CallQueueConfig(**self.settings.get("call_limits", {}))
        )

    def _enqueue(self, saved, coordinator, allocation, now):
        snapshot, ops = saved["snapshot"], saved["operations"]
        data = {}
        for asset in snapshot["assets"]:
            aid = asset["asset_id"]
            recommendation = None
            if allocation and aid in ops.get("route_guidance", {}):
                recommendation = build_approved_recommendation(
                    allocation,
                    aid,
                    as_of=now.isoformat(),
                    snapshot_id=snapshot["snapshot_id"],
                    route_guidance=ops["route_guidance"][aid],
                    expected_people=asset["estimated_occupancy"],
                )
            data[aid] = {
                "language": self.settings.get("language", "en"),
                "recommendation": recommendation,
            }

        def brief(asset, request_data):
            asset["road_warnings"] = deepcopy(ops.get("road_warnings", []))
            return snapshot_request_data(
                asset, request_data, snapshot_id=snapshot["snapshot_id"]
            )

        # Changed or expired guidance cancels unstarted requests through the adapter.
        queue = self._queue(coordinator)
        return enqueue_snapshot_contacts(
            queue,
            snapshot,
            self.settings.get("contacts", []),
            self.settings.get("approvals", []),
            data,
            epoch=saved["epoch"],
            now_at=now.isoformat(),
            buffer_min=30,
            briefing_adapter=brief,
            allow_synthetic=self.mode == "synthetic",
        )

    def refresh(self):
        saved = self._load()
        if not saved:
            return None
        now = self._now(saved)
        ops = saved["operations"]
        snapshot = saved["snapshot"]
        errors = list(saved.get("errors", []))
        bound = ops.get("snapshot_id") == snapshot["snapshot_id"]
        if not bound:
            errors.append({"code": "operational_inputs_need_refresh"})
        scenario = scenario_from_snapshot(snapshot, ops, utc(saved["epoch"]))
        coordinator = self._coordinator(saved, now)
        try:
            errors.extend(self._allocation_inputs(saved, scenario))
            queue = self._queue(coordinator)
            stale = coordinator.conn.execute(
                "SELECT request_id FROM voice_queue JOIN voice_calls USING(request_id) WHERE voice_queue.snapshot_id != ? AND dispatch_state='not_started' AND voice_queue.state IN ('pending', 'review')",
                (snapshot["snapshot_id"],),
            ).fetchall()
            for row in stale:
                queue.cancel(row[0])
            # Keep published approvals visible but withhold old instructions when risk changed.
            allocation = (
                AllocationStore(self.directory / "allocations.sqlite")
                if (self.directory / "allocations.sqlite").exists()
                else None
            )
            saved["enqueue"] = self._enqueue(saved, coordinator, allocation, now)
            response = response_from_snapshot(
                snapshot, scenario, ops, coordinator.voice, now, utc(saved["epoch"]),
                allocation_store=allocation,
            )
            roster = [
                {
                    "team_id": t["team_id"],
                    "name": t["team_id"],
                    "capabilities": t["capabilities"],
                    "available": t["available"],
                    "source": "configured operational roster",
                }
                for t in ops.get("teams", [])
            ]
            path = self.directory / "roster.json"
            path.write_text(encoded(roster), encoding="utf-8")
            coordinator.tasks.load_roster(path)
            events = self.system()["events"]
            state = coordinator.refresh(
                snapshot,
                scenario,
                [],
                [],
                road_warnings=ops.get("road_warnings", []),
                response_plan=response,
                allocation_store=allocation,
                system_events=events,
                system_errors=errors,
            )
            self._save(saved)
            return state
        finally:
            coordinator.close()

    def tick(self):
        saved = self._load()
        if not saved:
            return None
        self.refresh()  # expire or withdraw unstarted requests before admitting a call
        saved = self._load()
        coordinator = self._coordinator(saved)
        try:
            queue = self._queue(coordinator)
            if self.call_mode in ("live", "sync_only"):
                if self.client is None:
                    raise ValueError("call result client is not configured")
                from .slng_voice import ProviderError

                failures = queue.sync_active(self.client)
                saved["errors"] = [
                    {"code": "call_sync_failed", "request_id": r} for r in failures
                ]
                if self.call_mode == "live":
                    try:
                        queue.dispatch_next(self.client)
                    except ProviderError:
                        saved["errors"].append({"code": "dispatch_reconciliation_required"})
                self._save(saved)
            elif self.call_mode == "simulate_no_answer":
                pending = queue.status()["pending"]
                if pending:
                    aid = coordinator.voice.get(pending[0])["request"]["asset_id"]
                    coordinator.close()
                    coordinator = None
                    return self.simulate(aid, "no_answer")
        finally:
            if coordinator:
                coordinator.close()
        return self.refresh()

    def simulate(self, asset_id, status, answers=None):
        if self.mode != "synthetic":
            raise ValueError("simulation requires synthetic mode")
        if status not in ("no_answer", "completed", "failed", "declined"):
            raise ValueError("invalid synthetic status")
        saved = self._load()
        if saved is None:
            raise ValueError("no active incident")
        coordinator = self._coordinator(saved)
        try:
            records = [
                coordinator.voice.get(row[0])
                for row in coordinator.conn.execute(
                    "SELECT request_id FROM voice_calls"
                )
            ]
            record = next(
                (
                    r
                    for r in records
                    if r["request"]["asset_id"] == asset_id
                    and r["request"]["snapshot_id"] == saved["snapshot"]["snapshot_id"]
                ),
                None,
            )
            if not record:
                raise ValueError("asset has no approved queued call")
            if record["result"] is not None:
                return self.refresh()
            req = record["request"]
            rid = req["request_id"]
            callid = "synthetic-" + digest(rid)[:24]
            values = deepcopy(answers or {}) if status == "completed" else {}
            if values and set(values) - set(ANSWER_FIELDS) - {
                "evidence",
                "road_warning_acknowledged",
                "confidence",
                "confidence_basis",
            }:
                raise ValueError("invalid synthetic answers")
            result = CallResult(
                request_id=rid,
                asset_id=asset_id,
                snapshot_id=req["snapshot_id"],
                provider_call_id=callid,
                status=status,
                observed_at=self._now(saved).isoformat(),
                source="synthetic runtime exercise",
                **values,
            )
            coordinator.voice.bind(rid, callid)
            coordinator.voice.record_lifecycle(
                event_id="synthetic:" + rid,
                request_id=rid,
                asset_id=asset_id,
                snapshot_id=req["snapshot_id"],
                provider_call_id=callid,
                status=status,
                observed_at=self._now(saved).isoformat(),
            )
            coordinator.voice.record_result(result)
        finally:
            coordinator.close()
        self._event(
            "call_outcome",
            self._now(saved).isoformat(),
            f"{asset_id}: {status}; follow-up evaluated",
        )
        return self.refresh()

    def interview(self, body, authorization, token):
        saved = self._load()
        if not saved:
            raise ValueError("no active incident")
        coordinator = self._coordinator(saved)
        try:
            ingest(coordinator.voice, body, authorization=authorization, token=token)
        finally:
            coordinator.close()
        self._event(
            "interview_received",
            self._now(saved).isoformat(),
            "Call answers stored; replanning",
        )
        return self.refresh()

    def provider_event(self, provider, body, authorization, options):
        from .call_events import ingest_slng_call_end, ingest_vonage_event

        saved = self._load()
        if not saved:
            raise ValueError("no active incident")
        coordinator = self._coordinator(saved)
        try:
            if provider == "slng":
                if self.client is None:
                    raise ValueError("SLNG client not configured")
                result = ingest_slng_call_end(
                    coordinator.voice,
                    self.client,
                    body,
                    authorization,
                    token=options["token"],
                )
            elif provider == "vonage":
                result = ingest_vonage_event(
                    coordinator.voice, body, authorization, **options
                )
            else:
                raise ValueError("unknown provider")
        finally:
            coordinator.close()
        if result == "duplicate" or (
            isinstance(result, dict)
            and result
            and all(
                v == "duplicate"
                for k, v in result.items()
                if k in ("lifecycle", "result")
            )
            and any(k in result for k in ("lifecycle", "result"))
        ):
            self.refresh()  # recover projection if the first delivery stopped after persistence
            return result
        self._event(
            "provider_event",
            self._now(saved).isoformat(),
            f"{provider} lifecycle received; replanning",
        )
        self.refresh()
        return result


def demo_trigger(now=None):
    now = now or datetime.now(UTC)

    def at(minutes):
        return (now + timedelta(minutes=minutes)).isoformat()

    arrivals = {"A": 220, "B": 120, "C": 150}
    return {
        "trigger_id": "demo-fire-1",
        "scenario_id": "end-to-end-demo",
        "as_of": at(0),
        "fire": {
            "incident_id": "end-to-end-fire",
            "geometry": {"type": "Point", "coordinates": [3.01, 41.92]},
            "geometry_kind": "hotspot_centre",
            "observed_at": at(0),
            "received_at": at(0),
            "source": "synthetic ignition",
        },
        "forecast": {
            "schema_version": "forecast-input-1",
            "input_mode": "synthetic",
            "forecast_source": "synthetic spread exercise",
            "issued_at": at(0),
            "received_at": at(0),
            "forecast_horizon_at": at(300),
            "basis": "p10",
            "note": "synthetic, not a provider forecast",
            "estimates": {
                aid: {
                    "arrival_p10_at": at(minutes),
                    "arrival_p50_at": at(minutes + 15),
                    "burn_probability": 0.5,
                }
                for aid, minutes in arrivals.items()
            },
        },
    }
