#!/usr/bin/env python
"""Fetch real data into data/ (network). Subcommands: registers, wind, deepfire, notability.

    .venv/bin/python scripts/fetch_data.py registers
    .venv/bin/python scripts/fetch_data.py wind
    .venv/bin/python scripts/fetch_data.py deepfire
    .venv/bin/python scripts/fetch_data.py notability

Everything goes through fireline.feeds' file cache, so a second run is offline-safe.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fireline import env, feeds, fire_input, notability  # noqa: E402

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
    # The schools directory carries no enrolment; pupils come from xvme-26kg and join on codi_centre.
    enrolment = feeds.schools_enrolment(comarques=COMARQUES)
    _dump(out / "schools_enrolment.json", enrolment)
    print(f"schools_enrolment: {len(enrolment)} centres -> {out / 'schools_enrolment.json'}")
    assets, unlocated = feeds.registers_to_assets(**raw, schools_enrolment=enrolment)
    _dump(feeds.DATA_DIR / "assets_in.json", assets)
    _dump(feeds.DATA_DIR / "unlocated.json", unlocated)
    by_class = {}
    for a in assets + unlocated:
        by_class.setdefault(a["asset_class"], [0, 0])
        by_class[a["asset_class"]][0 if a["lon"] is not None else 1] += 1
    print(f"assets_in: {len(assets)} located, unlocated: {len(unlocated)}")
    for cls, (loc, unloc) in sorted(by_class.items()):
        print(f"  {cls}: {loc} located, {unloc} unlocated")
    schools_rows = [a for a in assets + unlocated if a["register"] == "schools"]
    with_pupils = [a for a in schools_rows if a["occupancy_source"] == "enrolment"]
    print(f"  school enrolment: {len(with_pupils)}/{len(schools_rows)} schools matched, "
          f"{sum(a['occupancy'] for a in with_pupils)} pupils; "
          f"{len(schools_rows) - len(with_pupils)} left occupancy_unknown")
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


# Named institutions the criticality layer needs in the corpus before their register classes land
# (fireline/feeds.py ASSET_CLASS_RULES is gaining research/fire-station/aerodrome rows). Barcelona
# Supercomputing Center sits outside the Gavarres box and is here as the worked example of a site
# whose importance is nothing to do with its building.
EXTRA_NOTABILITY_QUERIES = [
    "IRTA", "IRTA Monells", "Institut Català d'Oncologia", "Centre d'Estudis Avançats de Blanes",
    "Institut Català de Recerca de l'Aigua", "IDIBGI", "VICOROB", "Barcelona Supercomputing Center",
    "Aeroport de Girona-Costa Brava", "Bombers de la Generalitat de Catalunya",
    "Parc de Bombers", "Heliport de Costa Brava Centre",
]


def _load_assets() -> list[dict]:
    rows: list[dict] = []
    for name in ("assets_in.json", "unlocated.json"):
        path = feeds.DATA_DIR / name
        if not path.exists():
            print(f"note: {path} missing, skipped")
            continue
        with open(path, encoding="utf-8") as f:
            rows.extend(json.load(f))
    return rows


def cmd_notability(args) -> int:
    """Wikipedia + Wikidata evidence for named institutions -> fixtures/notability.json."""
    assets = _load_assets()
    extra = [q.strip() for q in (args.queries or "").split(",") if q.strip()] or list(EXTRA_NOTABILITY_QUERIES)
    # Explicit names first, so --limit exercises them rather than the top of the register alphabet.
    queries, seen = [], set()
    for query in extra + notability.asset_queries(assets):
        if notability.fold(query) not in seen:
            seen.add(notability.fold(query))
            queries.append(query)
    skipped = sum(1 for a in assets if notability.is_generic_name(a.get("name") or ""))
    print(f"assets: {len(assets)} rows, {skipped} skipped by the generic-name cost filter, "
          f"{len(queries)} queries to try ({len(extra)} named explicitly)")
    if args.limit:
        queries = queries[:args.limit]
        print(f"--limit {args.limit}: trying {len(queries)}")
    try:
        records = notability.fetch_notability(queries, cached_only=args.cached_only)
    except feeds.FeedError as exc:
        # Wikimedia throttles anonymous callers per IP. Writing the corpus now would record
        # "no article" for everything the rate limiter refused, so stop and keep the old file.
        print(f"notability: aborted, {exc}")
        print("  fixtures/notability.json left untouched; re-run to resume from the cache")
        return 1
    with_wikidata = sum(1 for r in records if r["wikidata_id"])
    if records and not with_wikidata:
        # The institution filter keeps an untyped record, so a run whose entity pass failed does
        # not error: it quietly resolves MORE queries and writes a larger, unfiltered corpus with
        # the junk back in. Overwriting a good corpus with that is worse than doing nothing. Seen
        # with --cached-only when the cache holds the article batches but not the entity batches.
        print(f"notability: aborted, {len(records)} records and not one Wikidata entity between "
              f"them; the entity pass failed, so the institution filter never ran")
        print("  fixtures/notability.json left untouched; re-run without --cached-only")
        return 1
    path = notability.write_corpus(records)
    print(f"notability: {len(records)}/{len(queries)} queries resolved to an article, "
          f"{with_wikidata} with a Wikidata entity -> {path}")
    for rec in sorted(records, key=lambda r: r["query"])[:10]:
        print(f"  {rec['query']!r} -> {rec['title']} [{rec['lang']}] {rec['wikidata_id']} "
              f"{rec['instance_of']}: {rec['summary'][:90]}...")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("registers", help="Gencat registers for Baix Empordà, Gironès, Selva -> data/registers, assets_in.json")
    sub.add_parser("wind", help="Open-Meteo previous runs 2026-07-03..05 on a 0.1 deg grid -> data/wind")
    sub.add_parser("deepfire", help="Deepfire clusters/hotspots/perimeters for the Gavarres, 2026-07-03..05")
    nota = sub.add_parser("notability", help="Wikipedia/Wikidata notability corpus -> fixtures/notability.json")
    nota.add_argument("--cached-only", action="store_true", help="no network: use only what is already cached")
    nota.add_argument("--limit", type=int, default=0, help="try at most N queries (0 = all)")
    nota.add_argument("--queries", default="", help="comma-separated names to query instead of the built-in extras")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return {"registers": cmd_registers, "wind": cmd_wind, "deepfire": cmd_deepfire,
            "notability": cmd_notability}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
