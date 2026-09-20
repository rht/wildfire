#!/usr/bin/env python
"""Build the recorded-mode incident-server fixtures for the REAL Gavarres fire (no network, committed inputs only).

    .venv/bin/python scripts/make_gavarres_trigger.py

Writes fixtures/incidents/gavarres_real/
  settings.json        `incident_server serve --settings`: input_mode recorded, calls disabled, no LLM,
                       catalog_file resolved relative to this file
  catalog.json         facility rows for `fire_assessment.assess_fire(facility_rows=...)`: the union of the
                       `assets` of fixtures/snapshots/gavarres_real_0001..0003.json (real Gencat register rows,
                       extract 2026-09-19), one row per asset_id, sorted, with every per-snapshot derived field
                       (distance, forecast, arrival, evacuation, value at risk, review flags) stripped so the
                       runtime recomputes them for each trigger
  trigger-0001..3.json one `POST /api/fire` body per recorded satellite perimeter, the same picks and order as
                       scripts/make_snapshots.py (first, middle, last by provider `computed_at`): `as_of` = that
                       `computed_at`, `fire` = the `fire_input.parse_deepfire` FireUpdate of that perimeter

The recorded Deepfire fire-spread run (fixtures/fire/deepfire/real/*fire-spread-simulation*) is NOT attached:
it was run on 2026-09-19 and `assess_fire` rejects a forecast issued after the trigger's July `as_of`.
No forecast is fabricated; every asset stays `forecast_unavailable`.

Deterministic: same inputs -> byte-identical files. Never reads the gitignored data/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.make_snapshots import recorded_real_fires  # noqa: E402

FIX = ROOT / "fixtures"
OUT_DIR = FIX / "incidents" / "gavarres_real"
SNAPSHOT_FILES = tuple(FIX / "snapshots" / f"gavarres_real_{n:04d}.json" for n in (1, 2, 3))
SCENARIO_ID = "gavarres_real"

# Static asset fields (CONTRACTS.md 2.4 identity / location / occupancy / provenance). Everything else in a
# snapshot asset row is derived per snapshot by assess_fire -> build_snapshot and must not be supplied.
STATIC_FIELDS = ("asset_id", "name", "asset_type", "latitude", "longitude", "geometry", "area_m2",
                 "capacity", "estimated_occupancy", "occupancy_basis", "municipality")
STATIC_FLAGS = ("occupancy_seasonal", "class_ambiguous")   # the only review reasons asset_record reads back

SETTINGS = {
    "input_mode": "recorded",
    "call_mode": "disabled",
    "search_radius_m": 10000,
    "catalog_file": "catalog.json",
    "llm_assessment": {"mode": "disabled"},
    "language": "en",
    "contacts": [],
    "approvals": [],
}


def catalog_row(asset: dict) -> dict:
    """One snapshot asset row -> static catalog row (register provenance kept, derived fields dropped)."""
    row = {k: asset.get(k) for k in STATIC_FIELDS}
    row["review_reasons"] = [r for r in asset.get("review_reasons") or [] if r in STATIC_FLAGS]
    row["sources"] = [s for s in asset.get("sources") or []
                      if s.get("fields") and set(s["fields"]) <= set(STATIC_FIELDS)]
    return row


def build_catalog(files=SNAPSHOT_FILES) -> list[dict]:
    rows = {}
    for path in files:
        for asset in json.loads(path.read_text(encoding="utf-8"))["assets"]:
            rows.setdefault(asset["asset_id"], catalog_row(asset))
    return [rows[k] for k in sorted(rows)]


def build_triggers() -> list[dict]:
    fires = recorded_real_fires()
    if not fires:
        raise SystemExit("fixtures/fire/deepfire/real/ has no satellite-perimeters response")
    return [{"trigger_id": f"gavarres-real-{n:04d}", "scenario_id": SCENARIO_ID, "input_mode": "recorded",
             "as_of": as_of.isoformat(), "fire": fire}
            for n, (fire, as_of) in enumerate(fires, start=1)]


def write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def generate(out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "settings.json", out_dir / "catalog.json"]
    write(written[0], SETTINGS)
    write(written[1], build_catalog())
    for trigger in build_triggers():
        path = out_dir / f"trigger-{trigger['trigger_id'].rsplit('-', 1)[1]}.json"
        write(path, trigger)
        written.append(path)
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    for path in generate(args.out_dir):
        body = json.loads(path.read_text(encoding="utf-8"))
        detail = (f"{len(body)} rows" if isinstance(body, list) else
                  f"as_of {body['as_of']} observed {body['fire']['observed_at']}" if "fire" in body else "")
        print(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
