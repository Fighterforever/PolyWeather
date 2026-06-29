from __future__ import annotations

from src.trading.polymarket_alpha.reward_programs import extract_liquidity_reward_metadata, normalize_reward_spread


def test_liquidity_reward_fields_classified_separately_from_maker_rebate():
    meta = extract_liquidity_reward_metadata({"rewards": {"min_size": 50, "max_spread": 4.5}, "maker_base_fee": 1000})

    assert meta["reward_program_type"] == "liquidity_reward"
    assert meta["min_incentive_size"] == 50
    assert meta["max_incentive_spread"] == 0.045


def test_maker_rebate_only_is_not_liquidity_reward():
    meta = extract_liquidity_reward_metadata({"maker_base_fee": 1000, "taker_base_fee": 1000})

    assert meta["reward_program_type"] == "maker_rebate"
    assert meta["gap_reason"] == "maker_rebate_only_not_liquidity_reward"


def test_normalize_reward_spread_percent_units():
    assert normalize_reward_spread(4.5) == 0.045
    assert normalize_reward_spread(0.04) == 0.04
