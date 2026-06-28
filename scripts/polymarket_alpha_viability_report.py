#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.alpha_viability_scoreboard import (  # noqa: E402
    build_alpha_viability_scoreboard,
    load_json,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_DENSITY = DEFAULT_ROOT / "opportunity_density_scoreboard.json"
DEFAULT_PAYOFF = DEFAULT_ROOT / "payoff_arbitrage_report.json"
DEFAULT_MAKER = DEFAULT_ROOT / "maker_shadow" / "report.json"
DEFAULT_RULE = DEFAULT_ROOT / "rule_confusion_report.json"
DEFAULT_WEATHER = Path("evidence/polymarket_weather_live_push_decision.json")
DEFAULT_OUTPUT = DEFAULT_ROOT / "alpha_viability_scoreboard.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Polymarket-only all-market alpha viability scoreboard.")
    parser.add_argument("--opportunity-density", default=str(DEFAULT_DENSITY))
    parser.add_argument("--payoff-arbitrage", default=str(DEFAULT_PAYOFF))
    parser.add_argument("--maker-shadow", default=str(DEFAULT_MAKER))
    parser.add_argument("--rule-confusion", default=str(DEFAULT_RULE))
    parser.add_argument("--weather-decision", default=str(DEFAULT_WEATHER))
    parser.add_argument("--summary-output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_alpha_viability_scoreboard(
        opportunity_density_report=load_json(args.opportunity_density),
        payoff_arbitrage_report=load_json(args.payoff_arbitrage),
        maker_shadow_report=load_json(args.maker_shadow),
        rule_confusion_report=load_json(args.rule_confusion),
        weather_decision_report=load_json(args.weather_decision),
    )
    write_json(args.summary_output, report)
    print(json.dumps(report.get("summary") or {}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
