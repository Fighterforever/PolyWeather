#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.alpha_tournament_scoreboard import (  # noqa: E402
    build_alpha_tournament_scoreboard,
    load_json,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_REPORT = DEFAULT_ROOT / "alpha_tournament_scoreboard.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only Polymarket alpha tournament scoreboard.")
    parser.add_argument("--crypto-touch-validation-report", default=str(DEFAULT_ROOT / "crypto_touch_forward_validation_report.json"))
    parser.add_argument("--crypto-terminal-report", default=str(DEFAULT_ROOT / "crypto_terminal_edge_report.json"))
    parser.add_argument("--microstructure-report", default=str(DEFAULT_ROOT / "microstructure_edge_report.json"))
    parser.add_argument("--microstructure-markout-report", default=str(DEFAULT_ROOT / "microstructure_markout_report.json"))
    parser.add_argument("--maker-shadow-report", default=str(DEFAULT_ROOT / "maker_shadow" / "report.json"))
    parser.add_argument("--payoff-arbitrage-report", default=str(DEFAULT_ROOT / "payoff_arbitrage_report.json"))
    parser.add_argument("--global-oos-report", default=str(DEFAULT_ROOT / "probability_edge_model_report.json"))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_alpha_tournament_scoreboard(
        crypto_touch_validation_report=load_json(args.crypto_touch_validation_report),
        crypto_terminal_report=load_json(args.crypto_terminal_report),
        microstructure_report=load_json(args.microstructure_report),
        microstructure_markout_report=load_json(args.microstructure_markout_report),
        maker_shadow_report=load_json(args.maker_shadow_report),
        payoff_arbitrage_report=load_json(args.payoff_arbitrage_report),
        global_oos_report=load_json(args.global_oos_report),
    )
    report["artifact_paths"] = {
        "crypto_touch_validation_report": str(args.crypto_touch_validation_report),
        "crypto_terminal_report": str(args.crypto_terminal_report),
        "microstructure_report": str(args.microstructure_report),
        "microstructure_markout_report": str(args.microstructure_markout_report),
        "maker_shadow_report": str(args.maker_shadow_report),
        "payoff_arbitrage_report": str(args.payoff_arbitrage_report),
        "global_oos_report": str(args.global_oos_report),
    }
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "top_lane": report.get("top_lane"),
                "lanes_downgraded": report.get("lanes_downgraded"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
