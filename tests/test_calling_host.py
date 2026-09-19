"""Software presence must not execute tools or disclose installation paths."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_calling_host.py"


def load_script():
    assert SCRIPT.is_file(), "read-only calling diagnostic has not been implemented"
    spec = importlib.util.spec_from_file_location("calling_host", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("present", [False, True])
def test_presence_does_not_leak_paths_or_claim_working_calls(monkeypatch, present):
    module = load_script()
    monkeypatch.setattr(module.shutil, "which", lambda _: "/private/SYNTHETIC_SECRET/tool" if present else None)
    monkeypatch.setattr(module.Path, "exists", lambda _: present)
    # Executing an installed tool can start a daemon or enumerate personal devices.
    def forbidden(*args, **kwargs):
        pytest.fail("diagnostic attempted to execute a program")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    report = module.collect_report()
    assert report["tools_on_path"]["adb"] is present
    assert report["tools_on_path"]["asterisk"] is present
    assert report["call_control"] == "unverified"
    assert report["two_way_call_audio"] == "unverified"
    assert "SYNTHETIC_SECRET" not in json.dumps(report)


def test_cli_emits_json_with_empty_path_without_loading_env(tmp_path):
    assert SCRIPT.is_file(), "read-only calling diagnostic has not been implemented"
    (tmp_path / ".env").write_text("SLNG_API_KEY=SYNTHETIC_SECRET\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=tmp_path,
        env={"PATH": "", "SLNG_API_KEY": "SYNTHETIC_SECRET"},
        capture_output=True, text=True, check=True,
    )
    report = json.loads(result.stdout)
    assert not any(report["tools_on_path"].values())
    assert report["two_way_call_audio"] == "unverified"
    assert "SYNTHETIC_SECRET" not in result.stdout + result.stderr
    assert result.stderr == ""
