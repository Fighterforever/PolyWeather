from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from scripts.polymarket_alpha_weather_lp_experiment_controller import build_report


def test_weather_lp_controller_requires_reward_metadata(tmp_path: Path):
    discovery = tmp_path / "discovery.json"
    strategy = tmp_path / "strategy.json"
    paper = tmp_path / "paper.json"
    window = tmp_path / "window.json"
    holder = tmp_path / "holder.json"
    discovery.write_text(json.dumps({"reward_metadata_available_count": 0, "reward_market_count": 0}), encoding="utf-8")
    strategy.write_text(json.dumps({}), encoding="utf-8")
    paper.write_text(json.dumps({"paper_quote_count": 0}), encoding="utf-8")
    window.write_text(json.dumps({"observations_count": 0}), encoding="utf-8")
    holder.write_text(json.dumps({"smart_holder_signal_count": 0}), encoding="utf-8")

    report = build_report(
        Namespace(
            discovery_report=discovery,
            strategy_report=strategy,
            paper_cycle_report=paper,
            reward_window_report=window,
            smart_holder_report=holder,
        )
    )

    assert report["recommendation"] == "insufficient_reward_metadata"
    assert report["live_order_path"] is False
