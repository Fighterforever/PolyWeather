from __future__ import annotations

import json

from src.trading.polymarket_alpha.live_safety import evaluate_live_safety
from src.trading.polymarket_alpha.polymarket_live_client import PolymarketLiveClient


def _env() -> dict[str, str]:
    return {
        "POLYWEATHER_ENABLE_LIVE": "true",
        "POLYWEATHER_ENABLE_WEATHER_LP_TINY_LIVE": "true",
        "POLYWEATHER_LIVE_STRATEGY_ALLOWLIST": "weather_lp_reward",
        "POLYMARKET_PRIVATE_KEY": "private-value",
        "POLYMARKET_API_KEY": "api-key",
        "POLYMARKET_API_SECRET": "api-secret",
        "POLYMARKET_PASSPHRASE": "passphrase",
    }


def _order() -> dict:
    return {
        "client_order_id": "c1",
        "strategy_id": "weather_lp_reward",
        "market_slug": "m",
        "token_id": "t",
        "action": "BUY",
        "price": 0.1,
        "size": 20,
        "order_type": "GTD",
        "post_only_or_resting_required": True,
        "capital_at_risk": 2,
    }


class Adapter:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.canceled: list[str] = []

    def get_balance(self):
        return {"balance": 100}

    def list_open_orders(self, strategy_id=None):
        return []

    def place_gtd_limit_order(self, order_request):
        self.orders.append(order_request)
        return {"order_id": "o1"}

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        return {"ok": True}


def test_live_client_noop_without_credentials(tmp_path):
    client = PolymarketLiveClient(env={}, audit_log_path=tmp_path / "orders.jsonl", error_log_path=tmp_path / "errors.jsonl")
    result = client.place_gtd_limit_order(_order())

    assert result["placed"] is False
    assert "credentials_missing" in result["blockers"]


def test_place_gtd_order_requires_safety_pass(tmp_path):
    adapter = Adapter()
    client = PolymarketLiveClient(adapter=adapter, env=_env(), audit_log_path=tmp_path / "orders.jsonl", error_log_path=tmp_path / "errors.jsonl")
    safety = evaluate_live_safety(
        order=_order(),
        env=_env(),
        credentials_present_override=True,
        balance_available=True,
        wallet_balance_check_passed=True,
    )
    result = client.place_gtd_limit_order(_order(), safety_report=safety)

    assert result["placed"] is True
    assert adapter.orders


def test_rejects_market_order(tmp_path):
    client = PolymarketLiveClient(adapter=Adapter(), env=_env(), error_log_path=tmp_path / "errors.jsonl")
    order = {**_order(), "order_type": "MARKET"}
    result = client.place_gtd_limit_order(order)

    assert result["placed"] is False
    assert "order_type_not_gtd" in result["blockers"]


def test_rejects_fok_fak(tmp_path):
    client = PolymarketLiveClient(adapter=Adapter(), env=_env(), error_log_path=tmp_path / "errors.jsonl")
    for order_type in ("FOK", "FAK"):
        result = client.place_gtd_limit_order({**_order(), "order_type": order_type})
        assert result["placed"] is False
        assert "forbidden_order_type" in result["blockers"]


def test_rejects_crossing_buy(tmp_path):
    client = PolymarketLiveClient(adapter=Adapter(), env=_env(), error_log_path=tmp_path / "errors.jsonl")
    result = client.place_gtd_limit_order(_order(), best_ask=0.1)

    assert result["placed"] is False
    assert "order_would_cross_spread" in result["blockers"]


def test_cancel_order_audit_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = PolymarketLiveClient(adapter=Adapter(), env=_env(), audit_log_path=tmp_path / "orders.jsonl", error_log_path=tmp_path / "errors.jsonl")
    result = client.cancel_order("o1")

    assert result["canceled"] is True
    assert (tmp_path / "evidence/weather_lp_rewards/tiny_live_cancellations.jsonl").exists()


def test_no_secret_written_to_artifacts(tmp_path):
    client = PolymarketLiveClient(adapter=Adapter(), env=_env(), audit_log_path=tmp_path / "orders.jsonl", error_log_path=tmp_path / "errors.jsonl")
    result = client.place_gtd_limit_order(_order())
    text = (tmp_path / "orders.jsonl").read_text(encoding="utf-8")

    assert result["placed"] is True
    assert "private-value" not in text
    assert "api-secret" not in text
    assert json.loads(text.splitlines()[0])["client_order_id"] == "c1"
