from __future__ import annotations

import json

from src.trading.weather_basket_paper_journal import build_basket_paper_fill, write_basket_paper_fills


def _candidate():
    return {
        "strategy_id": "bucket_family_buy_all_yes",
        "event_slug": "event",
        "station_code": "TEST",
        "target_date": "2026-06-28",
        "settlement_source": "metar",
        "total_cost": 0.6,
        "worst_case_payout": 1.0,
        "edge_cents": 40.0,
        "solver_method": "active_set_bruteforce",
        "outcome_payoff_vector": [
            {"outcome_index": 0, "payout": 1.0},
            {"outcome_index": 1, "payout": 1.0},
            {"outcome_index": 2, "payout": 1.0},
        ],
        "legs": [
            {"market_slug": "m1", "token_id": "t1", "side": "YES", "best_ask": 0.2, "orderbook_snapshot_id": "s1"},
            {"market_slug": "m2", "token_id": "t2", "side": "YES", "best_ask": 0.2, "orderbook_snapshot_id": "s2"},
            {"market_slug": "m3", "token_id": "t3", "side": "YES", "best_ask": 0.2, "orderbook_snapshot_id": "s3"},
        ],
    }


def test_basket_paper_fill_records_all_legs():
    fill = build_basket_paper_fill(_candidate(), created_at="2026-06-28T00:00:00Z")

    assert fill["basket_fill_id"]
    assert len(fill["legs"]) == 3
    assert fill["orderbook_snapshot_ids"] == ["s1", "s2", "s3"]


def test_basket_fill_has_worst_case_payout():
    fill = build_basket_paper_fill(_candidate(), created_at="2026-06-28T00:00:00Z")

    assert fill["total_cost"] == 0.6
    assert fill["worst_case_payout"] == 1.0
    assert fill["edge_cents"] == 40.0
    assert fill["solver_method"] == "active_set_bruteforce"
    assert fill["outcome_payoff_vector"][0]["payout"] == 1.0


def test_basket_fill_not_live_eligible(tmp_path):
    report = write_basket_paper_fills([_candidate()], paper_fill_dir=tmp_path, created_at="2026-06-28T00:00:00Z")

    assert report["basket_paper_fill_count"] == 1
    fill = json.loads((tmp_path / "fills.jsonl").read_text(encoding="utf-8").strip())
    assert fill["paper_only"] is True
    assert fill["counts_for_live_gate"] is False
    assert fill["live_order_path"] is False
    assert (tmp_path / "manifest.jsonl").exists()
