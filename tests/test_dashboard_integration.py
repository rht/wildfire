"""Optional cross-branch verification using an exported, verified coordination module.

DASHBOARD_COORDINATION_EXPORT points to a local copy of sibling coordination.py.
No sibling worktree or operational database is opened or modified.
"""
import importlib.util
import os

import pytest
from starlette.testclient import TestClient

from fireline.dashboard_server import CoordinationDatabase, create_app


def test_verified_coordination_store_publishes_to_read_only_dashboard(tmp_path):
    export = os.environ.get('DASHBOARD_COORDINATION_EXPORT')
    if export:
        spec = importlib.util.spec_from_file_location('fireline.coordination_export', export)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = pytest.importorskip('fireline.coordination')
    from datetime import datetime, timezone
    from fireline.snapshot import build_snapshot
    from tests.test_evacuation_readiness import inputs
    epoch = datetime(2026, 9, 20, 10, tzinfo=timezone.utc)
    scenario, _, centre, route = inputs()
    snapshot = build_snapshot([
        {'asset_id': a.asset_id, 'name': a.name, 'asset_type': 'house',
         'latitude': None, 'longitude': None, 'estimated_occupancy': a.people,
         'occupancy_basis': 'synthetic'} for a in scenario.locations],
        None, scenario_id=scenario.scenario_id, incident_id='dashboard-integration',
        sequence=1, as_of=epoch, input_mode='synthetic')
    snapshot['computed_at'] = epoch.isoformat()
    path = tmp_path / 'coordination.sqlite'
    store = module.CoordinationStore(path, epoch=epoch, clock=lambda: epoch)
    try:
        state = store.refresh(snapshot, scenario, [centre], [route])
        reader = CoordinationDatabase(path)
        assert reader.state() == store.state()
        assert reader.updates(0) == store.updates(0)
        with TestClient(create_app(reader)) as client:
            public = client.get('/api/state').json()
            assert public['revision'] == state['revision']
            assert public['contacts']['review'] == state['contacts']['review']
            with client.websocket_connect('/api/updates?after_revision=0') as ws:
                assert ws.receive_json() == public
            assert client.post('/api/calls').status_code == 404
        assert reader.state() == state
    finally:
        store.close()
