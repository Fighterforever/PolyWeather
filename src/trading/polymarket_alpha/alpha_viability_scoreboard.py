from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = "polyweather_polymarket_alpha_viability_scoreboard.v1"


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


def _row(
    *,
    strategy_id: str,
    category: str,
    candidate_count: int = 0,
    paper_fill_count: int = 0,
    markout_count: int = 0,
    resolved_pnl_cents: Optional[float] = None,
    main_blocker: str,
    next_action: str,
) -> Dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "category": category,
        "platform": "polymarket",
        "candidate_count": int(candidate_count),
        "paper_fill_count": int(paper_fill_count),
        "markout_count": int(markout_count),
        "resolved_pnl_cents": resolved_pnl_cents,
        "main_blocker": main_blocker,
        "next_action": next_action,
        "live_eligible": False,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def build_alpha_viability_scoreboard(
    *,
    opportunity_density_report: Dict[str, Any],
    payoff_arbitrage_report: Dict[str, Any],
    maker_shadow_report: Dict[str, Any],
    rule_confusion_report: Dict[str, Any],
    weather_decision_report: Dict[str, Any] | None = None,
    probability_edge_model_report: Dict[str, Any] | None = None,
    active_probability_edge_report: Dict[str, Any] | None = None,
    category_focus_report: Dict[str, Any] | None = None,
    probability_markout_report: Dict[str, Any] | None = None,
    probability_resolved_audit_report: Dict[str, Any] | None = None,
    maker_shadow_focus_report: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    weather_decision_report = weather_decision_report or {}
    probability_edge_model_report = probability_edge_model_report or {}
    active_probability_edge_report = active_probability_edge_report or {}
    category_focus_report = category_focus_report or {}
    probability_markout_report = probability_markout_report or {}
    probability_resolved_audit_report = probability_resolved_audit_report or {}
    maker_shadow_focus_report = maker_shadow_focus_report or {}
    top_categories = [row.get("category") for row in (opportunity_density_report.get("top_categories") or [])[:5]]
    structural_candidates = [row for row in payoff_arbitrage_report.get("candidates") or [] if isinstance(row, dict)]
    maker_quote_count = _safe_int(maker_shadow_report.get("quote_count"))
    maker_focus_quote_count = _safe_int(maker_shadow_focus_report.get("quote_count"))
    rule_candidate_count = _safe_int(rule_confusion_report.get("candidate_count"))
    weather_status = "research_monitoring_only_until_non_dust_due"
    model_positive_categories = [
        row.get("category")
        for row in (probability_edge_model_report.get("by_category") or [])
        if isinstance(row, dict)
        and (_safe_float(row.get("brier_improvement")) or 0.0) > 0
        and (_safe_float(row.get("log_loss_improvement")) or 0.0) > 0
    ]
    top_focus_categories = [
        row.get("category")
        for row in (category_focus_report.get("top_focus_categories") or [])
        if isinstance(row, dict) and row.get("category")
    ]
    forward_paper_fill_count = _safe_int(active_probability_edge_report.get("paper_fill_count"))
    markout_mean = _safe_float(probability_markout_report.get("mean_markout_1h"))
    if markout_mean is None:
        markout_mean = _safe_float(probability_markout_report.get("mean_markout"))
    resolved_pnl_cents = _safe_float(probability_resolved_audit_report.get("resolved_pnl_cents"))
    resolved_fill_count = _safe_int(probability_resolved_audit_report.get("resolved_fill_count"))
    if resolved_pnl_cents is not None and resolved_pnl_cents > 0 and resolved_fill_count >= 30:
        live_push_status = "tiny_live_review_candidate"
    elif markout_mean is not None and markout_mean > 0:
        live_push_status = "continue_forward_paper"
    elif forward_paper_fill_count > 0:
        live_push_status = "collect_forward_markouts"
    elif model_positive_categories and forward_paper_fill_count <= 0:
        live_push_status = "wait_for_forward_candidates"
    elif not model_positive_categories:
        live_push_status = "no_probability_edge_yet"
    else:
        live_push_status = "collect_forward_markouts"
    rows: List[Dict[str, Any]] = [
        _row(
            strategy_id="probability_edge_model",
            category="all_market",
            candidate_count=_safe_int(probability_edge_model_report.get("candidate_count")),
            paper_fill_count=0,
            resolved_pnl_cents=_safe_float(probability_edge_model_report.get("resolved_candidate_pnl_proxy")),
            main_blocker=(
                "oos_model_positive_categories_found"
                if model_positive_categories
                else "no_oos_probability_model_beats_market"
            ),
            next_action="scan active focus categories for forward paper candidates",
        ),
        _row(
            strategy_id="active_probability_edge_scanner",
            category="focus_categories",
            candidate_count=_safe_int(active_probability_edge_report.get("candidate_count")),
            paper_fill_count=forward_paper_fill_count,
            markout_count=_safe_int(probability_markout_report.get("markout_count")),
            resolved_pnl_cents=resolved_pnl_cents,
            main_blocker=(
                "forward_paper_candidates_found"
                if forward_paper_fill_count > 0
                else "no_active_executable_probability_edge_candidate"
            ),
            next_action="track markout and resolved audit; unresolved PnL stays null",
        ),
        _row(
            strategy_id="maker_shadow_focus",
            category="focus_categories",
            candidate_count=maker_focus_quote_count,
            paper_fill_count=_safe_int(maker_shadow_focus_report.get("inferred_fill_count")),
            markout_count=_safe_int(maker_shadow_focus_report.get("markout_count")),
            resolved_pnl_cents=None,
            main_blocker=(
                "maker_focus_quotes_found"
                if maker_focus_quote_count > 0
                else "no_focus_category_model_based_maker_quote"
            ),
            next_action="collect trade tape/orderbook touch markouts paper_only",
        ),
        _row(
            strategy_id="payoff_matrix_arbitrage",
            category="all_market",
            candidate_count=_safe_int(payoff_arbitrage_report.get("structural_candidate_count") or payoff_arbitrage_report.get("candidate_count")),
            paper_fill_count=0,
            main_blocker=(
                "no_current_structural_candidate"
                if _safe_int(payoff_arbitrage_report.get("structural_candidate_count") or payoff_arbitrage_report.get("candidate_count")) <= 0
                else "paper_only_structural_candidate_review"
            ),
            next_action="forward paper only on structural candidates and near misses",
        ),
        _row(
            strategy_id="maker_shadow",
            category="top_opportunity_categories",
            candidate_count=maker_quote_count,
            paper_fill_count=_safe_int(maker_shadow_report.get("inferred_fill_count")),
            markout_count=_safe_int(maker_shadow_report.get("markout_count")),
            resolved_pnl_cents=None,
            main_blocker="maker_shadow_diagnostic_only_no_real_fills",
            next_action="collect trade tape/orderbook touch markouts paper_only",
        ),
        _row(
            strategy_id="rule_confusion_scanner",
            category="all_market",
            candidate_count=rule_candidate_count,
            main_blocker="candidate_discovery_only_no_trade_rule",
            next_action="manually review top liquid confusion candidates before any paper fill design",
        ),
        _row(
            strategy_id="weather_module",
            category="weather",
            candidate_count=0,
            main_blocker="weather_live_push_downgraded_after_failed_or_unproven_substrategies",
            next_action="keep non_dust_uuww_due_runner and bucket_family_lp_low_frequency_monitor",
        ),
    ]
    model_blocker = str(probability_edge_model_report.get("oos_blocker_reason") or "")
    if structural_candidates:
        final_next_focus = "payoff_matrix_arbitrage_candidates_paper_review"
    elif live_push_status == "no_probability_edge_yet":
        final_next_focus = (
            "expand_mid_price_decision_snapshot_family_coverage"
            if model_blocker
            else "probability_edge_model_research"
        )
    elif live_push_status in {"continue_forward_paper", "collect_forward_markouts"}:
        final_next_focus = "probability_edge_forward_paper_markout"
    elif top_focus_categories:
        final_next_focus = "probability_edge_active_scan_focus_categories"
    elif maker_quote_count > 0:
        final_next_focus = "maker_shadow_top_categories_paper_markout"
    elif rule_candidate_count > 0:
        final_next_focus = "rule_confusion_manual_review"
    else:
        final_next_focus = "expand_market_discovery_and_collect_orderbook_trade_tape_density"
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "rows": rows,
        "weather_module_status": weather_status,
        "weather_live_push_status": weather_status,
        "weather_non_dust_uuww_due_runner": "remains_active",
        "weather_bucket_family_lp": "low_frequency_monitor",
        "top_opportunity_categories": top_categories,
        "top_focus_categories": top_focus_categories,
        "model_oos_positive_categories": model_positive_categories[:10],
        "top_structural_candidates": structural_candidates[:10],
        "top_maker_categories": maker_shadow_focus_report.get("top_focus_categories") or maker_shadow_report.get("top_categories") or [],
        "final_next_focus": final_next_focus,
        "live_push_status": live_push_status,
        "summary": {
            "strategy_count": len(rows),
            "top_opportunity_category": top_categories[0] if top_categories else None,
            "top_focus_categories": top_focus_categories,
            "model_oos_positive_categories": model_positive_categories[:10],
            "forward_paper_fill_count": forward_paper_fill_count,
            "markout_mean": markout_mean,
            "resolved_pnl_cents": resolved_pnl_cents,
            "live_push_status": live_push_status,
            "structural_candidate_count": _safe_int(
                payoff_arbitrage_report.get("structural_candidate_count") or payoff_arbitrage_report.get("candidate_count")
            ),
            "maker_shadow_quote_count": maker_quote_count,
            "maker_shadow_focus_quote_count": maker_focus_quote_count,
            "rule_confusion_candidate_count": rule_candidate_count,
            "weather_status": weather_status,
            "probability_model_blocker": model_blocker or None,
            "final_next_focus": final_next_focus,
            "live_order_path": False,
        },
    }


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["SCHEMA_VERSION", "build_alpha_viability_scoreboard", "load_json", "write_json"]
