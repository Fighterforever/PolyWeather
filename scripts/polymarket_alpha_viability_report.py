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
DEFAULT_MAKER_FOCUS = DEFAULT_ROOT / "maker_shadow_focus" / "report.json"
DEFAULT_RULE = DEFAULT_ROOT / "rule_confusion_report.json"
DEFAULT_WEATHER = Path("evidence/polymarket_weather_live_push_decision.json")
DEFAULT_MODEL = DEFAULT_ROOT / "probability_edge_model_report.json"
DEFAULT_ACTIVE_EDGE = DEFAULT_ROOT / "active_probability_edge_report.json"
DEFAULT_FOCUS = DEFAULT_ROOT / "category_focus_report.json"
DEFAULT_MARKOUT = DEFAULT_ROOT / "probability_edge_paper" / "markout_report.json"
DEFAULT_RESOLVED_AUDIT = DEFAULT_ROOT / "probability_edge_paper" / "resolved_audit_report.json"
DEFAULT_OUTPUT = DEFAULT_ROOT / "alpha_viability_scoreboard.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Polymarket-only all-market alpha viability scoreboard.")
    parser.add_argument("--opportunity-density", default=str(DEFAULT_DENSITY))
    parser.add_argument("--payoff-arbitrage", default=str(DEFAULT_PAYOFF))
    parser.add_argument("--maker-shadow", default=str(DEFAULT_MAKER))
    parser.add_argument("--maker-shadow-focus", default=str(DEFAULT_MAKER_FOCUS))
    parser.add_argument("--rule-confusion", default=str(DEFAULT_RULE))
    parser.add_argument("--weather-decision", default=str(DEFAULT_WEATHER))
    parser.add_argument("--probability-edge-model", default=str(DEFAULT_MODEL))
    parser.add_argument("--active-probability-edge", default=str(DEFAULT_ACTIVE_EDGE))
    parser.add_argument("--category-focus", default=str(DEFAULT_FOCUS))
    parser.add_argument("--probability-markout", default=str(DEFAULT_MARKOUT))
    parser.add_argument("--probability-resolved-audit", default=str(DEFAULT_RESOLVED_AUDIT))
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
        probability_edge_model_report=load_json(args.probability_edge_model),
        active_probability_edge_report=load_json(args.active_probability_edge),
        category_focus_report=load_json(args.category_focus),
        probability_markout_report=load_json(args.probability_markout),
        probability_resolved_audit_report=load_json(args.probability_resolved_audit),
        maker_shadow_focus_report=load_json(args.maker_shadow_focus),
    )
    write_json(args.summary_output, report)
    print(json.dumps(report.get("summary") or {}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
