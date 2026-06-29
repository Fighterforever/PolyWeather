#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.probability_dataset import write_json  # noqa: E402


def _load(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _mean(report: Dict[str, Any], key: str) -> Any:
    return report.get(key)


def build_report(args: argparse.Namespace) -> Dict[str, Any]:
    discovery = _load(args.discovery_report)
    strategy = _load(args.strategy_report)
    paper = _load(args.paper_cycle_report)
    window = _load(args.reward_window_report)
    holder = _load(args.smart_holder_report)
    reward_cents = paper.get("estimated_reward_cents")
    reward_cents_proxy = paper.get("estimated_reward_cents_proxy")
    reward_points_proxy = paper.get("reward_points_proxy")
    pnl_without = paper.get("net_estimated_pnl_without_reward")
    pnl_with = paper.get("net_estimated_pnl_with_reward_proxy", paper.get("net_estimated_pnl_with_reward"))
    quote_count = int(paper.get("paper_quote_count") or 0)
    if discovery.get("reward_metadata_available_count", 0) == 0:
        recommendation = "reward_metadata_pipeline_broken_or_no_rewards"
    elif quote_count < 50:
        recommendation = "continue_weather_lp_paper"
    elif pnl_with is not None and pnl_without is not None and pnl_with > 0 and pnl_without > -float(reward_cents_proxy or reward_cents or 0):
        recommendation = "continue_weather_lp_paper"
    else:
        recommendation = "reduce_lp_strategy"
    return {
        "schema_version": "polyweather_polymarket_alpha_weather_lp_experiment_controller.v1",
        "run_count": window.get("observations_count", 0),
        "reward_market_count": discovery.get("reward_market_count", 0),
        "reward_metadata_available_count": discovery.get("reward_metadata_available_count", 0),
        "reward_qualified_quote_count": strategy.get("reward_qualified_quote_count", 0),
        "paper_quote_count": quote_count,
        "active_quote_count": paper.get("active_quote_count", quote_count),
        "inferred_fill_count": paper.get("inferred_fill_count", 0),
        "estimated_reward_points": paper.get("estimated_reward_points"),
        "reward_points_proxy": reward_points_proxy,
        "estimated_reward_cents": reward_cents,
        "estimated_reward_cents_proxy": reward_cents_proxy,
        "markout_count": paper.get("markout_count", 0),
        "mean_markout_5m": _mean(paper, "mean_5m_markout"),
        "mean_markout_15m": _mean(paper, "mean_15m_markout"),
        "mean_markout_1h": _mean(paper, "mean_1h_markout"),
        "mean_5m_markout": _mean(paper, "mean_5m_markout"),
        "mean_15m_markout": _mean(paper, "mean_15m_markout"),
        "mean_1h_markout": _mean(paper, "mean_1h_markout"),
        "adverse_selection_count": paper.get("adverse_selection_count", 0),
        "net_estimated_pnl_with_reward": pnl_with,
        "net_estimated_pnl_with_reward_proxy": pnl_with,
        "net_estimated_pnl_without_reward": pnl_without,
        "by_city": [],
        "by_strategy_variant": [],
        "expensive_basket_rejection_count": strategy.get("expensive_basket_rejection_count", 0),
        "smart_holder_signal_count": holder.get("smart_holder_signal_count", 0),
        "time_window_confidence": window.get("window_confidence") or window.get("confidence"),
        "recommendation": recommendation,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery-report", default="evidence/weather_lp_rewards/lp_reward_discovery_report.json")
    parser.add_argument("--strategy-report", default="evidence/weather_lp_rewards/weather_lp_strategy_report.json")
    parser.add_argument("--paper-cycle-report", default="evidence/weather_lp_rewards/paper_cycle_report.json")
    parser.add_argument("--reward-window-report", default="evidence/weather_lp_rewards/reward_window_report.json")
    parser.add_argument("--smart-holder-report", default="evidence/weather_lp_rewards/smart_holder_signal_report.json")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/weather_lp_experiment_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_report(args)
    write_json(args.summary_output, report)
    print(json.dumps({"paper_quote_count": report.get("paper_quote_count"), "recommendation": report.get("recommendation"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
