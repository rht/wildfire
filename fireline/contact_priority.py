"""Independent contact ranking; no action benefit propagates into this score."""

from dataclasses import dataclass

from .priority_models import number, validate_locations


@dataclass(frozen=True)
class ContactPolicy:
    distance_scale_m: float = 200
    people_scale: float = 100
    value_scale: float = 100
    proximity_weight: float = 0.45
    people_weight: float = 0.20
    value_weight: float = 0.20
    assisted_weight: float = 0.15
    version: str = "static-contact-v1"


def rank_contacts(locations, policy=ContactPolicy()):
    validate_locations(locations)
    for value in (policy.distance_scale_m, policy.people_scale, policy.value_scale):
        number(value, "normalisation scale")
        if value <= 0:
            raise ValueError("normalisation scales must be positive")
    weights = [
        policy.proximity_weight,
        policy.people_weight,
        policy.value_weight,
        policy.assisted_weight,
    ]
    for w in weights:
        number(w, "weight")
    if abs(sum(weights) - 1) > 1e-9:
        raise ValueError("contact weights must sum to one")
    ranked, review = [], []
    for a in locations:
        missing = [
            field
            for field in ("distance_m", "people", "assisted", "value")
            if getattr(a, field) is None
        ]
        row = {
            "asset_id": a.asset_id,
            "name": a.name,
            "policy": policy.version,
            "score": None,
            "rank": None,
            "components": {},
            "review_reasons": missing,
        }
        if missing:
            review.append(row)
            continue
        components = {
            "proximity": (
                1 / (1 + a.distance_m / policy.distance_scale_m),
                policy.proximity_weight,
            ),
            "people": (min(a.people / policy.people_scale, 1), policy.people_weight),
            "value": (min(a.value / policy.value_scale, 1), policy.value_weight),
            "assisted": (
                min(a.assisted / policy.people_scale, 1),
                policy.assisted_weight,
            ),
        }
        row["components"] = {
            key: {"normalised": value, "weight": weight, "contribution": value * weight}
            for key, (value, weight) in components.items()
        }
        row["score"] = sum(value * weight for value, weight in components.values())
        ranked.append(row)
    ranked.sort(key=lambda r: (-r["score"], r["asset_id"]))
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    review.sort(key=lambda r: r["asset_id"])
    return {"ranked": ranked, "review": review, "policy": policy.version}
