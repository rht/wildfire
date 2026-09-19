"""Contact urgency from forecast arrival and estimated total evacuation duration."""

import math
from dataclasses import dataclass

from .priority_models import number, validate_locations


@dataclass(frozen=True)
class ContactPolicy:
    # Times share the scenario epoch; evacuation and buffer are durations.
    now_min: float = 0
    buffer_min: float = 0
    version: str = "forecast-evacuation-window-v2"


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
        missing = [
            field
            for field in ("fire_arrival_min", "evacuation_min")
            if getattr(a, field) is None
        ]
        missing += [
            field
            for field in ("forecast_source", "evacuation_source")
            if not (getattr(a, field) or "").strip()
        ]
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
        row["time_to_impact_min"] = a.fire_arrival_min - policy.now_min
        row["latest_start_min"] = (
            a.fire_arrival_min - a.evacuation_min - policy.buffer_min
        )
        # Round arithmetic noise only; preserve negative windows for urgent review.
        row["slack_min"] = round(row["latest_start_min"] - policy.now_min, 9)
        row["status"] = "window_exhausted" if row["slack_min"] <= 0 else "window_open"
        ranked.append(row)
    ranked.sort(
        key=lambda r: (
            r["slack_min"],
            r["components"]["fire_arrival_min"],
            r["components"]["distance_m"]
            if r["components"]["distance_m"] is not None
            else math.inf,
            r["asset_id"],
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
