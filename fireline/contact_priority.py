"""Contact urgency from forecast arrival and estimated total evacuation duration.

The window arithmetic (`window_arithmetic`), the missing-evidence rule (`missing_timing`) and the sort
key (`contact_sort_key`) are module functions so the snapshot consumer (`fireline.priority`) ranks
live assets with exactly the same rules as this static prototype.
"""

import math
from dataclasses import dataclass

from .priority_models import number, validate_locations

TIMING_NUMBERS = ("fire_arrival_min", "evacuation_min")
TIMING_SOURCES = ("forecast_source", "evacuation_source")


@dataclass(frozen=True)
class ContactPolicy:
    # Times share the scenario epoch; evacuation and buffer are durations.
    now_min: float = 0
    buffer_min: float = 0
    version: str = "forecast-evacuation-window-v2"


def missing_timing(fire_arrival_min, evacuation_min, forecast_source, evacuation_source):
    """Names of the timing inputs that block ranking: null numbers and blank/missing sources."""
    missing = [name for name, value in zip(TIMING_NUMBERS, (fire_arrival_min, evacuation_min)) if value is None]
    missing += [name for name, value in zip(TIMING_SOURCES, (forecast_source, evacuation_source))
                if not (value or "").strip()]
    return missing


def window_arithmetic(fire_arrival_min, evacuation_min, now_min, buffer_min):
    """time_to_impact, latest_start (absolute, same epoch as the inputs), slack and status.

    slack = latest_start - now is rounded to 9 decimals to remove floating-point noise only;
    negative windows are preserved and reported as `window_exhausted` (slack <= 0).
    """
    time_to_impact = fire_arrival_min - now_min
    latest_start = fire_arrival_min - evacuation_min - buffer_min
    slack = round(latest_start - now_min, 9)
    return {
        "time_to_impact_min": time_to_impact,
        "latest_start_min": latest_start,
        "slack_min": slack,
        "status": "window_exhausted" if slack <= 0 else "window_open",
    }


def contact_sort_key(slack_min, fire_arrival_min, distance_m, asset_id):
    """Smallest window first, then earlier arrival, then nearer known distance (null last), then id."""
    return (slack_min, fire_arrival_min, distance_m if distance_m is not None else math.inf, asset_id)


def rank_contacts(locations, policy=None):
    """Smallest remaining evacuation window first; missing evidence stays in review.

    Evacuation duration includes mobilisation, preparation/loading and movement to
    the designated receiving location. It must be supplied, not guessed from people
    count. Geographic distance only breaks ties after forecast arrival.
    """
    policy = policy or ContactPolicy()
    validate_locations(locations)
    number(policy.now_min, "now_min")
    number(policy.buffer_min, "buffer_min")
    ranked, review = [], []
    for a in locations:
        missing = missing_timing(a.fire_arrival_min, a.evacuation_min, a.forecast_source, a.evacuation_source)
        row = {
            "asset_id": a.asset_id,
            "name": a.name,
            "policy": policy.version,
            "rank": None,
            "slack_min": None,
            "latest_start_min": None,
            "time_to_impact_min": None,
            "status": "needs_review",
            "components": {
                "fire_arrival_min": a.fire_arrival_min,
                "now_min": policy.now_min,
                "evacuation_min": a.evacuation_min,
                "buffer_min": policy.buffer_min,
                "distance_m": a.distance_m,
            },
            "forecast_source": a.forecast_source,
            "evacuation_source": a.evacuation_source,
            "review_reasons": missing,
        }
        if missing:
            review.append(row)
            continue
        window = window_arithmetic(a.fire_arrival_min, a.evacuation_min, policy.now_min, policy.buffer_min)
        row.update(window)
        ranked.append(row)
    ranked.sort(
        key=lambda r: contact_sort_key(
            r["slack_min"], r["components"]["fire_arrival_min"], r["components"]["distance_m"], r["asset_id"]
        )
    )
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    review.sort(key=lambda r: r["asset_id"])
    return {
        "ranked": ranked,
        "review": review,
        "policy": policy.version,
        "ordering": "slack ascending; forecast arrival ascending; distance ascending; asset ID",
    }
