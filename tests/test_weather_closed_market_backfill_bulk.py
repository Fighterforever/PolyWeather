from __future__ import annotations

import json

from src.trading.polymarket_readonly import PolymarketReadonlyClient
from src.trading.weather_closed_market_backfill_bulk import build_closed_weather_market_bulk_report


class FakeClosedClient(PolymarketReadonlyClient):
    def __init__(self, events):
        super().__init__()
        self._events = events

    def _get_json(self, base_url, path, params=None):  # noqa: ANN001
        return self._events


def _event(token_ids=None):
    return {
        "id": "evt-1",
        "slug": "highest-temperature-in-moscow-on-june-20-2026",
        "title": "Highest temperature in Moscow on June 20?",
        "closed": True,
        "active": False,
        "markets": [
            {
                "id": "mkt-1",
                "slug": "highest-temperature-in-moscow-on-june-20-2026-20corabove",
                "question": "Will the highest temperature in Moscow be 20C or above on June 20?",
                "closed": True,
                "active": False,
                "endDate": "2026-06-20T12:00:00Z",
                "outcomes": json.dumps(["Yes", "No"]),
                "outcomePrices": json.dumps([1, 0]),
                "clobTokenIds": json.dumps(token_ids or ["yes-token", "no-token"]),
            }
        ],
    }


def test_closed_weather_bulk_builds_supported_temperature_market():
    report = build_closed_weather_market_bulk_report(
        client=FakeClosedClient([_event()]),
        event_limit=1,
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["closed_temperature_market_count"] == 1
    assert report["replay_supported_market_count"] == 1
    record = report["records"][0]
    assert record["token_id_by_outcome"]["Yes"] == "yes-token"
    assert record["winning_outcome"] == "Yes"
    assert record["station_code"] == "UUWW"
    assert record["target_date"] == "2026-06-20"
    assert record["paper_only"] is True


def test_closed_weather_bulk_gaps_incomplete_token_map():
    report = build_closed_weather_market_bulk_report(
        client=FakeClosedClient([_event(token_ids=["yes-token"])]),
        event_limit=1,
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["replay_supported_market_count"] == 0
    assert any(gap["gap_reason"] == "incomplete_token_id_by_outcome" for gap in report["gaps"])
