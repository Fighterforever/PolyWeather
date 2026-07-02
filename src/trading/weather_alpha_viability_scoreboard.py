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
        "structural_lp_arbitrage_forward_paper_started": "structural_lp_candidate_found_paper_only",
        "active_shadow_testing": "collect_maker_shadow_markouts_without_live_orders",
        "active_research": "collect_station_confusion_forward_evidence",
        "low_frequency_monitor_no_current_edge": "no_current_structural_edge",
        "monitoring_only_not_robust": "conservative_proxy_not_positive_after_outlier_removal",
        "paused": "strategy_paused_low_value",
        "killed": "strategy_killed_by_negative_or_reflected_evidence",
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
        "structural_lp_arbitrage_forward_paper_started": "write_lp_basket_paper_fills_and_track_resolution",
        "active_shadow_testing": "collect_30_plus_inferred_fills_and_markouts_paper_only",
        "active_research": "collect_station_confusion_forward_fills_paper_only",
        "low_frequency_monitor_no_current_edge": "monitor_at_low_frequency_only",
        "monitoring_only_not_robust": "keep_monitoring_only_no_frequency_increase",
        "paused": "pause_sampler_or_run_at_reduced_frequency",
        "killed": "do_not_continue_without_new_edge_hypothesis",
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
    platform: str = "polymarket",
) -> Dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "platform": platform,
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
        "live_order_path": False,
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
        return "low_frequency_monitor_no_current_edge"
    return "low_frequency_monitor_no_current_edge"


def _structural_lp_status(*, candidate_count: int, family_count: int) -> str:
    if candidate_count > 0:
        return "structural_lp_arbitrage_forward_paper_started"
    if family_count > 0:
        return "low_frequency_monitor_no_current_edge"
    return "low_frequency_monitor_no_current_edge"


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
    bucket_family_lp_arbitrage_report: Dict[str, Any] | None = None,
    bucket_family_historical_replay_report: Dict[str, Any] | None = None,
    maker_shadow_v2_report: Dict[str, Any] | None = None,
    maker_shadow_v2_funnel_report: Dict[str, Any] | None = None,
    station_confusion_edge_report: Dict[str, Any] | None = None,
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
    bucket_lp = _compact(bucket_family_lp_arbitrage_report or {})
    bucket_historical = _compact(bucket_family_historical_replay_report or {})
    maker_shadow = _compact(maker_shadow_v2_report or {})
    maker_funnel = _compact(maker_shadow_v2_funnel_report or {})
    station_confusion = _compact(station_confusion_edge_report or {})

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
        eq_status = "monitoring_only_not_robust" if expanded_status == "eq_dead_no_proxy_not_robust_keep_monitoring_only" else expanded_status
    elif eq_robust_present and eq_proxy_robust:
        eq_status = "eq_dead_no_proxy_robust_enough_for_forward_sampling"
    else:
        eq_status = "monitoring_only_not_robust"

    maker_funnel_quote_count = _safe_int(maker_funnel.get("quote_count") or maker_funnel.get("total_quote_count"))
    maker_funnel_inferred_count = _safe_int(
        maker_funnel.get("inferred_fill_count") or maker_funnel.get("total_inferred_fill_count")
    )
    maker_shadow_quote_count = maker_funnel_quote_count or _safe_int(maker_shadow.get("quote_count"))
    maker_shadow_inferred_count = maker_funnel_inferred_count or _safe_int(maker_shadow.get("inferred_fill_count"))
    maker_mean_without_rebate_for_row = _safe_float(
        maker_funnel.get("mean_markout_without_rebate")
        if maker_funnel.get("mean_markout_without_rebate") is not None
        else maker_shadow.get("mean_markout_without_rebate")
    )

    rows = [
        _row(
            strategy_id="maker_shadow_v2",
            status="active_shadow_testing",
            signal_count=maker_shadow_quote_count,
            executable_proxy_count=maker_shadow_quote_count,
            forward_paper_fill_count=maker_shadow_inferred_count,
            trade_proxy_pnl_cents=maker_mean_without_rebate_for_row,
            non_dust_only=True,
            main_blocker="maker_inferred_fills_are_diagnostic_only",
            next_action="collect_30_plus_inferred_fills_and_markouts_paper_only",
        ),
        _row(
            strategy_id="station_confusion_edge",
            status="active_research",
            signal_count=_safe_int(station_confusion.get("candidate_count") or station_confusion.get("active_candidate_count")),
            executable_proxy_count=_safe_int(station_confusion.get("candidate_count") or station_confusion.get("active_candidate_count")),
            forward_paper_fill_count=_safe_int(station_confusion.get("forward_paper_fill_count")),
            trade_proxy_pnl_cents=_safe_float(station_confusion.get("mean_markout_cents")),
            non_dust_only=True,
            main_blocker=(
                "station_confusion_candidate_found_paper_only"
                if _safe_int(station_confusion.get("candidate_count") or station_confusion.get("active_candidate_count")) > 0
                else "no_current_station_confusion_candidate"
            ),
            next_action="collect_station_confusion_forward_fills_paper_only",
        ),
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
            strategy_id="bucket_family_payoff_matrix_arbitrage",
            status=_structural_lp_status(
                candidate_count=_safe_int(bucket_lp.get("lp_candidate_count") or bucket_lp.get("candidate_count")),
                family_count=_safe_int(bucket_lp.get("lp_family_count") or bucket_lp.get("partition_family_count")),
            ),
            signal_count=_safe_int(bucket_lp.get("lp_family_count") or bucket_lp.get("partition_family_count")),
            executable_proxy_count=_safe_int(bucket_lp.get("lp_candidate_count") or bucket_lp.get("candidate_count")),
            forward_paper_fill_count=_safe_int(bucket_lp.get("lp_basket_paper_fill_count")),
            non_dust_only=True,
            main_blocker=(
                "structural_lp_candidate_found_paper_only"
                if _safe_int(bucket_lp.get("lp_candidate_count") or bucket_lp.get("candidate_count")) > 0
                else "no_current_payoff_matrix_edge"
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
            main_blocker="proxy_not_positive_after_outlier_removal" if eq_status == "monitoring_only_not_robust" else "needs_forward_paper_execution_sampling",
            next_action="keep_monitoring_only_no_frequency_increase" if eq_status == "monitoring_only_not_robust" else "forward_sample_supported_exact_breaches_paper_only",
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
                status="paused",
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
                status="killed",
                non_dust_only=True,
                main_blocker="market_already_reflected_observation",
                next_action="do_not_continue",
            ),
            _row(
                strategy_id="dust_tail_near_lock",
                status="killed",
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
                status="killed",
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
        if row["status"] in {"waiting_due", "single_positive_needs_more", "low_frequency_monitor_no_current_edge"}
    ]
    pause_strategies = [
        row["strategy_id"]
        for row in rows
        if row["status"] in {
            "eq_dead_no_proxy_not_robust_keep_monitoring_only",
            "monitoring_only_not_robust",
            "collapsed_into_eq_dead_no_or_paused",
            "paused_no_candidate",
            "paused",
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
            "killed",
        }
    ]
    structural_continue = [
        row["strategy_id"]
        for row in rows
        if row["status"] in {
            "structural_arbitrage_forward_paper_started",
            "structural_lp_arbitrage_forward_paper_started",
        }
    ]
    continue_strategies = sorted(set(continue_strategies + structural_continue))
    continue_research_strategies = [
        row["strategy_id"]
        for row in rows
        if row["status"] in {
            "active_shadow_testing",
            "active_research",
            "eq_dead_no_proxy_robust_enough_for_forward_sampling",
            "structural_arbitrage_forward_paper_started",
            "structural_lp_arbitrage_forward_paper_started",
        }
    ]
    low_frequency_monitor_strategies = [
        row["strategy_id"]
        for row in rows
        if row["status"] in {"low_frequency_monitor_no_current_edge", "monitoring_only_not_robust"}
    ]
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
    if non_dust_status == "failed" and eq_status == "monitoring_only_not_robust":
        live_should_pause = True
        reason = "all_current_weather_alpha_paths_failed_or_unproven"
    elif eq_status == "monitoring_only_not_robust" and non_dust_status != "single_positive_needs_more":
        live_should_pause = True
        reason = "waiting_for_non_dust_due_and_eq_dead_no_not_robust"
    elif not continue_strategies:
        live_should_pause = True
        reason = "no_current_strategy_has_robust_forward_or_resolved_edge"
    else:
        live_should_pause = False
        reason = "paper_only_forward_sampling_can_continue"
    structural_candidate_total = _safe_int(bucket_arbitrage.get("candidate_count")) + _safe_int(
        bucket_lp.get("lp_candidate_count") or bucket_lp.get("candidate_count")
    )
    if structural_candidate_total <= 0 and non_dust_status == "waiting_due":
        live_push_verdict = "wait_non_dust_due_only"
    elif structural_candidate_total <= 0 and non_dust_status == "failed":
        live_push_verdict = "live_push_pause_recommended"
    elif _safe_int(bucket_lp.get("lp_candidate_count") or bucket_lp.get("candidate_count")) > 0:
        live_push_verdict = "structural_lp_arbitrage_forward_paper_started"
    else:
        live_push_verdict = "structural_arbitrage_forward_paper_started"
    maker_inferred_count = maker_shadow_inferred_count
    maker_mean_without_rebate = _safe_float(
        maker_funnel.get("mean_markout_without_rebate")
        if maker_funnel.get("mean_markout_without_rebate") is not None
        else maker_shadow.get("mean_markout_without_rebate")
    )
    maker_opportunity_status = str(maker_funnel.get("conclusion") or maker_funnel.get("opportunity_status") or "").strip()
    station_candidate_count = _safe_int(station_confusion.get("candidate_count") or station_confusion.get("active_candidate_count"))
    station_forward_fills = _safe_int(station_confusion.get("forward_paper_fill_count"))
    station_markout = _safe_float(station_confusion.get("mean_markout_cents") or station_confusion.get("mean_markout_without_rebate"))
    station_alpha_conclusion = str(station_confusion.get("alpha_conclusion") or "").strip()
    station_confusion_status = str(station_confusion.get("station_confusion_status") or "").strip()
    maker_no_positive_statuses = {
        "maker_shadow_current_snapshot_no_quote",
        "maker_shadow_rolling_window_insufficient",
        "maker_shadow_no_current_opportunity_density",
    }
    station_no_positive_statuses = {
        "station_confusion_data_unavailable_reduce_priority",
        "station_confusion_no_current_edge",
        "research_only_insufficient_bias_samples",
    }
    if maker_opportunity_status == "maker_shadow_continue_paper_research" or (
        maker_inferred_count >= 30 and maker_mean_without_rebate is not None and maker_mean_without_rebate > 0
    ):
        live_push_status = "continue_maker_shadow_paper_only"
        next_go_no_go_trigger = "maker_shadow_v2_mean_markout_without_rebate_positive_over_30_inferred_fills"
    elif station_alpha_conclusion == "station_confusion_forward_paper_candidate_found" or (
        station_forward_fills > 0 and station_markout is not None and station_markout > 0
    ):
        live_push_status = "continue_station_confusion_forward_paper"
        next_go_no_go_trigger = "station_confusion_forward_markout_positive"
    elif non_dust_status == "single_positive_needs_more":
        live_push_status = "continue_non_dust_forward_paper_only"
        next_go_no_go_trigger = "collect_more_non_dust_forward_samples"
    elif (
        non_dust_status == "failed"
        and maker_opportunity_status in maker_no_positive_statuses
        and (station_alpha_conclusion in station_no_positive_statuses or station_confusion_status in station_no_positive_statuses)
    ):
        live_push_status = "pause_polymarket_weather_live_push"
        next_go_no_go_trigger = "new_polymarket_only_non_dust_edge_required"
    elif non_dust_status == "waiting_due":
        live_push_status = "wait_non_dust_due_and_collect_polymarket_only_shadow_evidence"
        next_go_no_go_trigger = "non_dust_uuww_due_verdict_or_maker_station_forward_evidence"
    else:
        live_push_status = "continue_polymarket_only_paper_research"
        next_go_no_go_trigger = "maker_or_station_confusion_positive_forward_evidence"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "rows": rows,
        "summary": {
            "strategy_count": len(rows),
            "scope": "polymarket_only",
            "continue_strategy_ids": continue_strategies,
            "continue_research_strategy_ids": sorted(set(continue_research_strategies)),
            "low_frequency_monitor_strategy_ids": sorted(set(low_frequency_monitor_strategies)),
            "killed_strategy_ids": kill_strategies,
            "wait_strategy_ids": wait_strategies,
            "pause_strategy_ids": pause_strategies,
            "kill_strategy_ids": kill_strategies,
            "live_should_pause": live_should_pause,
            "reason": reason,
            "alpha_viability_final_verdict": reason,
            "live_push_verdict": live_push_verdict,
            "live_push_status": live_push_status,
            "next_go_no_go_trigger": next_go_no_go_trigger,
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
            "maker_shadow_v2": {
                "quote_count": _safe_int(maker_shadow.get("quote_count")),
                "inferred_fill_count": maker_inferred_count,
                "markout_count": _safe_int(maker_shadow.get("markout_count")),
                "mean_markout_without_rebate": maker_mean_without_rebate,
                "mean_markout_with_rebate": _safe_float(maker_shadow.get("mean_markout_with_rebate")),
                "adverse_selection_count": _safe_int(maker_shadow.get("adverse_selection_count")),
                "funnel_opportunity_status": maker_opportunity_status or None,
                "funnel_total_quote_count": maker_funnel_quote_count,
                "funnel_total_inferred_fill_count": maker_funnel_inferred_count,
                "funnel_actual_window_minutes": _safe_float(maker_funnel.get("actual_window_minutes")),
                "funnel_rolling_window_sufficient": bool(maker_funnel.get("rolling_window_sufficient")),
            },
            "station_confusion_edge": {
                "station_bias_sample_count": _safe_int(station_confusion.get("station_bias_sample_count")),
                "min_required_sample_count": _safe_int(station_confusion.get("min_required_sample_count")),
                "station_confusion_status": station_confusion.get("station_confusion_status"),
                "candidate_count": station_candidate_count,
                "active_candidate_count": _safe_int(station_confusion.get("active_candidate_count")),
                "top_station_biases": station_confusion.get("top_station_biases") if isinstance(station_confusion.get("top_station_biases"), list) else [],
                "station_level_gap_reasons": station_confusion.get("station_level_gap_reasons")
                if isinstance(station_confusion.get("station_level_gap_reasons"), list)
                else [],
                "alpha_conclusion": station_alpha_conclusion or None,
            },
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
            "bucket_family_payoff_matrix_arbitrage": {
                "lp_family_count": _safe_int(bucket_lp.get("lp_family_count") or bucket_lp.get("partition_family_count")),
                "lp_candidate_count": _safe_int(bucket_lp.get("lp_candidate_count") or bucket_lp.get("candidate_count")),
                "best_lp_edge_cents": _safe_float(bucket_lp.get("best_lp_edge_cents")),
                "lp_near_miss_count": _safe_int(bucket_lp.get("lp_near_miss_count")),
                "lp_basket_paper_fill_count": _safe_int(bucket_lp.get("lp_basket_paper_fill_count")),
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
    updated["scope"] = "polymarket_only"
    updated["paper_only"] = True
    updated["counts_for_live_gate"] = False
    updated["live_order_path"] = False
    updated["historical_trade_proxy_guidance"] = scoreboard.get("historical_trade_proxy_guidance")
    updated["alpha_viability_scoreboard_summary"] = scoreboard.get("summary")
    summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}
    updated["polymarket_weather_live_push_decision"] = {
        "scope": "polymarket_only",
        "live_push_status": summary.get("live_push_status"),
        "next_go_no_go_trigger": summary.get("next_go_no_go_trigger"),
        "non_dust_threshold_cdf_status": summary.get("non_dust_threshold_cdf_status"),
        "maker_shadow_v2": summary.get("maker_shadow_v2"),
        "station_confusion_edge": summary.get("station_confusion_edge"),
        "bucket_family_payoff_matrix_arbitrage": summary.get("bucket_family_payoff_matrix_arbitrage"),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }
    updated["launchd_sampling_policy"] = {
        "scope": "polymarket_only",
        "keep": [
            "com.polyweather.intraday-observation-collector",
            "com.polyweather.bucket-family-arbitrage-sampler",
            "com.polyweather.eq-dead-no-sampler",
            "com.polyweather.non-dust-uuww-due-pipeline",
        ],
        "reduced_or_paused": {
            "com.polyweather.threshold-latency-sampler": {
                "target_start_interval_seconds": 300,
                "status": "paused_or_300s_low_value_sampler",
            },
            "com.polyweather.observation-lock-execution-sampler": {
                "target_start_interval_seconds": 300,
                "status": "paused_or_300s_low_value_sampler",
            },
        },
        "do_not_delete_evidence": True,
        "live_order_path": False,
    }
    active = updated.setdefault("active_alpha_research", {})
    if isinstance(active, dict):
        rows = {row.get("strategy_id"): row for row in scoreboard.get("rows") or [] if isinstance(row, dict)}
        maker_row = rows.get("maker_shadow_v2") or {}
        station_row = rows.get("station_confusion_edge") or {}
        active["maker_shadow_v2"] = {
            "platform": "polymarket",
            "status": maker_row.get("status") or "active_shadow_testing",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "quote_count": maker_row.get("signal_count"),
            "inferred_fill_count": maker_row.get("forward_paper_fill_count"),
            "mean_markout_without_rebate": maker_row.get("trade_proxy_pnl_cents"),
            "next_action": maker_row.get("next_action"),
            "report_path": "evidence/maker_shadow_v2/report.json",
        }
        active["station_confusion_edge"] = {
            "platform": "polymarket",
            "status": station_row.get("status") or "active_research",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "candidate_count": station_row.get("signal_count"),
            "forward_paper_fill_count": station_row.get("forward_paper_fill_count"),
            "next_action": station_row.get("next_action"),
            "report_path": "evidence/station_confusion/station_confusion_edge_report.json",
        }
        eq_row = rows.get("eq_dead_no_lock") or {}
        active["eq_dead_no_lock"] = {
            "platform": "polymarket",
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
            threshold["platform"] = "polymarket"
            threshold["status"] = "paused"
            threshold["next_action"] = "pause_or_run_at_300s_only_until_new_evidence"
            threshold["live_order_path"] = False
        bucket_rows = {
            key: rows.get(key) or {}
            for key in (
                "bucket_family_buy_all_yes",
                "bucket_family_buy_all_no",
                "monotonic_threshold_pair",
                "bucket_family_payoff_matrix_arbitrage",
            )
        }
        active["bucket_family_structural_arbitrage"] = {
            "status": "structural_arbitrage_forward_paper_started"
            if any(
                row.get("status")
                in {
                    "structural_arbitrage_forward_paper_started",
                    "structural_lp_arbitrage_forward_paper_started",
                }
                for row in bucket_rows.values()
            )
            else "low_frequency_monitor_no_current_edge",
            "platform": "polymarket",
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
