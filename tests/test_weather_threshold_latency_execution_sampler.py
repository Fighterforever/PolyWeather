from __future__ import annotations

import json
from pathlib import Path

from scripts.weather_threshold_latency_execution_sampler import build_threshold_latency_execution_sampler_report
from src.trading.weather_threshold_latency_markout import load_jsonl
from src.weather.weather_observations import OfficialIntradayObservationRepository


def _row(*, best_ask=0.2, ask_depth=10.0, side="yes", token_id="yes-token", bucket_type="ge", threshold=23.0):
    return {
        "market_slug": "market-1",
        "token_id": token_id,
        "side": side,
        "bucket_type": bucket_type,
        "threshold": threshold,
        "target_date": "2026-06-29",
        "settlement_station_code": "UUWW",
        "settlement_source": "metar",
        "order_book": {
            "token_id": token_id,
            "best_bid": 0.1,
            "best_ask": best_ask,
            "spread": 0.01,
            "ask_depth_usdc_3c": ask_depth,
            "bid_depth_usdc_3c": 10.0,
            "asks": [] if best_ask is None else [{"price": best_ask, "size": ask_depth}],
            "bids": [{"price": 0.1, "size": 10.0}],
        },
    }


def _obs(temp: float, *, available_at: str):
    return {
        "source": "aviationweather_metar_recent_72h",
        "settlement_source": "metar",
        "snapshot_type": "observation",
        "station_code": "UUWW",
        "target_date": "2026-06-29",
        "target_date_local": "2026-06-29",
        "available_at": available_at,
        "observed_at": available_at,
        "temperature_c": temp,
        "official_source_flag": True,
    }


def _run(tmp_path, rows, observations):
    repo = OfficialIntradayObservationRepository(tmp_path / "intraday.jsonl")
    repo.append(observations)
    return build_threshold_latency_execution_sampler_report(
        rows=rows,
        intraday_observation_path=repo.path,
        orderbook_archive_dir=tmp_path / "threshold_latency",
        paper_fill_dir=tmp_path / "threshold_latency",
        signal_report_output=tmp_path / "threshold_latency" / "threshold_latency_signal_report.json",
        generated_at="2026-06-29T10:06:00Z",
    )


def test_latency_sampler_writes_paper_fill_for_just_crossed_candidate(tmp_path):
    report = _run(tmp_path, [_row()], [_obs(22, available_at="2026-06-29T10:00:00Z"), _obs(23, available_at="2026-06-29T10:05:00Z")])

    assert report["candidate_count"] == 1
    assert report["paper_fill_count"] == 1
    fills = load_jsonl(tmp_path / "threshold_latency" / "paper_fills.jsonl")
    assert fills[0]["strategy_id"] == "threshold_latency"
    assert fills[0]["signal_type"] == "ge_just_crossed"
    assert fills[0]["counts_for_live_gate"] is False
    assert fills[0]["live_order_path"] is False


def test_latency_sampler_writes_watch_when_no_ask(tmp_path):
    report = _run(tmp_path, [_row(best_ask=None)], [_obs(22, available_at="2026-06-29T10:00:00Z"), _obs(23, available_at="2026-06-29T10:05:00Z")])

    assert report["candidate_count"] == 0
    assert report["paper_fill_count"] == 0
    assert report["watch_count"] == 1
    watch = load_jsonl(tmp_path / "threshold_latency" / "watch_rows.jsonl")
    assert "missing_direct_ask" in watch[0]["blockers"]


def test_latency_sampler_no_live_order_path(tmp_path):
    report = _run(tmp_path, [_row()], [_obs(22, available_at="2026-06-29T10:00:00Z"), _obs(23, available_at="2026-06-29T10:05:00Z")])

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["live_order_path"] is False


def test_latency_sampler_excludes_dust(tmp_path):
    report = _run(tmp_path, [_row(best_ask=0.001)], [_obs(22, available_at="2026-06-29T10:00:00Z"), _obs(23, available_at="2026-06-29T10:05:00Z")])

    assert report["candidate_count"] == 0
    assert report["paper_fill_count"] == 0
    watch = load_jsonl(tmp_path / "threshold_latency" / "watch_rows.jsonl")
    assert "dust_price" in watch[0]["blockers"]
