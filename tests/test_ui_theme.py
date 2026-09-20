"""The dashboard's presentation helpers: HTML builders, colour selection and bar segment widths.

`fireline.ui_theme` is pure (no Streamlit, no domain logic), so the whole look is testable here:
escaping of open-data names, the tone -> colour map, and the arithmetic of the stacked bar.
"""

from __future__ import annotations

import re

from fireline import ui_theme as t


# --------------------------------------------------------------------- colours and escaping
def test_tone_colour_falls_back_to_grey_instead_of_raising():
    assert t.tone_colour("red") == t.TONES["red"]
    assert t.tone_colour("no-such-tone") == t.TONES["grey"]
    assert t.tone_colour(None) == t.TONES["grey"]


def test_every_builder_escapes_the_text_it_is_given():
    """Asset names come from open data: a name with markup must never reach the page as markup."""
    name = 'Escola <script>"x" & y'
    for html in (t.dot_row_html(name, "red"),
                 t.card_html(name, "<b>body</b>"),
                 t.empty_state_html(name),
                 t.map_chip_html(name),
                 t.stat_summary_html([(1, name, "red")]),
                 t.tile_row_html([(1, name, "green")]),
                 t.kv_html([(name, name)]),
                 t.detail_html(name, [("k", name)]),
                 t.ranked_row_html(name=name, subtitle=name, value="1 min", segments=[1, 1, 1])):
        assert "<script>" not in html
        assert "&lt;script&gt;" in html and "&amp;" in html


# --------------------------------------------------------------------- stacked bar
def test_segment_widths_normalise_to_a_hundred_percent():
    assert t.segment_widths([90, 30, 60]) == [50.0, 16.7, 33.3]
    assert sum(t.segment_widths([1, 2, 3])) == 100.0


def test_segment_widths_treat_a_negative_or_missing_part_as_nothing_drawn():
    """An exhausted window is negative: it draws no green, never a reversed bar."""
    assert t.segment_widths([90, 30, -45]) == [75.0, 25.0, 0.0]
    assert t.segment_widths([90, None, 30]) == [75.0, 0.0, 25.0]
    assert t.segment_widths([0, 0, 0]) == [0.0, 0.0, 0.0]       # empty track, no division by zero
    assert t.segment_widths([None, None]) == [0.0, 0.0]


def test_stacked_bar_paints_one_span_per_positive_segment_in_tone_order():
    html = t.stacked_bar_html([90, 30, 60])
    widths = re.findall(r"width:([\d.]+)%", html)
    assert widths == ["50.0", "16.7", "33.3"]
    for tone in t.BAR_TONES:
        assert t.TONES[tone] in html
    assert t.stacked_bar_html([0, 0, 0]) == '<div class="ra-bar"></div>'
    assert t.TONES["green"] not in t.stacked_bar_html([90, 30, 0])   # nothing left of the window


def test_ranked_row_carries_the_name_subtitle_figure_and_its_tone():
    html = t.ranked_row_html(name="Vall Repos", subtitle="care_home - 950 m to fire", value="62 min",
                             segments=[90, 30, 62], value_tone="orange")
    assert "Vall Repos" in html and "care_home - 950 m to fire" in html and "62 min" in html
    assert f'color:{t.TONES["orange"]}' in html


# --------------------------------------------------------------------- cards and header
def test_card_html_shows_an_uppercase_title_with_an_optional_count_and_footer():
    html = t.card_html("Needs-review queue", "<div>rows</div>", count=7, foot="a footnote")
    assert "Needs-review queue (7)" in html and "<div>rows</div>" in html and "a footnote" in html
    assert 'class="ra-card-title"' in html
    assert "(7)" not in t.card_html("Selected location", "")           # no count, no parentheses


def test_stat_and_tile_rows_colour_each_number_by_its_tone():
    stats = t.stat_summary_html([(0, "window exhausted", "red"), (5, "ranked", "amber")])
    assert f'color:{t.TONES["red"]}' in stats and f'color:{t.TONES["amber"]}' in stats
    assert stats.count('class="ra-stat"') == 2
    tiles = t.tile_row_html([(3, "confirmed done", "green")])
    assert "CONFIRMED DONE" not in tiles          # uppercasing is the stylesheet's job, not the text's
    assert "confirmed done" in tiles and f'color:{t.TONES["green"]}' in tiles


def test_dot_row_draws_a_dot_only_when_a_tone_is_given():
    with_dot = t.dot_row_html("Can Xic", "red", right="proposal", right_tone="amber")
    assert 'class="ra-dot"' in with_dot and "proposal" in with_dot
    assert 'class="ra-dot"' not in t.dot_row_html("Pou del Glac", right="occupancy_seasonal",
                                                  right_tone="orange")


def test_header_carries_the_mode_the_status_pill_and_both_stamps():
    html = t.header_html(product="Respons'Ara", tagline="who needs a call first", mode="recorded",
                         status="current", status_tone="green", as_of="10:00Z 2026-07-03",
                         computed_at="10:00Z 2026-07-03")
    assert "Respons&#x27;Ara" in html and "who needs a call first" in html
    assert "mode: recorded" in html and "current" in html
    assert "as_of <b>10:00Z 2026-07-03</b>" in html and "computed_at <b>10:00Z 2026-07-03</b>" in html
    assert t.TONES["green"] in html


def test_style_block_is_a_single_style_element_with_the_shell_and_card_colours():
    block = t.style_block()
    assert block.startswith("<style>") and block.endswith("</style>")
    assert block.count("<style>") == 1
    assert t.SHELL_BG in block and t.CARD_BG in block
    assert "{{" not in block and "}}" not in block        # the f-string braces are all escaped
