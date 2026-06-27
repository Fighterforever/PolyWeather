from __future__ import annotations

from src.trading.polyweather_model_rows import (
    analysis_payload_to_model_rows,
    build_analysis_model_payload_for_targets,
    merge_scan_model_payloads,
)


def _analysis_payload():
    return {
        "display_name": "Seoul",
        "local_date": "2026-06-27",
        "local_time": "14:00",
        "temp_symbol": "°C",
        "deb": {"prediction": 28.2},
        "probabilities": {
            "engine": "legacy",
            "distribution_all": [
                {"value": 27, "probability": 0.30},
                {"value": 28, "probability": 0.45},
                {"value": 29, "probability": 0.25},
            ],
        },
        "multi_model_daily": {
            "2026-06-28": {
                "deb": {"prediction": 29.1},
                "models": {"ECMWF": 29.0},
                "probabilities_all": [
                    {"value": 28, "probability": 0.20},
                    {"value": 29, "probability": 0.50},
                    {"value": 30, "probability": 0.30},
                ],
            }
        },
    }


def test_analysis_payload_to_model_rows_uses_requested_future_date():
    rows = analysis_payload_to_model_rows(
        "seoul",
        _analysis_payload(),
        target_dates=["2026-06-28"],
    )

    assert len(rows) == 1
    assert rows[0]["city"] == "seoul"
    assert rows[0]["selected_date"] == "2026-06-28"
    assert rows[0]["deb_prediction"] == 29.1
    assert rows[0]["distribution_full"][1]["value"] == 29
    assert rows[0]["model_probability"] == 0.50


def test_analysis_payload_to_model_rows_falls_back_to_today_distribution():
    rows = analysis_payload_to_model_rows("seoul", _analysis_payload(), target_dates=[None])

    assert len(rows) == 1
    assert rows[0]["selected_date"] == "2026-06-27"
    assert rows[0]["probability_engine"] == "legacy"
    assert rows[0]["model_probability"] == 0.45


def test_build_analysis_model_payload_for_targets_uses_injected_runner():
    calls = []

    def fake_runner(city: str, force_refresh: bool, detail_mode: str):
        calls.append((city, force_refresh, detail_mode))
        return _analysis_payload()

    payload = build_analysis_model_payload_for_targets(
        {"seoul": ["2026-06-28"]},
        force_refresh=True,
        analysis_runner=fake_runner,
    )

    assert calls == [("seoul", True, "panel")]
    assert payload["status"] == "ready"
    assert payload["diagnostics"]["model_rows"] == 1
    assert payload["rows"][0]["selected_date"] == "2026-06-28"


def test_merge_scan_model_payloads_appends_fallback_rows():
    merged = merge_scan_model_payloads(
        {"snapshot_id": "scan", "status": "ready", "rows": [{"id": "scan-row"}]},
        {"snapshot_id": "fallback", "status": "ready", "rows": [{"id": "fallback-row"}]},
    )

    assert merged["status"] == "ready"
    assert [row["id"] for row in merged["rows"]] == ["scan-row", "fallback-row"]
    assert merged["diagnostics"]["primary_rows"] == 1
    assert merged["diagnostics"]["fallback_rows"] == 1
