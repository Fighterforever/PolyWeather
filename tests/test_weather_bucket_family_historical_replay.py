from __future__ import annotations

from src.trading.weather_bucket_family_historical_replay import build_bucket_family_historical_replay


def _row(market_slug, bucket_type, threshold, side, price):
    return {
        "market_family": "temperature",
        "event_slug": "event",
        "market_slug": market_slug,
        "city": "Test City",
        "station_code": "TEST",
        "settlement_source": "metar",
        "timezone": "UTC",
        "target_date": "2026-06-28",
        "bucket_type": bucket_type,
        "threshold": threshold,
        "side": side,
        "token_id": f"{market_slug}-{side}",
        "price": price,
        "settlement_spec_status": "supported",
    }


def _closed_rows():
    rows = []
    for market_slug, bucket_type, threshold in [
        ("m-le24", "le", 24),
        ("m-eq25", "eq", 25),
        ("m-ge26", "ge", 26),
    ]:
        rows.append(_row(market_slug, bucket_type, threshold, "yes", 0.2))
        rows.append(_row(market_slug, bucket_type, threshold, "no", 0.4))
    return rows


def test_closed_market_basket_replay_is_approximate_without_depth():
    report = build_bucket_family_historical_replay(_closed_rows(), min_edge_cents=1.0)

    assert report["family_count"] == 1
    assert report["replayable_family_count"] == 1
    assert report["approximate_edge_candidate_count"] > 0
    assert report["executable_depth_available_count"] == 0
    assert report["can_count_as_real_pnl"] is False
    assert report["missing_price_or_depth_count"] > 0


def test_closed_market_basket_replay_summarizes_by_station_and_date():
    report = build_bucket_family_historical_replay(_closed_rows(), min_edge_cents=1.0)

    assert report["by_station"][0]["station_code"] == "TEST"
    assert report["by_event_date"][0]["target_date"] == "2026-06-28"
    assert report["live_order_path"] is False
