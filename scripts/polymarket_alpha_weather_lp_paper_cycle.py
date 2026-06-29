#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_paper_journal import build_weather_lp_paper_cycle, load_jsonl, write_json, write_jsonl  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="evidence/weather_lp_rewards/weather_lp_candidates.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/paper_cycle_report.json")
    parser.add_argument("--quotes-output", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--quote-updates-output", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--fills-output", default="evidence/weather_lp_rewards/paper_fills.jsonl")
    parser.add_argument("--markouts-output", default="evidence/weather_lp_rewards/markouts.jsonl")
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_paper_cycle(candidates=load_jsonl(args.candidates), generated_at=args.generated_at)
    write_jsonl(args.quotes_output, report.get("quotes") or [])
    write_jsonl(args.quote_updates_output, report.get("quote_updates") or [])
    write_jsonl(args.fills_output, report.get("fills") or [])
    write_jsonl(args.markouts_output, report.get("markouts") or [])
    compact = {k: v for k, v in report.items() if k not in {"quotes", "quote_updates", "fills", "markouts"}}
    compact["artifact_paths"] = {"quotes": str(args.quotes_output), "quote_updates": str(args.quote_updates_output), "fills": str(args.fills_output), "markouts": str(args.markouts_output)}
    write_json(args.summary_output, compact)
    print(json.dumps({"paper_quote_count": compact.get("paper_quote_count"), "inferred_fill_count": compact.get("inferred_fill_count"), "reward_points_proxy": compact.get("reward_points_proxy"), "estimated_reward_cents_proxy": compact.get("estimated_reward_cents_proxy"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
