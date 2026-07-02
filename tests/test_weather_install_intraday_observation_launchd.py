from __future__ import annotations

import plistlib
from types import SimpleNamespace

from scripts import weather_install_intraday_observation_launchd as installer


def _args(tmp_path):
    return installer.parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--plist-path",
            str(tmp_path / "LaunchAgents" / "com.polyweather.intraday-observation-collector.plist"),
            "--python-path",
            str(tmp_path / ".venv" / "bin" / "python"),
        ]
    )


def test_intraday_observation_launchd_plist_contains_expected_schedule_and_paths(tmp_path):
    plist = installer.build_plist(_args(tmp_path))

    assert plist["Label"] == "com.polyweather.intraday-observation-collector"
    assert plist["StartInterval"] == 300
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert "weather_collect_intraday_observations.py" in plist["ProgramArguments"][1]
    assert plist["ProgramArguments"].count("--station-code") == 3
    assert "LTAC" in plist["ProgramArguments"]
    assert "UUWW" in plist["ProgramArguments"]
    assert "EGLC" in plist["ProgramArguments"]
    assert "evidence/official_observations/intraday_observations.jsonl" in plist["ProgramArguments"]
    assert plist["StandardOutPath"].endswith("evidence/logs/intraday_observation_collector.out.log")
    assert plist["StandardErrorPath"].endswith("evidence/logs/intraday_observation_collector.err.log")


def test_intraday_observation_launchd_install_report_is_paper_only(tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = installer.install_launchd(_args(tmp_path), run=fake_run)

    assert report["installed"] is True
    assert report["live_order_path"] is False
    assert report["station_codes"] == ["LTAC", "UUWW", "EGLC"]
    assert calls[0][:2] == ["launchctl", "bootstrap"]
    with (tmp_path / "LaunchAgents" / "com.polyweather.intraday-observation-collector.plist").open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["StartInterval"] == 300
