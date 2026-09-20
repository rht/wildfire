#!/usr/bin/env python
"""Build the recorded-mode incident-server fixtures for the REAL Gavarres fire (no network, committed inputs only).

    .venv/bin/python scripts/make_gavarres_trigger.py

Writes fixtures/incidents/gavarres_real/
  settings.json        `incident_server serve --settings`: input_mode recorded, calls disabled, no LLM,
                       catalog_file resolved relative to this file
  catalog.json         facility rows for `fire_assessment.assess_fire(facility_rows=...)`: the union of the
                       `assets` of fixtures/snapshots/gavarres_real_0001..0003.json (real Gencat register rows,
                       extract 2026-09-19), one row per asset_id, sorted, with every per-snapshot derived field
                       (distance, forecast, arrival, evacuation, value at risk, review flags) stripped so the
                       runtime recomputes them for each trigger
  trigger-0001..3.json one `POST /api/fire` body per recorded satellite perimeter, the same picks and order as
                       scripts/make_snapshots.py (first, middle, last by provider `computed_at`): `as_of` = that
                       `computed_at`, `fire` = the `fire_input.parse_deepfire` FireUpdate of that perimeter

The recorded Deepfire fire-spread run (fixtures/fire/deepfire/real/*fire-spread-simulation*) is NOT attached:
it was run on 2026-09-19 and `assess_fire` rejects a forecast issued after the trigger's July `as_of`.
Instead each trigger carries a `forecast` (forecast-input-1) lifted verbatim from the paired committed snapshot
gavarres_real_000N.json: the project's CA-ensemble estimates (labelled enrichment, not validated, not a provider
forecast) for every asset with an arrival, issued at the perimeter time. Nothing is inferred from distance.

Deterministic: same inputs -> byte-identical files. Never reads the gitignored data/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shapely.geometry import Point, shape  # noqa: E402

from fireline.voice_models import utc  # noqa: E402
from scripts.make_snapshots import recorded_real_fires  # noqa: E402

FIX = ROOT / "fixtures"
OUT_DIR = FIX / "incidents" / "gavarres_real"
SNAPSHOT_FILES = tuple(FIX / "snapshots" / f"gavarres_real_{n:04d}.json" for n in (1, 2, 3))
SCENARIO_ID = "gavarres_real"

# Static asset fields (CONTRACTS.md 2.4 identity / location / occupancy / provenance). Everything else in a
# snapshot asset row is derived per snapshot by assess_fire -> build_snapshot and must not be supplied.
STATIC_FIELDS = ("asset_id", "name", "asset_type", "latitude", "longitude", "geometry", "area_m2",
                 "capacity", "estimated_occupancy", "occupancy_basis", "municipality")
STATIC_FLAGS = ("occupancy_seasonal", "class_ambiguous")   # the only review reasons asset_record reads back

SETTINGS = {
    "input_mode": "recorded",
    "call_mode": "disabled",
    "search_radius_m": 10000,
    "catalog_file": "catalog.json",
    "llm_assessment": {"mode": "disabled"},
    "language": "en",
    "contacts": [],
    "approvals": [],
}


def catalog_row(asset: dict) -> dict:
    """One snapshot asset row -> static catalog row (register provenance kept, derived fields dropped)."""
    row = {k: asset.get(k) for k in STATIC_FIELDS}
    row["review_reasons"] = [r for r in asset.get("review_reasons") or [] if r in STATIC_FLAGS]
    row["sources"] = [s for s in asset.get("sources") or []
                      if s.get("fields") and set(s["fields"]) <= set(STATIC_FIELDS)]
    return row


def snapshot_assets(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["assets"]


FORECAST_NOTE = ("computed offline by the project's CA ensemble from recorded wind model data valid at this "
                 "perimeter's time; issued_at is the perimeter time, not the build time; labelled enrichment, "
                 "not validated, not a provider forecast")


def build_forecast(trigger: dict, assets: list[dict]) -> dict:
    """forecast-input-1 lifted verbatim from the paired snapshot: one estimate per asset with an arrival."""
    covered = [a for a in assets if a.get("arrival_p10_at") is not None]
    if not covered:
        raise SystemExit(f"{trigger['trigger_id']}: paired snapshot has no asset with arrival_p10_at")
    sources = {a["forecast_source"] for a in covered}
    horizons = {a["forecast_horizon_at"] for a in covered}
    if len(sources) != 1 or len(horizons) != 1:
        raise SystemExit(f"{trigger['trigger_id']}: mixed forecast_source / forecast_horizon_at in snapshot")
    return {"schema_version": "forecast-input-1", "forecast_source": sources.pop(), "input_mode": "recorded",
            "issued_at": trigger["as_of"], "received_at": trigger["as_of"], "forecast_horizon_at": horizons.pop(),
            "basis": "p10", "note": FORECAST_NOTE,
            "estimates": {a["asset_id"]: {k: a[k] for k in ("arrival_p10_at", "arrival_p50_at", "burn_probability")}
                          for a in sorted(covered, key=lambda a: a["asset_id"])}}


def build_catalog(files=SNAPSHOT_FILES) -> list[dict]:
    rows = {}
    for path in files:
        for asset in json.loads(path.read_text(encoding="utf-8"))["assets"]:
            rows.setdefault(asset["asset_id"], catalog_row(asset))
    return [rows[k] for k in sorted(rows)]


# FICTIONAL crews. Bases are real places (two Gencat fire stations from the catalog, two town centres) chosen
# to sit outside every recorded perimeter; the crews, positions and capabilities are invented for the demo.
CREW_SOURCE = "fictional crew GPS, not a real deployment"
CREWS = (
    ("bombers-1", 41.80871, 3.031821, ["protection"], 0,
     "fictional protection crew parked at Parc de Bombers de la Vall d'Aro"),
    ("bombers-2", 41.879694, 2.883842, ["protection"], 0,
     "fictional protection crew parked at Parc de Bombers de Cassa de la Selva"),
    ("transport-1", 41.96, 3.04, ["assisted_evacuation"], 8,
     "fictional assisted-evacuation minibus parked in La Bisbal d'Emporda"),
    ("police-1", 41.92, 3.16, ["traffic_control"], 0,
     "fictional police unit parked in Palafrugell"),
)
SHIFT_MIN = 720          # each crew is available for 12 h from the trigger
TARGETS = 3              # located catalog assets nearest the perimeter that get a fictional action
ROUTE_KMH = 40           # straight-line speed assumed for the fictional routes
MIN_LEAD_MIN = 120       # a target's forecast arrival must be at least this long after the trigger
# FICTIONAL assisted-evacuation counts by asset type (capped at the estimated occupancy when known).
ASSISTED_BY_TYPE = {"care_home": 4, "hospital": 4, "school": 2, "campsite": 2}
ASSISTED_SOURCE = "FICTIONAL assisted counts by asset type (care_home/hospital 4, school/campsite 2, else 0)"


def assisted_count(row: dict) -> int:
    count = ASSISTED_BY_TYPE.get(row.get("asset_type"), 0)
    occupancy = row.get("estimated_occupancy")
    return count if occupancy is None else min(count, int(occupancy))


def _km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km (haversine)."""
    from math import asin, cos, radians, sin, sqrt
    p1, p2, dl = radians(lat1), radians(lat2), radians(lon2 - lon1)
    h = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def nearest_assets(fire: dict, catalog: list[dict], forecast: dict, as_of, count: int = TARGETS) -> list[dict]:
    """The `count` located catalog rows nearest the perimeter (ties broken by asset_id) that the planner can
    schedule: outside the perimeter, known occupancy, and a forecast arrival >= MIN_LEAD_MIN after as_of."""
    geometry = shape(fire["geometry"])
    earliest = as_of + timedelta(minutes=MIN_LEAD_MIN)
    estimates = forecast["estimates"]

    def eligible(r):
        est = estimates.get(r["asset_id"])
        return (r["latitude"] is not None and r["longitude"] is not None
                and r.get("estimated_occupancy") is not None and est is not None
                and utc(est["arrival_p10_at"]) >= earliest
                and not geometry.contains(Point(r["longitude"], r["latitude"])))

    ranked = sorted((geometry.distance(Point(r["longitude"], r["latitude"])), r["asset_id"], r)
                    for r in catalog if eligible(r))
    return [r for _, _, r in ranked[:count]]


def build_operations(trigger: dict, catalog: list[dict], epoch, sequence: int) -> dict:
    """FICTIONAL operations bound to this trigger's snapshot: crews, straight-line routes, review-only actions."""
    as_of = utc(trigger["as_of"])
    elapsed = round((as_of - epoch).total_seconds() / 60)
    horizon = elapsed + SHIFT_MIN
    targets = nearest_assets(trigger["fire"], catalog, trigger["forecast"], as_of)
    teams, nodes, routes = [], {}, []
    for team_id, lat, lon, capabilities, capacity, note in CREWS:
        base = f"base-{team_id}"
        nodes[base] = [lon, lat]
        teams.append({"team_id": team_id, "start_node_id": base, "available": True,
                      "available_from_min": elapsed, "available_until_min": horizon,
                      "transport_capacity": capacity, "capabilities": capabilities, "note": note,
                      "current_location": {"latitude": lat, "longitude": lon,
                                           "observed_at": trigger["as_of"], "source": CREW_SOURCE}})
        for asset in targets:
            nodes[asset["asset_id"]] = [asset["longitude"], asset["latitude"]]
            routes.append({"from_node": base, "to_node": asset["asset_id"],
                           "minutes": max(1, round(_km(lat, lon, asset["latitude"], asset["longitude"])
                                                   / ROUTE_KMH * 60)),
                           "confirmed": True, "safe": True, "available_until_min": horizon,
                           "source": "fictional straight-line route, not an inspected road",
                           "path_lonlat": [[lon, lat], [asset["longitude"], asset["latitude"]]]})
    for src in targets:                       # target-to-target legs so one crew can chain actions
        for dst in targets:
            if src["asset_id"] != dst["asset_id"]:
                routes.append({"from_node": src["asset_id"], "to_node": dst["asset_id"],
                               "minutes": max(1, round(_km(src["latitude"], src["longitude"], dst["latitude"],
                                                           dst["longitude"]) / ROUTE_KMH * 60)),
                               "confirmed": True, "safe": True, "available_until_min": horizon,
                               "source": "fictional straight-line route, not an inspected road",
                               "path_lonlat": [[src["longitude"], src["latitude"]],
                                               [dst["longitude"], dst["latitude"]]]})
    actions = [{"action_id": f"protect-{a['asset_id']}", "asset_id": a["asset_id"], "duration_min": 30,
                "deadline_min": horizon, "requires": [], "capabilities": ["protection"],
                "transport_people": 0, "readiness_required": False,
                "effects": [{"asset_id": a["asset_id"], "coverage": 1, "confirmed": True,
                             "source": "fictional protection effect, not predicted lives saved"}]}
               for a in targets]
    assisted = {r["asset_id"]: assisted_count(r) for r in catalog
                if r["latitude"] is not None and r["longitude"] is not None}
    return {"snapshot_id": f"{SCENARIO_ID}-{sequence:04d}", "horizon_min": horizon,
            "source": "FICTIONAL crews, routes and assisted counts invented for the demo; not a real deployment",
            "note": ASSISTED_SOURCE,
            "assisted": assisted, "teams": teams, "road_nodes": nodes, "routes": routes, "actions": actions,
            "readiness_validity_min": 60}


def build_triggers(catalog: list[dict] | None = None) -> list[dict]:
    fires = recorded_real_fires()
    if not fires:
        raise SystemExit("fixtures/fire/deepfire/real/ has no satellite-perimeters response")
    catalog = build_catalog() if catalog is None else catalog
    epoch = fires[0][1]
    triggers = [{"trigger_id": f"gavarres-real-{n:04d}", "scenario_id": SCENARIO_ID, "input_mode": "recorded",
                 "as_of": as_of.isoformat(), "fire": fire}
                for n, (fire, as_of) in enumerate(fires, start=1)]
    for n, trigger in enumerate(triggers, start=1):
        trigger["forecast"] = build_forecast(trigger, snapshot_assets(SNAPSHOT_FILES[n - 1]))
        trigger["operations"] = build_operations(trigger, catalog, epoch, n)
    return triggers


def write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def generate(out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "settings.json", out_dir / "catalog.json"]
    catalog = build_catalog()
    write(written[0], SETTINGS)
    write(written[1], catalog)
    for trigger in build_triggers(catalog):
        path = out_dir / f"trigger-{trigger['trigger_id'].rsplit('-', 1)[1]}.json"
        write(path, trigger)
        written.append(path)
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    for path in generate(args.out_dir):
        body = json.loads(path.read_text(encoding="utf-8"))
        detail = (f"{len(body)} rows" if isinstance(body, list) else
                  f"as_of {body['as_of']} observed {body['fire']['observed_at']} "
                  f"{len(body['operations']['teams'])} fictional crews "
                  f"{len(body['forecast']['estimates'])} forecast estimates" if "fire" in body else "")
        print(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
