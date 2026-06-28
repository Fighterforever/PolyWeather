from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = "polyweather_alpha_viability_scoreboard.v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _summary(report: Dict[str, Any]) -> Dict[str, Any]:
    value = report.get("summary") if isinstance(report, dict) else {}
    return value if isinstance(value, dict) else {}


def _compact(report: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    if "summary" in report and isinstance(report["summary"], dict):
        return report["summary"]
    return report


def _status(
    *,
    trade_proxy_candidate_count: int = 0,
    forward_paper_fill_count: int = 0,
    resolved_pnl_cents: Optional[float] = None,
    trade_proxy_pnl_cents: Optional[float] = None,
) -> str:
    if resolved_pnl_cents is not None and resolved_pnl_cents < 0:
        return "failed_negative_resolved_pnl"
    if forward_paper_fill_count > 0 and (resolved_pnl_cents is None or resolved_pnl_cents >= 0):
        return "forward_paper_positive_pending_resolution" if resolved_pnl_cents is None else "forward_paper_positive"
    if trade_proxy_candidate_count == 0 and forward_paper_fill_count == 0:
        return "no_observed_executable_opportunity"
    if trade_proxy_pnl_cents is not None and trade_proxy_pnl_cents > 0 and forward_paper_fill_count == 0:
        return "historical_proxy_positive_needs_forward_execution_sampling"
    if trade_proxy_candidate_count > 0:
        return "historical_proxy_nonpositive_or_unclear"
    return "needs_more_evidence"


def _main_blocker(status: str) -> str:
    return {
        "failed_negative_resolved_pnl": "resolved_pnl_negative",
        "forward_paper_positive_pending_resolution": "awaiting_resolution_audit",
        "forward_paper_positive": "paper_only_review_required",
        "no_observed_executable_opportunity": "no_trade_proxy_or_forward_fill",
        "historical_proxy_positive_needs_forward_execution_sampling": "needs_forward_paper_execution_sampling",
        "historical_proxy_nonpositive_or_unclear": "historical_trade_proxy_not_positive",
    }.get(status, "insufficient_evidence")


def _next_action(status: str) -> str:
    return {
        "failed_negative_resolved_pnl": "pause_strategy_and_do_not_collect_more_without_new_edge_hypothesis",
        "forward_paper_positive_pending_resolution": "continue_forward_paper_until_resolution_audit",
        "forward_paper_positive": "paper_only_review_no_live_gate",
        "no_observed_executable_opportunity": "pause_or_reduce_sampler_frequency",
        "historical_proxy_positive_needs_forward_execution_sampling": "focus_forward_paper_sampler_on_positive_station_window",
        "historical_proxy_nonpositive_or_unclear": "pause_strategy_or_require_new_tradeability_filter",
    }.get(status, "collect_targeted_evidence")


def _row(
    *,
    strategy_id: str,
    status: str,
    signal_count: int = 0,
    executable_proxy_count: int = 0,
    forward_paper_fill_count: int = 0,
    resolved_fill_count: int = 0,
    resolved_pnl_cents: Optional[float] = None,
    trade_proxy_pnl_cents: Optional[float] = None,
    non_dust_only: bool = True,
    official_truth_coverage: Optional[float] = None,
    main_blocker: Optional[str] = None,
    next_action: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "status": status,
        "signal_count": int(signal_count),
        "executable_proxy_count": int(executable_proxy_count),
        "forward_paper_fill_count": int(forward_paper_fill_count),
        "resolved_fill_count": int(resolved_fill_count),
        "resolved_pnl_cents": resolved_pnl_cents,
        "trade_proxy_pnl_cents": trade_proxy_pnl_cents,
        "non_dust_only": bool(non_dust_only),
        "official_truth_coverage": official_truth_coverage,
        "main_blocker": main_blocker or _main_blocker(status),
        "next_action": next_action or _next_action(status),
        "live_eligible": False,
    }


def _truth_coverage(summary: Dict[str, Any]) -> Optional[float]:
    total = _safe_int(summary.get("closed_market_slug_count") or summary.get("closed_record_count") or summary.get("official_truth_expected_count"))
    truth = _safe_int(summary.get("official_truth_sample_count"))
    if total <= 0:
        return None
    return round(truth / total, 6)


def _positive_station_guidance(report: Dict[str, Any]) -> Dict[str, Any]:
    summary = _summary(report)
    station_rows = summary.get("by_station") if isinstance(summary.get("by_station"), list) else []
    window_rows = summary.get("by_window") if isinstance(summary.get("by_window"), list) else []
    station_codes = [
        str(row.get("station_code"))
        for row in station_rows
        if isinstance(row, dict) and _safe_float(row.get("trade_proxy_pnl_cents")) is not None and float(row["trade_proxy_pnl_cents"]) > 0
    ]
    windows = [
        str(row.get("window"))
        for row in window_rows
        if isinstance(row, dict) and _safe_float(row.get("trade_proxy_pnl_cents")) is not None and float(row["trade_proxy_pnl_cents"]) > 0
    ]
    return {
        "prioritized_station_codes": sorted(set(station_codes)),
        "prioritized_time_to_close_windows": sorted(set(windows)),
    }


def build_alpha_viability_scoreboard(
    *,
    strict_replay_report: Dict[str, Any] | None = None,
    archived_overlap_replay_report: Dict[str, Any] | None = None,
    observation_lock_tradability_report: Dict[str, Any] | None = None,
    observation_lock_trade_replay_report: Dict[str, Any] | None = None,
    threshold_latency_trade_replay_report: Dict[str, Any] | None = None,
    active_sampler_report: Dict[str, Any] | None = None,
    non_dust_due_runner_status: Dict[str, Any] | None = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    strict = _compact(strict_replay_report or {})
    archived = _compact(archived_overlap_replay_report or {})
    tradability = _summary(observation_lock_tradability_report or {})
    ol_trade = _summary(observation_lock_trade_replay_report or {})
    tl_trade = _summary(threshold_latency_trade_replay_report or {})
    active = _summary(active_sampler_report or {})
    due = _compact(non_dust_due_runner_status or {})

    strict_resolved_pnl = _safe_float(strict.get("resolved_pnl_cents"))
    archived_resolved_pnl = _safe_float(archived.get("resolved_pnl_cents"))
    ol_proxy_pnl = _safe_float(ol_trade.get("trade_proxy_pnl_cents"))
    tl_proxy_pnl = _safe_float(tl_trade.get("trade_proxy_pnl_cents"))
    active_fill_count = _safe_int(active.get("paper_fill_count") or active.get("fill_count"))
    active_resolved_pnl = _safe_float(active.get("resolved_pnl_cents"))

    rows = [
        _row(
            strategy_id="dust_tail_near_lock",
            status="failed_negative_resolved_pnl" if strict_resolved_pnl is not None and strict_resolved_pnl < 0 else "dust_tail_diagnostic_only",
            signal_count=_safe_int(strict.get("replay_candidate_count")),
            executable_proxy_count=_safe_int(strict.get("fill_count")),
            forward_paper_fill_count=_safe_int(strict.get("fill_count")),
            resolved_fill_count=_safe_int(strict.get("resolved_fill_count")),
            resolved_pnl_cents=strict_resolved_pnl,
            non_dust_only=False,
            main_blocker="dust_price_bucket_not_live_eligible",
            next_action="do_not_use_as_tiny_live_evidence",
        ),
        _row(
            strategy_id="archived_overlap_diagnostic",
            status="failed_negative_resolved_pnl" if archived_resolved_pnl is not None and archived_resolved_pnl < 0 else "diagnostic_only",
            signal_count=_safe_int(archived.get("fill_count")),
            executable_proxy_count=_safe_int(archived.get("fill_count")),
            resolved_fill_count=_safe_int(archived.get("resolved_fill_count") or archived.get("fill_count")),
            resolved_pnl_cents=archived_resolved_pnl,
            non_dust_only=False,
            official_truth_coverage=_truth_coverage(archived),
            main_blocker="diagnostic_only_not_strategy_signal",
            next_action="use_only_for_sanity_check",
        ),
        _row(
            strategy_id="post_lock_observation_lock_direct_taker",
            status="no_observed_executable_opportunity"
            if _safe_int(tradability.get("executable_depth_available_count")) == 0
            else "historical_executable_depth_available_needs_replay",
            signal_count=_safe_int(tradability.get("locked_signal_count")),
            executable_proxy_count=_safe_int(tradability.get("executable_depth_available_count")),
            non_dust_only=True,
            main_blocker="historical_orderbook_depth_missing",
            next_action="use_trade_proxy_and_forward_sampler_to_test_real tradability".replace(" ", "_"),
        ),
    ]

    ol_status = _status(
        trade_proxy_candidate_count=_safe_int(ol_trade.get("trade_proxy_candidate_count")),
        trade_proxy_pnl_cents=ol_proxy_pnl,
    )
    rows.append(
        _row(
            strategy_id="observation_lock_trade_proxy",
            status=ol_status,
            signal_count=_safe_int(ol_trade.get("locked_signal_count")),
            executable_proxy_count=_safe_int(ol_trade.get("trade_proxy_candidate_count")),
            trade_proxy_pnl_cents=ol_proxy_pnl,
            non_dust_only=True,
        )
    )
    tl_status = _status(
        trade_proxy_candidate_count=_safe_int(tl_trade.get("trade_proxy_candidate_count")),
        trade_proxy_pnl_cents=tl_proxy_pnl,
    )
    rows.append(
        _row(
            strategy_id="threshold_latency_trade_proxy",
            status=tl_status,
            signal_count=_safe_int(tl_trade.get("update_event_count")),
            executable_proxy_count=_safe_int(tl_trade.get("trade_proxy_candidate_count")),
            trade_proxy_pnl_cents=tl_proxy_pnl,
            non_dust_only=True,
        )
    )
    tl_forward_status = _status(
        forward_paper_fill_count=active_fill_count,
        resolved_pnl_cents=active_resolved_pnl,
    )
    rows.append(
        _row(
            strategy_id="threshold_latency_forward_paper",
            status=tl_forward_status,
            signal_count=_safe_int(active.get("candidate_count") or active.get("watch_count")),
            forward_paper_fill_count=active_fill_count,
            resolved_pnl_cents=active_resolved_pnl,
            non_dust_only=True,
        )
    )
    rows.append(
        _row(
            strategy_id="non_dust_threshold_cdf",
            status="needs_non_dust_forward_fills"
            if _safe_int(due.get("non_dust_resolved_fill_count") or due.get("resolved_fill_count")) == 0
            else "paper_only_review",
            signal_count=_safe_int(due.get("candidate_count") or due.get("signal_count")),
            executable_proxy_count=_safe_int(due.get("fill_count")),
            resolved_fill_count=_safe_int(due.get("non_dust_resolved_fill_count") or due.get("resolved_fill_count")),
            resolved_pnl_cents=_safe_float(due.get("non_dust_resolved_pnl_cents") or due.get("resolved_pnl_cents")),
            non_dust_only=True,
            main_blocker="non_dust_resolved_evidence_missing",
            next_action="continue_non_dust_threshold_sampling_only_if_trade_proxy_positive",
        )
    )

    continue_strategies = [
        row["strategy_id"]
        for row in rows
        if row["status"] in {"historical_proxy_positive_needs_forward_execution_sampling", "forward_paper_positive_pending_resolution"}
    ]
    pause_strategies = [
        row["strategy_id"]
        for row in rows
        if row["status"] in {"failed_negative_resolved_pnl", "no_observed_executable_opportunity", "historical_proxy_nonpositive_or_unclear"}
        or row["strategy_id"] in {"dust_tail_near_lock", "archived_overlap_diagnostic"}
    ]
    ol_guidance = _positive_station_guidance(observation_lock_trade_replay_report or {})
    tl_guidance = _positive_station_guidance(threshold_latency_trade_replay_report or {})
    prioritized_station_codes = sorted(set(ol_guidance["prioritized_station_codes"] + tl_guidance["prioritized_station_codes"]))
    prioritized_windows = sorted(set(ol_guidance["prioritized_time_to_close_windows"] + tl_guidance["prioritized_time_to_close_windows"]))
    if continue_strategies:
        sampler_mode = "aggressive"
    elif any(_safe_int(row.get("executable_proxy_count")) > 0 for row in rows):
        sampler_mode = "reduced"
    else:
        sampler_mode = "paused"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "rows": rows,
        "summary": {
            "strategy_count": len(rows),
            "continue_strategy_ids": continue_strategies,
            "pause_strategy_ids": pause_strategies,
            "wait_strategy_ids": [
                row["strategy_id"]
                for row in rows
                if row["strategy_id"] not in continue_strategies and row["strategy_id"] not in pause_strategies
            ],
            "live_should_pause": not bool(continue_strategies),
            "live_order_path": False,
            "counts_for_live_gate": False,
        },
        "historical_trade_proxy_guidance": {
            "sampler_mode": sampler_mode,
            "prioritized_station_codes": prioritized_station_codes,
            "prioritized_time_to_close_windows": prioritized_windows,
            "guidance_reason": "positive_trade_proxy_found" if continue_strategies else "no_historical_trade_proxy_edge",
        },
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def update_profit_strategy_state_report(
    *,
    existing_report: Dict[str, Any],
    scoreboard: Dict[str, Any],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    updated = dict(existing_report) if isinstance(existing_report, dict) else {}
    updated["schema_version"] = updated.get("schema_version") or "polyweather_profit_strategy_state.v1"
    updated["generated_at"] = generated_at or utc_now_iso()
    updated["paper_only"] = True
    updated["counts_for_live_gate"] = False
    updated["live_order_path"] = False
    updated["historical_trade_proxy_guidance"] = scoreboard.get("historical_trade_proxy_guidance")
    updated["alpha_viability_scoreboard_summary"] = scoreboard.get("summary")
    return updated


__all__ = [
    "SCHEMA_VERSION",
    "build_alpha_viability_scoreboard",
    "load_json",
    "update_profit_strategy_state_report",
    "write_json",
]
