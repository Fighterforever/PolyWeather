from __future__ import annotations

import json

from scripts.weather_maker_shadow_v2_funnel_report import build_funnel_report_from_paths
from src.trading.weather_maker_shadow_v2 import build_maker_shadow_v2_funnel_report


def test_maker_shadow_funnel_reports_current_snapshot_no_quote():
    report = build_maker_shadow_v2_funnel_report(
        [
            {
                "generated_at": "2026-06-28T00:00:00Z",
                "total_scanned_rows": 10,
                "total_ge_le_rows": 4,
                "total_non_dust_rows": 3,
                "total_orderbook_available_rows": 2,
                "total_spread_sufficient_rows": 0,
                "quote_count": 0,
                "inferred_fill_count": 0,
                "markout_count": 0,
                "blocker_counts": {"spread_below_min": 3},
            }
        ],
        generated_at="2026-06-29T00:00:00Z",
    )

    assert report["run_count"] == 1
    assert report["total_scanned_rows"] == 10
    assert report["total_quote_count"] == 0
    assert report["quote_count"] == 0
    assert report["actual_window_minutes"] == 0.0
    assert report["rolling_window_sufficient"] is False
    assert report["quote_rate"] == 0.0
    assert report["opportunity_status"] == "maker_shadow_current_snapshot_no_quote"
    assert report["conclusion"] == "maker_shadow_current_snapshot_no_quote"
    assert report["live_order_path"] is False


def test_maker_shadow_funnel_marks_insufficient_window_before_adverse_selection():
    report = build_maker_shadow_v2_funnel_report(
        [
            {
                "generated_at": "2026-06-28T00:00:00Z",
                "total_scanned_rows": 20,
                "quote_count": 5,
                "inferred_fill_count": 2,
                "markout_count": 2,
                "mean_markout_without_rebate": -0.4,
                "mean_markout_with_rebate": -0.2,
            }
        ],
        generated_at="2026-06-29T00:00:00Z",
    )

    assert report["total_quote_count"] == 5
    assert report["total_inferred_fill_count"] == 2
    assert report["mean_markout_without_rebate"] == -0.4
    assert report["opportunity_status"] == "maker_shadow_rolling_window_insufficient"


def test_maker_shadow_funnel_marks_negative_adverse_selection_after_enough_window():
    reports = [
        {
            "generated_at": f"2026-06-28T{index:02d}:00:00Z",
            "total_scanned_rows": 20,
            "quote_count": 5,
            "inferred_fill_count": 2,
            "markout_count": 2,
            "mean_markout_without_rebate": -0.4,
            "mean_markout_with_rebate": -0.2,
        }
        for index in range(12)
    ]

    report = build_maker_shadow_v2_funnel_report(reports, generated_at="2026-06-29T00:00:00Z")

    assert report["run_count"] == 12
    assert report["actual_window_minutes"] == 660.0
    assert report["rolling_window_sufficient"] is True
    assert report["opportunity_status"] == "maker_shadow_negative_adverse_selection"


def test_maker_shadow_funnel_path_loader(tmp_path):
    first = tmp_path / "report-1.json"
    second = tmp_path / "report-2.json"
    first.write_text(
        json.dumps(
            {
                "generated_at": "2026-06-28T00:00:00Z",
                "total_scanned_rows": 10,
                "quote_count": 0,
                "blocker_counts": {"dust_price": 2},
            }
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                "generated_at": "2026-06-28T01:00:00Z",
                "total_scanned_rows": 5,
                "quote_count": 0,
                "blocker_counts": {"dust_price": 1, "spread_below_min": 4},
            }
        ),
        encoding="utf-8",
    )

    report = build_funnel_report_from_paths([first, second], generated_at="2026-06-29T00:00:00Z")

    assert report["run_count"] == 2
    assert report["first_run_at"] == "2026-06-28T00:00:00Z"
    assert report["last_run_at"] == "2026-06-28T01:00:00Z"
    assert report["actual_window_minutes"] == 60.0
    assert report["blocker_counts"] == {"dust_price": 3, "spread_below_min": 4}
    assert report["conclusion"] == "maker_shadow_rolling_window_insufficient"
