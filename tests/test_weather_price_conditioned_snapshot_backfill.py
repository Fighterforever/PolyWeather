from __future__ import annotations

from src.trading.weather_paper_journal import load_jsonl, write_paper_journal
from src.trading.weather_price_conditioned_snapshot_backfill import replay_price_conditioned_snapshots_to_journal


def _price_conditioned_report(generated_at: str, *, price: float = 0.09) -> dict:
    return {
        "schema_version": "polyweather_weather_market_signal_report.v1",
        "report_type": "price_conditioned_paper_signal_report",
        "generated_at": generated_at,
        "source_snapshot_id": f"price-conditioned:{generated_at}",
        "source_status": "price_conditioned_risk_only_quarantine_found",
        "source": "price_conditioned_quality_search",
        "paper_only": True,
        "counts_for_live_gate": False,
        "summary": {
            "candidate_count": 0,
            "watch_count": 0,
            "quarantine_count": 1,
            "live_gate": False,
            "live_authorization_pct": 0,
        },
        "candidates": [],
        "watch": [],
        "quarantine": [
            {
                "decision": "quarantine",
                "row_id": "paris:no",
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "token_id": "no-token",
                "side": "no",
                "bucket_label": "<= 37°C",
                "bucket_type": "le",
                "price": price,
                "bid": price - 0.01,
                "ask": price,
                "spread": 0.01,
                "edge_percent": 13.6,
                "risk_rule_hits": ["negative_markout_rule:by_entry_spread_bucket:entry_spread_bucket=0.005-0.015"],
                "price_conditioned_status": "risk_only_quarantine",
                "base_rate_reference": {"side": "no", "bucket_type": "le", "count": 30},
                "conservative_base_rate_fair_price": 0.63,
                "price_discount_cents": 54.0,
                "counts_for_live_gate": False,
                "live_gate_excluded": True,
            }
        ],
    }


def test_replay_price_conditioned_snapshots_marks_backfill_and_respects_interval(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    write_paper_journal(
        _price_conditioned_report("2026-06-26T19:00:00Z"),
        journal_dir=source,
        include_quarantine=True,
        recorded_at="2026-06-26T19:00:00Z",
    )
    write_paper_journal(
        _price_conditioned_report("2026-06-26T19:20:00Z"),
        journal_dir=source,
        include_quarantine=True,
        recorded_at="2026-06-26T19:20:00Z",
    )
    write_paper_journal(
        _price_conditioned_report("2026-06-26T20:05:00Z"),
        journal_dir=source,
        include_quarantine=True,
        recorded_at="2026-06-26T20:05:00Z",
    )

    report = replay_price_conditioned_snapshots_to_journal(
        source_journal_dir=source,
        target_journal_dir=target,
        sample_interval_minutes=60,
    )
    fills = load_jsonl(target / "paper_fills.jsonl")

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["snapshot_count"] == 3
    assert report["fill_count"] == 2
    assert report["duplicate_skipped_count"] == 1
    assert len(fills) == 2
    assert all(row["paper_backfill_from_snapshot"] is True for row in fills)
    assert all(row["counts_for_live_gate"] is False for row in fills)
    assert fills[0]["backfill_source_recorded_at"] == "2026-06-26T19:00:00Z"
    assert fills[0]["price_conditioned_status"] == "risk_only_quarantine"
    assert fills[0]["price_discount_cents"] == 54.0
