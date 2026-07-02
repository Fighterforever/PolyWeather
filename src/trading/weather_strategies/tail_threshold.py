from __future__ import annotations

from typing import Any, Dict

from src.trading.weather_strategies.base import StrategyAssignment, assign_weather_strategy


def assign_tail_threshold(row: Dict[str, Any]) -> StrategyAssignment:
    return assign_weather_strategy(row, bucket_type=row.get("bucket_type") or "ge")
