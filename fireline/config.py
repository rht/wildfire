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

# Contact priority from the remaining evacuation window (readme 6). Times are converted to minutes
# from one epoch: the snapshot `as_of` ("current time" for that snapshot, live or recorded). The
# weighted proximity/size/value score of the earlier draft is gone; this replaces it.
CONTACT_POLICY = {
    "version": "forecast-evacuation-window-v2",
    "buffer_min": 30,              # safety margin subtracted from the latest start; shown in the UI
    "now": "snapshot as_of",       # epoch for time_to_impact / latest_start / remaining window
    "arrival_basis": "p10 when the provider supports quantiles, else the provider's single estimate",
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
    },
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
