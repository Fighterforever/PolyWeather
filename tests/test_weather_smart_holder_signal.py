from __future__ import annotations

from src.trading.polymarket_alpha.weather_smart_holder_signal import build_weather_smart_holder_signal


def test_holder_signal_outputs_gap_when_unavailable():
    report = build_weather_smart_holder_signal(markets=[{"market_slug": "m", "yes_token_id": "t"}], holder_rows=[])

    assert report["signals"][0]["gap_reason"] == "holder_data_unavailable"
    assert report["signals"][0]["can_override_expensive_basket_blocker"] is False
    assert report["live_order_path"] is False


def test_holder_signal_is_supporting_only():
    report = build_weather_smart_holder_signal(
        markets=[{"market_slug": "m", "yes_token_id": "t"}],
        holder_rows=[{"token_id": "t", "wallet": "0xabc", "balance": 100}],
        known_wallets=["0xabc"],
    )

    assert report["smart_holder_signal_count"] == 1
    assert report["signals"][0]["supporting_feature_only"] is True
