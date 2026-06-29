from __future__ import annotations

from src.trading.polymarket_alpha.maker_quote_sweep import build_maker_quote_aggressiveness_sweep


def _quote(**overrides):
    row = {
        "quote_id": "q1",
        "market_slug": "m1",
        "token_id": "tok",
        "entry_time": "2026-06-30T00:00:00Z",
        "current_best_bid": 0.40,
        "current_best_ask": 0.50,
        "paper_only": True,
        "live_order_path": False,
    }
    row.update(overrides)
    return row


def test_maker_sweep_reports_no_fill_even_aggressive():
    report = build_maker_quote_aggressiveness_sweep(
        maker_quotes=[_quote()],
        orderbook_snapshots=[
            {"token_id": "tok", "recorded_at": "2026-06-30T00:05:00Z", "best_bid": 0.42, "best_ask": 0.51}
        ],
        modes=["join_best_bid"],
    )

    assert report["original_quote_count"] == 1
    assert report["inferred_fill_count"] == 0
    assert report["mode_recommendation"] == "maker_no_fill_even_aggressive"
    assert report["live_order_path"] is False


def test_maker_sweep_detects_adverse_selection():
    report = build_maker_quote_aggressiveness_sweep(
        maker_quotes=[_quote()],
        orderbook_snapshots=[
            {"token_id": "tok", "recorded_at": "2026-06-30T00:01:00Z", "best_bid": 0.39, "best_ask": 0.44}
        ],
        modes=["inside_spread_50pct"],
    )

    assert report["inferred_fill_count"] == 1
    assert report["mode_recommendation"] == "maker_adverse_selection"
    assert report["inferred_fill_count_by_mode"][0]["mean_markout_cents"] < 0
