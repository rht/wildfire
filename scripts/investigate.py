#!/usr/bin/env python
"""One agent investigation (readme 8, CONTRACTS 6): live model call when ANTHROPIC_API_KEY is set,
otherwise a clearly labelled replay of fixtures/agent/prerecorded_investigation.json.

    .venv/bin/python scripts/investigate.py [snapshot.json] [--asset ASSET_ID] [--asset-json asset.json]
                                            [--fake] [--record] [--max-steps N]

Loads a snapshot (default fixtures/snapshots/synthetic_gavarres_0001.json), scores it with
fireline.priority.score_snapshot when importable, picks the asset (--asset or the first needs_review
asset) and runs fireline.agent.investigate. `--record` writes the record to the prerecorded fixture
path; `--fake` forces the offline FakeLLM (used to generate a labelled fixture when no key exists).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fireline import agent  # noqa: E402
from fireline.llm import AnthropicLLM, FakeLLM  # noqa: E402

DEFAULT_SNAPSHOT = ROOT / "fixtures" / "snapshots" / "synthetic_gavarres_0001.json"
PRERECORDED = ROOT / "fixtures" / "agent" / "prerecorded_investigation.json"

# Smoke-test asset when neither a snapshot nor --asset-json is available (CONTRACTS 2.2 shape).
INLINE_ASSET = {
    "asset_id": "fixture:pou_del_glac", "name": "Pou del Glaç", "asset_type": "camp",
    "latitude": 41.933, "longitude": 3.04, "geometry": None, "area_m2": None,
    "capacity": None, "estimated_occupancy": None, "occupancy_basis": None,
    "value_score": 0.8, "value_basis": "value-proto-2026-09-19",
    "distance_to_fire_m": 2134.6, "intersects_fire": False, "burn_probability": None,
    "arrival_p10_at": None, "arrival_p50_at": None, "forecast_horizon_at": None, "forecast_source": None,
    "needs_review": True, "review_reasons": ["occupancy_unknown", "occupancy_seasonal"],
    "sources": [{"fields": ["name", "asset_type"], "source": "fixture:assets", "observed_at": None,
                 "available_at": None, "fetched_at": None, "notes": "inline smoke-test asset"}],
    "municipality": "la Bisbal d'Empordà",
}


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def load_assets(snapshot_path: Path | None, asset_json: Path | None) -> tuple[list[dict], str]:
    """(scored assets, description of where they came from)."""
    if asset_json is not None:
        a = json.loads(asset_json.read_text())
        assets = a if isinstance(a, list) else [a]
        return score(assets, None), f"asset record(s) from {_rel(asset_json)}"
    if snapshot_path is not None and snapshot_path.exists():
        snap = json.loads(snapshot_path.read_text())
        return score(snap.get("assets", []), snap), f"snapshot {_rel(snapshot_path)} ({snap.get('snapshot_id')})"
    return score([dict(INLINE_ASSET)], None), "inline smoke-test asset (snapshot file not found)"


def score(assets: list[dict], snap: dict | None) -> list[dict]:
    """Score through fireline.priority when available; else raw assets flagged needs_review."""
    try:
        from fireline.priority import score_snapshot
    except ImportError:
        out = []
        for a in assets:
            a = dict(a)
            a.setdefault("priority_score", None)
            a.setdefault("priority_rank", None)
            a.setdefault("queue", "needs_review")
            out.append(a)
        return out
    envelope = dict(snap) if snap else {"assets": assets}
    envelope["assets"] = assets
    scored = score_snapshot(envelope)
    return scored["all"] if isinstance(scored, dict) else list(scored)


def pick_asset(assets: list[dict], asset_id: str | None) -> dict:
    if asset_id:
        for a in assets:
            if a["asset_id"] == asset_id:
                return a
        sys.exit(f"asset {asset_id!r} not found; known: {[a['asset_id'] for a in assets]}")
    for a in assets:
        if a.get("queue") == "needs_review" and a.get("review_reasons"):
            return a
    for a in assets:
        if a.get("review_reasons"):
            return a
    sys.exit("no asset with review reasons to investigate")


def print_record(record: dict) -> None:
    print(f"asset:        {record['asset_id']} ({record.get('name')})")
    print(f"reasons:      {', '.join(record.get('review_reasons') or [])}")
    print(f"llm_mode:     {record.get('llm_mode')}   steps: {record.get('steps')}   "
          f"postcheck_ok: {record.get('postcheck_ok')}")
    if record.get("label"):
        print(f"label:        {record['label']}")
    print("tool calls:")
    for i, call in enumerate(record.get("tool_calls") or [], 1):
        print(f"  {i}. {call['name']}({json.dumps(call.get('input'), ensure_ascii=False)})")
        result = call.get("result")
        text = json.dumps(result, ensure_ascii=False)
        if isinstance(result, list):
            text = f"{len(result)} candidate(s): " + json.dumps(
                [{k: c.get(k) for k in ("evidence_id", "name", "municipality", "capacity")} for c in result[:3]],
                ensure_ascii=False)
        if len(text) > 400:
            text = text[:400] + " ..."
        print(f"     -> {text}")
    print("proposals (pending analyst confirmation):")
    for p in record.get("proposals_added") or []:
        print(f"  {p['proposal_id']}: {p['field']} {p.get('previous')!r} -> {p['value']!r} from {p['source']} "
              f"({p['confidence']}); snippet: {p['quoted_snippet'][:120]!r}")
    if not record.get("proposals_added"):
        print("  (none)")
    print("questions (open):")
    for q in record.get("questions_added") or []:
        print(f"  {q['question_id']}: {q['question']} options={q['options']} default={q['default']!r}")
    if not record.get("questions_added"):
        print("  (none)")
    print("final text:")
    print("  " + (record.get("final_text") or ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot", nargs="?", default=str(DEFAULT_SNAPSHOT))
    ap.add_argument("--asset", default=None, help="asset_id to investigate (default: first needs_review asset)")
    ap.add_argument("--asset-json", default=None, help="path of a single asset record (or list) instead of a snapshot")
    ap.add_argument("--fake", action="store_true", help="force the offline FakeLLM even if a key is set")
    ap.add_argument("--record", action="store_true", help=f"write the record to {PRERECORDED}")
    ap.add_argument("--max-steps", type=int, default=6)
    args = ap.parse_args(argv)

    from fireline import env
    have_key = env.has_anthropic_key()   # loads .env first
    if not have_key and not args.fake and not args.record:
        if PRERECORDED.exists():
            print(f"PRERECORDED FALLBACK: no ANTHROPIC_API_KEY; replaying {PRERECORDED.relative_to(ROOT)}")
            record = json.loads(PRERECORDED.read_text())
            print(f"recorded_at:  {record.get('recorded_at')}   source: {record.get('input')}")
            print_record(record)
            return 0
        print(f"PRERECORDED FALLBACK: no ANTHROPIC_API_KEY and no {PRERECORDED.relative_to(ROOT)}; "
              f"running the offline FakeLLM instead")
        args.fake = True

    assets, where = load_assets(Path(args.snapshot) if args.snapshot else None,
                                Path(args.asset_json) if args.asset_json else None)
    wb = agent.Workbench.from_scored(assets)
    asset = pick_asset(assets, args.asset)

    if args.fake or not have_key:
        llm = FakeLLM()
        label = ("prerecorded with FakeLLM (no API key available on "
                 f"{datetime.now(timezone.utc).date().isoformat()}); rerun scripts/investigate.py --record with "
                 "ANTHROPIC_API_KEY to replace with a live transcript")
    else:
        llm = AnthropicLLM()
        label = f"live transcript, model {llm.model}"
    print(f"input:        {where}")
    print(f"mode:         {'FakeLLM (offline)' if isinstance(llm, FakeLLM) else 'live ' + llm.model}")
    record = agent.investigate(wb, asset["asset_id"], llm=llm, max_steps=args.max_steps)
    record["label"] = label
    record["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record["input"] = where
    print_record(record)

    if args.record:
        PRERECORDED.parent.mkdir(parents=True, exist_ok=True)
        PRERECORDED.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        print(f"\nrecorded to {PRERECORDED.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
