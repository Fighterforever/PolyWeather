from __future__ import annotations

from src.trading.weather_maker_shadow_v2 import (
    build_maker_shadow_quotes,
    build_maker_shadow_v2_report,
    simulate_maker_shadow_fills,
)


def _row(**overrides):
    row = {
        "market_family": "temperature",
        "market_slug": "highest-temperature-in-moscow-on-june-30-2026-25corabove",
        "event_slug": "highest-temperature-in-moscow-on-june-30-2026",
        "token_id": "yes-token",
        "side": "yes",
        "best_bid": 0.48,
        "best_ask": 0.54,
        "spread": 0.06,
        "bid_depth": 10.0,
        "ask_depth": 10.0,
        "market_implied_de_vig_side_probability": 0.51,
        "market_bucket": {"bucket_type": "ge", "threshold": 25.0},
        "settlement_spec": {
            "station_code": "UUWW",
            "settlement_source": "metar",
            "target_date": "2026-06-30",
            "bucket_type": "ge",
            "threshold": 25.0,
        },
    }
    row.update(overrides)
    return row


def test_maker_shadow_quotes_inside_spread():
    quotes, reasons = build_maker_shadow_quotes([_row()], generated_at="2026-06-28T00:00:00Z", maker_margin=0.01)

    assert len(quotes) == 2
    assert {quote["quote_type"] for quote in quotes} == {"maker_bid", "maker_ask"}
    assert all(0.48 < quote["quote_price"] < 0.54 for quote in quotes)
    assert reasons == {}


def test_maker_shadow_cancels_on_metar_update():
    quotes, reasons = build_maker_shadow_quotes(
        [_row(metar_update_event=True, current_high_change_c=0.8)],
        generated_at="2026-06-28T00:00:00Z",
    )

    assert quotes == []
    assert reasons["metar_update_changed_current_high"] == 1


def test_maker_shadow_excludes_dust_and_eq():
    dust = _row(best_bid=0.001, best_ask=0.004, spread=0.003)
    eq = _row(market_bucket={"bucket_type": "eq", "threshold": 25.0}, settlement_spec={"bucket_type": "eq", "threshold": 25.0, "station_code": "UUWW", "settlement_source": "metar"})

    quotes, reasons = build_maker_shadow_quotes([dust, eq], generated_at="2026-06-28T00:00:00Z", min_spread=0.001)

    assert quotes == []
    assert reasons["dust_price"] == 1
    assert reasons["bucket_not_ge_le"] == 1


def test_maker_shadow_estimates_adverse_selection():
    previous, _ = build_maker_shadow_quotes([_row()], generated_at="2026-06-28T00:00:00Z", maker_margin=0.01)
    bid_quotes = [quote for quote in previous if quote["quote_type"] == "maker_bid"]
    later = _row(best_bid=0.46, best_ask=0.49, spread=0.03, market_implied_de_vig_side_probability=0.48)

    fills, markouts = simulate_maker_shadow_fills(
        bid_quotes,
        [later],
        generated_at="2026-06-28T00:01:00Z",
        estimated_rebate_cents=0.0,
    )

    assert len(fills) == 1
    assert fills[0]["inferred_fill"] is True
    assert markouts[0]["adverse_selection"] is True
    assert markouts[0]["pnl_without_rebate"] < 0


def test_maker_shadow_rebate_is_separate_from_core_pnl():
    previous, _ = build_maker_shadow_quotes([_row()], generated_at="2026-06-28T00:00:00Z", maker_margin=0.01)
    bid_quotes = [quote for quote in previous if quote["quote_type"] == "maker_bid"]
    later = _row(best_bid=0.46, best_ask=0.49, spread=0.03, market_implied_de_vig_side_probability=0.48)

    _, markouts = simulate_maker_shadow_fills(
        bid_quotes,
        [later],
        generated_at="2026-06-28T00:01:00Z",
        estimated_rebate_cents=0.25,
    )

    assert markouts[0]["pnl_with_rebate"] == markouts[0]["pnl_without_rebate"] + 0.25


def test_maker_shadow_not_live_eligible():
    report = build_maker_shadow_v2_report([_row()], generated_at="2026-06-28T00:00:00Z")

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["live_order_path"] is False
    assert all(quote["counts_for_live_gate"] is False for quote in report["quotes"])
