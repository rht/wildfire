"""Snapshot producer (CONTRACTS 2): geometry checks from readme 11, record mapping, v1.1 timing fields,
validation, fixtures."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from shapely.geometry import Point, mapping
from shapely.ops import transform as shapely_transform

from fireline import config, snapshot, spread
from fireline.fire_state import FireState
from fireline.grid import Grid, lonlat_to_xy, xy_to_lonlat
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
    assert a["review_reasons"] == ["value_unknown", "evacuation_unknown", "forecast_unavailable"]
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
    assert n["review_reasons"] == ["occupancy_unknown", "occupancy_seasonal", "class_ambiguous", "forecast_unavailable"]
    assert r["review_reasons"] == ["forecast_unavailable"] and r["needs_review"] is True   # no forecast given


def test_school_enrolment_is_a_headcount_from_its_own_register():
    r = row("schools:17000001", lon=3.0, lat=41.9, occupancy=312, occupancy_source="enrolment",
            occupancy_register="gencat:schools_enrolment (xvme-26kg)", occupancy_period="2025/2026",
            register="gencat:schools (kvmv-ahh4)", fetched_at="2026-09-19T11:46:37+00:00",
            occupancy_fetched_at="2026-09-19T15:01:28+00:00")
    a = build_snapshot([r], fire_update(fire_disc()), **snap_kwargs())["assets"][0]
    assert a["capacity"] is None and a["estimated_occupancy"] == 312   # pupils, never a capacity
    assert "enrolled pupils 2025/2026" in a["occupancy_basis"] and "staff not included" in a["occupancy_basis"]
    assert "occupancy_unknown" not in a["review_reasons"]
    occ = [s for s in a["sources"] if "occupancy_basis" in s["fields"]]
    loc = [s for s in a["sources"] if "latitude" in s["fields"]]
    assert occ[0]["source"] == "gencat:schools_enrolment (xvme-26kg)"     # not the identity register
    assert occ[0]["fetched_at"] == "2026-09-19T15:01:28+00:00"
    assert loc[0]["source"] == "gencat:schools (kvmv-ahh4)"
    assert loc[0]["fetched_at"] == "2026-09-19T11:46:37+00:00"


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
    broken["assets"][0]["needs_review"] = False   # review_reasons carries forecast_unavailable
    assert any("needs_review" in e for e in validate_snapshot(broken))
    broken = json.loads(json.dumps(snap))
    broken["fire_geometry_kind"] = "hotspot_centre"
    assert any("perimeter" in e for e in validate_snapshot(broken))
    with pytest.raises(ValueError):
        build_snapshot([], None, **snap_kwargs(data_status="fresh"))


def test_sequence_and_snapshot_id_formatting():
    snap = build_snapshot([], None, **snap_kwargs(scenario_id="synthetic_gavarres", sequence=7))
    assert snap["snapshot_id"] == "synthetic_gavarres-0007" and snap["sequence"] == 7
    assert snap["as_of"] == "2026-07-03T08:00:00+00:00" and snap["schema_version"] == "1.1"
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


def _make_snapshots_module():
    spec = importlib.util.spec_from_file_location("make_snapshots", ROOT / "scripts" / "make_snapshots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_make_snapshots_script_is_deterministic(tmp_path):
    mod = _make_snapshots_module()
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


# ------------------------------------------------------------------------- v1.1 timing fields
def _forecast(estimates, issued_at=T0, horizon_h=12, **over):
    fc = {"schema_version": "forecast-input-1", "forecast_source": "fixture:test_forecast",
          "input_mode": "synthetic", "issued_at": issued_at.isoformat(),
          "forecast_horizon_at": (issued_at + timedelta(hours=horizon_h)).isoformat(), "basis": "p10",
          "note": "unit test; synthetic, not a provider forecast", "estimates": estimates}
    fc.update(over)
    return fc


def _min(minutes, base=T0):
    return (base + timedelta(minutes=minutes)).isoformat()


def test_timing_keys_present_and_evacuation_from_policy():
    snap = build_snapshot([row("fixture:s", cls="school", lon=3.0, lat=41.9)], fire_update(fire_disc()), **snap_kwargs())
    a = snap["assets"][0]
    assert all(k in a for k in snapshot.TIMING_KEYS) and set(snapshot.TIMING_KEYS) <= set(snapshot.ASSET_KEYS)
    policy = config.EVACUATION_POLICY
    cls = policy["by_type"]["school"]
    assert a["evacuation_min"] == sum(cls[c] for c in policy["components"]) == 90
    assert a["evacuation_source"] == policy["version"]
    src = [s for s in a["sources"] if "evacuation_min" in s["fields"]]
    assert len(src) == 1 and src[0]["source"] == "config.EVACUATION_POLICY"
    assert src[0]["fields"] == ["evacuation_min", "evacuation_source"]
    note = src[0]["notes"]
    assert "mobilisation 20 min" in note and "preparation 30 min" in note and "movement 40 min" in note
    assert "= 90 min" in note and cls["assumptions"] in note and policy["version"] in note
    assert "evacuation_unknown" not in a["review_reasons"]
    assert validate_snapshot(snap) == []


def test_unknown_class_gives_evacuation_unknown():
    for cls in ("unknown", "bunker"):
        snap = build_snapshot([row("fixture:x", cls=cls, lon=3.0, lat=41.9)], fire_update(fire_disc()), **snap_kwargs())
        a = snap["assets"][0]
        assert a["evacuation_min"] is None and a["evacuation_source"] is None
        assert "evacuation_unknown" in a["review_reasons"] and a["needs_review"]
        assert not any("evacuation_min" in s["fields"] for s in a["sources"])
        assert validate_snapshot(snap) == []


def test_no_forecast_gives_null_arrival_and_forecast_unavailable():
    snap = build_snapshot([row("fixture:a", lon=3.0, lat=41.9), row("fixture:u")], fire_update(fire_disc()), **snap_kwargs())
    for a in snap["assets"]:
        assert a["fire_arrival_at"] is None and a["fire_arrival_basis"] is None
        assert a["forecast_source"] is None and "forecast_unavailable" in a["review_reasons"] and a["needs_review"]
    # never inferred from distance: the located asset has a distance but still no arrival
    assert snap["assets"][0]["distance_to_fire_m"] is not None
    assert validate_snapshot(snap) == []


def test_build_snapshot_attaches_forecast_and_selects_p10():
    rows = [row("fixture:both", lon=3.0, lat=41.9), row("fixture:p50only", lon=3.01, lat=41.9),
            row("fixture:absent", lon=3.02, lat=41.9), row("fixture:unlocated")]
    fc = _forecast({"fixture:both": {"arrival_p10_at": _min(60), "arrival_p50_at": _min(90), "burn_probability": 0.8},
                    "fixture:p50only": {"arrival_p10_at": None, "arrival_p50_at": _min(120), "burn_probability": None},
                    "fixture:unlocated": {"arrival_p10_at": _min(30), "arrival_p50_at": None, "burn_probability": 0.9}})
    snap = build_snapshot(rows, fire_update(fire_disc()), forecast=fc, **snap_kwargs())
    both, p50, absent, unl = snap["assets"]
    assert both["fire_arrival_at"] == _min(60) and both["fire_arrival_basis"] == "p10"
    assert both["arrival_p10_at"] == _min(60) and both["arrival_p50_at"] == _min(90) and both["burn_probability"] == 0.8
    assert both["forecast_source"] == "fixture:test_forecast" and both["forecast_horizon_at"] == fc["forecast_horizon_at"]
    assert "forecast_unavailable" not in both["review_reasons"] and both["review_reasons"] == []
    src = [s for s in both["sources"] if "fire_arrival_at" in s["fields"]]
    assert len(src) == 1 and src[0]["observed_at"] == T0.isoformat() and src[0]["source"] == "fixture:test_forecast"
    assert src[0]["fields"] == ["burn_probability", "arrival_p10_at", "arrival_p50_at", "forecast_horizon_at",
                                "forecast_source", "fire_arrival_at", "fire_arrival_basis"]
    assert "synthetic, not a provider forecast" in src[0]["notes"]
    assert p50["fire_arrival_at"] == _min(120) and p50["fire_arrival_basis"] == "p50" and p50["burn_probability"] is None
    assert [s for s in p50["sources"] if "fire_arrival_at" in s["fields"]][0]["fields"] == \
        ["arrival_p50_at", "forecast_horizon_at", "forecast_source", "fire_arrival_at", "fire_arrival_basis"]
    for a in (absent, unl):   # no estimate / no coordinates: nothing filled, review reason set
        assert a["fire_arrival_at"] is None and a["forecast_source"] is None and a["arrival_p10_at"] is None
        assert "forecast_unavailable" in a["review_reasons"]
    assert validate_snapshot(snap) == []
    # v4 records fed back in are recomputed, not accumulated
    again = build_snapshot(snap["assets"], fire_update(fire_disc()), forecast=fc, **snap_kwargs(sequence=2))
    b = again["assets"][0]
    assert sum("fire_arrival_at" in s["fields"] for s in b["sources"]) == 1
    assert sum("evacuation_min" in s["fields"] for s in b["sources"]) == 1
    plain = build_snapshot(snap["assets"], fire_update(fire_disc()), **snap_kwargs(sequence=3))
    assert plain["assets"][0]["fire_arrival_at"] is None and "forecast_unavailable" in plain["assets"][0]["review_reasons"]
    assert validate_snapshot(plain) == []


def test_ca_enrichment_selects_p10_with_labelled_basis(monkeypatch):
    arrival = make_arrival()
    rows = [row("fixture:a", lon=IGNITION[0], lat=IGNITION[1] - 0.01, occupancy=10, occupancy_source="allocated")]
    monkeypatch.setitem(config.FEATURES, "forecast_enrichment", True)
    on = build_snapshot(rows, fire_update(fire_disc()), arrival=arrival, **snap_kwargs())
    a = on["assets"][0]
    assert a["fire_arrival_at"] == a["arrival_p10_at"] and a["fire_arrival_at"] is not None
    assert a["fire_arrival_basis"] == "p10 (synthetic, labelled enrichment, not validated)"
    assert "forecast_unavailable" not in a["review_reasons"]
    src = [s for s in a["sources"] if "fire_arrival_at" in s["fields"]][0]
    assert "fire_arrival_basis" in src["fields"] and "not validated" in src["notes"]
    assert validate_snapshot(on) == []
    # an explicit forecast takes precedence over the raster
    fc = _forecast({"fixture:a": {"arrival_p10_at": _min(15), "arrival_p50_at": None, "burn_probability": None}})
    both = build_snapshot(rows, fire_update(fire_disc()), arrival=arrival, forecast=fc, **snap_kwargs())
    assert both["assets"][0]["fire_arrival_at"] == _min(15) and both["assets"][0]["fire_arrival_basis"] == "p10"


def test_validate_timing_provenance_and_reason_pairing():
    fc = _forecast({"fixture:a": {"arrival_p10_at": _min(60), "arrival_p50_at": None, "burn_probability": None}})
    snap = build_snapshot([row("fixture:a", lon=3.0, lat=41.9)], fire_update(fire_disc()), forecast=fc, **snap_kwargs())
    assert validate_snapshot(snap) == []

    def broken(**changes):
        b = json.loads(json.dumps(snap))
        b["assets"][0].update(changes)
        return validate_snapshot(b)

    for k in ("fire_arrival_basis", "forecast_source", "forecast_horizon_at"):
        assert any(f"fire_arrival_at requires {k}" in e for e in broken(**{k: None})), k
    assert any("ISO timestamp" in e for e in broken(fire_arrival_at="soon"))
    assert any("forecast_unavailable must be present iff" in e for e in broken(fire_arrival_at=None, fire_arrival_basis=None))
    assert any("fire_arrival_basis must be null" in e for e in broken(fire_arrival_at=None, review_reasons=["forecast_unavailable"]))
    assert any("evacuation_min requires evacuation_source" in e for e in broken(evacuation_source=None))
    assert any("evacuation_unknown must be present iff" in e for e in broken(evacuation_min=None, evacuation_source=None))
    for bad in (-5, float("inf"), float("nan"), "90", True):
        assert any("evacuation_min must be a nonnegative finite number" in e for e in broken(evacuation_min=bad)), bad
    assert any("evacuation_source must be null" in e for e in broken(evacuation_min=None, review_reasons=["evacuation_unknown"]))
    missing = json.loads(json.dumps(snap))
    for k in snapshot.TIMING_KEYS:
        del missing["assets"][0][k]
    assert any("missing keys" in e and "fire_arrival_at" in e for e in validate_snapshot(missing))


def test_schema_1_0_files_still_load_and_validate(tmp_path):
    snap = build_snapshot([row("fixture:a", lon=3.0, lat=41.9)], fire_update(fire_disc()), **snap_kwargs())
    legacy = json.loads(json.dumps(snap))
    legacy["schema_version"] = "1.0"
    for a in legacy["assets"]:
        for k in snapshot.TIMING_KEYS:
            del a[k]
        a["review_reasons"] = [r for r in a["review_reasons"] if r not in ("forecast_unavailable", "evacuation_unknown")]
        a["needs_review"] = bool(a["review_reasons"])
    assert validate_snapshot(legacy) == []
    loaded = read_snapshot(write_snapshot(legacy, tmp_path / "legacy.json"))
    assert loaded["schema_version"] == "1.0" and "fire_arrival_at" not in loaded["assets"][0]
    assert loaded["assets"][0].get("fire_arrival_at") is None   # consumer reads the missing key as null
    bad = json.loads(json.dumps(legacy))
    bad["assets"][0]["fire_arrival_at"] = _min(30)   # a 1.0 file that does carry the key is still checked
    assert any("fire_arrival_at requires fire_arrival_basis" in e for e in validate_snapshot(bad))
    unknown = dict(snap, schema_version="2.0")
    assert any("schema_version" in e for e in validate_snapshot(unknown))


# ------------------------------------------------------------------------------ value at risk
VAR_KEYS = snapshot.VALUE_AT_RISK_KEYS
EVAC = {"hospital": 180.0, "school": 90.0, "campsite": 70.0, "nucleus": 105.0}   # config.EVACUATION_POLICY sums
BUFFER = config.CONTACT_POLICY["buffer_min"]


def var_on(monkeypatch):
    monkeypatch.setitem(config.FEATURES, "value_at_risk", True)


def var_snapshot(rows, estimates, **kw):
    """A snapshot whose located assets carry the forecast fields the layer reads."""
    return build_snapshot(rows, fire_update(fire_disc()), forecast=_forecast(estimates), **snap_kwargs(**kw))


def var_estimate(p10=None, p50=None, burn_probability=None):
    return {"arrival_p10_at": None if p10 is None else _min(p10),
            "arrival_p50_at": None if p50 is None else _min(p50), "burn_probability": burn_probability}


def test_value_at_risk_keys_absent_unless_flag(monkeypatch):
    rows = [row("fixture:h", cls="hospital", lon=3.0, lat=41.9, occupancy=120, occupancy_source="headcount")]
    est = {"fixture:h": var_estimate(p10=60, p50=400, burn_probability=0.5)}
    off = var_snapshot(rows, est)["assets"][0]
    assert not any(k in off for k in VAR_KEYS)
    assert not any("people_exposed" in s["fields"] for s in off["sources"])
    var_on(monkeypatch)
    on = var_snapshot(rows, est)["assets"][0]
    assert all(k in on for k in VAR_KEYS)
    # the layer only adds: strip its keys and its one provenance entry and the record is the flag-off record
    stripped = {k: v for k, v in on.items() if k not in VAR_KEYS}
    stripped["sources"] = [s for s in on["sources"] if not (set(s["fields"]) & set(VAR_KEYS))]
    assert stripped == off


def test_value_at_risk_worked_examples(monkeypatch):
    var_on(monkeypatch)
    rows = [row("fixture:h", cls="hospital", lon=3.0, lat=41.9, occupancy=120, occupancy_source="headcount"),
            row("fixture:s", cls="school", lon=3.01, lat=41.9, occupancy=312, occupancy_source="enrolment"),
            row("fixture:c", cls="campsite", lon=3.02, lat=41.9, occupancy=40, occupancy_source="allocated")]
    snap = var_snapshot(rows, {"fixture:h": var_estimate(p10=60, p50=400, burn_probability=0.5),
                               "fixture:s": var_estimate(p10=300, p50=400, burn_probability=0.25),
                               "fixture:c": var_estimate(p10=30, p50=45, burn_probability=0.8)})
    h, s, c = snap["assets"]
    assert h["replacement_value_eur"] == 25_000_000 and "assumed" in h["replacement_value_basis"]
    assert config.VALUE_AT_RISK_POLICY["version"] in h["replacement_value_basis"]
    assert h["people_exposed"] == 60.0                                   # 120 x 0.5
    assert (h["expected_loss_eur_low"], h["expected_loss_eur_mid"], h["expected_loss_eur_high"]) == \
           (1_250_000, 3_125_000, 6_250_000)                             # 0.5 x (0.10, 0.25, 0.50) x 25 M
    assert h["people_at_risk_p10"] == 120 and h["people_at_risk_p50"] == 0   # p10 60 min < 180 + 30 evacuation
    assert s["people_exposed"] == 78.0 and s["replacement_value_eur"] == 4_000_000
    assert (s["expected_loss_eur_low"], s["expected_loss_eur_mid"], s["expected_loss_eur_high"]) == \
           (150_000, 400_000, 800_000)
    assert s["people_at_risk_p10"] == 0 and s["people_at_risk_p50"] == 0      # 300 min > 90 + 30
    assert c["people_exposed"] == 32.0 and c["replacement_value_eur"] == 3_000_000
    assert (c["expected_loss_eur_low"], c["expected_loss_eur_mid"], c["expected_loss_eur_high"]) == \
           (720_000, 1_440_000, 2_160_000)
    assert c["people_at_risk_p10"] == 40 and c["people_at_risk_p50"] == 40    # both inside 70 + 30
    for a in (h, s, c):
        assert a["expected_loss_eur_low"] <= a["expected_loss_eur_mid"] <= a["expected_loss_eur_high"]
        src = [x for x in a["sources"] if "people_exposed" in x["fields"]]
        assert len(src) == 1 and src[0]["source"] == "config.VALUE_AT_RISK_POLICY"
        assert src[0]["fields"] == list(VAR_KEYS)
        note = src[0]["notes"]
        assert "never capacity" in note and "assumed per-class placeholder" in note
        assert "not an insurer's figure" in note and "never enter the ranking" in note
    assert validate_snapshot(snap) == []


def test_value_at_risk_zero_burn_probability_is_a_value_not_a_gap(monkeypatch):
    var_on(monkeypatch)
    rows = [row("fixture:h", cls="hospital", lon=3.0, lat=41.9, occupancy=120, occupancy_source="headcount")]
    a = var_snapshot(rows, {"fixture:h": var_estimate(burn_probability=0.0)})["assets"][0]
    assert a["burn_probability"] == 0.0 and a["fire_arrival_at"] is None
    assert a["people_exposed"] == 0.0
    assert (a["expected_loss_eur_low"], a["expected_loss_eur_mid"], a["expected_loss_eur_high"]) == (0, 0, 0)
    assert a["people_at_risk_p10"] == 0 and a["people_at_risk_p50"] == 0   # covered, not reached in the horizon


def test_value_at_risk_not_reached_in_horizon_is_zero_not_null(monkeypatch):
    var_on(monkeypatch)
    rows = [row("fixture:h", cls="hospital", lon=3.0, lat=41.9, occupancy=120, occupancy_source="headcount")]
    a = var_snapshot(rows, {"fixture:h": var_estimate(burn_probability=0.3)})["assets"][0]
    assert a["arrival_p10_at"] is None and a["arrival_p50_at"] is None and a["forecast_source"] is not None
    assert a["people_at_risk_p10"] == 0 and a["people_at_risk_p50"] == 0
    assert a["people_exposed"] == 36.0                                   # exposure still comes from the probability


def test_value_at_risk_is_null_not_zero_when_an_input_is_missing(monkeypatch):
    var_on(monkeypatch)
    rows = [row("fixture:cap", cls="school", lon=3.0, lat=41.9, occupancy=200, occupancy_source="register"),
            row("fixture:nofc", cls="school", lon=3.01, lat=41.9, occupancy=50, occupancy_source="allocated"),
            row("fixture:unlocated", cls="school", occupancy=50, occupancy_source="allocated"),
            row("fixture:unknown", cls="bunker", lon=3.02, lat=41.9, occupancy=50, occupancy_source="allocated"),
            row("fixture:nucleus", cls="nucleus", lon=3.03, lat=41.9, occupancy=300, occupancy_source="census")]
    est = {"fixture:cap": var_estimate(p10=10, burn_probability=0.4),
           "fixture:unknown": var_estimate(p10=10, burn_probability=0.4),
           "fixture:nucleus": var_estimate(p10=10, p50=20, burn_probability=0.4)}
    cap, nofc, unl, unknown, nucleus = var_snapshot(rows, est)["assets"]
    # capacity is a maximum, never a headcount: the people fields stay null
    assert cap["capacity"] == 200 and cap["estimated_occupancy"] is None
    assert cap["people_exposed"] is None and cap["people_at_risk_p10"] is None and cap["people_at_risk_p50"] is None
    assert cap["replacement_value_eur"] == 4_000_000                     # the class value is still known
    assert cap["expected_loss_eur_mid"] == 640_000                       # 0.4 x 0.40 x 4 M, independent of headcount
    # no forecast covers this asset: no probability, no euros, no people at risk
    assert nofc["burn_probability"] is None and nofc["forecast_source"] is None
    assert all(nofc[k] is None for k in VAR_KEYS if k not in ("replacement_value_eur", "replacement_value_basis"))
    # unlocated: nothing but the class value
    assert unl["people_exposed"] is None and unl["people_at_risk_p50"] is None and unl["expected_loss_eur_mid"] is None
    # a class outside the policy has no value and no evacuation duration
    assert unknown["replacement_value_eur"] is None and unknown["replacement_value_basis"] is None
    assert unknown["expected_loss_eur_mid"] is None and unknown["evacuation_min"] is None
    assert unknown["people_exposed"] == 20.0                             # 50 x 0.4 needs no class value
    assert unknown["people_at_risk_p10"] is None                         # ... but the window does need one
    # nucleus: not valued, people still counted
    assert nucleus["replacement_value_eur"] is None and nucleus["expected_loss_eur_mid"] is None
    assert nucleus["people_exposed"] == 120.0 and nucleus["people_at_risk_p10"] == 300
    assert "value_unknown" not in nucleus["review_reasons"]              # the review queue is unchanged
    assert [s for s in nucleus["sources"] if "people_exposed" in s["fields"]][0]["notes"].endswith(
        "no dwelling count in the data: not valued")


def test_value_at_risk_window_boundary_per_quantile(monkeypatch):
    var_on(monkeypatch)
    rows = [row("fixture:h", cls="hospital", lon=3.0, lat=41.9, occupancy=120, occupancy_source="headcount")]
    edge = EVAC["hospital"] + BUFFER                                     # slack exactly 0: window_exhausted
    a = var_snapshot(rows, {"fixture:h": var_estimate(p10=edge, p50=edge + 1, burn_probability=0.5)})["assets"][0]
    assert a["people_at_risk_p10"] == 120 and a["people_at_risk_p50"] == 0
    b = var_snapshot(rows, {"fixture:h": var_estimate(p10=edge + 1, p50=edge + 2, burn_probability=0.5)})["assets"][0]
    assert b["people_at_risk_p10"] == 0 and b["people_at_risk_p50"] == 0


def test_value_at_risk_evacuation_unknown_leaves_people_at_risk_null(monkeypatch):
    var_on(monkeypatch)
    rows = [row("fixture:h", cls="hospital", lon=3.0, lat=41.9, occupancy=120, occupancy_source="headcount")]
    a = var_snapshot(rows, {"fixture:h": var_estimate(p10=10, p50=20, burn_probability=0.5)})["assets"][0]
    a["evacuation_min"], a["evacuation_source"] = None, None
    snapshot.derive_value_at_risk(a, T0)
    assert a["people_at_risk_p10"] is None and a["people_at_risk_p50"] is None
    assert a["people_exposed"] == 60.0 and a["expected_loss_eur_mid"] == 3_125_000
    # an unusable timestamp is a gap, not a "not reached"
    a["evacuation_min"], a["evacuation_source"] = 180.0, "fixture"
    a["arrival_p10_at"] = "soon"
    snapshot.derive_value_at_risk(a, T0)
    assert a["people_at_risk_p10"] is None and a["people_at_risk_p50"] == 120   # p50 is usable and exhausted


def test_value_at_risk_re_derivation_replaces_its_provenance(monkeypatch):
    var_on(monkeypatch)
    rows = [row("fixture:h", cls="hospital", lon=3.0, lat=41.9, occupancy=120, occupancy_source="headcount")]
    a = var_snapshot(rows, {"fixture:h": var_estimate(p10=10, burn_probability=0.5)})["assets"][0]
    a["estimated_occupancy"] = 200
    snapshot.derive_value_at_risk(a, T0)
    assert a["people_exposed"] == 100.0 and a["people_at_risk_p10"] == 200
    assert sum("people_exposed" in s["fields"] for s in a["sources"]) == 1
    # without an epoch only the time-independent fields move
    a["estimated_occupancy"] = 10
    snapshot.derive_value_at_risk(a, None)
    assert a["people_exposed"] == 5.0 and a["people_at_risk_p10"] == 200


def test_validate_value_at_risk_rules(monkeypatch):
    var_on(monkeypatch)
    rows = [row("fixture:h", cls="hospital", lon=3.0, lat=41.9, occupancy=120, occupancy_source="headcount")]
    snap = var_snapshot(rows, {"fixture:h": var_estimate(p10=10, p50=20, burn_probability=0.5)})
    assert validate_snapshot(snap) == []

    def broken(**changes):
        b = json.loads(json.dumps(snap))
        b["assets"][0].update(changes)
        return validate_snapshot(b)

    partial = json.loads(json.dumps(snap))
    del partial["assets"][0]["people_exposed"]
    assert any("all present or all absent" in e for e in validate_snapshot(partial))
    assert any("null together" in e for e in broken(replacement_value_basis=None))
    assert any("replacement_value_eur must be a nonnegative" in e for e in broken(replacement_value_eur=-1))
    assert any("people_exposed must be a nonnegative" in e for e in broken(people_exposed=-1.0))
    assert any("people_exposed must be null iff" in e for e in broken(people_exposed=None))
    assert any("people_at_risk_p10 must be a nonnegative int" in e for e in broken(people_at_risk_p10=1.5))
    assert any("no partial clearance" in e for e in broken(people_at_risk_p10=60))
    assert any("people_at_risk_p50 requires" in e for e in broken(evacuation_min=None, evacuation_source=None,
                                                                  review_reasons=["evacuation_unknown"]))
    assert any("expected_loss_eur_mid must be null iff" in e
               for e in broken(burn_probability=None, people_exposed=None, arrival_p10_at=None, arrival_p50_at=None))
    assert any("expected_loss_eur_low <= _mid <= _high" in e for e in broken(expected_loss_eur_low=9_999_999))
    for bad in (float("inf"), float("nan")):
        assert any("expected_loss_eur_mid must be a nonnegative finite" in e for e in broken(expected_loss_eur_mid=bad))
    # a record without the keys stays valid (schema 1.1 files written before the layer)
    without = json.loads(json.dumps(snap))
    for k in VAR_KEYS:
        del without["assets"][0][k]
    without["assets"][0]["sources"] = [s for s in without["assets"][0]["sources"]
                                       if not (set(s["fields"]) & set(VAR_KEYS))]
    assert validate_snapshot(without) == []


# ---------------------------------------------------------------- committed forecast fixtures
def _slack_min(asset, now):
    """Consumer arithmetic (readme 6) on the committed data: arrival - evacuation - buffer - now, in minutes."""
    if asset["fire_arrival_at"] is None or asset["evacuation_min"] is None:
        return None
    tti = (datetime.fromisoformat(asset["fire_arrival_at"]) - now).total_seconds() / 60.0
    return tti - asset["evacuation_min"] - config.CONTACT_POLICY["buffer_min"]


def _ranking(snap):
    now = datetime.fromisoformat(snap["as_of"])
    rows = [(a, _slack_min(a, now)) for a in snap["assets"]]
    ranked = sorted((r for r in rows if r[1] is not None),
                    key=lambda r: (r[1], r[0]["fire_arrival_at"], r[0]["distance_to_fire_m"], r[0]["asset_id"]))
    return [a["asset_id"] for a, _ in ranked], {a["asset_id"]: s for a, s in rows}


def test_committed_forecast_fixtures_validate_and_feed_the_snapshots():
    from fireline.forecast_input import load_forecast
    for seq in (1, 2):
        fc = load_forecast(FIX / "forecast" / f"synthetic_gavarres_{seq:04d}.json")
        snap = _load(f"synthetic_gavarres_{seq:04d}.json")
        assert fc["input_mode"] == "synthetic" and fc["issued_at"] == snap["as_of"]
        assert "synthetic, not a provider forecast" in fc["note"]
        assert snap["schema_version"] == "1.1" and validate_snapshot(snap) == []
        by_id = {a["asset_id"]: a for a in snap["assets"]}
        assert set(fc["estimates"]) < set(by_id)
        for aid, est in fc["estimates"].items():
            a = by_id[aid]
            assert a["arrival_p10_at"] == est["arrival_p10_at"] and a["fire_arrival_at"] == est["arrival_p10_at"]
            assert a["fire_arrival_basis"] == "p10" and a["forecast_source"] == fc["forecast_source"]
            assert a["forecast_horizon_at"] == fc["forecast_horizon_at"]
            assert "forecast_unavailable" not in a["review_reasons"]
        for a in snap["assets"]:
            assert a["evacuation_min"] is not None and a["evacuation_source"] == config.EVACUATION_POLICY["version"]
            assert a["evacuation_min"] == sum(config.EVACUATION_POLICY["by_type"][a["asset_type"]][c]
                                              for c in config.EVACUATION_POLICY["components"])


def test_committed_forecast_fixtures_demonstrate_readme_11_priority_checks():
    s1, s2 = _load("synthetic_gavarres_0001.json"), _load("synthetic_gavarres_0002.json")
    a1 = {a["asset_id"]: a for a in s1["assets"]}
    a2 = {a["asset_id"]: a for a in s2["assets"]}
    # (a) farther downwind assets arrive EARLIER than nearer off-axis ones, so they outrank them
    for far, near in (("fixture:mas_pla", "fixture:sant_sadurni"), ("fixture:pou_del_glac", "fixture:residencia_la_bisbal")):
        assert a1[far]["distance_to_fire_m"] > a1[near]["distance_to_fire_m"]
        assert a1[far]["fire_arrival_at"] < a1[near]["fire_arrival_at"]
    order1, slack1 = _ranking(s1)
    assert order1.index("fixture:mas_pla") < order1.index("fixture:sant_sadurni")
    assert order1.index("fixture:pou_del_glac") < order1.index("fixture:residencia_la_bisbal")
    assert all(s > 0 for s in slack1.values() if s is not None)          # every window still open at 08:00
    # (b) at 10:00 some windows are exhausted and the order changes
    order2, slack2 = _ranking(s2)
    exhausted = [aid for aid, s in slack2.items() if s is not None and s <= 0]
    assert {"fixture:sant_pol", "fixture:pou_del_glac", "fixture:can_xic"} <= set(exhausted)
    assert order2[:len(exhausted)] == sorted(exhausted, key=lambda aid: slack2[aid])   # exhausted stay on top
    assert order1 != order2
    assert order1.index("fixture:escola_cruilles") < order1.index("fixture:camping_gavarres")
    assert order2.index("fixture:camping_gavarres") < order2.index("fixture:escola_cruilles")
    # (c) one located asset without an estimate and the unlocated care home: forecast_unavailable
    for snap in (s1, s2):
        by_id = {a["asset_id"]: a for a in snap["assets"]}
        vr, unl = by_id["fixture:vall_repos"], by_id["fixture:residencia_sense_coordenades"]
        assert vr["latitude"] is not None and vr["distance_to_fire_m"] is not None
        for a in (vr, unl):
            assert a["fire_arrival_at"] is None and a["forecast_source"] is None
            assert "forecast_unavailable" in a["review_reasons"]
        assert {aid for aid, a in by_id.items() if a["fire_arrival_at"] is None} == {vr["asset_id"], unl["asset_id"]}
    # (d) same class, identical arrival: tie resolves by distance then id
    for by_id in (a1, a2):
        m, s = by_id["fixture:monells"], by_id["fixture:sant_sadurni"]
        assert m["asset_type"] == s["asset_type"] and m["fire_arrival_at"] == s["fire_arrival_at"]
        assert m["evacuation_min"] == s["evacuation_min"] and m["distance_to_fire_m"] < s["distance_to_fire_m"]
    assert order1.index("fixture:monells") + 1 == order1.index("fixture:sant_sadurni")


CA_LABEL = "ca_ensemble (labelled enrichment, not validated)"
CA_BASIS = "p10 (ca_ensemble, labelled enrichment, not validated)"


def test_committed_real_snapshots_carry_labelled_ca_arrivals_and_policy_evacuation():
    """No provider forecast covers the July perimeters; `fire_arrival_at` on gavarres_real_0001..0003 is the
    v0 CA ensemble attached as labelled enrichment (forecast_source / basis say so), the wind and CA
    settings are in the provenance note, unlocated assets never get one, and nothing comes from distance."""
    mod = _make_snapshots_module()
    real_fires = mod.recorded_real_fires()
    readme = (FIX / "real_area" / "README.md").read_text()
    assert "fixtures/wind/" in readme and "ca_ensemble (labelled enrichment, not validated)" in readme
    for n in (1, 2, 3):
        s = _load(f"gavarres_real_{n:04d}.json")
        assert s["schema_version"] == "1.1" and validate_snapshot(s) == []
        as_of = datetime.fromisoformat(s["as_of"])
        located = [a for a in s["assets"] if a["latitude"] is not None]
        with_arrival = [a for a in s["assets"] if a["fire_arrival_at"] is not None]
        assert 0 < len(with_arrival) < len(located), n          # some ranked, some still forecast_unavailable
        _, wind = mod.real_fire_state(*real_fires[n - 1])         # the committed wind file, hour containing as_of
        assert wind["file"] == "fixtures/wind/41.90_3.05.json" and wind["exact_hour"] is True
        wind_text = f"wind {wind['wind_speed_mps']:.2f} m/s from {wind['wind_dir_deg']:.0f} deg valid {wind['valid_time'][:16]}Z"
        for a in s["assets"]:
            assert a["evacuation_min"] is not None and a["evacuation_source"] == config.EVACUATION_POLICY["version"]
            assert (a["forecast_source"] is not None) == (a["burn_probability"] is not None)   # raster coverage
            if a["latitude"] is None:
                assert a["fire_arrival_at"] is None and a["forecast_source"] is None and a["burn_probability"] is None
            if a["fire_arrival_at"] is None:
                assert "forecast_unavailable" in a["review_reasons"] and a["needs_review"] is True
                assert a["fire_arrival_basis"] is None and a["arrival_p10_at"] is None
                continue
            assert "forecast_unavailable" not in a["review_reasons"]
            assert a["forecast_source"] == CA_LABEL and a["fire_arrival_basis"] == CA_BASIS
            assert a["arrival_p10_at"] == a["fire_arrival_at"] and 0.1 <= a["burn_probability"] <= 1.0
            arrival, horizon = datetime.fromisoformat(a["fire_arrival_at"]), datetime.fromisoformat(a["forecast_horizon_at"])
            assert as_of < arrival <= horizon == as_of + timedelta(minutes=mod.CA_HORIZON_MIN)
            src = next(e for e in a["sources"] if "fire_arrival_at" in e["fields"])
            assert src["source"] == CA_LABEL and "not validated" in src["notes"]
            assert f"spread.run_ca n_runs {mod.CA_N_RUNS}" in src["notes"] and wind_text in src["notes"]
            assert "fixtures/wind/41.90_3.05.json" in src["notes"] and "not an observation" in src["notes"]
    # the fixture wind file is the Previous Runs slice the README describes
    wind_file = json.loads((FIX / "wind" / "41.90_3.05.json").read_text())
    assert wind_file["model"] == "ecmwf_ifs025" and wind_file["slice"] == "previous_day1"
    assert len(wind_file["time"]) == len(wind_file["wind_speed_10m"]) == len(wind_file["wind_direction_10m"]) == 72


def test_real_wind_helpers_pick_nearest_grid_point_and_hour(tmp_path):
    mod = _make_snapshots_module()
    utc = timezone.utc
    # committed fixture: every July perimeter centroid (near 3.027E, 41.90N) resolves to the one committed file
    assert mod.nearest_wind_file(3.027, 41.905).name == "41.90_3.05.json"
    wind_dir = tmp_path / "wind"
    wind_dir.mkdir()
    for name in ("41.90_3.05.json", "41.90_2.95.json", "42.00_3.05.json"):
        (wind_dir / name).write_text("{}")
    assert mod.nearest_wind_file(2.97, 41.93, wind_dir).name == "41.90_2.95.json"
    assert mod.nearest_wind_file(3.04, 41.96, wind_dir).name == "42.00_3.05.json"
    assert mod.nearest_wind_file(3.10, 41.85, wind_dir).name == "41.90_3.05.json"
    with pytest.raises(FileNotFoundError):
        mod.nearest_wind_file(3.0, 41.9, tmp_path / "empty")
    series = {"time": ["2026-07-03T12:00", "2026-07-03T13:00", "2026-07-03T14:00"],
              "wind_speed_10m": [7.24, 5.8, None], "wind_direction_10m": [6, 15, 20]}
    exact = mod.wind_at(series, datetime(2026, 7, 3, 13, 20, 1, tzinfo=utc))       # hour containing as_of
    assert exact == {"valid_time": "2026-07-03T13:00:00+00:00", "exact_hour": True,
                     "wind_speed_mps": 5.8, "wind_dir_deg": 15.0}
    null_hour = mod.wind_at(series, datetime(2026, 7, 3, 14, 30, tzinfo=utc))        # 14:00 is null -> 13:00
    assert null_hour["valid_time"] == "2026-07-03T13:00:00+00:00" and null_hour["exact_hour"] is False
    outside = mod.wind_at(series, datetime(2026, 7, 6, 0, 0, tzinfo=utc))            # after the series -> last value
    assert outside["valid_time"] == "2026-07-03T13:00:00+00:00" and outside["exact_hour"] is False
    with pytest.raises(ValueError):
        mod.wind_at({"time": ["2026-07-03T12:00"], "wind_speed_10m": [None], "wind_direction_10m": [None]},
                    datetime(2026, 7, 3, 12, tzinfo=utc))
    # FireState from the first committed real perimeter: EPSG:25831 polygon, wind of the as_of hour
    fire, as_of = mod.recorded_real_fires()[0]
    fs, wind = mod.real_fire_state(fire, as_of)
    assert fs.cluster_id == fire["incident_id"] and fs.t == as_of and fs.perimeter.is_valid and not fs.perimeter.is_empty
    assert 400_000 < fs.perimeter.centroid.x < 600_000 and 4_600_000 < fs.perimeter.centroid.y < 4_700_000
    assert (fs.wind_speed_mps, fs.wind_dir_deg) == (wind["wind_speed_mps"], wind["wind_dir_deg"]) == (5.8, 15.0)
    assert wind["valid_time"] == "2026-07-03T13:00:00+00:00" and wind["exact_hour"] is True
    assert wind["file"] == "fixtures/wind/41.90_3.05.json" and (wind["lat"], wind["lon"]) == (41.9, 3.05)
    assert wind["source"] == "ecmwf_ifs025 previous_day1"


def test_enrichment_config_is_a_copy_with_the_flag_on():
    mod = _make_snapshots_module()
    cfg = mod.enrichment_config()
    assert cfg.FEATURES["forecast_enrichment"] is True and config.FEATURES["forecast_enrichment"] is False
    assert cfg.FEATURES["value_at_risk"] is True and config.FEATURES["value_at_risk"] is False
    assert cfg.EVACUATION_POLICY == config.EVACUATION_POLICY and cfg.FRESHNESS == config.FRESHNESS
    cfg.EVACUATION_POLICY["by_type"]["school"]["mobilisation_min"] = 999
    assert config.EVACUATION_POLICY["by_type"]["school"]["mobilisation_min"] != 999   # deep copy
    # the synthetic snapshots take their arrivals from a forecast file, so only the value layer is on
    syn = mod.snapshot_config()
    assert syn.FEATURES["value_at_risk"] is True and syn.FEATURES["forecast_enrichment"] is False
    assert mod.feature_config().FEATURES == config.FEATURES     # no flags: an untouched copy
    with pytest.raises(KeyError):
        mod.feature_config(not_a_flag=True)


def test_ca_arrival_runs_only_when_a_wind_value_exists(tmp_path, capsys):
    """Fix B: the CA is the fallback for a real snapshot with no provider forecast, but it must not run on a
    guessed wind. Without a usable wind value `ca_arrival` returns None and the assets stay unforecast."""
    mod = _make_snapshots_module()
    fire, as_of = mod.recorded_real_fires()[0]
    empty = tmp_path / "wind"
    empty.mkdir()
    assert mod.ca_arrival(fire, as_of, label="gavarres_real-0009", wind_dir=empty) is None
    out = capsys.readouterr().out
    assert "gavarres_real-0009" in out and "no usable wind" in out and "forecast_unavailable" in out
    null_wind = tmp_path / "nulls"                              # a file exists, but the series has no value
    null_wind.mkdir()
    (null_wind / "41.90_3.05.json").write_text(json.dumps(
        {"lat": 41.9, "lon": 3.05, "model": "m", "slice": "s", "time": ["2026-07-03T13:00"],
         "wind_speed_10m": [None], "wind_direction_10m": [None]}))
    assert mod.ca_arrival(fire, as_of, label="gavarres_real-0009", wind_dir=null_wind) is None
    # with the committed wind file it runs and carries that wind and hour into the provenance note
    c = mod.perimeter_geometry(fire).centroid
    cx, cy = lonlat_to_xy(c.x, c.y)
    grid = Grid(cx - 2000, cy - 2000, cx + 2000, cy + 2000, 100.0)
    arrival = mod.ca_arrival(fire, as_of, grid, label="gavarres_real-0001")
    assert arrival is not None and arrival.wind["file"] == "fixtures/wind/41.90_3.05.json"
    assert "wind 5.80 m/s from 15 deg" in arrival.note and mod.WIND_SOURCE in arrival.note
    assert arrival.wind["exact_hour"] is True and "nearest available hour" not in arrival.summary


def test_build_pair_arrivals_path_enriches_only_under_the_enrichment_config(tmp_path, capsys):
    mod = _make_snapshots_module()
    fs = FireState.from_json(FIX / "synthetic_ignition.json")
    fire = mod.fire_update(fs, fs.perimeter, mod.T1, "x")
    cx, cy = fs.perimeter.centroid.x, fs.perimeter.centroid.y
    grid = Grid(cx - 2000, cy - 2000, cx + 2000, cy + 2000, 100.0)      # 40 x 40 cells: sub-second CA
    raster = spread.run_ca(fs, grid, n_runs=5, horizon_min=240, seed=1)
    arrival = mod.CaArrival(raster=raster, wind={}, note="wind 8.00 m/s from 340 deg (test)", summary="test CA")
    rows = mod.synthetic_assets()
    common = dict(scenario_id="ca_test", incident_id=fs.cluster_id, arrivals=[arrival], out_dir=tmp_path)
    on = mod.build_pair(rows, [(fire, mod.T1)], cfg=mod.enrichment_config(), **common)[0]
    off = mod.build_pair(rows, [(fire, mod.T1)], **common)[0]           # module config: flag off
    assert (tmp_path / "ca_test_0001.json").exists() and validate_snapshot(on) == [] and validate_snapshot(off) == []
    assert config.FEATURES["forecast_enrichment"] is False
    assert all(a["fire_arrival_at"] is None and a["forecast_source"] is None for a in off["assets"])
    by_id = {a["asset_id"]: a for a in on["assets"]}
    sant_pol = by_id["fixture:sant_pol"]                                 # 718 m downwind of the ignition
    assert sant_pol["fire_arrival_at"] is not None and sant_pol["forecast_source"] == CA_LABEL
    assert sant_pol["fire_arrival_basis"] == CA_BASIS and "forecast_unavailable" not in sant_pol["review_reasons"]
    src = next(e for e in sant_pol["sources"] if "fire_arrival_at" in e["fields"])
    assert src["notes"].endswith("; wind 8.00 m/s from 340 deg (test)")
    assert by_id["fixture:hospital_palamos"]["forecast_source"] is None    # outside the tiny grid: untouched
    unl = by_id["fixture:residencia_sense_coordenades"]
    assert unl["fire_arrival_at"] is None and unl["forecast_source"] is None and "forecast_unavailable" in unl["review_reasons"]
    assert all("forecast_unavailable" in a["review_reasons"] for a in on["assets"] if a["fire_arrival_at"] is None)
    assert "arrival from test CA" in capsys.readouterr().out


def test_simulated_fire_geometry_kind_is_labelled_and_validated():
    fire = fire_update(fire_disc(300), kind="simulated")
    snap = build_snapshot([row("fixture:a", lon=IGNITION[0], lat=IGNITION[1] - 0.01)], fire, **snap_kwargs())
    assert snap["fire_geometry_kind"] == "simulated" and validate_snapshot(snap) == []
    src = [s for s in snap["assets"][0]["sources"] if "distance_to_fire_m" in s["fields"]][0]
    assert snapshot.SIMULATED_NOTE in src["notes"] and snapshot.POINT_FALLBACK_NOTE in src["notes"]
    bad = dict(snap, fire_geometry_kind="perimeter", fire_geometry={"type": "Point", "coordinates": list(IGNITION)})
    assert any("hotspot_centre" in e for e in validate_snapshot(bad))
    with pytest.raises(ValueError):
        build_snapshot([], fire_update(fire_disc(), kind="guess"), **snap_kwargs())


def test_committed_real_snapshot_4_uses_the_recorded_fire_spread_run():
    s4 = _load("gavarres_real_0004.json")
    assert validate_snapshot(s4) == [] and s4["sequence"] == 4 and s4["snapshot_id"] == "gavarres_real-0004"
    assert s4["input_mode"] == "recorded" and s4["as_of"] == "2026-09-19T13:49:18.165734+00:00"
    assert s4["fire_geometry_kind"] == "simulated" and s4["fire_geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert s4["fire_source"].startswith("deepfire:fire-spread/elmfire/4bbd8e98") and "not an observed perimeter" in s4["fire_source"]
    s3 = _load("gavarres_real_0003.json")
    assert [a["asset_id"] for a in s4["assets"]] == [a["asset_id"] for a in s3["assets"]] and s4["as_of"] > s3["as_of"]
    located = [a for a in s4["assets"] if a["latitude"] is not None]
    assert len(located) == 111 and all(a["distance_to_fire_m"] is not None for a in located)
    # honest outcome: the 12 h simulated burned area (~20 ha) covers none of the real facilities
    assert all(a["fire_arrival_at"] is None and "forecast_unavailable" in a["review_reasons"] for a in s4["assets"])
    assert min(a["distance_to_fire_m"] for a in located) > 5000
    assert all(snapshot.SIMULATED_NOTE in [s for s in a["sources"] if "distance_to_fire_m" in s["fields"]][0]["notes"]
               for a in located)
    # Fix A: the run states a burn probability for every location it covers. 0.0 ("covered, nothing burns
    # here within the horizon") is not the null of the unlocated rows ("no forecast covers this asset").
    assert all(a["burn_probability"] == 0.0 and a["forecast_source"] is not None for a in located)
    assert all(a["forecast_source"].startswith("deepfire:fire-spread/elmfire/4bbd8e98") for a in located)
    unlocated = [a for a in s4["assets"] if a["latitude"] is None]
    assert len(unlocated) == 69
    assert all(a["burn_probability"] is None and a["forecast_source"] is None
               and "location_unknown" in a["review_reasons"] for a in unlocated)
    # a forecast that reaches nothing is a zero, not a gap: the header says "0 exposed", not "unavailable"
    valued = [a for a in located if a["estimated_occupancy"] is not None and a["replacement_value_eur"] is not None]
    assert valued and all(a["people_exposed"] == 0 and a["people_at_risk_p50"] == 0 and a["people_at_risk_p10"] == 0
                          and a["expected_loss_eur_low"] == a["expected_loss_eur_mid"]
                          == a["expected_loss_eur_high"] == 0 for a in valued)


def test_committed_snapshots_carry_the_value_at_risk_layer():
    """Every committed snapshot is built with FEATURES["value_at_risk"] on, so all eight keys are present on
    every asset, the arithmetic is the policy's, and a missing input gives null and never a zero."""
    policy = config.VALUE_AT_RISK_POLICY
    buffer_min = float(config.CONTACT_POLICY["buffer_min"])
    names = ["synthetic_gavarres_0001.json", "synthetic_gavarres_0002.json", "gavarres_real_0001.json",
             "gavarres_real_0002.json", "gavarres_real_0003.json", "gavarres_real_0004.json"]
    seen_unvalued = seen_occupancy_unknown = seen_at_risk = False
    for name in names:
        s = _load(name)
        assert validate_snapshot(s) == [], name
        for a in s["assets"]:
            assert [k for k in snapshot.VALUE_AT_RISK_KEYS if k not in a] == [], (name, a["asset_id"])
            band = policy["by_type"].get(a["asset_type"])
            value = None if band is None else band["replacement_value_eur"]
            # the class value is a property of the class, known even without a location or a forecast
            assert a["replacement_value_eur"] == value
            assert (a["replacement_value_basis"] is None) == (value is None)
            if value is None:
                seen_unvalued = True
                assert a["expected_loss_eur_mid"] is None
            occ, bp = a["estimated_occupancy"], a["burn_probability"]
            if occ is None or bp is None:
                seen_occupancy_unknown = seen_occupancy_unknown or (occ is None and a["latitude"] is not None)
                assert a["people_exposed"] is None                      # null, never 0
            else:
                assert a["people_exposed"] == round(occ * bp, 1)
            if value is not None and bp is not None:
                for level in ("low", "mid", "high"):
                    assert a[f"expected_loss_eur_{level}"] == round(bp * band[f"d_{level}"] * value)
            else:
                assert all(a[f"expected_loss_eur_{level}"] is None for level in ("low", "mid", "high"))
            if a["latitude"] is None:                                   # unlocated: no risk, only a class value
                assert all(a[k] is None for k in snapshot.VALUE_AT_RISK_KEYS
                           if k not in ("replacement_value_eur", "replacement_value_basis"))
            for key, quantile in (("people_at_risk_p50", "arrival_p50_at"), ("people_at_risk_p10", "arrival_p10_at")):
                if occ is None or a["evacuation_min"] is None or not a["forecast_source"] or a["latitude"] is None:
                    assert a[key] is None
                    continue
                if a[quantile] is None:                                 # covered but not reached: 0, not null
                    assert a[key] == 0
                    continue
                slack = ((datetime.fromisoformat(a[quantile]) - datetime.fromisoformat(s["as_of"])).total_seconds() / 60.0
                         - a["evacuation_min"] - buffer_min)
                assert a[key] == (occ if slack <= 0 else 0)
                seen_at_risk = seen_at_risk or a[key] > 0
            if a["replacement_value_eur"] is not None:
                entry = next(e for e in a["sources"] if "replacement_value_eur" in e["fields"])
                assert entry["source"] == "config.VALUE_AT_RISK_POLICY" and policy["version"] in entry["notes"]
                assert "never enter the ranking" in entry["notes"] and "not an insurer's figure" in entry["notes"]
    assert seen_unvalued and seen_occupancy_unknown and seen_at_risk    # each rule is actually exercised


@pytest.mark.parametrize("operation", ["read", "write"])
def test_snapshot_utf8_under_ascii_default(tmp_path, operation):
    import os
    import subprocess
    import sys

    path = tmp_path / "snapshot.json"
    payload = {"name": "Escola de Cruïlles — 東京"}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    script = f"""
import locale
from pathlib import Path
from fireline.snapshot import read_snapshot, write_snapshot
assert locale.getpreferredencoding(False).lower() in ('ascii', 'us-ascii', 'ansi_x3.4-1968')
payload = {ascii(payload)}
path = Path({ascii(str(path))})
if {operation!r} == 'write':
    write_snapshot(payload, path)
assert read_snapshot(path) == payload
assert path.read_bytes().decode('utf-8')
"""
    env = dict(os.environ, LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
    subprocess.run([sys.executable, "-c", script], env=env, check=True, timeout=30)
