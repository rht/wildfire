"""Fire updates: Deepfire poll or recorded responses -> ``FireUpdate`` (CONTRACTS.md section 3).

One processing path for live and recorded inputs (readme 3): :func:`poll_deepfire` fetches through
:class:`fireline.feeds.DeepfireClient` (file cache, ``max_age_s``, busy retries) and
:func:`load_recorded` reads ``fixtures/fire/deepfire/*.json``; both hand the raw GeoJSON body to
:func:`parse_deepfire`. Downstream, :class:`UpdateCache` deduplicates, :func:`data_status` labels
freshness with ``config.FRESHNESS`` and :func:`measure_update` keeps source age and processing time
as two separate numbers (readme 11).

A hotspot centre is never presented as a surveyed perimeter: only ``satellite-perimeters`` features
with Polygon/MultiPolygon geometry become ``geometry_kind == "perimeter"``; a cluster (or hotspot)
feature is always ``"hotspot_centre"`` with a Point geometry, whatever the provider drew.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform

from . import config as default_config
from .feeds import FeedError, LeakError, to_utc
from .grid import xy_to_lonlat

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RECORDED_DIR = ROOT / "fixtures" / "fire" / "deepfire"

PERIMETER_COLLECTION = "satellite-perimeters"
CLUSTER_COLLECTION = "clusters"
HOTSPOT_COLLECTION = "hotspots"
# Poll order for the live path: a surveyed perimeter beats a hotspot centre.
POLL_COLLECTIONS = (PERIMETER_COLLECTION, CLUSTER_COLLECTION)

# Observation-time fields per collection, first usable wins (docs.deepfire.co, read 2026-09-19;
# see data/README.md). ``observed_watermark`` is the acquisition time of the newest hotspot in a
# perimeter and is nullable, so ``computed_at`` is the fallback.
OBSERVED_FIELDS = {
    PERIMETER_COLLECTION: ("observed_watermark", "computed_at"),
    CLUSTER_COLLECTION: ("last_observed", "first_observed"),
    HOTSPOT_COLLECTION: ("observed_at",),
}
INCIDENT_FIELDS = ("cluster_id", "id")
UPDATE_KEYS = ("provider", "incident_id", "observed_at", "received_at", "geometry", "geometry_kind",
               "source", "raw_ref")


# --------------------------------------------------------------------------- time helpers


def _iso(dt: datetime | None) -> str | None:
    """Aware UTC datetime -> ``2026-07-03T08:00:00+00:00`` (CONTRACTS section 1)."""
    if dt is None:
        return None
    return to_utc(dt).isoformat(timespec="seconds")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- parsing


def _feature_observed(props: dict, collection: str) -> tuple[datetime | None, str | None]:
    """(observation time, name of the field it came from) for one feature's properties."""
    for field in OBSERVED_FIELDS.get(collection, ()):
        try:
            ts = to_utc(props.get(field))
        except (ValueError, TypeError):
            ts = None
        if ts is not None:
            return ts, field
    return None, None


def _incident_id(feature: dict, props: dict, collection: str) -> str:
    for field in INCIDENT_FIELDS:
        v = props.get(field)
        if v not in (None, ""):
            return str(v)
    if feature.get("id") not in (None, ""):
        return str(feature["id"])
    return f"deepfire:{collection}:unknown"


def _normalise_geometry(geometry: dict | None, collection: str) -> tuple[dict | None, str | None, str | None]:
    """GeoJSON geometry -> (geometry, geometry_kind, note).

    Perimeter collections keep Polygon/MultiPolygon as ``perimeter``. Anything else, and every
    cluster/hotspot feature, is reduced to a Point ``hotspot_centre`` (the centroid when the
    provider drew an area), because only a satellite-derived perimeter is a surveyed footprint.
    """
    if not isinstance(geometry, dict) or not geometry.get("type"):
        return None, None, "feature had no geometry"
    gtype = geometry["type"]
    if collection == PERIMETER_COLLECTION and gtype in ("Polygon", "MultiPolygon"):
        return geometry, "perimeter", None
    if gtype == "Point":
        return geometry, "hotspot_centre", None
    try:
        centre = shape(geometry).centroid
    except Exception as exc:  # malformed geometry: unknown, not zero
        return None, None, f"unusable {gtype} geometry: {exc}"
    return ({"type": "Point", "coordinates": [centre.x, centre.y]}, "hotspot_centre",
            f"{gtype} geometry from {collection} reduced to its centroid (not a surveyed perimeter)")


def _feature_key(item: tuple[int, datetime | None, dict]) -> tuple[int, float]:
    """Sort key: newest observation wins, undated features lose, later position breaks ties."""
    index, ts, _ = item
    return (0, index) if ts is None else (1, ts.timestamp())


def parse_deepfire(body: dict | list | None, received_at: datetime, collection: str, *,
                   raw_ref: str | None = None) -> dict | None:
    """One OGC Features response body -> the newest ``FireUpdate`` it contains, or None if empty.

    ``body`` is a FeatureCollection, a single Feature or a list of Features. When several
    features come back the one with the newest observation time is taken (undated features only
    if nothing is dated). Features without usable geometry are ignored.
    """
    if body is None:
        return None
    if isinstance(body, list):
        features = body
    elif body.get("type") == "Feature":
        features = [body]
    else:
        features = body.get("features") or []
    dated = []
    for i, feat in enumerate(features):
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        ts, _ = _feature_observed(props, collection)
        dated.append((i, ts, feat))
    for _, ts, feat in sorted(dated, key=_feature_key, reverse=True):
        props = feat.get("properties") or {}
        geometry, kind, note = _normalise_geometry(feat.get("geometry"), collection)
        if geometry is None:
            log.info("skipping %s feature %s: %s", collection, feat.get("id"), note)
            continue
        _, basis = _feature_observed(props, collection)
        notes = [n for n in (f"observed_at from {basis}" if basis else "no observation time", note) if n]
        return {
            "provider": "deepfire",
            "incident_id": _incident_id(feat, props, collection),
            "observed_at": _iso(ts),
            "received_at": _iso(received_at),
            "geometry": geometry,
            "geometry_kind": kind,
            "source": f"deepfire:{collection}",
            "raw_ref": raw_ref,
            "notes": "; ".join(notes),
        }
    return None


# --------------------------------------------------------------------------- live path


def _matches_incident(feature: dict, incident_id: str) -> bool:
    props = feature.get("properties") or {}
    return incident_id in {str(props.get(f)) for f in INCIDENT_FIELDS} | {str(feature.get("id"))}


def poll_deepfire(client, bbox: tuple, as_of: datetime, *, incident_id: str | None = None) -> dict | None:
    """Live path: satellite perimeters first, then clusters, through ``DeepfireClient``.

    Rate limits, retries and the file cache are the client's (``feeds.cached_get`` with the
    client's ``max_age_s``). A ``FeedError`` (no credentials, network down with no cache, API
    error) or ``LeakError`` on one collection is logged and the next collection is tried; None
    means nothing usable came back and the caller should keep its last valid update.
    """
    received_at = _now()
    methods = {PERIMETER_COLLECTION: getattr(client, "satellite_perimeters", None),
               CLUSTER_COLLECTION: getattr(client, "clusters", None)}
    for collection in POLL_COLLECTIONS:
        fetch = methods[collection]
        if fetch is None:
            continue
        try:
            features = fetch(bbox, as_of)
        except (FeedError, LeakError) as exc:
            log.warning("deepfire %s unavailable: %s", collection, exc)
            continue
        if incident_id is not None:
            features = [f for f in features if _matches_incident(f, incident_id)]
        update = parse_deepfire({"type": "FeatureCollection", "features": list(features)}, received_at, collection)
        if update is not None:
            return update
    return None


# --------------------------------------------------------------------------- recorded path


def record_response(collection: str, body: Any, received_at: datetime, out_dir: str | Path, *,
                    note: str | None = None) -> Path:
    """Write one raw provider response as ``{collection, received_at, body}`` and return its path.

    File name ``<received_at>_<collection>.json`` so a directory listing is already in time order.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = to_utc(received_at).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"{stamp}_{collection}.json"
    record = {"collection": collection, "received_at": _iso(received_at), "body": body}
    if note:
        record["note"] = note
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def _recorded_files(dir_or_files: str | Path | Iterable[str | Path]) -> list[Path]:
    if isinstance(dir_or_files, (str, Path)):
        p = Path(dir_or_files)
        return sorted(p.glob("*.json")) if p.is_dir() else [p]
    return [Path(f) for f in dir_or_files]


def load_recorded(dir_or_files: str | Path | Iterable[str | Path] = RECORDED_DIR) -> list[dict]:
    """Recorded responses (``{collection, received_at, body}``) -> FireUpdates in ``received_at`` order.

    Each file goes through :func:`parse_deepfire` exactly like a live response; ``raw_ref`` is
    the file path. Files that are not this shape or hold no usable feature are skipped with a log.
    """
    records = []
    for path in _recorded_files(dir_or_files):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            received = to_utc(rec["received_at"])
            collection, body = rec["collection"], rec["body"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("skipping recorded response %s: %s", path, exc)
            continue
        records.append((received, path.name, collection, body, path))
    updates = []
    for received, _, collection, body, path in sorted(records, key=lambda r: (r[0], r[1])):
        update = parse_deepfire(body, received, collection, raw_ref=str(path))
        if update is None:
            log.info("recorded response %s holds no usable feature", path)
            continue
        updates.append(update)
    return updates


# --------------------------------------------------------------------------- dedupe


def geometry_hash(geometry: dict | None) -> str:
    return hashlib.sha1(json.dumps(geometry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def update_key(update: dict) -> tuple[str, str | None, str]:
    return (str(update.get("incident_id")), update.get("observed_at"), geometry_hash(update.get("geometry")))


class UpdateCache:
    """Cache of the latest valid update; ``accept`` is False for duplicates and for regressions.

    A duplicate is the same ``(incident_id, observed_at, sha1(geometry))``. An update observed
    earlier than the current ``latest`` is rejected too (readme 11: older updates cannot regress
    state). Updates without geometry are never valid.
    """

    def __init__(self) -> None:
        self._seen: set[tuple] = set()
        self.latest: dict | None = None
        self.history: list[dict] = []

    def accept(self, update: dict | None) -> bool:
        if not update or update.get("geometry") is None or update.get("geometry_kind") is None:
            return False
        key = update_key(update)
        if key in self._seen:
            log.info("duplicate update ignored: %s", key[:2])
            return False
        if self.latest is not None:
            new, cur = to_utc(update.get("observed_at")), to_utc(self.latest.get("observed_at"))
            if new is not None and cur is not None and new < cur:
                log.info("older update ignored: %s < %s", new.isoformat(), cur.isoformat())
                return False
        self._seen.add(key)
        self.latest = update
        self.history.append(update)
        return True


# --------------------------------------------------------------------------- freshness and latency


def source_age_s(observed_at: datetime | str | None, now: datetime | str) -> float | None:
    """Seconds between the provider observation and ``now``; None when unknown."""
    obs = to_utc(observed_at)
    if obs is None:
        return None
    return (to_utc(now) - obs).total_seconds()


def data_status(observed_at: datetime | str | None, now: datetime | str, cfg=default_config) -> str:
    """``current`` below ``stale_after_s``, ``stale`` up to ``unavailable_after_s``, else ``unavailable``."""
    age = source_age_s(observed_at, now)
    if age is None:
        return "unavailable"
    fresh = cfg.FRESHNESS
    if age >= fresh["unavailable_after_s"]:
        return "unavailable"
    if age >= fresh["stale_after_s"]:
        return "stale"
    return "current"


class Stopwatch:
    """Receipt-to-snapshot processing time in seconds; also a context manager."""

    def __init__(self) -> None:
        self._t0: float | None = None
        self.processing_s: float | None = None

    def start(self) -> "Stopwatch":
        self._t0 = time.perf_counter()
        self.processing_s = None
        return self

    def stop(self) -> float:
        if self._t0 is None:
            raise RuntimeError("Stopwatch.stop() before start()")
        self.processing_s = time.perf_counter() - self._t0
        return self.processing_s

    @property
    def elapsed(self) -> float | None:
        if self._t0 is None:
            return None
        return self.processing_s if self.processing_s is not None else time.perf_counter() - self._t0

    def __enter__(self) -> "Stopwatch":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def _seconds_between(start: Any, end: Any) -> float | None:
    if start is None or end is None:
        return None
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return float(end) - float(start)
    return (to_utc(end) - to_utc(start)).total_seconds()


def measure_update(update: dict | None, started_at: Any, finished_at: Any, now: datetime | str | None = None) -> dict:
    """Snapshot ``metrics``: ``source_age_s`` (observation -> now) and ``processing_s`` (receipt -> done).

    The two are never combined (readme 11). ``started_at``/``finished_at`` are datetimes or
    monotonic seconds; ``now`` defaults to ``finished_at`` when it is a datetime.
    """
    if now is None and isinstance(finished_at, (datetime, str)):
        now = finished_at
    observed = update.get("observed_at") if update else None
    return {
        "source_age_s": source_age_s(observed, now) if now is not None else None,
        "processing_s": _seconds_between(started_at, finished_at),
    }


# --------------------------------------------------------------------------- synthetic (v0 FireState)


def geometry_to_wgs84(geom, ndigits: int = 7) -> dict:
    """Shapely geometry in EPSG:25831 -> GeoJSON geometry in WGS84 lon/lat."""
    wgs = shp_transform(lambda x, y, z=None: xy_to_lonlat(x, y), geom)
    return json.loads(json.dumps(mapping(wgs)), parse_float=lambda s: round(float(s), ndigits))


def synthetic_update(fire_state, source: str = "fixture:synthetic_ignition") -> dict:
    """v0 ``FireState`` (shapely perimeter in EPSG:25831) -> FireUpdate with a WGS84 perimeter."""
    t = to_utc(fire_state.t)
    return {
        "provider": "fixture",
        "incident_id": str(fire_state.cluster_id),
        "observed_at": _iso(t),
        "received_at": _iso(t),
        "geometry": geometry_to_wgs84(fire_state.perimeter),
        "geometry_kind": "perimeter",
        "source": source,
        "raw_ref": None,
        "notes": "synthetic ignition fixture; observed_at is the scenario time",
    }
