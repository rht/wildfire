"""Generate a six-page PDF from the executable static-priority scenarios."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from fireline.priority_examples import edge_cases, evaluate_case, load_scenario

INK = colors.HexColor("#19313B")
TEAL = colors.HexColor("#157A79")
AMBER = colors.HexColor("#B9652A")
PALE = colors.HexColor("#EFF5F4")
MUTED = colors.HexColor("#52656C")
WIDTH, HEIGHT = A4
LEFT, RIGHT = 44, WIDTH - 44
SPAN = RIGHT - LEFT


class Report:
    def __init__(self, path):
        self.canvas = Canvas(str(path), pagesize=A4, invariant=1)
        self.canvas.setTitle("FireLine | Static contact and response priorities")
        self.canvas.setAuthor("FireLine / @mirrdj")
        self.page = 0

    def start(self, kicker, title, subtitle):
        if self.page:
            self.canvas.showPage()
        self.page += 1
        c = self.canvas
        c.setFillColor(TEAL)
        c.rect(0, HEIGHT - 10, WIDTH, 10, fill=1, stroke=0)
        self.text(
            "FIRELINE  /  STATIC DECISION SUPPORT",
            LEFT,
            HEIGHT - 42,
            9,
            TEAL,
            bold=True,
        )
        self.text(kicker.upper(), LEFT, HEIGHT - 72, 9, AMBER, bold=True)
        y = self.para(title, HEIGHT - 94, size=25, bold=True, leading=29)
        y = self.para(subtitle, y - 10, size=10, color=MUTED)
        c.setStrokeColor(colors.HexColor("#D5DEDF"))
        c.line(LEFT, 44, RIGHT, 44)
        self.text(
            "Synthetic prototype | Supplied assumptions, not operational dispatch",
            LEFT,
            30,
            8,
            MUTED,
        )
        self.text(f"{self.page} / 6", RIGHT - 24, 30, 8, MUTED)
        return y - 20

    def text(self, text, x, y, size=10, color=INK, bold=False):
        c = self.canvas
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.setFillColor(color)
        c.drawString(x, y, text)

    def para(
        self,
        text,
        y,
        size=10.2,
        color=INK,
        bold=False,
        leading=None,
        x=LEFT,
        width=SPAN,
    ):
        style = ParagraphStyle(
            "p",
            fontName="Helvetica-Bold" if bold else "Helvetica",
            fontSize=size,
            leading=leading or size * 1.4,
            textColor=color,
        )
        p = Paragraph(text, style)
        _, height = p.wrap(width, HEIGHT)
        if y - height < 57:
            raise ValueError(
                f"Page {self.page}: paragraph would overflow ({text[:60]})"
            )
        p.drawOn(self.canvas, x, y - height)
        return y - height

    def section(self, title, y):
        return self.para(title, y, size=13, bold=True, color=TEAL) - 9

    def table(self, rows, widths, y, size=9):
        style = ParagraphStyle(
            "cell",
            fontName="Helvetica",
            fontSize=size,
            leading=size * 1.3,
            textColor=INK,
        )
        cells = [[Paragraph(escape(str(v)), style) for v in row] for row in rows]
        table = Table(cells, colWidths=widths, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D8EAE7")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.7, TEAL),
                ]
            )
        )
        _, height = table.wrap(SPAN, HEIGHT)
        if y - height < 57:
            raise ValueError(
                f"Page {self.page}: table overflows by {57 - (y - height):.1f}pt"
            )
        table.drawOn(self.canvas, LEFT, y - height)
        return y - height - 15

    def diagram(self, y):
        c = self.canvas
        bottom = y - 240
        c.setFillColor(PALE)
        c.roundRect(LEFT, bottom, SPAN, 240, 10, fill=1, stroke=0)
        ox, oy = LEFT + 100, bottom + 175
        c.setStrokeColor(colors.HexColor("#D6E3E1"))
        for i in range(6):
            c.line(LEFT + 35 + i * 40, bottom + 30, LEFT + 35 + i * 40, bottom + 215)
        for i in range(5):
            c.line(LEFT + 35, bottom + 35 + i * 40, LEFT + 235, bottom + 35 + i * 40)
        pts = {
            "O": (ox, oy),
            "B": (ox + 100, oy),
            "C": (ox, oy - 100),
            "A": (ox + 50, oy - 150),
        }
        c.setStrokeColor(TEAL)
        c.setLineWidth(2)
        cx, cy = pts["C"]
        ax, ay = pts["A"]
        c.line(cx, cy, ax, ay)
        c.line(ax, ay, ax - 12, ay + 3)
        c.line(ax, ay, ax - 3, ay + 12)
        c.setStrokeColor(AMBER)
        c.setDash(4, 3)
        c.line(ox + 8, oy - 8, ox + 70, oy - 70)
        c.setDash()
        c.line(ox + 70, oy - 70, ox + 58, oy - 67)
        c.line(ox + 70, oy - 70, ox + 67, oy - 58)
        for key, (x, p_y) in pts.items():
            c.setFillColor(AMBER if key == "O" else TEAL)
            c.circle(x, p_y, 11, fill=1, stroke=0)
            self.text(key, x - 4, p_y - 4, 11, colors.white, bold=True)
        self.para(
            "<b>O</b> Fire origin<br/><b>B</b> Value 80; 8 people<br/><b>C</b> Value 20; 4 people<br/><b>A</b> Value 100; 80 people,<br/>60 needing assistance",
            bottom + 206,
            x=LEFT + 285,
            width=SPAN - 305,
            size=10,
        )
        self.para(
            "B and C are equally distant.<br/>The solid C-to-A link is an<br/><b>explicit scenario assumption</b>.<br/>The fire arrow is illustrative.",
            bottom + 104,
            x=LEFT + 285,
            width=SPAN - 305,
            size=9,
            color=MUTED,
        )
        return bottom - 16


def sequence(result):
    return (
        " > ".join(step["site_id"] for step in result["steps"]) or "No feasible action"
    )


def build(path, fixture, test_results):
    scenario = load_scenario(fixture)
    cases = [evaluate_case(case) for case in edge_cases(scenario)]
    if not all(case["passed"] for case in cases):
        raise ValueError("Cannot report success: scenario expectations failed")
    base = cases[0]
    response = base["response"]
    greedy = base["greedy"]
    report = Report(path)
    y = report.start(
        "01 / The design question",
        "Two priorities. Two algorithms.",
        "Who should be contacted first, and which sequence of response actions best meets the supplied objectives?",
    )
    y = (
        report.para(
            "A location ranking cannot represent an action that unlocks or benefits another location. "
            "The prototype separates <b>forecast-based contact urgency</b> from a <b>constrained action-sequence search</b>.",
            y,
        )
        - 16
    )
    y = report.diagram(y)
    y = report.table(
        [
            ["Output", "Base fixture result"],
            [
                "Contact order",
                " > ".join(r["asset_id"] for r in base["contacts"]["ranked"]),
            ],
            ["Response sequence", sequence(response)],
            ["Direct-value greedy baseline", sequence(greedy)],
            [
                "Covered assisted-person units",
                f"Exact: {response['objective']['assisted_units']:g}; greedy: {greedy['objective']['assisted_units']:g}",
            ],
        ],
        [205, SPAN - 205],
        y,
    )
    report.para(
        "<b>Interpretation:</b> C comes first because the fixture explicitly requires it before assisting A. "
        "The algorithm does not infer that firefighters at a nearby building can stop a fire. "
        "All coordinates, times and effects here are synthetic.",
        y,
        size=9.5,
    )

    y = report.start(
        "02 / Contact priority",
        "Rank locations independently.",
        "Contact urgency depends on forecast arrival and evacuation duration; it does not determine a crew itinerary.",
    )
    y = report.section("Time remaining to start evacuation", y)
    y = (
        report.para(
            "Time to impact = predicted fire arrival - current time<br/>"
            "Latest start = predicted fire arrival - evacuation duration - buffer<br/>"
            "<b>Remaining window = latest start - current time</b><br/>"
            "Contact the location with the smallest remaining window first.",
            y,
        )
        - 15
    )
    rows = [["Location", "Distance (m)", "Fire arrival", "Evacuation", "Window"]]
    for row in base["contacts"]["ranked"]:
        a = next(a for a in scenario.locations if a.asset_id == row["asset_id"])
        rows.append(
            [
                a.asset_id,
                f"{a.distance_m:.1f}",
                f"{a.fire_arrival_min:g} min",
                f"{a.evacuation_min:g} min",
                f"{row['slack_min']:g} min",
            ]
        )
    y = report.table(rows, [60, 105, 115, 115, SPAN - 395], y)
    y = (
        report.para(
            "These are synthetic spread predictions and duration estimates at minute zero with no buffer. "
            "Evacuation includes mobilisation, preparation/loading and onward movement. "
            "A is first because its window is only two minutes, not because of its property value. "
            "Equal windows use earlier predicted arrival, then geographic distance, then asset ID.",
            y,
        )
        - 12
    )
    y = (
        report.para(
            "A farther location can be more urgent when the fire is spreading toward it. "
            "Missing forecasts, evacuation estimates or sources stay in an unranked review queue. "
            "Zero or negative windows are flagged for immediate review, not treated as an evacuation instruction.",
            y,
        )
        - 16
    )
    y = report.section("Static input contract", y)
    y = report.table(
        [
            ["Input", "Required meaning"],
            [
                "Location",
                "ID, coordinates, distance, sourced fire arrival and evacuation duration; people, assistance, value and action deadline for response planning.",
            ],
            [
                "Action",
                "Site, duration, completion deadline, prerequisites, required capabilities.",
            ],
            [
                "Benefit edge",
                "Target asset, coverage fraction [0,1], confirmation and evidence or labelled assumption.",
            ],
            [
                "Travel leg",
                "Directed end-to-end travel minutes and optional availability cutoff. Missing leg means unavailable.",
            ],
            [
                "Crew / scenario",
                "Start node, capability tags, planning horizon and nonnegative time buffer.",
            ],
        ],
        [105, SPAN - 105],
        y,
        size=9,
    )

    y = report.start(
        "03 / Response order",
        "Compare complete feasible sequences.",
        "Exact enumeration for one crew and at most eight actions. No GPS shortcut or assumed causal propagation.",
    )
    y = report.section("Feasibility before benefit", y)
    y = (
        report.para(
            "An action requires its prerequisites, crew capabilities and an available directed travel leg. "
            "Arrival must precede the leg cutoff. Completion plus the configured buffer must fit the work-site deadline, "
            "action deadline and planning horizon. A target receives benefit only before its own deadline. Inclusive comparisons tolerate only 1e-9 minutes of arithmetic noise; the scenario buffer is separate.",
            y,
        )
        - 16
    )
    y = report.section("Count each asset once", y)
    y = (
        report.para(
            "<b>coverage(asset) = maximum confirmed, on-time effect from completed actions.</b><br/>"
            "A 0.6 effect followed by an 0.8 effect gives 0.8 coverage, not 1.4. No transitive effects are invented. "
            "Unknown target inputs or unconfirmed effects receive no credit and create review items.",
            y,
        )
        - 16
    )
    y = report.section("Objective, compared in order", y)
    y = (
        report.para(
            "1. Maximise covered assisted-person units.<br/>2. Then maximise covered total-person units.<br/>"
            "3. Then maximise covered asset-value units.<br/>4. Then deliver each benefit category earlier, "
            "minimise completion time and action count, and break remaining ties by action ID.",
            y,
        )
        - 12
    )
    y = (
        report.para(
            "Each total is sum(asset amount x coverage). Assisted people are included in total people; these are "
            "separate priority dimensions, not added counts. Earlier delivery minimises completion time weighted by incremental "
            "benefit. This life-first ordering is an explicit prototype policy and needs analyst review.",
            y,
            size=9.5,
        )
        - 16
    )
    y = report.section("Search procedure", y)
    y = (
        report.para(
            "Evaluate the current sequence, including the empty sequence.<br/>"
            "For each unused action: check prerequisites and timing; extend if feasible.<br/>"
            "Update coverage and incremental-benefit timing; recurse.<br/>"
            "Retain the best sequence and the best alternative for every first action.",
            y,
        )
        - 12
    )
    report.para(
        "Zero-immediate-benefit actions are retained because they can unlock A. Worst-case search is factorial; "
        "eight actions have at most 109,601 partial sequences including the empty one. Inputs over eight actions are rejected, "
        "not silently truncated or reported as optimal. Optimality applies only to this supplied static model.",
        y,
        size=9.5,
    )

    y = report.start(
        "04 / Executable cases",
        "Test the counterexamples too.",
        f"{len(cases)} labelled cases run from the same model and produce the results below. All expectations passed.",
    )
    rows = [["Case / change", "Exact order", "Assisted units"]]
    for case in cases:
        rows.append(
            [
                case["title"],
                sequence(case["response"]),
                f"{case['response']['objective']['assisted_units']:g}",
            ]
        )
    y = report.table(rows, [SPAN - 180, 105, 75], y, size=8.6)
    report.para(
        '"Covered units" are a model accounting measure under supplied assumptions, not a prediction of people saved. '
        "Unserved assets and review questions remain in the output even when another feasible action is selected.",
        y,
        size=9,
    )

    y = report.start(
        "05 / Evidence and verification",
        "Why C first is conditional.",
        "Results are generated from the executable fixture rather than entered by hand.",
    )
    rows = [["Plan", "Action timing", "Assisted / total / value"]]
    for name, outcome in [("Exact", response), ("Greedy", greedy)]:
        rows.append(
            [
                name,
                "; ".join(
                    f"{s['site_id']}: {s['start_min']:g}-{s['finish_min']:g} min"
                    for s in outcome["steps"]
                ),
                " / ".join(
                    f"{outcome['objective'][key]:g}"
                    for key in ("assisted_units", "people_units", "value_units")
                ),
            ]
        )
    y = report.table(rows, [70, SPAN - 210, 140], y)
    y = (
        report.para(
            "In the base fixture, C finishes at minute 3 and A at minute 7, before A's minute-9 deadline. "
            "Choosing B first leads to C finishing at minute 8; A would finish at minute 12 and is infeasible. "
            "If A is directly accessible, the algorithm instead chooses A first. If C is blocked or the crew lacks "
            "the capability needed at A, it reports the limitation and evaluates remaining actions.",
            y,
        )
        - 18
    )
    y = report.section("Verification evidence", y)
    if test_results.exists():
        root = ET.parse(test_results).getroot()
        tests = root.findall(".//testcase")
        failures = root.findall(".//failure") + root.findall(".//error")
        y = (
            report.para(
                f"Test report: <b>{len(tests)} tests, {len(failures)} failures/errors</b>. "
                "The report builder reads the supplied JUnit file; rerun tests before regenerating a release report.",
                y,
            )
            - 10
        )
        if failures:
            raise ValueError("Test results contain failures")
    else:
        y = (
            report.para(
                "No JUnit file supplied; full-suite pass count is not asserted in this PDF.",
                y,
            )
            - 10
        )
    y = (
        report.para(
            "Checks include stable ties, reverse input order, deadline equality, buffers, unavailable legs, "
            "capabilities, partial overlap, unknown counts, dependency cycles, invalid/non-finite values, duplicate IDs "
            "and the action-count limit. Contact checks cover forecast-driven ordering, longer evacuations, negative/zero windows, missing evidence and distance tie-breaks. An independent permutation oracle compares small generated problems with the exact planner.",
            y,
        )
        - 18
    )
    y = report.section("Algorithm references", y)
    y = (
        report.para(
            '<link href="https://developers.google.com/optimization/routing/vrptw" color="#157A79">'
            "Google OR-Tools: Vehicle Routing Problem with Time Windows</link> describes the use of travel-time matrices "
            "and time windows. This prototype uses its own small exhaustive search, not OR-Tools.",
            y,
            size=9.5,
        )
        - 10
    )
    report.para(
        '<link href="https://developers.google.com/optimization/routing/penalties" color="#157A79">'
        "Google OR-Tools: Penalties and Dropping Visits</link> explains optional visits when all locations cannot be served. "
        "Here, unserved locations are explicit and the objective uses separate benefit priorities. Neither source validates "
        "the wildfire forecasts, evacuation estimates or response objectives.",
        y,
        size=9.5,
    )

    y = report.start(
        "06 / Reproduce and extend",
        "A static prototype with clear limits.",
        "Implementation: codex/static-priorities | Project owner for coordination: @mirrdj",
    )
    y = report.section("Run from the worktree root", y)
    y = (
        report.para(
            '<font face="Courier" size="8.7">uv venv<br/>'
            "uv pip install -e &quot;.[dev,report]&quot;<br/>"
            ".venv/bin/python scripts/static_priorities.py<br/>"
            "make static-priorities<br/>"
            ".venv/bin/python -m pytest -q --junitxml=data/priority-tests.xml<br/>"
            "make priority-report</font>",
            y,
            leading=15,
        )
        - 18
    )
    y = report.section("Files to inspect", y)
    y = (
        report.para(
            "<b>fixtures/static_priority.json</b> - editable locations, actions and directed travel inputs.<br/>"
            "<b>fireline/contact_priority.py</b> - forecast-based contact urgency.<br/>"
            "<b>fireline/response_priority.py</b> - sequence search and myopic comparator.<br/>"
            "<b>reports/static-priority-results.json</b> - scenario results, coverage and alternatives.<br/>"
            "<b>tests/test_static_priorities.py</b> - executable behaviours and oracle checks.",
            y,
            size=9.5,
        )
        - 18
    )
    y = report.section("What the algorithm cannot establish", y)
    y = (
        report.para(
            "The geometry is a synthetic local-metre layout; the southeast arrow does not estimate fire arrival. "
            "Fire arrival predictions, evacuation estimates, action deadlines, access and benefit effects must be supplied. The code does not model smoke, fire intensity, "
            "suppression effectiveness, changing routes, safe crew egress, return to base, multiple crews, partial evacuation capacity "
            "or live weather. A completed action is assumed to deliver its declared persistent effect. Maximum coverage is a "
            "conservative overlap rule, not a simulation of distinct groups being rescued.",
            y,
        )
        - 16
    )
    y = (
        report.para(
            "The result is a proposal for analyst review under these assumptions. Unknowns can change the answer; a mathematical "
            "optimum over the supplied inputs does not establish field safety. The existing live engine and UI are not automatically "
            "wired to this CLI experiment.",
            y,
        )
        - 18
    )
    y = report.section("Next integration step", y)
    report.para(
        "Map real location snapshots into this static input contract, with provenance and confirmed action relationships. "
        "Then add snapshot versioning and re-planning that preserves completed and assigned work. Larger problems and multiple "
        "crews need a scalable constrained solver and explicit resource/egress modelling before operational evaluation.",
        y,
    )
    report.canvas.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("fixtures/static_priority.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("reports/static-priority-report.pdf")
    )
    parser.add_argument(
        "--test-results", type=Path, default=Path("data/priority-tests.xml")
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build(args.output, args.input, args.test_results)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
