from __future__ import annotations

from src.weather.metar_intraday_collector import collect_metar_intraday_history
from src.weather.weather_observations import OfficialIntradayObservationRepository


def _api_row(station: str, report_time: str, receipt_time: str, temp: float):
    return {
        "icaoId": station,
        "reportTime": report_time,
        "receiptTime": receipt_time,
        "temp": temp,
        "rawOb": f"METAR {station} {temp}",
    }


def test_metar_history_backfill_rows_are_paper_only():
    report = collect_metar_intraday_history(
        station_codes=["UUWW"],
        start_date="2026-06-20",
        end_date="2026-06-20",
        fetched_at="2026-06-28T00:00:00Z",
        api_rows=[
            _api_row("UUWW", "2026-06-20T10:00:00Z", "2026-06-20T10:04:00Z", 20),
            _api_row("UUWW", "2026-06-20T11:00:00Z", "2026-06-20T11:04:00Z", 22),
        ],
    )

    assert report["source"] == "aviationweather_metar_history"
    assert report["observation_count"] == 2
    assert all(row["paper_only"] is True for row in report["observations"])
    assert all(row["counts_for_live_gate"] is False for row in report["observations"])


def test_current_high_as_of_cannot_see_future_history_rows(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "history.jsonl")
    report = collect_metar_intraday_history(
        station_codes=["UUWW"],
        start_date="2026-06-20",
        end_date="2026-06-20",
        fetched_at="2026-06-28T00:00:00Z",
        api_rows=[
            _api_row("UUWW", "2026-06-20T10:00:00Z", "2026-06-20T10:04:00Z", 20),
            _api_row("UUWW", "2026-06-20T12:00:00Z", "2026-06-20T12:04:00Z", 27),
        ],
    )
    repo.append(report["observations"])

    high = repo.current_high_as_of(
        station_code="UUWW",
        target_date="2026-06-20",
        replay_time="2026-06-20T11:00:00Z",
    )

    assert high["current_high_c"] == 20
    assert high["observation_count"] == 1
