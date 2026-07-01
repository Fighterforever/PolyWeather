from __future__ import annotations

from pathlib import Path

from src.trading.polymarket_alpha.weather_lp_reward_payout_audit import (
    build_weather_lp_reward_payout_audit_report,
    write_manual_template,
)


def test_payout_audit_outputs_manual_required_without_wallet(tmp_path: Path):
    report = build_weather_lp_reward_payout_audit_report(
        reward_dollarization_report={},
        profitability_simulation_report={"scenario_table": {"base_reward": 12.0}},
        paper_quotes=[{"market_slug": "m1"}],
        quote_updates=[{"quote_id": "q1"}],
        reward_allocation_audit_report={},
        generated_at="2026-07-01T00:00:00Z",
    )

    assert report["audit_status"] == "manual_audit_required"
    assert report["payout_gap_reason"] == "exact_total_score_unavailable"
    assert report["live_order_path"] is False
    count = write_manual_template(tmp_path / "template.csv", [{"market_slug": "m1"}], report)
    assert count == 1
    assert "actual_reward_received" in (tmp_path / "template.csv").read_text(encoding="utf-8")


def test_payout_audit_does_not_fake_payout():
    report = build_weather_lp_reward_payout_audit_report(
        reward_dollarization_report={"exact_reward_cents_available_count": 0},
        profitability_simulation_report={},
        paper_quotes=[],
        quote_updates=[],
        reward_allocation_audit_report={},
    )

    assert report["payout_observed"] is False
    assert report["payout_amount"] is None
