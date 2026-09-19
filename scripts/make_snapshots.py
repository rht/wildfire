#!/usr/bin/env python
"""Build the committed v4 snapshots and the real-area asset extract (no network, no raster).

    .venv/bin/python scripts/make_snapshots.py

Writes
  fixtures/snapshots/synthetic_gavarres_0001.json   12 fixture assets + 1 unlocated care home, fire at 08:00Z
  fixtures/snapshots/synthetic_gavarres_0002.json   same assets, fire grown ~1.7 km downwind (SSE), 10:00Z
  fixtures/real_area/assets_gavarres.json           every data/assets_in.json row inside GAVARRES_BBOX plus
                                                    unlocated care homes / campsites of the same municipalities
  fixtures/real_area/README.md                      counts and limitations
  fixtures/snapshots/gavarres_real_0001..0003.json  real facilities + three REAL recorded Deepfire satellite
                                                    perimeters (fixtures/fire/deepfire/real/, incident 5769dcea)

The real-area extract needs data/assets_in.json and data/unlocated.json (from `scripts/fetch_data.py
registers`, gitignored); when they are absent only the synthetic snapshots are rebuilt.
"""

from __future__ import annotations

import json
import math
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fireline import config  # noqa: E402
from fireline.fire_state import FireState  # noqa: E402
from fireline.grid import xy_to_lonlat  # noqa: E402
from fireline.snapshot import asset_record, build_snapshot, validate_snapshot, write_snapshot  # noqa: E402

FIX = ROOT / "fixtures"
DATA = ROOT / "data"
SNAP_DIR = FIX / "snapshots"
REAL_DIR = FIX / "real_area"

# Same as feeds.GAVARRES_BBOX (lon_min, lat_min, lon_max, lat_max); inline so this script never imports feeds.
GAVARRES_BBOX = (2.85, 41.80, 3.20, 42.05)
EXTRACTION_DATE = "2026-09-19"
# fetched_at of the register pulls behind data/assets_in.json (data/README.md, cache entries of 2026-09-19).
REGISTER_FETCHED_AT = {
    "equipaments": "2026-09-19T11:46:36+00:00",
    "schools": "2026-09-19T11:46:37+00:00",
    "care_homes": "2026-09-19T11:46:36+00:00",
    "campsites": "2026-09-19T11:46:37+00:00",
}
REGISTER_LABEL = {
    "equipaments": "gencat:equipaments (8gmd-gz7i)",
    "schools": "gencat:schools (kvmv-ahh4)",
    "care_homes": "gencat:care_homes (ivft-vegh)",
    "campsites": "gencat:campsites (t2h3-cgys)",
}

# Synthetic care home without coordinates, added to the snapshot fixtures only (readme 11: missing geometry case).
UNLOCATED_FIXTURE = {
    "asset_id": "fixture:residencia_sense_coordenades",
    "name": "Residència sense coordenades",
    "asset_class": "care_home",
    "lon": None,
    "lat": None,
    "municipality": "Cruïlles, Monells i Sant Sadurní de l'Heura",
    "occupancy": 40,
    "occupancy_source": "register",
    "note": "synthetic care-home register row with no coordinates (the care-home register carries none)",
}

T1 = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
COMPUTE_LAG_S = 20   # fixed so the fixtures are deterministic; a real run measures it


# ----------------------------------------------------------------------------------------- fire
def perimeter_geojson(perimeter) -> dict:
    polys = list(perimeter.geoms) if perimeter.geom_type == "MultiPolygon" else [perimeter]
    rings = []
    for p in polys:
        xs, ys = zip(*p.exterior.coords)
        lon, lat = xy_to_lonlat(np.array(xs), np.array(ys))
        rings.append([[round(float(a), 6), round(float(b), 6)] for a, b in zip(lon, lat)])
    if len(rings) == 1:
        return {"type": "Polygon", "coordinates": rings}
    return {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}


def fire_update(fs: FireState, perimeter, t: datetime, label: str) -> dict:
    """FireUpdate-shaped dict (CONTRACTS 3) for a synthetic perimeter; never imports fire_input."""
    return {
        "provider": "fixture",
        "incident_id": fs.cluster_id,
        "observed_at": t.isoformat(),
        "received_at": t.isoformat(),
        "geometry": perimeter_geojson(perimeter),
        "geometry_kind": "perimeter",
        "source": f"fixture:synthetic_ignition ({label})",
        "raw_ref": "fixtures/synthetic_ignition.json",
    }


def grown_perimeter(fs: FireState, advance_m: float = 1500.0, radius_m: float = 1000.0, bearing_deg: float | None = None):
    """Sequence-2 perimeter: the 08:00 disc plus a disc `advance_m` downwind (convex hull, simplified).
    Downwind = the direction the wind blows TO (wind_dir_deg is the meteorological FROM direction)."""
    if bearing_deg is None:
        bearing_deg = (fs.wind_dir_deg + 180.0) % 360.0
    cx, cy = fs.perimeter.centroid.x, fs.perimeter.centroid.y
    dx = advance_m * math.sin(math.radians(bearing_deg))
    dy = advance_m * math.cos(math.radians(bearing_deg))
    head = Point(cx + dx, cy + dy).buffer(radius_m)
    return unary_union([fs.perimeter, head]).convex_hull.simplify(25.0)


# --------------------------------------------------------------------------------- real area
def _fold(s: str | None) -> str:
    """Municipality key: accent- and case-folded, inverted article ('Bisbal d'Empordà, la') normalised."""
    if not s:
        return ""
    s = s.strip()
    if ", " in s:
        head, _, article = s.rpartition(", ")
        if article.lower() in ("el", "la", "els", "les", "l'", "es", "sa"):
            s = f"{article} {head}"
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().replace("’", "'").split())


def in_bbox(row: dict, bbox=GAVARRES_BBOX) -> bool:
    lon, lat = row.get("lon"), row.get("lat")
    if lon is None or lat is None:
        return False
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def real_area_rows() -> tuple[list[dict], list[dict], set[str]] | None:
    """(located rows in bbox, unlocated rows of those municipalities, municipality keys) or None without data/."""
    a_path, u_path = DATA / "assets_in.json", DATA / "unlocated.json"
    if not (a_path.exists() and u_path.exists()):
        return None
    located = [r for r in json.loads(a_path.read_text()) if in_bbox(r)]
    munis = {_fold(r.get("municipality")) for r in located} - {""}
    unlocated = [r for r in json.loads(u_path.read_text())
                 if r.get("lon") is None and _fold(r.get("municipality")) in munis]
    located.sort(key=lambda r: r["asset_id"])
    unlocated.sort(key=lambda r: r["asset_id"])
    return located, unlocated, munis


def real_asset_record(row: dict) -> dict:
    """v4 asset record for one register row: asset-level fields only, provenance with fetched_at."""
    reg = row.get("register") or row["asset_id"].split(":")[0]
    prepared = dict(row, register=REGISTER_LABEL.get(reg, reg), fetched_at=REGISTER_FETCHED_AT.get(reg))
    prepared["note"] = f"register category: {row.get('category')}" if row.get("category") else None
    return asset_record(prepared, config)


def write_real_area(located, unlocated) -> list[dict]:
    REAL_DIR.mkdir(parents=True, exist_ok=True)
    records = [real_asset_record(r) for r in located] + [real_asset_record(r) for r in unlocated]
    payload = {
        "area": "Gavarres",
        "bbox": list(GAVARRES_BBOX),
        "extracted_on": EXTRACTION_DATE,
        "registers": REGISTER_LABEL,
        "counts": {"located": len(located), "unlocated": len(unlocated), "total": len(records)},
        "assets": records,
    }
    (REAL_DIR / "assets_gavarres.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")

    def count(rows, key):
        out = {}
        for r in rows:
            out[r[key]] = out.get(r[key], 0) + 1
        return dict(sorted(out.items()))

    loc_by_class, unl_by_class = count(located, "asset_class"), count(unlocated, "asset_class")
    loc_by_reg, unl_by_reg = count(located, "register"), count(unlocated, "register")
    by_key = {}
    for r in located:   # one display name per folded municipality (the register spells Palamós two ways)
        by_key.setdefault(_fold(r["municipality"]), r["municipality"])
    muni_list = sorted(by_key.values(), key=_fold)
    lines = [
        "# Real-area facility extract: Gavarres",
        "",
        f"Gencat register extract of {EXTRACTION_DATE} (`scripts/fetch_data.py registers`, cached in `data/`), filtered",
        f"to the fixed area `GAVARRES_BBOX = {GAVARRES_BBOX}` (lon_min, lat_min, lon_max, lat_max) and written",
        "in the CONTRACTS.md 2.4 asset-record shape by `scripts/make_snapshots.py`. Only asset-level fields are kept;",
        "the raw register rows stay in `data/registers/`.",
        "",
        "## Counts",
        "",
        "| set | rows | by class | by register |",
        "|---|---|---|---|",
        f"| located (coordinates inside the bbox) | {len(located)} | {loc_by_class} | {loc_by_reg} |",
        f"| unlocated (no coordinates, municipality inside the area) | {len(unlocated)} | {unl_by_class} | {unl_by_reg} |",
        f"| total | {len(located) + len(unlocated)} | | |",
        "",
        f"Municipalities with located rows ({len(muni_list)}): " + ", ".join(muni_list) + ".",
        "",
        "## Limitations",
        "",
        "- The care-home (`ivft-vegh`) and campsite (`t2h3-cgys`) registers carry **no coordinates**; those rows are",
        "  included with null `latitude`/`longitude` and the `location_unknown` review reason. They are matched to the",
        "  area by municipality name (accent/case folded, inverted article normalised), not by geometry, so a",
        "  municipality that straddles the bbox contributes all of its unlocated rows. The schools register truncates",
        "  long names (`Cruïlles, Monells i Sant Sadurní de l'He`), so unlocated rows of that municipality would not",
        "  match; none exist in the 2026-09-19 extract.",
        "- Located rows are almost all schools (`kvmv-ahh4`, school year 2025/2026); Equipaments contributes the",
        "  hospital and sociosanitari rows only (`class_ambiguous` for the latter). No located row has a register",
        "  capacity, so every located asset carries `occupancy_unknown`; unlocated care homes and campsites carry the",
        "  register capacity (`capacity`, not `estimated_occupancy`).",
        "- No footprints: every distance in a snapshot built from this file is a labelled point fallback.",
        "- `fixtures/snapshots/gavarres_real_0001..0003.json` combine **these real facilities with three real",
        "  recorded Deepfire satellite perimeters** (`fixtures/fire/deepfire/real/`, `input_mode: recorded`);",
        "  the register extract (2026-09-19) postdates the fire (July 2026), so this is a recorded-input demo,",
        "  not historical as-of replay (readme section 4).",
        "",
    ]
    (REAL_DIR / "README.md").write_text("\n".join(lines))
    return records


# ------------------------------------------------------------------------------------ pipeline
def synthetic_assets() -> list[dict]:
    rows = json.loads((FIX / "assets.json").read_text())
    for r in rows:
        r.setdefault("register", "fixture")
    return rows + [dict(UNLOCATED_FIXTURE, register="fixture")]


def build_pair(assets, fires, scenario_id: str, incident_id: str, input_mode: str = "synthetic") -> list[dict]:
    snaps = []
    for seq, (fire, t) in enumerate(fires, start=1):
        observed = datetime.fromisoformat(fire["observed_at"]) if fire.get("observed_at") else None
        age = (t - observed).total_seconds() if observed else None
        snap = build_snapshot(assets, fire, scenario_id=scenario_id, incident_id=incident_id, sequence=seq,
                              as_of=t, input_mode=input_mode,
                              computed_at=t + timedelta(seconds=COMPUTE_LAG_S),
                              metrics={"source_age_s": age, "processing_s": float(COMPUTE_LAG_S)})
        errs = validate_snapshot(snap)
        if errs:
            raise SystemExit(f"{snap['snapshot_id']} invalid: {errs}")
        path = write_snapshot(snap, SNAP_DIR / f"{snap['snapshot_id'].replace('-', '_', 1)}.json")
        near = sorted((a for a in snap["assets"] if a["distance_to_fire_m"] is not None),
                      key=lambda a: (a["distance_to_fire_m"], a["asset_id"]))[:3]
        print(f"{path.relative_to(ROOT)}: {len(snap['assets'])} assets, data_status {snap['data_status']}, "
              f"{sum(a['intersects_fire'] is True for a in snap['assets'])} intersecting, "
              f"{sum(a['needs_review'] for a in snap['assets'])} need review, "
              f"{path.stat().st_size / 1e6:.2f} MB; nearest: "
              + ", ".join(f"{a['name']} {a['distance_to_fire_m']:.0f} m" for a in near))
        snaps.append(snap)
    return snaps


def main() -> int:
    fs = FireState.from_json(FIX / "synthetic_ignition.json")
    fires = [
        (fire_update(fs, fs.perimeter, T1, "08:00 ignition disc"), T1),
        (fire_update(fs, grown_perimeter(fs), T2, "10:00 perimeter grown ~1.5 km downwind, synthetic"), T2),
    ]
    build_pair(synthetic_assets(), fires, "synthetic_gavarres", fs.cluster_id)

    real = real_area_rows()
    if real is None:
        print("data/assets_in.json or data/unlocated.json missing: real-area extract not rebuilt")
        return 0
    located, unlocated, munis = real
    records = write_real_area(located, unlocated)
    print(f"fixtures/real_area/assets_gavarres.json: {len(located)} located + {len(unlocated)} unlocated "
          f"in {len(munis)} municipalities")
    real_fires = recorded_real_fires()
    if real_fires is None:
        print("fixtures/fire/deepfire/real/ has no satellite-perimeters response: real snapshots not rebuilt")
        return 0
    build_pair(records, real_fires, "gavarres_real", real_fires[0][0]["incident_id"], input_mode="recorded")
    return 0


# --------------------------------------------------------------------------------- real fire
REAL_FIRE_DIR = FIX / "fire" / "deepfire" / "real"
REAL_PERIMETER_PICKS = (0, 3, -1)   # first, middle and last perimeter by observation time


def recorded_real_fires() -> list[tuple[dict, datetime]] | None:
    """(FireUpdate, as_of) per chosen perimeter of the real recorded satellite-perimeters response.

    The raw response (one FeatureCollection with every perimeter of the incident) is split into one
    single-feature body per perimeter and each goes through `fire_input.parse_deepfire`, the same path a
    live poll uses. `as_of` = the provider's `computed_at` (when that perimeter became available);
    `observed_at` = `observed_watermark`."""
    from fireline import fire_input

    files = sorted(REAL_FIRE_DIR.glob("*satellite-perimeters.json"))
    if not files:
        return None
    rec = json.loads(files[-1].read_text())
    feats = rec["body"]["features"]
    updates = []
    for feat in feats:
        computed = datetime.fromisoformat(feat["properties"]["computed_at"].replace("Z", "+00:00"))
        upd = fire_input.parse_deepfire({"type": "FeatureCollection", "features": [feat]}, computed,
                                        "satellite-perimeters", raw_ref=str(files[-1].relative_to(ROOT)))
        if upd is not None:
            updates.append((upd, computed))
    updates.sort(key=lambda u: u[1])
    return [updates[i] for i in REAL_PERIMETER_PICKS]


if __name__ == "__main__":
    sys.exit(main())
