from __future__ import annotations

from src.trading.polymarket_alpha.crypto_touch_historical_calibration import build_crypto_touch_calibration_report


def _kline(open_ms: int, high: float, close: float):
    return [open_ms, "1", str(high), "1", str(close), "1", open_ms + 59_999]


def _closed_market(**overrides):
    row = {
        "market_slug": "will-bitcoin-reach-70000-by-december-31-2026",
        "title": "Will Bitcoin reach $70,000 by December 31, 2026?",
        "question": "Will Bitcoin reach $70,000 by December 31, 2026?",
        "description": "Resolves Yes if any Binance 1 minute candle High is at least $70,000 after market creation.",
        "category": "crypto",
        "createdAt": "2026-06-08T00:00:00Z",
        "end_time": "2027-01-01T05:00:00Z",
        "closed": True,
        "resolved": True,
    }
    row.update(overrides)
    return row


def test_crypto_touch_calibration_builds_no_lookahead_rows(tmp_path):
    report = build_crypto_touch_calibration_report(
        closed_markets=[_closed_market()],
        decision_snapshots=[
            {
                "market_slug": "will-bitcoin-reach-70000-by-december-31-2026",
                "outcome_label": "Yes",
                "decision_time": "2026-06-09T00:00:00Z",
                "price_mid": 0.25,
                "resolved_payout": 1.0,
            }
        ],
        cache_dir=tmp_path,
        kline_fetcher=lambda url: [_kline(1780876800000, 65000, 64000), _kline(1780963140000, 66000, 65000)],
    )

    assert report["sample_count"] == 1
    assert report["no_lookahead_violation_count"] == 0
    assert report["brier_model"] is not None
    assert report["brier_market"] is not None
    assert report["rows"][0]["no_lookahead"] is True
    assert report["live_order_path"] is False


def test_crypto_touch_calibration_rejects_after_end_snapshot(tmp_path):
    report = build_crypto_touch_calibration_report(
        closed_markets=[_closed_market(end_time="2026-06-09T00:00:00Z")],
        decision_snapshots=[
            {
                "market_slug": "will-bitcoin-reach-70000-by-december-31-2026",
                "outcome_label": "Yes",
                "decision_time": "2026-06-10T00:00:00Z",
                "price_mid": 0.25,
                "resolved_payout": 1.0,
            }
        ],
        cache_dir=tmp_path,
        kline_fetcher=lambda url: [_kline(1780876800000, 65000, 64000)],
    )

    assert report["sample_count"] == 0
    assert report["no_lookahead_violation_count"] == 1


def test_crypto_touch_calibration_reports_missing_samples():
    report = build_crypto_touch_calibration_report(closed_markets=[_closed_market()], decision_snapshots=[])

    assert report["sample_count"] == 0
    assert report["status"] == "insufficient_crypto_touch_calibration_samples"
    assert report["gap_reason_counts"][0]["reason"] == "missing_decision_snapshots"
