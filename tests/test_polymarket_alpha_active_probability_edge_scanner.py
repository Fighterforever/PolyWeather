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


def _model(category: str = "crypto"):
    return {
        "model": {
            f"{category}|0_15_0_35": {
                "p": 0.8,
                "p_lcb": 0.75,
                "p_ucb": 0.85,
                "sample_count": 100,
            }
        }
    }


def test_active_probability_edge_scanner_writes_candidate_when_ev_safe_positive():
    report = scan_active_probability_edges(
        active_markets=[_market()],
        model_report=_model(),
        focus_report={"top_focus_categories": [{"category": "crypto", "recommendation": "focus_forward_paper"}]},
        min_edge=0.05,
        min_depth=10,
        max_spread=0.1,
        cost=0.01,
    )

    assert report["candidate_count"] == 1
    candidate = report["candidates"][0]
    assert candidate["side"] == "YES"
    assert candidate["EV_safe"] > 0
    assert candidate["paper_only"] is True
    assert candidate["live_order_path"] is False


def test_active_probability_edge_scanner_blocks_non_focus_and_dust():
    report = scan_active_probability_edges(
        active_markets=[
            _market(category="sports"),
            _market(orderbooks={"dust": {"best_bid": 0.001, "best_ask": 0.004, "spread": 0.003, "ask_depth_usdc_3c": 100}}),
        ],
        model_report=_model(),
        focus_report={"top_focus_categories": [{"category": "crypto", "recommendation": "focus_forward_paper"}]},
    )

    blockers = {row["reason"]: row["count"] for row in report["blocker_counts"]}
    assert blockers["category_not_focus_forward_paper"] == 1
    assert blockers["dust"] == 1
    assert report["candidate_count"] == 0


def test_active_probability_edge_scanner_keeps_politics_neutral_stats_only():
    report = scan_active_probability_edges(
        active_markets=[_market(category="politics")],
        model_report=_model("politics"),
        focus_report={"top_focus_categories": [{"category": "politics", "recommendation": "focus_forward_paper"}]},
        min_edge=0.05,
    )

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["neutral_political_stats_only"] is True
