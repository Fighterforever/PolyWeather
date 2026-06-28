from __future__ import annotations

from src.trading.weather_bucket_family_lp_arbitrage import build_bucket_family_lp_arbitrage_report


def _bucket(market_slug, bucket_type, threshold, *, yes=0.2, no=0.4, depth=5.0):
    return {
        "market_slug": market_slug,
        "bucket_type": bucket_type,
        "threshold": threshold,
        "yes_token_id": f"{market_slug}-yes",
        "no_token_id": f"{market_slug}-no",
        "yes_best_ask": yes,
        "no_best_ask": no,
        "yes_ask_depth": depth,
        "no_ask_depth": depth,
        "yes_spread": 0.01,
        "no_spread": 0.01,
        "yes_orderbook_snapshot_id": f"{market_slug}-yes-snap",
        "no_orderbook_snapshot_id": f"{market_slug}-no-snap",
    }


def _catalog(*, partition=True, buckets=None):
    buckets = buckets or [
        _bucket("m-le24", "le", 24, yes=0.2, no=0.9),
        _bucket("m-eq25", "eq", 25, yes=0.2, no=0.9),
        _bucket("m-ge26", "ge", 26, yes=0.2, no=0.9),
    ]
    return {
        "families": [
            {
                "event_slug": "event",
                "station_code": "TEST",
                "target_date": "2026-06-28",
                "settlement_source": "metar",
                "bucket_count": len(buckets),
                "is_partition_candidate": partition,
                "partition_gap_reasons": [] if partition else ["missing_or_multiple_upper_tail"],
                "buckets": buckets,
            }
        ]
    }


def test_lp_finds_buy_all_yes_when_positive():
    report = build_bucket_family_lp_arbitrage_report(_catalog(), min_edge_cents=1.0, min_leg_depth=1.0)

    candidate = report["candidates"][0]
    assert report["lp_candidate_count"] == 1
    assert candidate["strategy_id"] == "bucket_family_payoff_matrix_arbitrage"
    assert candidate["edge_cents"] == 40.0
    assert candidate["leg_count"] == 3
    assert {leg["side"] for leg in candidate["legs"]} == {"YES"}
    assert min(row["payout"] for row in candidate["outcome_payoff_vector"]) >= 1.0


def test_lp_finds_buy_all_no_when_positive():
    catalog = _catalog(
        buckets=[
            _bucket("m-le24", "le", 24, yes=0.9, no=0.2),
            _bucket("m-eq25", "eq", 25, yes=0.9, no=0.2),
            _bucket("m-ge26", "ge", 26, yes=0.9, no=0.2),
        ]
    )

    report = build_bucket_family_lp_arbitrage_report(catalog, min_edge_cents=1.0, min_leg_depth=1.0)

    candidate = report["candidates"][0]
    assert report["lp_candidate_count"] == 1
    assert candidate["edge_cents"] == 70.0
    assert candidate["leg_count"] == 3
    assert {leg["side"] for leg in candidate["legs"]} == {"NO"}
    assert {round(leg["quantity"], 6) for leg in candidate["legs"]} == {0.5}


def test_lp_finds_partial_combo_not_found_by_full_basket():
    catalog = _catalog(
        buckets=[
            _bucket("m-le24", "le", 24, yes=0.9, no=0.95, depth=0.1),
            _bucket("m-eq25", "eq", 25, yes=0.9, no=0.35, depth=5.0),
            _bucket("m-ge26", "ge", 26, yes=0.9, no=0.35, depth=5.0),
        ]
    )

    report = build_bucket_family_lp_arbitrage_report(catalog, min_edge_cents=1.0, min_leg_depth=1.0)

    candidate = report["candidates"][0]
    assert report["lp_candidate_count"] == 1
    assert candidate["leg_count"] == 2
    assert candidate["edge_cents"] == 30.0
    assert {leg["market_slug"] for leg in candidate["legs"]} == {"m-eq25", "m-ge26"}
    assert {leg["side"] for leg in candidate["legs"]} == {"NO"}
    assert candidate["missing_leg_count"] > 0


def test_lp_rejects_incomplete_partition():
    report = build_bucket_family_lp_arbitrage_report(_catalog(partition=False), min_edge_cents=1.0, min_leg_depth=1.0)

    assert report["lp_candidate_count"] == 0
    assert report["family_rows"][0]["no_candidate_reason"] == "incomplete_partition"


def test_lp_respects_depth_constraints():
    catalog = _catalog(
        buckets=[
            _bucket("m-le24", "le", 24, yes=0.2, no=0.9, depth=0.5),
            _bucket("m-eq25", "eq", 25, yes=0.2, no=0.9, depth=0.5),
            _bucket("m-ge26", "ge", 26, yes=0.2, no=0.9, depth=0.5),
        ]
    )

    report = build_bucket_family_lp_arbitrage_report(catalog, min_edge_cents=1.0, min_leg_depth=1.0)

    assert report["lp_candidate_count"] == 0
    assert report["family_rows"][0]["no_candidate_reason"] == "no_tradable_legs"


def test_lp_candidate_not_live_eligible():
    report = build_bucket_family_lp_arbitrage_report(_catalog(), min_edge_cents=1.0, min_leg_depth=1.0)
    candidate = report["candidates"][0]

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["live_order_path"] is False
    assert candidate["paper_only"] is True
    assert candidate["counts_for_live_gate"] is False
    assert candidate["live_order_path"] is False
