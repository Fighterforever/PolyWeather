from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_paper_journal import build_weather_lp_paper_cycle


def test_paper_cycle_separates_reward_from_price_pnl():
    report = build_weather_lp_paper_cycle(
        candidates=[
            {
                "decision": "paper_quote",
                "market_slug": "m",
                "token_id": "t",
                "quote_price": 0.49,
                "quote_size": 50,
                "midpoint": 0.5,
                "spread_from_midpoint": 0.01,
                "max_incentive_spread": 0.04,
                "min_incentive_size": 50,
                "reward_estimate": 0.25,
                "reward_score_at_entry": {"q_min": 12.5, "qualifies_for_reward": True},
            }
        ],
        generated_at="2026-06-29T10:45:00Z",
    )

    assert report["paper_quote_count"] == 1
    assert report["reward_points_proxy"] == 0.25
    assert report["estimated_reward_cents"] is None
    assert report["reward_is_guaranteed"] is False
    assert report["quote_updates"][0]["still_qualifies_for_reward"] is True
    assert report["quotes"][0]["estimated_reward_cents_separate_from_markout"] is True
    assert report["live_order_path"] is False
