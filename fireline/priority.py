"""Consumer side of the location snapshot: sequence guard, overrides, explainable priority, queues.

CONTRACTS.md section 4 / readme.md section 6. Priority is for analyst attention, not physical risk:
`score = w_proximity * proximity + w_size * size + w_value * value` with every component normalised to
[0, 1] by the fixed scales in `config.PRIORITY_POLICY`. Any required component unknown -> score null
and the asset goes to the needs-review queue. Nothing here mutates its inputs.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from fireline import config

OVERRIDE_CONFLICT = "override_conflict"
CAPACITY_PROXY = "capacity as proxy"
OCCUPANCY_FIELDS = ("estimated_occupancy", "capacity")


# ---------------------------------------------------------------------------------------------
# Sequence guard (readme 5.1: ignore duplicate ids and lower/equal sequences within a scenario)
# ---------------------------------------------------------------------------------------------

class SnapshotSequence:
    """Per-scenario monotonic guard. `accept` returns False for a duplicate snapshot_id or a sequence
    that is not strictly greater than the last accepted one for that scenario_id."""

    def __init__(self):
        self._last: dict[str, dict] = {}       # scenario_id -> {"sequence", "snapshot_id"}
        self._seen: dict[str, set] = {}        # scenario_id -> accepted snapshot ids

    def record(self, scenario_id: str, sequence: int, snapshot_id: str | None) -> None:
        """Seed the guard from persisted state (e.g. tasks.TaskStore after reload)."""
        self._last[scenario_id] = {"sequence": int(sequence), "snapshot_id": snapshot_id}
        if snapshot_id is not None:
            self._seen.setdefault(scenario_id, set()).add(snapshot_id)

    def mark_seen(self, scenario_id: str, snapshot_id: str) -> None:
        """Remember an already-accepted id without moving the sequence (replay protection after reload)."""
        self._seen.setdefault(scenario_id, set()).add(snapshot_id)

    def last(self, scenario_id: str) -> dict | None:
        return dict(self._last[scenario_id]) if scenario_id in self._last else None

    def reject_reason(self, snap: dict) -> str | None:
        scenario_id = snap["scenario_id"]
        snapshot_id = snap["snapshot_id"]
        sequence = snap["sequence"]
        if snapshot_id in self._seen.get(scenario_id, ()):
            return f"duplicate snapshot_id {snapshot_id}"
        last = self._last.get(scenario_id)
        if last is not None and sequence <= last["sequence"]:
            return f"sequence {sequence} <= last accepted {last['sequence']} ({last['snapshot_id']})"
        return None

    def accept(self, snap: dict) -> bool:
        if self.reject_reason(snap) is not None:
            return False
        self.record(snap["scenario_id"], snap["sequence"], snap["snapshot_id"])
        return True


# ---------------------------------------------------------------------------------------------
# Confirmed analyst overrides (from tasks.TaskStore.overrides())
# ---------------------------------------------------------------------------------------------

def _parse_time(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _apply_one(asset: dict, override: dict, cfg) -> None:
    field = override["field"]
    value = override["value"]
    confirmed_at = _parse_time(override.get("confirmed_at"))
    # Conflict: a provider value for the same field observed after the analyst confirmed the override.
    conflict = False
    for entry in asset.get("sources") or []:
        if field not in (entry.get("fields") or []):
            continue
        if str(entry.get("source", "")).startswith("analyst override"):
            continue
        observed = _parse_time(entry.get("observed_at"))
        if observed is not None and confirmed_at is not None and observed > confirmed_at:
            conflict = True
    asset[field] = value
    entry = {
        "fields": [field],
        "source": "analyst override: " + str(override.get("source") or "unspecified"),
        "observed_at": override.get("observed_at"),
        "available_at": override.get("confirmed_at"),
        "fetched_at": override.get("confirmed_at"),
        "notes": "; ".join(s for s in (
            f"override {override.get('override_id')}" if override.get("override_id") else None,
            f"previous {override.get('previous')!r}",
            f"confidence {override.get('confidence')}" if override.get("confidence") is not None else None,
            f"snippet: {override.get('snippet')}" if override.get("snippet") else None,
            override.get("url"),
        ) if s),
    }
    asset["sources"] = list(asset.get("sources") or []) + [entry]
    reasons = list(asset.get("review_reasons") or [])
    if field in OCCUPANCY_FIELDS:
        asset["occupancy_basis"] = "analyst override"
        if value is not None and field == "estimated_occupancy":
            reasons = [r for r in reasons if r not in ("occupancy_unknown", "occupancy_seasonal")]
    elif field == "asset_type":
        policy = cfg.VALUE_POLICY["by_type"]
        if value in policy:
            asset["value_score"] = policy[value]
            asset["value_basis"] = cfg.VALUE_POLICY["version"]
            reasons = [r for r in reasons if r not in ("class_ambiguous", "value_unknown")]
        else:
            asset["value_score"] = None
            if "value_unknown" not in reasons:
                reasons.append("value_unknown")
    if conflict and OVERRIDE_CONFLICT not in reasons:
        reasons.append(OVERRIDE_CONFLICT)
    asset["review_reasons"] = reasons
    asset["needs_review"] = bool(reasons)


def apply_overrides(assets: list[dict], overrides: list[dict] | None, cfg=config) -> list[dict]:
    """Return deep copies of `assets` with confirmed overrides applied in confirmed_at order.

    Each override sets `asset[field] = value`, appends an `analyst override: <source>` provenance
    entry and marks `occupancy_basis` when it touches occupancy or capacity. When the asset's own
    provider source for that field was observed after the override was confirmed the override is
    kept and `override_conflict` is added to `review_reasons` so the analyst sees the disagreement.
    An asset_type override re-derives value_score from the value policy.
    """
    copies = [copy.deepcopy(a) for a in assets]
    if not overrides:
        return copies
    by_asset: dict[str, list[dict]] = {}
    for o in overrides:
        by_asset.setdefault(o["asset_id"], []).append(o)
    for asset in copies:
        for override in sorted(by_asset.get(asset["asset_id"], []),
                               key=lambda o: (o.get("confirmed_at") or "", o.get("override_id") or "")):
            _apply_one(asset, override, cfg)
    return copies


# ---------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------

def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _proximity(asset: dict, policy: dict) -> tuple[float | None, dict, str | None, str]:
    distance = asset.get("distance_to_fire_m")
    intersects = asset.get("intersects_fire")
    scale = policy["proximity_scale_m"]
    inputs = {"distance_to_fire_m": distance, "intersects_fire": intersects}
    if intersects is True:
        return 1.0, inputs, None, "proximity 1.00 (intersects fire)"
    if distance is None:
        return None, inputs, None, "proximity unknown: distance to fire is null"
    value = _clip01(1.0 - float(distance) / float(scale))
    return value, inputs, None, f"proximity {value:.2f} ({float(distance):.0f} m of {scale} m scale)"


def _size(asset: dict, policy: dict) -> tuple[float | None, dict, str | None, str]:
    occupancy = asset.get("estimated_occupancy")
    capacity = asset.get("capacity")
    scale = policy["size_scale_people"]
    inputs = {"estimated_occupancy": occupancy, "capacity": capacity}
    if occupancy is not None:
        value = _clip01(float(occupancy) / float(scale))
        basis = asset.get("occupancy_basis")
        suffix = f", {basis}" if basis else ""
        return value, inputs, None, f"size {value:.2f} from estimated occupancy {occupancy}{suffix}"
    if capacity is not None and policy.get("size_capacity_proxy", False):
        value = _clip01(float(capacity) / float(scale))
        return value, inputs, CAPACITY_PROXY, f"size {value:.2f} from capacity {capacity} (proxy)"
    if capacity is not None:
        return None, inputs, None, "size unknown: estimated occupancy null and capacity proxy disabled"
    return None, inputs, None, "size unknown: estimated occupancy and capacity are null"


def _value(asset: dict, cfg) -> tuple[float | None, dict, str | None, str]:
    score = asset.get("value_score")
    asset_type = asset.get("asset_type")
    inputs = {"asset_type": asset_type, "value_score": score, "value_basis": asset.get("value_basis")}
    if score is None:
        return None, inputs, None, f"value unknown: asset_type {asset_type!r} has no value policy entry"
    basis = asset.get("value_basis") or cfg.VALUE_POLICY["version"]
    return _clip01(score), inputs, None, f"value {float(score):.2f} ({asset_type}, {basis})"


def score_asset(asset: dict, cfg=config) -> dict:
    """Return a copy of `asset` with the six coordination keys added (priority_rank stays None here)."""
    policy = cfg.PRIORITY_POLICY
    weights = policy["weights"]
    out = copy.deepcopy(asset)
    parts = {
        "proximity": _proximity(asset, policy),
        "size": _size(asset, policy),
        "value": _value(asset, cfg),
    }
    components = {}
    reasons = []
    for name, (value, inputs, proxy, text) in parts.items():
        components[name] = {"value": value, "weight": weights[name], "input": inputs, "proxy": proxy}
        reasons.append(text)
    if all(c["value"] is not None for c in components.values()):
        score = round(sum(c["weight"] * c["value"] for c in components.values()), 4)
        queue = "ranked"
        reasons.append("score {:.4f} = ".format(score) + " + ".join(
            f"{c['weight']}*{c['value']:.2f}" for c in components.values()))
    else:
        score = None
        queue = "needs_review"
        missing = [n for n, c in components.items() if c["value"] is None]
        reasons.append("needs review: " + ", ".join(missing) + " unknown; not scored")
    for r in asset.get("review_reasons") or []:
        reasons.append(f"review flag: {r}")
    out.update({
        "priority_score": score,
        "priority_rank": None,
        "queue": queue,
        "score_components": components,
        "priority_policy_version": policy["version"],
        "priority_reasons": reasons,
    })
    return out


def _needs_review_key(asset: dict):
    distance = asset.get("distance_to_fire_m")
    return (0 if distance is None else 1, float(distance) if distance is not None else 0.0, asset["asset_id"])


def score_snapshot(snap: dict, cfg=config, overrides: list[dict] | None = None) -> dict:
    """Score every asset of a snapshot (after applying `overrides`) and split into queues.

    Returns `{"ranked", "needs_review", "flagged", "all"}`: `ranked` sorted by score desc then
    asset_id with 1-based `priority_rank`; `needs_review` with unknown exposure first, then ascending
    distance, then asset_id; `flagged` = the ranked assets that still carry review reasons (shown in
    the review view too); `all` = ranked followed by needs_review (same objects).
    """
    assets = apply_overrides(snap.get("assets") or [], overrides, cfg)
    scored = [score_asset(a, cfg) for a in assets]
    ranked = sorted((a for a in scored if a["priority_score"] is not None),
                    key=lambda a: (-a["priority_score"], a["asset_id"]))
    for i, a in enumerate(ranked, start=1):
        a["priority_rank"] = i
    needs_review = sorted((a for a in scored if a["priority_score"] is None), key=_needs_review_key)
    flagged = [a for a in ranked if a.get("review_reasons")]
    return {"ranked": ranked, "needs_review": needs_review, "flagged": flagged, "all": ranked + needs_review}


# ---------------------------------------------------------------------------------------------
# Input age for the UI (readme 6: show input age; recalculation never makes stale data fresh)
# ---------------------------------------------------------------------------------------------

def input_age(asset: dict, now: datetime | None = None) -> dict:
    """Oldest `observed_at` and newest `fetched_at` across the asset's sources, plus ages in seconds
    when `now` is given. Missing times stay null."""
    observed = [t for t in (_parse_time(s.get("observed_at")) for s in asset.get("sources") or []) if t]
    fetched = [t for t in (_parse_time(s.get("fetched_at")) for s in asset.get("sources") or []) if t]
    oldest = min(observed) if observed else None
    newest = max(fetched) if fetched else None
    now = _parse_time(now)
    return {
        "oldest_observed_at": oldest.isoformat() if oldest else None,
        "newest_fetched_at": newest.isoformat() if newest else None,
        "oldest_observed_age_s": (now - oldest).total_seconds() if now and oldest else None,
        "newest_fetched_age_s": (now - newest).total_seconds() if now and newest else None,
    }
