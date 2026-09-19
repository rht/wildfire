import json
import subprocess
import sys

from streamlit.testing.v1 import AppTest


def test_cli_report_contains_both_algorithms_and_persists_state(tmp_path):
    output = tmp_path / 'report.json'
    proc = subprocess.run([sys.executable, 'scripts/replay_voice_scenarios.py', '--case', 'baseline',
        '--db-dir', str(tmp_path / 'db'), '--output', str(output)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(output.read_text())
    baseline = report['cases'][0]
    assert baseline['contact_order'] == ['A', 'B', 'C']
    assert baseline['response_order'] == ['C', 'A']
    assert baseline['modes']['B'] == 'self_evacuate'
    assert baseline['passed'] is True
    assert report['live_validation'] is False


def test_panel_receives_mock_result_and_refreshes_from_persistent_state(tmp_path, monkeypatch):
    monkeypatch.setenv('FIRELINE_MOCK_DB_DIR', str(tmp_path))
    app = AppTest.from_string('from fireline.voice_demo_panel import render_voice_demo\nrender_voice_demo()', default_timeout=10).run()
    assert not app.exception
    app.selectbox(key='mock_case').set_value('baseline').run()
    assert any('Contact order: A' in m.value for m in app.markdown)
    app.button(key='mock_apply_next').click().run()
    assert not app.exception
    rows = app.dataframe[0].value
    assert rows.set_index('asset_id').loc['A', 'mode'] == 'assisted_evacuation'
    # External CLI/worker-style update; next UI refresh must see it, including after reload.
    from fireline.voice_replay import MockReplay
    s = MockReplay(tmp_path / 'baseline.sqlite', 'baseline')
    s.advance()
    s.close()
    app.run()
    assert app.dataframe[0].value.set_index('asset_id').loc['B', 'mode'] == 'self_evacuate'
    app = AppTest.from_string('from fireline.voice_demo_panel import render_voice_demo\nrender_voice_demo()', default_timeout=10).run()
    assert not app.exception
    app.selectbox(key='mock_case').set_value('baseline').run()
    assert app.dataframe[0].value.set_index('asset_id').loc['B', 'mode'] == 'self_evacuate'


def test_main_dashboard_can_open_directly_in_mock_mode(tmp_path, monkeypatch):
    monkeypatch.setenv('FIRELINE_MOCK_DB_DIR', str(tmp_path))
    monkeypatch.setenv('FIRELINE_DB', str(tmp_path / 'incident.sqlite'))
    app = AppTest.from_file('../fireline/app.py', default_timeout=10)
    app.query_params['demo'] = 'voice'
    app.run()
    assert not app.exception
    assert app.title[0].value == 'Mock voice scenarios'


def test_neighbourhood_ui_displays_buildings_capacity_and_event_updates(tmp_path, monkeypatch):
    monkeypatch.setenv('FIRELINE_MOCK_DB_DIR', str(tmp_path))
    app = AppTest.from_string('from fireline.voice_demo_panel import render_voice_demo\nrender_voice_demo()', default_timeout=10).run()
    assert app.selectbox(key='mock_case').value == 'village_shared_capacity'
    assert not app.exception
    assert any('49 people' in c.value for c in app.caption)
    assert any('Pine care home' in c.value for c in app.info)
    app.button(key='mock_apply_all').click().run()
    assert not app.exception
    rows = app.dataframe[0].value
    assert len(rows) == 6
    assert rows.set_index('asset_id').loc['H2', 'destination_id'] == 'ANNEX'
    capacities = next(d.value for d in app.dataframe if 'proposed_remaining_places' in d.value.columns)
    assert capacities['proposed_remaining_places'].sum() == 0
    assert any('Local layout' in c.value for c in app.subheader)


def test_cli_supports_buildings_other_than_abc(tmp_path):
    output = tmp_path / 'village.json'
    proc = subprocess.run([sys.executable, 'scripts/replay_voice_scenarios.py', '--case', 'village_shared_capacity',
        '--db-dir', str(tmp_path / 'db'), '--output', str(output)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    case = json.loads(output.read_text())['cases'][0]
    assert case['passed'] is True
    assert len(case['modes']) == 6
    assert case['destinations']['H2'] == 'ANNEX'
    assert case['remaining_capacity'] == {'HALL': 0, 'ANNEX': 0}
