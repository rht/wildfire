"""Connected offline scenario. Fictional contacts only; no provider client or credentials.

Two commands can publish queued and outcome states separately so an already-open
read-only dashboard receives the real coordination WebSocket revision.
"""

import argparse
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import networkx as nx

from .call_briefing import snapshot_request_data
from .coordination import CoordinationStore
from .evacuation_allocations import AllocationStore
from .evacuation_plan_adapter import build_approved_recommendation
from .evacuation_plans import (
    AnalystApproval,
    CandidateFacility,
    EvacuationGroup,
    PlanningContext,
    RoadEvidence,
)
from .evacuation_readiness import EvacuationRoute, ReceptionCentre
from .grid import lonlat_to_xy
from .multi_response import plan_multi_response
from .priority_models import Location, StaticScenario
from .routing import RoadGraph
from .snapshot import build_snapshot, validate_snapshot
from .snapshot_contacts import enqueue_snapshot_contacts
from .voice_models import ANSWER_FIELDS, CallResult, utc
from .voice_queue import CallQueueConfig, VoiceCallQueue

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures/integration/offline_scenario.json"
)


class OfflineScenario:
    """One fixed synthetic scenario per directory; replay never rewinds completed calls."""

    def __init__(self, directory, *, epoch=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        manifest = self.directory / "offline-run.json"
        if manifest.exists():
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            if saved.get("scenario") != "offline-connected-v1":
                raise ValueError("not an offline integration directory")
            self.epoch = utc(saved["epoch"])
            if epoch is not None and epoch != self.epoch:
                raise ValueError("offline epoch cannot change on replay")
        else:
            if any(self.directory.glob("*.sqlite*")):
                raise ValueError(
                    "refusing an existing database without an offline manifest"
                )
            self.epoch = utc((epoch or datetime.now(UTC)).isoformat())
            manifest.write_text(
                json.dumps(
                    {
                        "scenario": "offline-connected-v1",
                        "epoch": self.epoch.isoformat(),
                    }
                ),
                encoding="utf-8",
            )
        self.now = self.epoch
        self.spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.snapshot = self._snapshot()
        self.scenario = self._scenario()
        self.database = self.directory / "coordination.sqlite"
        self.coordinator = CoordinationStore(
            self.database, epoch=self.epoch, clock=lambda: self.now
        )
        self.queue = VoiceCallQueue(self.coordinator.voice, CallQueueConfig())
        # AllocationStore and TaskStore both own tables named snapshots/events.
        self.allocations = AllocationStore(self.directory / "allocations.sqlite")
        previous = self.coordinator.state()
        if previous:
            self.now = utc(previous["as_of"])

    def at(self, minutes):
        return (self.epoch + timedelta(minutes=minutes)).isoformat()

    def _snapshot(self):
        records = [
            {
                "asset_id": r["asset_id"],
                "name": r["name"],
                "asset_class": r["asset_class"],
                "lon": r["lon"],
                "lat": r["lat"],
                "occupancy": r["estimated_occupancy"],
                "occupancy_source": "headcount",
                "register": "synthetic scenario",
            }
            for r in self.spec["locations"]
        ]
        forecast = {
            "schema_version": "forecast-input-1",
            "forecast_source": "synthetic scenario forecast",
            "input_mode": "synthetic",
            "issued_at": self.at(0),
            "received_at": self.at(0),
            "forecast_horizon_at": self.at(300),
            "basis": "p10",
            "note": "synthetic, not a provider forecast",
            "estimates": {
                r["asset_id"]: {
                    "arrival_p10_at": self.at(r["arrival_min"]),
                    "arrival_p50_at": self.at(r["arrival_min"] + 15),
                    "burn_probability": 0.5,
                }
                for r in self.spec["locations"]
            },
        }
        observation = {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [2.99, 41.92],
                        [3.005, 41.92],
                        [3.005, 41.93],
                        [2.99, 41.93],
                        [2.99, 41.92],
                    ]
                ],
            },
            "geometry_kind": "perimeter",
            "observed_at": self.at(0),
            "received_at": self.at(0),
            "source": "synthetic perimeter",
        }
        snapshot = build_snapshot(
            records,
            observation,
            scenario_id=self.spec["scenario_id"],
            incident_id=self.spec["incident_id"],
            sequence=1,
            as_of=self.at(0),
            computed_at=self.at(0),
            input_mode="synthetic",
            forecast=forecast,
        )
        if validate_snapshot(snapshot):
            raise ValueError("offline fixture produced an invalid snapshot")
        return snapshot

    def _scenario(self):
        facts = {a["asset_id"]: a for a in self.snapshot["assets"]}
        locations = []
        for row in self.spec["locations"]:
            a = facts[row["asset_id"]]
            locations.append(
                Location(
                    a["asset_id"],
                    a["name"],
                    *lonlat_to_xy(a["longitude"], a["latitude"]),
                    a["distance_to_fire_m"],
                    a["estimated_occupancy"],
                    row["assisted"],
                    a["value_score"],
                    row["arrival_min"],
                    row["arrival_min"],
                    a["evacuation_min"],
                    a["forecast_source"],
                    a["evacuation_source"],
                )
            )
        return StaticScenario(
            self.spec["scenario_id"],
            tuple(locations),
            (),
            {},
            horizon_min=300,
            buffer_min=self.spec["buffer_min"],
        )

    def _prepare_allocations(self):
        context = PlanningContext(
            self.spec["scenario_id"],
            self.spec["incident_id"],
            self.snapshot["snapshot_id"],
            self.at(0),
            self.at(0),
            0,
            buffer_min=self.spec["buffer_min"],
        )
        centre = CandidateFacility(
            ReceptionCentre(
                "hall", "Demo Reception Hall", 20, True, 300, "synthetic reception desk"
            ),
            kind="community_centre",
            approval=AnalystApproval(
                "hall-approval",
                "demo-analyst",
                self.spec["incident_id"],
                "hall",
                "synthetic centre approval",
            ),
            safe_until_min=300,
            threat_source="synthetic forecast",
            capabilities=("assisted_evacuation",),
        )
        groups = [
            EvacuationGroup(
                "group-" + a.asset_id,
                a.asset_id,
                a.people,
                bool(a.assisted),
                ("assisted_evacuation",) if a.assisted else (),
                a.fire_arrival_min,
                a.evacuation_min,
                a.forecast_source,
            )
            for a in self.scenario.locations
        ]
        routes = [
            EvacuationRoute(
                a.asset_id,
                "hall",
                5,
                max(5, a.evacuation_min),
                True,
                300,
                "synthetic inspected Town Road",
                ("town-road",),
            )
            for a in self.scenario.locations
        ]
        self.allocations.update_inputs(
            context,
            [centre],
            groups,
            routes,
            [RoadEvidence("town-road", "open", 0, 300, "synthetic inspection")],
        )
        # Only this explicit fictional approval reserves places. No unanswered
        # household is declared self-evacuating or automatically allocated.
        approval = AnalystApproval(
            "reserve-B",
            "demo-analyst",
            self.spec["incident_id"],
            "hall",
            "synthetic household allocation approval",
            "group-B",
        )
        self.allocations.reserve(
            "reserve-B",
            "group-B",
            "hall",
            approval,
            as_of=self.at(0),
            snapshot_id=self.snapshot["snapshot_id"],
        )

    def _enqueue(self):
        contacts = [
            {"asset_id": a["asset_id"], "contact_number": f"+1202555012{i}"}
            for i, a in enumerate(self.snapshot["assets"])
        ]
        approvals = [
            dict(c, snapshot_id=self.snapshot["snapshot_id"]) for c in contacts
        ]
        requests = {}
        for a in self.snapshot["assets"]:
            aid = a["asset_id"]
            guidance = {
                "asset_id": aid,
                "centre_id": "hall",
                "snapshot_id": self.snapshot["snapshot_id"],
                "confirmed": True,
                "road_ids": ["town-road"],
                "road_names": ["Town Road"],
                "instructions": "Use Town Road to Demo Reception Hall.",
                "source": "synthetic inspected route",
            }
            recommendation = build_approved_recommendation(
                self.allocations,
                aid,
                as_of=self.at(0),
                snapshot_id=self.snapshot["snapshot_id"],
                route_guidance=guidance,
                expected_people=a["estimated_occupancy"],
            )
            requests[aid] = {"language": "en", "recommendation": recommendation}

        # Warnings are briefing inputs, never added to the immutable snapshot.
        def brief(asset, data):
            asset["road_warnings"] = deepcopy(self.spec["road_warnings"])
            return snapshot_request_data(
                asset, data, snapshot_id=self.snapshot["snapshot_id"]
            )

        report = enqueue_snapshot_contacts(
            self.queue,
            self.snapshot,
            contacts,
            approvals,
            requests,
            epoch=self.at(0),
            now_at=self.at(0),
            briefing_adapter=brief,
            allow_synthetic=True,
        )
        if report["blocked"] or len(report["enqueue"]["queued"]) != len(contacts):
            raise ValueError("offline contacts did not enqueue")

    def _response(self):
        elapsed = (self.now - self.epoch).total_seconds() / 60
        readiness = []
        for c in self.coordinator.voice.conn.execute(
            "SELECT request_id FROM voice_calls ORDER BY request_id"
        ):
            record = self.coordinator.voice.get(c[0])
            if not record["provider_call_id"]:
                continue
            assessment = self.coordinator.voice.assessment(c[0])
            status = "no_answer" if assessment.status == "no_answer" else "unknown"
            if (
                assessment.identity_confirmed is True
                and assessment.evidence
                and (
                    assessment.can_self_evacuate is False
                    or assessment.transport_available is False
                )
            ):
                status = "assistance_required"
            readiness.append(
                {
                    "asset_id": assessment.asset_id,
                    "status": status,
                    "observed_min": assessment.observed_min,
                    "valid_until_min": assessment.observed_min
                    + self.spec["readiness_validity_min"],
                    "source": "synthetic call scenario; no operational validity claim",
                    "request_id": c[0],
                }
            )
        data = {
            "schema_version": "multi-response-input-1",
            "scenario_id": self.snapshot["scenario_id"],
            "snapshot_id": self.snapshot["snapshot_id"],
            "now_min": elapsed,
            "horizon_min": 300,
            "buffer_min": self.spec["buffer_min"],
            "assets": [
                {
                    "asset_id": a.asset_id,
                    "node_id": a.asset_id,
                    "people": a.people,
                    "assisted": a.assisted,
                    "value": a.value,
                    "deadline_min": a.deadline_min,
                }
                for a in self.scenario.locations
            ],
            "teams": deepcopy(self.spec["teams"]),
            "actions": deepcopy(self.spec["actions"]),
            "routes": [],
            "readiness": readiness,
            "committed": [],
        }
        graph = RoadGraph(nx.DiGraph())
        for node, point in self.spec["road_nodes"].items():
            graph.add_node(node, *point)
        for route in self.spec["routes"]:
            graph.g.add_edge(
                route["from_node"],
                route["to_node"],
                travel_min=route["minutes"],
                closed=False,
                cut_min=route["available_until_min"],
                confirmed=route["confirmed"],
                safe=route["safe"],
                source=route["source"],
                geometry_lonlat=route["path_lonlat"],
            )
        return plan_multi_response(data, graph=graph)

    def refresh(self):
        return self.coordinator.refresh(
            self.snapshot,
            self.scenario,
            [],
            [],
            road_warnings=self.spec["road_warnings"],
            response_plan=self._response(),
            allocation_store=self.allocations,
        )

    def prepare(self):
        previous = self.coordinator.state()
        if previous:
            return previous
        self._prepare_allocations()
        self._enqueue()
        roster = [
            {
                "team_id": t["team_id"],
                "name": t["team_id"],
                "capabilities": t["capabilities"],
                "available": t["available"],
                "source": "synthetic crew roster",
            }
            for t in self.spec["teams"]
        ]
        path = self.directory / "roster.json"
        path.write_text(json.dumps(roster), encoding="utf-8")
        self.coordinator.tasks.load_roster(path)
        return self.refresh()

    def complete(self):
        self.prepare()
        self.now = self.epoch + timedelta(minutes=1)
        # Include bound calls whose synthetic result was interrupted. The queue's
        # pending list deliberately excludes them to prevent real duplicate calls.
        rows = self.coordinator.voice.conn.execute(
            "SELECT request_id FROM voice_queue ORDER BY latest_start, arrival, distance, asset_id"
        ).fetchall()
        for row in rows:
            rid = row[0]
            record = self.coordinator.voice.get(rid)
            if record["result"] is not None:
                continue
            req = record["request"]
            if (
                req["input_mode"] != "synthetic"
                or req["snapshot_id"] != self.snapshot["snapshot_id"]
            ):
                raise ValueError("unexpected request in offline scenario")
            aid = req["asset_id"]
            call_id = "offline-" + aid
            outcome = self.spec["outcomes"][aid]
            status = "no_answer" if outcome == "no_answer" else "completed"
            self.coordinator.voice.bind(rid, call_id)
            self.coordinator.voice.record_lifecycle(
                event_id="offline-result-" + aid,
                request_id=rid,
                asset_id=aid,
                snapshot_id=self.snapshot["snapshot_id"],
                provider_call_id=call_id,
                status=status,
                observed_at=self.now.isoformat(),
            )
            values = {}
            if status == "completed":
                values = {
                    "identity_confirmed": True,
                    "whole_household_confirmed": True,
                    "can_self_evacuate": outcome == "self_evacuate",
                    "transport_available": outcome == "self_evacuate",
                    "wants_human": False,
                    "acknowledged": True,
                    "confidence": 0.95,
                    "confidence_basis": "synthetic_review",
                    "road_warning_acknowledged": True,
                    "evidence": {
                        f: "Synthetic respondent: confirmed scripted answer."
                        for f in (*ANSWER_FIELDS, "road_warning_acknowledged")
                    },
                }
            self.coordinator.voice.record_result(
                CallResult(
                    request_id=rid,
                    asset_id=aid,
                    snapshot_id=self.snapshot["snapshot_id"],
                    provider_call_id=call_id,
                    status=status,
                    observed_at=self.now.isoformat(),
                    source="synthetic offline outcome; no phone call placed",
                    **values,
                )
            )
        return self.refresh()

    def close(self):
        self.coordinator.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--phase", choices=("queued", "outcomes"), default="outcomes")
    args = parser.parse_args(argv)
    run = OfflineScenario(args.directory)
    try:
        state = run.prepare() if args.phase == "queued" else run.complete()
        print(
            json.dumps(
                {
                    "database": str(run.database),
                    "revision": state["revision"],
                    "input_mode": "synthetic",
                    "calls": [
                        {"asset_id": c["asset_id"], "status": c["status"]}
                        for c in state["calls"]
                    ],
                }
            )
        )
    finally:
        run.close()


if __name__ == "__main__":
    main()
