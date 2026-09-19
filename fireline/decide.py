"""Confine / evacuate decision rule, window-based (PLAN 6.4). Contract in CONTRACTS.md."""

from __future__ import annotations

import math

from . import config

ISSUER_NOTE = "recommendation for the INFOCAT director; director del pla / alcalde orders, CECAT sends"
MEDICAL_CLASSES = ("care_home", "hospital")
STAGED_CLASSES = ("camp", "school")
STAGED_BUS = "confine now, evacuate when bus and route confirmed"
STAGED_BEDS = "confine now, evacuate when medical destination beds confirmed"


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float) and math.isinf(v):
        return "never"
    return f"{v:.0f}"


def shelter_viable_for(asset: dict) -> bool:
    """Explicit flag wins; campsites (tents) default to not viable, everything else to viable."""
    v = asset.get("shelter_viable")
    if v is None:
        return asset.get("asset_class") != "campsite"
    return bool(v)


def decide(asset: dict, route: dict | None, cfg=config) -> dict:
    cls = asset["asset_class"]
    bp = float(asset["burn_prob"])
    lap10 = float(asset["lead_adjusted_p10_min"])
    p10 = float(asset["arrival_p10_min"])
    load = cfg.LOAD_TIME_MIN[cls]

    exposed = bp >= cfg.BURN_PROB_MIN or lap10 <= cfg.PREPARE_MIN
    exposed_evidence = (
        f"burn_prob {bp:.2f} {'>=' if bp >= cfg.BURN_PROB_MIN else '<'} {cfg.BURN_PROB_MIN}; "
        f"p10 arrival {_fmt(p10)} min minus lead {asset['lead_time_min']} min = {_fmt(lap10)} min "
        f"{'<=' if lap10 <= cfg.PREPARE_MIN else '>'} {cfg.PREPARE_MIN} min horizon"
    )

    if route is None:
        window = None
        latest_departure = None
        window_ok = False
        window_evidence = "no viable route to any valid destination (cut, closed or destination inside envelope)"
    else:
        cut = float(route["first_cut_min"])
        travel = float(route["travel_min"])
        latest_departure = cut - travel - cfg.ROUTE_BUFFER_MIN
        window = latest_departure - load
        window_ok = window > 0
        window_evidence = (
            f"first cut {_fmt(cut)} min ({route.get('first_cut_road') or 'no road cut'}) - load {load} min "
            f"- travel {travel:.0f} min - buffer {cfg.ROUTE_BUFFER_MIN} min = {_fmt(window)} min "
            f"{'open' if window_ok else 'closed'}; latest departure t+{_fmt(latest_departure)} min "
            f"to {route['destination_name']}"
        )

    shelter = shelter_viable_for(asset)
    if asset.get("shelter_viable") is None:
        shelter_evidence = f"class default for {cls}: {'viable' if shelter else 'not viable (tents / no building)'}"
    else:
        shelter_evidence = f"asset record: shelter {'viable' if shelter else 'not viable'}"

    staged = None
    if not exposed:
        decision = "monitor"
    elif cls in MEDICAL_CLASSES:
        if bp >= cfg.BURN_PROB_HIGH and window_ok:
            decision, staged = "evacuate", STAGED_BEDS
        elif shelter:
            decision = "confine"
            if bp >= cfg.BURN_PROB_HIGH:
                staged = "confine now with Bombers protection; window closed for a medical evacuation"
        else:
            decision = "confine_request_protection"
    elif window_ok:
        decision = "evacuate"
        if cls in STAGED_CLASSES:
            staged = STAGED_BUS
    elif shelter:
        decision = "confine"
    else:
        decision = "confine_request_protection"

    reception = medical = None
    if route is not None:
        if route["destination_kind"] == "medical":
            medical = route["destination_name"]
        else:
            reception = route["destination_name"]

    return {
        "asset_id": asset["asset_id"],
        "decision": decision,
        "staged": staged,
        "exit_window_min": window,
        "latest_departure_min": latest_departure,
        "checks": {
            "exposed": {"ok": exposed, "evidence": exposed_evidence},
            "window_open": {"ok": window_ok, "evidence": window_evidence},
            "shelter_viable": {"ok": shelter, "evidence": shelter_evidence},
        },
        "reception_centre": reception,
        "medical_destination": medical,
        "route": route,
        "issuer_note": ISSUER_NOTE,
    }
