#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_maker_shadow_v2 import (  # noqa: E402
    build_maker_shadow_v2_funnel_report,
    write_json,
)


DEFAULT_OUTPUT = Path("evidence/maker_shadow_v2/maker_shadow_v2_24h_funnel_report.json")


def _load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    try:
        parsed = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _expand_inputs(paths: Iterable[str]) -> List[Path]:
    expanded: List[Path] = []
    for value in paths:
        text = str(value or "").strip()
        if not text:
            continue
        matches = sorted(Path().glob(text)) if any(char in text for char in "*?[") else [Path(text)]
        expanded.extend(matches)
    deduped: List[Path] = []
    seen: set[str] = set()
    for path in expanded:
        key = str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def build_funnel_report_from_paths(
    report_paths: Iterable[str | Path],
    *,
    generated_at: str | None = None,
    min_window_hours: float = 24.0,
) -> Dict[str, Any]:
    paths = [Path(path) for path in report_paths]
    reports = [_load_json(path) for path in paths]
    report = build_maker_shadow_v2_funnel_report(
        reports,
        generated_at=generated_at,
        min_window_hours=min_window_hours,
    )
    report["input_report_paths"] = [str(path) for path in paths]
    report["input_report_exists_count"] = sum(1 for path in paths if path.exists())
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate maker_shadow_v2 paper-only opportunity funnel reports.")
    parser.add_argument(
        "--report",
        action="append",
        default=["evidence/maker_shadow_v2/report.json", "evidence/maker_shadow_v2/reports/*.json"],
        help="Report path or glob. Can be repeated.",
    )
    parser.add_argument("--summary-output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--min-window-hours", type=float, default=24.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    paths = _expand_inputs(args.report)
    report = build_funnel_report_from_paths(
        paths,
        generated_at=args.generated_at,
        min_window_hours=float(args.min_window_hours),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "run_count",
                    "total_quote_count",
                    "total_inferred_fill_count",
                    "mean_markout_without_rebate",
                    "opportunity_status",
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
