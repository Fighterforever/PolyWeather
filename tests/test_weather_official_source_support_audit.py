from __future__ import annotations

import scripts.weather_official_source_support_audit as audit


def _active_row(*, source: str = "noaa", station: str = "LTFM", threshold: float = 26.0):
    return {
        "market_slug": "highest-temperature-in-istanbul-on-june-28-2026-26c",
        "bucket_type": "eq",
        "target_date": "2026-06-28",
        "city": "istanbul",
        "station_code": station,
        "settlement_source": source,
        "threshold": threshold,
        "settlement_spec": {
            "bucket_type": "eq",
            "target_date": "2026-06-28",
            "city": "istanbul",
            "station_code": station,
            "settlement_source": source,
            "threshold": threshold,
            "rounding": "integer_nearest",
        },
    }


def _closed_row(*, source: str = "noaa", station: str = "LTFM", threshold: float = 24.0, winning: str = "Yes"):
    return {
        "market_slug": "highest-temperature-in-istanbul-on-june-27-2026-24c",
        "bucket_type": "eq",
        "target_date": "2026-06-27",
        "city": "istanbul",
        "winning_outcome": winning,
        "settlement_spec": {
            "bucket_type": "eq",
            "target_date": "2026-06-27",
            "city": "istanbul",
            "station_code": station,
            "settlement_source": source,
            "threshold": threshold,
            "rounding": "integer_nearest",
        },
    }


def test_official_source_support_audit_supports_ltfm_noaa_without_reclassifying(monkeypatch):
    def fake_supplement(record, **kwargs):
        source = (record.get("settlement_spec") or {}).get("settlement_source")
        station = (record.get("settlement_spec") or {}).get("station_code")
        return {
            "status": "ready",
            "official_final_value": 24.0,
            "official_final_value_source": f"fake_{source}",
            "official_final_value_source_code": "noaa_station_observation" if source == "noaa" else "metar",
            "official_final_value_station_code": station,
        }

    monkeypatch.setattr(audit, "build_official_value_supplement", fake_supplement)
    report = audit.build_official_source_support_audit(
        active_rows=[_active_row()],
        closed_rows=[_closed_row()],
        fetch_external=True,
    )

    assert report["live_order_path"] is False
    assert report["unsupported_source_rows_by_source"] == [{"settlement_source": "noaa", "row_count": 1}]
    assert report["active_supported_station_count_before"] == 0
    assert report["active_supported_station_count_after"] == 1
    assert report["noaa_ltfm_support_verdict"] == "noaa_ltfm_supported_via_noaa_station_observation_adapter"
    row = next(item for item in report["source_audits"] if item["settlement_source"] == "noaa")
    assert row["station_code"] == "LTFM"
    assert row["can_fetch_via_noaa_endpoint"] is True
    assert row["official_truth_match_with_closed_backfill_if_available"] is True
    assert row["recommended_adapter"] == "noaa_station_observation"
    assert row["safe_to_support_source"] is True
    assert row["safe_to_reclassify_source"] is False


def test_official_source_support_audit_rejects_truth_mismatch(monkeypatch):
    def fake_supplement(record, **kwargs):
        return {
            "status": "ready",
            "official_final_value": 24.0,
            "official_final_value_source": "fake_noaa",
            "official_final_value_source_code": "noaa_station_observation",
        }

    monkeypatch.setattr(audit, "build_official_value_supplement", fake_supplement)
    report = audit.build_official_source_support_audit(
        active_rows=[_active_row()],
        closed_rows=[_closed_row(threshold=24.0, winning="No")],
        fetch_external=True,
    )

    row = next(item for item in report["source_audits"] if item["settlement_source"] == "noaa")
    assert row["official_truth_match_with_closed_backfill_if_available"] is False
    assert row["safe_to_support_source"] is False
