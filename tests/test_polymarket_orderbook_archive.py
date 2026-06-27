from __future__ import annotations

import json

from src.trading.polymarket_orderbook_archive import (
    build_orderbook_snapshot_records,
    estimate_taker_effective_price,
    write_orderbook_archive_from_payload,
)


def test_estimate_taker_effective_price_walks_visible_ask_depth():
    order_book = {
        "ask_ladder": [
            {"price": 0.40, "size": 1.0},
            {"price": 0.45, "size": 3.0},
        ],
        "bid_ladder": [
            {"price": 0.38, "size": 2.0},
        ],
    }

    estimate = estimate_taker_effective_price(order_book, side="buy", size=3.0)

    assert estimate["filled_size"] == 3.0
    assert estimate["avg_price"] == 0.43333333
    assert estimate["fully_filled"] is True
    assert estimate["levels_consumed"] == [
        {"price": 0.4, "size": 1.0},
        {"price": 0.45, "size": 2.0},
    ]


def test_build_and_write_orderbook_archive_records(tmp_path):
    payload = {
        "snapshot_id": "poly-1",
        "source": "polymarket_readonly",
        "rows": [
            {
                "market_id": "market-1",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "city": "seoul",
                "market_family": "temperature",
                "bucket_label": ">= 28°C",
                "target_date": "2026-06-27",
                "end_date": "2026-06-27T12:00:00Z",
                "settlement_spec_status": "supported",
                "settlement_spec": {
                    "end_time": "2026-06-27T12:00:00Z",
                    "target_date": "2026-06-27",
                    "station_code": "RKSI",
                    "settlement_source": "metar",
                    "rule_hash": "rule-1",
                },
                "market_bucket": {
                    "bucket_type": "ge",
                    "threshold": 28,
                    "unit": "C",
                },
                "token_id": "yes-token",
                "side": "yes",
                "outcome": "Yes",
                "order_book": {
                    "timestamp": "2026-06-27T10:00:00Z",
                    "best_bid": 0.39,
                    "best_ask": 0.41,
                    "spread": 0.02,
                    "bid_depth_usdc_3c": 20.0,
                    "ask_depth_usdc_3c": 30.0,
                    "bid_ladder": [{"price": 0.39, "size": 10.0}],
                    "ask_ladder": [{"price": 0.41, "size": 10.0}],
                },
            }
        ],
    }

    records = build_orderbook_snapshot_records(
        payload["rows"],
        recorded_at="2026-06-27T10:00:05Z",
        source_snapshot_id=payload["snapshot_id"],
    )
    manifest = write_orderbook_archive_from_payload(
        payload,
        archive_dir=tmp_path,
        recorded_at="2026-06-27T10:00:05Z",
    )

    assert len(records) == 1
    assert records[0]["paper_only"] is True
    assert records[0]["counts_for_live_gate"] is False
    assert records[0]["latency_ms"] == 5000
    assert records[0]["taker_buy_probe"]["avg_price"] == 0.41
    assert records[0]["end_time"] == "2026-06-27T12:00:00Z"
    assert records[0]["end_date"] == "2026-06-27T12:00:00Z"
    assert records[0]["settlement_station_code"] == "RKSI"
    assert records[0]["settlement_source"] == "metar"
    assert records[0]["settlement_rule_hash"] == "rule-1"
    assert records[0]["bucket_type"] == "ge"
    assert records[0]["threshold"] == 28.0
    assert records[0]["market_bucket"]["threshold"] == 28
    assert manifest["written_count"] == 1
    written = json.loads((tmp_path / "orderbook_snapshots.jsonl").read_text().splitlines()[0])
    assert written["schema_version"] == "polyweather_polymarket_orderbook_snapshot.v1"
    assert written["token_id"] == "yes-token"
    assert written["settlement_spec"]["station_code"] == "RKSI"
    saved_manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert saved_manifest["schema_version"] == "polyweather_polymarket_orderbook_archive.v1"
