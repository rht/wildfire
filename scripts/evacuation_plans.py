"""Offline evacuation evidence, approval commands and durable public plan export."""
import argparse
import json
from pathlib import Path
import sqlite3

from fireline.evacuation_allocations import AllocationStore, AssistancePlan
from fireline.evacuation_plans import (
    AnalystApproval, EvacuationGroup, EvacuationRoute, PlanningContext, RoadEvidence, candidate_from_record,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--as-of', required=True, help='Current UTC time in the scenario')
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding='utf-8'))
        store = AllocationStore(args.database)
        store.update_inputs(PlanningContext(**data['context']),
                            [candidate_from_record(c) for c in data['candidates']],
                            [EvacuationGroup(**g) for g in data['groups']],
                            [EvacuationRoute(**r) for r in data['routes']],
                            [RoadEvidence(**r) for r in data['roads']])
        operations = {'reserve': store.reserve, 'reassign': store.reassign, 'confirm': store.confirm,
                      'release': store.release, 'set_assistance': store.set_assistance}
        for item in data.get('commands', []):
            command = dict(item)
            kind = command.pop('kind')
            if kind not in operations:
                raise ValueError('unknown allocation command')
            if 'approval' in command:
                command['approval'] = AnalystApproval(**command['approval'])
            if 'plan' in command:
                command['plan'] = AssistancePlan(**command['plan'])
            operations[kind](**command)
        print(json.dumps(store.public_plan(as_of=args.as_of), indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        # Avoid echoing private evidence or contact data from malformed input.
        parser.exit(2, 'Invalid evacuation input or allocation command; review the local input.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
