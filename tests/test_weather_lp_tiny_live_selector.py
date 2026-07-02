from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_tiny_live_selector import build_weather_lp_tiny_live_selected_orders


def _selected_report() -> dict:
    return {
        "selected_quote_count": 1,
        "selected_quotes": [
            {
                "rank": 1,
                "market_slug": "highest-temperature-in-ankara-on-july-1-2026-31c",
                "token_id": "token-1",
                "city": "ankara",
                "side": "YES",
                "suggested_quote_price": 0.1,
                "suggested_quote_size": 20,
                "capital_at_risk": 2.0,
                "min_incentive_size": 20,
                "max_incentive_spread": 0.045,
                "distance_from_midpoint": 0.01,
                "visible_reward_share_proxy": 0.1,
                "expected_reward_base": 20,
                "cancel_rules": "cancel_at_hour_boundary",
            }
        ],
    }


def _impact() -> list[dict]:
    return [
        {
            "market_slug": "highest-temperature-in-ankara-on-july-1-2026-31c",
            "token_id": "token-1",
            "quote_would_cross_or_take": False,
            "quote_would_be_resting": True,
            "qualifies_for_reward_after_insert": True,
            "current_best_bid": 0.09,
            "current_best_ask": 0.12,
        }
    ]


def test_tiny_live_selector_builds_only_selected_whitelist_orders():
    report = build_weather_lp_tiny_live_selected_orders(
        selected_quote_report=_selected_report(),
        impact_rows=_impact(),
        kill_switch_checklist={"ready": True},
        env={},
    )

    assert report["selected_order_count"] == 1
    order = report["selected_orders"][0]
    assert order["strategy_id"] == "weather_lp_reward"
    assert order["order_type"] == "GTD"
    assert order["post_only_or_resting_required"] is True
    assert order["live_order_path_candidate"] is True
    assert order["live_order_path"] is False


def test_tiny_live_selector_rejects_crossing_quote():
    impact = _impact()
    impact[0]["quote_would_cross_or_take"] = True
    report = build_weather_lp_tiny_live_selected_orders(
        selected_quote_report=_selected_report(),
        impact_rows=impact,
        kill_switch_checklist={"ready": True},
        env={},
    )

    assert report["selected_order_count"] == 0
    assert any(row["reason"] == "quote_would_cross_or_take" for row in report["blocker_counts"])


def test_tiny_live_selector_respects_total_cap():
    report = build_weather_lp_tiny_live_selected_orders(
        selected_quote_report=_selected_report(),
        impact_rows=_impact(),
        kill_switch_checklist={"ready": True},
        env={"POLYWEATHER_MAX_TOTAL_CAPITAL_USD": "1"},
    )

    assert report["selected_order_count"] == 0
    assert any(row["reason"] == "max_total_capital_exceeded" for row in report["blocker_counts"])


def test_tiny_live_selector_requires_kill_switch():
    report = build_weather_lp_tiny_live_selected_orders(
        selected_quote_report=_selected_report(),
        impact_rows=_impact(),
        kill_switch_checklist={"ready": False},
        env={},
    )

    assert report["selected_order_count"] == 0
    assert any(row["reason"] == "kill_switch_not_ready" for row in report["blocker_counts"])
