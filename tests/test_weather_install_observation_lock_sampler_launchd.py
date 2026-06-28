from __future__ import annotations

import plistlib
from types import SimpleNamespace

from scripts import weather_install_observation_lock_sampler_launchd as installer


def _args(tmp_path):
    return installer.parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--plist-path",
            str(tmp_path / "LaunchAgents" / "com.polyweather.observation-lock-execution-sampler.plist"),
            "--python-path",
            str(tmp_path / ".venv" / "bin" / "python"),
            "--install-report-output",
            str(tmp_path / "install_report.json"),
        ]
    )


def test_observation_lock_sampler_plist_contains_interval_workdir_and_logs(tmp_path):
    args = _args(tmp_path)
    plist = installer.build_plist(args)

    assert plist["Label"] == "com.polyweather.observation-lock-execution-sampler"
    assert plist["StartInterval"] == 300
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert plist["StandardOutPath"].endswith("evidence/logs/observation_lock_sampler.out.log")
    assert plist["StandardErrorPath"].endswith("evidence/logs/observation_lock_sampler.err.log")
    assert "--paper-only" in plist["ProgramArguments"]
    assert "--intraday-observation-path" in plist["ProgramArguments"]
    assert "evidence/official_observations/intraday_observations.jsonl" in plist["ProgramArguments"]
    assert "--orderbook-archive-dir" in plist["ProgramArguments"]
    assert "evidence/observation_lock_execution" in plist["ProgramArguments"]
    assert "--exclude-dust" in plist["ProgramArguments"]
    assert plist["ProgramArguments"].count("--bucket-type") == 2
    assert "ge" in plist["ProgramArguments"]
    assert "le" in plist["ProgramArguments"]
    assert "--live" not in plist["ProgramArguments"]


def test_observation_lock_sampler_install_writes_plist_and_runs_launchctl(tmp_path):
    args = _args(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = installer.install_launchd(args, run=fake_run)

    plist_path = tmp_path / "LaunchAgents" / "com.polyweather.observation-lock-execution-sampler.plist"
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
