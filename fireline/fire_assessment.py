"""Bounded discovery upstream of risk assessment and analyst coordination.

The search radius describes catalog coverage, never a fire-arrival estimate.
Gencat's registered facility classes do not cover every building or private home.
Unknown catalog locations are retained for review, not counted as nearby matches.
"""

import math
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

from pyproj import CRS, Transformer
from shapely import get_coordinates
from shapely.geometry import Point, shape
from shapely.ops import transform

from . import config, feeds
from .forecast_input import deepfire_spread_to_forecast, validate_forecast
from .snapshot import INPUT_MODES, build_snapshot, validate_snapshot

MAX_SEARCH_RADIUS_M = 50_000


def _time(value, field, *, optional=False):
    if value is None and optional:
        return None
    try:
        dt = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError
        return dt.astimezone(UTC)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp") from exc


def _geometry(value, field):
    try:
        if not isinstance(value, dict) or value.get("type") not in ("Point", "Polygon", "MultiPolygon"):
            raise ValueError
        geom = shape(value)
        coords = get_coordinates(geom)
        if geom.is_empty or not geom.is_valid or geom.has_z:
            raise ValueError
        if any(not math.isfinite(x) or not math.isfinite(y) or not -180 <= x <= 180
               or not -90 <= y <= 90 for x, y in coords):
            raise ValueError
        return geom
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ValueError(f"{field} must be valid nonempty 2D WGS84 Point/Polygon/MultiPolygon geometry") from exc


def _mode(payload, input_mode, field):
    declared = payload.get("input_mode")
    if declared is not None and declared != input_mode:
        raise ValueError(f"{field} input_mode {declared!r} does not match {input_mode!r}")
    if input_mode == "live":
        labels = " ".join(str(payload.get(k) or "") for k in
                          ("source", "provider", "forecast_source", "raw_ref")).lower()
        if any(word in labels for word in ("fixture", "synthetic", "recorded")):
            raise ValueError(f"{field} recorded/synthetic provenance cannot be labelled live")


def _check_forecast(forecast, input_mode, as_of):
    validate_forecast(forecast)
    _mode(forecast, input_mode, "forecast")
    for key in ("issued_at", "forecast_horizon_at", "received_at"):
        dt = _time(forecast.get(key), f"forecast.{key}", optional=key == "received_at")
        if key == "issued_at" and dt > as_of:
            raise ValueError("forecast issued_at is after as_of")
    for est in forecast["estimates"].values():
        for key in ("arrival_p10_at", "arrival_p50_at", "arrival_at"):
            _time(est.get(key), f"forecast.{key}", optional=True)


def _location(row):
    modern = any(key in row for key in ("asset_type", "latitude", "capacity"))
    lon_key, lat_key = ("longitude", "latitude") if modern else ("lon", "lat")
    lon, lat = row.get(lon_key), row.get(lat_key)
    if lon is None or lat is None:
        # An incomplete pair cannot be a usable point; preserve it as unknown.
        row[lon_key] = row[lat_key] = None
        return None
    try:
        lon, lat = float(lon), float(lat)
        if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ValueError(f"asset {row.get('asset_id')!r} coordinates must be finite WGS84") from exc
    return lon, lat


def _registered_facilities(raw):
    """Keep registered identities even when the existing class rules do not match.

    Unknown classes receive no inferred occupancy or value. Only public identity,
    coordinates and register provenance are copied from unmatched provider rows.
    """
    for row in raw:
        identity = row.get("idequipament") if isinstance(row, dict) else None
        if type(identity) not in (str, int) or not str(identity).strip():
            raise ValueError("fetched facility requires a stable idequipament identity")
    located, unlocated = feeds.registers_to_assets(equipaments=raw)
    rows = located + unlocated
    for row in raw:
        if feeds.classify("equipaments", row) is not None:
            continue
        lon, lat = feeds._lonlat(row)
        rows.append({"asset_id": f"equipaments:{row['idequipament']}", "name": row.get("nom"),
                     "asset_class": "unknown", "lon": lon, "lat": lat,
                     "register": "equipaments", "class_ambiguous": True,
                     "note": "registered facility category has no matching assessment class; human classification required"})
    return rows


def assess_fire(fire, *, scenario_id, sequence, as_of, input_mode, search_radius_m,
                facility_rows=None, forecast=None, spread=None, fetcher=None):
    """Return ``{snapshot: v1.1, discovery: metadata}`` for one fire update.

    ``facility_rows`` accepts assets_in or snapshot asset rows. Otherwise ``fetcher``
    (default ``feeds.equipaments``) receives ``bbox=`` and returns raw register rows.
    ``spread`` accepts a Deepfire response envelope with ``body``, ``received_at``
    and ``input_mode``; unlabelled responses are recorded only. A forecast must
    have the same input mode as the snapshot. Provider errors propagate to callers.
    """
    if input_mode not in INPUT_MODES:
        raise ValueError(f"input_mode must be one of {INPUT_MODES}")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ValueError("scenario_id must be a nonempty string")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValueError("sequence must be an integer >= 1")
    if (isinstance(search_radius_m, bool) or not isinstance(search_radius_m, (int, float))
            or not math.isfinite(search_radius_m) or not 0 < search_radius_m <= MAX_SEARCH_RADIUS_M):
        raise ValueError(f"search_radius_m must be positive and <= {MAX_SEARCH_RADIUS_M}")
    now = _time(as_of, "as_of")
    if not isinstance(fire, dict):
        raise ValueError("fire must be a FireUpdate object")  # noqa: TRY004 — public validation contract
    for key in ("incident_id", "source"):
        if not isinstance(fire.get(key), str) or not fire[key].strip():
            raise ValueError(f"fire.{key} must be a nonempty string")
    _mode(fire, input_mode, "fire")
    for key in ("observed_at", "received_at"):
        stamp = _time(fire.get(key), f"fire.{key}", optional=True)
        if key == "observed_at" and stamp is not None and stamp > now:
            raise ValueError("fire observed_at is after as_of")
    geom = _geometry(fire.get("geometry"), "fire.geometry")
    kind = fire.get("geometry_kind")
    allowed = ("hotspot_centre",) if geom.geom_type == "Point" else ("perimeter", "simulated")
    if kind is not None and kind not in allowed:
        raise ValueError("fire geometry_kind is incompatible with geometry")
    if forecast is not None and spread is not None:
        raise ValueError("provide forecast or spread, not both")
    if forecast is not None:
        _check_forecast(forecast, input_mode, now)
    spread_body = None
    if spread is not None:
        if not isinstance(spread, dict):
            raise ValueError("spread must be a response object")
        spread_mode = spread.get("input_mode", "recorded")
        if spread_mode != input_mode or input_mode not in ("recorded", "live"):
            raise ValueError("spread input_mode must match the snapshot and be recorded or live")
        _mode(spread, input_mode, "spread")
        spread_body = spread.get("body", spread)
        spread_received = _time(spread.get("received_at", as_of), "spread.received_at")

    # A local azimuthal equidistant projection gives metres around the incident;
    # inverse bounds are densified to avoid dropping edge candidates in the query.
    centre = geom.centroid
    crs = CRS.from_proj4(f"+proj=aeqd +lat_0={centre.y} +lon_0={centre.x} +datum=WGS84 +units=m")
    forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    reverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    projected_fire = transform(forward.transform, geom)
    west, south, east, north = projected_fire.bounds
    radius = float(search_radius_m)
    bbox = reverse.transform_bounds(west - radius, south - radius, east + radius,
                                    north + radius, densify_pts=41)
    if not all(math.isfinite(n) for n in bbox) or bbox[0] > bbox[2]:
        raise ValueError("fire geometry search bounds cross unsupported antimeridian/polar coverage")
    fetched_at = None
    if facility_rows is None:
        raw = list((fetcher or feeds.equipaments)(bbox=tuple(bbox)))
        fetched_at = datetime.now(UTC).isoformat()
        rows = _registered_facilities(raw)
        for row in rows:
            row["fetched_at"] = fetched_at
        source = "gencat:equipaments:8gmd-gz7i"
        coverage = ("Returned Gencat registered facilities within the search radius, including unknown classes; "
                    "not a complete buildings inventory and does not cover all private homes. "
                    "Unlocated returned facilities require location review.")
        raw_count = len(raw)
    else:
        rows = deepcopy(list(facility_rows))
        source = "supplied_catalog"
        coverage = ("Supplied catalog only; completeness is unknown. Unlocated rows are retained "
                    "for review and are not confirmed nearby matches.")
        raw_count = len(rows)
    selected, unknown, points = [], [], {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("asset_id"), str) or not row["asset_id"]:
            raise ValueError("facility rows require a nonempty asset_id")
        _mode(row, input_mode, "facility")
        point = _location(row)
        footprint = row.get("geometry")
        asset_geom = (_geometry(footprint, "facility.geometry") if footprint is not None
                      else Point(*point) if point is not None else None)
        if asset_geom is None:
            unknown.append(row["asset_id"])
        elif transform(forward.transform, asset_geom).distance(projected_fire) > radius:
            continue
        selected.append(row)
        if point is not None:
            points[row["asset_id"]] = point
    if spread_body is not None:
        forecast = deepfire_spread_to_forecast(spread_body, spread_received, points, input_mode=input_mode)
        _check_forecast(forecast, input_mode, now)
    cfg = SimpleNamespace(**{name: deepcopy(getattr(config, name))
                             for name in dir(config) if name.isupper()})
    cfg.FEATURES["value_at_risk"] = True
    snap = build_snapshot(selected, deepcopy(fire), scenario_id=scenario_id,
                          incident_id=fire["incident_id"], sequence=sequence, as_of=now,
                          input_mode=input_mode, forecast=forecast, cfg=cfg)
    errors = validate_snapshot(snap)
    if errors:
        raise ValueError("invalid assessment snapshot: " + "; ".join(errors))
    discovery = {"source": source, "input_mode": input_mode, "search_radius_m": radius,
                 "bbox_wgs84": list(bbox), "coverage": coverage, "fetched_at": fetched_at,
                 "raw_count": raw_count, "candidate_count": len(rows),
                 "included_count": len(selected), "nearby_count": len(selected) - len(unknown),
                 "excluded_count": len(rows) - len(selected), "unlocated_asset_ids": unknown,
                 "distance_basis": "local WGS84 azimuthal equidistant metric distance to fire geometry"}
    return {"snapshot": snap, "discovery": discovery}
