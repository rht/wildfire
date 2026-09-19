"""fire_input.py tests: recorded fixtures through parse_deepfire, dedupe, freshness, latency, live path faked."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from fireline import config, feeds, fire_input
from fireline.fire_state import FireState

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "fixtures" / "fire" / "deepfire"
PERIM_1 = FIXTURE_DIR / "20260703T080500Z_satellite-perimeters.json"
PERIM_2 = FIXTURE_DIR / "20260703T100500Z_satellite-perimeters.json"
CLUSTER = FIXTURE_DIR / "20260703T091530Z_clusters.json"
GAVARRES = feeds.GAVARRES_BBOX
T0 = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _rec(path: Path) -> dict:
    return json.loads(path.read_text())


def _in_bbox(geometry: dict, bbox=GAVARRES) -> bool:
    w, s, e, n = bbox
    coords = geometry["coordinates"]
    pts = [coords] if geometry["type"] == "Point" else [
        p for ring in (coords if geometry["type"] == "Polygon" else [r for poly in coords for r in poly]) for p in ring]
    return all(w <= lon <= e and s <= lat <= n for lon, lat in pts)


# --------------------------------------------------------------------------- parse_deepfire


def test_parse_fixture_perimeter_uses_observed_watermark():
    rec = _rec(PERIM_1)
    upd = fire_input.parse_deepfire(rec["body"], feeds.to_utc(rec["received_at"]), rec["collection"])
    assert upd["provider"] == "deepfire"
    assert upd["source"] == "deepfire:satellite-perimeters"
    assert upd["geometry_kind"] == "perimeter"
    assert upd["geometry"]["type"] == "Polygon"
    assert upd["incident_id"] == "synthetic-gavarres"
    assert upd["observed_at"] == "2026-07-03T07:48:00+00:00"
    assert upd["received_at"] == "2026-07-03T08:05:00+00:00"
    assert "observed_watermark" in upd["notes"]
    assert set(fire_input.UPDATE_KEYS) <= set(upd)
    assert _in_bbox(upd["geometry"])


def test_parse_perimeter_falls_back_to_computed_at_when_watermark_null():
    rec = _rec(PERIM_2)
    assert rec["body"]["features"][0]["properties"]["observed_watermark"] is None
    upd = fire_input.parse_deepfire(rec["body"], feeds.to_utc(rec["received_at"]), rec["collection"])
    assert upd["observed_at"] == "2026-07-03T10:00:00+00:00"
    assert "computed_at" in upd["notes"]
    assert upd["geometry_kind"] == "perimeter"


def test_parse_fixture_cluster_is_hotspot_centre_with_last_observed():
    rec = _rec(CLUSTER)
    upd = fire_input.parse_deepfire(rec["body"], feeds.to_utc(rec["received_at"]), rec["collection"])
    assert upd["source"] == "deepfire:clusters"
    assert upd["geometry_kind"] == "hotspot_centre"
    assert upd["geometry"]["type"] == "Point"
    assert upd["observed_at"] == "2026-07-03T09:10:00+00:00"
    assert upd["incident_id"] == "synthetic-gavarres"
    assert _in_bbox(upd["geometry"])


def test_parse_cluster_polygon_is_never_a_perimeter():
    poly = {"type": "Polygon", "coordinates": [[[3.0, 41.9], [3.02, 41.9], [3.02, 41.92], [3.0, 41.92], [3.0, 41.9]]]}
    body = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": poly, "properties": {"id": "c1", "last_observed": "2026-07-03T09:00:00Z"}}]}
    upd = fire_input.parse_deepfire(body, T0, "clusters")
    assert upd["geometry_kind"] == "hotspot_centre"
    assert upd["geometry"]["type"] == "Point"
    assert upd["geometry"]["coordinates"] == pytest.approx([3.01, 41.91])
    assert "not a surveyed perimeter" in upd["notes"]


def test_parse_picks_newest_feature():
    def feat(fid, computed_at):
        return {"type": "Feature", "id": fid, "properties": {"cluster_id": "x", "computed_at": computed_at,
                                                             "observed_watermark": None},
                "geometry": {"type": "Polygon", "coordinates": [[[3, 41.9], [3.01, 41.9], [3.01, 41.91], [3, 41.9]]]}}
    body = {"type": "FeatureCollection", "features": [
        feat("old", "2026-07-03T06:00:00Z"), feat("newest", "2026-07-03T09:30:00Z"), feat("mid", "2026-07-03T08:00:00Z")]}
    upd = fire_input.parse_deepfire(body, T0, "satellite-perimeters")
    assert upd["observed_at"] == "2026-07-03T09:30:00+00:00"


def test_parse_empty_collection_returns_none():
    assert fire_input.parse_deepfire({"type": "FeatureCollection", "features": []}, T0, "satellite-perimeters") is None
    assert fire_input.parse_deepfire({"type": "FeatureCollection"}, T0, "clusters") is None
    assert fire_input.parse_deepfire(None, T0, "clusters") is None
    no_geom = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None, "properties": {}}]}
    assert fire_input.parse_deepfire(no_geom, T0, "satellite-perimeters") is None


# --------------------------------------------------------------------------- load_recorded


def test_load_recorded_yields_updates_in_time_order():
    updates = fire_input.load_recorded(FIXTURE_DIR)
    assert [u["received_at"] for u in updates] == sorted(u["received_at"] for u in updates)
    assert [u["geometry_kind"] for u in updates] == ["perimeter", "hotspot_centre", "perimeter"]
    assert [u["observed_at"] for u in updates] == [
        "2026-07-03T07:48:00+00:00", "2026-07-03T09:10:00+00:00", "2026-07-03T10:00:00+00:00"]
    assert all(u["raw_ref"] and Path(u["raw_ref"]).exists() for u in updates)
    # explicit file list works too and is still sorted by received_at
    again = fire_input.load_recorded([PERIM_2, CLUSTER, PERIM_1])
    assert [u["observed_at"] for u in again] == [u["observed_at"] for u in updates]


def test_fixture_files_are_labelled_synthetic():
    for path in FIXTURE_DIR.glob("*.json"):
        rec = _rec(path)
        assert {"collection", "received_at", "body"} <= set(rec)
        assert "SYNTHETIC" in rec["note"]


def test_record_response_round_trips_through_load_recorded(tmp_path):
    rec = _rec(CLUSTER)
    path = fire_input.record_response("clusters", rec["body"], datetime(2026, 7, 3, 9, 15, 30, tzinfo=UTC), tmp_path)
    assert path.name == "20260703T091530Z_clusters.json"
    written = json.loads(path.read_text())
    assert written["collection"] == "clusters" and written["received_at"] == "2026-07-03T09:15:30+00:00"
    (upd,) = fire_input.load_recorded(tmp_path)
    assert upd["geometry_kind"] == "hotspot_centre" and upd["raw_ref"] == str(path)


# --------------------------------------------------------------------------- UpdateCache


def test_update_cache_rejects_duplicate_and_older_accepts_newer():
    first, cluster, second = fire_input.load_recorded(FIXTURE_DIR)
    cache = fire_input.UpdateCache()
    assert cache.accept(first) is True
    assert cache.accept(dict(first, received_at="2026-07-03T08:06:00+00:00")) is False  # same obs + geometry
    assert cache.accept(second) is True
    assert cache.latest is second
    assert cache.accept(cluster) is False  # older than the current latest: cannot regress
    assert cache.latest is second
    assert cache.accept({**second, "geometry": None}) is False
    assert cache.accept(None) is False
    assert len(cache.history) == 2


def test_update_cache_same_time_new_geometry_is_not_a_duplicate():
    first, *_ = fire_input.load_recorded(FIXTURE_DIR)
    cache = fire_input.UpdateCache()
    assert cache.accept(first)
    moved = json.loads(json.dumps(first))
    moved["geometry"]["coordinates"][0][0][0] += 0.001
    assert cache.accept(moved) is True


# --------------------------------------------------------------------------- data_status / source_age_s


@pytest.mark.parametrize("age_s,expected", [
    (0, "current"), (3599, "current"), (3600, "stale"), (7200, "stale"), (21599, "stale"),
    (21600, "unavailable"), (86400, "unavailable"),
])
def test_data_status_boundaries(age_s, expected):
    assert config.FRESHNESS == {"stale_after_s": 3600, "unavailable_after_s": 21600}
    observed = T0 - timedelta(seconds=age_s)
    assert fire_input.data_status(observed, T0) == expected
    assert fire_input.data_status(observed.isoformat(), T0.isoformat()) == expected
    assert fire_input.source_age_s(observed, T0) == age_s


def test_data_status_none_is_unavailable():
    assert fire_input.data_status(None, T0) == "unavailable"
    assert fire_input.source_age_s(None, T0) is None


def test_data_status_uses_cfg_thresholds():
    class Cfg:
        FRESHNESS = {"stale_after_s": 10, "unavailable_after_s": 20}
    assert fire_input.data_status(T0 - timedelta(seconds=15), T0, Cfg) == "stale"
    assert fire_input.data_status(T0 - timedelta(seconds=25), T0, Cfg) == "unavailable"


# --------------------------------------------------------------------------- latency


def test_stopwatch_measures_positive_duration():
    sw = fire_input.Stopwatch()
    sw.start()
    time.sleep(0.01)
    s = sw.stop()
    assert s > 0 and sw.processing_s == s
    with fire_input.Stopwatch() as sw2:
        time.sleep(0.001)
    assert sw2.processing_s > 0
    with pytest.raises(RuntimeError):
        fire_input.Stopwatch().stop()


def test_measure_update_keeps_source_age_and_processing_separate():
    upd = {"observed_at": "2026-07-03T11:00:00+00:00"}
    started = datetime(2026, 7, 3, 12, 0, 0, tzinfo=UTC)
    finished = started + timedelta(seconds=12.5)
    m = fire_input.measure_update(upd, started, finished, now=finished)
    assert m == {"source_age_s": 3612.5, "processing_s": 12.5}
    assert fire_input.measure_update(None, 100.0, 101.5, now=finished) == {"source_age_s": None, "processing_s": 1.5}
    assert fire_input.measure_update({"observed_at": None}, started, finished)["source_age_s"] is None


# --------------------------------------------------------------------------- synthetic_update


def test_synthetic_update_from_fixture_fire_state():
    fs = FireState.from_json(ROOT / "fixtures" / "synthetic_ignition.json")
    upd = fire_input.synthetic_update(fs)
    assert upd["provider"] == "fixture"
    assert upd["source"] == "fixture:synthetic_ignition"
    assert upd["geometry_kind"] == "perimeter"
    assert upd["geometry"]["type"] == "Polygon"
    assert upd["observed_at"] == upd["received_at"] == "2026-07-03T08:00:00+00:00"
    assert upd["incident_id"] == fs.cluster_id
    assert upd["raw_ref"] is None
    assert _in_bbox(upd["geometry"])
    lon, lat = upd["geometry"]["coordinates"][0][0]
    assert abs(lon - 3.03) < 0.01 and abs(lat - 41.95) < 0.01
    assert fire_input.UpdateCache().accept(upd)


# --------------------------------------------------------------------------- poll_deepfire (live path, faked)


class FakeClient:
    def __init__(self, perimeters=None, clusters=None, error=None):
        self._p, self._c, self._err, self.calls = perimeters, clusters, error, []

    def satellite_perimeters(self, bbox, as_of):
        self.calls.append(("satellite-perimeters", bbox, as_of))
        if self._err:
            raise self._err
        return list(self._p or [])

    def clusters(self, bbox, as_of, active=None):
        self.calls.append(("clusters", bbox, as_of))
        if self._err:
            raise self._err
        return list(self._c or [])


def test_poll_deepfire_returns_perimeter_update_via_parse_path():
    client = FakeClient(perimeters=_rec(PERIM_1)["body"]["features"], clusters=_rec(CLUSTER)["body"]["features"])
    upd = fire_input.poll_deepfire(client, GAVARRES, T0)
    assert upd["geometry_kind"] == "perimeter" and upd["source"] == "deepfire:satellite-perimeters"
    assert upd["observed_at"] == "2026-07-03T07:48:00+00:00"
    assert client.calls[0][0] == "satellite-perimeters" and len(client.calls) == 1
    assert feeds.to_utc(upd["received_at"]) >= T0  # received now, not at as_of


def test_poll_deepfire_falls_back_to_clusters_and_filters_incident():
    client = FakeClient(perimeters=[], clusters=_rec(CLUSTER)["body"]["features"])
    upd = fire_input.poll_deepfire(client, GAVARRES, T0)
    assert upd["geometry_kind"] == "hotspot_centre" and upd["source"] == "deepfire:clusters"
    assert [c[0] for c in client.calls] == ["satellite-perimeters", "clusters"]
    assert fire_input.poll_deepfire(client, GAVARRES, T0, incident_id="someone-else") is None
    assert fire_input.poll_deepfire(client, GAVARRES, T0, incident_id="synthetic-gavarres") is not None


def test_poll_deepfire_returns_none_on_feed_error(caplog):
    client = FakeClient(error=feeds.FeedError("no Deepfire credentials"))
    with caplog.at_level("WARNING", logger="fireline.fire_input"):
        assert fire_input.poll_deepfire(client, GAVARRES, T0) is None
    assert "no Deepfire credentials" in caplog.text
    assert fire_input.poll_deepfire(FakeClient(error=feeds.LeakError("future")), GAVARRES, T0) is None


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self._body, self.status_code, self.headers = body, status, headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def test_poll_deepfire_through_real_client_with_faked_session(tmp_path, monkeypatch):
    """The real DeepfireClient + cached_get + busy retry, with requests.Session.get faked (no network)."""
    monkeypatch.setattr(feeds, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("FIRELINE_OFFLINE", raising=False)
    monkeypatch.setattr(feeds.time, "sleep", lambda s: None)
    monkeypatch.setenv("DEEPFIRE_TOKEN", "tok")
    calls = []
    seq = [FakeResponse({"code": "ogc-busy"}, 503, {"Retry-After": "0"}), FakeResponse(_rec(PERIM_1)["body"])]

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        calls.append((url, params, headers))
        return seq.pop(0)

    monkeypatch.setattr(requests.Session, "get", get)
    upd = fire_input.poll_deepfire(feeds.DeepfireClient(), GAVARRES, T0)
    assert upd["geometry_kind"] == "perimeter" and upd["incident_id"] == "synthetic-gavarres"
    assert len(calls) == 2 and calls[0][0].endswith("/collections/deepfire:satellite-perimeters/items")
    assert calls[0][2]["Authorization"] == "Bearer tok"
    assert "computed_at <= TIMESTAMP('2026-07-03T12:00:00Z')" in calls[0][1]["filter"]
    # second poll is served from the file cache (max_age 60 s): no further network call
    assert fire_input.poll_deepfire(feeds.DeepfireClient(), GAVARRES, T0)["observed_at"] == upd["observed_at"]
    assert len(calls) == 2
