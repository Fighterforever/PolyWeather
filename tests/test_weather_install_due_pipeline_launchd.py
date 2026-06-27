from __future__ import annotations

import plistlib
from types import SimpleNamespace

from scripts import weather_install_due_pipeline_launchd as installer


def _args(tmp_path):
    return installer.parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--plist-path",
            str(tmp_path / "LaunchAgents" / "com.polyweather.due-evidence-pipeline.plist"),
            "--python-path",
            str(tmp_path / ".venv" / "bin" / "python"),
        ]
    )


def test_launchd_plist_contains_interval_workdir_and_logs(tmp_path):
    args = _args(tmp_path)
    plist = installer.build_plist(args)

    assert plist["Label"] == "com.polyweather.due-evidence-pipeline"
    assert plist["StartInterval"] == 300
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert plist["StandardOutPath"].endswith("evidence/logs/due_pipeline_launchd.out.log")
    assert plist["StandardErrorPath"].endswith("evidence/logs/due_pipeline_launchd.err.log")
    assert "--run-after-utc" in plist["ProgramArguments"]
    assert "2026-06-28T03:05:00Z" in plist["ProgramArguments"]
    assert "--include-settlement-source" in plist["ProgramArguments"]
    assert "metar" in plist["ProgramArguments"]
    assert plist["ProgramArguments"].count("--include-station-code") == 2
    assert "LTAC" in plist["ProgramArguments"]
    assert "UUWW" in plist["ProgramArguments"]
    assert "--execute-closed-backfill" not in plist["ProgramArguments"]
    assert "--live" not in plist["ProgramArguments"]


def test_launchd_install_writes_plist_and_runs_bootstrap_enable_kickstart(tmp_path):
    args = _args(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = installer.install_launchd(args, run=fake_run)

    plist_path = tmp_path / "LaunchAgents" / "com.polyweather.due-evidence-pipeline.plist"
    assert report["installed"] is True
    assert report["live_order_path"] is False
    assert report["start_interval_seconds"] == 300
    assert calls[0][:2] == ["launchctl", "bootstrap"]
    assert calls[1][:2] == ["launchctl", "enable"]
    assert calls[2][:3] == ["launchctl", "kickstart", "-k"]
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["StartInterval"] == 300
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
