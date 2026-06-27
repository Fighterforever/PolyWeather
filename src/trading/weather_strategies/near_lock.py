from __future__ import annotations

from typing import Any, Dict, Optional

from src.trading.weather_strategies.base import StrategyAssignment, assign_weather_strategy


def assign_near_lock(row: Dict[str, Any], *, now: Optional[str] = None) -> StrategyAssignment:
    return assign_weather_strategy(row, bucket_type=row.get("bucket_type"), now=now)
