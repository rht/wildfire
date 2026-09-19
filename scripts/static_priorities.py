"""Run the static contact/response example or the labelled edge-case suite."""

import argparse
import json
from pathlib import Path

from fireline.contact_priority import rank_contacts
from fireline.priority_examples import edge_cases, evaluate_case, load_scenario
from fireline.response_priority import greedy_response, plan_response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("fixtures/static_priority.json")
    )
    parser.add_argument("--all-cases", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        scenario = load_scenario(args.input)
        if args.all_cases:
            result = {"cases": [evaluate_case(c) for c in edge_cases(scenario)]}
            result["passed"] = all(case["passed"] for case in result["cases"])
        else:
            result = {
                "contacts": rank_contacts(scenario.locations),
                "response": plan_response(scenario),
                "greedy": greedy_response(scenario),
            }
        encoded = (
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
            print(f"Wrote {args.output}")
        else:
            print(encoded, end="")
        if args.all_cases and not result["passed"]:
            return 1
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
