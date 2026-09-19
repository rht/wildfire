"""Scenario: run the engine end-to-end from a FireState and an ArrivalRaster (PLAN 6.3-6.5).

Contract in CONTRACTS.md. `run` takes the ArrivalRaster explicitly so the caller chooses the spread
source (CA, Deepfire, or a synthetic raster in tests). JSON persistence writes the raster arrays to a
`.npz` beside the JSON file (relative path stored).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import config
from .decide import decide
from .exposure import TIER_RANK, apply_override, build_asset_table
from .grid import Grid
from .routing import best_route
from .spread import ArrivalRaster


def scenario_id(cluster_id: str, t: datetime, config_hash: str) -> str:
    return hashlib.sha1(f"{cluster_id}|{t.isoformat()}|{config_hash}".encode()).hexdigest()[:12]


def _sort_key(row: dict):
    return (TIER_RANK.get(row["tier"], 99), row["lead_adjusted_p10_min"], row["asset_id"])


@dataclass
class Scenario:
    id: str
    cluster_id: str
    t: datetime
    config_hash: str
    assets: list[dict] = field(default_factory=list)
    destinations: list[dict] = field(default_factory=list)
    queue: list[dict] = field(default_factory=list)
    change_log: list[str] = field(default_factory=list)
    arrival: ArrivalRaster | None = None
    closures: list[str] = field(default_factory=list)
    spread_source: str = ""
    horizon_min: int = 0
    cfg: object = field(default=config, repr=False, compare=False)

    # ---------- build ----------
    @classmethod
    def run(cls, fire_state, arrival: ArrivalRaster, assets_in: list[dict], destinations: list[dict],
            road_graph, cfg=config, closures: list[str] | None = None) -> "Scenario":
        if closures:
            road_graph.apply_closures(closures)
        table = build_asset_table(assets_in, arrival, cfg)
        road_graph.cut_times(arrival)
        for asset in table:
            route = best_route(road_graph, asset, destinations, arrival, departure_min=0.0, cfg=cfg)
            if route is None:
                if "no_exit" not in asset["needs_review"]:
                    asset["needs_review"].append("no_exit")
            elif route.get("via_track"):
                asset["notes"].append(f"exit route uses an unpaved track ({route['first_cut_road'] or 'unnamed'} "
                                      f"first cut at {_fmt(route['first_cut_min'])})")
            asset["route"] = route
            asset["decision"] = decide(asset, route, cfg)
        table.sort(key=_sort_key)
        sc = cls(
            id=scenario_id(fire_state.cluster_id, fire_state.t, cfg.config_hash()),
            cluster_id=fire_state.cluster_id,
            t=fire_state.t,
            config_hash=cfg.config_hash(),
            assets=table,
            destinations=list(destinations),
            arrival=arrival,
            closures=list(getattr(road_graph, "closed_names", [])),
            spread_source=arrival.source,
            horizon_min=arrival.horizon_min,
            cfg=cfg,
        )
        counts = {}
        for a in table:
            counts[a["tier"]] = counts.get(a["tier"], 0) + 1
        sc.change_log.append(
            f"scenario {sc.id} built for cluster {sc.cluster_id} at {sc.t.isoformat()} from {arrival.source}: "
            f"{len(table)} assets, " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: TIER_RANK.get(kv[0], 9)))
            + f"; {sum(1 for a in table if a['needs_review'])} need review")
        return sc

    # ---------- access ----------
    def asset(self, asset_id: str) -> dict:
        for a in self.assets:
            if a["asset_id"] == asset_id:
                return a
        raise KeyError(asset_id)

    def escalation(self, escalation_id: str) -> dict:
        for e in self.queue:
            if e["escalation_id"] == escalation_id:
                return e
        raise KeyError(escalation_id)

    def _redecide(self, asset: dict, reason: str) -> None:
        before = asset["decision"]["decision"] if asset.get("decision") else None
        asset["decision"] = decide(asset, asset.get("route"), self.cfg)
        after = asset["decision"]["decision"]
        if before != after:
            self.change_log.append(f"{asset['asset_id']} decision {before} -> {after} ({reason})")
        self.assets.sort(key=_sort_key)

    # ---------- coordinator queue ----------
    def add_escalation(self, asset_id: str, question: str, options: list[str], default: str,
                       field: str | None = None, default_value=None) -> dict:
        """Append a question for the coordinator and apply the pessimistic default now.
        When `field` is given, `default_value` is applied through exposure.apply_override (pessimistic
        direction enforced) so the scenario already reflects the default until the coordinator answers."""
        asset = self.asset(asset_id)
        if default not in options:
            raise ValueError(f"default {default!r} not in options {options}")
        esc = {
            "escalation_id": f"esc-{len(self.queue) + 1:03d}-{asset_id.split(':')[-1]}",
            "asset_id": asset_id,
            "question": question,
            "options": list(options),
            "default": default,
            "status": "open",
            "answer": None,
            "field": field,
            "default_value": default_value,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if field is not None:
            apply_override(asset, {
                "field": field, "value": default_value, "source": "escalation default",
                "quoted_snippet": question, "confidence": "default",
            })
            self._redecide(asset, f"escalation default {field}={default_value}")
        self.queue.append(esc)
        self.change_log.append(f"{asset_id}: escalated '{question}' default '{default}'")
        return esc

    def answer_escalation(self, escalation_id: str, answer: str, value=None) -> dict:
        """Record the coordinator's answer. If the escalation maps to a field and `value` is given, the
        human answer is applied directly (a coordinator may move in either direction) and recorded."""
        esc = self.escalation(escalation_id)
        if answer not in esc["options"]:
            raise ValueError(f"answer {answer!r} not in options {esc['options']}")
        esc["status"] = "answered"
        esc["answer"] = answer
        esc["answered_at"] = datetime.now(timezone.utc).isoformat()
        asset = self.asset(esc["asset_id"])
        if esc.get("field") and value is not None and value != asset.get(esc["field"]):
            previous = asset.get(esc["field"])
            asset[esc["field"]] = value
            if esc["field"] == "occupancy":
                asset["occupancy_source"] = "override"
            asset.setdefault("overrides", []).append({
                "field": esc["field"], "value": value, "previous": previous,
                "source": "coordinator answer", "quoted_snippet": f"{esc['question']} -> {answer}",
                "fetched_at": esc["answered_at"], "confidence": "human", "direction": "human",
            })
            self._redecide(asset, f"coordinator answered {esc['field']}={value}")
        self.change_log.append(f"{esc['asset_id']}: '{esc['question']}' answered '{answer}'")
        return esc

    # ---------- comparison ----------
    def diff(self, other: "Scenario") -> list[str]:
        """Assets whose tier or decision differ between self and other (plus assets in only one)."""
        mine = {a["asset_id"]: a for a in self.assets}
        theirs = {a["asset_id"]: a for a in other.assets}
        out = []
        for aid in sorted(set(mine) | set(theirs)):
            a, b = mine.get(aid), theirs.get(aid)
            if a is None:
                out.append(f"{aid}: only in {other.id}")
            elif b is None:
                out.append(f"{aid}: only in {self.id}")
            else:
                da = a.get("decision", {}).get("decision")
                db = b.get("decision", {}).get("decision")
                parts = []
                if a["tier"] != b["tier"]:
                    parts.append(f"tier {a['tier']} -> {b['tier']}")
                if da != db:
                    parts.append(f"decision {da} -> {db}")
                if parts:
                    out.append(f"{aid} ({a['name']}): " + "; ".join(parts))
        return out

    # ---------- persistence ----------
    def to_json(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        npz_name = None
        if self.arrival is not None:
            npz_path = path.with_suffix(".npz")
            np.savez_compressed(npz_path, arrival_p10=self.arrival.arrival_p10, arrival_p50=self.arrival.arrival_p50,
                                arrival_p90=self.arrival.arrival_p90, burn_prob=self.arrival.burn_prob)
            npz_name = npz_path.name
        payload = {
            "id": self.id,
            "cluster_id": self.cluster_id,
            "t": self.t.isoformat(),
            "config_hash": self.config_hash,
            "spread_source": self.spread_source,
            "horizon_min": self.horizon_min,
            "closures": self.closures,
            "assets": self.assets,
            "destinations": self.destinations,
            "queue": self.queue,
            "change_log": self.change_log,
            "arrival_npz": npz_name,
            "grid": None if self.arrival is None else {
                "xmin": self.arrival.grid.xmin, "ymin": self.arrival.grid.ymin,
                "xmax": self.arrival.grid.xmax, "ymax": self.arrival.grid.ymax, "cell": self.arrival.grid.cell},
        }
        # inf is written as the JSON-nonstandard token Infinity (Python's json reads it back).
        path.write_text(json.dumps(payload, indent=1, default=_json_default))
        return path

    @classmethod
    def from_json(cls, path) -> "Scenario":
        path = Path(path)
        d = json.loads(path.read_text())
        arrival = None
        if d.get("arrival_npz") and d.get("grid"):
            npz_path = path.parent / d["arrival_npz"]
            if npz_path.exists():
                z = np.load(npz_path)
                g = d["grid"]
                arrival = ArrivalRaster(
                    grid=Grid(g["xmin"], g["ymin"], g["xmax"], g["ymax"], g["cell"]),
                    arrival_p10=z["arrival_p10"], arrival_p50=z["arrival_p50"], arrival_p90=z["arrival_p90"],
                    burn_prob=z["burn_prob"], source=d.get("spread_source", ""), horizon_min=int(d.get("horizon_min", 0)))
        for a in d["assets"]:
            if a.get("route") and a["route"].get("path_lonlat"):
                a["route"]["path_lonlat"] = [tuple(p) for p in a["route"]["path_lonlat"]]
        return cls(
            id=d["id"], cluster_id=d["cluster_id"], t=datetime.fromisoformat(d["t"]),
            config_hash=d["config_hash"], assets=d["assets"], destinations=d.get("destinations", []),
            queue=d.get("queue", []), change_log=d.get("change_log", []), arrival=arrival,
            closures=d.get("closures", []), spread_source=d.get("spread_source", ""),
            horizon_min=int(d.get("horizon_min", 0)),
        )


def _fmt(v) -> str:
    return "never" if v is None or (isinstance(v, float) and math.isinf(v)) else f"{v:.0f} min"


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")
