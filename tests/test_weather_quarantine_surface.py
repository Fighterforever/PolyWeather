from __future__ import annotations

from src.trading.weather_paper_journal import _append_jsonl
from src.trading.weather_quarantine_surface import build_quarantine_surface_report


def _fill(fill_id: str, *, city: str, side: str, bucket_label: str, risk_hits=None):
    return {
        "schema_version": "polyweather_weather_paper_fill.v1",
        "fill_id": fill_id,
        "recorded_at": "2026-06-27T00:00:00Z",
        "signal_bucket": "quarantine",
        "quarantine_reason": "risk_rule_only_reject",
        "city": city,
        "side": side,
        "bucket_label": bucket_label,
        "entry_price": 0.2,
        "entry_spread": 0.01,
        "market_slug": f"{city}-{fill_id}",
        "risk_rule_hits": risk_hits
        or [
            "negative_markout_rule:by_market_family:market_family=temperature",
            f"negative_markout_rule:by_city:city={city}",
        ],
    }


def _markout(fill_id: str, value: float):
    return {
        "schema_version": "polyweather_weather_paper_markout.v1",
        "fill_id": fill_id,
        "status": "marked",
        "markout_cents": value,
        "recorded_at": "2026-06-27T01:00:00Z",
    }


def _maker_quote(quote_id: str, *, city: str, side: str, bucket_label: str):
    return {
        "schema_version": "polyweather_weather_maker_quote.v1",
        "quote_id": quote_id,
        "city": city,
        "side": side,
        "bucket_label": bucket_label,
        "entry_spread": 0.01,
        "entry_ask": 0.2,
    }


def _maker_markout(quote_id: str, value: float):
    return {
        "schema_version": "polyweather_weather_maker_quote_markout.v1",
        "quote_id": quote_id,
        "status": "inferred_filled",
        "maker_markout_cents": value,
    }


def test_quarantine_surface_classifies_positive_and_negative_groups(tmp_path):
    _append_jsonl(
        tmp_path / "paper_fills.jsonl",
        [
            _fill("seoul-1", city="seoul", side="yes", bucket_label="= 25°C"),
            _fill("seoul-2", city="seoul", side="yes", bucket_label="= 26°C"),
            _fill("ankara-1", city="ankara", side="no", bucket_label="= 28°C"),
            _fill("ankara-2", city="ankara", side="no", bucket_label="= 29°C"),
        ],
    )
    _append_jsonl(
        tmp_path / "markouts.jsonl",
        [
            _markout("seoul-1", 1.2),
            _markout("seoul-2", 0.8),
            _markout("ankara-1", -0.4),
            _markout("ankara-2", -0.2),
        ],
    )

    report = build_quarantine_surface_report(
        quarantine_journal_dir=tmp_path,
        min_decision_count=2,
        min_promote_count=2,
        min_win_rate=0.55,
    )

    assert report["hard_conclusion"] == "quarantine_surface_ready_for_formal_paper_review"
    assert report["evidence"]["taker_record_count"] == 4
    by_city = report["groups"]["by_city"]
    seoul = next(row for row in by_city if row["dimensions"] == {"city": "seoul"})
    ankara = next(row for row in by_city if row["dimensions"] == {"city": "ankara"})
    assert seoul["action"] == "promote_to_formal_paper_review"
    assert seoul["mean_taker_markout_cents"] == 1.0
    assert ankara["action"] == "cooldown_exploration"
    assert ankara["mean_taker_markout_cents"] == -0.3
    assert any(row["action"] == "promote_to_formal_paper_review" for row in report["action_counts"])


def test_quarantine_surface_does_not_promote_taker_edge_with_adverse_maker_evidence(tmp_path):
    _append_jsonl(
        tmp_path / "paper_fills.jsonl",
        [
            _fill(f"london-{index}", city="london", side="no", bucket_label="= 25°C")
            for index in range(5)
        ],
    )
    _append_jsonl(
        tmp_path / "markouts.jsonl",
        [
            _markout("london-0", 1.0),
            _markout("london-1", 1.0),
            _markout("london-2", 1.0),
            _markout("london-3", -1.0),
            _markout("london-4", -2.0),
        ],
    )
    _append_jsonl(
        tmp_path / "maker_quotes.jsonl",
        [
            _maker_quote(f"quote-{index}", city="london", side="no", bucket_label="= 25°C")
            for index in range(5)
        ],
    )
    _append_jsonl(
        tmp_path / "maker_quote_markouts.jsonl",
        [_maker_markout(f"quote-{index}", -4.0) for index in range(5)],
    )

    report = build_quarantine_surface_report(
        quarantine_journal_dir=tmp_path,
        min_decision_count=5,
        min_promote_count=5,
        min_win_rate=0.55,
    )

    by_city_side = report["groups"]["by_city_and_side"]
    london_no = next(row for row in by_city_side if row["dimensions"] == {"city": "london", "side": "no"})
    assert london_no["taker_win_rate"] == 0.6
    assert london_no["mean_taker_markout_cents"] == 0.0
    assert london_no["mean_maker_markout_cents"] == -4.0
    assert london_no["action"] == "cooldown_exploration"
    assert report["promote_group_count"] == 0
    assert report["hard_conclusion"] == "quarantine_surface_currently_negative"
