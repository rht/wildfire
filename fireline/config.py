"""Tunable parameters. Every value here is shown in the UI; see CONTRACTS.md and PLAN.md 6.3-6.5."""

import hashlib
import json

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
