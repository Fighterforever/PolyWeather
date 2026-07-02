#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_OUTPUT = Path("evidence/polymarket_weather_live_push_decision.json")
DEFAULT_NON_DUST = Path("evidence/non_dust_uuww_due_verdict.json")
DEFAULT_MAKER_FUNNEL = Path("evidence/maker_shadow_v2/maker_shadow_v2_rolling_funnel_report.json")
DEFAULT_STATION_CONFUSION = Path("evidence/station_confusion/station_confusion_edge_report.json")
DEFAULT_BUCKET_LP = Path("evidence/bucket_family/lp_arbitrage_report.json")
DEFAULT_BUCKET_SIMPLE = Path("evidence/bucket_family/basket_arbitrage_report.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    try:
        parsed = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested(report: Dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = report
        found = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                found = False
                break
            current = current[part]
        if found:
            return current
    return None


def _non_dust_resolved_pnl(non_dust: Dict[str, Any]) -> Optional[float]:
    return _safe_float(
        _nested(non_dust, "strict_replay.resolved_pnl_cents", "resolved_pnl_cents", "summary.resolved_pnl_cents")
    )


def _non_dust_resolved_fill_count(non_dust: Dict[str, Any]) -> int:
    return _safe_int(_nested(non_dust, "strict_replay.resolved_fill_count", "resolved_fill_count"))


def _maker_quote_count(maker_funnel: Dict[str, Any]) -> int:
    return _safe_int(maker_funnel.get("quote_count") or maker_funnel.get("total_quote_count"))


def _maker_inferred_fill_count(maker_funnel: Dict[str, Any]) -> int:
    return _safe_int(maker_funnel.get("inferred_fill_count") or maker_funnel.get("total_inferred_fill_count"))


def _bucket_candidate_count(bucket_lp: Dict[str, Any], bucket_simple: Dict[str, Any]) -> int:
    return _safe_int(bucket_lp.get("lp_candidate_count") or bucket_lp.get("candidate_count")) + _safe_int(
        bucket_simple.get("candidate_count")
    )


def build_polymarket_weather_live_push_decision(
    *,
    non_dust_verdict: Dict[str, Any],
    maker_funnel_report: Dict[str, Any],
    station_confusion_report: Dict[str, Any],
    bucket_family_lp_report: Dict[str, Any],
    bucket_family_arbitrage_report: Dict[str, Any] | None = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or _utc_now_iso()
    bucket_family_arbitrage_report = bucket_family_arbitrage_report or {}

    non_dust_alpha = str(non_dust_verdict.get("alpha_conclusion") or non_dust_verdict.get("status") or "").strip()
    non_dust_due_reached = bool(non_dust_verdict.get("due_reached"))
    non_dust_resolved_pnl = _non_dust_resolved_pnl(non_dust_verdict)
    non_dust_positive = non_dust_resolved_pnl is not None and non_dust_resolved_pnl > 0
    non_dust_negative = non_dust_resolved_pnl is not None and non_dust_resolved_pnl < 0
    non_dust_unresolved_after_due = bool(non_dust_due_reached and non_dust_resolved_pnl is None)
    non_dust_waiting_due = bool(not non_dust_due_reached and "waiting" in non_dust_alpha)

    maker_quote_count = _maker_quote_count(maker_funnel_report)
    maker_inferred_fill_count = _maker_inferred_fill_count(maker_funnel_report)
    maker_markout = _safe_float(maker_funnel_report.get("mean_markout_without_rebate"))
    maker_positive = maker_quote_count > 0 and maker_markout is not None and maker_markout > 0

    station_bias_sample_count = _safe_int(station_confusion_report.get("station_bias_sample_count"))
    min_required_sample_count = _safe_int(station_confusion_report.get("min_required_sample_count") or 10)
    station_candidate_count = _safe_int(
        station_confusion_report.get("candidate_count") or station_confusion_report.get("active_candidate_count")
    )
    station_status = str(station_confusion_report.get("station_confusion_status") or "").strip()
    station_insufficient = station_status == "research_only_insufficient_bias_samples" or (
        station_bias_sample_count < min_required_sample_count and station_candidate_count <= 0
    )
    bucket_candidate_count = _bucket_candidate_count(bucket_family_lp_report, bucket_family_arbitrage_report)

    if non_dust_positive:
        live_push_status = "continue_non_dust_forward_paper_only"
        final_reason = "non_dust_uuww_resolved_pnl_positive_but_still_paper_only"
    elif maker_positive:
        live_push_status = "continue_maker_shadow_paper_only"
        final_reason = "maker_shadow_positive_markout_requires_more_paper_evidence"
    elif (
        (non_dust_negative or non_dust_unresolved_after_due)
        and maker_quote_count == 0
        and station_insufficient
        and bucket_candidate_count <= 0
    ):
        live_push_status = "pause_polymarket_weather_live_push"
        final_reason = "non_dust_failed_or_unresolved_after_due_and_no_current_maker_station_or_bucket_edge"
    elif non_dust_waiting_due:
        live_push_status = "wait_non_dust_due_and_collect_polymarket_only_shadow_evidence"
        final_reason = "non_dust_uuww_due_not_reached"
    else:
        live_push_status = "continue_polymarket_only_paper_research"
        final_reason = "paper_only_evidence_incomplete_without_positive_live_push_trigger"

    wait_strategy_ids = []
    if non_dust_waiting_due:
        wait_strategy_ids.append("non_dust_threshold_cdf")
    continue_research_strategy_ids = ["maker_shadow_v2", "station_confusion_edge"]
    low_frequency_monitor_strategy_ids = ["bucket_family_payoff_matrix_arbitrage"]
    killed_strategy_ids = [
        "dust_tail",
        "post_lock_ge_le",
        "threshold_latency",
        "dead_side_capture",
        "eq_dead_no_lock",
    ]

    return {
        "schema_version": "polyweather_polymarket_weather_live_push_decision.v1",
        "generated_at": generated_at,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "non_dust_uuww": {
            "due_reached": non_dust_due_reached,
            "status": non_dust_verdict.get("status"),
            "alpha_conclusion": non_dust_alpha,
            "resolved_fill_count": _non_dust_resolved_fill_count(non_dust_verdict),
            "resolved_pnl_cents": non_dust_resolved_pnl,
            "probability_score_sample_count": _safe_int(
                _nested(non_dust_verdict, "settlement_calibration.probability_score_sample_count")
            ),
            "resolved_pnl_sample_count": _safe_int(
                _nested(non_dust_verdict, "settlement_calibration.resolved_pnl_sample_count")
            ),
        },
        "maker_shadow_v2": {
            "run_count": _safe_int(maker_funnel_report.get("run_count")),
            "actual_window_minutes": _safe_float(maker_funnel_report.get("actual_window_minutes")),
            "quote_count": maker_quote_count,
            "inferred_fill_count": maker_inferred_fill_count,
            "mean_markout_without_rebate": maker_markout,
            "blocker_counts": maker_funnel_report.get("blocker_counts")
            if isinstance(maker_funnel_report.get("blocker_counts"), dict)
            else {},
            "conclusion": maker_funnel_report.get("conclusion") or maker_funnel_report.get("opportunity_status"),
        },
        "station_confusion": {
            "station_bias_sample_count": station_bias_sample_count,
            "min_required_sample_count": min_required_sample_count,
            "station_confusion_status": station_status,
            "candidate_count": station_candidate_count,
            "station_level_gap_reasons": station_confusion_report.get("station_level_gap_reasons")
            if isinstance(station_confusion_report.get("station_level_gap_reasons"), list)
            else [],
        },
        "bucket_family_payoff_matrix_arbitrage": {
            "lp_candidate_count": _safe_int(
                bucket_family_lp_report.get("lp_candidate_count") or bucket_family_lp_report.get("candidate_count")
            ),
            "best_lp_edge_cents": _safe_float(bucket_family_lp_report.get("best_lp_edge_cents")),
        },
        "bucket_family_structural_arbitrage": {
            "candidate_count": _safe_int(bucket_family_arbitrage_report.get("candidate_count")),
            "best_edge_cents": _safe_float(bucket_family_arbitrage_report.get("best_edge_cents")),
        },
        "live_push_status": live_push_status,
        "continue_research_strategy_ids": continue_research_strategy_ids,
        "low_frequency_monitor_strategy_ids": low_frequency_monitor_strategy_ids,
        "killed_strategy_ids": killed_strategy_ids,
        "wait_strategy_ids": wait_strategy_ids,
        "final_reason": final_reason,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Polymarket weather paper-only live-push go/pause decision package.")
    parser.add_argument("--non-dust-verdict", default=str(DEFAULT_NON_DUST))
    parser.add_argument("--maker-funnel-report", default=str(DEFAULT_MAKER_FUNNEL))
    parser.add_argument("--station-confusion-report", default=str(DEFAULT_STATION_CONFUSION))
    parser.add_argument("--bucket-family-lp-report", default=str(DEFAULT_BUCKET_LP))
    parser.add_argument("--bucket-family-arbitrage-report", default=str(DEFAULT_BUCKET_SIMPLE))
    parser.add_argument("--summary-output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    report = build_polymarket_weather_live_push_decision(
        non_dust_verdict=_load_json(args.non_dust_verdict),
        maker_funnel_report=_load_json(args.maker_funnel_report),
        station_confusion_report=_load_json(args.station_confusion_report),
        bucket_family_lp_report=_load_json(args.bucket_family_lp_report),
        bucket_family_arbitrage_report=_load_json(args.bucket_family_arbitrage_report),
        generated_at=args.generated_at,
    )
    output = Path(args.summary_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "live_push_status": report.get("live_push_status"),
                "final_reason": report.get("final_reason"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
