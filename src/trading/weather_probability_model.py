from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


SCHEMA_VERSION = "polyweather_weather_probability_estimate.v1"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _first_float(row: Dict[str, Any], *fields: str) -> Optional[float]:
    for field in fields:
        value = _safe_float(row.get(field))
        if value is not None:
            return value
    return None


def _effective_entry_price(row: Dict[str, Any], *, fallback_price: Optional[float]) -> tuple[Optional[float], str]:
    ask = _first_float(row, "best_ask", "ask")
    if ask is not None:
        return ask, "best_ask"
    price = _safe_float(fallback_price)
    if price is not None:
        return price, "fallback_price"
    row_price = _first_float(row, "price", "market_probability")
    if row_price is not None:
        return row_price, "row_price"
    return None, "missing"


@dataclass(frozen=True)
class WeatherProbabilityEstimate:
    schema_version: str
    p_model: Optional[float]
    p_lcb: Optional[float]
    probability_haircut: Optional[float]
    q_effective: Optional[float]
    executable_price_source: str
    cost: Optional[float]
    ev_safe: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        return {
            key: (round(value, 6) if isinstance(value, float) else value)
            for key, value in payload.items()
        }


def build_weather_probability_estimate(
    row: Dict[str, Any],
    *,
    model_probability: Optional[float],
    fallback_price: Optional[float] = None,
    min_probability_haircut: float = 0.03,
    default_fee_cost: float = 0.005,
    default_settlement_risk_cost: float = 0.0,
) -> WeatherProbabilityEstimate:
    """Build a conservative EV surface for one binary weather outcome.

    This is deliberately a small, auditable first step: it replaces raw
    model-minus-price edge with executable entry price, a probability haircut,
    and explicit cost fields. Full station-level calibration can later replace
    the haircut inputs without changing the candidate gate contract.
    """

    p_raw = _safe_float(model_probability)
    q_effective, price_source = _effective_entry_price(row, fallback_price=fallback_price)
    if p_raw is None or q_effective is None:
        return WeatherProbabilityEstimate(
            schema_version=SCHEMA_VERSION,
            p_model=None if p_raw is None else _clamp_probability(p_raw),
            p_lcb=None,
            probability_haircut=None,
            q_effective=q_effective,
            executable_price_source=price_source,
            cost=None,
            ev_safe=None,
        )

    p_model = _clamp_probability(p_raw)
    spread = _safe_float(row.get("spread"))
    row_haircut = _first_float(
        row,
        "calibration_error",
        "probability_haircut",
        "station_probability_haircut",
    )
    spread_haircut = max(0.0, spread or 0.0) * 0.5
    probability_haircut = max(float(min_probability_haircut), row_haircut or 0.0, spread_haircut)
    p_lcb = _clamp_probability(p_model - probability_haircut)

    explicit_cost = _first_float(row, "cost", "estimated_cost", "execution_cost")
    slippage_cost = _first_float(row, "slippage_cost", "estimated_slippage") or 0.0
    fee_cost = _first_float(row, "fee_cost", "taker_fee_cost") or float(default_fee_cost)
    settlement_risk_cost = (
        _first_float(row, "settlement_risk_cost") or float(default_settlement_risk_cost)
    )
    cost = explicit_cost if explicit_cost is not None else slippage_cost + fee_cost + settlement_risk_cost
    ev_safe = p_lcb - float(q_effective) - cost

    return WeatherProbabilityEstimate(
        schema_version=SCHEMA_VERSION,
        p_model=p_model,
        p_lcb=p_lcb,
        probability_haircut=probability_haircut,
        q_effective=float(q_effective),
        executable_price_source=price_source,
        cost=cost,
        ev_safe=ev_safe,
    )
