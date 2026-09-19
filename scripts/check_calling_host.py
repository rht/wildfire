#!/usr/bin/env python3
"""Read-only software preflight; never opens audio, Bluetooth, USB or telephony.

Uses only the standard library. Does not execute discovered tools, read .env,
enumerate devices, or report hostnames, usernames, device IDs or tool paths.
Presence is not proof that a driver is loaded or that a call path works.
"""

import json
from pathlib import Path
import platform
import shutil
import sys


def collect_report():
    is_mac = sys.platform == "darwin"
    return {
        "scope": "software_presence_only",
        "os": {"darwin": "macOS", "linux": "Linux", "win32": "Windows"}.get(sys.platform, "other"),
        "macos_version": platform.mac_ver()[0] if is_mac else None,
        "architecture": platform.machine(),
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "tools_on_path": {
            tool: shutil.which(tool) is not None
            for tool in ("swift", "clang", "adb", "scrcpy", "ffmpeg", "asterisk", "bluetoothctl", "pactl", "wpctl")
        },
        "macos_files_present": {
            "facetime_app": Path("/System/Applications/FaceTime.app").exists(),
            "phone_app": Path("/System/Applications/Phone.app").exists(),
            "blackhole_2ch_driver": Path("/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver").exists(),
            "blackhole_16ch_driver": Path("/Library/Audio/Plug-Ins/HAL/BlackHole16ch.driver").exists(),
        } if is_mac else {},
        "call_control": "unverified",
        "two_way_call_audio": "unverified",
        "next_step": "See Workstream B findings in readme.md; hardware tests need a separate targeted action.",
    }


if __name__ == "__main__":
    print(json.dumps(collect_report(), indent=2, sort_keys=True))
