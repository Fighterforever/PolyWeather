from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hours_to_expiry(row: Dict[str, Any], *, now: Optional[str] = None) -> Optional[float]:
    end_dt = _parse_utc(row.get("end_date"))
    if end_dt is None:
        return None
    now_dt = _parse_utc(now) if now else datetime.now(timezone.utc)
    if now_dt is None:
        return None
    return (end_dt - now_dt).total_seconds() / 3600.0


@dataclass(frozen=True)
class StrategyAssignment:
    strategy_id: str
    execution_style: str
    why_now: str
    live_eligible: bool
    counts_for_live_gate: bool
    risk_caps: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _risk_caps(row: Dict[str, Any], *, strategy_id: str) -> Dict[str, Any]:
    ev_safe = _safe_float(row.get("ev_safe"))
    price = _safe_float(row.get("q_effective") or row.get("price"))
    base_notional = 10.0
    if ev_safe is not None:
        base_notional = max(1.0, min(25.0, 100.0 * max(0.0, ev_safe)))
    if price is not None and price > 0.75:
        base_notional = min(base_notional, 5.0)
    return {
        "schema_version": "polyweather_weather_strategy_risk_caps.v1",
        "max_position_usdc": round(base_notional, 2),
        "max_market_usdc": round(base_notional, 2),
        "max_city_usdc": 50.0,
        "max_station_usdc": 50.0,
        "max_strategy_usdc": 100.0,
        "kill_switch_daily_loss_usdc": 10.0,
        "strategy_id": strategy_id,
    }


def assign_weather_strategy(
    row: Dict[str, Any],
    *,
    bucket_type: Optional[str] = None,
    now: Optional[str] = None,
) -> StrategyAssignment:
    bucket = str(bucket_type or row.get("bucket_type") or "").strip().lower()
    price = _safe_float(row.get("q_effective") or row.get("price") or row.get("market_probability"))
    spread = _safe_float(row.get("spread"))
    hours_to_expiry = _hours_to_expiry(row, now=now)

    if bucket == "eq":
        strategy_id = "eq_exact_shadow"
        return StrategyAssignment(
            strategy_id=strategy_id,
            execution_style="shadow_only",
            why_now="exact temperature bucket is used for calibration only",
            live_eligible=False,
            counts_for_live_gate=False,
            risk_caps=_risk_caps(row, strategy_id=strategy_id),
        )

    if hours_to_expiry is not None and 0 <= hours_to_expiry <= 6:
        strategy_id = "near_lock"
        return StrategyAssignment(
            strategy_id=strategy_id,
            execution_style="taker_or_maker_after_observation_check",
            why_now=f"market is within {hours_to_expiry:.2f} hours of end time",
            live_eligible=True,
            counts_for_live_gate=True,
            risk_caps=_risk_caps(row, strategy_id=strategy_id),
        )

    if bucket in {"le", "ge"}:
        strategy_id = "tail_threshold"
        return StrategyAssignment(
            strategy_id=strategy_id,
            execution_style="taker_depth_checked",
            why_now="threshold bucket can be compared against calibrated station CDF",
            live_eligible=True,
            counts_for_live_gate=True,
            risk_caps=_risk_caps(row, strategy_id=strategy_id),
        )

    if spread is not None and spread >= 0.01:
        strategy_id = "maker_passive"
        return StrategyAssignment(
            strategy_id=strategy_id,
            execution_style="maker_passive",
            why_now="spread is wide enough to require passive execution diagnostics",
            live_eligible=True,
            counts_for_live_gate=True,
            risk_caps=_risk_caps(row, strategy_id=strategy_id),
        )

    strategy_id = "taker_update"
    return StrategyAssignment(
        strategy_id=strategy_id,
        execution_style="taker_depth_checked",
        why_now="positive EV row after executable price and cost checks",
        live_eligible=True,
        counts_for_live_gate=True,
        risk_caps=_risk_caps(row, strategy_id=strategy_id),
    )
