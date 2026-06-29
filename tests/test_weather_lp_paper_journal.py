from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_paper_journal import build_weather_lp_paper_cycle


def test_paper_cycle_separates_reward_from_price_pnl():
    report = build_weather_lp_paper_cycle(
        candidates=[{"decision": "paper_quote", "market_slug": "m", "token_id": "t", "quote_price": 0.02, "reward_estimate": 0.03}],
        generated_at="2026-06-29T10:45:00Z",
    )

    assert report["paper_quote_count"] == 1
    assert report["estimated_reward_cents"] == 0.03
    assert report["reward_is_guaranteed"] is False
    assert report["quotes"][0]["estimated_reward_cents_separate_from_markout"] is True
    assert report["live_order_path"] is False
