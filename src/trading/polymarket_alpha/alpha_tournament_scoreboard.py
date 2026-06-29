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
    microstructure_policy_sweep_report: Optional[Dict[str, Any]] = None,
    microstructure_markout_report: Optional[Dict[str, Any]] = None,
    microstructure_experiment_report: Optional[Dict[str, Any]] = None,
    microstructure_attribution_report: Optional[Dict[str, Any]] = None,
    microstructure_inverse_report: Optional[Dict[str, Any]] = None,
    maker_quote_sweep_report: Optional[Dict[str, Any]] = None,
    weather_lp_experiment_report: Optional[Dict[str, Any]] = None,
    maker_shadow_report: Optional[Dict[str, Any]] = None,
    payoff_arbitrage_report: Optional[Dict[str, Any]] = None,
    global_oos_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    crypto_touch_validation_report = crypto_touch_validation_report or {}
    crypto_terminal_report = crypto_terminal_report or {}
    microstructure_report = microstructure_report or {}
    microstructure_policy_sweep_report = microstructure_policy_sweep_report or {}
    microstructure_markout_report = microstructure_markout_report or {}
    microstructure_experiment_report = microstructure_experiment_report or {}
    microstructure_attribution_report = microstructure_attribution_report or {}
    microstructure_inverse_report = microstructure_inverse_report or {}
    maker_quote_sweep_report = maker_quote_sweep_report or {}
    weather_lp_experiment_report = weather_lp_experiment_report or {}
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

    micro_policy_lane = _lane(
        lane_id="microstructure_policy_sweep",
        candidate_count=_safe_int(microstructure_policy_sweep_report.get("policy_candidate_count")),
        paper_fill_count=0,
        watch_count=_safe_int(microstructure_policy_sweep_report.get("input_watch_count")),
        sample_count=_safe_int(microstructure_policy_sweep_report.get("input_watch_count")),
        main_blocker=_top_blocker(microstructure_policy_sweep_report),
    )

    taker_failed = str(microstructure_attribution_report.get("microstructure_taker_status") or "") == "failed_negative_forward_markout"
    inverse_decision = str(microstructure_inverse_report.get("decision") or "")
    maker_sweep_recommendation = str(maker_quote_sweep_report.get("mode_recommendation") or "")
    inverse_promising = inverse_decision == "microstructure_taker_direction_maybe_inverted"
    maker_promising = maker_sweep_recommendation == "promising_shadow_mode"

    micro_taker_lane = _lane(
        lane_id="microstructure_taker",
        candidate_count=_safe_int(microstructure_policy_sweep_report.get("taker_candidate_count")),
        paper_fill_count=_safe_int(microstructure_experiment_report.get("taker_fill_count") or microstructure_policy_sweep_report.get("taker_fill_count")),
        watch_count=0,
        available_markout_count=_safe_int(microstructure_experiment_report.get("valid_markout_count") or microstructure_markout_report.get("available_markout_count")),
        mean_5m_markout=_safe_float(microstructure_experiment_report.get("mean_5m_markout")) or _mean_horizon(microstructure_markout_report, "5m"),
        mean_15m_markout=_safe_float(microstructure_experiment_report.get("mean_15m_markout")) or _mean_horizon(microstructure_markout_report, "15m"),
        mean_1h_markout=_safe_float(microstructure_experiment_report.get("mean_1h_markout")) or _mean_horizon(microstructure_markout_report, "1h"),
        sample_count=_safe_int(microstructure_experiment_report.get("taker_fill_count") or microstructure_policy_sweep_report.get("taker_fill_count")),
        main_blocker=str(microstructure_experiment_report.get("recommendation") or _top_blocker(microstructure_policy_sweep_report)),
    )
    if taker_failed and inverse_promising:
        micro_taker_lane["status"] = "inverse_candidate_promising"
        micro_taker_lane["next_action"] = "paper_test_inverse_only"
        micro_taker_lane["priority"] = 65
        micro_taker_lane["main_blocker"] = "follow_imbalance_failed_inverse_promising"
    elif taker_failed:
        micro_taker_lane["status"] = "failed_negative_forward_markout"
        micro_taker_lane["next_action"] = "pause_taker_microstructure"
        micro_taker_lane["priority"] = 5
        micro_taker_lane["main_blocker"] = "follow_and_or_inverse_not_promising"

    micro_maker_lane = _lane(
        lane_id="microstructure_maker",
        candidate_count=_safe_int(microstructure_policy_sweep_report.get("maker_candidate_count")),
        paper_fill_count=_safe_int(microstructure_experiment_report.get("maker_inferred_fill_count") or microstructure_policy_sweep_report.get("maker_inferred_fill_count")),
        watch_count=_safe_int(microstructure_experiment_report.get("maker_quote_count") or microstructure_policy_sweep_report.get("maker_quote_count")),
        available_markout_count=0,
        sample_count=_safe_int(microstructure_experiment_report.get("maker_quote_count") or microstructure_policy_sweep_report.get("maker_quote_count")),
        main_blocker=str(microstructure_experiment_report.get("recommendation") or _top_blocker(microstructure_policy_sweep_report)),
    )
    if maker_sweep_recommendation == "promising_shadow_mode":
        micro_maker_lane["status"] = "promising_shadow_mode"
        micro_maker_lane["next_action"] = "focused_maker_shadow_paper"
        micro_maker_lane["priority"] = 75
        micro_maker_lane["main_blocker"] = ""
        micro_maker_lane["available_markout_count"] = _safe_int(maker_quote_sweep_report.get("inferred_fill_count"))
        micro_maker_lane["mean_1h_markout"] = _best_maker_mode_markout(maker_quote_sweep_report)
    elif maker_sweep_recommendation in {"maker_adverse_selection", "reject_maker_for_now", "maker_no_fill_even_aggressive"}:
        micro_maker_lane["status"] = maker_sweep_recommendation
        micro_maker_lane["next_action"] = "downgrade_maker_microstructure"
        micro_maker_lane["priority"] = 5
        micro_maker_lane["main_blocker"] = maker_sweep_recommendation
    elif micro_maker_lane["watch_count"] >= 30 and micro_maker_lane["paper_fill_count"] == 0:
        micro_maker_lane["status"] = "watch_only_no_inferred_fills"
        micro_maker_lane["next_action"] = "keep_maker_shadow_only"
        micro_maker_lane["priority"] = 35

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
    if maker_sweep_recommendation == "promising_shadow_mode":
        maker_lane["status"] = "promising_shadow_mode"
        maker_lane["next_action"] = "focused_maker_shadow_paper"
        maker_lane["priority"] = max(int(maker_lane.get("priority") or 0), 75)
        maker_lane["main_blocker"] = ""
    elif maker_sweep_recommendation in {"maker_adverse_selection", "reject_maker_for_now", "maker_no_fill_even_aggressive"}:
        maker_lane["status"] = maker_sweep_recommendation
        maker_lane["next_action"] = "downgrade_maker_shadow"
        maker_lane["priority"] = 5
        maker_lane["main_blocker"] = maker_sweep_recommendation

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

    weather_lp_lane = _lane(
        lane_id="weather_lp_reward",
        candidate_count=_safe_int(weather_lp_experiment_report.get("reward_qualified_quote_count") or weather_lp_experiment_report.get("paper_quote_count")),
        paper_fill_count=_safe_int(weather_lp_experiment_report.get("inferred_fill_count")),
        watch_count=_safe_int(weather_lp_experiment_report.get("paper_quote_count")),
        available_markout_count=_safe_int(weather_lp_experiment_report.get("markout_count")),
        mean_5m_markout=_safe_float(weather_lp_experiment_report.get("mean_5m_markout")),
        mean_15m_markout=_safe_float(weather_lp_experiment_report.get("mean_15m_markout")),
        mean_1h_markout=_safe_float(weather_lp_experiment_report.get("mean_1h_markout")),
        sample_count=_safe_int(weather_lp_experiment_report.get("paper_quote_count")),
        main_blocker=str(weather_lp_experiment_report.get("recommendation") or "not_run"),
    )
    weather_lp_lane["estimated_reward"] = weather_lp_experiment_report.get("estimated_reward_cents")
    weather_lp_lane["reward_points_proxy"] = weather_lp_experiment_report.get("reward_points_proxy") or weather_lp_experiment_report.get("cumulative_reward_points_proxy")
    weather_lp_lane["estimated_reward_cents_proxy"] = weather_lp_experiment_report.get("estimated_reward_cents_proxy")
    weather_lp_lane["quote_update_count"] = _safe_int(weather_lp_experiment_report.get("quote_update_count"))
    weather_lp_lane["reward_to_risk_proxy"] = weather_lp_experiment_report.get("reward_to_risk_proxy")
    weather_lp_lane["net_estimated_pnl_with_reward"] = weather_lp_experiment_report.get("net_estimated_pnl_with_reward")
    if weather_lp_experiment_report.get("recommendation") in {"insufficient_reward_metadata", "reward_metadata_pipeline_broken_or_no_rewards"}:
        weather_lp_lane["status"] = str(weather_lp_experiment_report.get("recommendation"))
        weather_lp_lane["next_action"] = "collect_reward_metadata"
        weather_lp_lane["priority"] = 12
    elif _safe_int(weather_lp_experiment_report.get("quote_update_count")) < 50:
        weather_lp_lane["status"] = "continue_weather_lp_paper_insufficient_updates"
        weather_lp_lane["next_action"] = "collect_weather_lp_quote_update_markout"
        weather_lp_lane["priority"] = 40
    elif _safe_float(weather_lp_experiment_report.get("reward_to_risk_proxy")) is not None and float(weather_lp_experiment_report.get("reward_to_risk_proxy")) < 1.0:
        weather_lp_lane["status"] = "reward_vs_risk_negative_research_only"
        weather_lp_lane["next_action"] = "tighten_weather_lp_cancellation_or_city_filters"
        weather_lp_lane["priority"] = 25
    elif _safe_int(weather_lp_experiment_report.get("paper_quote_count")) >= 50 and (_safe_float(weather_lp_experiment_report.get("net_estimated_pnl_with_reward")) or 0.0) > 0:
        weather_lp_lane["status"] = "paper_reward_lane_candidate"
        weather_lp_lane["next_action"] = "continue_weather_lp_paper"
        weather_lp_lane["priority"] = 80

    lanes = [
        touch_lane,
        terminal_lane,
        weather_lp_lane,
        micro_lane,
        micro_policy_lane,
        micro_taker_lane,
        micro_maker_lane,
        maker_lane,
        payoff_lane,
        oos_lane,
    ]
    if taker_failed and not inverse_promising and not maker_promising:
        for lane in lanes:
            if lane["lane_id"] in {"microstructure", "microstructure_policy_sweep"}:
                lane["status"] = "pause_microstructure_after_negative_taker_and_maker_sweep"
                lane["next_action"] = "move_focus_to_next_lane"
                lane["priority"] = min(int(lane.get("priority") or 0), 8)
                lane["main_blocker"] = "negative_taker_markout_and_no_promising_maker_mode"
    ranked = sorted(lanes, key=lambda row: int(row.get("priority") or 0), reverse=True)
    top_lane = ranked[0]["lane_id"] if ranked else None
    downgraded = [row["lane_id"] for row in lanes if str(row.get("status") or "").startswith("downgraded")]
    paused = [
        row["lane_id"]
        for row in lanes
        if any(token in str(row.get("status") or "") for token in ("failed", "pause", "adverse_selection", "reject"))
    ]
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
        "lanes_paused": paused,
        "next_focus": top_lane,
        "final_decision": "paper_only_alpha_tournament_continue" if top_lane else "no_active_lane",
        "microstructure_decision_inputs": {
            "taker_failed": taker_failed,
            "inverse_decision": inverse_decision,
            "maker_sweep_recommendation": maker_sweep_recommendation,
        },
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


def _best_maker_mode_markout(report: Dict[str, Any]) -> Optional[float]:
    rows = report.get("inferred_fill_count_by_mode") or report.get("mean_markout_by_mode") or []
    best: Optional[float] = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = _safe_float(row.get("mean_markout_cents"))
        if value is None:
            continue
        if best is None or value > best:
            best = value
    return best


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
