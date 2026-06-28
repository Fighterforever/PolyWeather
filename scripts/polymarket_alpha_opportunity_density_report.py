#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.opportunity_density_scoreboard import (  # noqa: E402
    build_opportunity_density_scoreboard,
    load_json,
    load_jsonl,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_DISCOVERY = DEFAULT_ROOT / "market_discovery_report.json"
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_FAMILY = DEFAULT_ROOT / "market_family_catalog.json"
DEFAULT_FAMILY_ROWS = DEFAULT_ROOT / "market_family_rows.jsonl"
DEFAULT_PAYOFF = DEFAULT_ROOT / "payoff_arbitrage_report.json"
DEFAULT_OUTPUT = DEFAULT_ROOT / "opportunity_density_scoreboard.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank Polymarket categories by paper-only opportunity density.")
    parser.add_argument("--market-discovery", default=str(DEFAULT_DISCOVERY))
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--family-catalog", default=str(DEFAULT_FAMILY))
    parser.add_argument("--family-rows", default=str(DEFAULT_FAMILY_ROWS))
    parser.add_argument("--payoff-arbitrage", default=str(DEFAULT_PAYOFF))
    parser.add_argument("--summary-output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    family_catalog = load_json(args.family_catalog)
    if not family_catalog.get("families"):
        family_catalog["families"] = load_jsonl(args.family_rows)
    report = build_opportunity_density_scoreboard(
        market_discovery_report=load_json(args.market_discovery),
        active_markets=load_jsonl(args.active_markets),
        family_catalog=family_catalog,
        payoff_arbitrage_report=load_json(args.payoff_arbitrage),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "active_market_count": report.get("active_market_count"),
                "top_focus_categories": report.get("top_focus_categories"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
