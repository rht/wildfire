"""FireLine Streamlit UI (PLAN 6.7): map, asset table, coordinator queue. Reads only data/scenarios/.

    .venv/bin/streamlit run fireline/app.py

Every scenario is precomputed by scripts/precompute.py and keyed by scenario id. Coordinator answers and
override rejections live in st.session_state only; nothing is written to disk from the UI.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from fireline import config
from fireline.exposure import TIER_RANK
from fireline.grid import xy_to_lonlat
from fireline.scenario import Scenario

ROOT = Path(__file__).resolve().parent.parent
SCEN_DIR = ROOT / "data" / "scenarios"
DIRECTOR = "Recommendation for the INFOCAT director"
TIER_RGB = {"act_now": [220, 40, 40], "prepare": [245, 150, 20], "monitor": [130, 130, 130]}
CUT_SOON_MIN = 120

st.set_page_config(page_title="FireLine", layout="wide", page_icon=":fire:")


# ----------------------------------------------------------------------------- data
@st.cache_data(show_spinner=False)
def load_index() -> dict:
    return json.loads((SCEN_DIR / "index.json").read_text())


@st.cache_data(show_spinner=False)
def load_layers(name: str) -> tuple[dict, list[dict]]:
    """Map layers and the burn-probability cells (picklable, shared across sessions).

    Cells are built from the arrival raster rather than a bitmap: Streamlit's pydeck renderer parses
    string props as expressions and rejects a base64 data URI ("Unexpected ':' at character 4")."""
    d = SCEN_DIR / name
    layers = json.loads((d / "layers.json").read_text())
    sc = Scenario.from_json(d / "scenario.json")
    return layers, burn_cells(sc, block=2, min_prob=0.05)


def burn_cells(sc: Scenario, block: int = 2, min_prob: float = 0.05) -> list[dict]:
    """Downsample burn_prob by `block` (max pooling) and return one square polygon per cell above min_prob."""
    if sc.arrival is None:
        return []
    g, bp = sc.arrival.grid, sc.arrival.burn_prob
    nr, nc = (bp.shape[0] // block) * block, (bp.shape[1] // block) * block
    pooled = bp[:nr, :nc].reshape(nr // block, block, nc // block, block).max(axis=(1, 3))
    rows, cols = np.nonzero(pooled >= min_prob)
    size = g.cell * block
    x0 = g.xmin + cols * size
    y1 = g.ymax - rows * size
    lon0, lat1 = (a.tolist() for a in xy_to_lonlat(x0, y1))
    lon1, lat0 = (a.tolist() for a in xy_to_lonlat(x0 + size, y1 - size))
    cells = []
    for i in range(len(rows)):
        p = float(pooled[rows[i], cols[i]])
        cells.append({"polygon": [[lon0[i], lat0[i]], [lon1[i], lat0[i]], [lon1[i], lat1[i]], [lon0[i], lat1[i]]],
                      "color": [255, int(200 * (1 - p)), 0, int(40 + 160 * p)],
                      "tip": f"burn probability {p:.2f}"})
    return cells


def session_scenario(name: str) -> tuple[Scenario, dict, list[dict]]:
    """Per-session mutable Scenario (answers and rejections stay in st.session_state). The Scenario
    itself holds the config module, so it is built once per session rather than put in cache_data."""
    layers, cells = load_layers(name)
    store = st.session_state.setdefault("scenarios", {})
    if name not in store:
        store[name] = Scenario.from_json(SCEN_DIR / name / "scenario.json")
    return store[name], layers, cells


def fmt_min(v) -> str:
    if v is None or (isinstance(v, float) and (math.isinf(v) or math.isnan(v))):
        return "never" if v is not None else "-"
    return f"{v:.0f}"


# ----------------------------------------------------------------------------- map
def build_deck(sc: Scenario, layers: dict, cells: list[dict]) -> pdk.Deck:
    w, s, e, n = layers["bounds_lonlat"]
    burn = pdk.Layer("PolygonLayer", data=cells, get_polygon="polygon", get_fill_color="color", stroked=False,
                     pickable=True)
    perim = pdk.Layer("PolygonLayer", data=[{"polygon": ring, "tip": f"perimeter at {sc.t:%H:%M}Z, "
                                              f"{layers['perimeter_area_ha']} ha"} for ring in layers["perimeter_lonlat"]],
                      get_polygon="polygon", get_fill_color=[120, 0, 0, 120], get_line_color=[120, 0, 0],
                      line_width_min_pixels=2, stroked=True, filled=True, pickable=True)
    roads = []
    for r in layers["roads"]:
        cut = r["cut_min"]
        soon = cut is not None and cut <= CUT_SOON_MIN
        colour = [220, 30, 30] if soon else ([60, 60, 60] if r["closed"] else [150, 150, 150])
        roads.append({"path": r["path"], "color": colour, "width": 4 if soon or r["closed"] else 2,
                      "tip": f"{r['name'] or 'unnamed'} ({r['highway']}) cut in "
                             f"{fmt_min(cut if cut is not None else math.inf)} min" + (" - CLOSED" if r["closed"] else "")})
    roads_layer = pdk.Layer("PathLayer", data=roads, get_path="path", get_color="color", get_width="width",
                            width_units="pixels", pickable=True)
    routes, points = [], []
    for a in sc.assets:
        d = a.get("decision") or {}
        p10 = a["lead_adjusted_p10_min"]
        points.append({"lon": a["lon"], "lat": a["lat"], "color": TIER_RGB.get(a["tier"], [0, 0, 0]),
                       "tip": f"<b>{a['name']}</b> ({a['asset_class']})<br/>tier {a['tier']} - "
                              f"{d.get('decision', '?')}<br/>lead-adjusted p10: {fmt_min(p10)} min<br/>"
                              f"{DIRECTOR}"})
        rt = a.get("route")
        if rt and rt.get("path_lonlat"):
            routes.append({"path": [list(p) for p in rt["path_lonlat"]], "color": TIER_RGB.get(a["tier"]),
                           "tip": f"{a['name']} -> {rt['destination_name']} ({rt['travel_min']:.0f} min, "
                                  f"first cut {rt['first_cut_road'] or '-'} at {fmt_min(rt['first_cut_min'])} min)"})
    routes_layer = pdk.Layer("PathLayer", data=routes, get_path="path", get_color="color", get_width=3,
                             width_units="pixels", pickable=True)
    assets_layer = pdk.Layer("ScatterplotLayer", data=points, get_position=["lon", "lat"], get_fill_color="color",
                             get_radius=180, radius_min_pixels=6, pickable=True, stroked=True,
                             get_line_color=[255, 255, 255], line_width_min_pixels=1)
    clon, clat = layers["centre_lonlat"]
    view = pdk.ViewState(longitude=clon, latitude=clat, zoom=11.3, pitch=0)
    return pdk.Deck(layers=[burn, perim, roads_layer, routes_layer, assets_layer], initial_view_state=view,
                    tooltip={"html": "{tip}"}, map_style="light")


# ----------------------------------------------------------------------------- tables
def asset_frame(sc: Scenario) -> pd.DataFrame:
    rows = []
    for a in sorted(sc.assets, key=lambda r: (TIER_RANK.get(r["tier"], 9), r["lead_adjusted_p10_min"])):
        d = a.get("decision") or {}
        rows.append({
            "name": a["name"], "class": a["asset_class"], "municipality": a["municipality"],
            "occupancy": a.get("occupancy"), "burn_prob": round(a["burn_prob"], 2),
            "p10 min": fmt_min(a["arrival_p10_min"]), "lead-adjusted": fmt_min(a["lead_adjusted_p10_min"]),
            "tier": a["tier"], "decision": d.get("decision"), "staged": d.get("staged"),
            "exit window": fmt_min(d.get("exit_window_min")), "latest departure": fmt_min(d.get("latest_departure_min")),
            "reception centre": d.get("reception_centre"), "medical destination": d.get("medical_destination"),
            "needs_review": ", ".join(a["needs_review"]),
        })
    return pd.DataFrame(rows)


def tier_style(row):
    c = {"act_now": "#f8d0d0", "prepare": "#fbe3c0", "monitor": "#e8e8e8"}.get(row["tier"], "")
    return [f"background-color: {c}" if col == "tier" else "" for col in row.index]


# ----------------------------------------------------------------------------- sidebar
def sidebar(index: dict) -> str:
    st.sidebar.title("FireLine")
    st.sidebar.caption(f"Mode: **{index.get('mode', 'Replay (synthetic)')}**")
    names = [s["name"] for s in index["scenarios"]]
    name = st.sidebar.selectbox("Scenario", names, format_func=lambda n: {
        "synthetic_0800": "08:00 - synthetic ignition", "synthetic_1000": "10:00 - fire advanced, GI-660 closed",
        "whatif_east_1000": "10:00 - what-if: wind from the east"}.get(n, n))
    meta = next(s for s in index["scenarios"] if s["name"] == name)
    st.sidebar.write(f"id `{meta['scenario_id']}` - t {meta['t']}  \nsource: {meta['source']}  \n"
                     f"closures: {', '.join(meta['closures']) or 'none'}")
    with st.sidebar.expander("Lead times (min before arrival)"):
        st.table(pd.Series(config.LEAD_TIME_MIN, name="min"))
    with st.sidebar.expander("Load times (min to board)"):
        st.table(pd.Series(config.LOAD_TIME_MIN, name="min"))
    with st.sidebar.expander("Thresholds and CA"):
        st.table(pd.Series({"ACT_NOW_MIN": config.ACT_NOW_MIN, "PREPARE_MIN": config.PREPARE_MIN,
                            "BURN_PROB_MIN": config.BURN_PROB_MIN, "BURN_PROB_HIGH": config.BURN_PROB_HIGH,
                            "ROUTE_BUFFER_MIN": config.ROUTE_BUFFER_MIN, "DEST_BURN_PROB_MAX": config.DEST_BURN_PROB_MAX,
                            "DEST_MARGIN_MIN": config.DEST_MARGIN_MIN, **{f"CA.{k}": v for k, v in config.CA.items()}},
                           name="value").astype(str))
    with st.sidebar.expander("Legend"):
        st.markdown("Assets: red = act now, orange = prepare, grey = monitor.  \nRoads: red = cut within "
                    f"{CUT_SOON_MIN} min, dark = closed by SCT.  \nShading: burn probability within the horizon.")
    return name


# ----------------------------------------------------------------------------- main
def main() -> None:
    if not (SCEN_DIR / "index.json").exists():
        st.error("No precomputed scenarios. Run `make precompute` first.")
        st.stop()
    index = load_index()
    name = sidebar(index)
    sc, layers, cells = session_scenario(name)

    st.title(f"FireLine - {sc.cluster_id} at {sc.t:%Y-%m-%d %H:%M} UTC")
    st.caption(f"Wind from {layers['wind_dir_deg']:.0f} deg at {layers['wind_speed_mps']:.0f} m/s - spread source "
               f"`{sc.spread_source}` - horizon {sc.horizon_min} min - perimeter {layers['perimeter_area_ha']} ha - "
               f"p50 burned at horizon about {layers['burned_ha_p50_at_horizon']:.0f} ha")
    counts = {t: sum(1 for a in sc.assets if a["tier"] == t) for t in TIER_RANK}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Act now", counts.get("act_now", 0))
    c2.metric("Prepare", counts.get("prepare", 0))
    c3.metric("Monitor", counts.get("monitor", 0))
    c4.metric("Open questions", sum(1 for e in sc.queue if e["status"] == "open"))

    st.pydeck_chart(build_deck(sc, layers, cells), width="stretch")

    st.subheader(f"Assets by tier - {DIRECTOR}")
    st.caption("director del pla / alcalde orders, CECAT sends. Times in minutes after t.")
    df = asset_frame(sc)
    st.dataframe(df.style.apply(tier_style, axis=1), width="stretch", hide_index=True)

    st.subheader(f"Coordinator queue - {DIRECTOR}")
    open_q = [e for e in sc.queue if e["status"] == "open"]
    if not open_q:
        st.success("No open questions.")
    for e in open_q:
        asset = sc.asset(e["asset_id"])
        cols = st.columns([5] + [1] * len(e["options"]))
        cols[0].markdown(f"**{asset['name']}** ({asset['tier']}): {e['question']}  \n"
                         f"<small>default applied: *{e['default']}* - id `{e['escalation_id']}`</small>",
                         unsafe_allow_html=True)
        for i, opt in enumerate(e["options"]):
            if cols[i + 1].button(opt, key=f"{name}-{e['escalation_id']}-{opt}",
                                  type="primary" if opt == e["default"] else "secondary"):
                sc.answer_escalation(e["escalation_id"], opt)
                st.rerun()
    answered = [e for e in sc.queue if e["status"] != "open"]
    if answered:
        with st.expander(f"Answered ({len(answered)})"):
            for e in answered:
                st.write(f"- `{e['escalation_id']}` {e['question']} -> **{e['answer']}**")

    with st.expander(f"Change log ({len(sc.change_log)})", expanded=False):
        for line in sc.change_log:
            st.write(f"- {line}")

    with_over = [a for a in sc.assets if a.get("overrides")]
    if with_over:
        st.subheader(f"Overrides and evidence - {DIRECTOR}")
    for a in with_over:
        with st.expander(f"{a['name']} - {len(a['overrides'])} override(s), tier {a['tier']}"):
            for i, o in enumerate(list(a["overrides"])):
                c = st.columns([6, 1])
                c[0].markdown(f"**{o['field']}**: {o.get('previous')} -> {o['value']} ({o.get('direction')}, "
                              f"confidence {o.get('confidence')})  \nsource: {o.get('source')} at {o.get('fetched_at')}  \n"
                              f"> {o.get('quoted_snippet', '')}")
                if c[1].button("reject", key=f"{name}-rej-{a['asset_id']}-{i}"):
                    a["overrides"].remove(o)
                    if "previous" in o and o["field"] in a:
                        a[o["field"]] = o["previous"]
                    sc._redecide(a, f"coordinator rejected override {o['field']}={o['value']}")
                    sc.change_log.append(f"{a['asset_id']}: override {o['field']}={o['value']} rejected by coordinator")
                    st.rerun()

    if index.get("diff_0800_1000"):
        with st.expander("Diff 08:00 -> 10:00"):
            for line in index["diff_0800_1000"]:
                st.write(f"- {line}")


main()
