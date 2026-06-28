from __future__ import annotations

from src.trading.weather_observation_lock_journal import write_observation_lock_paper_journal
from src.trading.weather_paper_journal import load_jsonl


def _signal_row(**overrides):
    row = {
        "decision": "candidate",
        "market_slug": "highest-temperature-test",
        "token_id": "yes-token",
        "side": "YES",
        "locked_side": "YES",
        "lock_state": "ge_yes_locked",
        "bucket_type": "ge",
        "official_current_high": 24.0,
        "latest_observation_at": "2026-06-29T10:00:00Z",
        "latest_available_at": "2026-06-29T10:02:00Z",
        "q_effective": 0.40,
        "executable_edge": 0.595,
        "price_bucket": "price_ge_0_03",
        "orderbook_snapshot_id": "book-1",
        "settlement_spec": {"station_code": "UUWW"},
        "no_lookahead": True,
    }
    row.update(overrides)
    return row


def test_observation_lock_candidate_is_written_to_paper_journal(tmp_path):
    result = write_observation_lock_paper_journal(
        {"rows": [_signal_row()]},
        journal_dir=tmp_path,
        recorded_at="2026-06-29T10:03:00Z",
    )

    fills = load_jsonl(tmp_path / "fills.jsonl")
    assert result["fill_count"] == 1
    assert fills[0]["strategy_id"] == "observation_lock"
    assert fills[0]["market_slug"] == "highest-temperature-test"
    assert fills[0]["entry_price"] == 0.40
    assert fills[0]["no_lookahead"] is True
    assert fills[0]["paper_only"] is True
    assert fills[0]["counts_for_live_gate"] is False


def test_observation_lock_eq_or_dust_does_not_write_alpha_fill(tmp_path):
    result = write_observation_lock_paper_journal(
        {
            "rows": [
                _signal_row(decision="shadow", bucket_type="eq", price_bucket="price_ge_0_03"),
                _signal_row(decision="shadow", bucket_type="ge", price_bucket="price_lt_0_005"),
            ]
        },
        journal_dir=tmp_path,
        recorded_at="2026-06-29T10:03:00Z",
    )

    assert result["fill_count"] == 0
    assert load_jsonl(tmp_path / "fills.jsonl") == []
