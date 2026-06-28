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
) -> Dict[str, Any]:
    weather_decision_report = weather_decision_report or {}
    top_categories = [row.get("category") for row in (opportunity_density_report.get("top_categories") or [])[:5]]
    structural_candidates = [row for row in payoff_arbitrage_report.get("candidates") or [] if isinstance(row, dict)]
    maker_quote_count = _safe_int(maker_shadow_report.get("quote_count"))
    rule_candidate_count = _safe_int(rule_confusion_report.get("candidate_count"))
    weather_status = "research_monitoring_only_until_non_dust_due"
    rows: List[Dict[str, Any]] = [
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
    if structural_candidates:
        final_next_focus = "payoff_matrix_arbitrage_candidates_paper_review"
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
        "top_structural_candidates": structural_candidates[:10],
        "top_maker_categories": maker_shadow_report.get("top_categories") or [],
        "final_next_focus": final_next_focus,
        "summary": {
            "strategy_count": len(rows),
            "top_opportunity_category": top_categories[0] if top_categories else None,
            "structural_candidate_count": _safe_int(
                payoff_arbitrage_report.get("structural_candidate_count") or payoff_arbitrage_report.get("candidate_count")
            ),
            "maker_shadow_quote_count": maker_quote_count,
            "rule_confusion_candidate_count": rule_candidate_count,
            "weather_status": weather_status,
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
