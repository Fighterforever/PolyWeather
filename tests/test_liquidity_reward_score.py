from __future__ import annotations

from src.trading.polymarket_alpha.liquidity_reward_score import compute_liquidity_reward_score, order_score


def test_score_zero_outside_max_spread():
    assert order_score(0.04, 0.05, size=100, min_incentive_size=50) == 0.0


def test_score_increases_with_size():
    small = order_score(0.04, 0.01, size=50, min_incentive_size=50)
    large = order_score(0.04, 0.01, size=100, min_incentive_size=50)
    assert large > small > 0


def test_score_decreases_with_spread():
    tight = order_score(0.04, 0.005, size=100, min_incentive_size=50)
    wide = order_score(0.04, 0.03, size=100, min_incentive_size=50)
    assert tight > wide > 0


def test_single_sided_discount_midpoint_range():
    score = compute_liquidity_reward_score(
        midpoint=0.5,
        order_price=0.49,
        order_size=50,
        side="YES",
        max_incentive_spread=0.04,
        min_incentive_size=50,
    )

    assert score["qualifies_for_reward"] is True
    assert score["single_sided_discount_applied"] is True
    assert score["q_one"] > 0


def test_extreme_midpoint_requires_two_sided_liquidity():
    score = compute_liquidity_reward_score(
        midpoint=0.04,
        order_price=0.039,
        order_size=50,
        side="YES",
        max_incentive_spread=0.04,
        min_incentive_size=50,
    )

    assert score["qualifies_for_reward"] is False
    assert "extreme_midpoint_requires_two_sided_liquidity" in score["blockers"]


def test_min_incentive_size_required():
    score = compute_liquidity_reward_score(
        midpoint=0.5,
        order_price=0.49,
        order_size=49,
        side="YES",
        max_incentive_spread=0.04,
        min_incentive_size=50,
    )

    assert score["qualifies_for_reward"] is False
