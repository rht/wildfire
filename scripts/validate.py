#!/usr/bin/env python
"""Measured validation of the v4 MVP (readme.md section 11): recorded outcomes, not claims.

    .venv/bin/python scripts/validate.py            # run every check, print the table
    .venv/bin/python scripts/validate.py --write    # also write VALIDATION.md

Every check runs offline on the committed fixtures: no network, no Deepfire credentials, no LLM key
(the agent check uses `fireline.llm.FakeLLM` and is labelled as such). Each check is a function
returning `{"name", "outcome", "details", "measured"}` with outcome `pass`, `fail` or
`not verified`; `tests/test_validate.py` imports them. What the script cannot measure is listed
under "Not verified" rather than claimed.
"""

from __future__ import annotations

import argparse
import copy
import json
import platform
import statistics
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fireline import agent, config, fire_input, priority, snapshot  # noqa: E402
from fireline.grid import lonlat_to_xy, xy_to_lonlat  # noqa: E402
from fireline.tasks import AssignmentError, TaskStore  # noqa: E402

VALIDATION_DATE = "2026-09-19"
FIX = ROOT / "fixtures"
SNAP_DIR = FIX / "snapshots"
REAL_ASSETS = FIX / "real_area" / "assets_gavarres.json"
RECORDED_FIRE = FIX / "fire" / "deepfire"
TEAMS = FIX / "teams.json"
EVIDENCE = FIX / "evidence.json"
OUTPUT = ROOT / "VALIDATION.md"
LATENCY_TARGET_S = 60.0

PASS, FAIL, NOT_VERIFIED = "pass", "fail", "not verified"


# ------------------------------------------------------------------------------------------ helpers
def _snap(name: str) -> dict:
    return snapshot.read_snapshot(SNAP_DIR / f"{name}.json")


def _real_assets() -> list[dict]:
    payload = json.loads(REAL_ASSETS.read_text(encoding="utf-8"))
    return payload["assets"] if isinstance(payload, dict) else payload


def _store() -> TaskStore:
    store = TaskStore(":memory:")
    store.load_roster(TEAMS)
    return store


def _asset(**overrides) -> dict:
    """Complete synthetic asset record (CONTRACTS 2.2), same defaults as tests/helpers.make_asset."""
    asset_type = overrides.get("asset_type", "school")
    reasons = list(overrides.get("review_reasons", []))
    rec = {
        "asset_id": "fixture:test_asset", "name": "Test asset", "asset_type": asset_type,
        "latitude": 41.95, "longitude": 3.05, "geometry": None, "area_m2": None,
        "capacity": 200, "estimated_occupancy": None, "occupancy_basis": "register capacity",
        "value_score": config.VALUE_POLICY["by_type"].get(asset_type), "value_basis": config.VALUE_POLICY["version"],
        "distance_to_fire_m": 2000.0, "intersects_fire": False, "burn_probability": None,
        "arrival_p10_at": None, "arrival_p50_at": None, "forecast_horizon_at": None, "forecast_source": None,
        "fire_arrival_at": None, "fire_arrival_basis": None, "evacuation_min": None, "evacuation_source": None,
        "needs_review": bool(reasons), "review_reasons": reasons, "sources": [], "municipality": None,
    }
    rec.update(overrides)
    if "needs_review" not in overrides:
        rec["needs_review"] = bool(rec["review_reasons"])
    return rec


def _envelope(assets: list[dict], sequence: int = 1, scenario_id: str = "validate") -> dict:
    as_of = "2026-07-03T08:00:00+00:00"
    return {
        "schema_version": "1.0", "scenario_id": scenario_id, "incident_id": f"{scenario_id}-incident",
        "snapshot_id": f"{scenario_id}-{sequence:04d}", "sequence": sequence, "as_of": as_of, "computed_at": as_of,
        "input_mode": "synthetic", "fire_observed_at": as_of, "fire_source": "fixture:validate",
        "fire_geometry": {"type": "Point", "coordinates": [3.0, 41.9]}, "fire_geometry_kind": "hotspot_centre",
        "data_status": "current", "metrics": {"source_age_s": 0.0, "processing_s": 0.0}, "assets": list(assets),
    }


AS_OF = "2026-07-03T08:00:00+00:00"
FORECAST = "fixture:validate-spread (synthetic, not a validated forecast)"
EVAC_SOURCE = config.EVACUATION_POLICY["version"]


def _timed(asset_id: str, arrival_min: float | None, evacuation_min: float | None, **kw) -> dict:
    """Asset with a synthetic forecast arrival `arrival_min` minutes after AS_OF and an evacuation estimate."""
    from datetime import datetime, timedelta

    arrival = None if arrival_min is None else (datetime.fromisoformat(AS_OF) + timedelta(minutes=arrival_min)).isoformat()
    fields = {
        "asset_id": asset_id, "fire_arrival_at": arrival, "fire_arrival_basis": None if arrival is None else "p10",
        "forecast_source": None if arrival is None else FORECAST,
        "forecast_horizon_at": None if arrival is None else "2026-07-03T20:00:00+00:00",
        "evacuation_min": evacuation_min, "evacuation_source": None if evacuation_min is None else EVAC_SOURCE,
    }
    fields.update(kw)
    return _asset(**fields)


def _top(scored: dict, n: int = 3) -> list[str]:
    """Top-n of the ranked queue with the remaining window; falls back to the review queue when empty."""
    if scored["ranked"]:
        return [f"{a['asset_id'].split(':', 1)[-1]} {a['slack_min']:.0f} min" for a in scored["ranked"][:n]]
    return [f"{a['asset_id'].split(':', 1)[-1]} unranked" for a in scored["needs_review"][:n]]


def _square_wgs84(cx: float, cy: float, half: float) -> dict:
    """Axis-aligned square in EPSG:25831 metres around (cx, cy) as a WGS84 GeoJSON Polygon."""
    corners = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)]
    ring = [[float(v) for v in xy_to_lonlat(x, y)] for x, y in corners]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def _result(name: str, ok: bool, details: list[str], measured: dict | None = None,
            outcome: str | None = None) -> dict:
    return {"name": name, "outcome": outcome or (PASS if ok else FAIL), "details": details, "measured": measured or {}}


# ------------------------------------------------------------------------------------------ checks
def check_coverage() -> dict:
    """Readme 11 coverage/matching: counts per class in the fixed area, located vs unlocated, sample, gaps."""
    payload = json.loads(REAL_ASSETS.read_text(encoding="utf-8"))
    assets = payload["assets"]
    by_type = dict(sorted(Counter(a["asset_type"] for a in assets).items()))
    located = [a for a in assets if a["latitude"] is not None and a["longitude"] is not None]
    unlocated = [a for a in assets if a["latitude"] is None or a["longitude"] is None]
    ambiguous = [a for a in assets if "class_ambiguous" in a["review_reasons"]]
    unknown_class = [a for a in assets if a["asset_type"] == "unknown" or "value_unknown" in a["review_reasons"]]
    with_capacity = [a for a in assets if a["capacity"] is not None or a["estimated_occupancy"] is not None]
    by_register = dict(sorted(Counter(a["sources"][0]["source"] for a in assets).items()))
    munis = sorted({a["municipality"] for a in assets if a["municipality"]}, key=str.casefold)
    footprints = sum(1 for a in assets if a["geometry"] is not None)
    missing_keys = [a["asset_id"] for a in assets if any(k not in a for k in snapshot.ASSET_KEYS)]
    dup_ids = [k for k, n in Counter(a["asset_id"] for a in assets).items() if n > 1]
    counts_ok = payload.get("counts") == {"located": len(located), "unlocated": len(unlocated), "total": len(assets)}
    step = max(1, len(located) // 5)
    sample = [f"{a['name']} ({a['asset_type']}, {a['municipality']})" for a in located[::step][:5]]
    unmatched = [f"{a['asset_id']} {a['name']!r}: {a['sources'][0]['notes']}" for a in ambiguous + unknown_class]
    ok = bool(assets) and counts_ok and not missing_keys and not dup_ids and not unknown_class
    details = [
        f"file {REAL_ASSETS.relative_to(ROOT)}: area {payload.get('area')}, bbox {payload.get('bbox')}, "
        f"extracted {payload.get('extracted_on')}",
        f"facilities per asset_type: {by_type}",
        f"by register: {by_register}",
        f"located {len(located)}, unlocated (null coordinates, location_unknown) {len(unlocated)}, "
        f"total {len(assets)}; envelope counts consistent: {counts_ok}",
        f"class_ambiguous: {len(ambiguous)}; unresolved class (asset_type unknown / value_unknown): {len(unknown_class)}",
        f"records with a register capacity or headcount: {len(with_capacity)} (all unlocated care homes / campsites); "
        f"located rows with occupancy_unknown: {sum('occupancy_unknown' in a['review_reasons'] for a in located)}",
        f"footprint geometries: {footprints} (every distance is a labelled point fallback)",
        f"municipalities: {len(munis)}",
        "sample of 5 located names: " + "; ".join(sample),
        "unmatched / ambiguous records: " + ("; ".join(unmatched) if unmatched else "none"),
        "register limitation: the care-home (ivft-vegh) and campsite (t2h3-cgys) registers carry no coordinates, "
        "so those rows are matched by municipality name, not geometry; located rows are almost all schools and "
        "carry no capacity (fixtures/real_area/README.md)",
    ]
    if missing_keys or dup_ids:
        details.append(f"records missing keys: {missing_keys}; duplicate ids: {dup_ids}")
    return _result("Coverage/matching", ok, details, {
        "total": len(assets), "located": len(located), "unlocated": len(unlocated),
        "by_type": by_type, "class_ambiguous": len(ambiguous), "unresolved_class": len(unknown_class)})


def check_geometry() -> dict:
    """Readme 11 geometry: overlap gives zero, separated example gives the metric distance, missing
    geometry stays unknown, point fallback is labelled."""
    cx, cy = lonlat_to_xy(3.0, 41.95)
    half = 500.0
    fire = _square_wgs84(cx, cy, half)
    # overlap: asset point at the square's centre
    lon_in, lat_in = xy_to_lonlat(cx, cy)
    d_in, i_in, note_in = snapshot.asset_exposure(lon_in, lat_in, None, fire)
    overlap_ok = d_in == 0.0 and i_in is True
    # separated: asset point 2000 m east of the square's east edge (EPSG:25831), exchanged as WGS84
    lon_e, lat_e = xy_to_lonlat(cx + half + 2000.0, cy)
    d_e, i_e, note_e = snapshot.asset_exposure(lon_e, lat_e, None, fire)
    sep_err = None if d_e is None else abs(d_e - 2000.0)
    sep_ok = sep_err is not None and sep_err <= 5.0 and i_e is False
    # footprint: a 100 m square asset whose west edge is 2000 m east of the fire -> 2000 m too, no fallback note
    foot = _square_wgs84(cx + half + 2000.0 + 50.0, cy, 50.0)
    d_f, i_f, note_f = snapshot.asset_exposure(lon_e, lat_e, foot, fire)
    foot_ok = d_f is not None and abs(d_f - 2000.0) <= 5.0 and i_f is False and note_f is None
    # missing location / missing fire geometry
    d_m, i_m, note_m = snapshot.asset_exposure(None, None, None, fire)
    d_nf, i_nf, note_nf = snapshot.asset_exposure(lon_e, lat_e, None, None)
    missing_ok = (d_m, i_m, note_m) == (None, None, snapshot.NO_LOCATION_NOTE) and \
                 (d_nf, i_nf, note_nf) == (None, None, snapshot.NO_FIRE_NOTE)
    # through build_snapshot: null stays null with location_unknown + exposure_unknown; fallback labelled in sources
    fire_update = {"provider": "fixture", "incident_id": "validate", "observed_at": "2026-07-03T08:00:00+00:00",
                   "received_at": "2026-07-03T08:00:00+00:00", "geometry": fire, "geometry_kind": "perimeter",
                   "source": "fixture:validate-square", "raw_ref": None}
    rows = [
        {"asset_id": "fixture:no_coords", "name": "No coords", "asset_type": "care_home", "latitude": None,
         "longitude": None, "capacity": 10},
        {"asset_id": "fixture:point_only", "name": "Point only", "asset_type": "school", "latitude": lat_e,
         "longitude": lon_e, "capacity": 10},
        {"asset_id": "fixture:with_footprint", "name": "With footprint", "asset_type": "school", "latitude": lat_e,
         "longitude": lon_e, "capacity": 10, "geometry": foot},
    ]
    snap = snapshot.build_snapshot(rows, fire_update, scenario_id="validate_geom", incident_id="validate",
                                   sequence=1, as_of="2026-07-03T08:00:00+00:00", input_mode="synthetic")
    errs = snapshot.validate_snapshot(snap)
    by_id = {a["asset_id"]: a for a in snap["assets"]}
    nc = by_id["fixture:no_coords"]
    null_ok = nc["distance_to_fire_m"] is None and nc["intersects_fire"] is None and \
        {"location_unknown", "exposure_unknown"} <= set(nc["review_reasons"])

    def fallback_notes(a):
        return [s["notes"] for s in a["sources"] if "distance_to_fire_m" in s["fields"]]

    po_notes = fallback_notes(by_id["fixture:point_only"])
    wf_notes = fallback_notes(by_id["fixture:with_footprint"])
    label_ok = any(snapshot.POINT_FALLBACK_NOTE in (n or "") for n in po_notes) and \
        not any(snapshot.POINT_FALLBACK_NOTE in (n or "") for n in wf_notes)
    # the committed synthetic fixture: every located asset carries the label (no footprints in the fixture)
    fx = _snap("synthetic_gavarres_0001")
    fx_located = [a for a in fx["assets"] if a["latitude"] is not None]
    fx_labelled = sum(any(snapshot.POINT_FALLBACK_NOTE in (s["notes"] or "") for s in a["sources"]) for a in fx_located)
    ok = overlap_ok and sep_ok and foot_ok and missing_ok and null_ok and label_ok and not errs
    details = [
        f"overlap (asset point inside a 1 km square fire, EPSG:25831): distance {d_in}, intersects {i_in}",
        f"separated (asset point 2000 m east of the square, exchanged as WGS84): distance {d_e} m, "
        f"error {sep_err:.2f} m (tolerance 5 m), intersects {i_e}",
        f"footprint asset (100 m square, west edge 2000 m from the fire): distance {d_f} m, fallback note: {note_f!r}",
        f"missing location: ({d_m}, {i_m}, {note_m!r}); missing fire geometry: ({d_nf}, {i_nf}, {note_nf!r})",
        f"build_snapshot with null coordinates: distance {nc['distance_to_fire_m']}, intersects {nc['intersects_fire']}, "
        f"review_reasons {nc['review_reasons']}",
        f"point fallback label in sources[fields ∋ distance_to_fire_m].notes: point-only asset {po_notes}, "
        f"footprint asset {wf_notes}",
        f"committed fixture synthetic_gavarres_0001: {fx_labelled}/{len(fx_located)} located assets carry "
        f"{snapshot.POINT_FALLBACK_NOTE!r}",
        f"validate_snapshot errors: {errs or 'none'}",
    ]
    return _result("Geometry", ok, details, {
        "overlap_distance_m": d_in, "separated_distance_m": d_e, "separated_error_m": sep_err,
        "footprint_distance_m": d_f})


def check_priority() -> dict:
    """Readme 11 priority: window = arrival - now - evacuation - buffer; a farther downwind asset with an
    earlier arrival outranks a nearer one; zero/negative windows first as window_exhausted; identical
    timing ties by distance then asset_id; missing forecast or evacuation -> needs_review with the reason;
    a missing distance does not block ranking. Constructed assets: the checks verify the implementation,
    not the supplied forecasts or evacuation estimates."""
    policy = config.CONTACT_POLICY
    buffer_min = policy["buffer_min"]
    # arithmetic: arrival 180 min after as_of, school evacuation 90 min, buffer 30 -> window 60
    single = priority.rank_asset(_timed("fixture:one", 180.0, 90.0), AS_OF)
    expected = 180.0 - 90.0 - buffer_min
    arith_ok = single["time_to_impact_min"] == 180.0 and single["latest_start_min"] == expected and \
        single["slack_min"] == expected and single["priority_status"] == "window_open" and single["queue"] == "ranked"
    # now_at 30 min later shrinks the window by 30 (elapsed time counts)
    later = priority.rank_asset(_timed("fixture:one", 180.0, 90.0), "2026-07-03T08:30:00+00:00")
    elapsed_ok = later["slack_min"] == expected - 30.0
    # farther downwind asset with the earlier arrival outranks the nearer one (same evacuation)
    near = _timed("fixture:near", 300.0, 90.0, distance_to_fire_m=800.0)
    far = _timed("fixture:far_downwind", 150.0, 90.0, distance_to_fire_m=4000.0)
    # nearer asset with a longer evacuation (care home) ranks above a farther one with an earlier arrival
    slow = _timed("fixture:slow_evac", 400.0, 180.0, distance_to_fire_m=1500.0, asset_type="care_home")
    quick = _timed("fixture:quick_evac", 360.0, 60.0, distance_to_fire_m=600.0, asset_type="masia")
    order = priority.rank_snapshot(_envelope([near, far, quick, slow]))
    order_ids = [a["asset_id"].split(":")[-1] for a in order["ranked"]]
    order_windows = {a["asset_id"].split(":")[-1]: a["slack_min"] for a in order["ranked"]}
    order_ok = order_ids == ["far_downwind", "near", "slow_evac", "quick_evac"] and \
        [a["priority_rank"] for a in order["ranked"]] == [1, 2, 3, 4]
    # zero and negative windows rank first as window_exhausted; still ranked, not review
    zero = _timed("fixture:zero", 120.0, 90.0)                      # 120 - 90 - 30 = 0
    negative = _timed("fixture:negative", 60.0, 90.0)               # 60 - 90 - 30 = -60
    open_ = _timed("fixture:open", 240.0, 90.0)
    ex = priority.rank_snapshot(_envelope([open_, zero, negative]))
    ex_ids = [a["asset_id"].split(":")[-1] for a in ex["ranked"]]
    ex_status = {a["asset_id"].split(":")[-1]: (a["slack_min"], a["priority_status"]) for a in ex["ranked"]}
    exhausted_ok = ex_ids == ["negative", "zero", "open"] and ex_status["negative"] == (-60.0, "window_exhausted") \
        and ex_status["zero"] == (0.0, "window_exhausted") and ex_status["open"][1] == "window_open" and \
        ex["needs_review"] == []
    # ties: identical timing -> nearer distance first, then asset_id; null distance last
    tie = priority.rank_snapshot(_envelope([
        _timed("fixture:tie_c", 200.0, 60.0, distance_to_fire_m=None),
        _timed("fixture:tie_b", 200.0, 60.0, distance_to_fire_m=900.0),
        _timed("fixture:tie_d", 200.0, 60.0, distance_to_fire_m=900.0),
        _timed("fixture:tie_a", 200.0, 60.0, distance_to_fire_m=2500.0)]))
    tie_ids = [a["asset_id"].split(":")[-1] for a in tie["ranked"]]
    tie_ok = tie_ids == ["tie_b", "tie_d", "tie_a", "tie_c"] and len({a["slack_min"] for a in tie["ranked"]}) == 1
    # missing inputs -> needs_review with the right reason; the reasons are added to review_reasons
    no_forecast = priority.rank_asset(_timed("fixture:no_forecast", None, 90.0), AS_OF)
    no_evac = priority.rank_asset(_timed("fixture:no_evac", 180.0, None), AS_OF)
    no_source = priority.rank_asset(_timed("fixture:no_source", 180.0, 90.0, forecast_source=None), AS_OF)
    missing_ok = all(a["queue"] == "needs_review" and a["priority_status"] == "needs_review" and a["slack_min"] is None
                     and a["priority_rank"] is None for a in (no_forecast, no_evac, no_source)) and \
        no_forecast["review_reasons"] == ["forecast_unavailable"] and no_evac["review_reasons"] == ["evacuation_unknown"] \
        and no_source["review_reasons"] == ["forecast_unavailable"] and \
        "evacuation_unknown" not in no_forecast["review_reasons"]
    # missing distance does not block ranking
    no_distance = priority.rank_asset(_timed("fixture:no_distance", 180.0, 90.0, distance_to_fire_m=None,
                                             intersects_fire=None, review_reasons=["exposure_unknown"]), AS_OF)
    distance_ok = no_distance["queue"] == "ranked" and no_distance["slack_min"] == expected and \
        "review flag: exposure_unknown" in no_distance["priority_reasons"]
    # agreement with the static prototype (contact_priority.rank_contacts) on the same scenario
    from fireline.contact_priority import ContactPolicy, rank_contacts
    from fireline.priority_models import Location

    def loc(a):
        arr = None if a["fire_arrival_at"] is None else priority.minutes_between(a["fire_arrival_at"], AS_OF)
        return Location(asset_id=a["asset_id"], name=a["name"], x_m=0.0, y_m=0.0, distance_m=a["distance_to_fire_m"],
                        people=None, assisted=None, value=None, deadline_min=None, fire_arrival_min=arr,
                        evacuation_min=a["evacuation_min"], forecast_source=a["forecast_source"],
                        evacuation_source=a["evacuation_source"])
    scenario = [near, far, quick, slow, zero, negative, open_, _timed("fixture:no_forecast", None, 90.0)]
    static = rank_contacts([loc(a) for a in scenario], ContactPolicy(now_min=0.0, buffer_min=buffer_min))
    live = priority.rank_snapshot(_envelope(scenario))
    static_pairs = [(r["asset_id"], r["slack_min"]) for r in static["ranked"]]
    live_pairs = [(a["asset_id"], a["slack_min"]) for a in live["ranked"]]
    agree_ok = static_pairs == live_pairs and [r["asset_id"] for r in static["review"]] == \
        [a["asset_id"] for a in live["needs_review"]]
    # the committed fixture (whatever the producer supplies): every asset lands in one queue
    snap = _snap("synthetic_gavarres_0001")
    scored = priority.rank_snapshot(snap)
    fx_ranked, fx_review = scored["ranked"], scored["needs_review"]
    fx_ok = len(fx_ranked) + len(fx_review) == len(snap["assets"]) and \
        [a["priority_rank"] for a in fx_ranked] == list(range(1, len(fx_ranked) + 1)) and \
        [a["slack_min"] for a in fx_ranked] == sorted(a["slack_min"] for a in fx_ranked)
    fx_reasons = Counter(r for a in fx_review for r in a["review_reasons"] if r in ("forecast_unavailable", "evacuation_unknown"))
    ok = arith_ok and elapsed_ok and order_ok and exhausted_ok and tie_ok and missing_ok and distance_ok and agree_ok and fx_ok
    details = [
        f"policy {policy['version']}: buffer {buffer_min} min, now = {policy['now']}, arrival basis: {policy['arrival_basis']}; "
        f"evacuation durations from {config.EVACUATION_POLICY['version']} unless overridden",
        f"arithmetic (arrival +180 min, evacuation 90 min, buffer {buffer_min}): time_to_impact {single['time_to_impact_min']}, "
        f"latest start {single['latest_start_min']}, remaining window {single['slack_min']} (expected {expected}), "
        f"status {single['priority_status']}; with now 30 min later the window is {later['slack_min']}: {arith_ok and elapsed_ok}",
        f"farther outranks nearer: order {order_ids} with windows {order_windows} (far_downwind at 4000 m arrives at "
        f"+150 min and outranks near at 800 m arriving at +300 min; slow_evac at 1500 m with a 180 min care-home "
        f"evacuation outranks quick_evac at 600 m): {order_ok}",
        f"zero/negative windows: order {ex_ids}, (window, status) {ex_status}; needs_review empty: {exhausted_ok}",
        f"stable ties (identical timing): {tie_ids} = nearer distance first, then asset_id, null distance last: {tie_ok}",
        f"missing estimates: no forecast -> {no_forecast['queue']} {no_forecast['review_reasons']}; no evacuation -> "
        f"{no_evac['queue']} {no_evac['review_reasons']}; forecast without provenance -> {no_source['queue']} "
        f"{no_source['review_reasons']}: {missing_ok}",
        f"missing distance with known timing: queue {no_distance['queue']}, window {no_distance['slack_min']} "
        f"(does not block ranking): {distance_ok}",
        f"agreement with contact_priority.rank_contacts (readme 16) on {len(scenario)} assets: ranked (id, window) pairs "
        f"identical {static_pairs == live_pairs}, review sets identical: {agree_ok}",
        f"committed fixture synthetic_gavarres_0001: {len(fx_ranked)} ranked, {len(fx_review)} needs_review "
        f"(reasons {dict(fx_reasons)}); ranks contiguous and windows non-decreasing: {fx_ok}"
        + (" - the ranked queue is empty because these fixtures carry no fire_arrival_at / evacuation_min yet "
           "(producer v1.1 fields pending); ranking is verified on the constructed assets above" if not fx_ranked else ""),
        "these checks verify the implementation, not the accuracy of supplied forecasts or evacuation estimates",
    ]
    return _result("Priority", ok, details, {
        "buffer_min": buffer_min, "window_single": single["slack_min"], "expected_single": expected,
        "order": order_ids, "exhausted_order": ex_ids, "ties": tie_ids, "fixture_ranked": len(fx_ranked),
        "fixture_review": len(fx_review), "static_agreement": agree_ok})


def check_updates() -> dict:
    """Readme 11 updates: duplicates/older rejected, priorities change, source age preserved,
    missing asset flagged without task closure."""
    snap1, snap2 = _snap("synthetic_gavarres_0001"), _snap("synthetic_gavarres_0002")
    store = _store()
    r1 = store.apply_snapshot(snap1)
    dup = store.apply_snapshot(snap1)
    scored1 = priority.rank_snapshot(snap1, overrides=store.overrides())
    suggested = store.suggest_tasks(scored1["all"], snap1["snapshot_id"])
    before = _top(scored1)
    r2 = store.apply_snapshot(snap2)
    scored2 = priority.rank_snapshot(snap2, overrides=store.overrides())
    after = _top(scored2)
    ranked_any = bool(scored1["ranked"] or scored2["ranked"])
    order_changed = before != after
    older = copy.deepcopy(snap1)
    older["snapshot_id"] = "synthetic_gavarres-0001-replay"
    old = store.apply_snapshot(older)
    last_after_replay = store.last_sequence(snap1["scenario_id"])
    guard_ok = r1["accepted"] and not dup["accepted"] and "duplicate" in dup["reason"] and r2["accepted"] and \
        not old["accepted"] and "sequence" in old["reason"] and last_after_replay["sequence"] == 2
    change_ok = (order_changed or not ranked_any) and len(r2["changed"]) > 0 and \
        {a["asset_id"] for a in snap2["assets"]} == {a["asset_id"] for a in snap1["assets"]}
    # source age preserved: an asset whose exposure did not change keeps identical non-fire provenance;
    # on the real-area snapshots the register fetched_at stays 2026-09-19 while as_of advances 2 h
    a1 = next(a for a in snap1["assets"] if a["asset_id"] == "fixture:escola_cruilles")
    a2 = next(a for a in snap2["assets"] if a["asset_id"] == "fixture:escola_cruilles")
    # exposure and forecast provenance legitimately follow the fire update; everything else must be identical
    fire_dependent = {"distance_to_fire_m", "fire_arrival_at", "arrival_p10_at", "arrival_p50_at", "forecast_source"}
    non_fire = lambda a: [s for s in a["sources"] if not fire_dependent & set(s["fields"])]  # noqa: E731
    fire_src = lambda a: next(s for s in a["sources"] if "distance_to_fire_m" in s["fields"])  # noqa: E731
    same_synth = non_fire(a1) == non_fire(a2) and a1["distance_to_fire_m"] == a2["distance_to_fire_m"]
    real1, real2 = _snap("gavarres_real_0001"), _snap("gavarres_real_0002")
    ra1, ra2 = real1["assets"][0], real2["assets"][0]
    age1, age2 = priority.input_age(ra1, real1["as_of"]), priority.input_age(ra2, real2["as_of"])
    same_real = ra1["asset_id"] == ra2["asset_id"] and age1["newest_fetched_at"] == age2["newest_fetched_at"] and \
        age1["newest_fetched_at"] is not None and non_fire(ra1) == non_fire(ra2)
    age_ok = same_synth and same_real
    # missing asset: drop one asset with open tasks from a copy of seq 2 as sequence 3
    missing_id = "fixture:pou_del_glac"
    tasks_before = {t["task_id"]: (t["status"], t["assigned_team_id"]) for t in store.tasks(asset_id=missing_id)}
    snap3 = copy.deepcopy(snap2)
    snap3["sequence"], snap3["snapshot_id"] = 3, f"{snap2['scenario_id']}-0003"
    snap3["assets"] = [a for a in snap3["assets"] if a["asset_id"] != missing_id]
    r3 = store.apply_snapshot(snap3)
    tasks_after = {t["task_id"]: (t["status"], t["assigned_team_id"]) for t in store.tasks(asset_id=missing_id)}
    flagged = [t["task_id"] for t in store.tasks(asset_id=missing_id) if t["affected_by_snapshot_id"] == snap3["snapshot_id"]]
    exposure = store.exposure(snap2["scenario_id"]).get(missing_id) or {}
    missing_events = [e for e in store.events() if e["kind"] == "asset_missing" and e["asset_id"] == missing_id]
    missing_ok = r3["accepted"] and r3["missing_asset_ids"] == [missing_id] and tasks_before and \
        tasks_before == tasks_after and set(flagged) == set(tasks_before) and exposure.get("present") is False and \
        bool(missing_events) and all(s != "done" for s, _ in tasks_after.values())
    ok = guard_ok and change_ok and age_ok and missing_ok
    details = [
        f"seq 1 accepted: {r1['accepted']}; duplicate seq 1: accepted {dup['accepted']} ({dup.get('reason')})",
        f"seq 2 accepted: {r2['accepted']}, {len(r2['changed'])} assets changed, {len(r2['affected_task_ids'])} open tasks "
        f"flagged; replay of seq 1 under a new id: accepted {old['accepted']} ({old.get('reason')}); last sequence "
        f"after the replay {last_after_replay}",
        f"top-3 before (seq 1): {before}",
        f"top-3 after (seq 2): {after}; ranked order changed: {order_changed}"
        + ("" if ranked_any else " (no asset ranked: the fixtures carry no forecast arrival / evacuation estimate yet, "
                                "so the affected-priority check rests on the changed-asset flags above)"),
        f"source age preserved (synthetic, escola_cruilles unchanged at {a1['distance_to_fire_m']} m): non-fire, non-forecast sources "
        f"identical {same_synth}; fire source observed_at {fire_src(a1)['observed_at']} -> {fire_src(a2)['observed_at']}",
        f"source age preserved (real-area {ra1['asset_id']}): newest_fetched_at {age1['newest_fetched_at']} and the "
        f"register provenance identical in both snapshots while as_of advances {real1['as_of']} -> {real2['as_of']}: "
        f"{same_real} (the register extract of 2026-09-19 postdates the recorded real fire of 2026-07-03/04, so these "
        f"fixtures are not an as-of replay)",
        f"missing asset (seq 3 = seq 2 without {missing_id}): missing_asset_ids {r3['missing_asset_ids']}; tasks "
        f"{tasks_before} -> {tasks_after}; flagged {flagged}; exposure present {exposure.get('present')}; "
        f"asset_missing events {len(missing_events)}",
        f"{len(suggested)} tasks suggested from seq 1 review reasons",
    ]
    return _result("Updates", ok, details, {
        "changed_assets_seq2": len(r2["changed"]), "top3_before": before, "top3_after": after, "ranked_any": ranked_any,
        "missing_asset_ids": r3["missing_asset_ids"], "tasks_kept": len(tasks_after)})


def check_tasks() -> dict:
    """Readme 11 tasks: no duplicate suggestions, assignment survives update and reload, roster rejections,
    resource requests may stay unassigned and blocked."""
    snap1, snap2 = _snap("synthetic_gavarres_0001"), _snap("synthetic_gavarres_0002")
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "fireline.sqlite"
        store = TaskStore(db)
        store.load_roster(TEAMS)
        store.apply_snapshot(snap1)
        scored = priority.rank_snapshot(snap1, overrides=store.overrides())
        first = store.suggest_tasks(scored["all"], snap1["snapshot_id"])
        second = store.suggest_tasks(scored["all"], snap1["snapshot_id"])
        keys = Counter((t["asset_id"], t["action"], t["reason"]) for t in store.tasks())
        dedupe_ok = bool(first) and second == [] and max(keys.values()) == 1
        occ = [t for t in first if t["action"] == "confirm_occupancy"]
        # t_a is on fixture:pou_del_glac, whose exposure changes in seq 2 (1761 m -> intersects)
        t_a = next(t for t in occ if t["asset_id"] == "fixture:pou_del_glac")
        t_b = next(t for t in occ if t["asset_id"] != t_a["asset_id"])
        assigned = store.assign(t_a["task_id"], "team_bisbal_1")
        store.set_status(t_a["task_id"], "in_progress", note="called the facility")
        # rejections
        rejections = {}
        for label, task_id, team in (("busy", t_b["task_id"], "team_bisbal_1"),
                                     ("capability mismatch", t_b["task_id"], "team_medical_1"),
                                     ("unavailable", t_b["task_id"], "team_palafrugell_1")):
            try:
                store.assign(task_id, team)
                rejections[label] = "ACCEPTED (unexpected)"
            except AssignmentError as e:
                rejections[label] = str(e)
        rej_ok = "team busy" in rejections["busy"] and "capability mismatch" in rejections["capability mismatch"] \
            and "team unavailable" in rejections["unavailable"] and store.get(t_b["task_id"])["assigned_team_id"] is None
        # resource request stays unassigned and blocked
        req = store.create_task("fixture:sant_pol", "request_resources", "transport for 85 residents",
                                snapshot_id=snap1["snapshot_id"])
        store.add_question(req["task_id"], "how many vehicles are available?")
        req = store.set_status(req["task_id"], "blocked", note="no transport team on the roster")
        req_ok = req["status"] == "blocked" and req["assigned_team_id"] is None and req["blocking_questions"]
        # survives the next snapshot
        r2 = store.apply_snapshot(snap2)
        after_update = store.get(t_a["task_id"])
        update_ok = after_update["assigned_team_id"] == "team_bisbal_1" and after_update["status"] == "in_progress" \
            and after_update["notes"] == "called the facility" and after_update["affected_by_snapshot_id"] == snap2["snapshot_id"]
        n_tasks, n_events = len(store.tasks()), len(store.events())
        store.close()
        # survives reload
        reopened = TaskStore(db)
        after_reload = reopened.get(t_a["task_id"])
        replay = reopened.apply_snapshot(snap2)
        reload_ok = after_reload == after_update and len(reopened.tasks()) == n_tasks and \
            reopened.get(req["task_id"])["status"] == "blocked" and not replay["accepted"] and \
            reopened.last_sequence(snap1["scenario_id"])["sequence"] == 2
        reopened.close()
    ok = dedupe_ok and rej_ok and req_ok and update_ok and reload_ok
    details = [
        f"suggestions from seq 1: first call {len(first)} tasks, second call {len(second)}; max tasks per "
        f"(asset, action, reason) key {max(keys.values())}",
        f"assigned {t_a['task_id']} ({t_a['asset_id']}, {t_a['action']}) to team_bisbal_1 -> status {assigned['status']}, "
        f"then in_progress",
        f"after seq 2 (accepted {r2['accepted']}, {len(r2['affected_task_ids'])} tasks flagged): team "
        f"{after_update['assigned_team_id']}, status {after_update['status']}, affected_by {after_update['affected_by_snapshot_id']}",
        f"after reload from sqlite file: record identical {after_reload == after_update}; {n_tasks} tasks, {n_events} events "
        f"persisted; replay of seq 2 after reload accepted {replay['accepted']}",
        f"busy team rejected: {rejections['busy']!r}",
        f"capability mismatch rejected: {rejections['capability mismatch']!r}",
        f"unavailable team rejected: {rejections['unavailable']!r}",
        f"request_resources task {req['task_id']}: status {req['status']}, assigned_team_id {req['assigned_team_id']}, "
        f"blocking question {req['blocking_questions'][0]['question']!r}",
    ]
    return _result("Tasks", ok, details, {"suggested_first": len(first), "suggested_second": len(second),
                                          "tasks_persisted": n_tasks, "events_persisted": n_events})


def check_agent() -> dict:
    """Readme 11 agent, with the offline FakeLLM: proposals vs escalations over the flagged fixture assets,
    capacity never proposed as occupancy, post-check ok, the no-evidence case escalates."""
    snap1 = _snap("synthetic_gavarres_0001")
    store = _store()
    store.apply_snapshot(snap1)
    scored = priority.rank_snapshot(snap1, overrides=store.overrides())
    store.suggest_tasks(scored["all"], snap1["snapshot_id"])
    wb = agent.Workbench.from_scored(scored, tasks=store)
    # one extra case with no evidence entry at all (name absent from fixtures/evidence.json and registers)
    nowhere = dict(wb.asset("fixture:can_xic"), asset_id="fixture:mas_nou", name="Mas Nou de Ningú",
                   capacity=None, estimated_occupancy=None, occupancy_basis=None, slack_min=None,
                   priority_status="needs_review", priority_rank=None, queue="needs_review",
                   review_reasons=["occupancy_unknown"], needs_review=True)
    wb.assets[nowhere["asset_id"]] = nowhere
    # the scripted FakeLLM handles the occupancy / class / location / exposure reasons; forecast_unavailable is a
    # producer gap and evacuation_unknown is the analyst's evacuation control or a contact_facility task, so assets
    # flagged only for those are left in the review queue rather than counted as unresolved investigations
    timing_reasons = ("forecast_unavailable", "evacuation_unknown")
    actionable = lambda a: [r for r in a.get("review_reasons") or [] if r not in timing_reasons]  # noqa: E731
    ids = [aid for aid in agent.flagged_asset_ids(wb) if actionable(wb.asset(aid))]
    skipped = [aid for aid in agent.flagged_asset_ids(wb) if not actionable(wb.asset(aid))]
    records = [agent.investigate(wb, aid) for aid in ids]   # llm=None -> FakeLLM
    modes = {r["llm_mode"] for r in records}
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    evidence_names = {agent._fold(e["name"]) for e in evidence}
    headcount_keys = {"headcount", "estimated_occupancy", "people_present"}
    evidence_has_headcount = any(k in e for e in evidence for k in headcount_keys)
    proposals = [p for r in records for p in r["proposals_added"]]
    questions = [q for r in records for q in r["questions_added"]]
    by_field = dict(Counter(p["field"] for p in proposals))
    occ_props = [p for p in proposals if p["field"] in agent.OCCUPANCY_FIELDS]
    occupancy_from_capacity = [p["proposal_id"] for p in occ_props if p["field"] == "estimated_occupancy"]
    capacity_ok = (not occupancy_from_capacity) or evidence_has_headcount
    postcheck_ok = all(r["postcheck_ok"] for r in records)
    steps_ok = all(r["steps"] <= 6 for r in records)
    per_asset = {r["asset_id"].split(":")[-1]: (r["review_reasons"], len(r["proposals_added"]), len(r["questions_added"]))
                 for r in records}
    # cases that must escalate: no evidence entry, seasonal only, unlocated
    must = {aid: (n_p, n_q) for aid, (_, n_p, n_q) in per_asset.items()
            if aid == "mas_nou" or actionable(wb.asset(f"fixture:{aid}")) == ["occupancy_seasonal"]}
    escalate_ok = must["mas_nou"] == (0, 1) and all(n_q >= 1 and n_p == 0 for n_p, n_q in must.values())
    resolved = {aid for aid, (_, n_p, n_q) in per_asset.items() if n_p + n_q == 0}
    # the guard itself: capacity evidence offered as estimated_occupancy is refused
    try:
        agent.propose_update("fixture:residencia_la_bisbal", "estimated_occupancy", 48, "manual enrichment",
                             "Centre públic de 48 places per a gent gran", "medium", workbench=wb)
        guard = "ACCEPTED (unexpected)"
    except agent.CapacityAsOccupancyError as e:
        guard = f"refused: {str(e)[:70]}..."
    guard_ok = guard.startswith("refused")
    # analyst confirmation persists the override and re-ranking (rank_snapshot) applies it; the asset leaves the
    # review queue only when its timing (forecast arrival + evacuation estimate) is known
    cap = next(p for p in proposals if p["asset_id"] == "fixture:residencia_la_bisbal" and p["field"] == "capacity")
    agent.confirm_proposal(wb, cap["proposal_id"])          # no rescore callback: re-ranks the workbench snapshot
    ovr = store.overrides("fixture:residencia_la_bisbal")
    rb = wb.asset("fixture:residencia_la_bisbal")
    timing_known = rb.get("fire_arrival_at") is not None and rb.get("evacuation_min") is not None
    confirm_ok = len(ovr) == 1 and ovr[0]["field"] == "capacity" and ovr[0]["value"] == cap["value"] and \
        rb["capacity"] == cap["value"] and rb["occupancy_basis"] == "analyst override" and \
        (rb["queue"] == "ranked") == timing_known
    # an analyst-entered evacuation duration is an override too and recalculates the window
    evac = agent.propose_update("fixture:residencia_la_bisbal", "evacuation_min", 150, "phone call with the director",
                                "a full evacuation takes about two and a half hours", "medium", workbench=wb)
    agent.confirm_proposal(wb, evac["proposal_id"])
    rb2 = wb.asset("fixture:residencia_la_bisbal")
    evac_ok = rb2["evacuation_min"] == 150.0 and rb2["evacuation_source"].startswith("analyst override") and \
        "evacuation_unknown" not in rb2["review_reasons"] and \
        (rb2["slack_min"] is not None) == (rb2.get("fire_arrival_at") is not None)
    data_regs = agent.DATA_REGISTERS_DIR.is_dir()
    ok = modes == {"fake"} and capacity_ok and postcheck_ok and steps_ok and escalate_ok and guard_ok and \
        confirm_ok and evac_ok and not resolved
    details = [
        f"LLM: FakeLLM (offline, scripted; llm_mode {sorted(modes)}). A live-model run is pending an ANTHROPIC_API_KEY; "
        f"scripts/investigate.py replays fixtures/agent/prerecorded_investigation.json until then",
        f"{len(records)} investigations over {len(ids) - 1} flagged fixture assets of synthetic_gavarres_0001 plus the "
        f"no-evidence case fixture:mas_nou; evidence cache {EVIDENCE.relative_to(ROOT)} ({len(evidence)} entries"
        f"{', plus data/registers/*.json present locally' if data_regs else ''}); {len(skipped)} assets flagged only "
        f"for {timing_reasons} left to the review queue (no FakeLLM script; forecast is the producer's, evacuation "
        f"duration is the analyst's evacuation control)",
        f"proposals {len(proposals)} by field {by_field}; escalations {len(questions)}; per asset "
        f"{{id: (reasons, proposals, questions)}}: {per_asset}",
        f"estimated_occupancy proposals: {len(occupancy_from_capacity)} (evidence carries a headcount field: "
        f"{evidence_has_headcount}); every occupancy proposal is field 'capacity': "
        f"{all(p['field'] == 'capacity' for p in occ_props)}",
        f"post-check ok for all: {postcheck_ok}; steps <= 6 for all: {steps_ok}; max steps {max(r['steps'] for r in records)}",
        f"must-escalate cases {{id: (proposals, questions)}}: {must}; assets left with neither proposal nor question: "
        f"{sorted(resolved) or 'none'}",
        f"capacity-as-occupancy guard: {guard}",
        f"confirmation of {cap['proposal_id']} (capacity {cap['value']}): overrides persisted {len(ovr)}, re-ranked queue "
        f"{rb['queue']}, occupancy_basis {rb['occupancy_basis']!r}, remaining window {rb['slack_min']}, timing known "
        f"{timing_known}" + ("" if timing_known else " (stays in review: the fixture carries no forecast arrival / "
                                                    "evacuation estimate for it yet)"),
        f"evacuation_min proposal {evac['proposal_id']} confirmed: evacuation {rb2['evacuation_min']} min from "
        f"{rb2['evacuation_source']!r}, review_reasons {rb2['review_reasons']}, window {rb2['slack_min']}: {evac_ok}",
        "held-out examples: no investigation examples were held back from prompt development; the FakeLLM is a "
        "script, so a held-out check is only meaningful on the live model and remains not verified",
    ]
    return _result("Agent (FakeLLM)", ok, details, {
        "investigations": len(records), "proposals": len(proposals), "by_field": by_field,
        "escalations": len(questions), "estimated_occupancy_proposals": len(occupancy_from_capacity)})


def check_agent_live() -> dict:
    return _result("Agent (live model)", False, [
        "not run: no ANTHROPIC_API_KEY in this environment; supported proposals, correct escalations and unsupported "
        "claims on held-out examples are unmeasured for the real model",
    ], outcome=NOT_VERIFIED)


def check_latency(repeats: int = 3) -> dict:
    """Readme 11 latency: processing time from receipt of the recorded seq-2 perimeter to snapshot built,
    ranked and suggestions queued for the 168 real-area assets; source age reported separately."""
    assets = _real_assets()
    updates = fire_input.load_recorded(RECORDED_FIRE)
    perimeters = [u for u in updates if u["geometry_kind"] == "perimeter"]
    first, second = perimeters[0], perimeters[-1]
    runs, stages = [], []
    snap = scored = None
    for _ in range(repeats):
        store = _store()
        # untimed: the previous state (seq 1 from the first recorded perimeter) so seq 2 is a real update
        s1 = snapshot.build_snapshot(assets, first, scenario_id="gavarres_recorded", incident_id=first["incident_id"],
                                     sequence=1, as_of=first["received_at"], input_mode="recorded")
        store.apply_snapshot(s1)
        store.suggest_tasks(priority.rank_snapshot(s1, overrides=store.overrides())["all"], s1["snapshot_id"])
        # timed: receipt -> snapshot built -> scored -> queue updated
        sw = fire_input.Stopwatch().start()
        snap = snapshot.build_snapshot(assets, second, scenario_id="gavarres_recorded",
                                       incident_id=second["incident_id"], sequence=2, as_of=second["received_at"],
                                       input_mode="recorded")
        errs = snapshot.validate_snapshot(snap)
        t_build = sw.elapsed
        scored = priority.rank_snapshot(snap, overrides=store.overrides())
        t_score = sw.elapsed
        applied = store.apply_snapshot(snap)
        suggested = store.suggest_tasks(scored["all"], snap["snapshot_id"])
        total = sw.stop()
        runs.append(total)
        stages.append({"build_s": t_build, "score_s": t_score - t_build, "queue_s": total - t_score,
                       "errors": len(errs), "changed": len(applied["changed"]), "suggested": len(suggested)})
        store.close()
    median = statistics.median(runs)
    age = fire_input.source_age_s(second["observed_at"], snap["as_of"])
    metrics = fire_input.measure_update(second, 0.0, median, now=snap["as_of"])
    met = median < LATENCY_TARGET_S and all(s["errors"] == 0 for s in stages)
    n_ranked, n_review = len(scored["ranked"]), len(scored["needs_review"])
    details = [
        f"input: {len(assets)} real-area assets ({REAL_ASSETS.relative_to(ROOT)}) + recorded update "
        f"{Path(second['raw_ref']).name} ({second['source']}, SYNTHETIC content, observed {second['observed_at']}, "
        f"received {second['received_at']}) through fire_input.load_recorded",
        f"processing time (receipt -> snapshot built -> scored -> tasks suggested), {repeats} runs: "
        f"{[round(r, 3) for r in runs]} s; median {median:.3f} s",
        f"stages of run 1: build {stages[0]['build_s']:.3f} s, score {stages[0]['score_s']:.3f} s, "
        f"apply+suggest {stages[0]['queue_s']:.3f} s; validate errors {stages[0]['errors']}",
        f"result: {n_ranked} ranked, {n_review} needs_review; {stages[0]['changed']} assets changed vs seq 1, "
        f"{stages[0]['suggested']} new suggestions on the update"
        + (" (ranked queue empty: no fire-spread run exists for the recorded July incident, so these inputs carry "
           "no per-location forecast arrival and every asset is a review item until a forecast covers it)"
           if n_ranked == 0 else ""),
        f"source age (observation -> snapshot as_of {snap['as_of']}): {age:.0f} s, data_status {snap['data_status']}; "
        f"metrics {metrics} (separate numbers, never combined)",
        f"target < {LATENCY_TARGET_S:.0f} s processing for the selected area: {'met' if met else 'NOT met'} "
        f"on {platform.machine()}, Python {platform.python_version()}",
    ]
    return _result("Latency", met, details, {
        "runs_s": runs, "median_s": median, "source_age_s": age, "assets": len(assets), "target_s": LATENCY_TARGET_S})


def check_stale() -> dict:
    """data_status boundaries from config.FRESHNESS through fire_input.data_status."""
    fresh = config.FRESHNESS
    now = "2026-07-03T12:00:00+00:00"
    from datetime import datetime, timedelta

    t0 = datetime.fromisoformat(now)
    cases = [(None, "unavailable"), (0, "current"), (fresh["stale_after_s"] - 1, "current"),
             (fresh["stale_after_s"], "stale"), (fresh["unavailable_after_s"] - 1, "stale"),
             (fresh["unavailable_after_s"], "unavailable"), (-60, "current")]
    got = []
    for age, expected in cases:
        observed = None if age is None else (t0 - timedelta(seconds=age)).isoformat()
        got.append((age, expected, fire_input.data_status(observed, now)))
    agree = all(e == g for _, e, g in got)
    same_as_producer = all(snapshot.freshness(None if a is None else (t0 - timedelta(seconds=a)).isoformat(), now) == g
                           for a, _, g in got)
    details = [
        f"config.FRESHNESS {fresh}",
        "age s -> status: " + ", ".join(f"{a} -> {g}" for a, _, g in got),
        f"all boundaries as configured: {agree}; snapshot.freshness (producer) agrees with fire_input.data_status "
        f"(consumer): {same_as_producer}",
    ]
    return _result("Stale data", agree and same_as_producer, details, {"cases": [(a, g) for a, _, g in got]})


CHECKS = [check_coverage, check_geometry, check_priority, check_updates, check_tasks, check_agent, check_agent_live,
          check_latency, check_stale]

NOT_VERIFIED_ITEMS = [
    "Live Deepfire authentication and a real response: no credentials were available; fixtures/fire/deepfire is "
    "synthetic content in the documented response shape (fixtures/fire/deepfire/README.md).",
    "Live LLM investigation: no ANTHROPIC_API_KEY; the agent check ran the scripted FakeLLM and the prerecorded "
    "fixture is a labelled FakeLLM transcript. Supported proposals, correct escalations and unsupported claims on "
    "held-out examples are unmeasured for the real model.",
    "Forecast accuracy and evacuation estimates: no provider forecast was validated; the ranking checks use "
    "constructed synthetic arrivals and policy evacuation durations, and distance-based exposure is not time "
    "to impact.",
    "Evacuation decisions: nothing here validates an evacuation or confinement decision, lead time against "
    "historical response, or superiority over historical emergency response.",
    "Operational validity of the contact policy: the priority checks verify the window arithmetic, ordering, "
    "ties and missing-input handling of the implementation, not that forecast-evacuation-window-v2 with the "
    "prototype evacuation durations (evacuation-proto-2026-09-19) orders contacts correctly.",
    "Historical as-of replay: the recorded-input run is a recorded-input demo with synthetic coverage; input "
    "availability times were not established for all inputs.",
]


# ------------------------------------------------------------------------------------------ run / report
def run_checks(checks=CHECKS) -> list[dict]:
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as exc:  # a crashing check is a failed check, recorded as such
            tb = traceback.format_exc().strip().splitlines()[-1]
            results.append(_result(fn.__name__.replace("check_", "").replace("_", " ").title(), False,
                                   [f"check raised {type(exc).__name__}: {exc}", tb]))
    return results


def _measured_summary(r: dict) -> str:
    m = r["measured"]
    n = r["name"]
    if n == "Coverage/matching":
        return f"{m['total']} facilities ({m['located']} located, {m['unlocated']} unlocated), {m['by_type']}"
    if n == "Geometry":
        return f"overlap {m['overlap_distance_m']} m; separated {m['separated_distance_m']} m (error {m['separated_error_m']:.2f} m)"
    if n == "Priority":
        return (f"window {m['window_single']} = expected {m['expected_single']} min; order {m['order']}; exhausted first "
                f"{m['exhausted_order']}; fixture {m['fixture_ranked']} ranked / {m['fixture_review']} review")
    if n == "Updates":
        return f"{m['changed_assets_seq2']} assets changed on seq 2; missing {m['missing_asset_ids']}, {m['tasks_kept']} tasks kept"
    if n == "Tasks":
        return f"suggestions {m['suggested_first']} then {m['suggested_second']}; {m['tasks_persisted']} tasks survive reload"
    if n == "Agent (FakeLLM)":
        return f"{m['investigations']} runs: {m['proposals']} proposals {m['by_field']}, {m['escalations']} escalations, {m['estimated_occupancy_proposals']} occupancy-from-capacity"
    if n == "Latency":
        return f"median {m['median_s']:.3f} s for {m['assets']} assets (target < {m['target_s']:.0f} s); source age {m['source_age_s']:.0f} s"
    if n == "Stale data":
        return ", ".join(f"{a}->{g}" for a, g in m["cases"])
    return ""


def print_table(results: list[dict], verbose: bool = True) -> None:
    width = max(len(r["name"]) for r in results)
    print(f"FireLine v4 validation ({VALIDATION_DATE}, offline)")
    print(f"{'check':<{width}}  {'outcome':<13} measured")
    print("-" * (width + 80))
    for r in results:
        print(f"{r['name']:<{width}}  {r['outcome']:<13} {_measured_summary(r)}")
    if verbose:
        for r in results:
            print(f"\n## {r['name']}: {r['outcome']}")
            for d in r["details"]:
                print(f"- {d}")
    counts = Counter(r["outcome"] for r in results)
    print(f"\n{counts[PASS]} pass, {counts[FAIL]} fail, {counts[NOT_VERIFIED]} not verified")


def render_markdown(results: list[dict]) -> str:
    lines = [
        "# FireLine v4 MVP validation (readme.md section 11)",
        "",
        f"Validation date: {VALIDATION_DATE}. Written by `scripts/validate.py --write`; rerun it to refresh. "
        "Every check ran offline on the committed fixtures (no network, no Deepfire credentials, no LLM key). "
        "Outcomes and numbers below are what the script measured on that run; nothing here is a claim beyond "
        "those measurements. Fixture content is synthetic except the Gencat register extract in "
        "`fixtures/real_area/`.",
        "",
        "## Summary",
        "",
        "| check | outcome | measured |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['name']} | {r['outcome']} | {_measured_summary(r)} |")
    counts = Counter(r["outcome"] for r in results)
    lines += ["", f"{counts[PASS]} pass, {counts[FAIL]} fail, {counts[NOT_VERIFIED]} not verified.", "",
              "## Recorded outcomes", ""]
    for r in results:
        lines.append(f"### {r['name']}: {r['outcome']}")
        lines.append("")
        lines += [f"- {d}" for d in r["details"]]
        lines.append("")
    lines += ["## Not verified", ""]
    lines += [f"- {item}" for item in NOT_VERIFIED_ITEMS]
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help=f"write {OUTPUT.name} next to readme.md")
    ap.add_argument("--quiet", action="store_true", help="table only, no per-check details")
    args = ap.parse_args(argv)
    results = run_checks()
    print_table(results, verbose=not args.quiet)
    if args.write:
        OUTPUT.write_text(render_markdown(results), encoding="utf-8")
        shown = OUTPUT.relative_to(ROOT) if OUTPUT.is_relative_to(ROOT) else OUTPUT
        print(f"\nwrote {shown}")
    return 0 if all(r["outcome"] != FAIL for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
