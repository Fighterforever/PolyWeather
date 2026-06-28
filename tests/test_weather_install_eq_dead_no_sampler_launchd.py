from __future__ import annotations

import argparse

from scripts.weather_install_eq_dead_no_sampler_launchd import build_plist, build_program_arguments


def _args(tmp_path):
    return argparse.Namespace(
        repo_root=str(tmp_path),
        label="com.polyweather.eq-dead-no-sampler",
        intraday_observation_path="evidence/official_observations/intraday_observations.jsonl",
        intraday_manifest_path="evidence/official_observations/manifest.json",
        orderbook_archive_dir="evidence/eq_dead_no",
        paper_fill_dir="evidence/eq_dead_no",
        summary_output="evidence/eq_dead_no/eq_dead_no_execution_sampler_report.json",
        max_candidates=20,
        max_entry_price=0.95,
        min_ask_depth=1.0,
        station_codes=None,
        start_interval=60,
        python_path="/usr/bin/python3",
        stdout_path=None,
        stderr_path=None,
        plist_path=str(tmp_path / "agent.plist"),
    )


def test_eq_dead_no_launchd_plist_runs_sampler_every_60_seconds(tmp_path):
    args = _args(tmp_path)
    plist = build_plist(args)

    assert plist["Label"] == "com.polyweather.eq-dead-no-sampler"
    assert plist["StartInterval"] == 60
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert plist["StandardOutPath"].endswith("evidence/logs/eq_dead_no_sampler.out.log")
    assert plist["StandardErrorPath"].endswith("evidence/logs/eq_dead_no_sampler.err.log")
    program = plist["ProgramArguments"]
    assert "scripts/weather_eq_dead_no_execution_sampler.py" in program[1]
    assert "--paper-only" in program
    assert "--collect-intraday-before-scan" in program
    assert "--exclude-dust" in program
    assert "--max-entry-price" in program
    assert "0.95" in program


def test_eq_dead_no_program_arguments_include_default_stations(tmp_path):
    program = build_program_arguments(_args(tmp_path))

    assert program.count("--station-code") == 3
    assert "LTAC" in program
    assert "UUWW" in program
    assert "EGLC" in program
