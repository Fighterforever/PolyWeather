from __future__ import annotations

from src.trading.polymarket_alpha.crypto_touch_surface import build_crypto_touch_surface_report


def _row(**overrides):
    row = {
        "market_slug": "btc-80k",
        "token_id": "yes-80",
        "side": "YES",
        "asset": "BTC",
        "threshold": 80000.0,
        "target_time": "2026-12-31T00:00:00Z",
        "market_creation_time": "2026-06-08T00:00:00Z",
        "semantics_type": "touch_barrier",
        "probability_semantics": "touch_barrier",
        "high_since_start_verified": True,
        "barrier_already_touched": False,
        "p_yes_touch": 0.5,
        "best_ask": 0.42,
        "spread": 0.04,
        "EV_safe": 0.02,
        "paper_only": True,
        "live_order_path": False,
    }
    row.update(overrides)
    return row


def test_surface_probabilities_monotonic_by_threshold():
    report = build_crypto_touch_surface_report(
        crypto_probability_report={
            "near_misses": [
                _row(threshold=80000, best_ask=0.52, spread=0.04, p_yes_touch=0.55, EV_safe=-0.01),
                _row(market_slug="btc-90k", token_id="yes-90", threshold=90000, best_ask=0.32, spread=0.04, p_yes_touch=0.35, EV_safe=-0.01),
            ]
        }
    )

    rows = report["rows"]
    assert rows[0]["fitted_surface_probability"] >= rows[1]["fitted_surface_probability"]
    assert report["live_order_path"] is False


def test_surface_detects_relative_value_outlier():
    report = build_crypto_touch_surface_report(
        crypto_probability_report={
            "near_misses": [
                _row(threshold=80000, best_ask=0.62, spread=0.04, p_yes_touch=0.60, EV_safe=-0.01),
                _row(market_slug="btc-90k", token_id="yes-90", threshold=90000, best_ask=0.22, spread=0.04, p_yes_touch=0.42, EV_safe=0.02),
                _row(market_slug="btc-100k", token_id="yes-100", threshold=100000, best_ask=0.12, spread=0.04, p_yes_touch=0.22, EV_safe=-0.01),
            ]
        },
        min_edge=0.01,
    )

    candidates = report["top_relative_value_rows"]
    assert report["relative_value_candidate_count"] == 1
    assert candidates[0]["market_slug"] == "btc-90k"
    assert candidates[0]["surface_supports_model_direction"] is True


def test_surface_candidate_requires_ev_and_residual_agreement():
    report = build_crypto_touch_surface_report(
        crypto_probability_report={
            "near_misses": [
                _row(threshold=80000, best_ask=0.40, spread=0.04, p_yes_touch=0.30, EV_safe=0.02),
                _row(market_slug="btc-90k", token_id="yes-90", threshold=90000, best_ask=0.30, spread=0.04, p_yes_touch=0.20, EV_safe=0.02),
            ]
        },
        min_edge=0.01,
    )

    assert report["relative_value_candidate_count"] == 0


def test_surface_residual_supports_candidate():
    report = build_crypto_touch_surface_report(
        crypto_probability_report={
            "near_misses": [
                _row(threshold=80000, best_ask=0.62, spread=0.04, p_yes_touch=0.60, EV_safe=-0.01),
                _row(market_slug="btc-90k", token_id="yes-90", threshold=90000, best_ask=0.22, spread=0.04, p_yes_touch=0.42, EV_safe=0.02),
                _row(market_slug="btc-100k", token_id="yes-100", threshold=100000, best_ask=0.12, spread=0.04, p_yes_touch=0.22, EV_safe=-0.01),
            ]
        },
        formal_fills=[_row(market_slug="btc-90k", token_id="yes-90", threshold=90000)],
        min_edge=0.01,
    )

    supported = [row for row in report["rows"] if row["market_slug"] == "btc-90k"][0]
    assert supported["surface_supports_model_direction"] is True
    assert supported["formal_fill_exists"] is True
    assert report["surface_support_count_for_formal_fills"] == 1


def test_surface_rejects_candidate_against_surface():
    report = build_crypto_touch_surface_report(
        crypto_probability_report={
            "near_misses": [
                _row(threshold=80000, best_ask=0.22, spread=0.04, p_yes_touch=0.20, EV_safe=0.02),
                _row(market_slug="btc-90k", token_id="yes-90", threshold=90000, best_ask=0.30, spread=0.04, p_yes_touch=0.20, EV_safe=0.02),
            ]
        },
        min_edge=0.01,
    )

    assert report["relative_value_candidate_count"] == 0


def test_surface_handles_sparse_group():
    report = build_crypto_touch_surface_report(
        crypto_probability_report={"near_misses": [_row(best_ask=0.22, spread=0.04, p_yes_touch=0.42, EV_safe=0.02)]},
        min_edge=0.01,
    )

    assert report["group_count"] == 1
    assert report["rows"][0]["group_member_count"] == 1
    assert report["rows"][0]["market_yes_mid"] == 0.2


def test_surface_shadow_does_not_count_as_fill():
    report = build_crypto_touch_surface_report(
        crypto_probability_report={
            "near_misses": [
                _row(threshold=80000, best_ask=0.62, spread=0.04, p_yes_touch=0.60, EV_safe=-0.01),
                _row(market_slug="btc-90k", token_id="yes-90", threshold=90000, best_ask=0.22, spread=0.04, p_yes_touch=0.80, EV_safe=0.005),
                _row(market_slug="btc-100k", token_id="yes-100", threshold=100000, best_ask=0.12, spread=0.04, p_yes_touch=0.22, EV_safe=-0.01),
            ]
        },
        min_edge=0.01,
    )

    assert report["relative_value_candidate_count"] == 0
    assert report["surface_near_miss_count"] >= 1
    assert report["shadow_relative_value_rows"][0]["counts_for_live_gate"] is False
