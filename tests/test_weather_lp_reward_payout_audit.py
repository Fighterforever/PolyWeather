from __future__ import annotations

from pathlib import Path

from src.trading.polymarket_alpha.weather_lp_reward_payout_audit import (
    MANUAL_PAYOUT_AUDIT_TEMPLATE_V2_COLUMNS,
    build_manual_payout_audit_result,
    build_weather_lp_reward_payout_audit_report,
    render_manual_payout_template_v2_markdown,
    render_manual_template_markdown,
    write_empty_manual_payout_audit_template_v2,
    write_manual_template,
)
from src.trading.polymarket_alpha.weather_lp_tiny_live_audit import (
    build_weather_lp_tiny_live_audit_report,
    write_tiny_live_payout_manual_template,
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
    assert report["manual_audit_required"] is True
    assert report["reward_payout_gap_reason"] == "exact_total_score_unavailable"
    assert report["next_reward_audit_time_utc"] == "2026-07-02T00:00:00Z"
    assert report["live_order_path"] is False
    count = write_manual_template(tmp_path / "template.csv", [{"market_slug": "m1", "quote_id": "q1", "token_id": "t1"}], report)
    assert count == 1
    assert "actual_reward_received" in (tmp_path / "template.csv").read_text(encoding="utf-8")
    rendered = render_manual_template_markdown(report, [{"market_slug": "m1", "quote_id": "q1", "token_id": "t1"}])
    assert "tx_hash_or_statement_ref" in rendered


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


def test_manual_payout_audit_template_v2_is_empty_for_user_fill(tmp_path: Path):
    output = tmp_path / "manual_payout_audit_template.csv"
    count = write_empty_manual_payout_audit_template_v2(output)

    assert count == 0
    text = output.read_text(encoding="utf-8")
    assert "actual_reward_received" in text
    assert len(text.strip().splitlines()) == 1
    rendered = render_manual_payout_template_v2_markdown()
    assert "operator_notes" in rendered
    assert MANUAL_PAYOUT_AUDIT_TEMPLATE_V2_COLUMNS[0] == "audit_id"


def test_manual_payout_audit_ingest_waits_when_csv_missing():
    report = build_manual_payout_audit_result(filled_rows=[], filled_csv_exists=False)

    assert report["audit_status"] == "waiting_for_manual_audit"
    assert report["audit_verdict"] == "insufficient_manual_data"
    assert report["live_order_path"] is False


def test_manual_payout_audit_ingest_compares_expected_actual():
    report = build_manual_payout_audit_result(
        filled_rows=[
            {
                "expected_reward_low": "1",
                "expected_reward_base": "2",
                "expected_reward_high": "4",
                "actual_reward_received": "2.5",
                "actual_markout": "-0.4",
                "fill_occurred": "true",
                "adverse_selection_notes": "small fill",
            }
        ],
        filled_csv_exists=True,
    )

    assert report["audit_status"] == "manual_audit_ingested"
    assert report["audit_verdict"] == "reward_payout_confirmed"
    assert report["actual_vs_expected_ratio"] == 1.25
    assert report["fill_count"] == 1
    assert report["live_order_path"] is False


def test_tiny_live_audit_reports_manual_template_when_no_reward_api(tmp_path: Path):
    orders = [
        {
            "client_order_id": "c1",
            "market_slug": "m1",
            "token_id": "t1",
            "placed": True,
            "expected_reward_base": 10,
        }
    ]
    report = build_weather_lp_tiny_live_audit_report(order_rows=orders, update_rows=[], cancellation_rows=[], payout_rows=[])

    assert report["placed_order_count"] == 1
    assert report["payout_observed"] is False
    assert report["payout_gap_reason"] == "manual_payout_audit_required"
    assert report["live_order_path"] is True
    count = write_tiny_live_payout_manual_template(tmp_path / "tiny_template.csv", orders)
    assert count == 1
    assert "actual_reward_received" in (tmp_path / "tiny_template.csv").read_text(encoding="utf-8")
