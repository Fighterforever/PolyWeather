from __future__ import annotations

from src.trading.polymarket_alpha.polymarket_reward_metadata import audit_reward_allocation_conversion, audit_weather_reward_metadata


class FakeRewardClient:
    def get_gamma_market(self, market_id: str):
        assert market_id == "1"
        return {
            "id": "1",
            "conditionId": "0xabc",
            "clobTokenIds": '["yes","no"]',
            "rewardsMinSize": 50,
            "rewardsMaxSpread": 4.5,
            "feesEnabled": True,
        }

    def get_clob_market_by_condition_id(self, condition_id: str):
        assert condition_id == "0xabc"
        return {
            "condition_id": "0xabc",
            "market_slug": "weather",
            "rewards": {"min_size": 50, "max_spread": 4.5, "dailyRate": 10},
            "tokens": [{"token_id": "yes"}, {"token_id": "no"}],
        }

    def get_clob_market_search(self, condition_id: str):
        assert condition_id == "0xabc"
        return {}


def test_reward_metadata_audit_fetches_gamma_then_clob_rewards():
    report = audit_weather_reward_metadata(
        markets=[{"market_slug": "weather", "market_id": "1", "city": "ankara"}],
        client=FakeRewardClient(),  # type: ignore[arg-type]
    )

    row = report["rows"][0]
    assert report["reward_metadata_available_count"] == 1
    assert row["condition_id"] == "0xabc"
    assert row["source_found"] == "clob_market_full_object"
    assert row["min_incentive_size"] == 50
    assert row["max_incentive_spread"] == 0.045
    assert row["rewards_daily_rate"] == 10
    assert row["reward_program_type"] == "liquidity_reward"
    assert row["live_order_path"] is False


def test_reward_metadata_audit_outputs_no_condition_gap():
    report = audit_weather_reward_metadata(markets=[{"market_slug": "weather"}], fetch=False)

    assert report["reward_metadata_available_count"] == 0
    assert report["rows"][0]["gap_reason"] == "no_condition_id"


def test_reward_allocation_audit_does_not_fake_dollar_conversion():
    report = audit_reward_allocation_conversion(
        reward_metadata_rows=[{"market_slug": "weather", "condition_id": "0xabc", "reward_allocation": None, "raw_field_names_found": ["rewards.rate"]}],
        quote_updates=[{"market_slug": "weather", "cumulative_reward_points_proxy": 12.0}],
    )

    assert report["estimated_reward_cents_available_count"] == 0
    assert report["estimated_reward_cents_proxy"] is None
    assert report["rows"][0]["fields_found"] == ["rewards.rate"]
    assert report["rows"][0]["total_market_score_available"] is False
    assert report["rows"][0]["gap_reason"] == "reward_allocation_unavailable_in_market_objects"
    assert report["live_order_path"] is False
