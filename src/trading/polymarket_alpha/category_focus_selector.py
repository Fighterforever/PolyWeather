from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_category_focus.v1"


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _by_category(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("category")): row for row in rows if isinstance(row, dict) and row.get("category")}


def build_category_focus_report(
    *,
    model_report: Dict[str, Any],
    opportunity_density_report: Dict[str, Any],
    min_sample_count: int = 50,
) -> Dict[str, Any]:
    model_by_cat = _by_category(model_report.get("by_category") or [])
    density_by_cat = _by_category(opportunity_density_report.get("categories") or [])
    categories = sorted(set(model_by_cat) | set(density_by_cat))
    rows: List[Dict[str, Any]] = []
    for category in categories:
        model = model_by_cat.get(category) or {}
        density = density_by_cat.get(category) or {}
        sample_count = _safe_int(model.get("sample_count"))
        brier_improvement = _safe_float(model.get("brier_improvement"))
        log_loss_improvement = _safe_float(model.get("log_loss_improvement"))
        positive_ev_rate = _safe_float(model.get("positive_ev_candidate_rate"))
        active_market_count = _safe_int(density.get("active_market_count"))
        trade_tape_density = _safe_float(density.get("trade_tape_density"))
        median_spread = density.get("median_spread")
        median_depth = density.get("median_depth")
        execution_feasibility = 1.0 if median_depth is not None and _safe_float(median_depth) > 10 else 0.0
        resolution_speed = _safe_float(density.get("resolution_speed_score"))
        activity_score = round(min(1.0, active_market_count / 100.0) + min(1.0, trade_tape_density / 20.0) + resolution_speed, 6)
        model_edge_available = sample_count > 0 and model.get("brier_improvement") is not None and model.get("log_loss_improvement") is not None
        model_edge_score = round(
            max(0.0, brier_improvement) * 10
            + max(0.0, log_loss_improvement) * 2
            + positive_ev_rate
            + min(1.0, sample_count / max(1, int(min_sample_count))),
            6,
        ) if model_edge_available else None
        execution_score = round(execution_feasibility + (0.5 if median_spread is not None and _safe_float(median_spread) <= 0.05 else 0.0), 6)
        focus_score = round(activity_score + (model_edge_score or 0.0) + execution_score, 6)
        if not model_edge_available:
            recommendation = "collect_model_data"
        elif sample_count >= int(min_sample_count) and brier_improvement > 0 and log_loss_improvement > 0 and positive_ev_rate > 0:
            recommendation = "focus_forward_paper"
        elif sample_count < int(min_sample_count):
            recommendation = "collect_more_data"
        elif brier_improvement <= 0 and log_loss_improvement <= 0:
            recommendation = "reject_for_now"
        else:
            recommendation = "monitor_only"
        if category == "weather" and recommendation == "focus_forward_paper":
            recommendation = "monitor_only"
        rows.append(
            {
                "category": category,
                "focus_score": focus_score,
                "activity_score": activity_score,
                "model_edge_score": model_edge_score,
                "execution_score": execution_score,
                "sample_count": sample_count,
                "brier_improvement": brier_improvement,
                "log_loss_improvement": log_loss_improvement,
                "positive_ev_candidate_rate": positive_ev_rate,
                "active_market_count": active_market_count,
                "trade_tape_density": trade_tape_density,
                "median_spread": median_spread,
                "median_depth": median_depth,
                "resolution_speed": resolution_speed,
                "execution_feasibility": execution_feasibility,
                "recommendation": recommendation,
                "why": (
                    "model_edge_unavailable"
                    if not model_edge_available
                    else "positive_oos_model_edge_and_execution_context"
                    if recommendation == "focus_forward_paper"
                    else "insufficient_or_negative_oos_model_edge"
                ),
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    ranked = sorted(rows, key=lambda row: float(row.get("focus_score") or 0.0), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "min_sample_count": int(min_sample_count),
        "category_count": len(ranked),
        "categories": ranked,
        "top_focus_categories": [row for row in ranked if row.get("recommendation") == "focus_forward_paper"][:5],
        "top_categories_for_forward_paper": [
            row.get("category") for row in ranked if row.get("recommendation") == "focus_forward_paper"
        ][:5],
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["SCHEMA_VERSION", "build_category_focus_report", "load_json", "write_json"]
