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
    assert app.dataframe[0].value.set_index('asset_id').loc['B', 'mode'] == 'self_evacuate'


def test_main_dashboard_can_open_directly_in_mock_mode(tmp_path, monkeypatch):
    monkeypatch.setenv('FIRELINE_MOCK_DB_DIR', str(tmp_path))
    monkeypatch.setenv('FIRELINE_DB', str(tmp_path / 'incident.sqlite'))
    app = AppTest.from_file('../fireline/app.py', default_timeout=10)
    app.query_params['demo'] = 'voice'
    app.run()
    assert not app.exception
    assert app.title[0].value == 'Mock voice scenarios'
