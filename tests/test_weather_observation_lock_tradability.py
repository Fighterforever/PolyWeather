from __future__ import annotations

from src.trading.weather_observation_lock_tradability import build_observation_lock_tradability_report


def _signal(token="yes-token", side="YES", replay_time="2026-06-20T10:00:00Z"):
    return {
        "market_slug": "m",
        "token_id": token,
        "station_code": "UUWW",
        "target_date": "2026-06-20",
        "replay_time": replay_time,
        "lock_state": "ge_yes_locked" if side == "YES" else "le_yes_dead_no_locked",
        "locked_side": side,
        "official_current_high": 22,
        "threshold": 20,
        "bucket_type": "ge" if side == "YES" else "le",
        "time_to_close_minutes": 20,
    }


def _dataset():
    return [{"market_slug": "m", "payout": 1.0, "outcome": 1.0}]


def test_tradability_marks_price_history_non_executable():
    report = build_observation_lock_tradability_report(
        historical_replay_report={"signals": [_signal()]},
        price_history_rows=[
            {
                "token_id": "yes-token",
                "timestamp": "2026-06-20T09:59:00Z",
                "price": 0.4,
                "source": "polymarket_clob_prices_history",
                "executable_depth_available": False,
            }
        ],
        alpha_dataset_rows=_dataset(),
    )

    row = report["rows"][0]
    assert row["historical_price_available"] is True
    assert row["executable_depth_available"] is False
    assert row["can_compute_real_pnl"] is False
    assert row["can_count_as_pnl"] is False
    assert row["evidence_type"] == "approximate_price_only"


def test_tradability_computes_approx_edge_without_pnl():
    report = build_observation_lock_tradability_report(
        historical_replay_report={"signals": [_signal(side="NO")]},
        price_history_rows=[
            {
                "token_id": "yes-token",
                "timestamp": "2026-06-20T09:59:00Z",
                "price": 0.98,
                "source": "polymarket_clob_prices_history",
                "executable_depth_available": False,
            }
        ],
        alpha_dataset_rows=_dataset(),
    )

    row = report["rows"][0]
    assert round(row["approximate_price"], 6) == 0.02
    assert row["approximate_edge"] == 0.975
    assert row["payout"] == 0.0
    assert row["can_compute_real_pnl"] is False


def test_tradability_ranks_locked_signals():
    report = build_observation_lock_tradability_report(
        historical_replay_report={"signals": [_signal("a"), _signal("b")]},
        price_history_rows=[
            {"token_id": "a", "timestamp": "2026-06-20T09:59:00Z", "price": 0.8},
            {"token_id": "b", "timestamp": "2026-06-20T09:59:00Z", "price": 0.2},
        ],
        alpha_dataset_rows=[{"market_slug": "m", "payout": 1.0}],
    )

    assert report["rows"][0]["token_id"] == "b"
    assert report["rows"][0]["triage_rank"] == 1
    assert report["summary"]["price_available_count"] == 2


def test_tradability_excludes_dust_from_live_evidence():
    report = build_observation_lock_tradability_report(
        historical_replay_report={"signals": [_signal()]},
        price_history_rows=[{"token_id": "yes-token", "timestamp": "2026-06-20T09:59:00Z", "price": 0.001}],
        alpha_dataset_rows=_dataset(),
    )

    row = report["rows"][0]
    assert row["price_bucket"] == "price_lt_0_005"
    assert "dust_price_not_live_evidence" in row["data_gap_reason"]
    assert row["counts_for_live_gate"] is False


def test_tradability_outputs_ge_le_alpha_ranking():
    report = build_observation_lock_tradability_report(
        historical_replay_report={"signals": [_signal("a", side="NO"), _signal("b", side="YES")]},
        price_history_rows=[
            {"token_id": "a", "timestamp": "2026-06-20T09:59:00Z", "price": 0.98},
            {"token_id": "b", "timestamp": "2026-06-20T09:59:00Z", "price": 0.2},
        ],
        alpha_dataset_rows=[{"market_slug": "m", "payout": 1.0}],
    )

    assert report["summary"]["ge_le_locked_signal_count"] == 2
    assert report["summary"]["ge_le_price_available_count"] == 2
    assert report["summary"]["ge_le_approx_positive_edge_count"] == 2
    assert len(report["alpha_eligible_ge_le_locked_signal_triage"]) == 2
    assert report["alpha_eligible_ge_le_locked_signal_triage"][0]["ge_le_triage_rank"] == 1


def test_tradability_excludes_eq_from_alpha_ranking():
    eq_signal = _signal("eq-token")
    eq_signal["bucket_type"] = "eq"
    eq_signal["lock_state"] = "eq_yes_dead_no_locked"
    eq_signal["locked_side"] = "NO"
    report = build_observation_lock_tradability_report(
        historical_replay_report={"signals": [eq_signal, _signal("le-token", side="NO")]},
        price_history_rows=[
            {"token_id": "eq-token", "timestamp": "2026-06-20T09:59:00Z", "price": 0.1},
            {"token_id": "le-token", "timestamp": "2026-06-20T09:59:00Z", "price": 0.98},
        ],
        alpha_dataset_rows=[{"market_slug": "m", "payout": 1.0}],
    )

    alpha_rows = report["alpha_eligible_ge_le_locked_signal_triage"]
    assert [row["token_id"] for row in alpha_rows] == ["le-token"]
    assert report["summary"]["ge_le_locked_signal_count"] == 1
    assert all(row["bucket_type"] in {"ge", "le"} for row in alpha_rows)
