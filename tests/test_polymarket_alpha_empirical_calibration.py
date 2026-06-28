from __future__ import annotations

from src.trading.polymarket_alpha.empirical_calibration import build_empirical_calibration_report


def test_empirical_calibration_reports_market_miscalibration_groups():
    rows = [
        {
            "category": "crypto",
            "price_mid": 0.2,
            "resolved_payout": 1.0,
            "resolved": True,
            "price_bucket": "0_15_0_35",
            "spread_bucket": "1c_3c",
            "liquidity_bucket": "1k_10k",
            "time_to_close_bucket": "1d_7d",
        },
        {
            "category": "crypto",
            "price_mid": 0.3,
            "resolved_payout": 1.0,
            "resolved": True,
            "price_bucket": "0_15_0_35",
            "spread_bucket": "1c_3c",
            "liquidity_bucket": "1k_10k",
            "time_to_close_bucket": "1d_7d",
        },
    ]

    report = build_empirical_calibration_report(rows, min_sample_count=2)

    assert report["resolved_input_row_count"] == 2
    category_rows = [row for row in report["table"] if row["group_fields"] == ["category"]]
    assert category_rows[0]["meets_min_sample"] is True
    assert category_rows[0]["calibration_error"] > 0
    assert report["live_order_path"] is False
