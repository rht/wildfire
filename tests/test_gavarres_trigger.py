"""The real Gavarres fire reaches the incident runtime in recorded mode through the committed triggers."""

import json
from pathlib import Path

from shapely import get_coordinates
from shapely.geometry import shape

from fireline.dashboard_public import public_state
from fireline.incident_runtime import IncidentRuntime

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures/incidents/gavarres_real"


def test_recorded_triggers_carry_the_real_perimeter(tmp_path):
    settings = json.loads((FIXTURES / "settings.json").read_text())
    settings["catalog_file"] = str(FIXTURES / settings["catalog_file"])
    runtime = IncidentRuntime(tmp_path, settings)
    for n in (1, 2, 3):
        state = runtime.trigger(json.loads((FIXTURES / f"trigger-{n:04d}.json").read_text()))
        assert state["input_mode"] == "recorded"
        assert state["fire_geometry_kind"] == "perimeter"
        assert state["fire_geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert len(get_coordinates(shape(state["fire_geometry"]))) >= 40
        assert sum(a["latitude"] is not None for a in state["assets"]) >= 20
    assert public_state(runtime.state())["fire_geometry"] == state["fire_geometry"]
