from __future__ import annotations

import json

from scripts.weather_eq_dead_no_execution_sampler import build_eq_dead_no_execution_sampler_report


def _row(*, side: str, token_id: str, best_ask: float | None = 0.8, ask_depth: float = 5.0, best_bid: float = 0.1):
    return {
        "market_slug": "eq-market",
        "market_id": "market-1",
        "token_id": token_id,
        "side": side,
        "market_family": "temperature",
        "metric": "daily_high_temperature",
        "bucket_type": "eq",
        "threshold": 27.0,
        "target_date": "2026-06-29",
        "settlement_station_code": "UUWW",
        "settlement_source": "metar",
        "settlement_spec": {
            "market_family": "temperature",
            "station_code": "UUWW",
            "settlement_source": "metar",
            "bucket_type": "eq",
            "threshold": 27.0,
            "target_date": "2026-06-29",
        },
        "market_close_time": "2026-06-29T12:00:00Z",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": 0.02,
        "ask_depth_usdc_3c": ask_depth,
        "bid_depth_usdc_3c": 5.0,
    }


def _write_obs(path, value: float = 28.0):
    path.write_text(
        json.dumps(
            {
                "source": "metar",
                "snapshot_type": "observation",
                "station_code": "UUWW",
                "target_date": "2026-06-29",
                "available_at": "2026-06-29T10:00:00Z",
                "observed_at": "2026-06-29T10:00:00Z",
                "payload": {"temperature_c": value},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _report(tmp_path, rows, *, obs_value: float = 28.0, exclude_dust: bool = True):
    obs_path = tmp_path / "obs.jsonl"
    _write_obs(obs_path, value=obs_value)
    return build_eq_dead_no_execution_sampler_report(
        rows=rows,
        intraday_observation_path=obs_path,
        orderbook_archive_dir=tmp_path / "eq",
        paper_fill_dir=tmp_path / "eq",
        generated_at="2026-06-29T10:06:00Z",
        exclude_dust=exclude_dust,
    )


def test_eq_dead_no_sampler_writes_paper_fill_when_no_ask_available(tmp_path):
    report = _report(
        tmp_path,
        [
            _row(side="yes", token_id="yes-token"),
            _row(side="no", token_id="no-token", best_ask=0.8),
        ],
    )

    assert report["scanned_eq_rows"] == 2
    assert report["breached_eq_count"] == 2
    assert report["direct_no_book_count"] == 2
    assert report["executable_candidate_count"] == 1
    assert report["paper_fill_count"] == 1
    fill = json.loads((tmp_path / "eq" / "paper_fills.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert fill["strategy_id"] == "eq_dead_no_lock"
    assert fill["token_id"] == "no-token"
    assert fill["entry_price"] == 0.8
    assert fill["counts_for_live_gate"] is False


def test_eq_dead_no_sampler_rejects_yes_side(tmp_path):
    report = _report(tmp_path, [_row(side="yes", token_id="yes-token")])

    assert report["paper_fill_count"] == 0
    assert any(row["reason"] == "eq_yes_side_rejected" for row in report["reject_reason_counts"])


def test_eq_dead_no_sampler_rejects_dust(tmp_path):
    report = _report(
        tmp_path,
        [
            _row(side="yes", token_id="yes-token"),
            _row(side="no", token_id="no-token", best_ask=0.001),
        ],
        exclude_dust=True,
    )

    assert report["paper_fill_count"] == 0
    assert any(row["reason"] == "dust_price_rejected" for row in report["reject_reason_counts"])


def test_eq_dead_no_sampler_rejects_unbreached_exact(tmp_path):
    report = _report(
        tmp_path,
        [
            _row(side="yes", token_id="yes-token"),
            _row(side="no", token_id="no-token", best_ask=0.8),
        ],
        obs_value=27.0,
    )

    assert report["breached_eq_count"] == 0
    assert report["paper_fill_count"] == 0


def test_eq_dead_no_sampler_requires_direct_no_book(tmp_path):
    report = _report(tmp_path, [_row(side="yes", token_id="yes-token", best_bid=0.2)])

    assert report["paper_fill_count"] == 0
    assert any(row["reason"] == "missing_direct_no_book" for row in report["reject_reason_counts"])


def test_eq_dead_no_sampler_auto_extracts_supported_metar_stations(tmp_path):
    rows = []
    for station in ["LTAC", "UUWW", "EGLC", "RKSI"]:
        yes = _row(side="yes", token_id=f"{station}-yes")
        no = _row(side="no", token_id=f"{station}-no")
        for row in (yes, no):
            row["market_slug"] = f"{station.lower()}-eq"
            row["settlement_station_code"] = station
            row["settlement_spec"] = {**row["settlement_spec"], "station_code": station}
        rows.extend([yes, no])

    report = _report(tmp_path, rows, obs_value=20.0)

    assert report["active_supported_metar_station_count"] == 4
    assert report["active_station_scan_manifest"]["station_codes"] == ["EGLC", "LTAC", "RKSI", "UUWW"]


def test_eq_dead_no_sampler_unsupported_source_does_not_block_supported(tmp_path):
    metar = _row(side="no", token_id="metar-no")
    noaa = _row(side="no", token_id="noaa-no")
    noaa["market_slug"] = "noaa-market"
    noaa["settlement_source"] = "noaa"
    noaa["settlement_spec"] = {**noaa["settlement_spec"], "settlement_source": "noaa"}

    report = _report(tmp_path, [metar, noaa], obs_value=20.0)

    manifest = report["active_station_scan_manifest"]
    assert manifest["active_supported_metar_station_count"] == 1
    assert manifest["unsupported_eq_rows_by_source"] == [{"settlement_source": "noaa", "row_count": 1}]


def test_eq_dead_no_sampler_station_manifest(tmp_path):
    report = _report(tmp_path, [_row(side="no", token_id="no-token")], obs_value=20.0)

    manifest = report["active_station_scan_manifest"]
    assert manifest["active_eq_row_count"] == 1
    assert manifest["rows_by_station"] == [{"station_code": "UUWW", "row_count": 1}]
    assert manifest["collected_station_count"] == 1


def test_eq_dead_no_sampler_opportunity_funnel_counts_layers(tmp_path):
    report = _report(
        tmp_path,
        [
            _row(side="yes", token_id="yes-token"),
            _row(side="no", token_id="no-token", best_ask=0.8, ask_depth=5.0),
        ],
    )

    funnel = report["opportunity_funnel"]
    assert funnel["temperature_rows"] == 2
    assert funnel["exact_bucket_rows"] == 2
    assert funnel["supported_source_rows"] == 2
    assert funnel["breached_eq_rows"] == 2
    assert funnel["direct_no_book_rows"] == 2
    assert funnel["no_ask_available_rows"] == 2
    assert funnel["executable_candidate_rows"] == 1
    assert funnel["paper_fill_rows"] == 1


def test_eq_dead_no_sampler_nearest_breach_watchlist_when_no_breach(tmp_path):
    report = _report(
        tmp_path,
        [
            _row(side="yes", token_id="yes-token"),
            _row(side="no", token_id="no-token", best_ask=0.8),
        ],
        obs_value=26.5,
    )

    assert report["breached_eq_count"] == 0
    assert report["nearest_breach_watchlist"]["watchlist_count"] > 0
