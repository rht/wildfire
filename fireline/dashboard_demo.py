"""Offline illustration through the same read-only store interface; no call worker."""
from copy import deepcopy
import json
from pathlib import Path

from .contact_priority import ContactPolicy, rank_contacts
from .priority_models import Location


class DemoStore:
    """Static, explicitly labelled fixture. Updates can only replay its one revision."""
    def __init__(self):
        assets = json.loads((Path(__file__).resolve().parents[1] /
                             'fixtures/dashboard/assets.json').read_text())
        locations = [Location(
            asset_id=a['asset_id'], name=a['name'], x_m=0, y_m=0,
            distance_m=a['distance_to_fire_m'], people=a['estimated_occupancy'],
            assisted=None, value=a['value_score'], deadline_min=None,
            fire_arrival_min=20 + i * 10, evacuation_min=None if a['needs_review'] else 25,
            forecast_source='illustrative fixture', evacuation_source='illustrative fixture',
        ) for i, a in enumerate(assets)]
        self._state = {
            'schema_version': 'coordination-state-1', 'scenario_id': 'offline-dashboard',
            'snapshot_id': 'offline-1', 'revision': 1, 'as_of': '2026-09-20T10:00:00Z',
            'epoch': '2026-09-20T10:00:00Z', 'input_mode': 'offline_demo',
            'assets': assets, 'contacts': rank_contacts(locations, ContactPolicy()),
            'calls': [{'request_id': 'offline-example', 'asset_id': assets[1]['asset_id'],
                       'status': 'completed', 'needs_assistance': True, 'wants_human': False,
                       'acknowledged': True, 'departure_confirmed': None,
                       'arrival_confirmed': None, 'source': 'illustrative fixture'}],
            'plan': {'locations': [], 'remaining_capacity': {}, 'response': None},
            'teams': [], 'tasks': [], 'events': [{'kind': 'offline_demo',
                        'as_of': '2026-09-20T10:00:00Z', 'notes': 'Illustrative offline data; no calls dispatched.'}],
            'errors': [],
        }

    def state(self):
        return deepcopy(self._state)

    def updates(self, after_revision=0):
        return [self.state()] if after_revision < self._state['revision'] else []
