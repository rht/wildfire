"""Consumer side of the location snapshot: sequence guard, overrides, contact priority by remaining
evacuation window, queues.

CONTRACTS.md section 4 / readme.md section 6. Contact priority is the remaining evacuation window,
policy `config.CONTACT_POLICY` (`forecast-evacuation-window-v2`); the weighted proximity/size/value
score of v1.0 is gone. With every time converted to minutes from `now_at` (the snapshot `as_of` by
default):

    time_to_impact = fire_arrival_at - now_at
    latest_start   = fire_arrival_at - evacuation_min - buffer_min - now_at
    slack          = latest_start            (the remaining window, relative to now_at; negative allowed)

Ranked order is `slack_min` ascending, then earlier `fire_arrival_at`, then nearer known distance, then
`asset_id`. A zero or negative window stays at the top as `window_exhausted` (immediate analyst review,
not an evacuation instruction). A missing forecast, evacuation duration or provenance makes an unranked
review item; nothing is inferred from distance or headcount. A missing distance does not block ranking.
The arithmetic and sort key are shared with `contact_priority.rank_contacts` (readme 16). Nothing here
mutates its inputs.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from fireline import config
from fireline.contact_priority import contact_sort_key, missing_timing, window_arithmetic

OVERRIDE_CONFLICT = "override_conflict"
CAPACITY_PROXY = "capacity as proxy"
OCCUPANCY_FIELDS = ("estimated_occupancy", "capacity")
FORECAST_UNAVAILABLE = "forecast_unavailable"
EVACUATION_UNKNOWN = "evacuation_unknown"
OVERRIDE_FIELDS = ("estimated_occupancy", "capacity", "asset_type", "evacuation_min")
STATUS_OPEN, STATUS_EXHAUSTED, STATUS_REVIEW = "window_open", "window_exhausted", "needs_review"
ORDERING = "slack ascending; forecast arrival ascending; distance ascending; asset ID"


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
# Time helpers
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


def minutes_between(later, earlier) -> float | None:
    """`later - earlier` in minutes for ISO strings or datetimes; None when either is missing/invalid."""
    a, b = _parse_time(later), _parse_time(earlier)
    if a is None or b is None:
        return None
    return (a - b).total_seconds() / 60.0


def _fmt_min(value) -> str:
    if value is None:
        return "unknown"
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return f"{text} min"


# ---------------------------------------------------------------------------------------------
# Confirmed analyst overrides (from tasks.TaskStore.overrides())
# ---------------------------------------------------------------------------------------------

def _apply_one(asset: dict, override: dict, cfg) -> None:
    field = override["field"]
    value = override["value"]
    if field == "fire_arrival_at":
        raise ValueError("overrides on fire_arrival_at are not accepted: the forecast arrival comes from the "
                         "producer snapshot; confirm evacuation_min, occupancy, capacity or asset_type instead")
    if field not in OVERRIDE_FIELDS:
        raise ValueError(f"override field must be one of {list(OVERRIDE_FIELDS)}, got {field!r}")
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
    source_label = "analyst override: " + str(override.get("source") or "unspecified")
    entry = {
        "fields": [field],
        "source": source_label,
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
        try:
            score = policy[value]
        except KeyError:
            asset["value_score"] = None
            if "value_unknown" not in reasons:
                reasons.append("value_unknown")
        else:
            asset["value_score"] = score
            asset["value_basis"] = cfg.VALUE_POLICY["version"]
            reasons = [r for r in reasons if r not in ("class_ambiguous", "value_unknown")]
        # The class also selects the policy evacuation duration; an analyst-confirmed duration is kept.
        if not str(asset.get("evacuation_source") or "").startswith("analyst override"):
            total, version, note = _policy_evacuation(value, cfg)
            asset["evacuation_min"], asset["evacuation_source"] = total, version
            asset["sources"].insert(len(asset["sources"]) - 1, {      # before the override's own entry
                "fields": ["evacuation_min", "evacuation_source"], "source": "config.EVACUATION_POLICY",
                "observed_at": None, "available_at": override.get("confirmed_at"), "fetched_at": None,
                "notes": note or f"no evacuation policy for class {value!r} after asset_type override"})
            reasons = [r for r in reasons if r != EVACUATION_UNKNOWN]
            if total is None:
                reasons.append(EVACUATION_UNKNOWN)
    elif field == "evacuation_min":
        if value is not None:
            asset["evacuation_min"] = float(value)
            asset["evacuation_source"] = source_label
            reasons = [r for r in reasons if r != EVACUATION_UNKNOWN]
        else:
            asset["evacuation_source"] = None
            if EVACUATION_UNKNOWN not in reasons:
                reasons.append(EVACUATION_UNKNOWN)
    if conflict and OVERRIDE_CONFLICT not in reasons:
        reasons.append(OVERRIDE_CONFLICT)
    asset["review_reasons"] = reasons
    asset["needs_review"] = bool(reasons)


def _policy_evacuation(asset_type, cfg=config):
    """(evacuation_min, policy version, note) from cfg.EVACUATION_POLICY for a class; (None, None, None) when
    the class has no policy row. Mirrors the producer (snapshot._evacuation) so an asset_type override keeps
    the duration consistent with the confirmed class."""
    policy = cfg.EVACUATION_POLICY
    row = policy["by_type"].get(asset_type)
    if row is None:
        return None, None, None
    components = [c for c in policy["components"] if row.get(c) is not None]
    total = float(sum(float(row[c]) for c in components))
    breakdown = ", ".join(f"{c.removesuffix('_min')} {row[c]:g} min" for c in components)
    note = (f"policy {policy['version']} for class {asset_type} after asset_type override: {breakdown} = {total:g} min; "
            f"assumptions: {row.get('assumptions') or 'none stated'}; labelled prototype assumption, analyst override allowed")
    return total, policy["version"], note


def window_bucket(slack_min, cfg=config) -> str | None:
    """Coarse attention bucket of a remaining window: None (unranked), "exhausted" (<= 0), "small"
    (<= CONTACT_POLICY["attention_min"]) or "open". Task flagging and the map colour use it, so a window
    that merely shrinks with elapsed time does not flag every open task on every update."""
    if slack_min is None:
        return None
    if slack_min <= 0:
        return "exhausted"
    return "small" if slack_min <= cfg.CONTACT_POLICY.get("attention_min", 60) else "open"


def apply_overrides(assets: list[dict], overrides: list[dict] | None, cfg=config) -> list[dict]:
    """Return deep copies of `assets` with confirmed overrides applied in confirmed_at order.

    Each override sets `asset[field] = value`, appends an `analyst override: <source>` provenance
    entry and marks `occupancy_basis` when it touches occupancy or capacity. When the asset's own
    provider source for that field was observed after the override was confirmed the override is
    kept and `override_conflict` is added to `review_reasons` so the analyst sees the disagreement.
    An asset_type override re-derives value_score from the value policy. An evacuation_min override
    sets `evacuation_source = "analyst override: <source>"` and clears `evacuation_unknown`.
    Overrides on `fire_arrival_at` raise ValueError: forecasts come from the producer.
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
# Ranking by remaining evacuation window
# ---------------------------------------------------------------------------------------------

def _round9(value):
    return None if value is None else round(float(value), 9)


def rank_asset(asset: dict, now_at, cfg=config) -> dict:
    """Return a copy of `asset` with the coordination keys of CONTRACTS 4 added (priority_rank stays None).

    `now_at` is the epoch for every window quantity (an ISO string or datetime; the snapshot `as_of`
    in `rank_snapshot`). Keys added: priority_rank, queue, priority_status, time_to_impact_min,
    latest_start_min, slack_min, window_components, priority_policy_version, priority_reasons.
    Missing forecast or evacuation inputs -> queue needs_review with the timing keys null and
    `forecast_unavailable` / `evacuation_unknown` added to review_reasons when the producer did not.
    """
    policy = cfg.CONTACT_POLICY
    buffer_min = float(policy["buffer_min"])
    now = _parse_time(now_at)
    if now is None:
        raise ValueError(f"rank_asset needs a valid now_at (the snapshot as_of), got {now_at!r}")
    now_iso = now.isoformat()
    out = copy.deepcopy(asset)

    arrival_at = asset.get("fire_arrival_at")
    arrival_basis = asset.get("fire_arrival_basis")
    forecast_source = asset.get("forecast_source")
    horizon_at = asset.get("forecast_horizon_at")
    evacuation_min = asset.get("evacuation_min")
    evacuation_source = asset.get("evacuation_source")
    distance = asset.get("distance_to_fire_m")

    arrival_min = minutes_between(arrival_at, now) if arrival_at is not None else None
    evac_value = None if evacuation_min is None or isinstance(evacuation_min, bool) else float(evacuation_min)
    missing = missing_timing(arrival_min, evac_value, forecast_source, evacuation_source)
    # contact_priority names the static fields; map them onto the snapshot review reasons
    review_add = []
    if any(m in ("fire_arrival_min", "forecast_source") for m in missing):
        review_add.append(FORECAST_UNAVAILABLE)
    if any(m in ("evacuation_min", "evacuation_source") for m in missing):
        review_add.append(EVACUATION_UNKNOWN)

    reasons: list[str] = []
    if arrival_at is None:
        reasons.append("arrival unknown: fire_arrival_at is null, no forecast covers this location (forecast_unavailable)")
    elif arrival_min is None:
        reasons.append(f"arrival unusable: fire_arrival_at {arrival_at!r} is not a valid timestamp (forecast_unavailable)")
    else:
        provenance = f"{arrival_basis or 'basis unstated'}; forecast {forecast_source or 'source missing'}"
        if horizon_at:
            provenance += f", horizon {horizon_at}"
        reasons.append(f"arrival {arrival_at} ({provenance}): {_fmt_min(arrival_min)} after now {now_iso}")
        if not (forecast_source or "").strip():
            reasons.append("forecast provenance missing: forecast_source is null, arrival cannot be used (forecast_unavailable)")
    if evac_value is None:
        reasons.append("evacuation unknown: evacuation_min is null; confirm the total evacuation duration "
                       "(mobilisation + preparation/loading + movement) with the facility (evacuation_unknown)")
    else:
        reasons.append(f"evacuation {_fmt_min(evac_value)} ({evacuation_source or 'source missing'})")
        if not (evacuation_source or "").strip():
            reasons.append("evacuation provenance missing: evacuation_source is null (evacuation_unknown)")
    reasons.append(f"buffer {_fmt_min(buffer_min)} ({policy['version']})")

    components = {
        "fire_arrival_at": arrival_at,
        "fire_arrival_basis": arrival_basis,
        "forecast_source": forecast_source,
        "forecast_horizon_at": horizon_at,
        "now_at": now_iso,
        "evacuation_min": evac_value,
        "evacuation_source": evacuation_source,
        "buffer_min": buffer_min,
        "distance_to_fire_m": distance,
    }

    if missing:
        time_to_impact = latest_start = slack = None
        status, queue = STATUS_REVIEW, "needs_review"
        reasons.append("needs review: " + ", ".join(review_add) + "; not ranked")
    else:
        w = window_arithmetic(arrival_min, evac_value, 0.0, buffer_min)
        time_to_impact, latest_start, slack, status = (_round9(w["time_to_impact_min"]),
                                                       _round9(w["latest_start_min"]), w["slack_min"], w["status"])
        queue = "ranked"
        reasons.append(f"latest start {_fmt_min(latest_start)} after now = arrival {_fmt_min(arrival_min)} - "
                       f"evacuation {_fmt_min(evac_value)} - buffer {_fmt_min(buffer_min)}")
        if status == STATUS_EXHAUSTED:
            reasons.append(f"remaining window {_fmt_min(slack)}: window_exhausted (<= 0; immediate analyst review, "
                           f"not an evacuation instruction)")
        else:
            reasons.append(f"remaining window {_fmt_min(slack)}: window_open")
    if distance is None:
        reasons.append("distance to fire unknown: used only as a tie-breaker, does not block ranking")

    review_reasons = list(asset.get("review_reasons") or [])
    for r in review_add:
        if r not in review_reasons:
            review_reasons.append(r)
    for r in review_reasons:
        reasons.append(f"review flag: {r}")

    out.update({
        "review_reasons": review_reasons,
        "needs_review": bool(review_reasons),
        "priority_rank": None,
        "queue": queue,
        "priority_status": status,
        "time_to_impact_min": time_to_impact,
        "latest_start_min": latest_start,
        "slack_min": slack,
        "window_components": components,
        "priority_policy_version": policy["version"],
        "priority_reasons": reasons,
    })
    return out


def ranked_sort_key(asset: dict):
    """Shared ordering with contact_priority: slack, arrival, known distance (null last), asset_id."""
    return contact_sort_key(asset["slack_min"], asset["time_to_impact_min"], asset.get("distance_to_fire_m"),
                            asset["asset_id"])


def _needs_review_key(asset: dict):
    distance = asset.get("distance_to_fire_m")
    return (0 if distance is None else 1, float(distance) if distance is not None else 0.0, asset["asset_id"])


def rank_snapshot(snap: dict, cfg=config, overrides: list[dict] | None = None, now_at=None) -> dict:
    """Rank every asset of a snapshot (after applying `overrides`) by remaining evacuation window.

    `now_at` defaults to `snap["as_of"]` (CONTACT_POLICY["now"]). Returns `{"ranked", "needs_review",
    "flagged", "all", "now_at", "policy"}`: `ranked` sorted by slack ascending, then arrival, then known
    distance, then asset_id with 1-based `priority_rank`; `needs_review` with unknown exposure first,
    then ascending distance, then asset_id; `flagged` = the ranked assets that still carry review
    reasons (shown in the review view too); `all` = ranked followed by needs_review (same objects).
    """
    if now_at is None:
        now_at = snap.get("as_of")
    now = _parse_time(now_at)
    if now is None:
        raise ValueError(f"rank_snapshot needs now_at or a valid snapshot as_of, got {now_at!r}")
    assets = apply_overrides(snap.get("assets") or [], overrides, cfg)
    scored = [rank_asset(a, now, cfg) for a in assets]
    ranked = sorted((a for a in scored if a["queue"] == "ranked"), key=ranked_sort_key)
    for i, a in enumerate(ranked, start=1):
        a["priority_rank"] = i
    needs_review = sorted((a for a in scored if a["queue"] != "ranked"), key=_needs_review_key)
    flagged = [a for a in ranked if a.get("review_reasons")]
    policy = dict(cfg.CONTACT_POLICY)
    policy["ordering"] = ORDERING
    policy["evacuation_policy_version"] = cfg.EVACUATION_POLICY["version"]
    return {"ranked": ranked, "needs_review": needs_review, "flagged": flagged, "all": ranked + needs_review,
            "now_at": now.isoformat(), "policy": policy}


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
