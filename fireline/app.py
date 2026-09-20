"""ResponsAra analyst screen (readme 9, CONTRACTS 7): map, ranked table (remaining evacuation window),
review queue, selected asset with the timing breakdown and agent proposals, task controls, change log.
One screen; the sequence control walks the snapshot sequence in both directions: forward through the
store ("Next update"), back to an earlier moment as a re-ranked view that leaves the store untouched.

    FIRELINE_DB=data/fireline.sqlite .venv/bin/streamlit run fireline/app.py

All workflow logic lives in `fireline.ui_state.Session` (kept in st.session_state); this file renders.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pydeck as pdk
import streamlit as st

from fireline import config, priority, snapshot, tasks, ui_theme
from fireline.ui_state import Session, llm_available

STATUS_COLOUR = {"current": "green", "stale": "orange", "unavailable": "red"}
GREY = [198, 192, 186, 225]
RED, ORANGE, YELLOW = [232, 62, 46, 240], [240, 150, 40, 240], [236, 208, 60, 240]
FIRE_FILL, FIRE_LINE = [198, 48, 38, 95], [255, 96, 74, 255]
# Esri World Imagery: the satellite basemap of the mockup, public XYZ tiles with no API key.
SATELLITE_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/"
                 "tile/{z}/{y}/{x}")
SATELLITE_CREDIT = "Basemap Esri World Imagery (Esri, Maxar, Earthstar Geographics) - reference only."
TAGLINE = ("Which infrastructure, people and assets are in danger - and which hospital, which school, "
           "needs a call first.")
SMALL_WINDOW_MIN = config.CONTACT_POLICY["attention_min"]   # "small window" threshold shared with task flagging
# The euro columns are display strings, never numbers: an estimate from an assumed per-class replacement
# cost, shown with its damage-ratio band, that must never order or filter the table (handoff 002).
LOSS_COLUMN = "expected loss (estimate, assumed replacement cost, damage-ratio band)"
DEFAULT_SCENARIO = "gavarres_real"      # the real-area scenario opens first; the synthetic one stays selectable
# The approve-all preview: every pending agent proposal applied in memory at once, so the ranked queue
# can be read with and without them. It confirms nothing - no override reaches the store - and the fields
# the agent proposes are mostly absent from the contact sort key (readme 6), so the order usually does not
# move at all. The caption has to say that plainly instead of looking like a button that failed.
APPROVE_ALL_LABEL = "Preview all proposals as approved"
APPROVE_ALL_HELP = ("A what-if view, not an approval. Every pending agent proposal is applied in memory and "
                    "the queue re-ranked; the SQLite store is untouched, no override is confirmed, and no "
                    "analyst has checked these proposals. Switch it off to return to the confirmed data.")
RANK_DELTA_COLUMN = "rank change if all approved (+ = up the queue)"
SORT_KEY_NOTE = ("Occupancy, capacity and criticality_tier are absent from contact_priority.contact_sort_key "
                 "(readme 6), so approving them changes what the analyst knows about a location, not who is "
                 "contacted first; only asset_type and evacuation_min can move a row.")


# ----------------------------------------------------------------------------- helpers
def session() -> Session:
    if "session" not in st.session_state:
        st.session_state.session = Session()
    return st.session_state.session.ensure_open()


def fmt(v, nd=0) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def fmt_age(seconds) -> str:
    if seconds is None:
        return "unknown"
    s = abs(float(seconds))
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


def people(asset: dict) -> str:
    """Estimated occupancy, or capacity marked as a proxy (never shown as a headcount)."""
    if asset.get("estimated_occupancy") is not None:
        return f"{asset['estimated_occupancy']}"
    if asset.get("capacity") is not None:
        return f"{asset['capacity']} (capacity proxy)"
    return "unknown"


def exposed(asset: dict) -> str:
    """`people_exposed` (occupancy x burn probability), "-" when the layer is off or an input is null."""
    v = asset.get("people_exposed")
    return "-" if v is None else f"{v:g}"


def at_risk(asset: dict) -> str:
    """`people_at_risk_p50` with p10 beside it: the whole headcount of an exhausted window, else 0."""
    p50, p10 = asset.get("people_at_risk_p50"), asset.get("people_at_risk_p10")
    if p50 is None and p10 is None:
        return "-"
    return f"{'-' if p50 is None else p50} / {'-' if p10 is None else p10}"


def loss_eur(asset: dict) -> str:
    """Expected loss as a display string carrying its band, e.g. `EUR 800,000 (300,000 - 1,600,000)`.

    A string, never a number: the euro columns are an estimate from an assumed per-class replacement
    cost and must not order the table, so nothing here is sortable into a meaningful ranking."""
    mid = asset.get("expected_loss_eur_mid")
    if mid is None:
        return "not valued" if asset.get("burn_probability") is not None else "-"
    low, high = asset.get("expected_loss_eur_low"), asset.get("expected_loss_eur_high")
    return f"EUR {mid:,.0f} ({low:,.0f} - {high:,.0f})"


def horizon_hours(horizon_at, as_of) -> str:
    """`12 h` from the forecast horizon and the snapshot time, or "" when either is missing."""
    try:
        h = (datetime.fromisoformat(str(horizon_at).replace("Z", "+00:00"))
             - datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))).total_seconds() / 3600
    except (TypeError, ValueError):
        return ""
    return f"{h:g} h" if h > 0 else ""


def value_summary(v: dict, as_of) -> str:
    """The header sentence for the value-at-risk totals.

    A forecast that covers every located asset and reaches none of them is a zero, not a gap: it reads
    "0 exposed within the horizon", not "forecast unavailable". The review queue still carries
    `forecast_unavailable` for those assets, because none of them has an arrival time.
    """
    if not v["layer"]:
        return ('Value at risk is off for this scenario (config.FEATURES["value_at_risk"]): '
                "no people or euro totals are computed.")
    span = horizon_hours(v.get("horizon_at"), as_of)
    where = f"within the {span} forecast horizon" if span else "within the forecast horizon"
    if v["covered"] and not v["reached"]:
        return (f"0 exposed {where}: the forecast covers all {v['covered']} located assets and puts no burned "
                f"area at any of them, so the zeros are its statement about those locations, not a missing "
                f"forecast. Every asset still carries forecast_unavailable, which here means no arrival time.")
    return (f"{v['people_exposed']:g} people exposed {where}, {v['people_at_risk_p50']:g} at risk at p50 and "
            f"{v['people_at_risk_p10']:g} at p10, expected loss EUR {v['expected_loss_eur_mid']:,.0f} "
            f"(band {v['expected_loss_eur_low']:,.0f} to {v['expected_loss_eur_high']:,.0f}). Of "
            f"{v['located']} located assets, {v['excluded_people']} are left out of the people totals and "
            f"{v['excluded_eur']} out of the euro totals for want of an input, so neither is complete.")


def value_limits(v: dict) -> str:
    """The known limits that must sit next to any of these numbers (handoff 002)."""
    if not v["layer"]:
        return ""
    text = (f"Replacement values and damage ratios are per-class assumptions ({v['policy_version']}, "
            "value_basis \"assumed\") with no per-asset basis; the band shown is the damage-ratio band only, so "
            "the value uncertainty is at least as large. The figures are total economic loss, insured and "
            "uninsured, not an insurer's figure. People at risk counts the whole headcount of an asset whose "
            "remaining window at that arrival quantile is exhausted; it does not model partial clearance. "
            "Euros are a display column and this total only: they never enter the ranking, the sort or a "
            "filter, and there is no euro figure for lives.")
    if v["enrichment"]:
        text += (" The burn probabilities behind these totals come from the uncalibrated CA ensemble, which "
                 "percolates almost isotropically under light wind, so the expected loss is pessimistic by "
                 "construction (handoff 001).")
    return text


def window_colour(asset: dict) -> list[int]:
    """Red = window exhausted, orange = small remaining window (< SMALL_WINDOW_MIN), yellow = larger
    window, grey = unranked (needs review: no forecast or evacuation estimate)."""
    slack = asset.get("slack_min")
    if slack is None or asset.get("queue") != "ranked":
        return GREY
    if asset.get("priority_status") == "window_exhausted":
        return RED
    return ORANGE if float(slack) < SMALL_WINDOW_MIN else YELLOW


def fmt_window(asset: dict) -> str:
    slack = asset.get("slack_min")
    if slack is None:
        return "unranked (needs review)"
    return f"rank {asset['priority_rank']}, remaining window {slack:.0f} min ({asset['priority_status']})"


def window_tone(asset: dict) -> str:
    """Tone name for the remaining window, the same three bands `window_colour` paints on the map:
    red = exhausted, orange = under SMALL_WINDOW_MIN, ink = larger, grey = unranked."""
    slack = asset.get("slack_min")
    if slack is None or asset.get("queue") != "ranked":
        return "grey"
    if asset.get("priority_status") == "window_exhausted":
        return "red"
    return "orange" if float(slack) < SMALL_WINDOW_MIN else "ink"


def window_segments(asset: dict) -> list[float | None]:
    """The three parts of the window arithmetic, for the ranked row's stacked bar.

    `evacuation + buffer + remaining window = time to impact`, so the bar shows how the snapshot's own
    arithmetic spends the time before the forecast arrival. It is not a new score: nothing here
    computes risk, it only re-draws `window_components` (readme 5)."""
    c = asset.get("window_components") or {}
    return [c.get("evacuation_min"), c.get("buffer_min"), asset.get("slack_min")]


def location_subtitle(asset: dict) -> str:
    """`school - 1400 m to fire`, with the distance dropped when the asset has none."""
    distance = asset.get("distance_to_fire_m")
    where = f" - {distance:,.0f} m to fire" if distance is not None else " - distance unknown"
    return f"{asset.get('asset_type') or 'type unknown'}{where}"


def stamp(value) -> str:
    """`13:20Z 2026-07-03` for the header stamps; the raw text when it will not parse."""
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fmt(value)
    return f"{t:%H:%M}Z {t:%Y-%m-%d}"


def escalation_counts(rows: list[dict]) -> dict[str, int]:
    """Task pipeline totals for the escalation card: done, blocked, and everything still open."""
    done = sum(1 for t in rows if t["status"] == "done")
    blocked = sum(1 for t in rows if t["status"] == "blocked")
    return {"done": done, "blocked": blocked, "open": len(rows) - done - blocked}


def try_action(label: str, fn, *args, **kwargs) -> bool:
    """Run a store/agent action; show its error instead of crashing the page. True on success."""
    try:
        fn(*args, **kwargs)
    except tasks.AssignmentError as e:
        st.error(f"{label} rejected: {e}")
        return False
    except (KeyError, ValueError, RuntimeError) as e:
        st.error(f"{label} failed: {e}")
        return False
    return True


# ----------------------------------------------------------------------------- map
def polygon_rows(geometry: dict, geometry_kind: str | None = None) -> list[dict]:
    kind = geometry.get("type")
    polys = [geometry["coordinates"]] if kind == "Polygon" else geometry["coordinates"] if kind == "MultiPolygon" else []
    title, tip = (("Simulated burn area", "fire-spread run, not an observed perimeter") if geometry_kind == "simulated"
                  else ("Fire perimeter", "surveyed/synthetic footprint"))
    return [{"polygon": rings, "title": title, "tip": tip} for rings in polys]


def build_deck(sess: Session) -> tuple[pdk.Deck, int]:
    snap, status = sess.snapshot, sess.status()
    # Satellite basemap first so every other layer draws over it; no key, no style server.
    layers = [pdk.Layer("TileLayer", data=SATELLITE_URL, min_zoom=0, max_zoom=19, tile_size=256)]
    lons, lats = [], []
    geometry = snap.get("fire_geometry")
    if geometry and geometry.get("type") in ("Polygon", "MultiPolygon"):
        rows = polygon_rows(geometry, snap.get("fire_geometry_kind"))
        layers.append(pdk.Layer("PolygonLayer", data=rows, get_polygon="polygon", get_fill_color=FIRE_FILL,
                                get_line_color=FIRE_LINE, line_width_min_pixels=3, stroked=True, filled=True,
                                pickable=True))
        for rings in (r["polygon"] for r in rows):
            for x, y in rings[0]:
                lons.append(x)
                lats.append(y)
    elif geometry and geometry.get("type") == "Point":
        x, y = geometry["coordinates"][:2]
        lons.append(x)
        lats.append(y)
        layers.append(pdk.Layer("ScatterplotLayer",
                                data=[{"lon": x, "lat": y, "title": "Hotspot centre",
                                       "tip": "not a surveyed perimeter"}],
                                get_position=["lon", "lat"], get_radius=600, radius_min_pixels=14, filled=False,
                                stroked=True, get_line_color=[255, 120, 0], line_width_min_pixels=3, pickable=True))
    points = []
    for a in sess.assets_in_order():
        if a.get("latitude") is None or a.get("longitude") is None:
            continue
        lons.append(a["longitude"])
        lats.append(a["latitude"])
        points.append({"lon": a["longitude"], "lat": a["latitude"], "color": window_colour(a),
                       "title": f"{a['name']} ({a['asset_type']})",
                       "tip": "\n".join([
                           fmt_window(a),
                           f"arrival {a.get('fire_arrival_at') or 'no forecast'}; "
                           f"evacuation {fmt(a.get('evacuation_min'))} min",
                           f"distance {fmt(a.get('distance_to_fire_m'))} m; people {people(a)}",
                           ', '.join(a.get('review_reasons') or []) or 'no review flags',
                       ])})
    layers.append(pdk.Layer("ScatterplotLayer", data=points, get_position=["lon", "lat"], get_fill_color="color",
                            get_radius=220, radius_min_pixels=8, pickable=True, stroked=True,
                            get_line_color=[255, 255, 255, 210], line_width_min_pixels=2))
    clon = sum(lons) / len(lons) if lons else 3.0
    clat = sum(lats) / len(lats) if lats else 41.9
    view = pdk.ViewState(longitude=clon, latitude=clat, zoom=10.5, pitch=0)
    tooltip = {"html": "<b>{title}</b><br/>{tip}", "style": {"whiteSpace": "pre-line"}}
    deck = pdk.Deck(layers=layers, initial_view_state=view, tooltip=tooltip, map_style=None)
    return deck, status["counts"]["unlocated"]


# ----------------------------------------------------------------------------- tables
def open_task_counts(sess: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in sess.open_tasks():
        counts[t["asset_id"]] = counts.get(t["asset_id"], 0) + 1
    return counts


def criticality_label(asset: dict) -> str:
    """"tier (factor, factor)", or "" when no tier has been confirmed. Display only: no euro figure
    exists yet, and nothing in the ranking reads this (readme 6)."""
    tier = asset.get("criticality_tier")
    if not tier:
        return ""
    factors = asset.get("criticality_factors") or []
    return f"{tier} ({', '.join(f.replace('_', ' ') for f in factors)})" if factors else tier


def strategic_frame(assets: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "criticality": criticality_label(a), "name": a["name"], "type": a["asset_type"],
        "municipality": a.get("municipality"), "distance m": a.get("distance_to_fire_m"),
        "predicted arrival": a.get("fire_arrival_at"), "remaining window (min)": a.get("slack_min"),
        "basis": a.get("criticality_basis"), "asset_id": a["asset_id"],
    } for a in assets])


def rank_delta(asset: dict) -> str:
    """One asset's rank movement under the approve-all preview, signed: `+3 (up)` is three places nearer
    the top of the contact queue, `-2 (down)` is two places further from it, `0 (no move)` is the usual
    answer. Only meaningful while the preview is on; `ranked_frame` leaves the column out otherwise."""
    delta, baseline = asset.get("preview_rank_delta"), asset.get("preview_baseline_rank")
    if delta is None:
        return "new (not ranked before)" if baseline is None else "-"
    d = int(delta)
    if d == 0:
        return "0 (no move)"
    return f"+{d} (up)" if d > 0 else f"{d} (down)"


def by_field_text(by_field: dict | None) -> str:
    """`capacity 2, criticality_tier 13`: which fields the preview's proposals would write."""
    return ", ".join(f"{field} {n}" for field, n in sorted((by_field or {}).items())) or "no field"


def approve_all_caption(report: dict | None, brief: bool = False) -> str:
    """What approving every agent proposal actually changed - including, in the usual case, nothing at
    all about the contact order. `report` is `Session.preview_report`; "" when the preview is off.

    Zero rows moved is the designed separation (readme 6, measured in VALIDATION.md), not an empty result,
    so the caption states it and names the sort key rather than leaving a column of zeros unexplained.
    """
    if not report or not report.get("on"):
        return ""
    moved, total = report["rows_moved"], report["ranked_after"]
    if not report["proposals"]:
        if brief:
            return ("Preview on, but the agent has proposed nothing on this snapshot: the list below is the "
                    "confirmed ranking.")
        return (f"Preview on, and there is nothing to approve: the agent has made no proposals on this "
                f"snapshot ({report['investigated']} location(s) investigated, llm mode "
                f"`{report['llm_label']}`). The {total} ranked row(s) below are the confirmed ranking, "
                f"unchanged, and the store holds no new overrides.")
    if brief:
        tail = (f"{moved} of {total} row(s) moved." if moved else
                f"contact order unchanged, 0 of {total} rows moved - these fields are not in the sort key.")
        return (f"Preview: {report['proposals']} proposal(s) applied in memory, nothing written to the "
                f"store; {tail}")
    head = (f"Preview only: {report['proposals']} agent proposal(s) from {report['investigated']} "
            f"investigated location(s), applied in memory (llm mode `{report['llm_label']}`) - "
            f"{by_field_text(report['by_field'])}. No analyst confirmed them and the store holds no new "
            f"overrides. {report['flags_cleared']} review flag(s) cleared, {report['tiers_set']} "
            f"criticality tier(s) set.")
    if moved:
        return (f"{head} Contact order: {moved} of {total} ranked row(s) moved, "
                f"{len(report['entered_ranked'])} entered the ranked queue and {len(report['left_ranked'])} "
                f"left it ({report['ranked_before']} ranked before, {total} after).")
    return (f"{head} Contact order: unchanged - 0 of {total} ranked rows moved. That is the designed "
            f"separation, not a failed button. {SORT_KEY_NOTE} VALIDATION.md records the same result for "
            f"criticality: the contact order with every asset at the top tier is identical to the untiered "
            f"order.")


def ranked_frame(assets: list[dict], open_counts: dict[str, int], preview: bool = False) -> pd.DataFrame:
    """The ranked table. `preview` adds the rank-movement column, and only then: with the approve-all
    preview off the table is exactly the confirmed one, same columns as ever."""
    rows = []
    for a in assets:
        row: dict = {"rank": a["priority_rank"]}
        if preview:
            row[RANK_DELTA_COLUMN] = rank_delta(a)
        row.update({
            "name": a["name"], "type": a["asset_type"], "municipality": a.get("municipality"),
            "distance m": a.get("distance_to_fire_m"), "predicted arrival": a.get("fire_arrival_at"),
            "forecast source": a.get("forecast_source"), "evacuation min": a.get("evacuation_min"),
            "latest start (min from now)": a.get("latest_start_min"),
            "remaining window (min)": a.get("slack_min"), "status": a.get("priority_status"),
            "review flags": ", ".join(a.get("review_reasons") or []), "people": people(a),
            "people exposed": exposed(a), "people at risk p50 / p10": at_risk(a), LOSS_COLUMN: loss_eur(a),
            "criticality": criticality_label(a),
            "open tasks": open_counts.get(a["asset_id"], 0), "asset_id": a["asset_id"],
        })
        rows.append(row)
    return pd.DataFrame(rows)


def review_frame(assets: list[dict], open_counts: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame([{
        "name": a["name"], "type": a["asset_type"], "municipality": a.get("municipality"),
        "known distance m": a.get("distance_to_fire_m"), "predicted arrival": a.get("fire_arrival_at"),
        "evacuation min": a.get("evacuation_min"), "people": people(a),
        "people exposed": exposed(a), "people at risk p50 / p10": at_risk(a), LOSS_COLUMN: loss_eur(a),
        "reasons": ", ".join(a.get("review_reasons") or []), "criticality": criticality_label(a),
        "not ranked because": next((r for r in a["priority_reasons"] if r.startswith("needs review")), ""),
        "open tasks": open_counts.get(a["asset_id"], 0), "asset_id": a["asset_id"],
    } for a in assets])


def components_frame(asset: dict) -> pd.DataFrame:
    """Timing breakdown: arrival (+ basis, forecast source, horizon), evacuation (+ source), buffer, now."""
    c = asset.get("window_components") or {}
    rows = [
        {"component": "predicted fire arrival", "value": c.get("fire_arrival_at") or "unknown",
         "basis / source": f"{c.get('fire_arrival_basis') or 'basis unstated'}; forecast {c.get('forecast_source') or 'none'}"
                           + (f", horizon {c['forecast_horizon_at']}" if c.get("forecast_horizon_at") else ""),
         "minutes from now": asset.get("time_to_impact_min")},
        {"component": "total evacuation duration", "value": fmt(c.get("evacuation_min")) + " min" if c.get("evacuation_min") is not None else "unknown",
         "basis / source": c.get("evacuation_source") or "none", "minutes from now": None},
        {"component": "buffer", "value": f"{fmt(c.get('buffer_min'))} min",
         "basis / source": asset.get("priority_policy_version"), "minutes from now": None},
        {"component": "now (window epoch)", "value": c.get("now_at"), "basis / source": config.CONTACT_POLICY["now"],
         "minutes from now": 0.0},
        {"component": "latest start", "value": fmt(asset.get("latest_start_min")) + " min" if asset.get("latest_start_min") is not None else "-",
         "basis / source": "arrival - evacuation - buffer", "minutes from now": asset.get("latest_start_min")},
        {"component": "remaining window", "value": fmt(asset.get("slack_min")) + " min" if asset.get("slack_min") is not None else "-",
         "basis / source": asset.get("priority_status"), "minutes from now": asset.get("slack_min")},
    ]
    return pd.DataFrame(rows)


def value_frame(asset: dict) -> pd.DataFrame | None:
    """People and euros at risk for one asset, or None when the snapshot carries no layer.

    Shows the arithmetic rather than the result alone: headcount x burn probability, the assumed class
    replacement value with its policy basis, the three damage ratios and the three expected losses, and
    which arrival quantile made each `people_at_risk` flag fire. The policy note itself is already in the
    asset's `sources` entry for these fields, rendered below; it is not repeated here.
    """
    if not any(k in asset for k in snapshot.VALUE_AT_RISK_KEYS):
        return None
    occ, bp = asset.get("estimated_occupancy"), asset.get("burn_probability")
    band = config.VALUE_AT_RISK_POLICY["by_type"].get(asset.get("asset_type"))
    ratios = "-" if band is None else f"{band['d_low']:g} / {band['d_mid']:g} / {band['d_high']:g}"
    value, basis = asset.get("replacement_value_eur"), asset.get("replacement_value_basis")
    rows = [
        {"component": "people exposed", "value": exposed(asset),
         "basis / source": (f"occupancy {occ} x burn probability {bp:g}" if occ is not None and bp is not None
                            else "null: no headcount or no burn probability, never zero")},
        {"component": "replacement value", "value": "not valued" if value is None else f"EUR {value:,.0f}",
         "basis / source": basis or f"class {asset.get('asset_type')} has no replacement value in the policy"},
        {"component": "damage ratio low / mid / high", "value": ratios,
         "basis / source": (band or {}).get("note") or config.VALUE_AT_RISK_POLICY["version"]},
        {"component": "expected loss", "value": loss_eur(asset),
         "basis / source": "burn probability x damage ratio x replacement value; the band is the "
                           "damage-ratio band only, so the value uncertainty is at least as large"},
    ]
    for q in ("p50", "p10"):
        at = asset.get(f"people_at_risk_{q}")
        arrival = asset.get(f"arrival_{q}_at")
        if at is None:
            why = "null: no headcount, no evacuation estimate or no forecast covering this asset"
        elif at:
            why = f"window exhausted at arrival {arrival}: the whole headcount, no partial clearance"
        elif arrival:
            why = f"window still open at arrival {arrival}"
        else:
            why = "the forecast covers this asset but does not reach it inside its horizon"
        rows.append({"component": f"people at risk ({q})", "value": "-" if at is None else str(at),
                     "basis / source": why})
    return pd.DataFrame(rows)


def sources_frame(asset: dict) -> pd.DataFrame:
    return pd.DataFrame([{"fields": ", ".join(s.get("fields") or []), "source": s.get("source"),
                          "observed_at": s.get("observed_at"), "available_at": s.get("available_at"),
                          "fetched_at": s.get("fetched_at"), "notes": s.get("notes")} for s in asset.get("sources") or []])


def tasks_frame(rows: list[dict], teams: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame([{
        "task": t["task_id"], "asset": t["asset_id"], "action": t["action"], "reason": t["reason"],
        "status": t["status"], "team": teams.get(t["assigned_team_id"], t["assigned_team_id"]),
        "deadline": t["deadline_at"], "affected by": t["affected_by_snapshot_id"] or "",
        "questions": sum(1 for q in t["blocking_questions"] if q["answer"] is None), "suggested": t["suggested"],
    } for t in rows])


# ----------------------------------------------------------------------------- sequence (time) controls
def snapshot_label(entry: dict) -> str:
    """`3 - 2026-07-04 06:31Z`: the sequence number and the moment the snapshot describes."""
    as_of = (entry.get("as_of") or "")[:16].replace("T", " ")
    return f"{entry['sequence']} - {as_of}Z" if as_of else str(entry["sequence"])


def sequence_controls(sess: Session, s: dict) -> None:
    """Step and scrub through the scenario's snapshots. Forward past the store's high-water sequence
    applies the update; anywhere at or below it is a view (`Session.go_to`)."""
    entries = sess.sequence_entries
    st.sidebar.caption(f"snapshot `{s['snapshot_id']}` - sequence {s['sequence']} of {s['n_sequences']}"
                       + (f" - store at {s['applied_sequence']}" if s["reviewing_earlier"] else ""))
    back, forward = st.sidebar.columns(2)
    if back.button("Previous", disabled=not sess.has_previous, width="stretch", icon=":material/undo:"):
        sess.previous_update()
        st.rerun()
    if forward.button("Next update", disabled=not sess.has_next, width="stretch", type="primary"):
        sess.next_update()
        st.rerun()
    if len(entries) > 1:
        labels = [snapshot_label(e) for e in entries]
        here = labels[sess.index]
        # the key carries the position: a fresh widget each time the session moves, so a click on
        # Previous/Next is not overwritten by the slider's remembered value on the next rerun
        chosen = st.sidebar.select_slider("Snapshot time (as_of)", options=labels, value=here,
                                          key=f"seq-{sess.scenario_id}-{sess.index}")
        if chosen != here:
            sess.go_to(labels.index(chosen))
            st.rerun()
    if s["reviewing_earlier"]:
        st.sidebar.warning(f"Reviewing sequence {s['sequence']} of {s['n_sequences']}; the store stays at "
                           f"{s['applied_sequence']}. View only.")


# ----------------------------------------------------------------------------- sidebar
def sidebar(sess: Session) -> None:
    st.sidebar.title("ResponsAra")
    ids = sess.scenario_ids
    if not ids:
        st.sidebar.error("No snapshots found in fixtures/snapshots or data/snapshots.")
        st.stop()
    current = sess.scenario_id or ids[0]
    chosen = st.sidebar.selectbox("Scenario", ids, index=ids.index(current), key="scenario_select")
    if chosen != sess.scenario_id:
        sess.select_scenario(chosen)
        st.rerun()
    s = sess.status()
    sequence_controls(sess, s)
    result = sess.last_update
    if result:
        if result.get("view_only"):
            st.sidebar.info(result.get("reason"))
        elif not result.get("advanced"):
            st.sidebar.warning(result.get("reason"))
        elif result.get("accepted"):
            st.sidebar.success(f"{result['snapshot_id']} applied: {len(result['changed'])} assets changed, "
                               f"{len(result['affected_task_ids'])} tasks flagged, {len(result['missing_asset_ids'])} "
                               f"missing, {len(result['suggested_task_ids'])} tasks suggested.")
        else:
            st.sidebar.info(f"{result['snapshot_id']} shown; store bookkeeping unchanged: {result.get('reason')}")
        if result.get("affected_task_ids"):
            st.sidebar.caption("affected tasks: " + ", ".join(result["affected_task_ids"]))
        if result.get("missing_asset_ids"):
            st.sidebar.error("missing assets (tasks kept): " + ", ".join(result["missing_asset_ids"]))

    st.sidebar.subheader("Input")
    colour = STATUS_COLOUR.get(s["data_status_now"], "grey")
    st.sidebar.markdown(
        f"mode **{s['input_mode']}** - fire source `{s['fire_source']}` - geometry **{s['fire_geometry_kind']}**  \n"
        f"as_of `{s['as_of']}`  \ncomputed_at `{s['computed_at']}`  \n"
        f"data status now :{colour}[**{s['data_status_now']}**] (recorded in snapshot: {s['data_status_recorded']})  \n"
        f"source age: {fmt_age(s['source_age_s'])} at computation, {fmt_age(s['source_age_now_s'])} now  \n"
        f"processing time {fmt(s['processing_s'], 1)} s (receipt to snapshot)")
    with st.sidebar.expander("Contact, evacuation and value policy"):
        st.json({"CONTACT_POLICY": config.CONTACT_POLICY, "EVACUATION_POLICY": config.EVACUATION_POLICY,
                 "VALUE_POLICY": config.VALUE_POLICY, "FRESHNESS": config.FRESHNESS})
    with st.sidebar.expander("Team roster"):
        busy = {t["assigned_team_id"] for t in sess.open_tasks() if t["assigned_team_id"]}
        st.dataframe(pd.DataFrame([{"team": t["team_id"], "name": t["name"], "capabilities": ", ".join(t["capabilities"]),
                                    "available": t["available"], "busy": t["team_id"] in busy}
                                   for t in sess.store.teams()]), width="stretch", hide_index=True)
    for w in sess.discovery_warnings:
        st.sidebar.warning(w)
    st.sidebar.caption(f"store `{s['db_path']}`")


# ----------------------------------------------------------------------------- selected asset
def render_agent(sess: Session, asset: dict) -> None:
    aid = asset["asset_id"]
    live = llm_available()
    label = "Investigate (live LLM)" if live else "Investigate (FakeLLM, no LLM API key)"
    if st.button(label, key=f"inv-{aid}", disabled=not asset.get("review_reasons")):
        try:
            sess.investigate(aid, live=live)
        except Exception as e:  # LLM/network failure leaves the question answerable manually
            st.error(f"investigation failed ({type(e).__name__}: {e}); answer the open items manually")
        st.rerun()
    rec = sess.investigations.get(aid)
    if rec:
        st.markdown(f"**Investigation** - llm mode `{rec['llm_label']}`, {rec['steps']} steps, "
                    f"number post-check {'ok' if rec['postcheck_ok'] else 'FAILED'}")
        for c in rec["tool_calls"]:
            with st.expander(f"tool {c['name']}({', '.join(f'{k}={v!r}' for k, v in c['input'].items())})"):
                st.json(c["result"])
        st.info(rec["final_text"])
    for p in sess.pending_proposals(aid):
        st.markdown(f"**Proposal `{p['proposal_id']}`**: {p['field']} {p['previous']!r} -> {p['value']!r} "
                    f"({p['confidence']}) from {p['source']} {p.get('url') or ''}  \n> {p['quoted_snippet']}")
        c1, c2 = st.columns(2)
        if c1.button("Confirm", key=f"ok-{p['proposal_id']}", type="primary"):
            if try_action("confirm", sess.confirm, p["proposal_id"]):
                st.rerun()
        if c2.button("Reject", key=f"no-{p['proposal_id']}"):
            if try_action("reject", sess.reject, p["proposal_id"], "rejected by analyst"):
                st.rerun()
    for q in sess.open_questions(aid):
        st.markdown(f"**Question `{q['question_id']}`**: {q['question']} (default *{q['default']}*)")
        cols = st.columns(len(q["options"]))
        for i, opt in enumerate(q["options"]):
            if cols[i].button(opt, key=f"q-{q['question_id']}-{i}", type="primary" if opt == q["default"] else "secondary"):
                if try_action("answer", sess.answer, q["question_id"], opt):
                    st.rerun()


def render_task(sess: Session, t: dict, team_ids: list[str], team_names: dict[str, str]) -> None:
    flag = f" - affected by {t['affected_by_snapshot_id']}" if t["affected_by_snapshot_id"] else ""
    head = (f"{t['task_id']} {t['action']} - **{t['status']}** - {t['reason']}"
            f"{' (suggested)' if t['suggested'] else ''}{flag}")
    with st.expander(head, expanded=t["status"] != "done"):
        st.caption(f"needs {', '.join(t['required_capabilities'])}; team {team_names.get(t['assigned_team_id'], 'none')}; "
                   f"based on {t['based_on_snapshot_id']}; updated {t['updated_at']}")
        if t["notes"]:
            st.text(t["notes"])
        for e in t["evidence"]:
            st.caption(f"evidence: {e}")
        k = t["task_id"]
        if t["status"] in ("open", "done"):
            c1, c2 = st.columns([3, 1])
            team = c1.selectbox("Assign team", team_ids, key=f"team-{k}", format_func=lambda i: f"{i} - {team_names[i]}")
            if c2.button("Assign", key=f"assign-{k}") and try_action("assignment", sess.store.assign, k, team):
                st.rerun()
        cols = st.columns(4)
        if t["assigned_team_id"] and cols[0].button("In progress", key=f"prog-{k}") \
                and try_action("status", sess.store.set_status, k, "in_progress"):
            st.rerun()
        if t["status"] != "done" and cols[1].button("Done", key=f"done-{k}") \
                and try_action("status", sess.store.set_status, k, "done"):
            st.rerun()
        if t["assigned_team_id"] and cols[2].button("Release team", key=f"rel-{k}") \
                and try_action("release", sess.store.release, k):
            st.rerun()
        if t["status"] == "done" and cols[3].button("Reopen", key=f"reopen-{k}") \
                and try_action("reopen", sess.store.set_status, k, "open"):
            st.rerun()
        c1, c2 = st.columns([3, 1])
        question = c1.text_input("Blocking question", key=f"bq-{k}", placeholder="what is outstanding?")
        if t["status"] != "done" and c2.button("Block", key=f"block-{k}") and try_action("block", sess.block, k, question):
            st.rerun()
        for i, q in enumerate(t["blocking_questions"]):
            if q["answer"] is not None:
                st.caption(f"Q: {q['question']} - A: {q['answer']}")
                continue
            a1, a2 = st.columns([3, 1])
            answer = a1.text_input(f"Answer: {q['question']}", key=f"ans-{k}-{i}")
            if a2.button("Answer", key=f"ansbtn-{k}-{i}") and answer.strip() \
                    and try_action("answer", sess.store.answer_question, k, i, answer.strip()):
                st.rerun()
        d1, d2, d3 = st.columns([2, 2, 1])
        deadline = d1.text_input("Deadline (ISO 8601 UTC)", value=t["deadline_at"] or "", key=f"dl-{k}")
        basis = d2.text_input("Deadline basis", value=t["deadline_basis"] or "", key=f"dlb-{k}")
        if d3.button("Set deadline", key=f"dlbtn-{k}") and try_action("deadline", sess.set_deadline, k, deadline, basis):
            st.rerun()


def render_evacuation_control(sess: Session, asset: dict) -> None:
    """Analyst-entered total evacuation duration with its source: persisted as a confirmed override."""
    aid = asset["asset_id"]
    with st.form(f"evac-{aid}", clear_on_submit=False):
        st.markdown("**Set evacuation duration (min) + source**")
        c1, c2 = st.columns([1, 2])
        current = asset.get("evacuation_min")
        minutes = c1.number_input("Total evacuation duration (min)", min_value=0.0, step=5.0,
                                  value=float(current) if current is not None else 0.0)
        source = c2.text_input("Source (who confirmed it, how)", placeholder="phone call with the director, evacuation plan ...")
        snippet = st.text_input("Quoted evidence (optional)", placeholder="'the full evacuation takes about two hours'")
        confidence = st.selectbox("Confidence", ["low", "medium", "high"], index=1)
        if st.form_submit_button("Confirm evacuation duration and re-rank") and try_action(
                "evacuation override", sess.set_evacuation, aid, minutes, source, snippet, confidence):
            st.rerun()


def render_selected(sess: Session, asset: dict) -> None:
    aid = asset["asset_id"]
    st.subheader(f"{asset['name']} - {asset['asset_type']} - {fmt_window(asset)}")
    st.caption(f"`{aid}` - {asset.get('municipality') or 'municipality unknown'} - distance "
               f"{fmt(asset.get('distance_to_fire_m'))} m - intersects {asset.get('intersects_fire')} - people {people(asset)}"
               f" ({asset.get('occupancy_basis') or 'no basis'}) - policy {asset['priority_policy_version']}")
    left, right = st.columns(2)
    with left:
        st.markdown("**Timing breakdown** (window = arrival - evacuation - buffer, relative to now)")
        for r in asset["priority_reasons"]:
            st.write(f"- {r}")
        st.dataframe(components_frame(asset), width="stretch", hide_index=True)
        value = value_frame(asset)
        if value is not None:
            st.markdown("**People and euros at risk** (estimate; the policy note is in Sources below)")
            st.dataframe(value, width="stretch", hide_index=True)
        render_evacuation_control(sess, asset)
        age = priority.input_age(asset, sess.clock())
        st.markdown(f"**Input age**: oldest observed `{age['oldest_observed_at'] or 'unknown'}` "
                    f"({fmt_age(age['oldest_observed_age_s'])}); newest fetched `{age['newest_fetched_at'] or 'unknown'}` "
                    f"({fmt_age(age['newest_fetched_age_s'])}). Recalculation never makes stale data fresh.")
        forecast = asset.get("forecast_source")
        st.caption(f"forecast: {forecast}" if forecast else "forecast unavailable")
        st.markdown("**Sources**")
        st.dataframe(sources_frame(asset), width="stretch", hide_index=True)
        overrides = sess.store.overrides(aid)
        if overrides:
            st.markdown("**Confirmed overrides**")
            st.dataframe(pd.DataFrame(overrides)[["override_id", "field", "value", "previous", "source", "confidence",
                                                  "confirmed_at", "proposal_id"]], width="stretch", hide_index=True)
    with right:
        st.markdown("**Agent: evidence, proposals, questions**")
        render_agent(sess, asset)
        st.markdown("**Tasks for this asset**")
        with st.form(f"create-{aid}", clear_on_submit=True):
            action = st.selectbox("Action", list(config.TASK_ACTIONS))
            reason = st.text_input("Reason")
            notes = st.text_area("Notes (access concern, destination note, ...)", height=68)
            if st.form_submit_button("Create task") and try_action(
                    "create task", sess.create_task, aid, action, reason.strip() or action.replace("_", " "), notes):
                st.rerun()
        teams = sess.store.teams()
        team_ids = [t["team_id"] for t in teams]
        team_names = {t["team_id"]: t["name"] for t in teams}
        for t in sess.store.tasks(asset_id=aid):
            render_task(sess, t, team_ids, team_names)



# ----------------------------------------------------------------------------- dashboard shell
ACTION_BUTTONS = (
    ("Notify fire dept", "request_resources", True),
    ("Contact facility", "contact_facility", False),
    ("Check access", "check_access", False),
    ("Confirm occupancy", "confirm_occupancy", False),
)


def selected_asset_id(sess: Session) -> str | None:
    """The analyst's selection, dropped when it is not in the snapshot on screen (nothing by default)."""
    aid = st.session_state.get("selected_asset")
    return aid if any(a["asset_id"] == aid for a in sess.assets_in_order()) else None


def select_asset(asset_id: str | None) -> None:
    st.session_state.selected_asset = asset_id


def render_header(sess: Session, s: dict) -> None:
    """The header bar on the dark shell, with the sequence's forward control at its right."""
    head, action = st.columns([0.86, 0.14], vertical_alignment="center")
    head.html(ui_theme.header_html(
        product="Respons'Ara", tagline=TAGLINE, mode=s["input_mode"] or "unknown",
        status=s["data_status_now"] or "unknown", status_tone=STATUS_COLOUR.get(s["data_status_now"], "grey"),
        as_of=stamp(s["as_of"]), computed_at=stamp(s["computed_at"])))
    with action.container(key="ra-next"):
        if st.button("Next update →", width="stretch", disabled=not sess.has_next, key="ra-next-btn",
                     help="Apply the next snapshot of this scenario (the sidebar has Previous and the "
                          "time-travel slider)."):
            sess.next_update()
            st.rerun()


def render_change_log(sess: Session, events: list[dict]) -> None:
    st.caption(f"{len(events)} store events, {len(sess.workbench.change_log)} agent lines.")
    st.dataframe(pd.DataFrame(events[::-1]), width="stretch", hide_index=True)
    for line in reversed(sess.workbench.change_log):
        st.write(f"- {line}")


def render_map_card(sess: Session, s: dict, c: dict) -> None:
    """The map card: chips overlaid on a satellite basemap, fire footprint and located assets."""
    deck, unlocated = build_deck(sess)
    with st.container(key="racard-map"):
        with st.container(key="ra-mapchips"):
            chip, log = st.columns([0.7, 0.3], vertical_alignment="center")
            chip.html(ui_theme.map_chip_html("Response map - live exposure radius"))
            events = sess.store.events()
            with log.popover(f"Change log ({len(events)})"):
                render_change_log(sess, events)
        st.pydeck_chart(deck, width="stretch", height=520)
    st.caption(f"{c['assets'] - unlocated} located assets shown; {unlocated} unlocated assets are not on the map "
               f"(see needs-review queue). Colour by remaining window: red = exhausted (<= 0 min), orange = under "
               f"{SMALL_WINDOW_MIN} min, yellow = {SMALL_WINDOW_MIN} min or more, grey = unranked (no forecast or "
               f"evacuation estimate). Fire: {s['fire_geometry_kind']} from {s['fire_source']}. {SATELLITE_CREDIT}")


def render_action_row(sess: Session, asset: dict | None) -> None:
    """Work the analyst can start on the selected location: the four declared task actions, then the
    investigation agent. Every button writes to the task store, so none of them is a mock."""
    cols = st.columns(len(ACTION_BUTTONS) + 1)
    disabled = asset is None
    for col, (label, action, primary) in zip(cols, ACTION_BUTTONS):
        if col.button(label, key=f"act-{action}", width="stretch", disabled=disabled,
                      type="primary" if primary else "secondary",
                      help=f"Create a {action.replace('_', ' ')} task for the selected location "
                           f"(needs {', '.join(config.TASK_ACTIONS[action])})."):
            if try_action("create task", sess.create_task, asset["asset_id"], action, action.replace("_", " ")):
                st.rerun()
    live = llm_available()
    if cols[-1].button("Investigate location →", key="act-investigate", width="stretch",
                       disabled=disabled or not (asset or {}).get("review_reasons"),
                       help="Run one bounded investigation on the selected location's review flags "
                            + ("(live LLM)" if live else "(FakeLLM: no LLM API key)")):
        try:
            sess.investigate(asset["asset_id"], live=live)
        except Exception as e:                  # LLM/network failure leaves the question answerable manually
            st.error(f"investigation failed ({type(e).__name__}: {e}); answer the open items manually")
        st.rerun()
    if disabled:
        st.caption("Select a location to start work on it. Scenario, time travel, policy and the team roster "
                   "are in the sidebar (top left).")


def render_priority_card(c: dict) -> None:
    body = ui_theme.stat_summary_html([(c["window_exhausted"], "window exhausted", "red"),
                                       (c["ranked"], "ranked to contact", "amber"),
                                       (c["needs_review"], "needs review", "orange")])
    st.html(ui_theme.card_html(
        "Response priority summary", body,
        foot=f"{c['flagged']} ranked assets still carry review flags - {c.get('strategic', 0)} strategic, "
             f"{c.get('criticality_unassessed', 0)} unassessed - {c['assets']} assets in the snapshot."))


def render_escalation_card(sess: Session) -> None:
    """The task pipeline and what is waiting on the analyst: proposals and open questions."""
    counts = escalation_counts(sess.store.tasks())
    body = ui_theme.tile_row_html([(counts["done"], "confirmed done", "green"),
                                   (counts["open"], "open", "orange"),
                                   (counts["blocked"], "blocked", "blue")])
    rows = [ui_theme.dot_row_html(f"{sess.asset(p['asset_id'])['name']} - {p['field']}", "amber",
                                  right="proposal", right_tone="amber")
            for p in sess.pending_proposals()]
    rows += [ui_theme.dot_row_html(f"{sess.asset(q['asset_id'])['name']} - {q['question']}", "blue",
                                   right="question", right_tone="blue")
             for q in sess.open_questions()]
    body += ui_theme.sub_heading_html("Awaiting confirmation")
    body += ui_theme.scroll_html("".join(rows)) if rows else ui_theme.empty_state_html(
        "Nothing is waiting on an analyst confirmation.")
    st.html(ui_theme.card_html("Escalation & notification status", body,
                               foot="Tasks are recommendations with a team capability and a deadline basis, "
                                    "never a dispatch order."))


def render_approve_all_toggle(sess: Session) -> None:
    """The preview switch, read beside the ranked list. `Session.set_approve_all` applies every pending
    proposal in memory and rescores; nothing here confirms a proposal or writes to the store."""
    on = bool(st.toggle(APPROVE_ALL_LABEL, value=bool(sess.approve_all), key="ra-approve-all",
                        help=APPROVE_ALL_HELP))
    if on != bool(sess.approve_all):
        sess.set_approve_all(on)
        st.rerun()


def render_ranked_card(sess: Session, current: str | None) -> None:
    """Ranked locations: the rank number is the button that selects the location. The approve-all
    preview sits on the heading, where the order it might change is read."""
    ranked = sess.scored["ranked"]
    report = sess.preview_report
    with st.container(key="racard-ranked"):
        title, control = st.columns([0.46, 0.54], vertical_alignment="center")
        title.html(ui_theme.card_title_html("Ranked locations - analyst priority", len(ranked)))
        with control:
            render_approve_all_toggle(sess)
        brief = approve_all_caption(report, brief=True)
        if brief:
            st.caption(brief)
        if not ranked:
            st.html(ui_theme.empty_state_html("No location has both a forecast arrival and an evacuation estimate."))
        with st.container(key="ra-ranklist", height=300, border=False):
            for a in ranked:
                num, row = st.columns([0.09, 0.91], vertical_alignment="center")
                with num.container(key=f"rarank-{a['asset_id']}"):
                    if st.button(str(a["priority_rank"]), key=f"rankbtn-{a['asset_id']}",
                                 help=f"Show {a['name']}"):
                        select_asset(a["asset_id"])
                        st.rerun()
                row.html(ui_theme.ranked_row_html(
                    name=a["name"], subtitle=location_subtitle(a), value=f"{a['slack_min']:.0f} min",
                    segments=window_segments(a), value_tone=window_tone(a),
                    selected=a["asset_id"] == current))
        foot = ("Smallest remaining evacuation window first. The bar is that window's arithmetic: "
                "evacuation duration, buffer and the window that is left.")
        if brief:
            foot += " " + SORT_KEY_NOTE
        st.html(f'<div class="ra-card-foot">{ui_theme.esc(foot)}</div>')


def render_selected_card(sess: Session, current: str | None) -> None:
    assets = sess.assets_in_order()
    labels = {a["asset_id"]: f"{a['name']} ({a['asset_type']}; {a['queue']})" for a in assets}
    ids = list(labels)
    open_counts = open_task_counts(sess)
    with st.container(key="racard-selected"):
        st.html(ui_theme.card_title_html("Selected location"))
        if current is None:
            st.html(ui_theme.empty_state_html("Select a location from the list, or a pin on the map."))
        else:
            a = sess.asset(current)
            st.html(ui_theme.detail_html(a["name"], [
                ("priority", fmt_window(a)),
                ("type / municipality", f"{a['asset_type']} - {a.get('municipality') or 'unknown'}"),
                ("distance to fire", f"{fmt(a.get('distance_to_fire_m'))} m"),
                ("predicted arrival", a.get("fire_arrival_at") or "no forecast"),
                ("evacuation duration", f"{fmt(a.get('evacuation_min'))} min"),
                ("people", people(a)),
                ("review flags", ", ".join(a.get("review_reasons") or []) or "none"),
                ("open tasks", open_counts.get(current, 0)),
            ]))
        chosen = st.selectbox("Selected location", ids, format_func=labels.get, label_visibility="collapsed",
                              index=ids.index(current) if current in ids else None,
                              placeholder="Search every location, ranked or not...",
                              key=f"asset-select-{sess.scenario_id}-{current or 'none'}")
        if chosen != current:
            select_asset(chosen)
            st.rerun()


def render_review_card(sess: Session, c: dict) -> None:
    rows = sess.scored["needs_review"]
    body = "".join(ui_theme.dot_row_html(a["name"],
                                         right=", ".join(a.get("review_reasons") or []) or "unranked",
                                         right_tone="orange") for a in rows)
    body = ui_theme.scroll_html(body) if rows else ui_theme.empty_state_html("Every location is ranked.")
    st.html(ui_theme.card_html(
        "Needs-review queue", body, count=c["needs_review"],
        foot=f"{c['forecast_unavailable']} without a forecast arrival, {c['evacuation_unknown']} without an "
             f"evacuation estimate. An investigation queue, not an assertion of highest risk."))


# ----------------------------------------------------------------------------- sections below the fold
def render_detail_sections(sess: Session, s: dict, c: dict, current: str | None) -> None:
    """Everything the two columns do not have room for, kept reachable: the full selected-asset view,
    the tables the ranking is read from, strategic exposure, tasks and the method notes."""
    open_counts = open_task_counts(sess)
    st.html('<div class="ra-section">Detail, tables and method</div>')
    with st.expander("Selected location - full detail, agent, evacuation duration and tasks",
                     expanded=current is not None):
        if current is None:
            st.caption("No location selected: pick one in the ranked list or the selector above.")
        else:
            render_selected(sess, sess.asset(current))

    preview = approve_all_caption(sess.preview_report)
    with st.expander(f"Ranked assets ({c['ranked']})"):
        st.caption("Smallest remaining window first, then earlier predicted arrival, nearer distance, asset id. "
                   "A farther asset can rank higher when the fire reaches it sooner or its evacuation takes longer.")
        if preview:
            st.caption(preview)
        st.dataframe(ranked_frame(sess.scored["ranked"], open_counts, bool(preview)), width="stretch",
                     hide_index=True)
        if sess.scored["flagged"]:
            st.caption(f"Ranked assets that still carry review flags ({len(sess.scored['flagged'])}):")
            st.dataframe(ranked_frame(sess.scored["flagged"], open_counts, bool(preview)), width="stretch",
                         hide_index=True)

    with st.expander(f"Needs-review queue ({c['needs_review']})"):
        st.caption("Unranked: no forecast arrival (forecast_unavailable, a producer gap) or no evacuation estimate "
                   "(evacuation_unknown, confirm with the facility or set it on the selected asset). Unknown "
                   "exposure first, then by known distance. An investigation queue, not an assertion of highest risk.")
        st.dataframe(review_frame(sess.scored["needs_review"], open_counts), width="stretch", hide_index=True)

    strategic = sess.scored.get("strategic") or []
    if strategic or c.get("criticality_unassessed"):
        with st.expander(f"Strategic exposure ({len(strategic)})"):
            st.caption("A separate view, not a contact order: property value never overrides contact urgency "
                       "(readme 6), so nothing here changes the ranked queue. Most critical tier first, "
                       "then the same remaining window. Every tier is an analyst-confirmed proposal from the "
                       f"investigation agent, quoting its evidence; {c.get('criticality_unassessed', 0)} asset(s) "
                       f"are still unassessed. Policy {config.CRITICALITY_POLICY['version']}, assumed.")
            if strategic:
                st.dataframe(strategic_frame(strategic), width="stretch", hide_index=True)

    with st.expander(f"All tasks ({len(sess.store.tasks())})"):
        team_names = {t["team_id"]: t["name"] for t in sess.store.teams()}
        st.dataframe(tasks_frame(sess.store.tasks(), team_names), width="stretch", hide_index=True)

    with st.expander("Value at risk, method and limits"):
        v = s["value_at_risk"]
        if v["layer"]:
            n = st.columns(4)
            n[0].metric("People exposed", f"{v['people_exposed']:g}",
                        help=f"sum of estimated occupancy x burn probability over located assets; "
                             f"{v['excluded_people']} of {v['located']} located assets have no headcount or no "
                             f"burn probability and are left out, never counted as zero")
            n[1].metric("People at risk (p50)", f"{v['people_at_risk_p50']:g}",
                        help=f"whole headcount of every located asset whose remaining evacuation window at the p50 "
                             f"arrival is exhausted; at p10 it is {v['people_at_risk_p10']:g}. No partial clearance "
                             f"is modelled, and {v['excluded_people']} located assets are excluded for want of an input")
            n[2].metric("Expected loss (mid)", f"EUR {v['expected_loss_eur_mid']:,.0f}",
                        help=f"band EUR {v['expected_loss_eur_low']:,.0f} to {v['expected_loss_eur_high']:,.0f} from the "
                             f"class damage ratios; assumed per-class replacement costs ({v['policy_version']}), not a "
                             f"per-asset valuation, and {v['excluded_eur']} of {v['located']} located assets are not valued")
            n[3].metric("Excluded from totals", f"{v['excluded_people']} people / {v['excluded_eur']} eur",
                        help=f"located assets left out of each total because an input is null (no headcount, no burn "
                             f"probability, or a class the policy does not value), so neither total is complete. "
                             f"{v['located']} located assets in all")
        limits = value_limits(v)
        st.caption(value_summary(v, s["as_of"]) + (f" {limits}" if limits else ""))
        st.caption(f"Contact priority is the remaining evacuation window: forecast arrival - total evacuation "
                   f"duration - buffer ({s['buffer_min']} min), relative to the snapshot time {s['now_at']}. A zero "
                   "or negative window means immediate analyst review, not an evacuation instruction. Forecast and "
                   "evacuation estimates are the producer's / policy's inputs, not validated predictions. Arrivals "
                   "whose forecast source says 'labelled enrichment, not validated' come from an uncalibrated spread "
                   "model seeded on the observed perimeter, not from a provider forecast. Recommendations, not orders.")
        st.caption(f"Open tasks {c['open_tasks']} - pending proposals {c['pending_proposals']} - open questions "
                   f"{c['open_questions']} - snapshot `{s['snapshot_id']}` sequence {s['sequence']} of "
                   f"{s['n_sequences']}.")


# ----------------------------------------------------------------------------- main
def main() -> None:
    st.set_page_config(page_title="ResponsAra", layout="wide", initial_sidebar_state="collapsed")
    ui_theme.inject()
    if st.sidebar.toggle("Mock voice scenarios", value=st.query_params.get("demo") == "voice", key="mock_voice_mode"):
        from fireline.voice_demo_panel import render_voice_demo
        render_voice_demo()
        return
    sess = session()
    if sess.scenario_id is None and sess.scenario_ids:
        sess.select_scenario(DEFAULT_SCENARIO if DEFAULT_SCENARIO in sess.scenario_ids else sess.scenario_ids[0])
    sidebar(sess)
    s = sess.status()
    c = s["counts"]
    render_header(sess, s)
    if s["reviewing_earlier"]:
        with st.expander(f"Earlier moment under review - sequence {s['sequence']} of {s['n_sequences']}, view only"):
            st.write(f"Snapshot `{s['snapshot_id']}`, as_of {s['as_of']}. The map, ranking and review queue are "
                     f"recomputed for that moment with your confirmed overrides; tasks, the change log and the "
                     f"store's accepted sequence stay at {s['applied_sequence']}, and no tasks are suggested from "
                     f"it. Work you create here is still recorded, stamped with this snapshot.")

    current = selected_asset_id(sess)
    left, right = st.columns([0.62, 0.38], gap="medium")
    with left:
        render_map_card(sess, s, c)
        render_action_row(sess, sess.asset(current) if current else None)
    with right:
        render_priority_card(c)
        render_escalation_card(sess)
        render_ranked_card(sess, current)
        render_selected_card(sess, current)
        render_review_card(sess, c)
    render_detail_sections(sess, s, c, current)


if __name__ == "__main__":
    main()
