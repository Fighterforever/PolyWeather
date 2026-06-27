from __future__ import annotations

from typing import Any, Dict

from src.trading.polymarket_readonly import (
    PolymarketReadonlyClient,
    _parse_json_list,
    build_polymarket_closed_weather_payload,
    city_temperature_queries,
    classify_weather_market_family,
    classify_weather_market_family_from_row,
    is_weather_like_event,
    summarize_order_book,
)


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self, payloads: Dict[str, Any]) -> None:
        self.payloads = payloads
        self.calls = []

    def get(self, url: str, params: Dict[str, Any], timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        for suffix, payload in self.payloads.items():
            if url.endswith(suffix):
                return FakeResponse(payload)
        return FakeResponse({"error": "missing fake payload"}, status_code=404)


def test_parse_json_list_accepts_gamma_stringified_lists():
    assert _parse_json_list('["Yes", "No"]') == ["Yes", "No"]
    assert _parse_json_list(["Yes"]) == ["Yes"]
    assert _parse_json_list("not-json") == []
    assert _parse_json_list(None) == []


def test_summarize_order_book_computes_executable_surface():
    summary = summarize_order_book(
        "token",
        {
            "market": "0xmarket",
            "timestamp": "123",
            "bids": [
                {"price": "0.41", "size": "20"},
                {"price": "0.39", "size": "100"},
            ],
            "asks": [
                {"price": "0.45", "size": "10"},
                {"price": "0.47", "size": "20"},
                {"price": "0.50", "size": "500"},
            ],
        },
    )

    assert summary.best_bid == 0.41
    assert summary.best_ask == 0.45
    assert summary.spread == 0.04
    assert summary.ask_depth_usdc_3c == 13.9
    assert summary.bid_depth_usdc_3c == 47.2
    assert summary.bid_ladder == [
        {"price": 0.41, "size": 20.0},
        {"price": 0.39, "size": 100.0},
    ]
    assert summary.ask_ladder[0] == {"price": 0.45, "size": 10.0}


def test_weather_filter_excludes_non_meteorological_matches():
    assert is_weather_like_event({"title": "Highest temperature in Seoul on June 27?"})
    assert not is_weather_like_event({"title": "Floyd Mayweather vs Manny Pacquiao 2"})
    assert not is_weather_like_event({"title": "How many major space weather events this week?"})


def test_classify_weather_market_family_distinguishes_supported_weather_types():
    assert classify_weather_market_family({"title": "Highest temperature in Seoul on June 27?"}) == "temperature"
    assert classify_weather_market_family({"title": "Will it rain in New York on Friday?"}) == "rain"
    assert classify_weather_market_family({"title": "Will any Category 4 hurricane make landfall?"}) == "hurricane"
    assert classify_weather_market_family({"title": "Air quality in London this week"}) == "air_quality"
    assert classify_weather_market_family({"title": "Floyd Mayweather fight"}) == "excluded"


def test_classify_weather_market_family_from_row_uses_question_and_slug():
    assert (
        classify_weather_market_family_from_row(
            {
                "question": "Will any Category 4 hurricane make landfall in the US before 2027?",
                "market_slug": "will-any-category-4-hurricane-make-landfall-in-the-us-in-before-2027",
            }
        )
        == "hurricane"
    )
    assert (
        classify_weather_market_family_from_row(
            {
                "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
            }
        )
        == "temperature"
    )


def test_city_temperature_queries_cover_monitored_cities():
    queries = city_temperature_queries(max_cities=3)

    assert queries
    assert all(query.startswith("highest temperature in ") for query in queries)


def test_market_to_signal_rows_maps_binary_gamma_market_without_network():
    client = PolymarketReadonlyClient(session=FakeSession({}))
    event = {
        "id": "event-1",
        "title": "Highest temperature in Seoul on June 27?",
        "slug": "highest-temperature-in-seoul-on-june-27-2026",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "endDate": "2026-06-27T12:00:00Z",
    }
    market = {
        "id": "market-1",
        "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
        "slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "endDate": "2026-06-27T12:00:00Z",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.31", "0.69"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "liquidityNum": 1200,
    }

    rows = client.market_to_signal_rows(
        event,
        market,
        include_order_books=False,
    )

    assert [row["side"] for row in rows] == ["yes", "no"]
    assert [row["token_id"] for row in rows] == ["yes-token", "no-token"]
    assert rows[0]["price"] == 0.31
    assert rows[0]["market_family"] == "temperature"
    assert rows[0]["settlement_spec_status"] == "supported"
    assert rows[0]["settlement_spec"]["station_code"] == "RKSI"
    assert rows[0]["market_bucket"]["bucket_type"] == "ge"
    assert rows[0]["bucket_label"] == ">= 28°C"
    assert rows[0]["tradable"] is True
    assert rows[0]["accepting_orders"] is True
    assert rows[0]["liquidity"] == 1200


def test_market_to_signal_rows_marks_not_accepting_orders_as_not_tradable():
    client = PolymarketReadonlyClient(session=FakeSession({}))
    event = {
        "id": "event-1",
        "title": "Highest temperature in Seoul on June 27?",
        "slug": "highest-temperature-in-seoul-on-june-27-2026",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "endDate": "2026-06-27T12:00:00Z",
    }
    market = {
        "id": "market-1",
        "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
        "slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": False,
        "endDate": "2026-06-27T12:00:00Z",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.31", "0.69"]',
        "clobTokenIds": '["yes-token", "no-token"]',
    }

    rows = client.market_to_signal_rows(event, market, include_order_books=False)

    assert rows[0]["tradable"] is False
    assert rows[0]["accepting_orders"] is False


def test_build_weather_market_payload_filters_and_reads_clob_books():
    event = {
        "id": "event-1",
        "title": "Highest temperature in Seoul on June 27?",
        "slug": "highest-temperature-in-seoul-on-june-27-2026",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "endDate": "2026-06-27T12:00:00Z",
        "markets": [
            {
                "id": "market-1",
                "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
                "slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "active": True,
                "closed": False,
                "enableOrderBook": True,
                "acceptingOrders": True,
                "endDate": "2026-06-27T12:00:00Z",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.31", "0.69"]',
                "clobTokenIds": '["yes-token", "no-token"]',
                "liquidityNum": 1200,
            }
        ],
    }
    session = FakeSession(
        {
            "/public-search": {"events": []},
            "/events": [event],
            "/book": {
                "market": "0xmarket",
                "timestamp": "123",
                "bids": [{"price": "0.30", "size": "100"}],
                "asks": [{"price": "0.32", "size": "100"}],
            },
        }
    )
    client = PolymarketReadonlyClient(session=session)

    payload = client.build_weather_market_payload(
        queries=("temperature",),
        row_limit=10,
        active_scan_limit=100,
        include_order_books=True,
    )

    assert payload["status"] == "ready"
    assert payload["diagnostics"]["weather_events"] == 1
    assert payload["diagnostics"]["markets_kept"] == 1
    assert payload["diagnostics"]["market_implied"]["threshold_cdf_group_count"] == 1
    assert len(payload["rows"]) == 2
    assert payload["rows"][0]["price"] == 0.32
    assert payload["rows"][0]["spread"] == 0.02
    assert payload["rows"][0]["settlement_spec_status"] == "supported"
    assert payload["rows"][0]["settlement_spec"]["station_code"] == "RKSI"


def test_build_weather_market_payload_skips_expired_and_not_accepting_markets():
    base_market = {
        "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
        "slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.31", "0.69"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "liquidityNum": 1200,
    }
    event = {
        "id": "event-1",
        "title": "Highest temperature in Seoul on June 27?",
        "slug": "highest-temperature-in-seoul-on-june-27-2026",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "markets": [
            {
                **base_market,
                "id": "expired-market",
                "endDate": "2000-01-01T00:00:00Z",
                "acceptingOrders": True,
            },
            {
                **base_market,
                "id": "not-accepting-market",
                "endDate": "2099-01-01T00:00:00Z",
                "acceptingOrders": False,
            },
            {
                **base_market,
                "id": "kept-market",
                "endDate": "2099-01-01T00:00:00Z",
                "acceptingOrders": True,
            },
        ],
    }
    session = FakeSession({"/public-search": {"events": []}, "/events": [event]})
    client = PolymarketReadonlyClient(session=session)

    payload = client.build_weather_market_payload(
        queries=("temperature",),
        row_limit=10,
        active_scan_limit=100,
        include_order_books=False,
    )

    assert payload["status"] == "ready"
    assert payload["diagnostics"]["markets_seen"] == 3
    assert payload["diagnostics"]["markets_kept"] == 1
    assert payload["diagnostics"]["expired_markets_skipped"] == 1
    assert payload["diagnostics"]["not_accepting_orders_markets_skipped"] == 1
    assert {row["market_id"] for row in payload["rows"]} == {"kept-market"}


def test_build_weather_market_payload_adds_city_temperature_searches():
    session = FakeSession(
        {
            "/public-search": {"events": []},
            "/events": [],
        }
    )
    client = PolymarketReadonlyClient(session=session)

    payload = client.build_weather_market_payload(
        queries=("temperature",),
        row_limit=10,
        active_scan_limit=100,
        include_order_books=False,
        include_city_temperature_queries=True,
        max_city_temperature_queries=2,
    )

    searched_queries = [
        call["params"]["q"]
        for call in session.calls
        if call["url"].endswith("/public-search")
    ]
    assert payload["diagnostics"]["city_temperature_query_count"] == 2
    assert "temperature" in searched_queries
    assert any(query.startswith("highest temperature in ") for query in searched_queries)


def test_build_closed_weather_market_payload_keeps_closed_weather_markets_only():
    closed_event = {
        "id": "event-closed",
        "title": "Highest temperature in NYC on June 25?",
        "slug": "highest-temperature-in-nyc-on-june-25-2026",
        "active": True,
        "closed": True,
        "enableOrderBook": True,
        "endDate": "2026-06-25T12:00:00Z",
        "markets": [
            {
                "id": "market-closed",
                "question": "Will the highest temperature in New York City be between 86-87°F on June 25?",
                "slug": "highest-temperature-in-nyc-on-june-25-2026-between-86-87f",
                "active": True,
                "closed": True,
                "endDate": "2026-06-25T12:00:00Z",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
                "clobTokenIds": '["yes-token", "no-token"]',
                "liquidityNum": 1200,
            }
        ],
    }
    open_event = {
        **closed_event,
        "id": "event-open",
        "closed": False,
        "markets": [{**closed_event["markets"][0], "id": "market-open", "closed": False}],
    }
    session = FakeSession({"/public-search": {"events": [closed_event, open_event]}})
    client = PolymarketReadonlyClient(session=session)

    payload = build_polymarket_closed_weather_payload(
        queries=("highest temperature in NYC",),
        include_city_temperature_queries=False,
        client=client,
    )

    assert payload["status"] == "ready"
    assert payload["diagnostics"]["closed_markets_kept"] == 1
    assert len(payload["rows"]) == 2
    assert {row["market_id"] for row in payload["rows"]} == {"market-closed"}
    assert payload["rows"][0]["closed"] is True
    assert payload["rows"][0]["settlement_spec_status"] == "supported"
    assert payload["rows"][0]["settlement_spec"]["station_code"] == "KLGA"
