from __future__ import annotations

from src.trading.polymarket_alpha.crypto_touch_forward_validation import build_crypto_touch_forward_validation_report


def _fill(**overrides):
    row = {
        "fill_id": "fill-1",
        "market_slug": "eth-touch",
        "token_id": "yes-token",
        "asset": "ETH",
        "threshold": 2500,
        "max_high_since_start": 1800,
        "spread": 0.02,
        "EV_safe": 0.02,
        "paper_only": True,
        "live_order_path": False,
    }
    row.update(overrides)
    return row


def test_crypto_touch_forward_validation_requires_more_formal_fills():
    report = build_crypto_touch_forward_validation_report(
        formal_fills=[_fill()],
        formal_markouts=[
            {"fill_id": "fill-1", "asset": "ETH", "horizon_seconds": 300, "markout_cents": 1.0},
        ],
        near_miss_watch=[],
        near_miss_markouts=[],
    )

    assert report["formal_fills"]["fill_count"] == 1
    assert report["formal_fills"]["mean_5m_markout"] == 1.0
    assert report["verdict"]["status"] == "continue_crypto_touch_sampling_insufficient_forward_fills"
    assert report["live_order_path"] is False


def test_crypto_touch_forward_validation_blocks_lowering_threshold_when_near_miss_negative():
    report = build_crypto_touch_forward_validation_report(
        formal_fills=[_fill()],
        formal_markouts=[],
        near_miss_watch=[{"watch_id": "watch-1", "EV_safe": 0.005}],
        near_miss_markouts=[
            {"watch_id": "watch-1", "asset": "BTC", "horizon": 300, "markout_cents": -1.5},
            {"watch_id": "watch-1", "asset": "BTC", "horizon": 900, "markout_cents": -1.0},
        ],
    )

    assert report["near_miss_watch"]["mean_5m_markout"] == -1.5
    assert report["verdict"]["do_not_lower_threshold"] is True
    assert report["verdict"]["keep_min_edge_threshold"] is True


def test_crypto_touch_forward_validation_waits_for_valid_horizon_markout():
    report = build_crypto_touch_forward_validation_report(
        formal_fills=[_fill()],
        formal_markouts=[
            {"fill_id": "fill-1", "asset": "ETH", "horizon_seconds": 300, "markout_cents": None, "horizon_match_status": "missing_snapshot_for_horizon"},
        ],
        near_miss_watch=[],
        near_miss_markouts=[],
    )

    assert report["horizon_markout_validity"]["5m"]["missing_snapshot_for_horizon"] == 1
    assert report["verdict"]["status"] == "crypto_touch_waiting_for_valid_horizon_markout"


def test_crypto_touch_forward_validation_reduces_priority_when_surface_unsupported_and_markout_negative():
    report = build_crypto_touch_forward_validation_report(
        formal_fills=[_fill(market_slug="eth-touch")],
        formal_markouts=[
            {"fill_id": "fill-1", "market_slug": "eth-touch", "asset": "ETH", "horizon_seconds": 300, "markout_cents": -1.0, "horizon_match_status": "exact"},
        ],
        near_miss_watch=[],
        near_miss_markouts=[],
        surface_report={"rows": [{"market_slug": "eth-touch", "surface_supports_model_direction": False}]},
        sensitivity_report={"sensitivity_fragile_count": 2},
    )

    assert report["surface_support_count"] == 0
    assert report["sensitivity_fragile_count"] == 0
    assert report["verdict"]["status"] == "shadow_only_pending_recalibration"
    assert report["verdict"]["do_not_create_new_formal_fills"] is True


def test_crypto_touch_forward_validation_reduces_priority_when_fragile_and_markout_negative():
    report = build_crypto_touch_forward_validation_report(
        formal_fills=[_fill(market_slug="eth-touch", token_id="yes-token", side="YES")],
        formal_markouts=[
            {"fill_id": "fill-1", "market_slug": "eth-touch", "token_id": "yes-token", "asset": "ETH", "horizon_seconds": 3600, "markout_cents": -1.0, "horizon_match_status": "exact"},
        ],
        near_miss_watch=[],
        near_miss_markouts=[],
        surface_report={"rows": [{"market_slug": "eth-touch", "token_id": "yes-token", "side": "YES", "surface_supports_model_direction": True}]},
        sensitivity_report={"rows": [{"market_slug": "eth-touch", "token_id": "yes-token", "side": "YES", "sensitivity_fragile": True}]},
    )

    assert report["surface_support_count"] == 1
    assert report["sensitivity_fragile_count"] == 1
    assert report["verdict"]["status"] == "shadow_only_pending_recalibration"
    assert report["verdict"]["do_not_create_new_formal_fills"] is True
