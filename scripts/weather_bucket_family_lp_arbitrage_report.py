#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_basket_paper_journal import write_basket_paper_fills  # noqa: E402
from src.trading.weather_bucket_family_lp_arbitrage import (  # noqa: E402
    build_bucket_family_lp_arbitrage_report,
    load_bucket_family_catalog,
)
from src.trading.weather_paper_journal import utc_now_iso  # noqa: E402


DEFAULT_ROOT = Path("evidence/bucket_family")
DEFAULT_CATALOG = DEFAULT_ROOT / "weather_bucket_family_catalog.json"
DEFAULT_SUMMARY_OUTPUT = DEFAULT_ROOT / "lp_arbitrage_report.json"
DEFAULT_CANDIDATES_OUTPUT = DEFAULT_ROOT / "lp_arbitrage_candidates.jsonl"
DEFAULT_NEAR_MISSES_OUTPUT = DEFAULT_ROOT / "lp_arbitrage_near_misses.jsonl"
DEFAULT_PAPER_FILL_DIR = DEFAULT_ROOT / "lp_basket_paper"


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


def build_bucket_family_lp_arbitrage_cli_report(
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
    paper_fill_dir: str | Path = DEFAULT_PAPER_FILL_DIR,
    min_edge_cents: float = 1.0,
    min_leg_depth: float = 1.0,
    cost_cents: float = 0.0,
    max_candidates: int = 20,
    max_active_set_enumerations: int = 250_000,
    write_paper_fills: bool = True,
) -> Dict[str, Any]:
    generated_at = utc_now_iso()
    catalog = load_bucket_family_catalog(catalog_path)
    report = build_bucket_family_lp_arbitrage_report(
        catalog,
        min_edge_cents=min_edge_cents,
        min_leg_depth=min_leg_depth,
        cost_cents=cost_cents,
        max_candidates=max_candidates,
        max_active_set_enumerations=max_active_set_enumerations,
    )
    paper_report = (
        write_basket_paper_fills(report.get("candidates") or [], paper_fill_dir=paper_fill_dir, created_at=generated_at)
        if write_paper_fills and int(report.get("lp_candidate_count") or 0) > 0
        else {
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "basket_paper_fill_count": 0,
            "written_count": 0,
            "fills": [],
            "fills_path": str(Path(paper_fill_dir) / "fills.jsonl"),
            "manifest_path": str(Path(paper_fill_dir) / "manifest.jsonl"),
        }
    )
    report.update(
        {
            "generated_at": generated_at,
            "catalog_path": str(catalog_path),
            "thresholds": {
                "min_edge_cents": float(min_edge_cents),
                "min_leg_depth": float(min_leg_depth),
                "cost_cents": float(cost_cents),
                "max_candidates": int(max_candidates),
            },
            "lp_basket_paper_fill_count": int(paper_report.get("basket_paper_fill_count") or 0),
            "paper_journal": {
                "fills_path": paper_report.get("fills_path"),
                "manifest_path": paper_report.get("manifest_path"),
                "written_count": paper_report.get("written_count"),
            },
        }
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only generalized weather bucket-family LP arbitrage solver.")
    parser.add_argument("--paper-only", action="store_true")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--paper-fill-dir", default=str(DEFAULT_PAPER_FILL_DIR))
    parser.add_argument("--min-edge-cents", type=float, default=1.0)
    parser.add_argument("--min-leg-depth", type=float, default=1.0)
    parser.add_argument("--cost-cents", type=float, default=0.0)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--max-active-set-enumerations", type=int, default=250_000)
    parser.add_argument("--no-paper-fills", action="store_true")
    parser.add_argument("--summary-output", default=str(DEFAULT_SUMMARY_OUTPUT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES_OUTPUT))
    parser.add_argument("--near-misses-output", default=str(DEFAULT_NEAR_MISSES_OUTPUT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_bucket_family_lp_arbitrage_cli_report(
        catalog_path=args.catalog,
        paper_fill_dir=args.paper_fill_dir,
        min_edge_cents=args.min_edge_cents,
        min_leg_depth=args.min_leg_depth,
        cost_cents=args.cost_cents,
        max_candidates=args.max_candidates,
        max_active_set_enumerations=args.max_active_set_enumerations,
        write_paper_fills=not args.no_paper_fills,
    )
    report["artifact_paths"] = {
        "summary_output": str(args.summary_output),
        "candidates_output": str(args.candidates_output),
        "near_misses_output": str(args.near_misses_output),
    }
    write_json(args.summary_output, report)
    write_jsonl(args.candidates_output, report.get("candidates") or [])
    write_jsonl(args.near_misses_output, report.get("near_misses") or [])
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "lp_family_count",
                    "lp_candidate_count",
                    "best_lp_edge_cents",
                    "lp_near_miss_count",
                    "lp_basket_paper_fill_count",
                    "live_order_path",
                )
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
