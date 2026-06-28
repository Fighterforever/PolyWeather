from __future__ import annotations

from src.trading.weather_closed_replay_seed import build_closed_replay_seed_report
from src.weather.official_value_backfill import (
    apply_official_value_supplements,
    build_official_observation_request_plan,
    build_official_value_backfill_report,
    build_official_value_supplement,
    load_official_value_supplements,
)


def _record(**overrides):
    row = {
        "status": "resolved",
        "record_id": "closed-busan-1",
        "market_id": "market-busan-1",
        "market_slug": "highest-temperature-in-busan-on-june-26-2026-30c-or-above",
        "event_slug": "highest-temperature-in-busan-on-june-26-2026",
        "question": "Will the highest temperature in Busan be 30°C or above on June 26?",
        "city": "busan",
        "target_date": "2026-06-26",
        "end_date": "2026-06-26T12:00:00Z",
        "bucket_label": ">= 30°C",
        "parsed_temperature_spec": {
            "city": "busan",
            "target_date": "2026-06-26",
            "threshold": 30,
            "comparator": "ge",
            "unit": "C",
        },
        "outcomes": ["Yes", "No"],
        "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "winning_outcome": "Yes",
        "winning_token_id": "yes-token",
        "resolution_source": "polymarket_api",
        "rule_hash": "rule-hash",
        "official_final_value": None,
        "settlement_spec": {
            "city": "busan",
            "station_code": "RKPK",
            "settlement_source": "wunderground",
            "target_date": "2026-06-26",
            "unit": "C",
        },
    }
    row.update(overrides)
    return row


class EmptyRepository:
    def load_points(self, *, source_code, station_code, target_date):
        return []


class CountingRepository:
    def __init__(self, points):
        self.points = points
        self.calls = []

    def load_points(self, *, source_code, station_code, target_date):
        self.calls.append(
            {
                "source_code": source_code,
                "station_code": station_code,
                "target_date": target_date,
            }
        )
        return list(self.points)


class FakeWundergroundCollector:
    def __init__(self):
        self.calls = []

    def fetch_wunderground_historical(self, city, *, use_fahrenheit, utc_offset, local_date):
        self.calls.append(
            {
                "city": city,
                "use_fahrenheit": use_fahrenheit,
                "utc_offset": utc_offset,
                "local_date": local_date,
            }
        )
        return {
            "source": "wunderground_historical",
            "station_code": "RKPK",
            "daily_high": 31.2,
            "max_temp_time": "14:20",
            "observation_count": 24,
        }


class FakeMetarCollector:
    metar_timeout_sec = 4
    timeout = 4

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def _http_get(self, url, *, params, timeout):
        self.calls.append({"url": url, "params": params, "timeout": timeout})

        class Response:
            content = b"[]"

            def __init__(self, rows):
                self._rows = rows

            def raise_for_status(self):
                return None

            def json(self):
                return self._rows

        return Response(self.rows)


def test_official_value_backfill_uses_wunderground_for_wunderground_settlement_source():
    collector = FakeWundergroundCollector()
    supplement = build_official_value_supplement(
        _record(),
        repository=EmptyRepository(),
        collector=collector,
        fetch_external=True,
    )

    assert supplement["status"] == "ready"
    assert supplement["official_final_value"] == 31.2
    assert supplement["official_final_value_source"] == "wunderground_historical"
    assert supplement["official_final_value_station_code"] == "RKPK"
    assert supplement["retrieval_method"] == "wunderground_historical"
    assert collector.calls == [
        {
            "city": "busan",
            "use_fahrenheit": False,
            "utc_offset": 32400,
            "local_date": "2026-06-26",
        }
    ]


def test_official_value_backfill_does_not_use_wunderground_proxy_for_metar_source():
    collector = FakeWundergroundCollector()
    supplement = build_official_value_supplement(
        _record(
            city="seoul",
            settlement_spec={
                "city": "seoul",
                "station_code": "RKSI",
                "settlement_source": "metar",
                "target_date": "2026-06-26",
                "unit": "C",
            },
            parsed_temperature_spec={
                "city": "seoul",
                "target_date": "2026-06-26",
                "threshold": 30,
                "comparator": "ge",
                "unit": "C",
            },
        ),
        repository=EmptyRepository(),
        collector=collector,
        fetch_external=True,
    )

    assert supplement["status"] == "gap"
    assert supplement["gap_reason"] == "metar_recent_fetch_error"
    assert collector.calls == []


def test_official_value_backfill_uses_recent_metar_for_metar_settlement_source():
    collector = FakeMetarCollector(
        [
            {
                "icaoId": "RKSI",
                "reportTime": "2026-06-26T00:00:00.000Z",
                "temp": 24,
            },
            {
                "icaoId": "RKSI",
                "reportTime": "2026-06-26T05:00:00.000Z",
                "temp": 31,
            },
            {
                "icaoId": "RKSI",
                "reportTime": "2026-06-25T05:00:00.000Z",
                "temp": 33,
            },
        ]
    )

    supplement = build_official_value_supplement(
        _record(
            city="seoul",
            target_date="2026-06-26",
            settlement_spec={
                "city": "seoul",
                "station_code": "RKSI",
                "settlement_source": "metar",
                "target_date": "2026-06-26",
                "unit": "C",
            },
            parsed_temperature_spec={
                "city": "seoul",
                "target_date": "2026-06-26",
                "threshold": 30,
                "comparator": "ge",
                "unit": "C",
            },
        ),
        repository=EmptyRepository(),
        collector=collector,
        fetch_external=True,
    )

    assert supplement["status"] == "ready"
    assert supplement["official_final_value"] == 31.0
    assert supplement["official_final_value_source"] == "aviationweather_metar_recent"
    assert supplement["official_final_value_station_code"] == "RKSI"
    assert supplement["retrieval_method"] == "aviationweather_metar_recent_72h"
    assert collector.calls[0]["params"] == {"ids": "RKSI", "format": "json", "hours": 72}


def test_official_value_backfill_uses_noaa_station_adapter_for_noaa_source():
    collector = FakeMetarCollector(
        [
            {
                "icaoId": "LTFM",
                "reportTime": "2026-06-27T10:00:00.000Z",
                "temp": 24,
            },
            {
                "icaoId": "LTFM",
                "reportTime": "2026-06-27T12:00:00.000Z",
                "temp": 27,
            },
        ]
    )

    supplement = build_official_value_supplement(
        _record(
            city="istanbul",
            target_date="2026-06-27",
            settlement_spec={
                "city": "istanbul",
                "station_code": "LTFM",
                "settlement_source": "noaa",
                "target_date": "2026-06-27",
                "unit": "C",
            },
            parsed_temperature_spec={
                "city": "istanbul",
                "target_date": "2026-06-27",
                "threshold": 27,
                "comparator": "eq",
                "unit": "C",
            },
        ),
        repository=EmptyRepository(),
        collector=collector,
        fetch_external=True,
    )

    assert supplement["status"] == "ready"
    assert supplement["official_final_value"] == 27.0
    assert supplement["official_final_value_source"] == "aviationweather_noaa_station_recent"
    assert supplement["official_final_value_source_code"] == "noaa_station_observation"
    assert supplement["retrieval_method"] == "aviationweather_noaa_station_recent_72h"
    assert collector.calls[0]["params"] == {"ids": "LTFM", "format": "json", "hours": 72}


def test_official_value_backfill_converts_recent_metar_to_fahrenheit_for_f_markets():
    collector = FakeMetarCollector(
        [
            {
                "icaoId": "KLGA",
                "reportTime": "2026-06-25T18:00:00.000Z",
                "temp": 25,
            },
            {
                "icaoId": "KLGA",
                "reportTime": "2026-06-25T20:00:00.000Z",
                "temp": 28,
            },
        ]
    )

    supplement = build_official_value_supplement(
        _record(
            city="new york",
            target_date="2026-06-25",
            settlement_spec={
                "city": "new york",
                "station_code": "KLGA",
                "settlement_source": "metar",
                "target_date": "2026-06-25",
                "unit": "F",
            },
            parsed_temperature_spec={
                "city": "new york",
                "target_date": "2026-06-25",
                "threshold": 80,
                "comparator": "ge",
                "unit": "F",
            },
        ),
        repository=EmptyRepository(),
        collector=collector,
        fetch_external=True,
    )

    assert supplement["status"] == "ready"
    assert supplement["official_final_value"] == 82.4


def test_official_value_backfill_metar_cache_preserves_each_market_identity():
    collector = FakeMetarCollector(
        [
            {
                "icaoId": "RKSI",
                "reportTime": "2026-06-26T05:00:00.000Z",
                "temp": 31,
            },
        ]
    )
    records = [
        _record(
            city="seoul",
            target_date="2026-06-26",
            market_id="market-25",
            market_slug="highest-temperature-in-seoul-on-june-26-2026-25c",
            settlement_spec={
                "city": "seoul",
                "station_code": "RKSI",
                "settlement_source": "metar",
                "target_date": "2026-06-26",
                "unit": "C",
            },
            parsed_temperature_spec={
                "city": "seoul",
                "target_date": "2026-06-26",
                "threshold": 25,
                "comparator": "eq",
                "unit": "C",
            },
        ),
        _record(
            city="seoul",
            target_date="2026-06-26",
            market_id="market-26",
            market_slug="highest-temperature-in-seoul-on-june-26-2026-26c",
            settlement_spec={
                "city": "seoul",
                "station_code": "RKSI",
                "settlement_source": "metar",
                "target_date": "2026-06-26",
                "unit": "C",
            },
            parsed_temperature_spec={
                "city": "seoul",
                "target_date": "2026-06-26",
                "threshold": 26,
                "comparator": "eq",
                "unit": "C",
            },
        ),
    ]

    report = build_official_value_backfill_report(
        records,
        repository=EmptyRepository(),
        collector=collector,
        fetch_external=True,
    )

    assert report["ready_count"] == 2
    assert len(collector.calls) == 1
    assert [row["market_slug"] for row in report["supplements"]] == [
        "highest-temperature-in-seoul-on-june-26-2026-25c",
        "highest-temperature-in-seoul-on-june-26-2026-26c",
    ]


def test_official_value_backfill_plan_dedupes_station_date_requests():
    records = [
        _record(
            market_id="market-25",
            market_slug="highest-temperature-in-busan-on-june-26-2026-25c",
            parsed_temperature_spec={
                "city": "busan",
                "target_date": "2026-06-26",
                "threshold": 25,
                "comparator": "eq",
                "unit": "C",
            },
        ),
        _record(
            market_id="market-26",
            market_slug="highest-temperature-in-busan-on-june-26-2026-26c",
            parsed_temperature_spec={
                "city": "busan",
                "target_date": "2026-06-26",
                "threshold": 26,
                "comparator": "eq",
                "unit": "C",
            },
        ),
    ]

    report = build_official_value_backfill_report(
        records,
        repository=EmptyRepository(),
        fetch_external=False,
    )

    plan = report["official_observation_backfill_plan"]
    assert report["ready_count"] == 0
    assert report["gap_count"] == 2
    assert plan["request_count"] == 1
    assert plan["returned_request_count"] == 1
    assert plan["truncated"] is False
    assert plan["records_covered_count"] == 2
    assert plan["by_settlement_source"] == [{"settlement_source": "wunderground", "count": 1}]
    assert plan["by_gap_reason"] == [{"reason": "missing_observation_points", "count": 2}]
    assert plan["requests"][0]["record_count"] == 2
    assert plan["requests"][0]["settlement_source"] == "wunderground"
    assert plan["requests"][0]["station_code"] == "RKPK"
    assert plan["requests"][0]["target_date"] == "2026-06-26"
    assert plan["requests"][0]["supported_external_method"] == "wunderground_historical"
    assert plan["requests"][0]["market_slug_samples"] == [
        "highest-temperature-in-busan-on-june-26-2026-25c",
        "highest-temperature-in-busan-on-june-26-2026-26c",
    ]


def test_official_value_backfill_reuses_station_date_source_lookup_for_multiple_buckets():
    repository = CountingRepository(
        [
            {"time": "2026-06-26T01:00:00Z", "temp": 28.0},
            {"time": "2026-06-26T05:00:00Z", "temp": 31.2},
        ]
    )
    records = [
        _record(
            market_id="market-25",
            market_slug="highest-temperature-in-busan-on-june-26-2026-25c",
            parsed_temperature_spec={
                "city": "busan",
                "target_date": "2026-06-26",
                "threshold": 25,
                "comparator": "eq",
                "unit": "C",
            },
        ),
        _record(
            market_id="market-26",
            market_slug="highest-temperature-in-busan-on-june-26-2026-26c",
            parsed_temperature_spec={
                "city": "busan",
                "target_date": "2026-06-26",
                "threshold": 26,
                "comparator": "eq",
                "unit": "C",
            },
        ),
    ]

    report = build_official_value_backfill_report(records, repository=repository)

    assert report["ready_count"] == 2
    assert repository.calls == [
        {
            "source_code": "wunderground",
            "station_code": "RKPK",
            "target_date": "2026-06-26",
        }
    ]


def test_official_observation_request_plan_dedupes_archive_pending_markets_and_flags_unsupported_sources():
    records = []
    for station_code, source, city in (
        ("LTAC", "metar", "ankara"),
        ("LTFM", "noaa", "istanbul"),
        ("UUWW", "metar", "moscow"),
        ("EGLC", "metar", "london"),
    ):
        for index in range(11):
            records.append(
                {
                    "market_slug": f"highest-temperature-in-{city}-on-june-27-2026-{index}",
                    "city": city,
                    "target_date": "2026-06-27",
                    "settlement_station_code": station_code,
                    "settlement_source": source,
                    "settlement_spec": {
                        "station_code": station_code,
                        "settlement_source": source,
                        "target_date": "2026-06-27",
                        "unit": "C",
                    },
                }
            )

    plan = build_official_observation_request_plan(
        records,
        expected_reuse_market_count=44,
        max_requests=10,
    )

    assert plan["request_count"] == 4
    assert plan["supported_request_count"] == 4
    assert plan["unsupported_request_count"] == 0
    assert plan["expected_reuse_market_count"] == 44
    by_key = {
        (row["station_code"], row["settlement_source"], row["target_date"]): row
        for row in plan["by_station_source_date"]
    }
    assert by_key[("LTAC", "metar", "2026-06-27")]["market_count"] == 11
    assert by_key[("LTFM", "noaa", "2026-06-27")]["supported"] is True
    assert by_key[("LTFM", "noaa", "2026-06-27")]["gap_reason"] is None
    assert by_key[("LTFM", "noaa", "2026-06-27")]["group_state"] == "official_source_supported"
    assert by_key[("LTFM", "noaa", "2026-06-27")]["counts_for_live_gate"] is True
    assert by_key[("LTFM", "noaa", "2026-06-27")]["calibration_excluded_reason"] is None
    requests = {
        (row["station_code"], row["settlement_source"]): row
        for row in plan["requests"]
    }
    assert requests[("LTAC", "metar")]["supported_external_method"] == "aviationweather_metar_recent_72h"
    assert requests[("LTFM", "noaa")]["supported_external_method"] == "aviationweather_noaa_station_recent_72h"
    assert requests[("LTFM", "noaa")]["gap_reason"] is None
    assert requests[("LTFM", "noaa")]["group_state"] == "official_source_supported"
    assert requests[("LTFM", "noaa")]["counts_for_live_gate"] is True
    assert requests[("LTFM", "noaa")]["calibration_excluded_reason"] is None


def test_official_value_supplement_can_make_closed_replay_seed_settlement_truth_complete():
    report = build_official_value_backfill_report(
        [_record()],
        repository=EmptyRepository(),
        collector=FakeWundergroundCollector(),
        fetch_external=True,
    )

    assert report["ready_count"] == 1
    supplemented = apply_official_value_supplements(
        [_record()],
        supplements=report["supplements"],
    )
    assert supplemented[0]["official_final_value"] == 31.2
    assert supplemented[0]["official_value_supplement_applied"] is True

    seed_report = build_closed_replay_seed_report(
        [_record()],
        official_value_supplements=report["supplements"],
    )
    assert seed_report["hard_conclusion"] == "closed_replay_seed_ready"
    assert seed_report["missing_official_final_value_count"] == 0
    assert seed_report["official_value_supplemented_market_count"] == 1
    assert seed_report["seeds"][0]["official_final_value"] == 31.2


def test_load_official_value_supplements_accepts_single_jsonl_row(tmp_path):
    path = tmp_path / "supplements.jsonl"
    path.write_text(
        '{"status":"ready","market_id":"market-busan-1","official_final_value":31.2}\n',
        encoding="utf-8",
    )

    rows = load_official_value_supplements(path)

    assert rows == [
        {
            "status": "ready",
            "market_id": "market-busan-1",
            "official_final_value": 31.2,
        }
    ]


def test_official_value_supplements_prefer_slug_when_market_id_is_shared():
    records = [
        _record(
            market_id="shared-event-id",
            market_slug="highest-temperature-in-busan-on-june-26-2026-25c",
        ),
        _record(
            market_id="shared-event-id",
            market_slug="highest-temperature-in-busan-on-june-26-2026-26c",
        ),
    ]
    supplemented = apply_official_value_supplements(
        records,
        supplements=[
            {
                "status": "ready",
                "market_id": "shared-event-id",
                "market_slug": "highest-temperature-in-busan-on-june-26-2026-25c",
                "official_final_value": 25.0,
            },
            {
                "status": "ready",
                "market_id": "shared-event-id",
                "market_slug": "highest-temperature-in-busan-on-june-26-2026-26c",
                "official_final_value": 26.0,
            },
        ],
    )

    assert [row["official_final_value"] for row in supplemented] == [25.0, 26.0]
