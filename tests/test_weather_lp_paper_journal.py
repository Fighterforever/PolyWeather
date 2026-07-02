from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_paper_journal import (
    build_weather_lp_paper_cycle,
    build_weather_lp_quote_lifecycle_audit,
    build_weather_lp_quote_update_ledger,
)


def test_paper_cycle_separates_reward_from_price_pnl():
    report = build_weather_lp_paper_cycle(
        candidates=[
            {
                "decision": "paper_quote",
                "market_slug": "m",
                "token_id": "t",
                "quote_price": 0.49,
                "quote_size": 50,
                "midpoint": 0.5,
                "spread_from_midpoint": 0.01,
                "max_incentive_spread": 0.04,
                "min_incentive_size": 50,
                "reward_estimate": 0.25,
                "reward_score_at_entry": {"q_min": 12.5, "qualifies_for_reward": True},
            }
        ],
        generated_at="2026-06-29T10:45:00Z",
    )

    assert report["paper_quote_count"] == 1
    assert report["reward_points_proxy"] > 0
    assert report["estimated_reward_cents"] is None
    assert report["reward_is_guaranteed"] is False
    assert report["quote_updates"][0]["still_qualifies_for_reward"] is True
    assert report["quotes"][0]["estimated_reward_cents_separate_from_markout"] is True
    assert report["live_order_path"] is False


def test_quote_update_ledger_tracks_reward_points_and_markout():
    quotes = [
        {
            "quote_id": "q1",
            "market_slug": "m",
            "token_id": "t",
            "city": "ankara",
            "station_code": "LTAC",
            "strategy_variant": "single_sided_low_risk_quote",
            "quote_start_time": "2026-06-29T10:00:00Z",
            "quote_price": 0.49,
            "quote_size": 50,
            "max_incentive_spread": 0.05,
            "min_incentive_size": 50,
            "side": "YES",
            "paper_only": True,
            "live_order_path": False,
        }
    ]
    report = build_weather_lp_quote_update_ledger(
        quotes=quotes,
        reward_markets=[{"market_slug": "m", "token_id": "t", "best_bid": 0.50, "best_ask": 0.52}],
        existing_updates=[],
        generated_at="2026-06-29T10:05:00Z",
    )

    update = report["new_updates"][0]
    assert update["qualifies_for_reward"] is True
    assert update["reward_points_increment_proxy"] > 0
    assert update["cumulative_reward_points_proxy"] == update["reward_points_increment_proxy"]
    assert update["price_markout_from_entry"] == 2.0
    assert update["paper_only"] is True
    assert update["live_order_path"] is False


def test_paper_cycle_reuses_existing_quote_id_and_updates():
    report = build_weather_lp_paper_cycle(
        candidates=[
            {
                "decision": "paper_quote",
                "market_slug": "m",
                "token_id": "t",
                "strategy_variant": "single_sided_low_risk_quote",
                "quote_price": 0.49,
                "quote_size": 50,
                "max_incentive_spread": 0.05,
                "min_incentive_size": 50,
                "reward_score_at_entry": {"q_min": 10, "qualifies_for_reward": True},
            }
        ],
        existing_quotes=[{"quote_id": "stable", "market_slug": "m", "token_id": "t", "strategy_variant": "single_sided_low_risk_quote", "quote_start_time": "2026-06-29T09:00:00Z"}],
        reward_markets=[{"market_slug": "m", "token_id": "t", "best_bid": 0.49, "best_ask": 0.51}],
        generated_at="2026-06-29T09:05:00Z",
    )

    assert report["quotes"][0]["quote_id"] == "stable"
    assert report["quote_update_count"] == 1
    assert report["quote_updates"][0]["quote_id"] == "stable"


def test_paper_cycle_closes_old_quote_when_price_changes():
    report = build_weather_lp_paper_cycle(
        candidates=[
            {
                "decision": "paper_quote",
                "market_slug": "m",
                "token_id": "t",
                "strategy_variant": "single_sided_low_risk_quote",
                "quote_price": 0.50,
                "quote_size": 50,
                "max_incentive_spread": 0.05,
                "min_incentive_size": 50,
                "reward_score_at_entry": {"q_min": 10, "qualifies_for_reward": True},
            }
        ],
        existing_quotes=[
            {
                "quote_id": "old",
                "market_slug": "m",
                "token_id": "t",
                "side": "YES",
                "strategy_variant": "single_sided_low_risk_quote",
                "quote_price": 0.49,
                "quote_size": 50,
                "quote_status": "active",
                "quote_start_time": "2026-06-29T09:00:00Z",
            }
        ],
        generated_at="2026-06-29T09:05:00Z",
    )

    statuses = {row["quote_id"]: row["quote_status"] for row in report["quotes"]}
    assert statuses["old"] == "cancelled"
    assert any(row.get("close_reason") == "quote_price_changed" for row in report["quotes"])
    assert report["active_quote_count"] == 1


def test_quote_lifecycle_audit_explains_missing_horizon_updates():
    report = build_weather_lp_quote_lifecycle_audit(
        quotes=[{"quote_id": "q1", "market_slug": "m", "token_id": "t", "quote_start_time": "2026-06-29T10:00:00Z"}],
        quote_updates=[{"quote_id": "q1", "update_time": "2026-06-29T11:00:00Z"}],
    )

    reasons = {row["reason"]: row["count"] for row in report["missing_horizon_reason_counts"]}
    assert report["unique_quote_id_count"] == 1
    assert report["updates_per_quote_median"] == 1
    assert reasons["horizon_match_too_strict"] >= 1
    assert report["live_order_path"] is False
