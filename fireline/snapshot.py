"""Snapshot producer: location assessment records for one fire update (CONTRACTS.md section 2).

`build_snapshot` turns asset rows (v0 `assets_in` shape or v4 records) plus one FireUpdate-shaped
dict into the shared snapshot envelope. Exposure is geometric only: minimum distance in EPSG:25831
between the asset footprint (or its representative point, labelled) and the fire geometry.

v1.1 timing fields (CONTRACTS 2.2, readme 6): `fire_arrival_at` / `fire_arrival_basis` come only from a
forecast passed as `forecast=` (a `forecast_input` forecast-input-1 dict) or, behind
`config.FEATURES["forecast_enrichment"]`, from an arrival raster labelled as unvalidated enrichment.
They are never derived from distance: an asset no forecast covers keeps them null and carries
`forecast_unavailable`. `evacuation_min` / `evacuation_source` come from `config.EVACUATION_POLICY`
by class (component minutes and assumptions recorded in `sources`); an unknown class gives null and
`evacuation_unknown`.

Occupancy follows the input row's `occupancy_source`: `register` fills `capacity` only (maximum
places, never a headcount), `enrolment` (schools, from the Gencat enrolment register) and the other
headcount sources fill `estimated_occupancy`. `occupancy_register` / `occupancy_period` /
`occupancy_fetched_at` on the row, when the figure comes from a different register than the
identity fields, are recorded in the `sources` entry for the occupancy fields.

Behind `config.FEATURES["value_at_risk"]` each record also carries the value-at-risk layer of handoff 002:
`replacement_value_eur` / `replacement_value_basis` (an assumed per-class figure from
`config.VALUE_AT_RISK_POLICY`), `people_exposed`, `people_at_risk_p50` / `people_at_risk_p10` and
`expected_loss_eur_low` / `_mid` / `_high`. The eight keys are an optional flagged extension: with the flag
off they are absent and the record is unchanged. Every one of them is `null`, never zero, when an input is
null; `burn_probability = 0.0` is a value, not a null, and gives zeros.

Schema version `1.1`; `validate_snapshot` still accepts `1.0` files, whose v1.1 keys are optional.
This module never imports `fire_input`; the `fire` argument is a plain dict (or None).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import transform as shapely_transform

from . import config
from .contact_priority import window_arithmetic
from .forecast_input import FORECAST_UNAVAILABLE, attach_forecast
from .grid import lonlat_to_xy

SCHEMA_VERSION = "1.1"
READABLE_SCHEMA_VERSIONS = ("1.0", "1.1")   # 1.0 files load and validate; their v1.1 keys are optional
INPUT_MODES = ("live", "recorded", "synthetic")
DATA_STATUSES = ("current", "stale", "unavailable")
GEOMETRY_KINDS = ("perimeter", "hotspot_centre", "simulated")   # simulated: model burned area, not observed
REVIEW_REASONS = ("location_unknown", "occupancy_unknown", "occupancy_seasonal", "class_ambiguous",
                  "value_unknown", "exposure_unknown", "forecast_unavailable", "evacuation_unknown",
                  "criticality_unassessed", "valuation_unassessed", "llm_assessment_failed",
                  "llm_assessment_unavailable", "llm_assessment_needs_review",
                  "llm_evacuation_context_missing")
EVACUATION_UNKNOWN = "evacuation_unknown"
POINT_FALLBACK_NOTE = "point fallback: facility footprint missing"
HOTSPOT_NOTE = "fire geometry is a hotspot centre, not a surveyed perimeter"
SIMULATED_NOTE = "fire geometry is a simulated burned area, not an observed perimeter"
NO_FIRE_NOTE = "fire geometry unavailable"
NO_LOCATION_NOTE = "asset location unknown"

ENVELOPE_KEYS = ("schema_version", "scenario_id", "incident_id", "snapshot_id", "sequence", "as_of",
                 "computed_at", "input_mode", "fire_observed_at", "fire_source", "fire_geometry",
                 "fire_geometry_kind", "data_status", "metrics", "assets")
ASSET_KEYS = ("asset_id", "name", "asset_type", "latitude", "longitude", "geometry", "area_m2",
              "capacity", "estimated_occupancy", "occupancy_basis", "value_score", "value_basis",
              "distance_to_fire_m", "intersects_fire", "burn_probability", "arrival_p10_at",
              "arrival_p50_at", "forecast_horizon_at", "forecast_source", "fire_arrival_at",
              "fire_arrival_basis", "evacuation_min", "evacuation_source", "needs_review",
              "review_reasons", "sources", "municipality", "criticality_tier", "criticality_factors",
              "criticality_basis", "custom_value_eur_low", "custom_value_eur_mid", "custom_value_eur_high",
              "custom_value_method", "custom_value_components", "custom_value_basis")
TIMING_KEYS = ("fire_arrival_at", "fire_arrival_basis", "evacuation_min", "evacuation_source")   # v1.1
# Per-asset criticality (config.CRITICALITY_POLICY). Optional like TIMING_KEYS: a snapshot written
# before the layer existed stays valid without them. The producer never sets a tier - it is proposed
# by the agent and written by an analyst-confirmed override (fireline/priority.py).
CRITICALITY_KEYS = ("criticality_tier", "criticality_factors", "criticality_basis")
CRITICALITY_UNASSESSED = "criticality_unassessed"
# Per-asset custom valuation (config.CUSTOM_VALUATION_POLICY). Optional like CRITICALITY_KEYS, and
# written the same way: the producer never asserts a figure, it arrives only through an
# analyst-confirmed override (fireline/priority.py). `custom_value_eur_mid`, when set, replaces the
# class replacement cost in `derive_value_at_risk` - and nothing else, because no sort key reads it.
CUSTOM_VALUATION_KEYS = ("custom_value_eur_low", "custom_value_eur_mid", "custom_value_eur_high",
                         "custom_value_method", "custom_value_components", "custom_value_basis")
VALUATION_UNASSESSED = "valuation_unassessed"
# Optional flagged extension (FEATURES["value_at_risk"]): all eight present together or all absent.
VALUE_AT_RISK_KEYS = ("replacement_value_eur", "replacement_value_basis", "expected_loss_eur_low",
                      "expected_loss_eur_mid", "expected_loss_eur_high", "people_exposed",
                      "people_at_risk_p50", "people_at_risk_p10")
SOURCE_KEYS = ("fields", "source", "observed_at", "available_at", "fetched_at", "notes")
_COMPUTED_FIELDS = {"value_score", "value_basis", "distance_to_fire_m", "intersects_fire",
                    "burn_probability", "arrival_p10_at", "arrival_p50_at", "forecast_horizon_at",
                    "forecast_source", "fire_arrival_at", "fire_arrival_basis", "evacuation_min",
                    "evacuation_source", "criticality_tier", "criticality_factors",
                    "criticality_basis", *CUSTOM_VALUATION_KEYS, *VALUE_AT_RISK_KEYS}


# ------------------------------------------------------------------------------------------ time
def _utc(t) -> datetime | None:
    """ISO string or datetime -> tz-aware UTC datetime; None stays None."""
    if t is None:
        return None
    if isinstance(t, str):
        s = t.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        return t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _iso(t) -> str | None:
    t = _utc(t)
    return None if t is None else t.isoformat()


def freshness(observed_at, as_of, cfg=config) -> str:
    """`current` / `stale` / `unavailable` from the observation age at `as_of` (config.FRESHNESS)."""
    obs, now = _utc(observed_at), _utc(as_of)
    if obs is None or now is None:
        return "unavailable"
    age = (now - obs).total_seconds()
    if age >= cfg.FRESHNESS["unavailable_after_s"]:
        return "unavailable"
    if age >= cfg.FRESHNESS["stale_after_s"]:
        return "stale"
    return "current"


# -------------------------------------------------------------------------------------- geometry
def _to_25831(geojson: dict):
    """GeoJSON geometry (WGS84) -> shapely geometry in EPSG:25831."""
    return shapely_transform(lonlat_to_xy, shape(geojson))


def asset_exposure(lon, lat, geometry, fire_geometry):
    """Minimum distance (m, EPSG:25831) from the asset to the fire geometry.

    Returns `(distance_m, intersects, note)`. Overlap gives `(0.0, True, ...)`. The footprint is used
    when `geometry` (GeoJSON) is given; otherwise the representative point with a point-fallback
    note. Missing fire geometry or missing location gives `(None, None, note)`. A Point fire
    geometry (hotspot centre) still yields a distance but the note says it is not a perimeter.
    """
    if fire_geometry is None:
        return None, None, NO_FIRE_NOTE
    notes = []
    if geometry is not None:
        asset_geom = _to_25831(geometry)
    elif lon is not None and lat is not None:
        x, y = lonlat_to_xy(float(lon), float(lat))
        asset_geom = Point(x, y)
        notes.append(POINT_FALLBACK_NOTE)
    else:
        return None, None, NO_LOCATION_NOTE
    fire_geom = _to_25831(fire_geometry)
    if fire_geom.is_empty or asset_geom.is_empty:
        return None, None, NO_FIRE_NOTE if fire_geom.is_empty else NO_LOCATION_NOTE
    if fire_geom.geom_type == "Point" or fire_geometry.get("type") == "Point":
        notes.append(HOTSPOT_NOTE)
    intersects = bool(asset_geom.intersects(fire_geom))
    distance = 0.0 if intersects else float(asset_geom.distance(fire_geom))
    return round(distance, 1), intersects, ("; ".join(notes) or None)


def _plain_geojson(geojson):
    """GeoJSON geometry with tuple coordinates (e.g. shapely.mapping) -> nested lists, JSON-identical."""
    if geojson is None:
        return None

    def plain(c):
        return [plain(x) for x in c] if isinstance(c, (list, tuple)) else c

    out = {"type": geojson["type"]}
    if "coordinates" in geojson:
        out["coordinates"] = plain(geojson["coordinates"])
    if geojson.get("type") == "GeometryCollection":
        out["geometries"] = [_plain_geojson(g) for g in geojson.get("geometries", [])]
    return out


def _footprint_area_m2(geometry) -> float | None:
    if geometry is None:
        return None
    try:
        g = _to_25831(geometry)
    except Exception:
        return None
    return round(float(g.area), 1) if g.geom_type in ("Polygon", "MultiPolygon") else None


# ----------------------------------------------------------------------------------- asset rows
def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _int(v):
    f = _num(v)
    return None if f is None else int(round(f))


def _register_of(row: dict) -> str:
    reg = row.get("register")
    if reg:
        return str(reg)
    aid = str(row.get("asset_id", ""))
    return aid.split(":", 1)[0] if ":" in aid else "unknown"


def _source_entry(fields, source, *, observed_at=None, available_at=None, fetched_at=None, notes=None) -> dict:
    return {"fields": list(fields), "source": source, "observed_at": _iso(observed_at),
            "available_at": _iso(available_at), "fetched_at": _iso(fetched_at), "notes": notes}


def _from_v0(row: dict) -> dict:
    """v0 assets_in / asset-table row -> partial v4 record (identity, location, occupancy, flags)."""
    review_list = row.get("needs_review") if isinstance(row.get("needs_review"), list) else []
    occ = _int(row.get("occupancy"))
    occ_src = (row.get("occupancy_source") or ("unknown" if occ is None else "register")).lower()
    register = _register_of(row)
    occ_register = row.get("occupancy_register") or register
    capacity = estimated = basis = None
    occ_note = None
    if occ is not None:
        if occ_src == "register":
            capacity = occ
            basis = f"register capacity ({register}); maximum places, not a headcount"
            occ_note = "register capacity: not a confirmed headcount; estimated_occupancy left null"
        elif occ_src == "enrolment":
            estimated = occ
            period = row.get("occupancy_period")
            basis = f"enrolled pupils{' ' + str(period) if period else ''} ({occ_register}); staff not included"
            occ_note = ("enrolment for the school year: not a time-of-day headcount, and staff and "
                        "visitors are not counted")
        elif occ_src in ("allocated", "headcount", "census", "estimate"):
            estimated = occ
            basis = f"{occ_src} headcount ({occ_register})"
            occ_note = f"{occ_src} estimate of people present, not a register capacity"
        elif occ_src == "override":
            estimated = occ
            basis = "analyst override"
            occ_note = "analyst-confirmed value"
        else:
            estimated = occ
            basis = f"{occ_src} ({occ_register})"
    return {
        "asset_id": row["asset_id"],
        "name": row.get("name"),
        "asset_type": row.get("asset_class") or "unknown",
        "latitude": _num(row.get("lat")),
        "longitude": _num(row.get("lon")),
        "geometry": _plain_geojson(row.get("geometry")),
        "area_m2": _num(row.get("area_m2")),
        "capacity": capacity,
        "estimated_occupancy": estimated,
        "occupancy_basis": basis,
        "municipality": row.get("municipality") or None,
        "_seasonal": bool(row.get("seasonal")) or "occupancy_seasonal" in review_list,
        "_class_ambiguous": bool(row.get("class_ambiguous")) or "class_ambiguous" in review_list,
        "_register": register,
        "_occ_register": occ_register,
        "_occ_fetched_at": row.get("occupancy_fetched_at") or row.get("fetched_at"),
        "_fetched_at": row.get("fetched_at"),
        "_observed_at": row.get("observed_at"),
        "_note": row.get("note"),
        "_occ_note": occ_note,
        "_sources": None,
    }


def _from_v4(row: dict) -> dict:
    reasons = row.get("review_reasons") or []
    kept = [s for s in (row.get("sources") or []) if not (set(s.get("fields") or []) & _COMPUTED_FIELDS)]
    return {
        "asset_id": row["asset_id"],
        "name": row.get("name"),
        "asset_type": row.get("asset_type") or "unknown",
        "latitude": _num(row.get("latitude")),
        "longitude": _num(row.get("longitude")),
        "geometry": _plain_geojson(row.get("geometry")),
        "area_m2": _num(row.get("area_m2")),
        "capacity": _int(row.get("capacity")),
        "estimated_occupancy": _int(row.get("estimated_occupancy")),
        "occupancy_basis": row.get("occupancy_basis"),
        "municipality": row.get("municipality"),
        "_seasonal": "occupancy_seasonal" in reasons,
        "_class_ambiguous": "class_ambiguous" in reasons,
        "_register": _register_of(row),
        "_occ_register": _register_of(row),
        "_occ_fetched_at": None,
        "_fetched_at": None,
        "_observed_at": None,
        "_note": None,
        "_occ_note": None,
        "_sources": kept,
    }


def _is_v4(row: dict) -> bool:
    return "asset_type" in row or "latitude" in row or "capacity" in row


def asset_record(row: dict, cfg=config) -> dict:
    """Static part of a v4 asset record (no fire, no forecast): identity, occupancy, value, provenance.
    Accepts a v0 assets_in row or a v4 record; computed fields are null and `exposure_unknown` is not
    yet set (build_snapshot adds exposure)."""
    p = _from_v4(row) if _is_v4(row) else _from_v0(row)
    sources = list(p["_sources"]) if p["_sources"] is not None else []
    if p["_sources"] is None:
        loc_fields = ["name", "asset_type", "municipality"]
        loc_note = p["_note"]
        if p["latitude"] is not None and p["longitude"] is not None:
            loc_fields += ["latitude", "longitude"]
        else:
            loc_note = "; ".join(n for n in (loc_note, "register carries no coordinates") if n)
        if p["geometry"] is not None:
            loc_fields += ["geometry", "area_m2"]
        sources.append(_source_entry(loc_fields, p["_register"], observed_at=p["_observed_at"],
                                     fetched_at=p["_fetched_at"], notes=loc_note))
        if p["capacity"] is not None or p["estimated_occupancy"] is not None:
            occ_fields = ["capacity"] if p["capacity"] is not None else ["estimated_occupancy"]
            occ_fields.append("occupancy_basis")
            sources.append(_source_entry(occ_fields, p["_occ_register"], observed_at=p["_observed_at"],
                                         fetched_at=p["_occ_fetched_at"], notes=p["_occ_note"]))
    if p["geometry"] is not None and p["area_m2"] is None:
        p["area_m2"] = _footprint_area_m2(p["geometry"])

    policy = cfg.VALUE_POLICY
    value_score = policy["by_type"].get(p["asset_type"])
    value_basis = policy["version"] if value_score is not None else None
    if value_score is not None:
        sources.append(_source_entry(["value_score", "value_basis"], "config.VALUE_POLICY",
                                     notes=f"class-based operational importance, policy {policy['version']}; "
                                           "prototype policy, not a monetary valuation"))
    evacuation_min, evacuation_source, evac_entry = _evacuation(p["asset_type"], cfg)
    if evac_entry is not None:
        sources.append(evac_entry)
    reasons = []
    if p["latitude"] is None or p["longitude"] is None:
        reasons.append("location_unknown")
    if p["capacity"] is None and p["estimated_occupancy"] is None:
        reasons.append("occupancy_unknown")
    if p["_seasonal"]:
        reasons.append("occupancy_seasonal")
    if p["_class_ambiguous"]:
        reasons.append("class_ambiguous")
    if value_score is None:
        reasons.append("value_unknown")
    if evacuation_min is None:
        reasons.append(EVACUATION_UNKNOWN)
    if _criticality_wanted(p["asset_type"], cfg):
        reasons.append(CRITICALITY_UNASSESSED)
    if _valuation_wanted(p["asset_type"], cfg):
        reasons.append(VALUATION_UNASSESSED)
    return {
        "asset_id": p["asset_id"],
        "name": p["name"],
        "asset_type": p["asset_type"],
        "latitude": p["latitude"],
        "longitude": p["longitude"],
        "geometry": p["geometry"],
        "area_m2": p["area_m2"],
        "capacity": p["capacity"],
        "estimated_occupancy": p["estimated_occupancy"],
        "occupancy_basis": p["occupancy_basis"],
        "value_score": value_score,
        "value_basis": value_basis,
        "distance_to_fire_m": None,
        "intersects_fire": None,
        "burn_probability": None,
        "arrival_p10_at": None,
        "arrival_p50_at": None,
        "forecast_horizon_at": None,
        "forecast_source": None,
        "fire_arrival_at": None,
        "fire_arrival_basis": None,
        "evacuation_min": evacuation_min,
        "evacuation_source": evacuation_source,
        "needs_review": bool(reasons),
        "review_reasons": reasons,
        "sources": sources,
        "municipality": p["municipality"],
        # Producer default. A tier only ever arrives through an analyst-confirmed override of an
        # agent proposal (fireline/priority.py), so all three stay null here.
        "criticality_tier": None,
        "criticality_factors": None,
        "criticality_basis": None,
        # Producer default, as above. A bespoke euro figure only ever arrives through an
        # analyst-confirmed override of an agent proposal, so all six stay null here.
        "custom_value_eur_low": None,
        "custom_value_eur_mid": None,
        "custom_value_eur_high": None,
        "custom_value_method": None,
        "custom_value_components": None,
        "custom_value_basis": None,
    }


# ------------------------------------------------------------------------------------ criticality
def _criticality_errors(tag: str, a: dict, cfg=config) -> list[str]:
    """Internal consistency of the three criticality keys, independent of who wrote them.

    The tier must name a policy tier; the three keys are null together; the factors are a list drawn
    from the policy enum with no repeats; and a tier may not carry fewer factors than the policy's
    inflation guard allows. Whether the tier is *right* is the analyst's call, not a validation.
    """
    policy = cfg.CRITICALITY_POLICY
    tier, factors, basis = a.get("criticality_tier"), a.get("criticality_factors"), a.get("criticality_basis")
    if tier is None:
        out = []
        if factors is not None:
            out.append(f"{tag}: criticality_factors must be null without criticality_tier")
        if basis is not None:
            out.append(f"{tag}: criticality_basis must be null without criticality_tier")
        return out
    if tier not in policy["tiers"]:
        return [f"{tag}: criticality_tier {tier!r} not in {tuple(policy['tiers'])}"]
    out = []
    if not isinstance(basis, str) or not basis:
        out.append(f"{tag}: criticality_tier requires a non-empty criticality_basis")
    if not isinstance(factors, list) or any(not isinstance(f, str) for f in factors):
        return out + [f"{tag}: criticality_factors must be a list of strings"]
    unknown = [f for f in factors if f not in policy["factors"]]
    if unknown:
        out.append(f"{tag}: criticality_factors {unknown} not in {tuple(policy['factors'])}")
    if len(set(factors)) != len(factors):
        out.append(f"{tag}: criticality_factors must not repeat")
    need = policy["min_factors"].get(tier, 0)
    if len(factors) < need:
        out.append(f"{tag}: criticality_tier {tier!r} needs at least {need} factor(s), got {len(factors)}")
    return out


def _custom_valuation_errors(tag: str, a: dict, cfg=config) -> list[str]:
    """Internal consistency of the six custom-valuation keys, independent of who wrote them.

    Either all six are absent (a snapshot written before the layer existed), or they are present and
    consistent: the method names a policy method; `not_valued` carries no amounts; any other method
    carries a full, ordered band with a basis and well-formed priced components. Whether the figure is
    *right* is the analyst's call, not a validation - the guards that gate a proposal live in
    `priority.custom_valuation_value`.
    """
    policy = cfg.CUSTOM_VALUATION_POLICY
    present = [k for k in CUSTOM_VALUATION_KEYS if k in a]
    if not present:
        return []
    if len(present) != len(CUSTOM_VALUATION_KEYS):
        missing = [k for k in CUSTOM_VALUATION_KEYS if k not in a]
        return [f"{tag}: custom-valuation keys must be all present or all absent (missing {missing})"]

    method, basis = a["custom_value_method"], a["custom_value_basis"]
    amounts = {level: a[f"custom_value_eur_{level}"] for level in ("low", "mid", "high")}
    components = a["custom_value_components"]
    out = []
    if method is None:
        for k in ("custom_value_basis", "custom_value_components"):
            if a[k] is not None:
                out.append(f"{tag}: {k} must be null without custom_value_method")
        for level, v in amounts.items():
            if v is not None:
                out.append(f"{tag}: custom_value_eur_{level} must be null without custom_value_method")
        return out
    if method not in policy["methods"]:
        return [f"{tag}: custom_value_method {method!r} not in {tuple(policy['methods'])}"]
    if not isinstance(basis, str) or not basis:
        out.append(f"{tag}: custom_value_method requires a non-empty custom_value_basis")
    if components is not None:
        if not isinstance(components, list):
            out.append(f"{tag}: custom_value_components must be a list or null")
        else:
            for i, c in enumerate(components):
                if not isinstance(c, dict) or not str(c.get("label") or "").strip():
                    out.append(f"{tag}: custom_value_components[{i}] needs a non-empty label")
                    continue
                amount = c.get("amount_eur")
                if (amount is None or isinstance(amount, bool) or not isinstance(amount, (int, float))
                        or not math.isfinite(amount) or amount < 0):
                    out.append(f"{tag}: custom_value_components[{i}].amount_eur must be a nonnegative "
                               "finite number")
    if method == "not_valued":
        for level, v in amounts.items():
            if v is not None:
                out.append(f"{tag}: custom_value_eur_{level} must be null for method 'not_valued'")
        if components:
            out.append(f"{tag}: custom_value_components must be empty or null for method 'not_valued'")
        return out
    for level, v in amounts.items():
        if v is None:
            out.append(f"{tag}: custom_value_eur_{level} is required for method {method!r}")
        elif (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0):
            out.append(f"{tag}: custom_value_eur_{level} must be a nonnegative finite number")
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in amounts.values()):
        if not amounts["low"] <= amounts["mid"] <= amounts["high"]:
            out.append(f"{tag}: custom_value_eur_low <= _mid <= _high required, got "
                       f"{amounts['low']:g} / {amounts['mid']:g} / {amounts['high']:g}")
    return out


def _criticality_wanted(asset_type: str, cfg=config) -> bool:
    """True when this class should enter the review queue as `criticality_unassessed`.

    Gated on FEATURES["asset_criticality"], so a snapshot built with the flag off is unchanged.
    """
    if not cfg.FEATURES.get("asset_criticality"):
        return False
    return asset_type in tuple(cfg.CRITICALITY_POLICY["assess_classes"])


def _valuation_wanted(asset_type: str, cfg=config) -> bool:
    """True when this class should enter the review queue as `valuation_unassessed`.

    Gated on FEATURES["custom_valuation"], so a snapshot built with the flag off is unchanged. The
    classes are exactly those `VALUE_AT_RISK_POLICY` has no row for: asking for a bespoke figure is
    worth a model call only where the class table gives none, and a class it does price keeps the
    class answer (config.CUSTOM_VALUATION_POLICY["assess_classes"]).
    """
    if not cfg.FEATURES.get("custom_valuation"):
        return False
    return asset_type in tuple(cfg.CUSTOM_VALUATION_POLICY["assess_classes"])


# ------------------------------------------------------------------------------------- evacuation
def _evacuation(asset_type: str, cfg=config):
    """(evacuation_min, evacuation_source, sources entry) from cfg.EVACUATION_POLICY for one class.

    `evacuation_min` is the sum of the policy's component minutes (mobilisation, preparation/loading,
    movement to a receiving location); the source is the policy version. Unknown class -> (None, None, None).
    """
    policy = cfg.EVACUATION_POLICY
    row = policy["by_type"].get(asset_type)
    if row is None:
        return None, None, None
    components = [c for c in policy["components"] if row.get(c) is not None]
    total = float(sum(float(row[c]) for c in components))
    breakdown = ", ".join(f"{c.removesuffix('_min')} {row[c]:g} min" for c in components)
    entry = _source_entry(["evacuation_min", "evacuation_source"], "config.EVACUATION_POLICY",
                          notes=f"policy {policy['version']} for class {asset_type}: {breakdown} = {total:g} min; "
                                f"assumptions: {row.get('assumptions') or 'none stated'}; "
                                "labelled prototype assumption, not an emergency-service rule; analyst override allowed")
    return total, policy["version"], entry


# --------------------------------------------------------------------------------------- forecast
def _minutes_to_iso(base: datetime, minutes) -> str | None:
    m = _num(minutes)
    if m is None or math.isinf(m):
        return None
    return (base + timedelta(minutes=m)).isoformat()


def _enrich_forecast(rec: dict, arrival, as_of: datetime, note: str | None = None) -> None:
    """Fill burn_probability / arrival_* from an ArrivalRaster (labelled, unvalidated enrichment).
    `note` (optional) is appended to the provenance entry, e.g. the wind and run settings behind the raster."""
    if arrival is None or rec["latitude"] is None or rec["longitude"] is None:
        return
    bp = _num(arrival.sample("burn_prob", rec["longitude"], rec["latitude"]))
    if bp is None:   # outside the raster grid
        return
    label = f"{arrival.source}_ensemble" if arrival.source == "ca" else str(arrival.source)
    rec["burn_probability"] = round(bp, 3)
    rec["arrival_p10_at"] = _minutes_to_iso(as_of, arrival.sample("arrival_p10", rec["longitude"], rec["latitude"]))
    rec["arrival_p50_at"] = _minutes_to_iso(as_of, arrival.sample("arrival_p50", rec["longitude"], rec["latitude"]))
    rec["forecast_horizon_at"] = (as_of + timedelta(minutes=int(arrival.horizon_min))).isoformat()
    rec["forecast_source"] = f"{label} (labelled enrichment, not validated)"
    fields = ["burn_probability", "arrival_p10_at", "arrival_p50_at", "forecast_horizon_at", "forecast_source"]
    if rec["arrival_p10_at"] is not None:   # p10 selected; inf (not burned in >=10% of runs) stays null
        rec["fire_arrival_at"] = rec["arrival_p10_at"]
        rec["fire_arrival_basis"] = f"p10 ({label}, labelled enrichment, not validated)"
        fields += ["fire_arrival_at", "fire_arrival_basis"]
    rec["sources"].append(_source_entry(
        fields, rec["forecast_source"], observed_at=as_of,
        notes="nearest raster cell at the representative point; minutes after as_of; "
              "fire_arrival_at = arrival_p10_at when finite; not a provider forecast, not validated on Gavarres"
              + (f"; {note}" if note else "")))


# --------------------------------------------------------------------------------- value at risk
def _arrival_slack_min(rec: dict, field: str, now, evacuation_min: float, buffer_min: float):
    """(slack_min, known) for one arrival quantile: minutes of window left at `now`.

    `known` is False when the timestamp is unusable. A null quantile on an asset a forecast covers is
    not a gap: the fire does not reach it inside the horizon, so the window is not exhausted and the
    caller reads `slack_min = None, known = True` as "not at risk".
    """
    raw = rec.get(field)
    if raw is None:
        return None, True
    try:
        arrival = _utc(raw)
    except (TypeError, ValueError):
        return None, False
    minutes = (arrival - now).total_seconds() / 60.0
    return window_arithmetic(minutes, evacuation_min, 0.0, buffer_min)["slack_min"], True


def derive_value_at_risk(rec: dict, now_at=None, cfg=config) -> None:
    """Fill the eight `VALUE_AT_RISK_KEYS` of one asset record IN PLACE (handoff 002).

        people_exposed         = estimated_occupancy x burn_probability          # never capacity
        people_at_risk_p50     = estimated_occupancy if slack_p50 <= 0 else 0
        people_at_risk_p10     = estimated_occupancy if slack_p10 <= 0 else 0
        expected_loss_eur_mid  = burn_probability x d_mid  x replacement_value_eur   # low / high alike

    `slack_pXX = arrival_pXX_at - evacuation_min - CONTACT_POLICY["buffer_min"] - now_at`, the remaining
    evacuation window of `priority.rank_asset` evaluated at each quantile rather than at the selected
    arrival. The threshold is `<= 0`, matching `window_exhausted`: `people_at_risk` is a re-labelling of
    that status weighted by headcount, so it uses the same boundary (the handoff writes `< 0`).

    Every field is `null`, never zero, when an input is null: no headcount (`capacity` is never a
    headcount), no `burn_probability`, no `evacuation_min`, no forecast covering the asset, no location,
    or a class with no replacement value (`nucleus`, or any class outside the policy). An asset a forecast
    does cover whose quantile is null is not reached inside the horizon, so `people_at_risk_* = 0`, and
    `burn_probability = 0.0` gives `people_exposed = 0` and `expected_loss_eur_* = 0`: those are the
    forecast's statement, not a gap. `now_at` (the snapshot `as_of`) may be None when only the
    time-independent fields can be re-derived; `people_at_risk_*` is then left as it is.

    One `sources` entry naming exactly the fields it set replaces any earlier entry for them, so
    re-deriving after an analyst override does not duplicate provenance. No review reason is added: a
    class that is not valued is reported by the scenario header's excluded count, not by the review queue.
    """
    policy = cfg.VALUE_AT_RISK_POLICY
    custom = custom_valuation_band(rec, cfg)
    # A confirmed bespoke figure replaces the class replacement cost, and brings its own damage band
    # because the classes it applies to have no row in VALUE_AT_RISK_POLICY at all.
    band = dict(cfg.CUSTOM_VALUATION_POLICY["damage_ratio"]) if custom else policy["by_type"].get(rec.get("asset_type"))
    located = rec.get("latitude") is not None and rec.get("longitude") is not None
    covered = located and bool(str(rec.get("forecast_source") or "").strip())
    occupancy = rec.get("estimated_occupancy")
    occupancy = None if isinstance(occupancy, bool) else _int(occupancy)
    burn_probability = _num(rec.get("burn_probability"))
    evacuation_min = rec.get("evacuation_min")
    evacuation_min = None if isinstance(evacuation_min, bool) else _num(evacuation_min)
    if custom:
        value = custom["mid"]
    else:
        value = None if band is None else _num(band.get("replacement_value_eur"))

    values = {k: None for k in VALUE_AT_RISK_KEYS}
    if value is not None:
        values["replacement_value_eur"] = int(value) if float(value).is_integer() else value
        values["replacement_value_basis"] = (
            (f"per-asset custom valuation, method {custom['method']}, policy "
             f"{cfg.CUSTOM_VALUATION_POLICY['version']} (analyst-confirmed; not a market valuation)")
            if custom else
            (f"assumed per-class replacement cost for {rec.get('asset_type')}, "
             f"policy {policy['version']} ({policy['value_basis']})"))
    if occupancy is not None and burn_probability is not None:
        values["people_exposed"] = round(float(occupancy) * burn_probability, 1)
    if value is not None and burn_probability is not None:
        for level in ("low", "mid", "high"):
            # A class figure is one number, so only the damage band moves. A bespoke figure is itself a
            # band, so the low end of the loss takes the low end of BOTH and the high end takes both:
            # the result states the valuation uncertainty as well as the damage uncertainty.
            amount = custom[level] if custom else value
            values[f"expected_loss_eur_{level}"] = round(burn_probability * float(band[f"d_{level}"]) * amount)
    at_risk = {"people_at_risk_p50": None, "people_at_risk_p10": None}
    if occupancy is not None and evacuation_min is not None and covered and now_at is not None:
        now = _utc(now_at)
        buffer_min = float(cfg.CONTACT_POLICY["buffer_min"])
        for key, field in (("people_at_risk_p50", "arrival_p50_at"), ("people_at_risk_p10", "arrival_p10_at")):
            slack, known = _arrival_slack_min(rec, field, now, evacuation_min, buffer_min)
            if not known:
                continue                                  # unusable timestamp: leave it null
            at_risk[key] = occupancy if slack is not None and slack <= 0 else 0
    if now_at is None:                                    # time-independent re-derivation only
        at_risk = {k: rec.get(k) for k in at_risk}
    values.update(at_risk)
    rec.update(values)

    sources = [s for s in (rec.get("sources") or [])
               if not (set(s.get("fields") or []) & set(VALUE_AT_RISK_KEYS))]
    fields = [k for k in VALUE_AT_RISK_KEYS if values[k] is not None]
    if band is not None and fields:
        sources.append(_source_entry(fields,
                                     "config.CUSTOM_VALUATION_POLICY" if custom else "config.VALUE_AT_RISK_POLICY",
                                     notes=_value_at_risk_note(rec.get("asset_type"), band, value, policy, cfg,
                                                               custom)))
    rec["sources"] = sources


def custom_valuation_band(rec: dict, cfg=config) -> dict | None:
    """The confirmed bespoke valuation of one asset as `{low, mid, high, method}`, or None.

    None when the asset carries no custom valuation, when the analyst confirmed `not_valued` (the agent
    looked and found nothing that supports a bespoke figure), or when the band is incomplete. The
    guards that produced the payload live in `priority.custom_valuation_value`; this only reads back
    what an analyst confirmed, so it stays permissive and returns None rather than raising.
    """
    method = rec.get("custom_value_method")
    if not method or method == "not_valued":
        return None
    levels = {}
    for level in ("low", "mid", "high"):
        amount = rec.get(f"custom_value_eur_{level}")
        if amount is None or isinstance(amount, bool):
            return None
        amount = _num(amount)
        if amount is None:
            return None
        levels[level] = float(amount)
    if not levels["low"] <= levels["mid"] <= levels["high"]:
        return None
    return {**levels, "method": method}


def _value_at_risk_note(asset_type, band, value, policy, cfg, custom=None) -> str:
    money = "not valued" if value is None else f"replacement_value_eur {value:.0f}"
    if custom:
        cpol = cfg.CUSTOM_VALUATION_POLICY
        return (f"policy {cpol['version']} for THIS building, not for class {asset_type}: a per-asset "
                f"valuation an analyst confirmed from evidence the agent quoted, by method "
                f"{custom['method']}; {money} is the mid of a band "
                f"{custom['low']:.0f} - {custom['high']:.0f}; the class table prices no "
                f"{asset_type} at all, so the damage ratio is the policy default "
                f"{band['d_low']:g} / {band['d_mid']:g} / {band['d_high']:g} (low / mid / high); "
                "expected_loss_eur_low / _mid / _high = burn_probability x damage ratio x the matching "
                "end of the valuation band, so the range states the valuation uncertainty and the damage "
                "uncertainty together; it is an assumed figure from quoted references, never a market "
                "valuation, an insurer's figure or a measured number; total economic loss (insured and "
                "uninsured); euros never enter the ranking, the sort or a filter, and this figure orders "
                "the strategic exposure view only; "
                "people_exposed = estimated_occupancy x burn_probability (a headcount, never capacity)")
    return (f"policy {policy['version']} for class {asset_type}: {money}, damage ratio "
            f"{band['d_low']:g} / {band['d_mid']:g} / {band['d_high']:g} (low / mid / high); "
            "people_exposed = estimated_occupancy x burn_probability (a headcount, never capacity); "
            "people_at_risk_p50 / _p10 = estimated_occupancy when the remaining evacuation window at that "
            f"arrival quantile is exhausted (arrival - evacuation_min - buffer {float(cfg.CONTACT_POLICY['buffer_min']):g} min "
            "<= 0 from as_of), else 0, and 0 when the forecast does not reach the asset inside its horizon; "
            "expected_loss_eur_low / _mid / _high = burn_probability x damage ratio x replacement_value_eur; "
            "the replacement value is an assumed per-class placeholder with no per-asset basis and the band "
            "shown is the damage-ratio band only, so the value uncertainty is at least as large; total "
            "economic loss (insured and uninsured), not an insurer's figure; euros never enter the ranking, "
            "the sort or a filter"
            + (f"; {band['note']}" if band.get("note") else ""))


# ---------------------------------------------------------------------------------------- builder
def build_snapshot(assets_in, fire, *, scenario_id, incident_id, sequence, as_of, input_mode,
                   computed_at=None, data_status=None, metrics=None, arrival=None, forecast=None,
                   arrival_note=None, cfg=config) -> dict:
    """Snapshot envelope (CONTRACTS 2.1) with one record per input asset, input order preserved.

    `fire` is a FireUpdate-shaped dict (`observed_at`, `geometry`, `geometry_kind`, `source`,
    `incident_id`, ...) or None. `forecast` is a `forecast_input` forecast-input-1 dict applied with
    `attach_forecast` (per-location arrival estimates -> `fire_arrival_at`); when given it takes
    precedence over `arrival` (spread.ArrivalRaster), which is used only when
    `cfg.FEATURES["forecast_enrichment"]` is on (`arrival_note`, optional, is appended to that
    enrichment's provenance entry). Every asset without `fire_arrival_at` afterwards carries
    `forecast_unavailable`; nothing is inferred from distance. With `cfg.FEATURES["value_at_risk"]` on,
    `derive_value_at_risk` then adds the eight value-at-risk keys to every record (absent when it is off).
    """
    if input_mode not in INPUT_MODES:
        raise ValueError(f"input_mode {input_mode!r} not in {INPUT_MODES}")
    sequence = int(sequence)
    if sequence < 1:
        raise ValueError("sequence starts at 1")
    as_of_dt = _utc(as_of)
    computed_dt = _utc(computed_at) or datetime.now(timezone.utc)
    fire = fire or {}
    fire_geometry = _plain_geojson(fire.get("geometry"))
    fire_kind = fire.get("geometry_kind")
    if fire_geometry is not None and fire_kind is None:
        fire_kind = "hotspot_centre" if fire_geometry.get("type") == "Point" else "perimeter"
    if fire_geometry is not None and fire_kind not in GEOMETRY_KINDS:
        raise ValueError(f"geometry_kind {fire_kind!r} not in {GEOMETRY_KINDS}")
    if fire_geometry is None:
        fire_kind = None
    fire_observed_at = _iso(fire.get("observed_at"))
    fire_source = fire.get("source")
    if data_status is None:
        data_status = "unavailable" if fire_geometry is None else freshness(fire_observed_at, as_of_dt, cfg)
    if data_status not in DATA_STATUSES:
        raise ValueError(f"data_status {data_status!r} not in {DATA_STATUSES}")
    if metrics is None:
        obs = _utc(fire_observed_at)
        metrics = {"source_age_s": None if obs is None else round((as_of_dt - obs).total_seconds(), 1),
                   "processing_s": None}
    metrics = {"source_age_s": metrics.get("source_age_s"), "processing_s": metrics.get("processing_s")}

    enrich = forecast is None and bool(cfg.FEATURES.get("forecast_enrichment")) and arrival is not None
    assets = []
    for row in assets_in:
        rec = asset_record(row, cfg)
        distance, intersects, note = asset_exposure(rec["longitude"], rec["latitude"], rec["geometry"], fire_geometry)
        rec["distance_to_fire_m"] = distance
        rec["intersects_fire"] = intersects
        dist_note = note if note else "minimum distance in EPSG:25831 to the fire footprint"
        if fire_kind == "simulated" and distance is not None:
            dist_note = f"{dist_note}; {SIMULATED_NOTE}"
        rec["sources"].append(_source_entry(["distance_to_fire_m", "intersects_fire"],
                                            fire_source or "fire geometry", observed_at=fire_observed_at,
                                            available_at=fire.get("received_at"), notes=dist_note))
        if distance is None:
            rec["review_reasons"].append("exposure_unknown")
            rec["needs_review"] = True
        if enrich:
            _enrich_forecast(rec, arrival, as_of_dt, arrival_note)
        assets.append(rec)
    if forecast is not None:
        attach_forecast(assets, forecast)
    for rec in assets:   # no forecast covers this asset: null arrival, never a distance-based guess
        if rec["fire_arrival_at"] is None and FORECAST_UNAVAILABLE not in rec["review_reasons"]:
            rec["review_reasons"].append(FORECAST_UNAVAILABLE)
            rec["needs_review"] = True
    if cfg.FEATURES.get("value_at_risk"):   # optional layer: people exposed / at risk and expected loss
        for rec in assets:
            derive_value_at_risk(rec, as_of_dt, cfg)

    return {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": str(scenario_id),
        "incident_id": str(incident_id if incident_id is not None else fire.get("incident_id")),
        "snapshot_id": f"{scenario_id}-{sequence:04d}",
        "sequence": sequence,
        "as_of": as_of_dt.isoformat(),
        "computed_at": computed_dt.isoformat(),
        "input_mode": input_mode,
        "fire_observed_at": fire_observed_at,
        "fire_source": fire_source,
        "fire_geometry": fire_geometry,
        "fire_geometry_kind": fire_kind,
        "data_status": data_status,
        "metrics": metrics,
        "assets": assets,
    }


# ------------------------------------------------------------------------------------- validation
def _is_time(s) -> bool:
    try:
        return _utc(s) is not None
    except (TypeError, ValueError):
        return False


def validate_snapshot(snap) -> list[str]:
    """[] when the snapshot satisfies CONTRACTS 2.1/2.2; otherwise human-readable problems."""
    errs = []
    if not isinstance(snap, dict):
        return ["snapshot is not a dict"]
    for k in ENVELOPE_KEYS:
        if k not in snap:
            errs.append(f"missing envelope key {k}")
    if errs:
        return errs
    if snap["schema_version"] not in READABLE_SCHEMA_VERSIONS:
        errs.append(f"schema_version {snap['schema_version']!r} not in {READABLE_SCHEMA_VERSIONS}")
    legacy = snap["schema_version"] == "1.0"   # v1.1 timing keys optional; checked when present
    seq = snap["sequence"]
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        errs.append(f"sequence must be an int >= 1, got {seq!r}")
    elif snap["snapshot_id"] != f"{snap['scenario_id']}-{seq:04d}":
        errs.append(f"snapshot_id {snap['snapshot_id']!r} != '{snap['scenario_id']}-{seq:04d}'")
    for k in ("as_of", "computed_at"):
        if not isinstance(snap[k], str) or not _is_time(snap[k]):
            errs.append(f"{k} is not an ISO timestamp")
    if snap["fire_observed_at"] is not None and not _is_time(snap["fire_observed_at"]):
        errs.append("fire_observed_at is not an ISO timestamp")
    if snap["input_mode"] not in INPUT_MODES:
        errs.append(f"input_mode {snap['input_mode']!r} not in {INPUT_MODES}")
    if snap["data_status"] not in DATA_STATUSES:
        errs.append(f"data_status {snap['data_status']!r} not in {DATA_STATUSES}")
    geom, kind = snap["fire_geometry"], snap["fire_geometry_kind"]
    if geom is None:
        if kind is not None:
            errs.append("fire_geometry_kind must be null without fire_geometry")
    else:
        if not isinstance(geom, dict) or "type" not in geom:
            errs.append("fire_geometry is not a GeoJSON geometry")
        elif kind not in GEOMETRY_KINDS:
            errs.append(f"fire_geometry_kind {kind!r} not in {GEOMETRY_KINDS}")
        elif geom.get("type") == "Point" and kind != "hotspot_centre":
            errs.append("a Point fire_geometry must be labelled hotspot_centre")
        elif geom.get("type") in ("Polygon", "MultiPolygon") and kind not in ("perimeter", "simulated"):
            errs.append("a polygon fire_geometry must be labelled perimeter or simulated")
    m = snap["metrics"]
    if not isinstance(m, dict) or set(m) != {"source_age_s", "processing_s"}:
        errs.append("metrics must have exactly source_age_s and processing_s")
    if not isinstance(snap["assets"], list):
        return errs + ["assets is not a list"]
    seen = set()
    for i, a in enumerate(snap["assets"]):
        tag = f"assets[{i}]"
        if not isinstance(a, dict):
            errs.append(f"{tag} is not a dict")
            continue
        # CRITICALITY_KEYS and CUSTOM_VALUATION_KEYS are optional groups: a snapshot written before
        # either layer existed stays valid without them (their own error helpers accept all-absent).
        optional = CRITICALITY_KEYS + CUSTOM_VALUATION_KEYS
        missing = [k for k in ASSET_KEYS
                   if k not in a and k not in optional and not (legacy and k in TIMING_KEYS)]
        if missing:
            errs.append(f"{tag} missing keys {missing}")
            continue
        a = dict(a, **{k: a.get(k) for k in CRITICALITY_KEYS})  # absent criticality keys read as null
        if legacy:
            a = dict(a, **{k: a.get(k) for k in TIMING_KEYS})   # 1.0: missing timing keys read as null
        tag = f"{a['asset_id']}"
        if a["asset_id"] in seen:
            errs.append(f"duplicate asset_id {tag}")
        seen.add(a["asset_id"])
        if (a["latitude"] is None) != (a["longitude"] is None):
            errs.append(f"{tag}: latitude/longitude must both be null or both set")
        for k in ("capacity", "estimated_occupancy"):
            v = a[k]
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                errs.append(f"{tag}: {k} must be a nonnegative int or null")
        for k in ("value_score", "burn_probability"):
            v = a[k]
            if v is not None and not (isinstance(v, (int, float)) and 0.0 <= v <= 1.0):
                errs.append(f"{tag}: {k} must be in [0, 1] or null")
        if (a["value_score"] is None) != (a["value_basis"] is None):
            errs.append(f"{tag}: value_score and value_basis must be null together")
        d = a["distance_to_fire_m"]
        if d is not None and not (isinstance(d, (int, float)) and d >= 0):
            errs.append(f"{tag}: distance_to_fire_m must be nonnegative or null")
        if (d is None) != (a["intersects_fire"] is None):
            errs.append(f"{tag}: distance_to_fire_m and intersects_fire must be null together")
        if d is not None and a["intersects_fire"] is not None and (d == 0) != bool(a["intersects_fire"]):
            errs.append(f"{tag}: intersects_fire must be true iff distance is 0")
        for k in ("arrival_p10_at", "arrival_p50_at", "forecast_horizon_at"):
            if a[k] is not None and not _is_time(a[k]):
                errs.append(f"{tag}: {k} is not an ISO timestamp")
        if a["forecast_source"] is None and any(a[k] is not None for k in ("burn_probability", "arrival_p10_at", "arrival_p50_at")):
            errs.append(f"{tag}: forecast fields need a forecast_source")
        # v1.1 timing fields: a selected arrival needs its semantics and provenance; a duration needs a basis.
        arr = a["fire_arrival_at"]
        if arr is not None:
            if not _is_time(arr):
                errs.append(f"{tag}: fire_arrival_at is not an ISO timestamp")
            for k in ("fire_arrival_basis", "forecast_source", "forecast_horizon_at"):
                if a[k] is None:
                    errs.append(f"{tag}: fire_arrival_at requires {k}")
            if a["fire_arrival_basis"] is not None and not isinstance(a["fire_arrival_basis"], str):
                errs.append(f"{tag}: fire_arrival_basis must be a string")
        elif a["fire_arrival_basis"] is not None:
            errs.append(f"{tag}: fire_arrival_basis must be null without fire_arrival_at")
        ev = a["evacuation_min"]
        if ev is not None:
            if isinstance(ev, bool) or not isinstance(ev, (int, float)) or not math.isfinite(ev) or ev < 0:
                errs.append(f"{tag}: evacuation_min must be a nonnegative finite number or null")
            if a["evacuation_source"] is None:
                errs.append(f"{tag}: evacuation_min requires evacuation_source")
            elif not isinstance(a["evacuation_source"], str):
                errs.append(f"{tag}: evacuation_source must be a string")
        elif a["evacuation_source"] is not None:
            errs.append(f"{tag}: evacuation_source must be null without evacuation_min")
        errs.extend(_criticality_errors(tag, a))
        errs.extend(_custom_valuation_errors(tag, a))
        # value-at-risk layer (optional, FEATURES["value_at_risk"]): all eight keys or none, never a zero
        # standing in for a null, and euros only where both a burn probability and a class value exist.
        present = [k for k in VALUE_AT_RISK_KEYS if k in a]
        if present and len(present) != len(VALUE_AT_RISK_KEYS):
            missing_var = [k for k in VALUE_AT_RISK_KEYS if k not in a]
            errs.append(f"{tag}: value-at-risk keys must be all present or all absent (missing {missing_var})")
        elif present:
            value, basis = a["replacement_value_eur"], a["replacement_value_basis"]
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                errs.append(f"{tag}: replacement_value_eur must be a nonnegative finite number or null")
            if (value is None) != (basis is None):
                errs.append(f"{tag}: replacement_value_eur and replacement_value_basis must be null together")
            elif basis is not None and not isinstance(basis, str):
                errs.append(f"{tag}: replacement_value_basis must be a string")
            exposed = a["people_exposed"]
            if exposed is not None and (isinstance(exposed, bool) or not isinstance(exposed, (int, float))
                                        or not math.isfinite(exposed) or exposed < 0):
                errs.append(f"{tag}: people_exposed must be a nonnegative finite number or null")
            if (exposed is None) != (a["estimated_occupancy"] is None or a["burn_probability"] is None):
                errs.append(f"{tag}: people_exposed must be null iff estimated_occupancy or burn_probability is null")
            for k in ("people_at_risk_p50", "people_at_risk_p10"):
                v = a[k]
                if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                    errs.append(f"{tag}: {k} must be a nonnegative int or null")
                elif v is not None and v not in (0, a["estimated_occupancy"]):
                    errs.append(f"{tag}: {k} must be 0 or the whole estimated_occupancy (no partial clearance)")
                if v is not None and any(a[f] is None for f in ("estimated_occupancy", "evacuation_min", "forecast_source")):
                    errs.append(f"{tag}: {k} requires estimated_occupancy, evacuation_min and forecast_source")
            losses = [a[f"expected_loss_eur_{level}"] for level in ("low", "mid", "high")]
            for level, v in zip(("low", "mid", "high"), losses):
                if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
                                      or not math.isfinite(v) or v < 0):
                    errs.append(f"{tag}: expected_loss_eur_{level} must be a nonnegative finite number or null")
                if (v is None) != (a["burn_probability"] is None or value is None):
                    errs.append(f"{tag}: expected_loss_eur_{level} must be null iff burn_probability or "
                                "replacement_value_eur is null")
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in losses) \
                    and not losses[0] <= losses[1] <= losses[2]:
                errs.append(f"{tag}: expected_loss_eur_low <= _mid <= _high is required")
        reasons = a["review_reasons"]
        if not isinstance(reasons, list) or any(r not in REVIEW_REASONS for r in reasons):
            errs.append(f"{tag}: review_reasons must be a list from {REVIEW_REASONS}")
        elif a["needs_review"] is not bool(reasons):
            errs.append(f"{tag}: needs_review must be true iff review_reasons is non-empty")
        else:
            if d is None and "exposure_unknown" not in reasons:
                errs.append(f"{tag}: distance null requires exposure_unknown")
            if a["latitude"] is None and "location_unknown" not in reasons:
                errs.append(f"{tag}: null coordinates require location_unknown")
            if a["value_score"] is None and "value_unknown" not in reasons:
                errs.append(f"{tag}: null value_score requires value_unknown")
            if a.get("criticality_tier") is not None and CRITICALITY_UNASSESSED in reasons:
                errs.append(f"{tag}: criticality_unassessed must be absent once a tier is set")
            if a.get("custom_value_method") is not None and VALUATION_UNASSESSED in reasons:
                errs.append(f"{tag}: valuation_unassessed must be absent once a valuation is confirmed")
            if not legacy:
                if (arr is None) != (FORECAST_UNAVAILABLE in reasons):
                    errs.append(f"{tag}: forecast_unavailable must be present iff fire_arrival_at is null")
                if (ev is None) != (EVACUATION_UNKNOWN in reasons):
                    errs.append(f"{tag}: evacuation_unknown must be present iff evacuation_min is null")
        if not isinstance(a["sources"], list):
            errs.append(f"{tag}: sources must be a list")
        else:
            for j, s in enumerate(a["sources"]):
                if not isinstance(s, dict) or [k for k in SOURCE_KEYS if k not in s]:
                    errs.append(f"{tag}: sources[{j}] missing keys")
                elif not isinstance(s["fields"], list) or not s["fields"]:
                    errs.append(f"{tag}: sources[{j}].fields must be a non-empty list")
    return errs


# --------------------------------------------------------------------------------------------- io
def write_snapshot(snap: dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return path


def read_snapshot(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
