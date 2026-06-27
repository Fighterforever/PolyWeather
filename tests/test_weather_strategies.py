from __future__ import annotations

from src.trading.weather_strategies import assign_weather_strategy


def test_assign_weather_strategy_marks_exact_bucket_shadow_only():
    assignment = assign_weather_strategy(
        {"ev_safe": 0.2, "price": 0.2},
        bucket_type="eq",
    )

    assert assignment.strategy_id == "eq_exact_shadow"
    assert assignment.live_eligible is False
    assert assignment.counts_for_live_gate is False
    assert assignment.execution_style == "shadow_only"


def test_assign_weather_strategy_selects_near_lock_before_tail_threshold():
    assignment = assign_weather_strategy(
        {
            "end_date": "2026-06-27T04:00:00Z",
            "ev_safe": 0.12,
            "q_effective": 0.30,
        },
        bucket_type="ge",
        now="2026-06-27T00:00:00Z",
    )

    assert assignment.strategy_id == "near_lock"
    assert assignment.live_eligible is True
    assert assignment.risk_caps["max_position_usdc"] == 12.0


def test_assign_weather_strategy_selects_tail_threshold_for_le_ge():
    assignment = assign_weather_strategy(
        {"ev_safe": 0.05, "q_effective": 0.10},
        bucket_type="le",
    )

    assert assignment.strategy_id == "tail_threshold"
    assert assignment.execution_style == "taker_depth_checked"
    assert assignment.counts_for_live_gate is True
