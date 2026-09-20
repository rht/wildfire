"""Presentation layer for the analyst screen: the dashboard CSS and the pure HTML builders.

No Streamlit calls except `inject()`, no domain logic and no risk arithmetic: every function here
takes already-computed display values (a count, a label, a colour tone) and returns an HTML string,
so the whole look is unit-testable without a browser. `app.py` decides *what* to show and reads it
from the snapshot/scored structures; this module decides only how it looks.

Colour tones are names, never raw hex, at the call site: `tone_colour` is the one place that maps a
tone to a colour, so a re-theme is a single edit here.
"""

from __future__ import annotations

from html import escape

# --------------------------------------------------------------------------------- tokens
SHELL_BG = "#15100D"          # near-black warm charcoal: the page shell behind the cards
CARD_BG = "#F8F5F0"           # off-white card
CARD_INK = "#241F1B"          # dark text on a card
CARD_MUTED = "#7C736A"        # card sub-text and titles
CARD_RULE = "#E5DFD6"         # hairline divider inside a card

TONES = {
    "red": "#C33A2C",
    "amber": "#D08A12",
    "orange": "#CC5B22",
    "green": "#1F7A52",
    "blue": "#2C5EA8",
    "grey": CARD_MUTED,
    "ink": CARD_INK,
}
BAR_TONES = ("red", "amber", "green")   # evacuation / buffer / remaining window


def tone_colour(tone: str | None) -> str:
    """Hex for a tone name; an unknown or missing tone falls back to grey (never raises)."""
    return TONES.get(tone or "", TONES["grey"])


def esc(value) -> str:
    """HTML-escape any value, including quotes: asset names come from open data and are not trusted."""
    return escape("" if value is None else str(value), quote=True)


# --------------------------------------------------------------------------------- CSS
CSS = f"""
:root {{
  --ra-shell: {SHELL_BG};
  --ra-card: {CARD_BG};
  --ra-ink: {CARD_INK};
  --ra-muted: {CARD_MUTED};
  --ra-rule: {CARD_RULE};
}}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{ background: var(--ra-shell); }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMainBlockContainer"] {{ padding: 1.1rem 1.4rem 2rem 1.4rem; max-width: 100%; }}
[data-testid="stSidebarContent"] {{ background: #1C1613; }}

/* ---- header bar -------------------------------------------------------------- */
.ra-head {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 2px 4px 10px 4px; color: #EFE9E2; }}
.ra-head-name {{ font-size: 21px; font-weight: 700; letter-spacing: -0.01em; white-space: nowrap; }}
.ra-head-tagline {{ font-size: 13px; color: #9A928A; flex: 1 1 320px; min-width: 200px; }}
.ra-head-mode {{ font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: #CFC7BE; white-space: nowrap; }}
.ra-pill {{ font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  padding: 4px 12px; border-radius: 999px; white-space: nowrap; }}
.ra-stamp {{ text-align: right; font-size: 12px; color: #9A928A; line-height: 1.45; white-space: nowrap;
  font-variant-numeric: tabular-nums; }}
.ra-stamp b {{ color: #EFE9E2; font-weight: 600; }}

/* ---- cards ------------------------------------------------------------------- */
.ra-card, [class*="st-key-racard-"] {{ background: var(--ra-card); color: var(--ra-ink);
  border-radius: 16px; padding: 14px 16px 15px 16px; box-shadow: 0 10px 26px rgba(0,0,0,.34);
  margin-bottom: 12px; }}
[class*="st-key-racard-"] {{ gap: 0.35rem; }}
[class*="st-key-racard-"] p, [class*="st-key-racard-"] label, [class*="st-key-racard-"] span {{
  color: var(--ra-ink); }}
.ra-card-title {{ font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--ra-muted); margin: 0 0 10px 0; }}
.ra-card-foot {{ font-size: 11px; color: var(--ra-muted); margin-top: 10px; }}
.ra-empty {{ color: var(--ra-muted); font-style: italic; text-align: center; padding: 22px 8px; font-size: 13px; }}
.ra-scroll {{ max-height: 232px; overflow-y: auto; }}

/* ---- numbers ----------------------------------------------------------------- */
.ra-stats {{ display: flex; align-items: stretch; }}
.ra-stat {{ flex: 1 1 0; padding: 0 14px; }}
.ra-stat + .ra-stat {{ border-left: 1px solid var(--ra-rule); }}
.ra-stat:first-child {{ padding-left: 2px; }}
.ra-stat-value {{ font-size: 30px; font-weight: 700; line-height: 1.05; font-variant-numeric: tabular-nums; }}
.ra-stat-label {{ font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--ra-muted); margin-top: 4px; }}
.ra-tiles {{ display: flex; align-items: stretch; margin-bottom: 4px; }}
.ra-tile {{ flex: 1 1 0; padding: 0 12px; }}
.ra-tile + .ra-tile {{ border-left: 1px solid var(--ra-rule); }}
.ra-tile:first-child {{ padding-left: 2px; }}
.ra-tile-value {{ font-size: 22px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }}
.ra-tile-label {{ font-size: 10px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase;
  color: var(--ra-muted); margin-top: 2px; }}
.ra-sub {{ font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--ra-muted); margin: 12px 0 2px 0; }}

/* ---- rows -------------------------------------------------------------------- */
.ra-row {{ display: flex; align-items: center; gap: 10px; padding: 8px 2px; font-size: 13px;
  border-top: 1px solid var(--ra-rule); }}
.ra-row:first-child {{ border-top: none; }}
.ra-dot {{ width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }}
.ra-row-name {{ flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.ra-row-right {{ flex: 0 0 auto; font-size: 12px; }}
.ra-ranked {{ display: flex; align-items: center; gap: 10px; width: 100%; }}
.ra-bar {{ display: flex; width: 62px; height: 9px; border-radius: 3px; overflow: hidden; flex: 0 0 62px;
  background: var(--ra-rule); }}
.ra-bar span {{ display: block; height: 100%; }}
.ra-ranked-on {{ background: #EFE7DA; box-shadow: inset 3px 0 0 {TONES["red"]}; border-radius: 4px;
  padding: 2px 6px 2px 8px; margin-left: -8px; }}
.ra-ranked-text {{ flex: 1 1 auto; min-width: 0; }}
.ra-ranked-name {{ font-size: 14px; font-weight: 700; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }}
.ra-ranked-sub {{ font-size: 11px; color: var(--ra-muted); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }}
.ra-ranked-score {{ flex: 0 0 auto; font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.ra-detail-name {{ font-size: 16px; font-weight: 700; margin: 2px 0 8px 0; }}
.ra-kv {{ display: flex; justify-content: space-between; gap: 12px; font-size: 12px; padding: 5px 2px;
  border-top: 1px solid var(--ra-rule); }}
.ra-kv:first-child {{ border-top: none; }}
.ra-kv-k {{ color: var(--ra-muted); }}
.ra-kv-v {{ font-weight: 600; text-align: right; }}

/* ---- map card ---------------------------------------------------------------- */
[class*="st-key-racard-map"] {{ position: relative; padding: 10px; }}
[class*="st-key-ra-mapchips"] {{ position: absolute; top: 20px; left: 20px; right: 20px; z-index: 6; }}
[class*="st-key-ra-mapchips"] [data-testid="stColumn"]:last-child {{ display: flex; justify-content: flex-end; }}
.ra-mapchip {{ display: inline-block; background: #FFFFFF; color: #2A2420; font-size: 11px; font-weight: 700;
  letter-spacing: 0.08em; text-transform: uppercase; padding: 7px 14px; border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0,0,0,.3); }}
[class*="st-key-racard-map"] [data-testid="stDeckGlJsonChart"] {{ border-radius: 11px; overflow: hidden; }}
[class*="st-key-racard-map"] canvas {{ border-radius: 11px; }}

/* ---- buttons ----------------------------------------------------------------- */
[data-testid="stMain"] .stButton > button, [data-testid="stMain"] [data-testid="stPopover"] > div > button {{
  background: #FFFFFF; color: #2A2420; border: 1px solid rgba(0,0,0,.08); border-radius: 10px;
  font-weight: 600; box-shadow: 0 4px 14px rgba(0,0,0,.28); }}
[data-testid="stMain"] .stButton > button:hover {{ background: #F1ECE4; color: #2A2420;
  border-color: rgba(0,0,0,.14); }}
[data-testid="stMain"] .stButton > button[kind="primary"] {{ background: #C33A2C; color: #FFF7F4;
  border-color: #C33A2C; }}
[data-testid="stMain"] .stButton > button[kind="primary"]:hover {{ background: #AC3125; color: #FFF7F4; }}
[data-testid="stMain"] .stButton > button:disabled {{ opacity: .45; box-shadow: none; }}
[class*="st-key-ra-next"] .stButton > button, [class*="st-key-ra-next"] > button,
[class*="st-key-ra-prev"] .stButton > button, [class*="st-key-ra-prev"] > button {{
  background: #1C5C3E; color: #EAF6EF; border-color: #1C5C3E; white-space: nowrap; }}
[class*="st-key-ra-next"] .stButton > button:hover,
[class*="st-key-ra-prev"] .stButton > button:hover {{ background: #16704A; color: #EAF6EF; }}

/* ---- the approve-all preview switch, on the dark shell beside it -------------- */
[class*="st-key-ra-approve"] [data-testid="stWidgetLabel"] p,
[class*="st-key-ra-approve"] label span, [class*="st-key-ra-approve"] label p {{
  color: #CFC7BE; font-size: 11px; font-weight: 700; line-height: 1.25; }}
[class*="st-key-ra-approve"] svg {{ fill: #9A928A; }}
[class*="st-key-ra-approve"] label > div:first-of-type {{ background: #4A403A; }}
[class*="st-key-ra-approve"] label:has(input:checked) > div:first-of-type,
[class*="st-key-ra-approve"] label:has([aria-checked="true"]) > div:first-of-type {{ background: #1C5C3E; }}
[class*="st-key-rarank-"] .stButton > button, [class*="st-key-rarank-"] > button {{
  background: transparent; color: var(--ra-muted); border: none; box-shadow: none; padding: 0;
  font-variant-numeric: tabular-nums; font-weight: 600; }}
[class*="st-key-rarank-"] .stButton > button:hover {{ background: transparent; color: #C33A2C; }}

/* ---- widgets that sit on a light card ---------------------------------------- */
[class*="st-key-racard-"] [data-baseweb="select"] > div {{ background: #FFFFFF; border-color: var(--ra-rule); }}
[class*="st-key-racard-"] [data-baseweb="select"] div {{ color: var(--ra-ink); }}
[class*="st-key-racard-"] svg {{ fill: var(--ra-muted); }}
[class*="st-key-ra-ranklist"] {{ padding-right: 4px; }}
[class*="st-key-ra-ranklist"] [data-testid="stHorizontalBlock"] {{ gap: 0.4rem;
  border-top: 1px solid var(--ra-rule); padding: 5px 0; }}
[class*="st-key-ra-ranklist"] [data-testid="stHorizontalBlock"]:first-child {{ border-top: none; }}

/* ---- sections below the fold -------------------------------------------------- */
[data-testid="stMain"] [data-testid="stExpander"] details {{ background: #1D1714; border-color: #2E2622;
  border-radius: 12px; }}
[data-testid="stMain"] [data-testid="stExpander"] summary {{ color: #E4DCD4; }}
.ra-section {{ color: #C9C0B7; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
  font-weight: 700; margin: 18px 2px 6px 2px; }}
"""


def style_block() -> str:
    """The `<style>` element for the dashboard (pure; `inject` is the only Streamlit caller)."""
    return f"<style>{CSS}</style>"


def inject() -> None:
    """Install the dashboard CSS on the current page."""
    import streamlit as st

    st.html(style_block())


# --------------------------------------------------------------------------------- header
def header_html(*, product: str, tagline: str, mode: str, status: str, status_tone: str,
                as_of: str, computed_at: str) -> str:
    """The dark header bar: product, tagline, input mode, data-status pill and the two stamps."""
    colour = tone_colour(status_tone)
    return (
        '<div class="ra-head">'
        f'<div class="ra-head-name">{esc(product)}</div>'
        f'<div class="ra-head-tagline">{esc(tagline)}</div>'
        f'<div class="ra-head-mode">mode: {esc(mode)}</div>'
        f'<div class="ra-pill" style="background:{colour}33;color:{colour}">{esc(status)}</div>'
        f'<div class="ra-stamp">as_of <b>{esc(as_of)}</b><br/>computed_at <b>{esc(computed_at)}</b></div>'
        "</div>"
    )


# --------------------------------------------------------------------------------- cards
def card_title_html(title: str, count=None) -> str:
    text = f"{title} ({count})" if count is not None else title
    return f'<div class="ra-card-title">{esc(text)}</div>'


def card_html(title: str, body: str, *, count=None, foot: str = "") -> str:
    """A light card: uppercase title, caller-built body HTML and an optional muted footer line."""
    tail = f'<div class="ra-card-foot">{esc(foot)}</div>' if foot else ""
    return f'<div class="ra-card">{card_title_html(title, count)}{body}{tail}</div>'


def empty_state_html(text: str) -> str:
    return f'<div class="ra-empty">{esc(text)}</div>'


def stat_summary_html(stats) -> str:
    """Big numbers side by side, separated by hairlines: `[(value, label, tone), ...]`."""
    cells = "".join(
        f'<div class="ra-stat"><div class="ra-stat-value" style="color:{tone_colour(tone)}">{esc(value)}</div>'
        f'<div class="ra-stat-label">{esc(label)}</div></div>'
        for value, label, tone in stats)
    return f'<div class="ra-stats">{cells}</div>'


def tile_row_html(tiles) -> str:
    """Number + uppercase label tiles: `[(value, label, tone), ...]` (smaller than `stat_summary_html`)."""
    cells = "".join(
        f'<div class="ra-tile"><div class="ra-tile-value" style="color:{tone_colour(tone)}">{esc(value)}</div>'
        f'<div class="ra-tile-label">{esc(label)}</div></div>'
        for value, label, tone in tiles)
    return f'<div class="ra-tiles">{cells}</div>'


def sub_heading_html(text: str) -> str:
    return f'<div class="ra-sub">{esc(text)}</div>'


def dot_row_html(name: str, tone: str | None = None, right: str = "", right_tone: str | None = None) -> str:
    """A divider-separated list row: optional coloured dot, name, optional right-aligned note.

    `tone=None` draws no dot (the needs-review rows are a name and a flag reason, nothing else)."""
    dot = f'<div class="ra-dot" style="background:{tone_colour(tone)}"></div>' if tone else ""
    tail = (f'<div class="ra-row-right" style="color:{tone_colour(right_tone)}">{esc(right)}</div>'
            if right else "")
    return f'<div class="ra-row">{dot}<div class="ra-row-name">{esc(name)}</div>{tail}</div>'


def detail_html(name: str, pairs) -> str:
    """The selected-location card body: the location's name over its label/value lines."""
    return f'<div class="ra-detail-name">{esc(name)}</div>{kv_html(pairs)}'


def kv_html(pairs) -> str:
    """Label/value lines for the selected-location card: `[(label, value), ...]`."""
    return "".join(f'<div class="ra-kv"><div class="ra-kv-k">{esc(k)}</div>'
                   f'<div class="ra-kv-v">{esc(v)}</div></div>' for k, v in pairs)


def scroll_html(body: str) -> str:
    return f'<div class="ra-scroll">{body}</div>'


def map_chip_html(text: str) -> str:
    return f'<span class="ra-mapchip">{esc(text)}</span>'


# --------------------------------------------------------------------------------- ranked row
def segment_widths(values) -> list[float]:
    """Percentages summing to 100 for a stacked bar, one per value.

    Negative and null values count as zero (a negative remaining window is drawn as no green, not as a
    reversed bar). When nothing is positive every segment is zero, so the caller draws an empty track
    rather than dividing by zero. Percentages are rounded to one decimal.
    """
    clean = [max(0.0, float(v)) if v is not None else 0.0 for v in values]
    total = sum(clean)
    if total <= 0:
        return [0.0 for _ in clean]
    return [round(v * 100.0 / total, 1) for v in clean]


def stacked_bar_html(values, tones=BAR_TONES) -> str:
    """A small stacked bar; `values` are absolute amounts, normalised by `segment_widths`."""
    widths = segment_widths(values)
    spans = "".join(f'<span style="width:{w}%;background:{tone_colour(t)}"></span>'
                    for w, t in zip(widths, tones) if w > 0)
    return f'<div class="ra-bar">{spans}</div>'


def ranked_row_html(*, name: str, subtitle: str, value: str, segments, value_tone: str = "ink",
                    tones=BAR_TONES, selected: bool = False) -> str:
    """One ranked-location row: stacked bar, bold name, muted sub-line, right-aligned bold figure.

    `selected` marks the row the selected-location card is showing."""
    css = "ra-ranked ra-ranked-on" if selected else "ra-ranked"
    return (f'<div class="{css}">{stacked_bar_html(segments, tones)}'
            f'<div class="ra-ranked-text"><div class="ra-ranked-name">{esc(name)}</div>'
            f'<div class="ra-ranked-sub">{esc(subtitle)}</div></div>'
            f'<div class="ra-ranked-score" style="color:{tone_colour(value_tone)}">{esc(value)}</div></div>')
