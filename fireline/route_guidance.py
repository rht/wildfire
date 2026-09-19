"""Active, incident-scoped road restrictions supplied by the analyst/upstream feed.

These records describe reported hazards; they neither discover roads nor certify routes.
"""
import re
import hashlib
import json


def validate_road_ids(road_ids):
    if not isinstance(road_ids, (list, tuple)) or any(
        not isinstance(r, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', r)
        for r in road_ids
    ) or len(set(road_ids)) != len(road_ids):
        raise ValueError('invalid road_ids')


def validate_road_warnings(warnings):
    if not isinstance(warnings, (list, tuple)) or len(warnings) > 50:
        raise ValueError('invalid road_warnings')
    for warning in warnings:
        if not isinstance(warning, dict) or set(warning) != {'road_id', 'road_name', 'reason', 'source'}:
            raise ValueError('invalid road warning fields')
        for key in ('road_name', 'reason', 'source'):
            if not isinstance(warning[key], str) or not warning[key].strip() or len(warning[key]) > 512:
                raise ValueError('invalid road warning ' + key)
    validate_road_ids([w['road_id'] for w in warnings])


def road_warning_brief(warnings):
    validate_road_warnings(warnings)
    if not warnings:
        return 'No road restrictions supplied. This does not establish that any road is safe.'
    return '\n'.join(f"Do not take {w['road_name']}: {w['reason']}. Source: {w['source']}." for w in warnings)


def road_warning_version(warnings):
    validate_road_warnings(warnings)
    canonical = json.dumps(sorted(warnings, key=lambda w: w['road_id']), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()
