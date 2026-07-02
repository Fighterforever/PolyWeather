from __future__ import annotations

import plistlib
from types import SimpleNamespace

from scripts import polymarket_alpha_install_crypto_touch_watcher_launchd as installer


def _args(tmp_path):
    return installer.parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--plist-path",
            str(tmp_path / "LaunchAgents" / "com.polyweather.crypto-touch-near-miss-watcher.plist"),
            "--python-path",
            str(tmp_path / ".venv" / "bin" / "python"),
            "--install-report-output",
            str(tmp_path / "install_report.json"),
        ]
    )


def test_crypto_touch_watcher_launchd_plist_contains_paper_only_args(tmp_path):
    args = _args(tmp_path)
    plist = installer.build_plist(args)

    assert plist["Label"] == "com.polyweather.crypto-touch-near-miss-watcher"
    assert plist["StartInterval"] == 60
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert plist["StandardOutPath"].endswith("evidence/polymarket_alpha/logs/crypto_touch_watcher.out.log")
    assert plist["StandardErrorPath"].endswith("evidence/polymarket_alpha/logs/crypto_touch_watcher.err.log")
    argv = plist["ProgramArguments"]
    assert "scripts/polymarket_alpha_crypto_touch_watcher_report.py" in argv[1]
    assert "--min-edge" in argv
    assert "0.01" in argv
    assert "--live" not in argv


def test_crypto_touch_watcher_launchd_install_writes_plist_and_report(tmp_path):
    args = _args(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = installer.install_launchd(args, run=fake_run)

    plist_path = tmp_path / "LaunchAgents" / "com.polyweather.crypto-touch-near-miss-watcher.plist"
    assert report["installed"] is True
    assert report["live_order_path"] is False
    assert report["start_interval_seconds"] == 60
    assert calls[0][:2] == ["launchctl", "bootstrap"]
    assert calls[1][:2] == ["launchctl", "enable"]
    assert calls[2][:3] == ["launchctl", "kickstart", "-k"]
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["StartInterval"] == 60
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
