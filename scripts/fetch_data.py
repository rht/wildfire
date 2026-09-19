#!/usr/bin/env python
"""Fetch real data into data/ (network). Subcommands: registers, wind, deepfire.

    .venv/bin/python scripts/fetch_data.py registers
    .venv/bin/python scripts/fetch_data.py wind
    .venv/bin/python scripts/fetch_data.py deepfire

Everything goes through fireline.feeds' file cache, so a second run is offline-safe.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fireline import env, feeds, fire_input  # noqa: E402

COMARQUES = ["Baix Empordà", "Gironès", "Selva"]
REPLAY_START = date(2026, 7, 3)
REPLAY_END = date(2026, 7, 5)
WIND_STEP_DEG = 0.1


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)


def cmd_registers(args) -> int:
    out = feeds.DATA_DIR / "registers"
    raw = {
        "equipaments": feeds.equipaments(comarques=COMARQUES),
        "care_homes": feeds.care_homes(comarques=COMARQUES),
        "campsites": feeds.campsites(comarques=COMARQUES),
        "schools": feeds.schools(comarques=COMARQUES),
    }
    for name, rows in raw.items():
        _dump(out / f"{name}.json", rows)
        print(f"{name}: {len(rows)} rows -> {out / (name + '.json')}")
    assets, unlocated = feeds.registers_to_assets(**raw)
    _dump(feeds.DATA_DIR / "assets_in.json", assets)
    _dump(feeds.DATA_DIR / "unlocated.json", unlocated)
    by_class = {}
    for a in assets + unlocated:
        by_class.setdefault(a["asset_class"], [0, 0])
        by_class[a["asset_class"]][0 if a["lon"] is not None else 1] += 1
    print(f"assets_in: {len(assets)} located, unlocated: {len(unlocated)}")
    for cls, (loc, unloc) in sorted(by_class.items()):
        print(f"  {cls}: {loc} located, {unloc} unlocated")
    return 0


def _grid(bbox, step):
    w, s, e, n = bbox
    lons = [round(w + i * step, 2) for i in range(int((e - w) / step + 1e-9) + 1)]
    lats = [round(s + i * step, 2) for i in range(int((n - s) / step + 1e-9) + 1)]
    return [(lat, lon) for lat in lats for lon in lons]


def cmd_wind(args) -> int:
    out = feeds.DATA_DIR / "wind"
    points = _grid(feeds.GAVARRES_BBOX, WIND_STEP_DEG)
    n_ok = n_null = 0
    for lat, lon in points:
        series = feeds.open_meteo_series(lat, lon, REPLAY_START, REPLAY_END, replay=True)
        nulls = sum(v is None for v in series["wind_speed_10m"])
        n_null += nulls
        n_ok += len(series["time"]) - nulls
        _dump(out / f"{lat:.2f}_{lon:.2f}.json", {"lat": lat, "lon": lon, "model": feeds.OPEN_METEO_REPLAY_MODEL,
                                                  "slice": "previous_day1", **series})
    print(f"wind: {len(points)} grid points x {REPLAY_START}..{REPLAY_END}, "
          f"{n_ok} hourly wind values, {n_null} null -> {out}")
    check = feeds.open_meteo_wind(41.95, 3.03, datetime(2026, 7, 3, 12, tzinfo=timezone.utc), replay=True)
    print(f"check 2026-07-03 12:00Z @41.95,3.03 previous_day1: {check}")
    return 0


def cmd_deepfire(args) -> int:
    creds = env.has_deepfire_credentials()   # loads .env first
    as_of = datetime.combine(REPLAY_END + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    since = datetime.combine(REPLAY_START, datetime.min.time(), tzinfo=timezone.utc)
    bbox = feeds.GAVARRES_BBOX
    plan = [
        f"GET {feeds.DeepfireClient.ITEMS.format(collection='clusters')} bbox={bbox} "
        f"filter=last_observed <= {as_of.isoformat()}",
        f"GET {feeds.DeepfireClient.ITEMS.format(collection='hotspots')} bbox={bbox} "
        f"filter=observed_at <= {as_of.isoformat()} AND observed_at >= {since.isoformat()}",
        f"GET {feeds.DeepfireClient.ITEMS.format(collection='satellite-perimeters')} bbox={bbox} "
        f"filter=computed_at <= {as_of.isoformat()}",
    ]
    if not creds:
        print("no DEEPFIRE_TOKEN / DEEPFIRE_CLIENT_ID+SECRET in the environment or .env; would run:")
        for p in plan:
            print("  " + p)
        print(f"  and write data/deepfire/{{clusters,hotspots,perimeters}}.json")
        print("  and record each raw response as {collection, received_at, body} in "
              "data/deepfire/recorded/ (fire_input.load_recorded shape)")
        return 0
    client = feeds.DeepfireClient(max_age_s=None)
    out = feeds.DATA_DIR / "deepfire"
    recorded = out / "recorded"
    results = {}
    for collection, fetch in (("clusters", lambda: client.clusters(bbox, as_of)),
                              ("hotspots", lambda: client.hotspots(bbox, as_of, since=since)),
                              ("satellite-perimeters", lambda: client.satellite_perimeters(bbox, as_of))):
        features = fetch()
        received_at = datetime.now(timezone.utc)
        results[collection] = features
        body = {"type": "FeatureCollection", "features": features,
                "numberReturned": len(features), "bbox": list(bbox), "as_of": as_of.isoformat()}
        path = fire_input.record_response(collection, body, received_at, recorded)
        print(f"recorded {collection}: {len(features)} features -> {path}")
    clusters, hotspots, perimeters = results["clusters"], results["hotspots"], results["satellite-perimeters"]
    _dump(out / "clusters.json", clusters)
    _dump(out / "hotspots.json", hotspots)
    _dump(out / "hotspot_records.json", feeds.hotspots_to_records(hotspots))
    _dump(out / "perimeters.json", perimeters)
    print(f"deepfire: {len(clusters)} clusters, {len(hotspots)} hotspots, {len(perimeters)} perimeters -> {out}")
    for update in fire_input.load_recorded(recorded):
        print(f"  update {update['source']} {update['geometry_kind']} observed_at={update['observed_at']} "
              f"incident={update['incident_id']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("registers", help="Gencat registers for Baix Empordà, Gironès, Selva -> data/registers, assets_in.json")
    sub.add_parser("wind", help="Open-Meteo previous runs 2026-07-03..05 on a 0.1 deg grid -> data/wind")
    sub.add_parser("deepfire", help="Deepfire clusters/hotspots/perimeters for the Gavarres, 2026-07-03..05")
    args = ap.parse_args(argv)
    return {"registers": cmd_registers, "wind": cmd_wind, "deepfire": cmd_deepfire}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
