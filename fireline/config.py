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
    },
}

# Explainable priority (readme 6): weights sum to one; components normalised to [0, 1] with fixed scales.
PRIORITY_POLICY = {
    "version": "priority-proto-2026-09-19",
    "weights": {"proximity": 0.5, "size": 0.3, "value": 0.2},
    "proximity_scale_m": 5000,     # proximity = clip(1 - distance / scale, 0, 1); 1 on intersection
    "size_scale_people": 300,      # size = clip(people / scale, 0, 1)
    "size_capacity_proxy": True,   # use capacity as a labelled proxy when estimated_occupancy is null
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
