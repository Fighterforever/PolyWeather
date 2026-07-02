from __future__ import annotations

from pathlib import Path

from src.trading.polymarket_alpha.external_strategy_ingestion import build_weather_lp_strategy_card


def test_external_strategy_card_is_paper_only(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("LP reward idea, not proven.", encoding="utf-8")
    card = build_weather_lp_strategy_card(note)

    assert card["strategy_id"] == "weather_lp_reward_city_specific"
    assert card["source_type"] == "user_supplied_external_note"
    assert card["live_eligible"] is False
    assert card["paper_only"] is True
    assert card["live_order_path"] is False
    assert "basket_cost_filter" in card["expected_edge_source"]
