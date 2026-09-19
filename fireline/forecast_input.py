"""Per-location fire arrival estimates for the snapshot producer (CONTRACTS.md 2.2, v1.1 timing fields).

A forecast is a plain dict in the `forecast-input-1` shape, read from a labelled file
(`load_forecast`), or built from a Deepfire fire-spread simulation by `deepfire_spread_to_forecast`
(`forecast_from_recorded_spread` for a recorded response), and attached to asset records by
`attach_forecast`. The selected arrival (`fire_arrival_at`) is the provider's p10 when present, else
its p50, else the single `arrival_at` estimate (basis = the forecast's declared `basis`), else null;
`fire_arrival_basis` states which. Nothing in this module derives an arrival time, a quantile or a
probability from distance: assets that the forecast does not cover get null and the
`forecast_unavailable` review reason (readme section 4 "Spread forecast"). A `burn_probability` of 0.0
from the Deepfire adapter is different from null: the run covers that location and puts no burned area
there within its horizon, still by containment and never by distance.

File format `forecast-input-1`:

    {
      "schema_version": "forecast-input-1",
      "forecast_source": str,                    # provider/method label copied to asset.forecast_source
      "input_mode": "synthetic" | "recorded" | "live",   # synthetic = hand-designed, not a provider forecast
      "issued_at": iso,                          # when the estimates were issued (asset sources.observed_at)
      "received_at": iso | absent,               # optional: response receipt time (asset sources available_at/fetched_at)
      "forecast_horizon_at": iso,                # end of the forecast run; arrivals never exceed it
      "basis": "p10" | "p50" | <other>,          # semantics of the single `arrival_at` estimate, or the declared quantile
      "note": str,                               # method and, for synthetic files, the design reasoning
      "estimates": {asset_id: {"arrival_p10_at": iso | null, "arrival_p50_at": iso | null,
                               "burn_probability": float | null,
                               "arrival_at": iso | null}}     # optional: a provider's single estimate
    }

Selection is p10 (basis "p10"), then p50 (basis "p50"), then `arrival_at` (basis = the forecast's
`basis` string, e.g. the Deepfire isochrone-crossing label). Synthetic files must say "synthetic, not a
provider forecast" in their note.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely import make_valid
from shapely.geometry import Point, shape
from shapely.ops import unary_union

SCHEMA_VERSION = "forecast-input-1"
INPUT_MODES = ("synthetic", "recorded", "live")
SYNTHETIC_LABEL = "synthetic, not a provider forecast"
FORECAST_UNAVAILABLE = "forecast_unavailable"
ESTIMATE_KEYS = ("arrival_p10_at", "arrival_p50_at", "burn_probability")   # required per estimate
OPTIONAL_ESTIMATE_KEYS = ("arrival_at",)                                    # a provider's single estimate
# Asset keys this module may set, in record order.
FORECAST_FIELDS = ("burn_probability", "arrival_p10_at", "arrival_p50_at", "forecast_horizon_at",
                   "forecast_source", "fire_arrival_at", "fire_arrival_basis")


# ------------------------------------------------------------------------------------------ time
def _utc(t) -> datetime | None:
    """ISO string or datetime -> tz-aware UTC datetime; None stays None. Raises ValueError on junk."""
    if t is None:
        return None
    if isinstance(t, datetime):
        return (t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t).astimezone(timezone.utc)
    if not isinstance(t, str):
        raise ValueError(f"not a timestamp: {t!r}")
    s = t.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)


def _iso(t) -> str | None:
    dt = _utc(t)
    return None if dt is None else dt.isoformat()


# ------------------------------------------------------------------------------------ validation
def validate_forecast(forecast) -> None:
    """Raise ValueError unless `forecast` is a well-formed forecast-input-1 dict."""
    if not isinstance(forecast, dict):
        raise ValueError("forecast is not a dict")
    if forecast.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version {forecast.get('schema_version')!r} != {SCHEMA_VERSION!r}")
    for key in ("forecast_source", "input_mode", "issued_at", "forecast_horizon_at", "basis", "note", "estimates"):
        if key not in forecast:
            raise ValueError(f"missing key {key}")
    if not isinstance(forecast["forecast_source"], str) or not forecast["forecast_source"].strip():
        raise ValueError("forecast_source must be a non-empty string")
    if forecast["input_mode"] not in INPUT_MODES:
        raise ValueError(f"input_mode {forecast['input_mode']!r} not in {INPUT_MODES}")
    if not isinstance(forecast["basis"], str) or not forecast["basis"].strip():
        raise ValueError("basis must be a non-empty string")
    if not isinstance(forecast["note"], str):
        raise ValueError("note must be a string")
    if forecast["input_mode"] == "synthetic" and SYNTHETIC_LABEL not in forecast["note"]:
        raise ValueError(f"a synthetic forecast note must say {SYNTHETIC_LABEL!r}")
    try:
        issued, horizon = _utc(forecast["issued_at"]), _utc(forecast["forecast_horizon_at"])
    except ValueError as e:
        raise ValueError(f"issued_at / forecast_horizon_at: {e}") from e
    if issued is None or horizon is None:
        raise ValueError("issued_at and forecast_horizon_at are required timestamps")
    if horizon < issued:
        raise ValueError("forecast_horizon_at is before issued_at")
    estimates = forecast["estimates"]
    if not isinstance(estimates, dict):
        raise ValueError("estimates must be a dict keyed by asset_id")
    for asset_id, est in estimates.items():
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError(f"estimate key {asset_id!r} is not an asset_id")
        if not isinstance(est, dict):
            raise ValueError(f"estimate {asset_id}: not a dict")
        extra = set(est) - set(ESTIMATE_KEYS) - set(OPTIONAL_ESTIMATE_KEYS)
        missing = [k for k in ESTIMATE_KEYS if k not in est]
        if extra or missing:
            raise ValueError(f"estimate {asset_id}: keys must be exactly {ESTIMATE_KEYS} plus optional "
                             f"{OPTIONAL_ESTIMATE_KEYS} (missing {missing}, extra {sorted(extra)})")
        times = {}
        for k in ("arrival_p10_at", "arrival_p50_at", "arrival_at"):
            try:
                times[k] = _utc(est.get(k))
            except ValueError as e:
                raise ValueError(f"estimate {asset_id}: {k}: {e}") from e
            if times[k] is not None and times[k] > horizon:
                raise ValueError(f"estimate {asset_id}: {k} is after forecast_horizon_at")
        p10, p50 = times["arrival_p10_at"], times["arrival_p50_at"]
        if p10 is not None and p50 is not None and p10 > p50:
            raise ValueError(f"estimate {asset_id}: arrival_p10_at is after arrival_p50_at")
        bp = est["burn_probability"]
        if bp is not None:
            if isinstance(bp, bool) or not isinstance(bp, (int, float)) or math.isnan(bp) or not 0.0 <= bp <= 1.0:
                raise ValueError(f"estimate {asset_id}: burn_probability must be in [0, 1] or null")


def load_forecast(path) -> dict:
    """Read and validate a forecast-input-1 JSON file; ValueError on a bad file."""
    path = Path(path)
    try:
        forecast = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not JSON ({e})") from e
    try:
        validate_forecast(forecast)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e
    return forecast


# ---------------------------------------------------------------------------------------- attach
def _mark_unavailable(rec: dict) -> None:
    if FORECAST_UNAVAILABLE not in rec["review_reasons"]:
        rec["review_reasons"].append(FORECAST_UNAVAILABLE)
    rec["needs_review"] = True


def _method_note(forecast: dict, basis: str | None) -> str:
    if basis is None:
        selected = "no arrival within the horizon: fire_arrival_at null"
    elif basis in ("p10", "p50"):
        selected = f"fire_arrival_at = arrival_{basis}_at"
    else:
        selected = f"fire_arrival_at = arrival_at ({basis})"
    label = SYNTHETIC_LABEL if forecast["input_mode"] == "synthetic" else f"{forecast['input_mode']} provider response"
    return (f"per-location estimates from {forecast['forecast_source']} ({forecast['input_mode']} "
            f"{SCHEMA_VERSION} file, declared basis {forecast['basis']}); {selected}; {label}")


def attach_forecast(assets, forecast) -> None:
    """Fill the forecast fields of `assets` (a list of asset records) IN PLACE from `forecast`.

    Every asset is touched: a located asset whose `asset_id` is in `estimates` gets `burn_probability`,
    `arrival_p10_at`, `arrival_p50_at`, `forecast_horizon_at`, `forecast_source`, and `fire_arrival_at`
    = p10 (basis "p10") else p50 (basis "p50") else the estimate's `arrival_at` (basis = the forecast's
    `basis`) else null, plus one `sources` entry listing exactly the fields set (`observed_at` = the
    file's `issued_at`). Any asset left without `fire_arrival_at`
    (no coordinates, not in `estimates`, or no arrival within the horizon) gets `forecast_unavailable`
    in `review_reasons`. Previously attached forecast fields on covered assets are overwritten;
    uncovered assets keep whatever forecast fields they had only if those are already null.
    """
    validate_forecast(forecast)
    issued = _iso(forecast["issued_at"])
    horizon = _iso(forecast["forecast_horizon_at"])
    estimates = forecast["estimates"]
    for rec in assets:
        located = rec.get("latitude") is not None and rec.get("longitude") is not None
        est = estimates.get(rec["asset_id"]) if located else None
        if est is None:
            _mark_unavailable(rec)
            continue
        p10, p50, single = _iso(est["arrival_p10_at"]), _iso(est["arrival_p50_at"]), _iso(est.get("arrival_at"))
        bp = est["burn_probability"]
        if p10 is not None:
            arrival, basis = p10, "p10"
        elif p50 is not None:
            arrival, basis = p50, "p50"
        elif single is not None:
            arrival, basis = single, forecast["basis"]
        else:
            arrival, basis = None, None
        values = {
            "burn_probability": None if bp is None else round(float(bp), 3),
            "arrival_p10_at": p10,
            "arrival_p50_at": p50,
            "forecast_horizon_at": horizon,
            "forecast_source": forecast["forecast_source"],
            "fire_arrival_at": arrival,
            "fire_arrival_basis": basis,
        }
        rec.update(values)
        fields = [k for k in FORECAST_FIELDS if values[k] is not None]
        rec.setdefault("sources", []).append({
            "fields": fields, "source": forecast["forecast_source"], "observed_at": issued,
            "available_at": forecast.get("received_at"), "fetched_at": forecast.get("received_at"),
            "notes": _method_note(forecast, basis)})
        if arrival is None:
            _mark_unavailable(rec)


# ---------------------------------------------------------------------------- Deepfire fire-spread
# Verified on 2026-09-19 (fixtures/fire/deepfire/README.md): POST /v1/fire-spread/simulations returns a
# body {id, status, model, durationHours, ensembleMembers, latitude, longitude, locationName,
# ignitionPointCount, ignition, createdAt, summary, result, links}. `result.features` are CUMULATIVE
# burned-area MultiPolygons per hour with properties {hour, elapsed_seconds}; an ensemble run splits each
# hour into disjoint bands with an extra `burn_probability` (multiples of 1/ensembleMembers), which the
# adapter keeps as the asset's `burn_probability`. No absolute time other than createdAt is given, so
# t0 = createdAt is an assumption stated in the note.
T0_ASSUMPTION = "t0 = createdAt (the response states no simulation start time)"


def _spread_bands(features):
    """[(hour, elapsed_seconds, burn_probability, geometry)] for every feature, ascending by hour.

    Each provider polygon is validated once with `shapely.make_valid` (the recorded ensemble bands are
    not all valid) and reused by both the arrival union and the band-probability scan. Missing
    `burn_probability` is 1.0: a single-member run carries no band probability."""
    bands = []
    for ft in features:
        props = ft.get("properties") or {}
        if "hour" not in props or "elapsed_seconds" not in props:
            raise ValueError("fire-spread feature without hour/elapsed_seconds")
        bp = props.get("burn_probability")
        bp = 1.0 if bp is None else float(bp)      # a single-member run carries no band probability
        bands.append((int(props["hour"]), int(props["elapsed_seconds"]), bp, make_valid(shape(ft["geometry"]))))
    bands.sort(key=lambda b: (b[0], b[1]))
    return bands


def _spread_hours(bands, min_burn_probability: float | None):
    """{hour: (elapsed_seconds, shapely geometry)} of the covering polygon per hour, ascending.

    One member: the hour's geometry. Ensemble: union of that hour's bands whose `burn_probability` is
    >= `min_burn_probability`. This selects the arrival only; the band-probability scan below reads
    every band, whatever its probability."""
    per_hour: dict[tuple[int, int], list] = {}
    for hour, elapsed, bp, geom in bands:
        if min_burn_probability is not None and bp < min_burn_probability:
            continue
        per_hour.setdefault((hour, elapsed), []).append(geom)
    out = {}
    for (hour, elapsed), geoms in sorted(per_hour.items()):
        out[hour] = (elapsed, unary_union(geoms) if len(geoms) > 1 else geoms[0])
    return out


def _probability_levels(bands):
    """[(burn_probability, geometry)] descending, one geometry per distinct band probability.

    Bands of the same probability are unioned across every hour, so the maximum band probability
    covering a point is the first level whose geometry covers it: at most one containment test per
    distinct probability (a multiple of 1/ensembleMembers), not one per feature."""
    per_prob: dict[float, list] = {}
    for _hour, _elapsed, bp, geom in bands:
        per_prob.setdefault(bp, []).append(geom)
    return [(bp, unary_union(geoms) if len(geoms) > 1 else geoms[0])
            for bp, geoms in sorted(per_prob.items(), reverse=True)]


def deepfire_spread_to_forecast(body: dict, received_at, asset_points: dict, *, min_burn_probability=None,
                                input_mode: str = "recorded") -> dict:
    """Deepfire fire-spread simulation response -> forecast-input-1 dict (hourly isochrone crossing).

    `asset_points` is `{asset_id: (longitude, latitude)}` for the located assets; every one of them gets
    an estimate. For each hour in ascending order the covering polygon is the hour's cumulative burned
    area (one member) or the union of that hour's bands with `burn_probability >= min_burn_probability`
    (ensemble; default 1/ensembleMembers, i.e. reached by any member: the conservative earliest arrival).
    The first hour whose polygon covers the facility point gives `arrival_at = createdAt +
    elapsed_seconds`; a point no such polygon covers keeps `arrival_at` null and `attach_forecast` marks
    it forecast_unavailable. `burn_probability` is the maximum band probability among the bands covering
    the point at any hour within the horizon -- every band is scanned, including those below
    `min_burn_probability`, which only selects the arrival union -- and `0.0` when no band covers it,
    which is the run's own statement about that location, not a distance inference. A single-member run
    carries no band probability, so a covered point is 1.0 and an uncovered one 0.0.
    `forecast_horizon_at = createdAt + durationHours` makes the horizon explicit. Point-in-polygon is
    evaluated in WGS84 (containment only, no distances). `input_mode` is "recorded" when replayed from a
    file and "live" when polled. Status other than COMPLETED -> ValueError.
    """
    if not isinstance(body, dict):
        raise ValueError("fire-spread body is not a dict")
    if body.get("status") != "COMPLETED":
        raise ValueError(f"fire-spread run status {body.get('status')!r} is not COMPLETED")
    for key in ("id", "model", "durationHours", "ensembleMembers", "createdAt", "result"):
        if key not in body:
            raise ValueError(f"fire-spread body missing {key}")
    if input_mode not in ("recorded", "live"):
        raise ValueError(f"input_mode {input_mode!r} must be recorded or live")
    members = int(body["ensembleMembers"])
    if members < 1:
        raise ValueError("ensembleMembers must be >= 1")
    if min_burn_probability is None:
        min_burn_probability = 1.0 / members
    created = _utc(body["createdAt"])
    horizon = created + timedelta(hours=float(body["durationHours"]))
    features = (body["result"] or {}).get("features") or []
    bands = _spread_bands(features)
    hours = _spread_hours(bands, min_burn_probability)      # arrival: bands at or above the member fraction
    levels = _probability_levels(bands)                     # burn probability: every band, highest first
    estimates = {}
    for asset_id, (lon, lat) in sorted(asset_points.items()):
        pt = Point(float(lon), float(lat))
        arrival = None
        for _hour, (elapsed, geom) in hours.items():
            if geom.covers(pt):
                arrival = created + timedelta(seconds=elapsed)
                break
        burn_probability = 0.0                              # no band covers it within the horizon
        for level, geom in levels:
            if geom.covers(pt):
                burn_probability = level
                break
        estimates[asset_id] = {"arrival_p10_at": None, "arrival_p50_at": None,
                               "burn_probability": burn_probability,
                               "arrival_at": None if arrival is None else arrival.isoformat()}
    if members == 1:
        basis = (f"hourly isochrone crossing, deterministic {body['model']}, burn probability 1.0 inside the "
                 f"burned area and 0.0 outside it, {T0_ASSUMPTION.split(' (')[0]}")
    else:
        basis = (f"hourly isochrone crossing, member fraction >= {min_burn_probability:g}, burn probability = "
                 f"max band probability covering the point, {body['model']}, {T0_ASSUMPTION.split(' (')[0]}")
    summary = body.get("summary") or {}
    note = (f"Deepfire fire-spread simulation {body['id']} ({body['model']}, {members} member(s), "
            f"{body['durationHours']} h, {body.get('ignitionPointCount')} ignition point(s) at "
            f"{body.get('latitude')},{body.get('longitude')} {body.get('locationName')!r}, createdAt {body['createdAt']}, "
            f"burnedAreaM2 {summary.get('burnedAreaM2')}, edgeReached {summary.get('edgeReached')}); "
            f"per-location arrival = first hourly cumulative burned-area polygon"
            + (f" (union of bands with burn_probability >= {min_burn_probability:g})" if members > 1 else "")
            + f" covering the facility point, elapsed_seconds after t0; {T0_ASSUMPTION}; assets not covered by hour "
            f"{max(hours) if hours else 0} keep arrival_at null. burn_probability = the maximum band probability "
            "among the bands covering the point at any hour within the horizon, and 0.0 when no band covers it "
            "(the run's statement about that location, not a distance inference); every band is scanned, including "
            + (f"those below the member fraction {min_burn_probability:g} used for the arrival union, and band "
               f"probabilities are multiples of 1/{members}. " if members > 1 else
               "bands of any probability, and a single-member run carries no band probability, so a covered point "
               "is 1.0 and an uncovered one 0.0. ")
            + f"Received {_iso(received_at)}. Simulated point ignition, "
            "not an observed fire; no distance-based fallback.")
    forecast = {
        "schema_version": SCHEMA_VERSION,
        "forecast_source": f"deepfire:fire-spread/{body['model']}/{body['id']}",
        "input_mode": input_mode,
        "issued_at": created.isoformat(),
        "received_at": _iso(received_at),          # optional: when the response was received (sources fetched_at)
        "forecast_horizon_at": horizon.isoformat(),
        "basis": basis,
        "note": note,
        "estimates": estimates,
    }
    validate_forecast(forecast)
    return forecast


def read_recorded_spread(path) -> dict:
    """Read a recorded fire-spread record `{"collection", "received_at", "body", "note"}`; ValueError on a bad file."""
    path = Path(path)
    rec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rec, dict) or not all(k in rec for k in ("collection", "received_at", "body")):
        raise ValueError(f"{path}: not a recorded response (collection, received_at, body)")
    if "fire-spread" not in str(rec["collection"]):
        raise ValueError(f"{path}: collection {rec['collection']!r} is not a fire-spread simulation")
    return rec


def forecast_from_recorded_spread(path, asset_points: dict, *, min_burn_probability=None) -> dict:
    """`deepfire_spread_to_forecast` on a recorded response file (input_mode "recorded")."""
    rec = read_recorded_spread(path)
    return deepfire_spread_to_forecast(rec["body"], rec["received_at"], asset_points,
                                       min_burn_probability=min_burn_probability, input_mode="recorded")
