from __future__ import annotations

import json
from pathlib import Path

from scripts.weather_observation_lock_execution_sampler import build_observation_lock_execution_sampler_report
from src.trading.weather_paper_journal import load_jsonl
from src.weather.weather_observations import OfficialIntradayObservationRepository


def _row(
    *,
    bucket_type: str = "ge",
    threshold: float = 23.0,
    best_bid: float = 0.38,
    best_ask: float = 0.40,
    spread: float = 0.02,
    ask_depth: float = 10.0,
    bid_depth: float = 10.0,
    token_id: str = "yes-token",
    side: str = "yes",
) -> dict:
    return {
        "market_slug": f"highest-temperature-test-{bucket_type}-{token_id}",
        "token_id": token_id,
        "side": side,
        "bucket_type": bucket_type,
        "threshold": threshold,
        "target_date": "2026-06-29",
        "settlement_station_code": "UUWW",
        "settlement_source": "metar",
        "market_close_time": "2026-06-29T12:00:00Z",
        "observation_window_end_time": "2026-06-29T20:59:59Z",
        "order_book": {
            "token_id": token_id,
            "timestamp": "2026-06-29T10:05:30Z",
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "ask_depth_usdc_3c": ask_depth,
            "bid_depth_usdc_3c": bid_depth,
            "asks": [{"price": best_ask, "size": ask_depth}],
            "bids": [{"price": best_bid, "size": bid_depth}],
        },
    }


def _obs(value: float, *, available_at: str = "2026-06-29T10:00:00Z", flags: list[str] | None = None) -> dict:
    return {
        "source": "aviationweather_metar_recent_72h",
        "settlement_source": "metar",
        "snapshot_type": "observation",
        "station_code": "UUWW",
        "target_date": "2026-06-29",
        "target_date_local": "2026-06-29",
        "available_at": available_at,
        "observed_at": available_at,
        "temperature_c": value,
        "official_source_flag": True,
        "quality_flags": [],
        "anomaly_flags": flags or [],
    }


def _repo(tmp_path, observations):
    repo = OfficialIntradayObservationRepository(tmp_path / "intraday.jsonl")
    repo.append(observations)
    return repo.path


def _run(tmp_path, rows, observations, *, generated_at="2026-06-29T10:06:00Z"):
    return build_observation_lock_execution_sampler_report(
        rows=rows,
        intraday_observation_path=_repo(tmp_path, observations),
        orderbook_archive_dir=tmp_path / "archive",
        paper_fill_dir=tmp_path / "fills",
        signal_report_output=tmp_path / "signal_report.json",
        generated_at=generated_at,
        max_candidates=20,
        bucket_types=("ge", "le"),
        exclude_dust=True,
        max_staleness_minutes=10,
    )


def test_execution_sampler_writes_paper_fill_for_locked_non_dust_candidate(tmp_path):
    report = _run(tmp_path, [_row()], [_obs(24)])

    assert report["paper_only"] is True
    assert report["live_order_path"] is False
    assert report["paper_fill_count"] == 1
    assert report["orderbook_snapshot_count"] == 1
    snapshots = load_jsonl(tmp_path / "archive" / "orderbook_snapshots.jsonl")
    fills = load_jsonl(tmp_path / "fills" / "paper_fills.jsonl")
    assert len(snapshots) == 1
    assert len(fills) == 1
    assert fills[0]["orderbook_snapshot_id"] == snapshots[0]["snapshot_id"]
    assert fills[0]["counts_for_live_gate"] is False


def test_execution_sampler_rejects_missing_intraday(tmp_path):
    report = _run(tmp_path, [_row()], [])

    assert report["paper_fill_count"] == 0
    reasons = {row["reason"] for row in report["reject_reason_counts"]}
    assert "missing_intraday_observation" in reasons


def test_execution_sampler_rejects_dust(tmp_path):
    report = _run(tmp_path, [_row(best_ask=0.001, spread=0.001)], [_obs(24)])

    assert report["paper_fill_count"] == 0
    reasons = {row["reason"] for row in report["reject_reason_counts"]}
    assert "dust_price" in reasons


def test_execution_sampler_rejects_anomaly(tmp_path):
    report = _run(tmp_path, [_row()], [_obs(20, available_at="2026-06-29T10:00:00Z"), _obs(33, available_at="2026-06-29T10:05:00Z")])

    assert report["paper_fill_count"] == 0
    reasons = {row["reason"] for row in report["reject_reason_counts"]}
    assert "blocked_by_observation_anomaly" in reasons


def test_sampler_blocks_stale_not_locked_intraday_observation(tmp_path):
    report = _run(
        tmp_path,
        [_row()],
        [_obs(22, available_at="2026-06-29T09:40:00Z")],
        generated_at="2026-06-29T10:06:00Z",
    )

    assert report["paper_fill_count"] == 0
    reasons = {row["reason"] for row in report["reject_reason_counts"]}
    assert "stale_intraday_observation" in reasons


def test_sampler_uses_latest_visible_observation(tmp_path):
    observations = [
        _obs(22, available_at="2026-06-29T09:55:00Z"),
        _obs(24, available_at="2026-06-29T10:00:00Z"),
        _obs(30, available_at="2026-06-29T10:10:00Z"),
    ]
    report = _run(tmp_path, [_row(threshold=23)], observations, generated_at="2026-06-29T10:06:00Z")

    assert report["paper_fill_count"] == 1
    assert report["candidate_samples"][0]["official_current_high"] == 24
    signal_report = json.loads(Path(tmp_path / "signal_report.json").read_text(encoding="utf-8"))
    assert signal_report["rows"][0]["latest_available_at"] == "2026-06-29T10:00:00Z"


def test_sampler_archives_locked_watch_orderbook_even_without_candidate(tmp_path):
    report = _run(
        tmp_path,
        [_row(bucket_type="le", threshold=24, best_bid=0.2, best_ask=0.3, side="yes", token_id="yes-token")],
        [_obs(28)],
    )

    assert report["paper_fill_count"] == 0
    assert report["ge_le_locked_count"] == 1
    assert report["locked_watch_orderbook_snapshot_count"] == 1
    assert report["locked_watch_rows_written_count"] == 1
    reasons = {row["reason"] for row in report["ge_le_locked_reject_reason_counts"]}
    assert "synthetic_no_price_diagnostic_only" in reasons
    watch_rows = load_jsonl(tmp_path / "archive" / "locked_watch_rows.jsonl")
    watch_snapshots = load_jsonl(tmp_path / "archive" / "locked_watch_orderbook_snapshots.jsonl")
    assert len(watch_rows) == 1
    assert len(watch_snapshots) == 1


def test_sampler_does_not_archive_eq_shadow_as_alpha_watch(tmp_path):
    report = _run(
        tmp_path,
        [_row(bucket_type="eq", threshold=23, best_bid=0.2, best_ask=0.3, side="yes", token_id="yes-token")],
        [_obs(28)],
    )

    assert report["paper_fill_count"] == 0
    assert report["ge_le_locked_count"] == 0
    assert report["locked_watch_orderbook_snapshot_count"] == 0
    assert load_jsonl(tmp_path / "archive" / "locked_watch_rows.jsonl") == []
