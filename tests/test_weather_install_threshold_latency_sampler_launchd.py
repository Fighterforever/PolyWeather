from __future__ import annotations

import argparse

from scripts.weather_install_threshold_latency_sampler_launchd import build_plist, install_launchd


def _args(tmp_path):
    return argparse.Namespace(
        repo_root=str(tmp_path),
        label="com.polyweather.threshold-latency-sampler",
        intraday_observation_path="evidence/official_observations/intraday_observations.jsonl",
        intraday_manifest_path="evidence/official_observations/manifest.json",
        orderbook_archive_dir="evidence/threshold_latency",
        paper_fill_dir="evidence/threshold_latency",
        signal_report_output="evidence/threshold_latency/threshold_latency_signal_report.json",
        summary_output="evidence/threshold_latency/threshold_latency_execution_sampler_report.json",
        max_candidates=20,
        station_codes=None,
        max_update_age_minutes=5.0,
        max_spread=0.03,
        min_ask_depth=1.0,
        start_interval=60,
        python_path="python3",
        stdout_path=None,
        stderr_path=None,
        plist_path=str(tmp_path / "LaunchAgents" / "com.polyweather.threshold-latency-sampler.plist"),
        install_report_output="evidence/threshold_latency/threshold_latency_sampler_launchd_install_report.json",
    )


def test_threshold_latency_sampler_plist_contains_60s_interval_and_paper_only(tmp_path):
    plist = build_plist(_args(tmp_path))

    assert plist["StartInterval"] == 60
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert "--paper-only" in plist["ProgramArguments"]
    assert "--collect-intraday-before-scan" in plist["ProgramArguments"]
    assert "--station-code" in plist["ProgramArguments"]
    assert "LTAC" in plist["ProgramArguments"]
    assert "UUWW" in plist["ProgramArguments"]
    assert "EGLC" in plist["ProgramArguments"]
    assert "--live" not in plist["ProgramArguments"]
    assert plist["StandardOutPath"].endswith("evidence/logs/threshold_latency_sampler.out.log")
    assert plist["StandardErrorPath"].endswith("evidence/logs/threshold_latency_sampler.err.log")


def test_threshold_latency_sampler_install_report_is_paper_only(tmp_path):
    calls = []

    def fake_run(command, text=True, capture_output=True, check=False):
        calls.append(command)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    report = install_launchd(_args(tmp_path), run=fake_run)

    assert report["installed"] is True
    assert report["start_interval_seconds"] == 60
    assert report["collect_intraday_before_scan"] is True
    assert report["station_codes"] == ["LTAC", "UUWW", "EGLC"]
    assert report["live_order_path"] is False
    assert calls[0][:2] == ["launchctl", "bootstrap"]
