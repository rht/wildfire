"""Guards on the Streamlit page source itself (fireline/app.py).

Streamlit's "magic" renders every bare expression statement in the script, so a statement written
only for its side effects (`a.append(x), b.append(y)` evaluates to a tuple) is printed to the page.
That once filled the analyst screen with "None None" lines, one per map point."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import tomllib

from tests.helpers import make_asset

from fireline import config, ui_state

APP = Path(__file__).resolve().parent.parent / "fireline" / "app.py"


def test_importing_display_helpers_does_not_start_a_streamlit_session():
    code = '''
from unittest.mock import patch
with patch('streamlit.set_page_config', side_effect=AssertionError('page rendered during import')), \\
     patch('fireline.ui_state.Session', side_effect=AssertionError('session opened during import')):
    from fireline.app import fmt
    assert fmt(None) == '-'
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_no_bare_expressions_streamlit_magic_would_render():
    source = APP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rendered = [
        (node.lineno, ast.get_source_segment(source, node).splitlines()[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and not isinstance(node.value, ast.Call)
        and not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))  # docstrings
    ]
    assert not rendered, f"bare expressions Streamlit magic would print: {rendered}"


def test_default_scenario_is_the_real_area_one_and_is_discoverable():
    """`make demo` opens on the real Gavarres scenario; the synthetic one stays selectable."""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    default = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                   and getattr(node.targets[0], "id", None) == "DEFAULT_SCENARIO")
    scenarios, _ = ui_state.discover_snapshots()
    assert default == "gavarres_real" and default in scenarios
    assert "synthetic_gavarres" in scenarios


def test_sequence_controls_step_back_to_an_earlier_snapshot_without_rewinding_the_store(tmp_path, monkeypatch):
    """The page's own widgets: Next update advances, Previous steps back to the earlier moment as a
    labelled view, and the slider scrubs to any snapshot. Streamlit keeps widget state by key, so this
    also guards the sequence slider against overriding a Previous/Next click on the next rerun."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("FIRELINE_DB", str(tmp_path / "app.sqlite"))
    at = AppTest.from_file(str(APP), default_timeout=180).run()
    assert not at.exception
    previous, next_update = at.sidebar.button[0], at.sidebar.button[1]
    assert (previous.label, next_update.label) == ("Previous", "Next update")
    assert previous.disabled and not next_update.disabled            # first snapshot: nowhere to go back to
    first, second = at.sidebar.select_slider[0].options[:2]
    assert first.startswith("1 - ") and second.startswith("2 - ")    # sequence and as_of label each step
    opening_title = at.title[0].value
    assert not at.warning

    at = at.sidebar.button[1].click().run()                          # Next update: applied, no review banner
    assert not at.exception and at.sidebar.select_slider[0].value == second and not at.warning

    at = at.sidebar.button[0].click().run()                          # Previous: an earlier moment, view only
    assert not at.exception and at.title[0].value == opening_title
    assert at.sidebar.select_slider[0].value == first
    assert any("store stays at 2" in w.value for w in at.warning)          # sidebar notice, no body banner
    assert any("Earlier moment under review" in e.label for e in at.expander)

    at = at.sidebar.select_slider[0].set_value(second).run()         # the slider scrubs back to the update
    assert not at.exception and at.sidebar.select_slider[0].value == second
    assert not at.warning


def test_the_header_carries_the_same_step_pair_as_the_sidebar(tmp_path, monkeypatch):
    """The page header holds Previous beside Next update: dead on the first snapshot, and after a
    forward step it takes the session back to the earlier moment. The sidebar pair is untouched."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("FIRELINE_DB", str(tmp_path / "header.sqlite"))
    at = AppTest.from_file(str(APP), default_timeout=180).run()
    assert not at.exception
    assert at.button(key="ra-prev-btn").label == "← Previous"
    assert at.button(key="ra-prev-btn").disabled            # sequence 1: nowhere to step back to
    assert not at.button(key="ra-next-btn").disabled
    first, second = at.sidebar.select_slider[0].options[:2]

    at = at.button(key="ra-next-btn").click().run()          # forward, from the header
    assert not at.exception and at.sidebar.select_slider[0].value == second
    assert not at.button(key="ra-prev-btn").disabled

    at = at.button(key="ra-prev-btn").click().run()          # back, from the header
    assert not at.exception and at.sidebar.select_slider[0].value == first
    assert any("store stays at 2" in w.value for w in at.warning)   # an earlier moment, view only
    assert [b.label for b in at.sidebar.button[:2]] == ["Previous", "Next update"]


# --------------------------------------------------------------- dashboard display helpers
def test_window_segments_are_the_window_components_not_a_new_score():
    """The ranked row's bar re-draws the snapshot's own arithmetic: evacuation + buffer + window."""
    from fireline.app import window_segments

    a = make_asset(window_components={"evacuation_min": 90.0, "buffer_min": 30.0}, evacuation_min=90.0)
    a.update(slack_min=62.0, time_to_impact_min=182.0)
    assert window_segments(a) == [90.0, 30.0, 62.0]
    assert sum(window_segments(a)) == a["time_to_impact_min"]
    assert window_segments(make_asset()) == [None, None, None]     # no components: an empty bar


def test_window_tone_matches_the_three_bands_the_map_paints():
    from fireline.app import SMALL_WINDOW_MIN, window_tone

    ranked = dict(queue="ranked", priority_status="window_open")
    assert window_tone({**ranked, "slack_min": -5.0, "priority_status": "window_exhausted"}) == "red"
    assert window_tone({**ranked, "slack_min": SMALL_WINDOW_MIN - 1}) == "orange"
    assert window_tone({**ranked, "slack_min": SMALL_WINDOW_MIN + 1}) == "ink"
    assert window_tone({"queue": "needs_review", "slack_min": None}) == "grey"


def test_location_subtitle_reads_type_and_distance_and_says_when_there_is_none():
    from fireline.app import location_subtitle

    assert location_subtitle({"asset_type": "school", "distance_to_fire_m": 1400.4}) == "school - 1,400 m to fire"
    assert location_subtitle({"asset_type": "masia"}) == "masia - distance unknown"


def test_stamp_shortens_a_timestamp_and_keeps_anything_it_cannot_parse():
    from fireline.app import stamp

    assert stamp("2026-07-03T13:20:01.981000+00:00") == "13:20Z 2026-07-03"
    assert stamp(None) == "-" and stamp("not a time") == "not a time"


def test_escalation_counts_split_the_task_pipeline():
    from fireline.app import escalation_counts

    rows = [{"status": s} for s in ("open", "assigned", "in_progress", "blocked", "done", "done")]
    assert escalation_counts(rows) == {"done": 2, "blocked": 1, "open": 3}
    assert escalation_counts([]) == {"done": 0, "blocked": 0, "open": 0}


# --------------------------------------------------------------- value at risk (handoff 002)
def var_status(**totals) -> dict:
    """A `Session.status()` value_at_risk block with the layer on."""
    v = {"layer": True, "people_exposed": 100.0, "people_at_risk_p50": 0, "people_at_risk_p10": 0,
         "expected_loss_eur_low": 300_000, "expected_loss_eur_mid": 800_000, "expected_loss_eur_high": 1_600_000,
         "located": 10, "excluded_people": 2, "excluded_eur": 1, "covered": 10, "reached": 10,
         "horizon_at": "2026-07-03T20:00:00+00:00", "enrichment": False,
         "policy_version": config.VALUE_AT_RISK_POLICY["version"]}
    v.update(totals)
    return v


def test_display_columns_show_the_layer_and_fall_back_to_a_dash_without_it():
    from fireline.app import at_risk, exposed, loss_eur

    with_layer = make_asset(value_at_risk=True, estimated_occupancy=200, burn_probability=0.5, capacity=None,
                            forecast_source="fixture:test", evacuation_min=90.0, evacuation_source="policy",
                            arrival_p10_at="2026-07-03T09:00:00+00:00")
    assert exposed(with_layer) == "100"
    assert at_risk(with_layer) == "0 / 200"       # p50 not reached inside the horizon, p10 exhausted
    assert loss_eur(with_layer) == "EUR 800,000 (300,000 - 1,600,000)"
    without = make_asset()
    assert exposed(without) == "-" and at_risk(without) == "-" and loss_eur(without) == "-"
    nucleus = make_asset(value_at_risk=True, asset_type="nucleus", estimated_occupancy=50, burn_probability=0.4,
                         capacity=None, forecast_source="fixture:test")
    assert loss_eur(nucleus) == "not valued" and exposed(nucleus) == "20"


def test_euro_column_is_a_display_string_so_it_cannot_order_the_table():
    """The ranked frame keeps its window order and the euro column is text, not a number to sort on."""
    from fireline.app import LOSS_COLUMN, ranked_frame

    rows = []
    for i, (occ, bp) in enumerate(((10, 0.9), (400, 0.1)), start=1):
        a = make_asset(value_at_risk=True, asset_id=f"fixture:{i}", name=f"Asset {i}", capacity=None,
                       estimated_occupancy=occ, burn_probability=bp, forecast_source="fixture:test",
                       evacuation_min=90.0, evacuation_source="policy")
        a.update(priority_rank=i, priority_status="window_open", slack_min=float(i), latest_start_min=float(i),
                 time_to_impact_min=float(i), queue="ranked", priority_reasons=[])
        rows.append(a)
    frame = ranked_frame(rows, {})
    assert list(frame["rank"]) == [1, 2]                                   # window order, untouched
    assert frame[LOSS_COLUMN].map(type).eq(str).all()
    assert "estimate" in LOSS_COLUMN and "assumed replacement cost" in LOSS_COLUMN
    assert {"people exposed", "people at risk p50 / p10"} <= set(frame.columns)


def test_review_frame_carries_the_layer_columns_too():
    from fireline.app import LOSS_COLUMN, review_frame

    a = make_asset(value_at_risk=True, estimated_occupancy=200, burn_probability=0.5, capacity=None,
                   forecast_source="fixture:test", review_reasons=["forecast_unavailable"])
    a.update(priority_reasons=["needs review: forecast_unavailable; not ranked"])
    frame = review_frame([a], {})
    assert {"people exposed", "people at risk p50 / p10", LOSS_COLUMN} <= set(frame.columns)


def test_value_frame_shows_the_arithmetic_or_nothing_at_all():
    from fireline.app import value_frame

    assert value_frame(make_asset()) is None                               # layer off: no panel
    a = make_asset(value_at_risk=True, estimated_occupancy=200, burn_probability=0.5, capacity=None,
                   forecast_source="fixture:test", evacuation_min=90.0, evacuation_source="policy",
                   arrival_p10_at="2026-07-03T09:00:00+00:00")
    frame = value_frame(a)
    components = list(frame["component"])
    assert components == ["people exposed", "replacement value", "damage ratio low / mid / high",
                          "expected loss", "people at risk (p50)", "people at risk (p10)"]
    text = " ".join(frame["basis / source"])
    assert "occupancy 200 x burn probability 0.5" in text
    assert "window exhausted at arrival 2026-07-03T09:00:00+00:00" in text   # p10 fired, with its quantile
    assert "does not reach it inside its horizon" in text                    # p50 is null: covered, not reached


def test_header_reads_a_forecast_that_reaches_nothing_as_a_zero_not_a_gap():
    from fireline.app import value_summary

    zero = value_summary(var_status(people_exposed=0, expected_loss_eur_low=0, expected_loss_eur_mid=0,
                                    expected_loss_eur_high=0, reached=0, covered=99, located=99),
                         "2026-07-03T08:00:00+00:00")
    assert zero.startswith("0 exposed within the 12 h forecast horizon")     # derived, not hardcoded
    assert "not a missing forecast" in zero and "forecast_unavailable" in zero
    gap = value_summary(var_status(), "2026-07-03T08:00:00+00:00")
    assert "people exposed within the 12 h forecast horizon" in gap
    assert "2 are left out of the people totals and 1 out of the euro totals" in gap
    off = value_summary({"layer": False}, "2026-07-03T08:00:00+00:00")
    assert "Value at risk is off for this scenario" in off


def test_header_limits_state_the_assumptions_and_the_ca_bias():
    from fireline.app import value_limits

    text = value_limits(var_status())
    for phrase in ("per-class assumptions", "damage-ratio band only", "total economic loss",
                   "does not model partial clearance", "never enter the ranking, the sort or a filter",
                   "no euro figure for lives"):
        assert phrase in text, phrase
    assert "uncalibrated CA ensemble" not in text                            # a provider forecast: no CA caveat
    assert "uncalibrated CA ensemble" in value_limits(var_status(enrichment=True))
    assert value_limits({"layer": False}) == ""


# --------------------------------------------------------------- satellite basemap (map card)
STYLE_FILE = APP.parent / "static" / "esri-world-imagery-style.json"
CONFIG_TOML = APP.parent.parent / ".streamlit" / "config.toml"


class DeckSession:
    """The three members `build_deck` reads, without a store or a database behind them."""

    def __init__(self, assets):
        self._assets = assets
        self.snapshot = {"fire_geometry": {"type": "Point", "coordinates": [3.0, 41.9]},
                         "fire_geometry_kind": "hotspot"}

    def status(self):
        return {"counts": {"unlocated": 0}}

    def assets_in_order(self):
        return self._assets


def test_map_basemap_is_the_esri_satellite_style_not_streamlits_carto_substitute():
    """The imagery is the deck's basemap style. It cannot be a deck TileLayer: deck.gl renders tile
    sub-layers with a GeoJsonLayer unless `renderSubLayers` is replaced by a function returning a
    BitmapLayer, and the deck.gl JSON that st.pydeck_chart speaks cannot carry a function - so a
    TileLayer of raster tiles draws nothing at all. Leaving mapStyle unset is no good either: the
    frontend then substitutes a Carto street basemap, which is the grey map this replaces."""
    from fireline.app import SATELLITE_STYLE, build_deck
    deck, unlocated = build_deck(DeckSession([make_asset()]))
    spec = json.loads(deck.to_json())
    assert spec["mapStyle"] == SATELLITE_STYLE and spec["mapProvider"] == "maplibre"
    assert unlocated == 0
    kinds = [layer["@@type"] for layer in spec["layers"]]
    assert kinds == ["ScatterplotLayer", "ScatterplotLayer"], kinds   # hotspot ring, then the assets


def test_satellite_style_document_declares_the_credited_esri_imagery_tiles():
    from fireline.app import SATELLITE_CREDIT, SATELLITE_STYLE, SATELLITE_URL
    assert SATELLITE_STYLE == f"app/static/{STYLE_FILE.name}"
    style = json.loads(STYLE_FILE.read_text(encoding="utf-8"))
    source = style["sources"]["esri-world-imagery"]
    assert style["version"] == 8 and source["type"] == "raster"
    assert source["tiles"] == [SATELLITE_URL] and "arcgisonline.com" in SATELLITE_URL
    assert [l["source"] for l in style["layers"] if l["type"] == "raster"] == ["esri-world-imagery"]
    for credit in (source["attribution"], SATELLITE_CREDIT):    # the source the imagery really comes from
        assert "Esri" in credit and "Maxar" in credit and "Earthstar Geographics" in credit


def test_static_serving_is_enabled_so_the_style_url_resolves():
    """Streamlit serves `static/` beside the main script at `app/static/`, but only with
    server.enableStaticServing set - without it the style URL 404s and no basemap loads."""
    assert STYLE_FILE.parent == APP.parent / "static"
    assert tomllib.loads(CONFIG_TOML.read_text(encoding="utf-8"))["server"]["enableStaticServing"] is True


# --------------------------------------------------------------- per-asset custom valuation
def valued_asset(**payload):
    """A `research_facility` with an analyst-confirmed bespoke valuation, built the way the page gets
    one: through `priority.apply_overrides` with `field="custom_valuation"`, so the six snapshot keys
    and the re-derived euros come from the real guard rather than from a hand-written dict."""
    from fireline import priority

    value = {"method": "component_replacement",
             "components": [{"label": "computing installation", "amount_eur": 9_000_000},
                            {"label": "building shell", "amount_eur": 3_000_000}],
             "amount_eur_low": 8_000_000, "amount_eur_mid": 12_000_000, "amount_eur_high": 20_000_000,
             "note": "the machines dominate the shell"}
    value.update(payload)
    asset = make_asset(asset_id="a:rf", asset_type="research_facility", value_at_risk=True,
                       estimated_occupancy=120, burn_probability=0.4, capacity=None,
                       forecast_source="fixture:test", evacuation_min=90.0, evacuation_source="policy",
                       review_reasons=["valuation_unassessed"])
    return priority.apply_overrides([asset], [{
        "asset_id": "a:rf", "field": "custom_valuation", "override_id": "ovr-1", "value": value,
        "source": "ca.wikipedia.org", "snippet": "el centre allotja el supercomputador",
        "confidence": "medium", "confirmed_at": "2026-07-03T12:00:00+00:00",
    }], config, now_at="2026-07-03T08:00:00+00:00")[0]


def test_value_frame_names_the_bespoke_figure_as_an_assumption_that_replaced_the_class_one():
    """The per-asset detail has to be unmistakable: a bespoke valuation is an analyst-confirmed
    assumption, not a market valuation, and it replaced a class figure this class never had."""
    from fireline.app import CUSTOM_VALUATION_CAUTION, value_frame

    frame = value_frame(valued_asset())
    rows = dict(zip(frame["component"], frame["value"]))
    basis = dict(zip(frame["component"], frame["basis / source"]))
    assert list(frame["component"])[1:3] == ["custom valuation method",
                                             "custom valuation band (low - mid - high)"]
    assert rows["custom valuation method"] == "component replacement"
    assert rows["custom valuation band (low - mid - high)"] == \
        "EUR 8,000,000 - EUR 12,000,000 - EUR 20,000,000"
    assert basis["custom valuation band (low - mid - high)"] == CUSTOM_VALUATION_CAUTION
    assert "not a market valuation" in CUSTOM_VALUATION_CAUTION
    # the class figure it replaced, said plainly, and the class table that prices nothing here
    assert "ASSUMED bespoke figure replaced the per-class one" in basis["replacement value"]
    assert "research_facility" in basis["replacement value"]
    assert rows["replacement value"] == "EUR 12,000,000"
    # the damage band is the wider custom one, not the "-" the class table would have given
    assert rows["damage ratio low / mid / high"] == "0.1 / 0.35 / 0.75"
    assert config.CUSTOM_VALUATION_POLICY["version"] in basis["damage ratio low / mid / high"]
    assert "compounds" in basis["expected loss"] and "d_low" in basis["expected loss"]


def test_value_frame_is_unchanged_for_an_asset_with_no_bespoke_valuation():
    """The two valuation rows appear only when there is one: an ordinary class-valued asset keeps the
    exact table it had, damage-ratio basis included."""
    from fireline.app import value_frame

    a = make_asset(value_at_risk=True, estimated_occupancy=200, burn_probability=0.5, capacity=None,
                   forecast_source="fixture:test")
    frame = value_frame(a)
    assert list(frame["component"]) == ["people exposed", "replacement value",
                                        "damage ratio low / mid / high", "expected loss",
                                        "people at risk (p50)", "people at risk (p10)"]
    assert "bespoke" not in " ".join(frame["basis / source"]).lower()


def test_a_confirmed_not_valued_reads_as_a_look_that_found_nothing():
    """`not_valued` is the ordinary answer and a good one: the rows say the agent looked, and no euro
    figure appears - which is different from an asset nobody assessed."""
    from fireline.app import custom_value_label, valuation_band_text, value_frame

    asset = valued_asset(method="not_valued", components=[], amount_eur_low=None,
                         amount_eur_mid=None, amount_eur_high=None)
    rows = dict(zip(value_frame(asset)["component"], value_frame(asset)["value"]))
    assert rows["custom valuation method"] == "not valued"
    assert rows["custom valuation band (low - mid - high)"] == \
        "no bespoke figure (the agent looked; the evidence supports none)"
    assert rows["replacement value"] == "not valued"
    assert custom_value_label(asset) == "not valued (looked; no figure)"
    assert "valuation_unassessed" not in asset["review_reasons"]     # the look cleared the flag
    unassessed = make_asset(value_at_risk=True)
    assert custom_value_label(unassessed) == "not assessed"
    assert valuation_band_text(unassessed) == "not assessed"


def test_strategic_frame_shows_the_bespoke_value_beside_the_tier_as_a_display_string():
    """The strategic view is ordered tier then `custom_value_eur_mid`, so the euro column belongs
    there - as text, so nothing can sort the table into a different ranking."""
    from fireline.app import CUSTOM_VALUE_COLUMN, strategic_frame

    asset = dict(valued_asset(), criticality_tier="high",
                 criticality_factors=["national_research_infrastructure"],
                 criticality_basis="criticality-proto; analyst override: x", slack_min=30.0)
    frame = strategic_frame([asset])
    assert CUSTOM_VALUE_COLUMN in frame.columns and "valuation method" in frame.columns
    row = frame.iloc[0]
    assert row[CUSTOM_VALUE_COLUMN] == "EUR 12,000,000 (assumed)"       # a string, never a number
    assert isinstance(row[CUSTOM_VALUE_COLUMN], str)
    assert row["valuation method"] == "component replacement"
    assert config.CUSTOM_VALUATION_POLICY["version"] in row["valuation basis"]


class _RecordingStreamlit:
    """Enough of `st` for the proposal card: everything it writes, in order."""

    def __init__(self):
        self.written: list[str] = []
        self.frames: list = []

    def markdown(self, text, **kw):
        self.written.append(str(text))

    caption = markdown

    def dataframe(self, frame, **kw):
        self.frames.append(frame)


def test_the_valuation_proposal_card_shows_the_band_components_and_what_confirming_means():
    """A `custom_valuation` payload is an object, so the generic `{value!r}` line renders an
    unreadable dict. The card has to show the method, the band, the priced components, the note and
    the quoted evidence - and say that confirming it replaces the class figure."""
    import fireline.app as app_module
    from fireline.app import CUSTOM_VALUATION_CAUTION

    recorder = _RecordingStreamlit()
    proposal = {"proposal_id": "prop-1", "asset_id": "a:rf", "field": "custom_valuation",
                "confidence": "medium", "source": "ca.wikipedia.org",
                "url": "https://ca.wikipedia.org/wiki/x",
                "quoted_snippet": "el centre allotja el supercomputador",
                "value": {"method": "component_replacement",
                          "components": [{"label": "computing installation", "amount_eur": 9_000_000},
                                         {"label": "building shell", "amount_eur": 3_000_000}],
                          "amount_eur_low": 8_000_000, "amount_eur_mid": 12_000_000,
                          "amount_eur_high": 20_000_000, "note": "the machines dominate the shell"}}
    original, app_module.st = app_module.st, recorder
    try:
        app_module.render_valuation_proposal(proposal)
    finally:
        app_module.st = original

    text = "\n".join(recorder.written)
    assert "{" not in text and "amount_eur_mid" not in text          # not a dict dump
    assert "prop-1" in text and "component replacement" in text and "medium" in text
    assert "EUR 8,000,000 - EUR 12,000,000 - EUR 20,000,000" in text
    assert "replaces the per-class replacement cost" in text
    assert "the machines dominate the shell" in text                 # the agent's note
    assert "el centre allotja el supercomputador" in text            # the quoted evidence
    assert CUSTOM_VALUATION_CAUTION in recorder.written
    assert len(recorder.frames) == 1
    components = recorder.frames[0]
    assert list(components["priced component"]) == ["computing installation", "building shell"]
    assert list(components["amount"]) == ["EUR 9,000,000", "EUR 3,000,000"]


def test_the_valuation_proposal_card_reads_a_not_valued_proposal_as_an_answer():
    import fireline.app as app_module

    recorder = _RecordingStreamlit()
    proposal = {"proposal_id": "prop-2", "asset_id": "a:rf", "field": "custom_valuation",
                "confidence": "low", "source": "ca.wikipedia.org", "url": None,
                "quoted_snippet": "no hi ha cap xifra publicada",
                "value": {"method": "not_valued", "components": [], "amount_eur_low": None,
                          "amount_eur_mid": None, "amount_eur_high": None, "note": None}}
    original, app_module.st = app_module.st, recorder
    try:
        app_module.render_valuation_proposal(proposal)
    finally:
        app_module.st = original

    text = "\n".join(recorder.written)
    assert "no bespoke figure" in text and "the ordinary answer" in text
    assert "valuation_unassessed" in text and "without putting any euro figure" in text
    assert recorder.frames == []                                     # no component table to show


def test_render_agent_uses_the_valuation_card_only_for_a_custom_valuation_proposal():
    """The generic rendering stays for every other field: the branch is on the field name, not on the
    shape of the value."""
    source = APP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    body = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "render_agent")
    branch = next(n for n in ast.walk(body)
                  if isinstance(n, ast.If) and "custom_valuation" in ast.get_source_segment(source, n.test))
    called = [c.func.id for c in ast.walk(branch) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
    assert "render_valuation_proposal" in called
    assert "{p['value']!r}" in ast.get_source_segment(source, branch.orelse[0])
