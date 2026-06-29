#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.external_strategy_ingestion import build_weather_lp_strategy_card, write_json  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--note-path", default="docs/external_strategy_notes/polymarket_weather_lp_rewards_note.md")
    parser.add_argument("--summary-output", default="evidence/polymarket_alpha/external_strategy/weather_lp_reward_strategy_card.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    card = build_weather_lp_strategy_card(args.note_path)
    write_json(args.summary_output, card)
    print(json.dumps({"strategy_id": card["strategy_id"], "live_order_path": card["live_order_path"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
