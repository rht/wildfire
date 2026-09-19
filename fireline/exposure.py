"""Asset table: sample arrival rasters at assets, tier them, flag edge cases (PLAN 6.3).

Contract in CONTRACTS.md. Rows are plain dicts. Deterministic edge cases are resolved here with the
pessimistic answer and a human-readable note; cases that need outside evidence get a `needs_review`
code and are handed to the agent (PLAN 6.6).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from . import config

TIERS = ("act_now", "prepare", "monitor")
TIER_RANK = {t: i for i, t in enumerate(TIERS)}
REVIEW_CODES = ("occupancy_unknown", "occupancy_seasonal", "class_ambiguous", "no_exit")
OVERRIDABLE_FIELDS = ("occupancy", "tier", "shelter_viable")


class OptimisticMoveError(ValueError):
    """Raised when an override would move an asset in the optimistic direction."""


def _finite_or_inf(v) -> float:
    """Raster sample -> float; NaN (outside grid) becomes inf for arrivals."""
    v = float(v)
    return math.inf if math.isnan(v) else v


def _sample_point(arrival, lon: float, lat: float) -> dict:
    bp = float(arrival.sample("burn_prob", lon, lat))
    outside = math.isnan(bp)
    return {
        "burn_prob": 0.0 if outside else bp,
        "arrival_p10_min": _finite_or_inf(arrival.sample("arrival_p10", lon, lat)),
        "arrival_p50_min": _finite_or_inf(arrival.sample("arrival_p50", lon, lat)),
        "outside_grid": outside,
    }


def tier_for(lead_adjusted_p10: float, burn_prob: float, cfg=config) -> str:
    if burn_prob >= cfg.BURN_PROB_MIN and lead_adjusted_p10 <= cfg.ACT_NOW_MIN:
        return "act_now"
    if burn_prob >= cfg.BURN_PROB_MIN and lead_adjusted_p10 <= cfg.PREPARE_MIN:
        return "prepare"
    return "monitor"


def build_asset_table(assets_in: list[dict], arrival, cfg=config) -> list[dict]:
    """One row per input asset, sampled on `arrival` (spread.ArrivalRaster). Order preserved."""
    rows = []
    for a in assets_in:
        cls = a["asset_class"]
        if cls not in cfg.LEAD_TIME_MIN:
            raise ValueError(f"{a.get('asset_id')}: unknown asset_class {cls!r}")
        notes: list[str] = []
        needs_review: list[str] = []

        lon, lat = float(a["lon"]), float(a["lat"])
        s = _sample_point(arrival, lon, lat)
        # Location conflict (Gencat vs OSM coordinates): use the location with the earlier arrival.
        if a.get("alt_lon") is not None and a.get("alt_lat") is not None:
            alt_lon, alt_lat = float(a["alt_lon"]), float(a["alt_lat"])
            s_alt = _sample_point(arrival, alt_lon, alt_lat)
            chosen = "primary"
            if s_alt["arrival_p10_min"] < s["arrival_p10_min"] or (
                s_alt["arrival_p10_min"] == s["arrival_p10_min"] and s_alt["burn_prob"] > s["burn_prob"]
            ):
                s, lon, lat, chosen = s_alt, alt_lon, alt_lat, "alternate"
            notes.append(
                f"location conflict: primary ({a['lon']:.4f}, {a['lat']:.4f}) p10 "
                f"{_fmt_min(_sample_point(arrival, float(a['lon']), float(a['lat']))['arrival_p10_min'])}"
                f" vs alternate ({alt_lon:.4f}, {alt_lat:.4f}) p10 {_fmt_min(s_alt['arrival_p10_min'])};"
                f" using {chosen} (earlier arrival)"
            )
        if s["outside_grid"]:
            notes.append("outside modelled grid: burn_prob set to 0, arrival never")

        occupancy = a.get("occupancy")
        occupancy = int(occupancy) if occupancy is not None else None
        occ_source = a.get("occupancy_source") or ("unknown" if occupancy is None else "register")
        if occupancy is None:
            needs_review.append("occupancy_unknown")
        if a.get("seasonal"):
            needs_review.append("occupancy_seasonal")
        if a.get("class_ambiguous"):
            needs_review.append("class_ambiguous")

        lead = int(cfg.LEAD_TIME_MIN[cls])
        lead_adj = s["arrival_p10_min"] - lead  # inf stays inf
        rows.append({
            "asset_id": a["asset_id"],
            "name": a["name"],
            "asset_class": cls,
            "lon": lon,
            "lat": lat,
            "municipality": a.get("municipality", ""),
            "occupancy": occupancy,
            "occupancy_source": occ_source,
            # provenance of the occupancy figure when it does not come from the identity register
            # (schools: the enrolment register); absent for the usual same-register case
            **{k: a[k] for k in ("occupancy_register", "occupancy_period") if a.get(k) is not None},
            "shelter_viable": a.get("shelter_viable"),   # None -> decide.py applies the class default
            "burn_prob": s["burn_prob"],
            "arrival_p10_min": s["arrival_p10_min"],
            "arrival_p50_min": s["arrival_p50_min"],
            "lead_time_min": lead,
            "lead_adjusted_p10_min": lead_adj,
            "tier": tier_for(lead_adj, s["burn_prob"], cfg),
            "needs_review": needs_review,
            "notes": notes,
            "overrides": [],
        })
    return rows


def _fmt_min(v: float) -> str:
    return "never" if math.isinf(v) else f"{v:.0f} min"


def apply_override(asset: dict, override: dict) -> dict:
    """Apply a pessimistic-only override in place and record it on the asset.

    override: {field, value, source?, quoted_snippet?, fetched_at?, confidence?}.
    Allowed moves: occupancy up, tier towards act_now, shelter_viable -> False. Anything else raises
    OptimisticMoveError. A no-op (same value) is allowed and recorded with direction "same".
    """
    field = override.get("field")
    value = override.get("value")
    if field not in OVERRIDABLE_FIELDS:
        raise OptimisticMoveError(
            f"field {field!r} cannot be overridden (allowed: {', '.join(OVERRIDABLE_FIELDS)})")
    current = asset.get(field)

    if field == "occupancy":
        if value is None:
            raise OptimisticMoveError("occupancy override must be a number")
        value = int(value)
        if current is not None and value < current:
            raise OptimisticMoveError(f"occupancy may only increase ({current} -> {value} refused)")
        direction = "same" if current == value else "pessimistic"
    elif field == "tier":
        if value not in TIER_RANK:
            raise OptimisticMoveError(f"unknown tier {value!r}")
        if current in TIER_RANK and TIER_RANK[value] > TIER_RANK[current]:
            raise OptimisticMoveError(f"tier may only move towards act_now ({current} -> {value} refused)")
        direction = "same" if current == value else "pessimistic"
    else:  # shelter_viable
        if value is not False:
            raise OptimisticMoveError("shelter_viable may only be overridden to False")
        direction = "same" if current is False else "pessimistic"

    record = {
        "field": field,
        "value": value,
        "previous": current,
        "source": override.get("source", "unknown"),
        "quoted_snippet": override.get("quoted_snippet", ""),
        "fetched_at": override.get("fetched_at") or datetime.now(timezone.utc).isoformat(),
        "confidence": override.get("confidence", "low"),
        "direction": direction,
    }
    asset[field] = value
    asset.setdefault("overrides", []).append(record)
    if field == "occupancy":
        asset["occupancy_source"] = "override"
        if "occupancy_unknown" in asset.get("needs_review", []):
            asset["needs_review"].remove("occupancy_unknown")
    return asset
