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
        "historical_proxy_positive_needs_forward_eq_dead_no_sampling": "needs_forward_eq_dead_no_sampling",
        "eq_dead_no_historical_proxy_positive_waiting_for_forward_breach": "waiting_for_exact_breach_liquidity",
        "eq_dead_no_proxy_not_robust_reduce_priority": "conservative_proxy_not_positive_after_outlier_removal",
        "eq_dead_no_forward_paper_started": "awaiting_forward_markout_and_resolution",
        "eq_dead_no_forward_markout_positive_pending_resolution": "awaiting_resolution_audit",
        "eq_dead_no_alpha_candidate_paper_only_review": "paper_only_review_required",
        "forward_paper_positive": "paper_only_review_required",
        "alpha_candidate_paper_only_review": "paper_only_review_required",
        "waiting_for_exact_breach_liquidity": "no_current_exact_breach_liquidity",
        "historical_proxy_nonpositive_or_unclear": "historical_trade_proxy_not_positive",
    }.get(status, "insufficient_evidence")


def _next_action(status: str) -> str:
    return {
        "failed_negative_resolved_pnl": "pause_strategy_and_do_not_collect_more_without_new_edge_hypothesis",
        "forward_paper_positive_pending_resolution": "continue_forward_paper_until_resolution_audit",
        "forward_paper_positive": "paper_only_review_no_live_gate",
        "no_observed_executable_opportunity": "pause_or_reduce_sampler_frequency",
        "historical_proxy_positive_needs_forward_execution_sampling": "focus_forward_paper_sampler_on_positive_station_window",
        "historical_proxy_positive_needs_forward_eq_dead_no_sampling": "focus_forward_paper_sampler_on_eq_dead_no_lock",
        "eq_dead_no_historical_proxy_positive_waiting_for_forward_breach": "run_aggressive_eq_dead_no_forward_sampler_until_supported_exact_breach",
        "eq_dead_no_proxy_not_robust_reduce_priority": "reduce_priority_until_new_proxy_sample_remains_positive_without_outliers",
        "eq_dead_no_forward_paper_started": "run_markout_and_resolved_audit_for_eq_dead_no_fills",
        "eq_dead_no_forward_markout_positive_pending_resolution": "continue_forward_paper_until_resolution_audit",
        "eq_dead_no_alpha_candidate_paper_only_review": "paper_only_review_no_live_gate",
        "alpha_candidate_paper_only_review": "paper_only_review_no_live_gate",
        "waiting_for_exact_breach_liquidity": "keep_eq_dead_no_sampler_running_until_direct_no_ask_appears",
        "historical_proxy_nonpositive_or_unclear": "pause_strategy_or_require_new_tradeability_filter",
        "structural_arbitrage_forward_paper_started": "collect_forward_basket_markouts_and_resolution_paper_only",
        "no_current_structural_arbitrage": "keep_low_frequency_bucket_family_sampler_running",
        "historical_structural_edge_needs_forward_sampling": "forward_sample_bucket_family_arbitrage_with_real_orderbooks",
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


def _structural_status(
    *,
    candidate_count: int,
    family_count: int,
    historical_approximate_count: int,
    historical_depth_count: int,
) -> str:
    if candidate_count > 0:
        return "structural_arbitrage_forward_paper_started"
    if historical_approximate_count > 0 and historical_depth_count <= 0:
        return "historical_structural_edge_needs_forward_sampling"
    if family_count > 0:
        return "no_current_structural_arbitrage"
    return "no_current_structural_arbitrage"


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
    eq_dead_no_trade_replay_report: Dict[str, Any] | None = None,
    eq_dead_no_proxy_robustness_report: Dict[str, Any] | None = None,
    eq_dead_no_expanded_robustness_report: Dict[str, Any] | None = None,
    eq_dead_no_sampler_report: Dict[str, Any] | None = None,
    eq_dead_no_markout_report: Dict[str, Any] | None = None,
    eq_dead_no_resolved_audit_report: Dict[str, Any] | None = None,
    threshold_latency_trade_replay_report: Dict[str, Any] | None = None,
    active_sampler_report: Dict[str, Any] | None = None,
    non_dust_due_runner_status: Dict[str, Any] | None = None,
    bucket_family_arbitrage_report: Dict[str, Any] | None = None,
    bucket_family_historical_replay_report: Dict[str, Any] | None = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    strict = _compact(strict_replay_report or {})
    ol_trade = _summary(observation_lock_trade_replay_report or {})
    eq_trade = _summary(eq_dead_no_trade_replay_report or {})
    eq_robust = _compact(eq_dead_no_proxy_robustness_report or {})
    eq_expanded = _compact(eq_dead_no_expanded_robustness_report or {})
    eq_sampler = _compact(eq_dead_no_sampler_report or {})
    eq_audit = _compact(eq_dead_no_resolved_audit_report or {})
    tl_trade = _summary(threshold_latency_trade_replay_report or {})
    active = _summary(active_sampler_report or {})
    due = _compact(non_dust_due_runner_status or {})
    bucket_arbitrage = _compact(bucket_family_arbitrage_report or {})
    bucket_historical = _compact(bucket_family_historical_replay_report or {})

    strict_resolved_pnl = _safe_float(strict.get("resolved_pnl_cents"))
    ol_proxy_pnl = _safe_float(ol_trade.get("trade_proxy_pnl_cents"))
    eq_proxy_pnl = _safe_float(eq_trade.get("deduped_trade_proxy_pnl_cents") or eq_trade.get("trade_proxy_pnl_cents"))
    eq_conservative_count = _safe_int(eq_robust.get("conservative_candidate_count"))
    eq_conservative_pnl = _safe_float(eq_robust.get("conservative_proxy_pnl_cents"))
    eq_conservative_without_top_1 = _safe_float(eq_robust.get("conservative_proxy_pnl_without_top_1"))
    expanded_status = str(eq_expanded.get("status") or "").strip()
    expanded_sample_count = _safe_int(eq_expanded.get("sample_count"))
    expanded_unique_market_count = _safe_int(eq_expanded.get("unique_market_count"))
    expanded_conservative_pnl = _safe_float(eq_expanded.get("conservative_proxy_pnl_cents"))
    expanded_without_top_1 = _safe_float(eq_expanded.get("pnl_without_top_1"))
    expanded_without_top_market = _safe_float(eq_expanded.get("pnl_without_top_market"))
    eq_proxy_robust = bool(eq_robust.get("conservative_proxy_positive_after_outlier_removal"))
    eq_robust_present = bool(eq_robust)
    eq_forward_fill_count = _safe_int(eq_sampler.get("paper_fill_count") or eq_sampler.get("fill_count"))
    eq_resolved_pnl = _safe_float(eq_audit.get("resolved_pnl_cents"))
    tl_proxy_pnl = _safe_float(tl_trade.get("trade_proxy_pnl_cents"))
    active_fill_count = _safe_int(active.get("paper_fill_count") or active.get("fill_count"))
    active_resolved_pnl = _safe_float(active.get("resolved_pnl_cents"))

    due_conclusion = str(due.get("alpha_conclusion") or due.get("status") or "").strip()
    due_resolved_pnl = _safe_float(
        due.get("resolved_pnl_cents")
        or due.get("non_dust_resolved_pnl_cents")
        or (due.get("strict_replay") if isinstance(due.get("strict_replay"), dict) else {}).get("resolved_pnl_cents")
    )
    due_resolved_count = _safe_int(
        due.get("resolved_fill_count")
        or due.get("non_dust_resolved_fill_count")
        or (due.get("strict_replay") if isinstance(due.get("strict_replay"), dict) else {}).get("resolved_fill_count")
    )
    due_fill_count = _safe_int(
        due.get("fill_count")
        or (due.get("strict_replay") if isinstance(due.get("strict_replay"), dict) else {}).get("fill_count")
    )
    if due_resolved_pnl is not None and due_resolved_pnl < 0 or "failed" in due_conclusion:
        non_dust_status = "failed"
    elif due_resolved_pnl is not None and due_resolved_pnl > 0:
        non_dust_status = "single_positive_needs_more"
    else:
        non_dust_status = "waiting_due"

    if expanded_status in {
        "eq_dead_no_proxy_not_robust_keep_monitoring_only",
        "eq_dead_no_proxy_robust_enough_for_forward_sampling",
    }:
        eq_status = expanded_status
    elif eq_robust_present and eq_proxy_robust:
        eq_status = "eq_dead_no_proxy_robust_enough_for_forward_sampling"
    else:
        eq_status = "eq_dead_no_proxy_not_robust_keep_monitoring_only"

    rows = [
        _row(
            strategy_id="bucket_family_buy_all_yes",
            status=_structural_status(
                candidate_count=_safe_int(bucket_arbitrage.get("buy_all_yes_candidate_count")),
                family_count=_safe_int(bucket_arbitrage.get("family_count")),
                historical_approximate_count=_safe_int(bucket_historical.get("approximate_edge_candidate_count")),
                historical_depth_count=_safe_int(bucket_historical.get("executable_depth_available_count")),
            ),
            signal_count=_safe_int(bucket_arbitrage.get("family_count")),
            executable_proxy_count=_safe_int(bucket_arbitrage.get("buy_all_yes_candidate_count")),
            forward_paper_fill_count=_safe_int(bucket_arbitrage.get("basket_paper_fill_count")),
            trade_proxy_pnl_cents=_safe_float(bucket_historical.get("approximate_pnl_cents")),
            non_dust_only=True,
            main_blocker=(
                "structural_basket_candidate_found_paper_only"
                if _safe_int(bucket_arbitrage.get("buy_all_yes_candidate_count")) > 0
                else "no_current_buy_all_yes_basket_edge"
            ),
        ),
        _row(
            strategy_id="bucket_family_buy_all_no",
            status=_structural_status(
                candidate_count=_safe_int(bucket_arbitrage.get("buy_all_no_candidate_count")),
                family_count=_safe_int(bucket_arbitrage.get("family_count")),
                historical_approximate_count=_safe_int(bucket_historical.get("approximate_edge_candidate_count")),
                historical_depth_count=_safe_int(bucket_historical.get("executable_depth_available_count")),
            ),
            signal_count=_safe_int(bucket_arbitrage.get("family_count")),
            executable_proxy_count=_safe_int(bucket_arbitrage.get("buy_all_no_candidate_count")),
            forward_paper_fill_count=_safe_int(bucket_arbitrage.get("basket_paper_fill_count")),
            trade_proxy_pnl_cents=_safe_float(bucket_historical.get("approximate_pnl_cents")),
            non_dust_only=True,
            main_blocker=(
                "structural_basket_candidate_found_paper_only"
                if _safe_int(bucket_arbitrage.get("buy_all_no_candidate_count")) > 0
                else "no_current_buy_all_no_basket_edge"
            ),
        ),
        _row(
            strategy_id="monotonic_threshold_pair",
            status=_structural_status(
                candidate_count=_safe_int(bucket_arbitrage.get("monotonic_pair_candidate_count")),
                family_count=_safe_int(bucket_arbitrage.get("family_count")),
                historical_approximate_count=0,
                historical_depth_count=0,
            ),
            signal_count=_safe_int(bucket_arbitrage.get("monotonic_pair_count")),
            executable_proxy_count=_safe_int(bucket_arbitrage.get("monotonic_pair_candidate_count")),
            forward_paper_fill_count=_safe_int(bucket_arbitrage.get("basket_paper_fill_count")),
            non_dust_only=True,
            main_blocker=(
                "structural_pair_candidate_found_paper_only"
                if _safe_int(bucket_arbitrage.get("monotonic_pair_candidate_count")) > 0
                else "no_current_monotonic_pair_edge"
            ),
        ),
        _row(
            strategy_id="non_dust_threshold_cdf",
            status=non_dust_status,
            signal_count=_safe_int(due.get("candidate_count") or due.get("signal_count")),
            executable_proxy_count=due_fill_count or 0,
            forward_paper_fill_count=due_fill_count or 0,
            resolved_fill_count=due_resolved_count or 0,
            resolved_pnl_cents=due_resolved_pnl,
            non_dust_only=True,
            main_blocker="awaiting_non_dust_due_resolution" if non_dust_status == "waiting_due" else None,
            next_action="wait_for_scheduled_non_dust_due_runner" if non_dust_status == "waiting_due" else "paper_only_review_no_live_gate",
        ),
    ]

    eq_row = _row(
            strategy_id="eq_dead_no_lock",
            status=eq_status,
            signal_count=_safe_int(eq_trade.get("breached_eq_signal_count") or eq_sampler.get("breached_eq_count")),
            executable_proxy_count=expanded_sample_count
            or eq_conservative_count
            or _safe_int(eq_trade.get("deduped_trade_proxy_candidate_count") or eq_sampler.get("executable_candidate_count")),
            forward_paper_fill_count=eq_forward_fill_count,
            resolved_fill_count=_safe_int(eq_audit.get("resolved_fill_count")),
            resolved_pnl_cents=eq_resolved_pnl,
            trade_proxy_pnl_cents=expanded_conservative_pnl if expanded_conservative_pnl is not None else (eq_conservative_pnl if eq_conservative_pnl is not None else eq_proxy_pnl),
            non_dust_only=True,
            main_blocker="proxy_not_positive_after_outlier_removal" if eq_status == "eq_dead_no_proxy_not_robust_keep_monitoring_only" else "needs_forward_paper_execution_sampling",
            next_action="keep_monitoring_only_no_frequency_increase" if eq_status == "eq_dead_no_proxy_not_robust_keep_monitoring_only" else "forward_sample_supported_exact_breaches_paper_only",
        )
    eq_row.update(
        {
            "eq_dead_no_proxy_robustness": eq_status == "eq_dead_no_proxy_robust_enough_for_forward_sampling",
            "conservative_proxy_candidate_count": expanded_sample_count or eq_conservative_count,
            "conservative_proxy_pnl_cents": expanded_conservative_pnl if expanded_conservative_pnl is not None else eq_conservative_pnl,
            "conservative_proxy_pnl_without_top_1": expanded_without_top_1 if expanded_without_top_1 is not None else eq_conservative_without_top_1,
            "pnl_without_top_market": expanded_without_top_market,
            "expanded_unique_market_count": expanded_unique_market_count,
            "active_eq_station_count": _safe_int(
                eq_sampler.get("active_supported_official_station_count")
                or eq_sampler.get("active_supported_metar_station_count")
            ),
            "active_supported_station_count_before": _safe_int(eq_sampler.get("active_supported_metar_station_count")),
            "active_supported_station_count_after": _safe_int(
                eq_sampler.get("active_supported_official_station_count")
                or eq_sampler.get("active_supported_metar_station_count")
            ),
            "active_breached_eq_count": _safe_int(eq_sampler.get("breached_eq_count")),
            "eq_dead_no_forward_paper_fill_count": eq_forward_fill_count,
            "eq_dead_no_current_status": eq_status,
        }
    )
    rows.append(eq_row)

    rows.extend(
        [
            _row(
                strategy_id="observation_lock_trade_proxy",
                status="collapsed_into_eq_dead_no_or_paused",
                signal_count=_safe_int(ol_trade.get("locked_signal_count")),
                executable_proxy_count=_safe_int(ol_trade.get("trade_proxy_candidate_count")),
                trade_proxy_pnl_cents=ol_proxy_pnl,
                non_dust_only=True,
                main_blocker="collapsed_into_eq_dead_no_filter",
                next_action="do_not_expand_separately",
            ),
            _row(
                strategy_id="threshold_latency",
                status="paused_no_candidate",
                signal_count=_safe_int(tl_trade.get("update_event_count") or active.get("candidate_count") or active.get("watch_count")),
                executable_proxy_count=_safe_int(tl_trade.get("trade_proxy_candidate_count")),
                forward_paper_fill_count=active_fill_count,
                resolved_pnl_cents=active_resolved_pnl,
                trade_proxy_pnl_cents=tl_proxy_pnl,
                non_dust_only=True,
                main_blocker="no_current_candidate",
                next_action="reduced_frequency_sampling_only",
            ),
            _row(
                strategy_id="post_lock_ge_le",
                status="killed_market_already_reflected",
                non_dust_only=True,
                main_blocker="market_already_reflected_observation",
                next_action="do_not_continue",
            ),
            _row(
                strategy_id="dust_tail_near_lock",
                status="killed_negative_dust_only",
                signal_count=_safe_int(strict.get("replay_candidate_count")),
                executable_proxy_count=_safe_int(strict.get("fill_count")),
                resolved_fill_count=_safe_int(strict.get("resolved_fill_count")),
                resolved_pnl_cents=strict_resolved_pnl,
                non_dust_only=False,
                main_blocker="negative_or_dust_only_evidence",
                next_action="do_not_use_as_tiny_live_evidence",
            ),
            _row(
                strategy_id="maker_inferred",
                status="killed_diagnostic_only",
                non_dust_only=False,
                main_blocker="inferred_fill_not_real_execution_evidence",
                next_action="do_not_continue_without_quote_lifecycle",
            ),
        ]
    )

    continue_strategies = [
        row["strategy_id"]
        for row in rows
        if row["status"] == "eq_dead_no_proxy_robust_enough_for_forward_sampling"
    ]
    wait_strategies = [
        row["strategy_id"]
        for row in rows
        if row["status"] in {"waiting_due", "single_positive_needs_more", "no_current_structural_arbitrage"}
    ]
    pause_strategies = [
        row["strategy_id"]
        for row in rows
        if row["status"] in {
            "eq_dead_no_proxy_not_robust_keep_monitoring_only",
            "collapsed_into_eq_dead_no_or_paused",
            "paused_no_candidate",
        }
    ]
    kill_strategies = [
        row["strategy_id"]
        for row in rows
        if row["status"] in {
            "failed",
            "killed_market_already_reflected",
            "killed_negative_dust_only",
            "killed_diagnostic_only",
        }
    ]
    structural_continue = [
        row["strategy_id"]
        for row in rows
        if row["status"] == "structural_arbitrage_forward_paper_started"
    ]
    continue_strategies = sorted(set(continue_strategies + structural_continue))
    ol_guidance = _positive_station_guidance(observation_lock_trade_replay_report or {})
    tl_guidance = _positive_station_guidance(threshold_latency_trade_replay_report or {})
    eq_guidance = _positive_station_guidance(eq_dead_no_trade_replay_report or {})
    prioritized_station_codes = sorted(
        set(ol_guidance["prioritized_station_codes"] + tl_guidance["prioritized_station_codes"] + eq_guidance["prioritized_station_codes"])
    )
    prioritized_windows = sorted(
        set(
            ol_guidance["prioritized_time_to_close_windows"]
            + tl_guidance["prioritized_time_to_close_windows"]
            + eq_guidance["prioritized_time_to_close_windows"]
        )
    )
    if continue_strategies:
        sampler_mode = "aggressive"
    elif any(_safe_int(row.get("executable_proxy_count")) > 0 for row in rows):
        sampler_mode = "reduced"
    else:
        sampler_mode = "paused"
    if non_dust_status == "failed" and eq_status == "eq_dead_no_proxy_not_robust_keep_monitoring_only":
        live_should_pause = True
        reason = "all_current_weather_alpha_paths_failed_or_unproven"
    elif eq_status == "eq_dead_no_proxy_not_robust_keep_monitoring_only" and non_dust_status != "single_positive_needs_more":
        live_should_pause = True
        reason = "waiting_for_non_dust_due_and_eq_dead_no_not_robust"
    elif not continue_strategies:
        live_should_pause = True
        reason = "no_current_strategy_has_robust_forward_or_resolved_edge"
    else:
        live_should_pause = False
        reason = "paper_only_forward_sampling_can_continue"
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
            "wait_strategy_ids": wait_strategies,
            "pause_strategy_ids": pause_strategies,
            "kill_strategy_ids": kill_strategies,
            "live_should_pause": live_should_pause,
            "reason": reason,
            "alpha_viability_final_verdict": reason,
            "live_order_path": False,
            "counts_for_live_gate": False,
            "eq_dead_no_proxy_robustness": {
                "conservative_proxy_candidate_count": expanded_sample_count or eq_conservative_count,
                "conservative_proxy_pnl_cents": expanded_conservative_pnl if expanded_conservative_pnl is not None else eq_conservative_pnl,
                "conservative_proxy_pnl_without_top_1": expanded_without_top_1 if expanded_without_top_1 is not None else eq_conservative_without_top_1,
                "pnl_without_top_market": expanded_without_top_market,
                "conservative_proxy_positive_after_outlier_removal": eq_status == "eq_dead_no_proxy_robust_enough_for_forward_sampling",
                "direction_confidence_filtered_pnl_cents": _safe_float(eq_robust.get("high_confidence_pnl_cents")),
            },
            "active_supported_station_count_before": _safe_int(eq_sampler.get("active_supported_metar_station_count")),
            "active_supported_station_count_after": _safe_int(
                eq_sampler.get("active_supported_official_station_count")
                or eq_sampler.get("active_supported_metar_station_count")
            ),
            "active_eq_station_count": _safe_int(
                eq_sampler.get("active_supported_official_station_count")
                or eq_sampler.get("active_supported_metar_station_count")
            ),
            "active_breached_eq_count": _safe_int(eq_sampler.get("breached_eq_count")),
            "eq_dead_no_forward_paper_fill_count": eq_forward_fill_count,
            "eq_dead_no_current_status": eq_status,
            "non_dust_threshold_cdf_status": non_dust_status,
            "bucket_family_structural_arbitrage": {
                "family_count": _safe_int(bucket_arbitrage.get("family_count")),
                "partition_family_count": _safe_int(bucket_arbitrage.get("partition_family_count")),
                "candidate_count": _safe_int(bucket_arbitrage.get("candidate_count")),
                "buy_all_yes_candidate_count": _safe_int(bucket_arbitrage.get("buy_all_yes_candidate_count")),
                "buy_all_no_candidate_count": _safe_int(bucket_arbitrage.get("buy_all_no_candidate_count")),
                "monotonic_pair_candidate_count": _safe_int(bucket_arbitrage.get("monotonic_pair_candidate_count")),
                "best_edge_cents": _safe_float(bucket_arbitrage.get("best_edge_cents")),
                "basket_paper_fill_count": _safe_int(bucket_arbitrage.get("basket_paper_fill_count")),
                "historical_approximate_edge_candidate_count": _safe_int(
                    bucket_historical.get("approximate_edge_candidate_count")
                ),
                "historical_executable_depth_available_count": _safe_int(
                    bucket_historical.get("executable_depth_available_count")
                ),
            },
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
    active = updated.setdefault("active_alpha_research", {})
    if isinstance(active, dict):
        rows = {row.get("strategy_id"): row for row in scoreboard.get("rows") or [] if isinstance(row, dict)}
        eq_row = rows.get("eq_dead_no_lock") or {}
        active["eq_dead_no_lock"] = {
            "status": eq_row.get("status") or "waiting_for_exact_breach_liquidity",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "trade_proxy_pnl_cents": eq_row.get("trade_proxy_pnl_cents"),
            "eq_dead_no_proxy_robustness": eq_row.get("eq_dead_no_proxy_robustness"),
            "conservative_proxy_candidate_count": eq_row.get("conservative_proxy_candidate_count"),
            "conservative_proxy_pnl_cents": eq_row.get("conservative_proxy_pnl_cents"),
            "conservative_proxy_pnl_without_top_1": eq_row.get("conservative_proxy_pnl_without_top_1"),
            "active_eq_station_count": eq_row.get("active_eq_station_count"),
            "active_breached_eq_count": eq_row.get("active_breached_eq_count"),
            "eq_dead_no_forward_paper_fill_count": eq_row.get("eq_dead_no_forward_paper_fill_count"),
            "eq_dead_no_current_status": eq_row.get("eq_dead_no_current_status"),
            "forward_paper_fill_count": eq_row.get("forward_paper_fill_count"),
            "next_action": eq_row.get("next_action"),
            "report_path": "evidence/eq_dead_no/eq_dead_no_execution_sampler_report.json",
            "historical_replay_path": "evidence/eq_dead_no/eq_dead_no_trade_replay_report.json",
        }
        threshold = active.get("threshold_latency")
        threshold_row = rows.get("threshold_latency") or {}
        if isinstance(threshold, dict) and int(threshold_row.get("forward_paper_fill_count") or 0) <= 0:
            threshold["status"] = "reduced_frequency_no_current_candidate"
            threshold["next_action"] = "keep_observation_logging_reduce_execution_sampling_until_new_evidence"
            threshold["live_order_path"] = False
        bucket_rows = {
            key: rows.get(key) or {}
            for key in (
                "bucket_family_buy_all_yes",
                "bucket_family_buy_all_no",
                "monotonic_threshold_pair",
            )
        }
        active["bucket_family_structural_arbitrage"] = {
            "status": "structural_arbitrage_forward_paper_started"
            if any(row.get("status") == "structural_arbitrage_forward_paper_started" for row in bucket_rows.values())
            else "no_current_structural_arbitrage",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "strategies": bucket_rows,
            "report_path": "evidence/bucket_family/basket_arbitrage_report.json",
            "historical_replay_path": "evidence/bucket_family/basket_historical_replay_report.json",
        }
    return updated


__all__ = [
    "SCHEMA_VERSION",
    "build_alpha_viability_scoreboard",
    "load_json",
    "update_profit_strategy_state_report",
    "write_json",
]
