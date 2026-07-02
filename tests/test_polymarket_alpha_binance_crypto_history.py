from __future__ import annotations

from src.trading.polymarket_alpha.binance_crypto_history import (
    binance_pair_for_asset,
    fetch_binance_klines,
    verify_high_since_start,
)


def _kline(open_ms: int, high: float):
    return [open_ms, "1", str(high), "1", "1", "1", open_ms + 59_999]


def test_high_since_start_detects_barrier_touch(tmp_path):
    def fetcher(url: str):
        return [_kline(1780876800000, 69000), _kline(1780876860000, 71000)]

    report = verify_high_since_start(
        asset="BTC",
        threshold=70000,
        market_creation_time="2026-06-08T00:00:00Z",
        current_time="2026-06-08T00:03:00Z",
        cache_dir=tmp_path,
        fetcher=fetcher,
    )

    assert report["pair"] == "BTCUSDT"
    assert report["high_since_start_verified"] is True
    assert report["barrier_already_touched"] is True
    assert report["max_high_since_start"] == 71000
    assert report["kline_count"] == 2
    assert report["live_order_path"] is False


def test_high_since_start_no_touch_allows_future_probability(tmp_path):
    report = verify_high_since_start(
        asset="ETH",
        threshold=2500,
        market_creation_time="2026-06-08T00:00:00Z",
        current_time="2026-06-08T00:03:00Z",
        cache_dir=tmp_path,
        fetcher=lambda url: [_kline(1780876800000, 2300), _kline(1780876860000, 2400)],
    )

    assert report["pair"] == "ETHUSDT"
    assert report["high_since_start_verified"] is True
    assert report["barrier_already_touched"] is False
    assert report["max_high_since_start"] == 2400


def test_high_since_start_unverified_blocks_candidate(tmp_path):
    report = verify_high_since_start(
        asset="BTC",
        threshold=70000,
        market_creation_time="2026-06-08T00:00:00Z",
        current_time="2026-06-08T00:03:00Z",
        cache_dir=tmp_path,
        fetcher=lambda url: [],
    )

    assert report["high_since_start_verified"] is False
    assert report["gap_reason"] == "empty_binance_klines"


def test_uses_binance_pair_from_resolution_rule():
    assert binance_pair_for_asset("BTC") == "BTCUSDT"
    assert binance_pair_for_asset("ETH") == "ETHUSDT"


def test_does_not_use_non_binance_source(tmp_path):
    result = fetch_binance_klines(
        pair="BTCUSD",
        start_time="2026-06-08T00:00:00Z",
        end_time="2026-06-08T00:03:00Z",
        cache_dir=tmp_path,
        fetcher=lambda url: [_kline(1780876800000, 71000)],
    )

    assert result["ok"] is False
    assert result["gap_reason"] == "unsupported_binance_pair"


def test_fetch_klines_reuses_covering_cache(tmp_path):
    result = fetch_binance_klines(
        pair="BTCUSDT",
        start_time="2026-06-08T00:00:00Z",
        end_time="2026-06-08T00:05:00Z",
        cache_dir=tmp_path,
        fetcher=lambda url: [
            _kline(1780876800000, 69000),
            _kline(1780876860000, 70000),
            _kline(1780876920000, 71000),
        ],
    )
    assert result["kline_count"] == 3

    subset = fetch_binance_klines(
        pair="BTCUSDT",
        start_time="2026-06-08T00:01:00Z",
        end_time="2026-06-08T00:03:00Z",
        cache_dir=tmp_path,
        fetcher=lambda url: (_ for _ in ()).throw(AssertionError("should use cache")),
    )

    assert subset["request_count"] == 0
    assert subset["cache_coverage"] == "covering_range"
    assert subset["kline_count"] == 2
