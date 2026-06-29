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
    updates = tmp_path / "updates.json"
    risk = tmp_path / "risk.json"
    cancellation = tmp_path / "cancellation.json"
    optimizer = tmp_path / "optimizer.json"
    quote_updates = tmp_path / "updates.jsonl"
    discovery.write_text(json.dumps({"reward_metadata_available_count": 0, "reward_market_count": 0}), encoding="utf-8")
    strategy.write_text(json.dumps({}), encoding="utf-8")
    paper.write_text(json.dumps({"paper_quote_count": 0}), encoding="utf-8")
    updates.write_text(json.dumps({}), encoding="utf-8")
    risk.write_text(json.dumps({}), encoding="utf-8")
    cancellation.write_text(json.dumps({}), encoding="utf-8")
    optimizer.write_text(json.dumps({}), encoding="utf-8")
    quote_updates.write_text("", encoding="utf-8")
    window.write_text(json.dumps({"observations_count": 0}), encoding="utf-8")
    holder.write_text(json.dumps({"smart_holder_signal_count": 0}), encoding="utf-8")

    report = build_report(
        Namespace(
            discovery_report=discovery,
            strategy_report=strategy,
            paper_cycle_report=paper,
            quote_update_report=updates,
            reward_risk_report=risk,
            cancellation_policy_report=cancellation,
            quote_optimizer_report=optimizer,
            quote_updates=quote_updates,
            reward_window_report=window,
            smart_holder_report=holder,
        )
    )

    assert report["recommendation"] == "reward_metadata_pipeline_broken_or_no_rewards"
    assert report["live_order_path"] is False


def test_weather_lp_controller_requires_enough_quote_updates(tmp_path: Path):
    discovery = tmp_path / "discovery.json"
    strategy = tmp_path / "strategy.json"
    paper = tmp_path / "paper.json"
    updates = tmp_path / "updates.json"
    risk = tmp_path / "risk.json"
    cancellation = tmp_path / "cancellation.json"
    optimizer = tmp_path / "optimizer.json"
    quote_updates = tmp_path / "updates.jsonl"
    window = tmp_path / "window.json"
    holder = tmp_path / "holder.json"
    discovery.write_text(json.dumps({"reward_metadata_available_count": 89, "reward_market_count": 89}), encoding="utf-8")
    strategy.write_text(json.dumps({"reward_qualified_quote_count": 20}), encoding="utf-8")
    paper.write_text(json.dumps({"paper_quote_count": 20, "active_quote_count": 20}), encoding="utf-8")
    updates.write_text(json.dumps({"quote_update_count": 20, "cumulative_reward_points_proxy": 3.0}), encoding="utf-8")
    risk.write_text(json.dumps({"quote_update_count": 20, "cumulative_reward_points_proxy": 3.0, "mean_current_markout": -0.2}), encoding="utf-8")
    cancellation.write_text(json.dumps({"cancellation_policy_recommendation": "continue_collecting_policy_updates"}), encoding="utf-8")
    optimizer.write_text(json.dumps({"selected_quote_count": 20, "rejected_expensive_basket_count": 0}), encoding="utf-8")
    quote_updates.write_text('{"update_time":"2026-06-29T10:00:00Z"}\n{"update_time":"2026-06-29T10:05:00Z"}\n', encoding="utf-8")
    window.write_text(json.dumps({"observations_count": 3, "window_confidence": "insufficient_observations"}), encoding="utf-8")
    holder.write_text(json.dumps({"smart_holder_signal_count": 0}), encoding="utf-8")

    report = build_report(
        Namespace(
            discovery_report=discovery,
            strategy_report=strategy,
            paper_cycle_report=paper,
            quote_update_report=updates,
            reward_risk_report=risk,
            cancellation_policy_report=cancellation,
            quote_optimizer_report=optimizer,
            quote_updates=quote_updates,
            reward_window_report=window,
            smart_holder_report=holder,
        )
    )

    assert report["quote_update_count"] == 20
    assert report["recommendation"] == "continue_weather_lp_paper_insufficient_updates"
    assert report["cancellation_policy_recommendation"] == "continue_collecting_policy_updates"
    assert report["quote_optimizer_selected_count"] == 20
