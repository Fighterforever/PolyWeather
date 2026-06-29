from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_external_strategy_ingestion.v1"
STRATEGY_ID = "weather_lp_reward_city_specific"


def build_weather_lp_strategy_card(note_path: str | Path) -> Dict[str, Any]:
    source = Path(note_path)
    note_text = source.read_text(encoding="utf-8") if source.exists() else ""
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "source_type": "user_supplied_external_note",
        "source_path": str(source),
        "market_type": "polymarket_weather",
        "expected_edge_source": [
            "maker_rebate_spread",
            "city_specific_weather_regime",
            "time_of_day_liquidity_reward_window",
            "smart_holder_signal",
            "basket_cost_filter",
        ],
        "execution_mode": "maker_shadow / lp_reward_shadow",
        "testable_hypotheses": [
            "lp_reward_metadata_observable",
            "city_specific_range_width_reduces_price_risk",
            "expensive_basket_filter_improves_risk_reward",
            "smart_holder_signal_is_supporting_only",
            "reward_estimate_separate_from_price_markout",
        ],
        "note_character_count": len(note_text),
        "live_eligible": False,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["SCHEMA_VERSION", "STRATEGY_ID", "build_weather_lp_strategy_card", "load_json", "write_json"]
