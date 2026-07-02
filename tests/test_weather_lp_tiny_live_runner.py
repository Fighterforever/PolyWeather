from __future__ import annotations

import json
from argparse import Namespace

from scripts.polymarket_alpha_weather_lp_tiny_live_runner import build_tiny_live_runner_report


def _args(tmp_path):
    selected = tmp_path / "selected.json"
    selected.write_text(
        json.dumps(
            {
                "selected_quote_count": 1,
                "selected_quotes": [
                    {
                        "rank": 1,
                        "market_slug": "m1",
                        "token_id": "t1",
                        "city": "ankara",
                        "side": "YES",
                        "suggested_quote_price": 0.1,
                        "suggested_quote_size": 20,
                        "capital_at_risk": 2,
                        "min_incentive_size": 20,
                        "max_incentive_spread": 0.05,
                        "distance_from_midpoint": 0.01,
                        "visible_reward_share_proxy": 0.1,
                        "expected_reward_base": 10,
                        "cancel_rules": "cancel",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    impact = tmp_path / "impact.jsonl"
    impact.write_text(
        json.dumps(
            {
                "market_slug": "m1",
                "token_id": "t1",
                "quote_would_cross_or_take": False,
                "quote_would_be_resting": True,
                "qualifies_for_reward_after_insert": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    kill = tmp_path / "kill.json"
    kill.write_text(json.dumps({"ready": True}), encoding="utf-8")
    return Namespace(
        generated_at="2026-07-02T00:00:00Z",
        selected_quotes=str(selected),
        impact_rows=str(impact),
        kill_switch=str(kill),
        selected_orders_output=str(tmp_path / "orders_selected.json"),
        selected_orders_csv=str(tmp_path / "orders_selected.csv"),
        summary_output=str(tmp_path / "runner.json"),
        orders_output=str(tmp_path / "orders.jsonl"),
        updates_output=str(tmp_path / "updates.jsonl"),
        cancellations_output=str(tmp_path / "cancels.jsonl"),
        errors_output=str(tmp_path / "errors.jsonl"),
    )


def _enabled_env() -> dict[str, str]:
    return {
        "POLYWEATHER_ENABLE_LIVE": "true",
        "POLYWEATHER_ENABLE_WEATHER_LP_TINY_LIVE": "true",
        "POLYMARKET_PRIVATE_KEY": "private",
        "POLYMARKET_API_KEY": "api",
        "POLYMARKET_API_SECRET": "secret",
        "POLYMARKET_PASSPHRASE": "passphrase",
    }


def test_tiny_live_runner_noop_by_default(tmp_path):
    report = build_tiny_live_runner_report(_args(tmp_path), env={})

    assert report["noop_reason"] == "live_disabled"
    assert report["placed_order_count"] == 0
    assert report["live_order_path"] is False


def test_tiny_live_runner_noop_without_credentials(tmp_path):
    report = build_tiny_live_runner_report(
        _args(tmp_path),
        env={"POLYWEATHER_ENABLE_LIVE": "true", "POLYWEATHER_ENABLE_WEATHER_LP_TINY_LIVE": "true"},
    )

    assert report["noop_reason"] == "credentials_missing"
    assert report["placed_order_count"] == 0


def test_tiny_live_runner_places_only_when_two_flags_enabled(tmp_path):
    env = _enabled_env()
    env["POLYWEATHER_ENABLE_WEATHER_LP_TINY_LIVE"] = "false"
    report = build_tiny_live_runner_report(_args(tmp_path), env=env)

    assert report["noop_reason"] == "strategy_live_disabled"
    assert report["placed_order_count"] == 0


def test_tiny_live_runner_rejects_crossing_order(tmp_path):
    args = _args(tmp_path)
    impact_path = tmp_path / "impact.jsonl"
    impact_path.write_text(
        json.dumps(
            {
                "market_slug": "m1",
                "token_id": "t1",
                "quote_would_cross_or_take": True,
                "quote_would_be_resting": False,
                "qualifies_for_reward_after_insert": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = build_tiny_live_runner_report(args, env=_enabled_env())

    assert report["placed_order_count"] == 0
    assert report["live_order_path"] is False


def test_tiny_live_runner_respects_cap(tmp_path):
    env = _enabled_env()
    env["POLYWEATHER_MAX_TOTAL_CAPITAL_USD"] = "1"
    report = build_tiny_live_runner_report(_args(tmp_path), env=env)

    assert report["selected_order_count"] == 0
    assert report["placed_order_count"] == 0


def test_tiny_live_runner_audit_logs_every_attempt(tmp_path):
    report = build_tiny_live_runner_report(_args(tmp_path), env=_enabled_env())

    assert report["placed_order_count"] == 0
    assert (tmp_path / "errors.jsonl").exists()
    assert "sdk_adapter_missing" in (tmp_path / "errors.jsonl").read_text(encoding="utf-8")


def test_tiny_live_runner_only_weather_lp_strategy(tmp_path):
    args = _args(tmp_path)
    data = json.loads((tmp_path / "selected.json").read_text(encoding="utf-8"))
    data["selected_quotes"][0]["strategy_id"] = "microstructure"
    (tmp_path / "selected.json").write_text(json.dumps(data), encoding="utf-8")
    report = build_tiny_live_runner_report(args, env=_enabled_env())

    assert report["placed_order_count"] == 0
    assert report["live_order_path"] is False
