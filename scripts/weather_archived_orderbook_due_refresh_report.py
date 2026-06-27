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

from src.trading.polymarket_orderbook_archive import DEFAULT_ORDERBOOK_ARCHIVE_DIR  # noqa: E402
from src.trading.weather_closed_backfill import (  # noqa: E402
    DEFAULT_BACKFILL_DIR,
    run_targeted_closed_weather_backfill_from_market_slugs,
)
from src.trading.weather_closed_historical_replay import build_preresolution_orderbook_replay_report_from_dirs  # noqa: E402
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements  # noqa: E402
from src.trading.weather_orderbook_archive_coverage import build_orderbook_closed_token_coverage_report  # noqa: E402
from src.trading.weather_paper_journal import load_jsonl  # noqa: E402


SCHEMA_VERSION = "polyweather_archived_orderbook_due_refresh_report.v1"
CONFIRM_TOKEN = "PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH"


def _summary_preresolution_replay(report: Dict[str, Any]) -> Dict[str, Any]:
    replay = report.get("replay") if isinstance(report.get("replay"), dict) else {}
    evidence = report.get("historical_evidence") if isinstance(report.get("historical_evidence"), dict) else {}
    input_summary = report.get("input_summary") if isinstance(report.get("input_summary"), dict) else {}
    return {
        "schema_version": report.get("schema_version"),
        "paper_only": report.get("paper_only"),
        "diagnostic_only": report.get("diagnostic_only"),
        "counts_for_live_gate": report.get("counts_for_live_gate"),
        "hard_conclusion": report.get("hard_conclusion"),
        "input_summary": input_summary,
        "resolved_outcome_count": report.get("resolved_outcome_count"),
        "replay": {
            key: replay.get(key)
            for key in (
                "candidate_count",
                "fill_count",
                "missed_fill_count",
                "no_visible_orderbook_count",
                "missing_resolution_count",
                "resolved_pnl_usdc",
                "mean_resolved_pnl_per_share",
                "fill_rate",
            )
        },
        "historical_evidence": {
            key: evidence.get(key)
            for key in ("supplement_count", "gap_count", "gaps_by_reason")
        },
    }


def _build_plan_report(
    *,
    backfill_dir: Path,
    orderbook_archive_dir: Path,
    generated_at: str | None,
    max_samples: int,
) -> Dict[str, Any]:
    return build_orderbook_closed_token_coverage_report(
        closed_records=load_closed_backfill_records_with_snapshot_supplements(backfill_dir),
        orderbook_snapshots=load_jsonl(orderbook_archive_dir / "orderbook_snapshots.jsonl"),
        generated_at=generated_at,
        max_samples=max(0, int(max_samples)),
    )


def build_due_refresh_report(
    *,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    orderbook_archive_dir: str | Path = DEFAULT_ORDERBOOK_ARCHIVE_DIR,
    generated_at: str | None = None,
    replay_time: str | None = None,
    size: float = 1.0,
    max_samples: int = 1000,
    execute: bool = False,
    confirm: str | None = None,
) -> Dict[str, Any]:
    backfill_root = Path(backfill_dir)
    orderbook_root = Path(orderbook_archive_dir)
    plan_report = _build_plan_report(
        backfill_dir=backfill_root,
        orderbook_archive_dir=orderbook_root,
        generated_at=generated_at,
        max_samples=max_samples,
    )
    followup = (
        plan_report.get("closed_backfill_followup_plan")
        if isinstance(plan_report.get("closed_backfill_followup_plan"), dict)
        else {}
    )
    market_queries = [
        str(value)
        for value in (followup.get("market_queries") or [])
        if str(value or "").strip()
    ]
    market_query_count = int(followup.get("market_query_count") or 0)
    execution: Dict[str, Any] = {
        "paper_only": True,
        "counts_for_live_gate": False,
        "execute_requested": bool(execute),
        "confirm_required": CONFIRM_TOKEN,
        "due_market_query_count": market_query_count,
        "loaded_market_query_count": len(market_queries),
        "status": "dry_run",
    }
    replay_report = None

    if execute:
        if confirm != CONFIRM_TOKEN:
            execution["status"] = "confirm_missing"
        elif market_query_count <= 0:
            execution["status"] = "no_due_markets"
        elif len(market_queries) < market_query_count:
            execution["status"] = "due_market_query_list_truncated"
        else:
            backfill_result = run_targeted_closed_weather_backfill_from_market_slugs(
                market_slugs=market_queries,
                backfill_dir=backfill_root,
            )
            replay_report = build_preresolution_orderbook_replay_report_from_dirs(
                backfill_dir=backfill_root,
                orderbook_archive_dir=orderbook_root,
                replay_time=replay_time or plan_report.get("generated_at"),
                size=float(size),
            )
            execution.update(
                {
                    "status": "executed",
                    "targeted_backfill_result": backfill_result,
                    "preresolution_replay_summary": _summary_preresolution_replay(replay_report),
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "backfill_dir": str(backfill_root),
        "orderbook_archive_dir": str(orderbook_root),
        "generated_at": plan_report.get("generated_at"),
        "plan_report": plan_report,
        "execution": execution,
        "preresolution_replay_report": replay_report,
        "hard_conclusion": (
            "archived_orderbook_due_refresh_executed"
            if execution["status"] == "executed"
            else "archived_orderbook_due_refresh_not_executed"
        ),
    }


def _summary_only(report: Dict[str, Any]) -> Dict[str, Any]:
    trimmed = dict(report)
    plan = dict(trimmed.get("plan_report") or {})
    plan.pop("matched_samples", None)
    plan.pop("closed_markets_missing_archive_samples", None)
    plan.pop("archived_markets_pending_closed_backfill_samples", None)
    plan.pop("closed_missing_token_samples", None)
    followup = dict(plan.get("closed_backfill_followup_plan") or {})
    followup.pop("requests", None)
    followup.pop("await_market_end_samples", None)
    followup.pop("missing_end_time_samples", None)
    followup.pop("next_await_market_queries", None)
    plan["closed_backfill_followup_plan"] = followup
    trimmed["plan_report"] = plan
    if trimmed.get("preresolution_replay_report"):
        trimmed["preresolution_replay_report"] = _summary_preresolution_replay(
            trimmed["preresolution_replay_report"]
        )
    return trimmed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Paper-only orchestration: find due archived weather orderbooks, optionally "
            "refresh exact-slug closed backfill, then rerun pre-resolution replay."
        ),
    )
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--orderbook-archive-dir", default=str(DEFAULT_ORDERBOOK_ARCHIVE_DIR))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--replay-time", default=None)
    parser.add_argument("--size", type=float, default=1.0)
    parser.add_argument("--max-samples", type=int, default=1000)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default=None)
    parser.add_argument("--summary-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_due_refresh_report(
        backfill_dir=args.backfill_dir,
        orderbook_archive_dir=args.orderbook_archive_dir,
        generated_at=args.generated_at,
        replay_time=args.replay_time,
        size=float(args.size),
        max_samples=max(0, int(args.max_samples)),
        execute=bool(args.execute),
        confirm=args.confirm,
    )
    if args.summary_only:
        report = _summary_only(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
