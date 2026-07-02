from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Protocol

from src.trading.polymarket_alpha.live_safety import credentials_present, evaluate_live_safety


SCHEMA_VERSION = "polyweather_polymarket_alpha_polymarket_live_client.v1"
STRATEGY_ID = "weather_lp_reward"
REQUIRED_CREDENTIAL_KEYS = (
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_PASSPHRASE",
)
OPTIONAL_CREDENTIAL_KEYS = ("POLYMARKET_FUNDER_ADDRESS", "POLYMARKET_SIGNATURE_TYPE")
FORBIDDEN_ORDER_TYPES = {"MARKET", "FOK", "FAK", "IOC"}


class PolymarketLiveAdapter(Protocol):
    def get_balance(self) -> Any: ...

    def list_open_orders(self, strategy_id: str | None = None) -> Any: ...

    def place_gtd_limit_order(self, order_request: Dict[str, Any]) -> Any: ...

    def cancel_order(self, order_id: str) -> Any: ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_jsonl(path: str | Path, row: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(secret in lower for secret in ("private", "secret", "passphrase", "api_key", "apikey")):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def validate_credentials_present(env: Mapping[str, str] | None = None) -> Dict[str, Any]:
    env = env or {}
    missing = [key for key in REQUIRED_CREDENTIAL_KEYS if not str(env.get(key) or "").strip()]
    optional_present = [key for key in OPTIONAL_CREDENTIAL_KEYS if str(env.get(key) or "").strip()]
    return {
        "schema_version": f"{SCHEMA_VERSION}.credentials.v1",
        "credentials_present": not missing,
        "missing_credential_keys": missing,
        "optional_credential_keys_present": optional_present,
        "secret_values_written": False,
        "live_order_path": False,
    }


class PolymarketLiveClient:
    """Tiny isolated wrapper for the Weather LP reward audit path.

    The wrapper only accepts GTD resting limit requests for the weather LP reward
    strategy. It can be wired to an SDK adapter by an operator, but it is a safe
    no-op without one.
    """

    def __init__(
        self,
        *,
        adapter: PolymarketLiveAdapter | None = None,
        env: Mapping[str, str] | None = None,
        audit_log_path: str | Path = "evidence/weather_lp_rewards/tiny_live_orders.jsonl",
        error_log_path: str | Path = "evidence/weather_lp_rewards/tiny_live_errors.jsonl",
    ) -> None:
        self.adapter = adapter
        self.env = env or {}
        self.audit_log_path = Path(audit_log_path)
        self.error_log_path = Path(error_log_path)

    def validate_credentials_present(self) -> Dict[str, Any]:
        return validate_credentials_present(self.env)

    def get_balance(self) -> Dict[str, Any]:
        if not credentials_present(self.env):
            return {"balance_available": False, "wallet_balance_check_passed": False, "reason": "credentials_missing"}
        if self.adapter is None:
            return {"balance_available": False, "wallet_balance_check_passed": False, "reason": "sdk_adapter_missing"}
        try:
            raw = self.adapter.get_balance()
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            return {"balance_available": False, "wallet_balance_check_passed": False, "reason": type(exc).__name__}
        balance = _safe_float(raw.get("balance") if isinstance(raw, dict) else raw)
        return {
            "balance_available": balance is not None,
            "wallet_balance_check_passed": bool(balance is not None and balance >= 0.0),
            "balance": balance,
        }

    def list_open_orders(self, strategy_id: str | None = STRATEGY_ID) -> list[Dict[str, Any]]:
        if self.adapter is None or not credentials_present(self.env):
            return []
        try:
            raw = self.adapter.list_open_orders(strategy_id=strategy_id)
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            _append_jsonl(self.error_log_path, {"generated_at": utc_now_iso(), "error": type(exc).__name__, "operation": "list_open_orders"})
            return []
        if isinstance(raw, list):
            return [row for row in raw if isinstance(row, dict)]
        return []

    def place_gtd_limit_order(
        self,
        order_request: Dict[str, Any],
        *,
        safety_report: Dict[str, Any] | None = None,
        best_bid: float | None = None,
        best_ask: float | None = None,
        total_capital_usd: float | None = None,
        per_market_capital_usd: float | None = None,
        per_city_capital_usd: float | None = None,
        open_order_count: int = 0,
        selected_quote_allowlisted: bool = True,
        reward_qualified: bool = True,
    ) -> Dict[str, Any]:
        request = dict(order_request or {})
        generated_at = utc_now_iso()
        order_type = str(request.get("order_type") or "").upper()
        strategy_id = str(request.get("strategy_id") or "")
        blockers: list[str] = []
        if strategy_id != STRATEGY_ID:
            blockers.append("strategy_not_weather_lp_reward")
        if order_type != "GTD":
            blockers.append("order_type_not_gtd")
        if order_type in FORBIDDEN_ORDER_TYPES:
            blockers.append("forbidden_order_type")
        if request.get("post_only_or_resting_required") is not True:
            blockers.append("resting_required_missing")
        if safety_report is None:
            balance = self.get_balance()
            safety_report = evaluate_live_safety(
                order=request,
                env=self.env,
                credentials_present_override=credentials_present(self.env),
                balance_available=bool(balance.get("balance_available")),
                wallet_balance_check_passed=bool(balance.get("wallet_balance_check_passed")),
                open_order_count=open_order_count,
                total_capital_usd=total_capital_usd if total_capital_usd is not None else float(request.get("capital_at_risk") or 0.0),
                per_market_capital_usd=per_market_capital_usd if per_market_capital_usd is not None else float(request.get("capital_at_risk") or 0.0),
                per_city_capital_usd=per_city_capital_usd if per_city_capital_usd is not None else float(request.get("capital_at_risk") or 0.0),
                selected_quote_allowlisted=selected_quote_allowlisted,
                reward_qualified=reward_qualified,
                best_bid=best_bid,
                best_ask=best_ask,
            )
        if not safety_report.get("safety_passed"):
            blockers.extend(safety_report.get("safety_blockers") or ["safety_gate_failed"])
        if self.adapter is None:
            blockers.append("sdk_adapter_missing")
        result: Dict[str, Any]
        if blockers:
            result = {
                "schema_version": f"{SCHEMA_VERSION}.attempt.v1",
                "generated_at": generated_at,
                "operation": "place_gtd_limit_order",
                "accepted": False,
                "placed": False,
                "client_order_id": request.get("client_order_id"),
                "strategy_id": strategy_id,
                "market_slug": request.get("market_slug"),
                "token_id": request.get("token_id"),
                "blockers": list(dict.fromkeys(blockers)),
                "safety_report": safety_report,
                "live_order_path": False,
            }
            _append_jsonl(self.error_log_path, _sanitize(result))
            return result
        try:
            adapter_result = self.adapter.place_gtd_limit_order(request)  # type: ignore[union-attr]
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            result = {
                "schema_version": f"{SCHEMA_VERSION}.attempt.v1",
                "generated_at": generated_at,
                "operation": "place_gtd_limit_order",
                "accepted": False,
                "placed": False,
                "client_order_id": request.get("client_order_id"),
                "strategy_id": strategy_id,
                "market_slug": request.get("market_slug"),
                "token_id": request.get("token_id"),
                "blockers": [type(exc).__name__],
                "live_order_path": False,
            }
            _append_jsonl(self.error_log_path, _sanitize(result))
            return result
        result = {
            "schema_version": f"{SCHEMA_VERSION}.attempt.v1",
            "generated_at": generated_at,
            "operation": "place_gtd_limit_order",
            "accepted": True,
            "placed": True,
            "client_order_id": request.get("client_order_id"),
            "strategy_id": strategy_id,
            "market_slug": request.get("market_slug"),
            "token_id": request.get("token_id"),
            "adapter_result": _sanitize(adapter_result),
            "live_order_path": True,
        }
        _append_jsonl(self.audit_log_path, _sanitize({**request, **result}))
        return result

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        generated_at = utc_now_iso()
        if not order_id:
            result = {"generated_at": generated_at, "operation": "cancel_order", "canceled": False, "reason": "missing_order_id", "live_order_path": False}
            _append_jsonl(self.error_log_path, result)
            return result
        if self.adapter is None or not credentials_present(self.env):
            result = {"generated_at": generated_at, "operation": "cancel_order", "order_id": order_id, "canceled": False, "reason": "credentials_or_adapter_missing", "live_order_path": False}
            _append_jsonl("evidence/weather_lp_rewards/tiny_live_cancellations.jsonl", result)
            return result
        try:
            raw = self.adapter.cancel_order(order_id)
            result = {"generated_at": generated_at, "operation": "cancel_order", "order_id": order_id, "canceled": True, "adapter_result": _sanitize(raw), "live_order_path": True}
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            result = {"generated_at": generated_at, "operation": "cancel_order", "order_id": order_id, "canceled": False, "reason": type(exc).__name__, "live_order_path": False}
        _append_jsonl("evidence/weather_lp_rewards/tiny_live_cancellations.jsonl", result)
        return result

    def cancel_weather_lp_orders(self) -> list[Dict[str, Any]]:
        return self.cancel_all_strategy_orders(STRATEGY_ID)

    def cancel_all_strategy_orders(self, strategy_id: str = STRATEGY_ID) -> list[Dict[str, Any]]:
        if strategy_id != STRATEGY_ID:
            result = {"generated_at": utc_now_iso(), "operation": "cancel_all_strategy_orders", "strategy_id": strategy_id, "canceled": False, "reason": "strategy_not_weather_lp_reward", "live_order_path": False}
            _append_jsonl(self.error_log_path, result)
            return [result]
        orders = self.list_open_orders(strategy_id=strategy_id)
        return [self.cancel_order(str(order.get("order_id") or order.get("id") or "")) for order in orders]


def sanitize_audit_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _sanitize(payload)


__all__ = [
    "SCHEMA_VERSION",
    "STRATEGY_ID",
    "PolymarketLiveClient",
    "sanitize_audit_payload",
    "utc_now_iso",
    "validate_credentials_present",
]
