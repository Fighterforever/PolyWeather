from __future__ import annotations

from src.trading.weather_bucket_family_arbitrage import build_bucket_family_arbitrage_report


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
        _bucket("m-le24", "le", 24, yes=0.2, no=0.4),
        _bucket("m-eq25", "eq", 25, yes=0.2, no=0.4),
        _bucket("m-ge26", "ge", 26, yes=0.2, no=0.4),
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
                "buckets": buckets,
            }
        ]
    }


def _candidate(report, strategy_id):
    return [row for row in report["candidates"] if row["strategy_id"] == strategy_id]


def test_buy_all_yes_partition_positive_edge():
    report = build_bucket_family_arbitrage_report(_catalog(), min_edge_cents=1.0, min_leg_depth=1.0)

    rows = _candidate(report, "bucket_family_buy_all_yes")
    assert len(rows) == 1
    assert rows[0]["total_cost"] == 0.6
    assert rows[0]["worst_case_payout"] == 1.0
    assert rows[0]["edge_cents"] == 40.0


def test_buy_all_no_partition_positive_edge():
    report = build_bucket_family_arbitrage_report(_catalog(), min_edge_cents=1.0, min_leg_depth=1.0)

    rows = _candidate(report, "bucket_family_buy_all_no")
    assert len(rows) == 1
    assert rows[0]["total_cost"] == 1.2
    assert rows[0]["worst_case_payout"] == 2.0
    assert rows[0]["edge_cents"] == 80.0


def test_incomplete_partition_not_candidate():
    report = build_bucket_family_arbitrage_report(_catalog(partition=False), min_edge_cents=1.0, min_leg_depth=1.0)

    assert report["buy_all_yes_candidate_count"] == 0
    assert any(item["blocker"] == "incomplete_partition" for item in report["no_candidate_blocker_counts"])


def test_missing_ask_not_candidate():
    buckets = [
        _bucket("m-le24", "le", 24, yes=None, no=0.4),
        _bucket("m-eq25", "eq", 25, yes=0.2, no=0.4),
        _bucket("m-ge26", "ge", 26, yes=0.2, no=0.4),
    ]

    report = build_bucket_family_arbitrage_report(_catalog(buckets=buckets), min_edge_cents=1.0, min_leg_depth=1.0)

    assert report["buy_all_yes_candidate_count"] == 0
    assert any(item["blocker"] == "missing_ask_or_token" for item in report["no_candidate_blocker_counts"])


def test_depth_limit_applied():
    buckets = [
        _bucket("m-le24", "le", 24, yes=0.2, no=0.4, depth=0.5),
        _bucket("m-eq25", "eq", 25, yes=0.2, no=0.4, depth=5.0),
        _bucket("m-ge26", "ge", 26, yes=0.2, no=0.4, depth=5.0),
    ]

    report = build_bucket_family_arbitrage_report(_catalog(buckets=buckets), min_edge_cents=1.0, min_leg_depth=1.0)

    assert report["candidate_count"] == 0
    assert any(item["blocker"] == "depth_below_min" for item in report["no_candidate_blocker_counts"])


def test_basket_arbitrage_not_live_eligible():
    report = build_bucket_family_arbitrage_report(_catalog(), min_edge_cents=1.0, min_leg_depth=1.0)
    candidate = report["candidates"][0]

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["live_order_path"] is False
    assert candidate["live_order_path"] is False
    assert candidate["counts_for_live_gate"] is False


def test_ge_monotonic_pair_positive_edge():
    catalog = _catalog(
        partition=False,
        buckets=[
            _bucket("m-ge30", "ge", 30, yes=0.3, no=0.7),
            _bucket("m-ge32", "ge", 32, yes=0.2, no=0.3),
        ],
    )

    report = build_bucket_family_arbitrage_report(catalog, min_edge_cents=1.0, min_leg_depth=1.0)

    rows = _candidate(report, "monotonic_threshold_pair")
    assert len(rows) == 1
    assert rows[0]["pair_type"] == "ge"
    assert rows[0]["superset_market_slug"] == "m-ge30"
    assert rows[0]["subset_market_slug"] == "m-ge32"
    assert rows[0]["edge_cents"] == 40.0


def test_le_monotonic_pair_positive_edge():
    catalog = _catalog(
        partition=False,
        buckets=[
            _bucket("m-le24", "le", 24, yes=0.2, no=0.3),
            _bucket("m-le26", "le", 26, yes=0.3, no=0.7),
        ],
    )

    report = build_bucket_family_arbitrage_report(catalog, min_edge_cents=1.0, min_leg_depth=1.0)

    rows = _candidate(report, "monotonic_threshold_pair")
    assert len(rows) == 1
    assert rows[0]["pair_type"] == "le"
    assert rows[0]["superset_market_slug"] == "m-le26"
    assert rows[0]["subset_market_slug"] == "m-le24"
    assert rows[0]["edge_cents"] == 40.0


def test_monotonic_pair_no_edge_when_cost_above_payout():
    catalog = _catalog(
        partition=False,
        buckets=[
            _bucket("m-ge30", "ge", 30, yes=0.7, no=0.3),
            _bucket("m-ge32", "ge", 32, yes=0.2, no=0.4),
        ],
    )

    report = build_bucket_family_arbitrage_report(catalog, min_edge_cents=1.0, min_leg_depth=1.0)

    assert report["monotonic_pair_candidate_count"] == 0


def test_monotonic_pair_requires_direct_no_ask():
    catalog = _catalog(
        partition=False,
        buckets=[
            _bucket("m-ge30", "ge", 30, yes=0.3, no=0.7),
            _bucket("m-ge32", "ge", 32, yes=0.2, no=None),
        ],
    )

    report = build_bucket_family_arbitrage_report(catalog, min_edge_cents=1.0, min_leg_depth=1.0)

    assert report["monotonic_pair_candidate_count"] == 0
    assert any(item["blocker"] == "missing_ask_or_token" for item in report["no_candidate_blocker_counts"])
