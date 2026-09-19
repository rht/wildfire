"""Snapshot producer (CONTRACTS 2): geometry checks from readme 11, record mapping, validation, fixtures."""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from shapely.geometry import Point, mapping
from shapely.ops import transform as shapely_transform

from fireline import config, snapshot
from fireline.fire_state import FireState
from fireline.grid import lonlat_to_xy, xy_to_lonlat
from fireline.routing import RoadGraph
from fireline.scenario import Scenario
from fireline.snapshot import asset_exposure, build_snapshot, read_snapshot, validate_snapshot, write_snapshot
from tests.test_exposure import IGNITION, make_arrival

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
T0 = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
IX, IY = lonlat_to_xy(*IGNITION)


def wgs84(geom_25831) -> dict:
    return mapping(shapely_transform(xy_to_lonlat, geom_25831))


def fire_disc(radius_m=300.0) -> dict:
    return wgs84(Point(IX, IY).buffer(radius_m, 128))


def fire_update(geometry, kind="perimeter", observed_at=T0):
    return {"provider": "fixture", "incident_id": "test-fire", "observed_at": observed_at.isoformat(),
            "received_at": observed_at.isoformat(), "geometry": geometry, "geometry_kind": kind,
            "source": "fixture:test", "raw_ref": None}


def row(asset_id="fixture:a", cls="school", lon=None, lat=None, **extra):
    return dict({"asset_id": asset_id, "name": asset_id, "asset_class": cls, "lon": lon, "lat": lat,
                 "municipality": "Test", "occupancy": 50, "occupancy_source": "register"}, **extra)


def snap_kwargs(**over):
    kw = dict(scenario_id="test", incident_id="test-fire", sequence=1, as_of=T0, input_mode="synthetic",
              computed_at=T0)
    kw.update(over)
    return kw


# ------------------------------------------------------------------------------------- geometry
def test_overlap_gives_zero_and_intersects():
    fire = fire_disc(300)
    lon, lat = xy_to_lonlat(IX + 100, IY)                     # inside the disc
    d, inter, note = asset_exposure(lon, lat, None, fire)
    assert d == 0.0 and inter is True and note == snapshot.POINT_FALLBACK_NOTE
    footprint = wgs84(Point(IX + 250, IY).buffer(100))        # footprint overlapping the edge
    d, inter, note = asset_exposure(None, None, footprint, fire)
    assert d == 0.0 and inter is True and note is None


def test_known_offset_gives_metric_distance():
    fire = fire_disc(300)
    lon, lat = xy_to_lonlat(IX, IY - 2300)                    # 2 km south of the disc edge
    d, inter, _ = asset_exposure(lon, lat, None, fire)
    assert inter is False and abs(d - 2000.0) < 5
    footprint = wgs84(Point(IX + 1800, IY).buffer(200, 128))  # edge to edge: 1800 - 300 - 200
    d, inter, note = asset_exposure(None, None, footprint, fire)
    assert inter is False and abs(d - 1300.0) < 5 and note is None


def test_missing_geometry_and_location_and_fire():
    fire = fire_disc(300)
    d, inter, note = asset_exposure(None, None, None, fire)
    assert d is None and inter is None and note == snapshot.NO_LOCATION_NOTE
    d, inter, note = asset_exposure(3.0, 41.9, None, None)
    assert d is None and inter is None and note == snapshot.NO_FIRE_NOTE


def test_hotspot_centre_point_fire_gives_distance_with_note():
    fire = {"type": "Point", "coordinates": list(IGNITION)}
    lon, lat = xy_to_lonlat(IX + 500, IY)
    d, inter, note = asset_exposure(lon, lat, None, fire)
    assert abs(d - 500.0) < 1 and inter is False
    assert snapshot.POINT_FALLBACK_NOTE in note and snapshot.HOTSPOT_NOTE in note


# ------------------------------------------------------------------------------------- records
def test_point_fallback_labelled_in_sources_and_footprint_not():
    lon, lat = xy_to_lonlat(IX, IY - 1300)
    footprint = wgs84(Point(IX + 1300, IY).buffer(50))
    snap = build_snapshot([row("fixture:p", lon=lon, lat=lat), row("fixture:f", geometry=footprint)],
                          fire_update(fire_disc(300)), **snap_kwargs())
    p, f = snap["assets"]
    dist_src = [s for s in p["sources"] if "distance_to_fire_m" in s["fields"]]
    assert len(dist_src) == 1 and dist_src[0]["notes"] == snapshot.POINT_FALLBACK_NOTE
    assert dist_src[0]["source"] == "fixture:test" and dist_src[0]["observed_at"] == T0.isoformat()
    assert abs(p["distance_to_fire_m"] - 1000) < 5
    f_src = [s for s in f["sources"] if "distance_to_fire_m" in s["fields"]][0]
    assert "point fallback" not in (f_src["notes"] or "")
    assert f["latitude"] is None and f["area_m2"] is not None and abs(f["distance_to_fire_m"] - 950) < 5
    assert "location_unknown" in f["review_reasons"] and "exposure_unknown" not in f["review_reasons"]


def test_missing_location_is_unknown_not_zero():
    snap = build_snapshot([row("fixture:u")], fire_update(fire_disc()), **snap_kwargs())
    a = snap["assets"][0]
    assert a["latitude"] is None and a["longitude"] is None
    assert a["distance_to_fire_m"] is None and a["intersects_fire"] is None
    assert {"location_unknown", "exposure_unknown"} <= set(a["review_reasons"]) and a["needs_review"]


def test_no_fire_gives_null_fields_and_unavailable():
    snap = build_snapshot([row("fixture:a", lon=3.0, lat=41.9)], None, **snap_kwargs())
    assert snap["fire_geometry"] is None and snap["fire_geometry_kind"] is None
    assert snap["fire_observed_at"] is None and snap["data_status"] == "unavailable"
    assert snap["metrics"] == {"source_age_s": None, "processing_s": None}
    a = snap["assets"][0]
    assert a["distance_to_fire_m"] is None and "exposure_unknown" in a["review_reasons"]
    assert validate_snapshot(snap) == []


def test_unknown_class_gives_value_unknown():
    snap = build_snapshot([row("fixture:x", cls="bunker", lon=3.0, lat=41.9)], fire_update(fire_disc()), **snap_kwargs())
    a = snap["assets"][0]
    assert a["asset_type"] == "bunker" and a["value_score"] is None and a["value_basis"] is None
    assert a["review_reasons"] == ["value_unknown"]
    ok = build_snapshot([row("fixture:y", cls="school", lon=3.0, lat=41.9)], fire_update(fire_disc()), **snap_kwargs())
    assert ok["assets"][0]["value_score"] == config.VALUE_POLICY["by_type"]["school"]
    assert ok["assets"][0]["value_basis"] == config.VALUE_POLICY["version"]


def test_occupancy_mapping_keeps_capacity_and_headcount_apart():
    rows = [row("fixture:r", lon=3.0, lat=41.9, occupancy=70, occupancy_source="register"),
            row("fixture:h", lon=3.0, lat=41.9, occupancy=85, occupancy_source="allocated"),
            row("fixture:n", lon=3.0, lat=41.9, occupancy=None, occupancy_source="unknown", seasonal=True,
                class_ambiguous=True)]
    snap = build_snapshot(rows, fire_update(fire_disc()), **snap_kwargs())
    r, h, n = snap["assets"]
    assert r["capacity"] == 70 and r["estimated_occupancy"] is None and "capacity" in r["occupancy_basis"]
    assert h["capacity"] is None and h["estimated_occupancy"] == 85 and "headcount" in h["occupancy_basis"]
    assert n["capacity"] is None and n["estimated_occupancy"] is None and n["occupancy_basis"] is None
    assert n["review_reasons"] == ["occupancy_unknown", "occupancy_seasonal", "class_ambiguous"]
    assert r["review_reasons"] == [] and r["needs_review"] is False


def test_v4_records_accepted_and_recomputed():
    first = build_snapshot([row("fixture:v", lon=3.0, lat=41.9, occupancy=30, occupancy_source="register")],
                           fire_update(fire_disc()), **snap_kwargs())
    again = build_snapshot(first["assets"], fire_update(fire_disc(1000)), **snap_kwargs(sequence=2))
    a = again["assets"][0]
    assert a["capacity"] == 30 and a["asset_type"] == "school" and a["distance_to_fire_m"] < first["assets"][0]["distance_to_fire_m"]
    assert sum("distance_to_fire_m" in s["fields"] for s in a["sources"]) == 1
    assert sum("value_score" in s["fields"] for s in a["sources"]) == 1
    assert validate_snapshot(again) == []


def test_data_status_from_freshness_thresholds():
    obs = T0
    for as_of, expected in ((T0, "current"),
                            (datetime(2026, 7, 3, 9, 30, tzinfo=timezone.utc), "stale"),
                            (datetime(2026, 7, 3, 15, 0, tzinfo=timezone.utc), "unavailable")):
        snap = build_snapshot([], fire_update(fire_disc(), observed_at=obs), **snap_kwargs(as_of=as_of))
        assert snap["data_status"] == expected, as_of
        assert snap["metrics"]["source_age_s"] == (as_of - obs).total_seconds()
    forced = build_snapshot([], fire_update(fire_disc()), **snap_kwargs(data_status="stale"))
    assert forced["data_status"] == "stale"


def test_forecast_fields_null_unless_enrichment_flag(monkeypatch):
    arrival = make_arrival()
    rows = [row("fixture:a", lon=IGNITION[0], lat=IGNITION[1] - 0.01, occupancy=10, occupancy_source="allocated")]
    off = build_snapshot(rows, fire_update(fire_disc()), arrival=arrival, **snap_kwargs())
    a = off["assets"][0]
    assert all(a[k] is None for k in ("burn_probability", "arrival_p10_at", "arrival_p50_at", "forecast_horizon_at", "forecast_source"))
    monkeypatch.setitem(config.FEATURES, "forecast_enrichment", True)
    on = build_snapshot(rows, fire_update(fire_disc()), arrival=arrival, **snap_kwargs())
    b = on["assets"][0]
    assert 0 < b["burn_probability"] <= 1 and b["arrival_p10_at"] < b["arrival_p50_at"]
    assert "not validated" in b["forecast_source"] and b["forecast_horizon_at"] is not None
    assert any("burn_probability" in s["fields"] for s in b["sources"])
    assert validate_snapshot(on) == []


# ------------------------------------------------------------------------------------ validation
def test_validate_rejects_missing_key_and_wrong_status():
    snap = build_snapshot([row("fixture:a", lon=3.0, lat=41.9)], fire_update(fire_disc()), **snap_kwargs())
    assert validate_snapshot(snap) == []
    broken = json.loads(json.dumps(snap))
    del broken["assets"][0]["intersects_fire"]
    assert any("missing keys" in e and "intersects_fire" in e for e in validate_snapshot(broken))
    broken = json.loads(json.dumps(snap))
    del broken["data_status"]
    assert validate_snapshot(broken) == ["missing envelope key data_status"]
    broken = json.loads(json.dumps(snap))
    broken["data_status"] = "fresh"
    assert any("data_status" in e for e in validate_snapshot(broken))
    broken = json.loads(json.dumps(snap))
    broken["assets"][0]["needs_review"] = True
    assert any("needs_review" in e for e in validate_snapshot(broken))
    broken = json.loads(json.dumps(snap))
    broken["fire_geometry_kind"] = "hotspot_centre"
    assert any("perimeter" in e for e in validate_snapshot(broken))
    with pytest.raises(ValueError):
        build_snapshot([], None, **snap_kwargs(data_status="fresh"))


def test_sequence_and_snapshot_id_formatting():
    snap = build_snapshot([], None, **snap_kwargs(scenario_id="synthetic_gavarres", sequence=7))
    assert snap["snapshot_id"] == "synthetic_gavarres-0007" and snap["sequence"] == 7
    assert snap["as_of"] == "2026-07-03T08:00:00+00:00" and snap["schema_version"] == "1.0"
    with pytest.raises(ValueError):
        build_snapshot([], None, **snap_kwargs(sequence=0))
    bad = dict(snap, snapshot_id="synthetic_gavarres-7")
    assert any("snapshot_id" in e for e in validate_snapshot(bad))


def test_write_and_read_roundtrip(tmp_path):
    snap = build_snapshot([row("fixture:a", lon=3.0, lat=41.9)], fire_update(fire_disc()), **snap_kwargs())
    path = write_snapshot(snap, tmp_path / "s.json")
    assert read_snapshot(path) == snap


# ------------------------------------------------------------------------------------- scenario
def test_scenario_to_snapshot(monkeypatch):
    fs = FireState("test-cluster", T0, Point(IX, IY).buffer(200), hotspots=[], fros_dir_deg=175,
                   fros_speed_mps=0.5, wind_dir_deg=340, wind_speed_mps=8)
    assets = json.load(open(FIX / "assets.json"))
    dests = json.load(open(FIX / "destinations.json"))
    rg = RoadGraph.from_fixture(FIX / "roads.json")
    sc = Scenario.run(fs, make_arrival(), assets, dests, rg, config)
    snap = sc.to_snapshot(1, scenario_id="synthetic_gavarres", input_mode="synthetic", computed_at=T0)
    assert validate_snapshot(snap) == []
    assert snap["snapshot_id"] == "synthetic_gavarres-0001" and snap["incident_id"] == "test-cluster"
    assert snap["fire_geometry"]["type"] == "Polygon" and snap["fire_geometry_kind"] == "perimeter"
    assert snap["data_status"] == "current" and snap["input_mode"] == "synthetic"
    assert len(snap["assets"]) == 12 and all(a["forecast_source"] is None for a in snap["assets"])
    pou = next(a for a in snap["assets"] if a["asset_id"] == "fixture:pou_del_glac")
    assert pou["asset_type"] == "camp" and {"occupancy_unknown", "occupancy_seasonal"} <= set(pou["review_reasons"])
    assert 1500 < pou["distance_to_fire_m"] < 2500
    lon, lat = snap["fire_geometry"]["coordinates"][0][0]
    x, y = lonlat_to_xy(lon, lat)
    assert abs(((x - IX) ** 2 + (y - IY) ** 2) ** 0.5 - 200) < 2
    monkeypatch.setitem(config.FEATURES, "forecast_enrichment", True)
    enriched = sc.to_snapshot(2, scenario_id="synthetic_gavarres", computed_at=T0)
    assert validate_snapshot(enriched) == []
    assert any(a["burn_probability"] is not None and "not validated" in a["forecast_source"] for a in enriched["assets"])


# ------------------------------------------------------------------------------------- fixtures
def _load(name):
    return read_snapshot(FIX / "snapshots" / name)


def _by_distance(snap):
    return [a["asset_id"] for a in sorted((a for a in snap["assets"] if a["distance_to_fire_m"] is not None),
                                          key=lambda a: (a["distance_to_fire_m"], a["asset_id"]))]


def test_committed_synthetic_fixtures_validate_and_reorder():
    s1, s2 = _load("synthetic_gavarres_0001.json"), _load("synthetic_gavarres_0002.json")
    for s in (s1, s2):
        assert validate_snapshot(s) == [], s["snapshot_id"]
        assert s["input_mode"] == "synthetic" and s["scenario_id"] == "synthetic_gavarres"
        assert s["fire_geometry_kind"] == "perimeter" and len(s["assets"]) == 13
        reasons = {r for a in s["assets"] for r in a["review_reasons"]}
        assert {"location_unknown", "occupancy_unknown", "class_ambiguous", "occupancy_seasonal", "exposure_unknown"} <= reasons
    assert (s1["sequence"], s2["sequence"]) == (1, 2) and s1["snapshot_id"] != s2["snapshot_id"]
    assert [a["asset_id"] for a in s1["assets"]] == [a["asset_id"] for a in s2["assets"]]   # same set, same order
    assert s1["as_of"] < s2["as_of"]
    assert _by_distance(s1) != _by_distance(s2)
    assert not any(a["intersects_fire"] for a in s1["assets"])
    assert any(a["intersects_fire"] for a in s2["assets"])
    unl = next(a for a in s1["assets"] if a["asset_id"] == "fixture:residencia_sense_coordenades")
    assert unl["capacity"] == 40 and unl["latitude"] is None and "location_unknown" in unl["review_reasons"]


def test_committed_real_area_fixtures():
    extract = json.loads((FIX / "real_area" / "assets_gavarres.json").read_text())
    assets = extract["assets"]
    assert extract["counts"]["total"] == len(assets) == extract["counts"]["located"] + extract["counts"]["unlocated"]
    assert extract["counts"]["located"] > 50 and extract["counts"]["unlocated"] > 10
    assert len({a["asset_id"] for a in assets}) == len(assets)
    for a in assets:
        assert [k for k in snapshot.ASSET_KEYS if k not in a] == []
        assert a["asset_id"].split(":")[0] in ("equipaments", "schools", "care_homes", "campsites")
        assert any(s["fetched_at"] and s["fetched_at"].startswith("2026-09-19") for s in a["sources"])
        if a["latitude"] is None:
            assert "location_unknown" in a["review_reasons"] and a["asset_type"] in ("care_home", "campsite")
        else:
            assert 2.85 <= a["longitude"] <= 3.20 and 41.80 <= a["latitude"] <= 42.05
    r1, r2 = _load("gavarres_real_0001.json"), _load("gavarres_real_0003.json")
    for s in (r1, r2):
        assert validate_snapshot(s) == [] and s["input_mode"] == "recorded"
        assert s["fire_source"] == "deepfire:satellite-perimeters" and s["fire_geometry_kind"] == "perimeter"
        assert s["incident_id"] == "5769dcea-385a-4ee5-9313-804f55ddb5fa"
        assert len(s["assets"]) == len(assets)
    assert _by_distance(r1) != _by_distance(r2)
    assert (FIX / "real_area" / "README.md").read_text().count("2026-09-19") >= 1


def test_make_snapshots_script_is_deterministic(tmp_path):
    spec = importlib.util.spec_from_file_location("make_snapshots", ROOT / "scripts" / "make_snapshots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fs = FireState.from_json(FIX / "synthetic_ignition.json")
    grown = mod.grown_perimeter(fs)
    assert grown.area > fs.perimeter.area and grown.centroid.y < fs.perimeter.centroid.y
    assert mod._fold("Bisbal d'Empordà, la") == mod._fold("La Bisbal d'Empordà") == "la bisbal d'emporda"
    assert mod._fold("PALAMÓS") == mod._fold("Palamós")
    fire1 = mod.fire_update(fs, fs.perimeter, mod.T1, "x")
    snap = build_snapshot(mod.synthetic_assets(), fire1, scenario_id="synthetic_gavarres", incident_id=fs.cluster_id,
                          sequence=1, as_of=mod.T1, input_mode="synthetic", computed_at=mod.T1.replace(second=mod.COMPUTE_LAG_S),
                          metrics={"source_age_s": 0.0, "processing_s": float(mod.COMPUTE_LAG_S)})
    committed = _load("synthetic_gavarres_0001.json")
    assert [(a["asset_id"], a["distance_to_fire_m"]) for a in snap["assets"]] == \
           [(a["asset_id"], a["distance_to_fire_m"]) for a in committed["assets"]]
