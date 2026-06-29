from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_tournament_scoreboard.v1"


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


def _mean_horizon(report: Dict[str, Any], label: str) -> Optional[float]:
    aliases = {
        "5m": {"300", "300s", "5m", "300.0"},
        "15m": {"900", "900s", "15m", "900.0"},
        "1h": {"3600", "3600s", "1h", "3600.0"},
    }.get(label, {label})
    for key in ("by_horizon", "mean_markout_by_horizon"):
        for row in report.get(key) or []:
            if not isinstance(row, dict):
                continue
            bucket = str(row.get("bucket") if row.get("bucket") is not None else row.get("horizon_seconds"))
            if bucket in aliases:
                value = row.get("mean_markout_cents")
                if value is None:
                    value = row.get("mean")
                return _safe_float(value)
    direct = {
        "5m": ("mean_5m_markout", "mean_markout_5m"),
        "15m": ("mean_15m_markout", "mean_markout_15m"),
        "1h": ("mean_1h_markout", "mean_markout_1h_cents", "mean_markout_1h"),
    }.get(label, ())
    for key in direct:
        value = _safe_float(report.get(key))
        if value is not None:
            return value
    return None


def _lane_priority(
    *,
    candidate_count: int,
    paper_fill_count: int,
    watch_count: int,
    available_markout_count: int,
    mean_1h_markout: Optional[float],
    main_blocker: str,
) -> tuple[str, str, int]:
    if mean_1h_markout is not None and mean_1h_markout < 0:
        return "downgraded_negative_markout", "downgrade", 10
    if available_markout_count >= 20 and mean_1h_markout is not None and mean_1h_markout > 0:
        return "focused_paper_candidate", "promote_to_focused_paper", 90
    if candidate_count > 0 or paper_fill_count > 0:
        return "paper_candidates_found", "collect_forward_markout", 70
    if watch_count > 0:
        return "watch_only", "continue_collecting", 45
    if main_blocker:
        return "blocked_or_monitoring", "monitor", 20
    return "no_current_edge", "monitor", 15


def _lane(
    *,
    lane_id: str,
    candidate_count: int = 0,
    paper_fill_count: int = 0,
    watch_count: int = 0,
    available_markout_count: int = 0,
    mean_5m_markout: Optional[float] = None,
    mean_15m_markout: Optional[float] = None,
    mean_1h_markout: Optional[float] = None,
    sample_count: int = 0,
    main_blocker: str = "",
) -> Dict[str, Any]:
    status, next_action, priority = _lane_priority(
        candidate_count=int(candidate_count),
        paper_fill_count=int(paper_fill_count),
        watch_count=int(watch_count),
        available_markout_count=int(available_markout_count),
        mean_1h_markout=mean_1h_markout,
        main_blocker=main_blocker,
    )
    return {
        "lane_id": lane_id,
        "status": status,
        "candidate_count": int(candidate_count),
        "paper_fill_count": int(paper_fill_count),
        "watch_count": int(watch_count),
        "available_markout_count": int(available_markout_count),
        "mean_5m_markout": mean_5m_markout,
        "mean_15m_markout": mean_15m_markout,
        "mean_1h_markout": mean_1h_markout,
        "sample_count": int(sample_count),
        "main_blocker": main_blocker,
        "next_action": next_action,
        "priority": int(priority),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def build_alpha_tournament_scoreboard(
    *,
    crypto_touch_validation_report: Optional[Dict[str, Any]] = None,
    crypto_terminal_report: Optional[Dict[str, Any]] = None,
    microstructure_report: Optional[Dict[str, Any]] = None,
    microstructure_markout_report: Optional[Dict[str, Any]] = None,
    maker_shadow_report: Optional[Dict[str, Any]] = None,
    payoff_arbitrage_report: Optional[Dict[str, Any]] = None,
    global_oos_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    crypto_touch_validation_report = crypto_touch_validation_report or {}
    crypto_terminal_report = crypto_terminal_report or {}
    microstructure_report = microstructure_report or {}
    microstructure_markout_report = microstructure_markout_report or {}
    maker_shadow_report = maker_shadow_report or {}
    payoff_arbitrage_report = payoff_arbitrage_report or {}
    global_oos_report = global_oos_report or {}

    formal = crypto_touch_validation_report.get("formal_fills") if isinstance(crypto_touch_validation_report.get("formal_fills"), dict) else {}
    near = crypto_touch_validation_report.get("near_miss_watch") if isinstance(crypto_touch_validation_report.get("near_miss_watch"), dict) else {}
    verdict = crypto_touch_validation_report.get("verdict") if isinstance(crypto_touch_validation_report.get("verdict"), dict) else {}
    touch_lane = _lane(
        lane_id="crypto_touch_shadow",
        candidate_count=0,
        paper_fill_count=_safe_int(formal.get("fill_count")),
        watch_count=_safe_int(near.get("watch_count")),
        available_markout_count=_safe_int(formal.get("available_markout_count")),
        mean_5m_markout=_safe_float(formal.get("mean_5m_markout")),
        mean_15m_markout=_safe_float(formal.get("mean_15m_markout")),
        mean_1h_markout=_safe_float(formal.get("mean_1h_markout")),
        sample_count=_safe_int(formal.get("fill_count")),
        main_blocker=str(verdict.get("status") or crypto_touch_validation_report.get("crypto_touch_status") or "shadow_only_pending_recalibration"),
    )
    if str(touch_lane["main_blocker"]) == "shadow_only_pending_recalibration":
        touch_lane["status"] = "downgraded_recalibration_pending"
        touch_lane["next_action"] = "keep watch only until recalibration improves"
        touch_lane["priority"] = 5

    terminal_lane = _lane(
        lane_id="crypto_terminal",
        candidate_count=_safe_int(crypto_terminal_report.get("candidate_count")),
        paper_fill_count=_safe_int(crypto_terminal_report.get("paper_fill_count")),
        watch_count=_safe_int(crypto_terminal_report.get("near_miss_count")),
        available_markout_count=0,
        sample_count=_safe_int(crypto_terminal_report.get("candidate_count")),
        main_blocker=_top_blocker(crypto_terminal_report),
    )

    micro_lane = _lane(
        lane_id="microstructure",
        candidate_count=_safe_int(microstructure_report.get("candidate_count")),
        paper_fill_count=_safe_int(microstructure_report.get("paper_fill_count")),
        watch_count=_safe_int(microstructure_report.get("watch_count")),
        available_markout_count=_safe_int(microstructure_markout_report.get("available_markout_count")),
        mean_5m_markout=_mean_horizon(microstructure_markout_report, "5m"),
        mean_15m_markout=_mean_horizon(microstructure_markout_report, "15m"),
        mean_1h_markout=_mean_horizon(microstructure_markout_report, "1h"),
        sample_count=_safe_int(microstructure_report.get("candidate_count")),
        main_blocker=_top_blocker(microstructure_report),
    )

    maker_lane = _lane(
        lane_id="maker_shadow",
        candidate_count=_safe_int(maker_shadow_report.get("quote_count")),
        paper_fill_count=_safe_int(maker_shadow_report.get("inferred_fill_count")),
        watch_count=_safe_int(maker_shadow_report.get("quote_count")),
        available_markout_count=_safe_int(maker_shadow_report.get("markout_count")),
        mean_5m_markout=_safe_float(maker_shadow_report.get("mean_markout_without_rebate")),
        sample_count=_safe_int(maker_shadow_report.get("quote_count")),
        main_blocker=_top_blocker(maker_shadow_report),
    )

    payoff_lane = _lane(
        lane_id="payoff_arbitrage",
        candidate_count=_safe_int(payoff_arbitrage_report.get("candidate_count") or payoff_arbitrage_report.get("lp_candidate_count")),
        paper_fill_count=_safe_int(payoff_arbitrage_report.get("paper_fill_count") or payoff_arbitrage_report.get("basket_paper_fill_count")),
        watch_count=_safe_int(payoff_arbitrage_report.get("near_miss_count") or payoff_arbitrage_report.get("lp_near_miss_count")),
        main_blocker=_top_blocker(payoff_arbitrage_report),
    )

    oos_lane = _lane(
        lane_id="global_probability_oos",
        candidate_count=_safe_int(global_oos_report.get("candidate_count")),
        paper_fill_count=0,
        watch_count=0,
        sample_count=_safe_int(global_oos_report.get("oos_prediction_count") or global_oos_report.get("validate_row_count")),
        main_blocker=str(global_oos_report.get("status") or global_oos_report.get("hard_conclusion") or _top_blocker(global_oos_report)),
    )

    lanes = [touch_lane, terminal_lane, micro_lane, maker_lane, payoff_lane, oos_lane]
    ranked = sorted(lanes, key=lambda row: int(row.get("priority") or 0), reverse=True)
    top_lane = ranked[0]["lane_id"] if ranked else None
    downgraded = [row["lane_id"] for row in lanes if str(row.get("status") or "").startswith("downgraded")]
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "lane_count": len(lanes),
        "lanes": lanes,
        "top_lane": top_lane,
        "lanes_downgraded": downgraded,
        "next_focus": top_lane,
        "final_decision": "paper_only_alpha_tournament_continue" if top_lane else "no_active_lane",
    }


def _top_blocker(report: Dict[str, Any]) -> str:
    rows = report.get("blocker_counts") or report.get("gap_reasons") or []
    if isinstance(rows, list) and rows:
        first = rows[0]
        if isinstance(first, dict):
            return str(first.get("reason") or first.get("bucket") or "blocked")
    if report.get("candidate_count") == 0:
        return "no_current_candidate"
    return ""


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "SCHEMA_VERSION",
    "build_alpha_tournament_scoreboard",
    "load_json",
    "write_json",
]
