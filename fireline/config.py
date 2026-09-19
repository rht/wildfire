"""Tunable parameters and policies. Every value here is shown in the UI; see CONTRACTS.md v1.0.

v4 policies (readme sections 4-7) come first; the v0 engine parameters (spread, routing, decisions)
follow and are only used when the matching FEATURES flag is on.
"""

import hashlib
import json

# ---------------------------------------------------------------------------------------------
# v4 MVP (readme.md): feature flags, freshness, value policy, priority policy
# ---------------------------------------------------------------------------------------------

# The v0 engine is gated: off by default, labelled enrichment when on (readme 2 "deferred").
FEATURES = {
    "spread_ca": False,           # cellular-automaton spread ensemble (not a provider; not validated)
    "routing": False,             # road graph, cut times, destinations
    "decisions": False,           # confine / evacuate rule; `recommendation` stays null otherwise
    "forecast_enrichment": False, # fill burn_probability / arrival_* on snapshot assets from a raster
    "asset_criticality": True,   # per-asset criticality tier proposed by the agent (CRITICALITY_POLICY)
}

# Stale-data thresholds on the fire observation age (readme 3, 5.1 data_status).
FRESHNESS = {"stale_after_s": 3600, "unavailable_after_s": 21600}

# Class-based operational importance for analyst attention: a prototype policy, not a monetary
# valuation nor an established emergency-service rule (readme 4 "Value"). Scores in [0, 1].
VALUE_POLICY = {
    "version": "value-proto-2026-09-19",
    "by_type": {
        "hospital": 1.0,
        "care_home": 1.0,
        "school": 0.8,
        "camp": 0.8,
        "campsite": 0.6,
        "nucleus": 0.7,
        "masia": 0.4,
        # Strategic classes (readme 4 "Value"): staffed sites with no vulnerable resident population.
        # The score is attention weight, not irreplaceability - that is per-asset, see CRITICALITY_POLICY.
        "fire_station": 0.9,
        "university": 0.8,
        "research_facility": 0.7,
        "aerodrome": 0.7,
    },
}

# Contact priority from the remaining evacuation window (readme 6). Times are converted to minutes
# from one epoch: the snapshot `as_of` ("current time" for that snapshot, live or recorded). The
# weighted proximity/size/value score of the earlier draft is gone; this replaces it.
CONTACT_POLICY = {
    "version": "forecast-evacuation-window-v2",
    "buffer_min": 30,              # safety margin subtracted from the latest start; shown in the UI
    "now": "snapshot as_of",       # epoch for time_to_impact / latest_start / remaining window
    "arrival_basis": "p10, else p50, else the provider's single estimate (its declared basis)",
    "attention_min": 60,           # windows at or below this are "small": map colour and task flagging bucket
}

# Total evacuation duration by facility class: mobilisation + preparation/loading + movement to a
# receiving location (readme 4 "Evacuation duration"). A versioned prototype assumption that the
# analyst can override per asset; it is not derived from headcount and is not an emergency-service rule.
EVACUATION_POLICY = {
    "version": "evacuation-proto-2026-09-19",
    "components": ["mobilisation_min", "preparation_min", "movement_min"],
    "by_type": {
        "hospital":  {"mobilisation_min": 30, "preparation_min": 90, "movement_min": 60,
                      "assumptions": "assisted patients; ambulances and staff assumed available"},
        "care_home": {"mobilisation_min": 30, "preparation_min": 90, "movement_min": 60,
                      "assumptions": "assisted residents; bus and ambulance transport assumed available"},
        "school":    {"mobilisation_min": 20, "preparation_min": 30, "movement_min": 40,
                      "assumptions": "ambulatory pupils with staff; buses assumed available"},
        "camp":      {"mobilisation_min": 20, "preparation_min": 30, "movement_min": 40,
                      "assumptions": "ambulatory children with staff; buses assumed available"},
        "campsite":  {"mobilisation_min": 20, "preparation_min": 20, "movement_min": 30,
                      "assumptions": "self-evacuating guests with own vehicles"},
        "nucleus":   {"mobilisation_min": 30, "preparation_min": 45, "movement_min": 30,
                      "assumptions": "residents mostly self-evacuating; door-to-door notification"},
        "masia":     {"mobilisation_min": 15, "preparation_min": 15, "movement_min": 30,
                      "assumptions": "single household with own vehicle"},
        "fire_station":      {"mobilisation_min": 5, "preparation_min": 10, "movement_min": 20,
                              "assumptions": "crews are already mobile and self-relocating; "
                                             "this is a relocation time, not an evacuation of occupants"},
        "university":        {"mobilisation_min": 20, "preparation_min": 30, "movement_min": 40,
                              "assumptions": "ambulatory students and staff; own transport and buses"},
        "research_facility": {"mobilisation_min": 20, "preparation_min": 40, "movement_min": 30,
                              "assumptions": "ambulatory staff; preparation includes securing samples, "
                                             "animals and hazardous stores, which can dominate"},
        "aerodrome":         {"mobilisation_min": 15, "preparation_min": 30, "movement_min": 30,
                              "assumptions": "ambulatory staff; preparation includes flying out or "
                                             "towing based aircraft"},
    },
}

# Per-asset criticality (readme 4 "Value", readme 8 "Focused agent workflow"). The class tables above
# say what an average facility of a class is worth; this says whether ONE building is more than its
# class - a national research centre, the fire brigade's own station, the only oncology centre for a
# province. The tier is proposed by the investigation agent from evidence it quotes, and applied only
# after analyst confirmation; nothing here is computed from the fire, from distance, or from the
# model's unaided opinion. Criticality never reorders the contact queue (readme 6: "Property value
# does not override contact urgency") - it drives a separate strategic-exposure view.
CRITICALITY_POLICY = {
    "version": "criticality-proto-2026-09-19",
    "default_tier": "routine",
    # `rank` orders the strategic view, highest first. `loss_multiplier` is what the euro ledger of
    # docs/VALUE_AT_RISK.md will multiply its per-class replacement value by; that ledger is not
    # implemented yet, so nothing reads the multiplier today. Both are assumed values.
    "tiers": {
        "routine":     {"rank": 0, "loss_multiplier": 1.0,
                        "label": "nothing beyond its class"},
        "elevated":    {"rank": 1, "loss_multiplier": 3.0,
                        "label": "locally significant; the disruption outlasts the building"},
        "high":        {"rank": 2, "loss_multiplier": 10.0,
                        "label": "regionally significant, or partly irreplaceable"},
        "exceptional": {"rank": 3, "loss_multiplier": 30.0,
                        "label": "national infrastructure, or holdings that cannot be rebuilt"},
    },
    # Closed enum: the agent may name only these, and every factor it names must be supported by a
    # snippet quoted verbatim from its own tool results (scripts/validate.py `_supported`).
    "factors": {
        "irreplaceable_holdings":
            "collections, biobanks, archives or long-running experiments that cannot be rebuilt",
        "national_research_infrastructure":
            "serves researchers beyond its own institution",
        "sole_regional_service":
            "the only provider of its service for the region; losing it displaces the service, not just the staff",
        "emergency_response_capability":
            "part of the response to this incident; losing it degrades the response itself",
        "hazardous_materials":
            "stores that make a fire here worse than a fire next door",
        "network_single_point_of_failure":
            "power, water, telecoms or transport that other assets depend on",
    },
    # Inflation guard: a tier may not be proposed with fewer named factors than this, so
    # "exceptional" can never rest on prose alone.
    "min_factors": {"routine": 0, "elevated": 1, "high": 1, "exceptional": 2},
    # Classes where one building can differ enough from its class average to be worth an
    # investigation. Assets of these classes enter the review queue as `criticality_unassessed`
    # while FEATURES["asset_criticality"] is on. The rest keep the class answer: the registers hold
    # hundreds of near-identical schools and campsites, and asking the model about each of them
    # costs tokens to learn nothing. An analyst can still point the agent at any asset by hand.
    "assess_classes": ("hospital", "research_facility", "university", "fire_station", "aerodrome"),
}

# Four analyst actions and the capability tags each needs (readme 7).
TASK_ACTIONS = {
    "confirm_occupancy": ["occupancy_check"],
    "contact_facility": ["facility_contact"],
    "check_access": ["access_check"],
    "request_resources": ["resource_request"],
}

# ---------------------------------------------------------------------------------------------
# v0 engine parameters (used only behind FEATURES)
# ---------------------------------------------------------------------------------------------

# Minutes of lead an evacuation of this class needs before the fire arrives (PLAN 6.3).
LEAD_TIME_MIN = {
    "care_home": 180,
    "hospital": 180,
    "camp": 120,
    "school": 120,
    "campsite": 90,
    "nucleus": 60,
    "masia": 60,
    "fire_station": 60,
    "university": 120,
    "research_facility": 90,
    "aerodrome": 90,
}

# Minutes to load people into vehicles (PLAN 6.4 exit window).
LOAD_TIME_MIN = {
    "care_home": 90,
    "hospital": 90,
    "camp": 30,
    "school": 30,
    "campsite": 20,
    "nucleus": 45,
    "masia": 15,
    "fire_station": 15,
    "university": 30,
    "research_facility": 30,
    "aerodrome": 30,
}

# Tier thresholds on lead-adjusted p10 arrival (minutes) and burn probability.
ACT_NOW_MIN = 120
PREPARE_MIN = 360
BURN_PROB_MIN = 0.2
BURN_PROB_HIGH = 0.7

# Routing and destinations.
ROUTE_BUFFER_MIN = 20
DEST_BURN_PROB_MAX = 0.1
DEST_MARGIN_MIN = 60
DEFAULT_SPEED_KMH = {"motorway": 90, "trunk": 80, "primary": 70, "secondary": 60, "tertiary": 50,
                     "residential": 30, "unclassified": 40, "track": 15, "service": 20}

# Cellular automaton (Alexandridis et al. 2008 style). Not calibrated on Gavarres.
CA = {"p0": 0.5, "minutes_per_step": 4, "wind_c1": 0.045, "wind_c2": 0.30, "slope_a": 0.078}


def config_hash() -> str:
    payload = {k: v for k, v in globals().items() if k.isupper()}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:8]
