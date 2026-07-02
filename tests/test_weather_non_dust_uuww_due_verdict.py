from __future__ import annotations

import json

from scripts.weather_non_dust_uuww_due_verdict_report import build_non_dust_uuww_due_verdict


def test_non_dust_verdict_waits_before_due_and_keeps_unresolved_pnl_null(tmp_path):
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"status": "pending", "live_order_path": False}), encoding="utf-8")

    report = build_non_dust_uuww_due_verdict(
        run_after_utc="2026-06-30T03:05:00Z",
        after_due_summary_path=tmp_path / "missing.json",
        pending_summary_path=pending,
        generated_at="2026-06-28T09:00:00Z",
    )

    assert report["status"] == "waiting_due"
    assert report["due_reached"] is False
    assert report["strict_replay"]["resolved_pnl_cents"] is None
    assert report["alpha_conclusion"] == "non_dust_threshold_cdf_waiting_resolution"
    assert report["live_order_path"] is False


def test_non_dust_verdict_marks_negative_resolved_pnl_failed(tmp_path):
    after = tmp_path / "after.json"
    after.write_text(
        json.dumps(
            {
                "closed_backfill_executed": True,
                "matched_closed_archived_token_count": 1,
                "strict_replay": {"fill_count": 1, "resolved_fill_count": 1, "resolved_pnl_cents": -12.0},
                "settlement_calibration": {"probability_score_sample_count": 1, "resolved_pnl_sample_count": 1},
            }
        ),
        encoding="utf-8",
    )

    report = build_non_dust_uuww_due_verdict(
        run_after_utc="2026-06-30T03:05:00Z",
        after_due_summary_path=after,
        pending_summary_path=tmp_path / "missing-pending.json",
        generated_at="2026-06-30T04:00:00Z",
    )

    assert report["status"] == "after_due_artifact_ready"
    assert report["strict_replay"]["resolved_pnl_cents"] == -12.0
    assert report["alpha_conclusion"] == "non_dust_threshold_cdf_failed"
