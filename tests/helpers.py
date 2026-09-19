"""Builders for complete v4 asset records and snapshot envelopes (CONTRACTS.md sections 2.1, 2.2).

Tests build their own snapshot dicts here rather than importing the producer, so the consumer-side
tests do not depend on `fireline.snapshot` or the fixture snapshot files.
"""

from __future__ import annotations

from fireline import config

AS_OF = "2026-07-03T08:00:00+00:00"


def make_asset(**overrides) -> dict:
    """A complete asset record with every CONTRACTS 2.2 key present and sensible defaults."""
    asset_type = overrides.get("asset_type", "school")
    review_reasons = list(overrides.get("review_reasons", []))
    record = {
        "asset_id": "fixture:test_asset",
        "name": "Test asset",
        "asset_type": asset_type,
        "latitude": 41.95,
        "longitude": 3.05,
        "geometry": None,
        "area_m2": None,
        "capacity": 200,
        "estimated_occupancy": None,
        "occupancy_basis": "register capacity",
        "value_score": config.VALUE_POLICY["by_type"].get(asset_type),
        "value_basis": config.VALUE_POLICY["version"],
        "distance_to_fire_m": 2000.0,
        "intersects_fire": False,
        "burn_probability": None,
        "arrival_p10_at": None,
        "arrival_p50_at": None,
        "forecast_horizon_at": None,
        "forecast_source": None,
        "needs_review": bool(review_reasons),
        "review_reasons": review_reasons,
        "sources": [],
        "municipality": None,
    }
    record.update(overrides)
    if "needs_review" not in overrides:
        record["needs_review"] = bool(record["review_reasons"])
    return record


def make_snapshot(assets, scenario_id="test", sequence=1, snapshot_id=None, as_of=AS_OF,
                  data_status="current", **overrides) -> dict:
    """A complete snapshot envelope per CONTRACTS 2.1 wrapping `assets`."""
    envelope = {
        "schema_version": "1.0",
        "scenario_id": scenario_id,
        "incident_id": f"{scenario_id}-incident",
        "snapshot_id": snapshot_id or f"{scenario_id}-{sequence:04d}",
        "sequence": sequence,
        "as_of": as_of,
        "computed_at": as_of,
        "input_mode": "synthetic",
        "fire_observed_at": as_of,
        "fire_source": "fixture:test",
        "fire_geometry": {"type": "Point", "coordinates": [3.0, 41.9]},
        "fire_geometry_kind": "hotspot_centre",
        "data_status": data_status,
        "metrics": {"source_age_s": 0.0, "processing_s": 0.0},
        "assets": list(assets),
    }
    envelope.update(overrides)
    return envelope
