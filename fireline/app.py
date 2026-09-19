"""FireLine analyst screen (readme 9, CONTRACTS 7): map, ranked table (remaining evacuation window),
review queue, selected asset with the timing breakdown and agent proposals, task controls, change log.
One screen; "Next update" walks the snapshot sequence through the store.

    FIRELINE_DB=data/fireline.sqlite .venv/bin/streamlit run fireline/app.py

All workflow logic lives in `fireline.ui_state.Session` (kept in st.session_state); this file renders.
"""

from __future__ import annotations

import pandas as pd
import pydeck as pdk
import streamlit as st

from fireline import config, priority, tasks
from fireline.ui_state import Session, llm_available

st.set_page_config(page_title="FireLine", layout="wide", page_icon=":fire:")

STATUS_COLOUR = {"current": "green", "stale": "orange", "unavailable": "red"}
GREY = [150, 150, 150, 200]
RED, ORANGE, YELLOW = [200, 30, 30, 230], [240, 140, 20, 230], [235, 210, 40, 230]
SMALL_WINDOW_MIN = config.CONTACT_POLICY["attention_min"]   # "small window" threshold shared with task flagging
DEFAULT_SCENARIO = "gavarres_real"      # the real-area scenario opens first; the synthetic one stays selectable


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
    tip = ("simulated burned area (fire-spread run), not an observed perimeter" if geometry_kind == "simulated"
           else "fire perimeter (surveyed/synthetic footprint)")
    return [{"polygon": rings, "tip": tip} for rings in polys]


def build_deck(sess: Session) -> tuple[pdk.Deck, int]:
    snap, status = sess.snapshot, sess.status()
    layers, lons, lats = [], [], []
    geometry = snap.get("fire_geometry")
    if geometry and geometry.get("type") in ("Polygon", "MultiPolygon"):
        rows = polygon_rows(geometry, snap.get("fire_geometry_kind"))
        layers.append(pdk.Layer("PolygonLayer", data=rows, get_polygon="polygon", get_fill_color=[160, 20, 20, 90],
                                get_line_color=[140, 0, 0], line_width_min_pixels=2, stroked=True, filled=True,
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
                                data=[{"lon": x, "lat": y, "tip": "hotspot centre, not a surveyed perimeter"}],
                                get_position=["lon", "lat"], get_radius=600, radius_min_pixels=14, filled=False,
                                stroked=True, get_line_color=[255, 120, 0], line_width_min_pixels=3, pickable=True))
    points = []
    for a in sess.assets_in_order():
        if a.get("latitude") is None or a.get("longitude") is None:
            continue
        lons.append(a["longitude"])
        lats.append(a["latitude"])
        points.append({"lon": a["longitude"], "lat": a["latitude"], "color": window_colour(a),
                       "tip": f"<b>{a['name']}</b> ({a['asset_type']})<br/>{fmt_window(a)}<br/>arrival "
                              f"{a.get('fire_arrival_at') or 'no forecast'}; evacuation {fmt(a.get('evacuation_min'))} min"
                              f"<br/>distance {fmt(a.get('distance_to_fire_m'))} m; people {people(a)}<br/>"
                              f"{', '.join(a.get('review_reasons') or []) or 'no review flags'}"})
    layers.append(pdk.Layer("ScatterplotLayer", data=points, get_position=["lon", "lat"], get_fill_color="color",
                            get_radius=150, radius_min_pixels=6, pickable=True, stroked=True,
                            get_line_color=[40, 40, 40], line_width_min_pixels=1))
    clon = sum(lons) / len(lons) if lons else 3.0
    clat = sum(lats) / len(lats) if lats else 41.9
    view = pdk.ViewState(longitude=clon, latitude=clat, zoom=10.5, pitch=0)
    deck = pdk.Deck(layers=layers, initial_view_state=view, tooltip={"html": "{tip}"}, map_style="light")
    return deck, status["counts"]["unlocated"]


# ----------------------------------------------------------------------------- tables
def open_task_counts(sess: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in sess.open_tasks():
        counts[t["asset_id"]] = counts.get(t["asset_id"], 0) + 1
    return counts


def ranked_frame(assets: list[dict], open_counts: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame([{
        "rank": a["priority_rank"], "name": a["name"], "type": a["asset_type"], "municipality": a.get("municipality"),
        "distance m": a.get("distance_to_fire_m"), "predicted arrival": a.get("fire_arrival_at"),
        "evacuation min": a.get("evacuation_min"), "latest start (min from now)": a.get("latest_start_min"),
        "remaining window (min)": a.get("slack_min"), "status": a.get("priority_status"),
        "review flags": ", ".join(a.get("review_reasons") or []), "people": people(a),
        "open tasks": open_counts.get(a["asset_id"], 0), "asset_id": a["asset_id"],
    } for a in assets])


def review_frame(assets: list[dict], open_counts: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame([{
        "name": a["name"], "type": a["asset_type"], "municipality": a.get("municipality"),
        "known distance m": a.get("distance_to_fire_m"), "predicted arrival": a.get("fire_arrival_at"),
        "evacuation min": a.get("evacuation_min"), "people": people(a),
        "reasons": ", ".join(a.get("review_reasons") or []),
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


# ----------------------------------------------------------------------------- sidebar
def sidebar(sess: Session) -> None:
    st.sidebar.title("FireLine")
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
    st.sidebar.caption(f"snapshot `{s['snapshot_id']}` - sequence {s['sequence']} of {s['n_sequences']}")
    if st.sidebar.button("Next update", disabled=not sess.has_next, width="stretch", type="primary"):
        sess.next_update()
        st.rerun()
    result = sess.last_update
    if result:
        if not result.get("advanced"):
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
    label = "Investigate (live LLM)" if live else "Investigate (FakeLLM, no ANTHROPIC_API_KEY)"
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


# ----------------------------------------------------------------------------- main
def main() -> None:
    sess = session()
    if sess.scenario_id is None and sess.scenario_ids:
        sess.select_scenario(DEFAULT_SCENARIO if DEFAULT_SCENARIO in sess.scenario_ids else sess.scenario_ids[0])
    sidebar(sess)
    s = sess.status()
    c = s["counts"]
    st.title(f"FireLine - {s['scenario_id']} - {s['as_of']}")
    st.caption(f"Contact priority is the remaining evacuation window: forecast arrival - total evacuation duration "
               f"- buffer ({s['buffer_min']} min), relative to the snapshot time {s['now_at']}. A zero or negative "
               "window means immediate analyst review, not an evacuation instruction. Forecast and evacuation "
               "estimates are the producer's / policy's inputs, not validated predictions. Recommendations, not orders.")
    m = st.columns(6)
    m[0].metric("Ranked", c["ranked"])
    m[1].metric("Window exhausted", c["window_exhausted"], help="remaining window <= 0; review first")
    m[2].metric("Needs review", c["needs_review"],
                help=f"unranked: {c['forecast_unavailable']} without forecast, {c['evacuation_unknown']} without evacuation estimate")
    m[3].metric("Open tasks", c["open_tasks"])
    m[4].metric("Pending proposals", c["pending_proposals"])
    m[5].metric("Open questions", c["open_questions"])

    deck, unlocated = build_deck(sess)
    st.pydeck_chart(deck, width="stretch")
    st.caption(f"{c['assets'] - unlocated} located assets shown; {unlocated} unlocated assets are not on the map "
               f"(see needs-review queue). Colour by remaining window: red = exhausted (<= 0 min), orange = under "
               f"{SMALL_WINDOW_MIN} min, yellow = {SMALL_WINDOW_MIN} min or more, grey = unranked (no forecast or "
               f"evacuation estimate). Fire: {s['fire_geometry_kind']} from {s['fire_source']}.")

    open_counts = open_task_counts(sess)
    st.subheader(f"Ranked assets ({c['ranked']})")
    st.caption("Smallest remaining window first, then earlier predicted arrival, nearer distance, asset id. "
               "A farther asset can rank higher when the fire reaches it sooner or its evacuation takes longer.")
    st.dataframe(ranked_frame(sess.scored["ranked"], open_counts), width="stretch", hide_index=True)
    st.subheader(f"Needs-review queue ({c['needs_review']})")
    st.caption("Unranked: no forecast arrival (forecast_unavailable, a producer gap) or no evacuation estimate "
               "(evacuation_unknown, confirm with the facility or set it on the selected asset). Unknown exposure "
               "first, then by known distance. An investigation queue, not an assertion of highest risk.")
    st.dataframe(review_frame(sess.scored["needs_review"], open_counts), width="stretch", hide_index=True)
    if sess.scored["flagged"]:
        st.caption(f"Ranked assets that still carry review flags ({len(sess.scored['flagged'])}):")
        st.dataframe(ranked_frame(sess.scored["flagged"], open_counts), width="stretch", hide_index=True)

    assets = sess.assets_in_order()
    labels = {a["asset_id"]: f"{a['name']} ({a['asset_type']}; {a['queue']})" for a in assets}
    ids = list(labels)
    default = st.session_state.get("selected_asset")
    chosen = st.selectbox("Selected asset", ids, index=ids.index(default) if default in ids else 0,
                          format_func=labels.get, key="asset_select")
    st.session_state.selected_asset = chosen
    render_selected(sess, sess.asset(chosen))

    st.subheader(f"All tasks ({len(sess.store.tasks())})")
    team_names = {t["team_id"]: t["name"] for t in sess.store.teams()}
    st.dataframe(tasks_frame(sess.store.tasks(), team_names), width="stretch", hide_index=True)

    events = sess.store.events()
    with st.expander(f"Change log ({len(events)} store events, {len(sess.workbench.change_log)} agent lines)"):
        st.dataframe(pd.DataFrame(events[::-1]), width="stretch", hide_index=True)
        for line in reversed(sess.workbench.change_log):
            st.write(f"- {line}")


main()
