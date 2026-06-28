from __future__ import annotations

from src.trading.polymarket_alpha.active_probability_edge_scanner import scan_active_probability_edges


def _market(category: str = "crypto", **overrides):
    row = {
        "category": category,
        "market_slug": "btc-up",
        "event_slug": "btc",
        "active": True,
        "token_id_by_outcome": {"Yes": "yes-token"},
        "orderbooks": {
            "yes-token": {
                "best_bid": 0.2,
                "best_ask": 0.25,
                "spread": 0.05,
                "ask_depth_usdc_3c": 100,
            }
        },
    }
    row.update(overrides)
    return row


def _model(oos_passed: bool = True):
    return {
        "validate_row_count": 10 if oos_passed else 0,
        "overall": {
            "brier_improvement": 0.1 if oos_passed else None,
            "log_loss_improvement": 0.1 if oos_passed else None,
        },
        "model": {
            "bins": {
                "global|0_15_0_35|missing|3c_7c": {
                    "p": 0.8,
                    "p_lcb": 0.75,
                    "p_ucb": 0.85,
                    "sample_count": 100,
                },
                "global|0_15_0_35|*|*": {
                    "p": 0.8,
                    "p_lcb": 0.75,
                    "p_ucb": 0.85,
                    "sample_count": 100,
                },
            }
        },
    }


def test_active_probability_edge_scanner_writes_candidate_when_oos_model_passes():
    report = scan_active_probability_edges(
        active_markets=[_market()],
        model_report=_model(True),
        min_edge=0.05,
        min_depth=10,
        max_spread=0.1,
        cost=0.01,
    )

    assert report["candidate_count"] == 1
    candidate = report["candidates"][0]
    assert candidate["model_source"] == "global_oos_probability_model"
    assert candidate["side"] == "YES"
    assert candidate["EV_safe"] > 0
    assert candidate["paper_only"] is True
    assert candidate["live_order_path"] is False


def test_active_probability_edge_scanner_blocks_when_global_oos_not_passed():
    report = scan_active_probability_edges(active_markets=[_market()], model_report=_model(False))

    blockers = {row["reason"]: row["count"] for row in report["blocker_counts"]}
    assert blockers["global_oos_model_not_passed"] == 1
    assert report["candidate_count"] == 0


def test_active_probability_edge_scanner_prioritizes_crypto_candidates():
    crypto_candidate = {
        "market_slug": "btc-over-100k",
        "token_id": "yes-token",
        "category": "crypto",
        "model_source": "crypto_lognormal_threshold_model",
        "EV_safe": 0.03,
        "paper_only": True,
        "live_order_path": False,
    }
    report = scan_active_probability_edges(
        active_markets=[_market(category="sports")],
        model_report=_model(False),
        crypto_report={"candidates": [crypto_candidate]},
    )

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["model_source"] == "crypto_lognormal_threshold_model"
    assert report["by_model_source"] == [{"model_source": "crypto_lognormal_threshold_model", "count": 1}]


def test_active_probability_edge_scanner_keeps_politics_neutral_stats_only():
    report = scan_active_probability_edges(
        active_markets=[_market(category="politics")],
        model_report=_model(True),
        min_edge=0.05,
    )

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["neutral_political_stats_only"] is True
