from __future__ import annotations

from typing import Any, Dict

from src.trading.weather_strategies.base import StrategyAssignment, assign_weather_strategy


def assign_maker_passive(row: Dict[str, Any]) -> StrategyAssignment:
    return assign_weather_strategy({**row, "spread": row.get("spread") or 0.02})
