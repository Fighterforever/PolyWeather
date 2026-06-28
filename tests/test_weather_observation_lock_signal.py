from __future__ import annotations

from src.trading.weather_observation_lock_signal import (
    build_market_side_book_pair_index,
    build_observation_lock_signal_report,
)
from src.weather.weather_observations import OfficialIntradayObservationRepository, detect_station_observation_anomalies


def _row(
    *,
    bucket_type: str = "ge",
    threshold: float = 23.0,
    upper_threshold: float | None = None,
    best_bid: float = 0.30,
    best_ask: float = 0.40,
    spread: float = 0.02,
    ask_depth: float = 10.0,
    bid_depth: float = 10.0,
    station: str = "UUWW",
    source: str = "metar",
    side: str = "yes",
    token_id: str = "yes-token",
    market_slug: str | None = None,
):
    return {
        "market_slug": market_slug or f"highest-temperature-test-{bucket_type}",
        "token_id": token_id,
        "side": side,
        "bucket_type": bucket_type,
        "threshold": threshold,
        "upper_threshold": upper_threshold,
        "target_date": "2026-06-29",
        "settlement_station_code": station,
        "settlement_source": source,
        "market_close_time": "2026-06-29T12:00:00Z",
        "observation_window_end_time": "2026-06-29T20:59:59Z",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "ask_depth_usdc_3c": ask_depth,
        "bid_depth_usdc_3c": bid_depth,
    }


def _obs(value: float, *, observed_at: str = "2026-06-29T10:00:00Z", source: str = "metar"):
    return {
        "source": source,
        "snapshot_type": "observation",
        "station_code": "UUWW",
        "target_date": "2026-06-29",
        "available_at": observed_at,
        "observed_at": observed_at,
        "payload": {
            "temperature_c": value,
            "official_source_flag": True,
        },
    }


def _first(report):
    return report["rows"][0]


def _report(rows, observations):
    return build_observation_lock_signal_report(
        rows,
        observations=observations,
        generated_at="2026-06-29T10:06:00Z",
    )


def test_ge_bucket_locks_yes_when_current_high_crosses_threshold():
    report = _report([_row(threshold=23, best_ask=0.40)], [_obs(24)])

    row = _first(report)
    assert row["lock_state"] == "ge_yes_locked"
    assert row["locked_side"] == "YES"
    assert row["decision"] == "candidate"
    assert row["executable_edge"] == 0.595


def test_le_bucket_current_high_above_threshold_locks_no_side():
    report = _report([_row(bucket_type="le", threshold=24, best_bid=0.20)], [_obs(28)])

    row = _first(report)
    assert row["lock_state"] == "le_yes_dead_no_locked"
    assert row["locked_side"] == "NO"
    assert row["price_semantics"] == "synthetic_no_from_yes_bid"
    assert row["q_effective"] == 0.8
    assert row["synthetic_price_diagnostic_only"] is True


def test_ge_bucket_not_locked_below_threshold():
    report = _report([_row(bucket_type="ge", threshold=23)], [_obs(22)])

    row = _first(report)
    assert row["lock_state"] == "ge_not_locked"
    assert row["decision"] == "reject"
    assert "observation_not_locked" in row["blockers"]


def test_eq_yes_prediction_remains_forbidden():
    report = _report([_row(bucket_type="eq", threshold=27, best_bid=0.20)], [_obs(27)])

    row = _first(report)
    assert row["lock_state"] == "eq_not_locked"
    assert row["decision"] == "shadow"
    assert row["eq_yes_prediction_forbidden"] is True
    assert row["eq_yes_prediction_status"] == "forbidden_live"
    assert "exact_yes_prediction_unstable" in row["blockers"]


def test_eq_dead_no_lock_can_be_paper_candidate():
    rows = [
        _row(bucket_type="eq", threshold=27, side="yes", token_id="yes-token", market_slug="eq-market"),
        _row(bucket_type="eq", threshold=27, side="no", token_id="no-token", market_slug="eq-market", best_ask=0.8),
    ]
    report = _report(rows, [_obs(28)])
    row = [item for item in report["rows"] if item["token_id"] == "no-token"][0]

    assert row["lock_state"] == "eq_yes_dead_no_locked"
    assert row["locked_side"] == "NO"
    assert row["strategy_id"] == "eq_dead_no_lock"
    assert row["decision"] == "candidate"
    assert row["exact_dead_no_lock_candidate"] is True
    assert row["eq_yes_prediction_forbidden"] is False
    assert row["live_gate_excluded_reason"] == "exact_dead_no_needs_forward_evidence"


def test_eq_dead_no_lock_requires_direct_no_ask():
    report = _report([_row(bucket_type="eq", threshold=27, side="yes", token_id="yes-token", best_bid=0.2)], [_obs(28)])
    row = _first(report)

    assert row["lock_state"] == "eq_yes_dead_no_locked"
    assert row["locked_side"] == "NO"
    assert row["strategy_id"] == "eq_dead_no_lock"
    assert row["decision"] == "watch"
    assert "eq_dead_no_requires_direct_no_ask" in row["blockers"]


def test_eq_dead_no_lock_not_counted_for_live_gate():
    rows = [
        _row(bucket_type="eq", threshold=27, side="yes", token_id="yes-token", market_slug="eq-market"),
        _row(bucket_type="eq", threshold=27, side="no", token_id="no-token", market_slug="eq-market", best_ask=0.8),
    ]
    report = _report(rows, [_obs(28)])
    row = [item for item in report["rows"] if item["token_id"] == "no-token"][0]

    assert row["decision"] == "candidate"
    assert row["counts_for_live_gate"] is False
    assert row["live_order_path"] is False
    assert report["counts_for_live_gate"] is False
    assert report["live_order_path"] is False


def test_dust_price_locked_signal_is_shadow_only():
    report = _report([_row(bucket_type="ge", threshold=23, best_ask=0.001)], [_obs(24)])

    row = _first(report)
    assert row["lock_state"] == "ge_yes_locked"
    assert row["price_bucket"] == "price_lt_0_005"
    assert row["decision"] == "shadow"
    assert "dust_price_bucket_not_alpha" in row["blockers"]


def test_observation_anomaly_blocks_candidate():
    observations = [
        _obs(20, observed_at="2026-06-29T10:00:00Z"),
        _obs(33, observed_at="2026-06-29T10:05:00Z"),
    ]
    assert detect_station_observation_anomalies(observations) == ["sudden_temperature_spike"]

    report = _report([_row(bucket_type="ge", threshold=23)], observations)
    row = _first(report)
    assert row["lock_state"] == "ge_yes_locked"
    assert row["decision"] == "watch"
    assert "blocked_by_observation_anomaly" in row["blockers"]


def test_non_dust_locked_signal_can_candidate_from_repository(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "intraday.jsonl")
    repo.append(
        [
            {
                **_obs(24, observed_at="2026-06-29T10:00:00Z"),
                "source": "aviationweather_metar_recent_72h",
                "settlement_source": "metar",
                "target_date_local": "2026-06-29",
                "temperature_c": 24,
                "quality_flags": [],
                "anomaly_flags": [],
            }
        ]
    )

    report = build_observation_lock_signal_report(
        [_row(bucket_type="ge", threshold=23, best_ask=0.40)],
        intraday_repository=repo,
        generated_at="2026-06-29T10:06:00Z",
    )

    row = _first(report)
    assert row["lock_state"] == "ge_yes_locked"
    assert row["decision"] == "candidate"
    assert row["latest_available_at"] == "2026-06-29T10:00:00Z"
    assert report["summary"]["candidate_count"] == 1
    assert report["summary"]["intraday_visible_station_count"] == 1


def test_no_intraday_blocks_candidate_from_repository(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "intraday.jsonl")

    report = build_observation_lock_signal_report(
        [_row(bucket_type="ge", threshold=23, best_ask=0.40)],
        intraday_repository=repo,
        generated_at="2026-06-29T10:06:00Z",
    )

    row = _first(report)
    assert row["lock_state"] == "missing_intraday_observation"
    assert row["decision"] == "reject"
    assert report["summary"]["missing_intraday_count"] == 1


def test_no_locked_uses_direct_no_token_ask():
    rows = [
        _row(bucket_type="le", threshold=24, best_bid=0.2, best_ask=0.3, side="yes", token_id="yes-token"),
        _row(bucket_type="le", threshold=24, best_bid=0.7, best_ask=0.8, side="no", token_id="no-token"),
    ]
    report = _report(rows, [_obs(28)])
    no_row = [row for row in report["rows"] if row["token_id"] == "no-token"][0]

    assert no_row["lock_state"] == "le_yes_dead_no_locked"
    assert no_row["locked_side"] == "NO"
    assert no_row["locked_side_token_id"] == "no-token"
    assert no_row["executable_price_source_type"] == "direct_locked_side_book"
    assert no_row["q_effective"] == 0.8
    assert no_row["current_row_is_locked_side_token"] is True


def test_no_locked_synthetic_from_yes_bid_is_diagnostic_only():
    report = _report([_row(bucket_type="le", threshold=24, best_bid=0.2, side="yes")], [_obs(28)])
    row = _first(report)

    assert row["executable_price_source_type"] == "synthetic_from_opposite_bid"
    assert row["synthetic_price_diagnostic_only"] is True
    assert "synthetic_no_price_diagnostic_only" in row["blockers"]
    assert row["decision"] == "watch"


def test_yes_locked_uses_yes_token_ask():
    report = _report([_row(bucket_type="ge", threshold=23, best_ask=0.4, side="yes", token_id="yes-token")], [_obs(24)])
    row = _first(report)

    assert row["locked_side"] == "YES"
    assert row["locked_side_token_id"] == "yes-token"
    assert row["executable_price_source_type"] == "direct_locked_side_book"
    assert row["q_effective"] == 0.4


def test_missing_locked_side_book_blocks_candidate_with_reason():
    report = _report([_row(bucket_type="le", threshold=24, best_bid=None, best_ask=0.3, side="yes")], [_obs(28)])
    row = _first(report)

    assert row["locked_side"] == "NO"
    assert row["executable_price_source_type"] == "missing"
    assert "missing_locked_side_orderbook" in row["blockers"]
    assert row["decision"] == "watch"


def test_market_side_orderbook_index_pairs_yes_no_tokens():
    rows = [
        _row(bucket_type="le", threshold=24, side="yes", token_id="yes-token", best_ask=0.3),
        _row(bucket_type="le", threshold=24, side="no", token_id="no-token", best_ask=0.8),
    ]
    report = _report(rows, [_obs(28)])

    assert report["summary"]["direct_locked_side_book_count"] == 2
    assert {row["locked_side_token_id"] for row in report["rows"]} == {"no-token"}


def test_market_side_book_pair_indexes_yes_no():
    rows = [
        _row(side="yes", token_id="yes-token", market_slug="market-1", best_bid=0.1, best_ask=0.2),
        _row(side="no", token_id="no-token", market_slug="market-1", best_bid=0.8, best_ask=0.9),
    ]
    pair = build_market_side_book_pair_index(rows)["market-1"]

    assert pair["market_slug"] == "market-1"
    assert pair["yes_token_id"] == "yes-token"
    assert pair["no_token_id"] == "no-token"
    assert pair["yes_best_bid"] == 0.1
    assert pair["no_best_ask"] == 0.9
    assert pair["pair_complete"] is True
    assert pair["pair_gap_reason"] is None


def test_market_side_book_pair_reports_missing_counterpart():
    pair = build_market_side_book_pair_index([_row(side="yes", token_id="yes-token", market_slug="market-1")])[
        "market-1"
    ]

    assert pair["yes_token_id"] == "yes-token"
    assert pair["no_token_id"] is None
    assert pair["pair_complete"] is False
    assert pair["pair_gap_reason"] == "missing_counterpart_token_book"


def test_market_side_book_pair_preserves_depth_ladders():
    rows = [
        {
            **_row(side="yes", token_id="yes-token", market_slug="market-1"),
            "order_book": {
                "token_id": "yes-token",
                "best_bid": 0.1,
                "best_ask": 0.2,
                "bids": [{"price": 0.1, "size": 4}, {"price": 0.09, "size": 3}],
                "asks": [{"price": 0.2, "size": 5}],
            },
        },
        {
            **_row(side="no", token_id="no-token", market_slug="market-1"),
            "order_book": {
                "token_id": "no-token",
                "best_bid": 0.7,
                "best_ask": 0.8,
                "bids": [{"price": 0.7, "size": 6}],
                "asks": [{"price": 0.8, "size": 7}],
            },
        },
    ]
    pair = build_market_side_book_pair_index(rows)["market-1"]

    assert pair["yes_bid_ladder"] == [{"price": 0.1, "size": 4.0}, {"price": 0.09, "size": 3.0}]
    assert pair["yes_ask_ladder"] == [{"price": 0.2, "size": 5.0}]
    assert pair["no_bid_ladder"] == [{"price": 0.7, "size": 6.0}]
    assert pair["no_ask_ladder"] == [{"price": 0.8, "size": 7.0}]


def test_dead_side_bid_capture_for_locked_no_uses_yes_bid():
    rows = [
        _row(bucket_type="le", threshold=24, side="yes", token_id="yes-token", best_bid=0.02, bid_depth=10.0),
        _row(bucket_type="le", threshold=24, side="no", token_id="no-token", best_bid=0.98, best_ask=None),
    ]
    report = _report(rows, [_obs(28)])
    row = [item for item in report["rows"] if item["token_id"] == "no-token"][0]

    assert row["locked_side"] == "NO"
    assert row["dead_side"] == "YES"
    assert row["dead_side_token_id"] == "yes-token"
    assert row["dead_side_best_bid"] == 0.02
    assert row["dead_side_capture_edge"] == 0.015
    assert row["dead_side_capture_candidate"] is True


def test_dead_side_bid_capture_for_locked_yes_uses_no_bid():
    rows = [
        _row(bucket_type="ge", threshold=23, side="yes", token_id="yes-token", best_bid=0.98, best_ask=None),
        _row(bucket_type="ge", threshold=23, side="no", token_id="no-token", best_bid=0.03, bid_depth=10.0),
    ]
    report = _report(rows, [_obs(24)])
    row = [item for item in report["rows"] if item["token_id"] == "yes-token"][0]

    assert row["locked_side"] == "YES"
    assert row["dead_side"] == "NO"
    assert row["dead_side_token_id"] == "no-token"
    assert row["dead_side_best_bid"] == 0.03
    assert row["dead_side_capture_edge"] == 0.025
    assert row["dead_side_capture_candidate"] is True


def test_dead_side_bid_capture_requires_bid_depth():
    rows = [
        _row(bucket_type="le", threshold=24, side="yes", token_id="yes-token", best_bid=0.02, bid_depth=0.0),
        _row(bucket_type="le", threshold=24, side="no", token_id="no-token", best_bid=0.98, best_ask=None),
    ]
    report = _report(rows, [_obs(28)])
    row = [item for item in report["rows"] if item["token_id"] == "no-token"][0]

    assert row["dead_side_capture_candidate"] is False
    assert "dead_side_bid_depth_too_low" in row["dead_side_capture_blockers"]


def test_dead_side_bid_capture_is_diagnostic_only():
    rows = [
        _row(bucket_type="le", threshold=24, side="yes", token_id="yes-token", best_bid=0.02, bid_depth=10.0),
        _row(bucket_type="le", threshold=24, side="no", token_id="no-token", best_bid=0.98, best_ask=None),
    ]
    report = _report(rows, [_obs(28)])
    row = [item for item in report["rows"] if item["dead_side_capture_candidate"]][0]

    assert row["execution_mode"] == "dead_side_bid_capture"
    assert row["execution_mode_status"] == "diagnostic_only_until_ctf_split_merge_supported"
    assert row["dead_side_capture_diagnostic_only"] is True
    assert row["dead_side_capture_counts_for_live_gate"] is False
    assert row["dead_side_capture_live_gate_excluded"] is True


def test_dead_side_capture_not_live_eligible():
    rows = [
        _row(bucket_type="le", threshold=24, side="yes", token_id="yes-token", best_bid=0.02, bid_depth=10.0),
        _row(bucket_type="le", threshold=24, side="no", token_id="no-token", best_bid=0.98, best_ask=None),
    ]
    report = _report(rows, [_obs(28)])
    row = [item for item in report["rows"] if item["dead_side_capture_candidate"]][0]

    assert row["counts_for_live_gate"] is False
    assert row["live_order_path"] is False
    assert report["counts_for_live_gate"] is False
    assert report["live_order_path"] is False


def test_market_already_reflected_when_locked_side_bid_high_no_ask_no_dead_bid():
    rows = [
        _row(bucket_type="le", threshold=24, side="yes", token_id="yes-token", best_bid=0.0, bid_depth=0.0),
        _row(bucket_type="le", threshold=24, side="no", token_id="no-token", best_bid=0.999, best_ask=None),
    ]
    report = _report(rows, [_obs(28)])
    row = [item for item in report["rows"] if item["token_id"] == "no-token"][0]

    assert row["market_reflection_state"] == "market_already_reflected_lock"


def test_market_not_reflected_when_dead_side_bid_positive():
    rows = [
        _row(bucket_type="le", threshold=24, side="yes", token_id="yes-token", best_bid=0.02, bid_depth=10.0),
        _row(bucket_type="le", threshold=24, side="no", token_id="no-token", best_bid=0.999, best_ask=None),
    ]
    report = _report(rows, [_obs(28)])
    row = [item for item in report["rows"] if item["token_id"] == "no-token"][0]

    assert row["market_reflection_state"] != "market_already_reflected_lock"


def test_stale_but_crossed_ge_lock_is_warning_not_blocker():
    report = build_observation_lock_signal_report(
        [_row(bucket_type="ge", threshold=23, best_ask=0.4)],
        observations=[_obs(24, observed_at="2026-06-29T09:40:00Z")],
        generated_at="2026-06-29T10:06:00Z",
    )
    row = _first(report)

    assert row["lock_is_immutable"] is True
    assert row["freshness_status"] == "stale_warning"
    assert row["freshness_warning"] == "stale_intraday_observation"
    assert row["freshness_blocker"] is None
    assert "stale_intraday_observation" not in row["blockers"]


def test_stale_but_crossed_le_lock_is_warning_not_blocker():
    rows = [
        _row(bucket_type="le", threshold=24, side="yes", token_id="yes-token", best_bid=0.2),
        _row(bucket_type="le", threshold=24, side="no", token_id="no-token", best_ask=0.8),
    ]
    report = build_observation_lock_signal_report(
        rows,
        observations=[_obs(28, observed_at="2026-06-29T09:40:00Z")],
        generated_at="2026-06-29T10:06:00Z",
    )
    no_row = [row for row in report["rows"] if row["token_id"] == "no-token"][0]

    assert no_row["lock_is_immutable"] is True
    assert no_row["freshness_status"] == "stale_warning"
    assert no_row["freshness_blocker"] is None
    assert "stale_intraday_observation" not in no_row["blockers"]


def test_stale_not_locked_signal_still_blocked():
    report = build_observation_lock_signal_report(
        [_row(bucket_type="ge", threshold=23)],
        observations=[_obs(22, observed_at="2026-06-29T09:40:00Z")],
        generated_at="2026-06-29T10:06:00Z",
    )
    row = _first(report)

    assert row["lock_is_immutable"] is False
    assert row["freshness_status"] == "stale_blocker"
    assert row["freshness_blocker"] == "stale_intraday_observation"
    assert "stale_intraday_observation" in row["blockers"]


def test_wrong_target_date_observation_blocks_lock():
    wrong_date_obs = {**_obs(24), "target_date": "2026-06-28", "target_date_local": "2026-06-28"}
    report = build_observation_lock_signal_report(
        [_row(bucket_type="ge", threshold=23)],
        observations=[wrong_date_obs],
        generated_at="2026-06-29T10:06:00Z",
    )
    row = _first(report)

    assert row["observation_target_date_valid"] is False
    assert row["freshness_blocker"] == "wrong_observation_target_date"
    assert "wrong_observation_target_date" in row["blockers"]


def test_future_available_at_blocks_lock():
    future_obs = _obs(24, observed_at="2026-06-29T10:10:00Z")
    report = build_observation_lock_signal_report(
        [_row(bucket_type="ge", threshold=23)],
        observations=[future_obs],
        generated_at="2026-06-29T10:06:00Z",
    )
    row = _first(report)

    assert row["freshness_blocker"] == "future_available_at"
    assert "future_available_at" in row["blockers"]
