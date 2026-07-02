from __future__ import annotations

from src.trading.polymarket_alpha.live_safety import evaluate_live_safety, load_live_safety_config


def _enabled_env() -> dict[str, str]:
    return {
        "POLYWEATHER_ENABLE_LIVE": "true",
        "POLYWEATHER_ENABLE_WEATHER_LP_TINY_LIVE": "true",
        "POLYWEATHER_LIVE_STRATEGY_ALLOWLIST": "weather_lp_reward",
        "POLYWEATHER_ORDER_TYPE_ALLOWLIST": "GTD",
        "POLYWEATHER_ALLOW_TAKER": "false",
        "POLYWEATHER_REQUIRE_RESTING_ONLY": "true",
        "POLYMARKET_PRIVATE_KEY": "secret",
        "POLYMARKET_API_KEY": "secret",
        "POLYMARKET_API_SECRET": "secret",
        "POLYMARKET_PASSPHRASE": "secret",
    }


def test_live_disabled_by_default():
    config = load_live_safety_config({})
    assert config["global_live_enabled"] is False
    report = evaluate_live_safety(order={"strategy_id": "weather_lp_reward", "order_type": "GTD", "price": 0.1}, env={})
    assert "global_live_disabled" in report["safety_blockers"]
    assert report["live_order_path"] is False


def test_requires_global_and_strategy_live_flags():
    env = _enabled_env()
    env["POLYWEATHER_ENABLE_WEATHER_LP_TINY_LIVE"] = "false"
    report = evaluate_live_safety(
        order={"strategy_id": "weather_lp_reward", "order_type": "GTD", "price": 0.1},
        env=env,
        balance_available=True,
        wallet_balance_check_passed=True,
    )
    assert "strategy_live_disabled" in report["safety_blockers"]


def test_only_weather_lp_allowlisted():
    report = evaluate_live_safety(
        order={"strategy_id": "microstructure", "order_type": "GTD", "price": 0.1},
        env=_enabled_env(),
        balance_available=True,
        wallet_balance_check_passed=True,
    )
    assert "strategy_not_allowlisted" in report["safety_blockers"]


def test_rejects_taker_orders():
    report = evaluate_live_safety(
        order={"strategy_id": "weather_lp_reward", "order_type": "GTD", "action": "BUY", "price": 0.5},
        env=_enabled_env(),
        balance_available=True,
        wallet_balance_check_passed=True,
        best_ask=0.5,
    )
    assert "order_would_cross_spread" in report["safety_blockers"]
    assert "taker_not_allowed" in report["safety_blockers"]


def test_rejects_non_gtd_orders():
    for order_type in ("MARKET", "FOK", "FAK"):
        report = evaluate_live_safety(
            order={"strategy_id": "weather_lp_reward", "order_type": order_type, "price": 0.1},
            env=_enabled_env(),
            balance_available=True,
            wallet_balance_check_passed=True,
        )
        assert "order_type_not_allowlisted" in report["safety_blockers"]
        assert "forbidden_order_type" in report["safety_blockers"]


def test_rejects_crossing_order():
    report = evaluate_live_safety(
        order={"strategy_id": "weather_lp_reward", "order_type": "GTD", "action": "BUY", "price": 0.41},
        env=_enabled_env(),
        balance_available=True,
        wallet_balance_check_passed=True,
        best_bid=0.39,
        best_ask=0.40,
    )
    assert "order_would_cross_spread" in report["safety_blockers"]
    assert report["safety_passed"] is False


def test_rejects_cap_exceeded():
    report = evaluate_live_safety(
        order={"strategy_id": "weather_lp_reward", "order_type": "GTD", "price": 0.1},
        env=_enabled_env(),
        balance_available=True,
        wallet_balance_check_passed=True,
        total_capital_usd=26,
    )
    assert "max_total_capital_exceeded" in report["safety_blockers"]


def test_no_credentials_noop():
    env = _enabled_env()
    for key in ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_API_KEY", "POLYMARKET_API_SECRET", "POLYMARKET_PASSPHRASE"):
        env.pop(key, None)
    report = evaluate_live_safety(
        order={"strategy_id": "weather_lp_reward", "order_type": "GTD", "price": 0.1},
        env=env,
        balance_available=True,
        wallet_balance_check_passed=True,
    )
    assert "credentials_missing" in report["safety_blockers"]


def test_selected_quote_allowlist_required():
    report = evaluate_live_safety(
        order={"strategy_id": "weather_lp_reward", "order_type": "GTD", "price": 0.1},
        env=_enabled_env(),
        balance_available=True,
        wallet_balance_check_passed=True,
        selected_quote_allowlisted=False,
    )
    assert "selected_quote_not_allowlisted" in report["safety_blockers"]
