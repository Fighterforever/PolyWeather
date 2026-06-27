from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from src.data_collection.city_registry import CITY_REGISTRY


GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
SCHEMA_VERSION = "polyweather_polymarket_readonly_payload.v1"
DEFAULT_WEATHER_QUERIES = ("temperature", "rain", "hurricane", "air quality")

WEATHER_INCLUDE_RE = re.compile(
    r"("
    r"highest\s+temperature|"
    r"\btemperature\s+in\b|"
    r"\bwill\s+it\s+rain\b|"
    r"\brain\s+in\b|"
    r"\bprecipitation\b|"
    r"\brainfall\b|"
    r"\bsnowfall\b|"
    r"\bsnow\s+in\b|"
    r"\bhurricane\b|"
    r"\bheat\s+index\b|"
    r"\bair\s+quality\b|"
    r"\bhottest\s+years\b"
    r")",
    re.IGNORECASE,
)
WEATHER_EXCLUDE_RE = re.compile(r"(mayweather|space\s+weather)", re.IGNORECASE)


class PolymarketReadonlyError(RuntimeError):
    """Raised when a public Polymarket read endpoint cannot be queried."""


@dataclass(frozen=True)
class OrderBookSummary:
    token_id: str
    market: Optional[str]
    timestamp: Optional[str]
    best_bid: Optional[float]
    best_ask: Optional[float]
    spread: Optional[float]
    bid_depth_usdc_3c: float
    ask_depth_usdc_3c: float
    bid_levels: int
    ask_levels: int


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _market_text(event: Dict[str, Any], market: Optional[Dict[str, Any]] = None) -> str:
    market = market or {}
    return " ".join(
        str(value or "")
        for value in (
            event.get("title"),
            event.get("slug"),
            event.get("description"),
            market.get("question"),
            market.get("slug"),
            market.get("description"),
        )
    )


def _market_end_datetime(event: Dict[str, Any], market: Dict[str, Any]) -> Optional[datetime]:
    return _parse_utc_datetime(_first_text(market.get("endDate"), event.get("endDate")))


def is_weather_like_event(event: Dict[str, Any]) -> bool:
    text = _market_text(event)
    return bool(WEATHER_INCLUDE_RE.search(text)) and not bool(WEATHER_EXCLUDE_RE.search(text))


def is_weather_like_market(event: Dict[str, Any], market: Dict[str, Any]) -> bool:
    text = _market_text(event, market)
    return bool(WEATHER_INCLUDE_RE.search(text)) and not bool(WEATHER_EXCLUDE_RE.search(text))


def classify_weather_market_family(event: Dict[str, Any], market: Optional[Dict[str, Any]] = None) -> str:
    text = _market_text(event, market or {}).lower()
    search_text = re.sub(r"[-_]+", " ", text)
    if WEATHER_EXCLUDE_RE.search(search_text):
        return "excluded"
    if re.search(r"highest\s+temperature|\btemperature\s+in\b|\bheat\s+index\b", search_text):
        return "temperature"
    if re.search(r"\bwill\s+it\s+rain\b|\brain\s+in\b|\bprecipitation\b|\brainfall\b", search_text):
        return "rain"
    if re.search(r"\bsnowfall\b|\bsnow\s+in\b", search_text):
        return "snow"
    if re.search(r"\bhurricane\b", search_text):
        return "hurricane"
    if re.search(r"\bair\s+quality\b", search_text):
        return "air_quality"
    if re.search(r"\bhottest\s+years\b", search_text):
        return "climate"
    return "weather_other"


def classify_weather_market_family_from_row(row: Dict[str, Any]) -> str:
    event = {
        "title": _first_text(row.get("event_title"), row.get("question")),
        "slug": row.get("event_slug"),
        "description": row.get("event_description"),
    }
    market = {
        "question": row.get("question"),
        "slug": row.get("market_slug") or row.get("slug"),
        "description": row.get("description"),
    }
    return classify_weather_market_family(event, market)


def city_temperature_queries(*, max_cities: Optional[int] = None) -> List[str]:
    queries: List[str] = []
    for city_key, meta in CITY_REGISTRY.items():
        display_name = _first_text(meta.get("name"), city_key)
        if not display_name:
            continue
        queries.append(f"highest temperature in {display_name}")
        if max_cities is not None and len(queries) >= max(0, int(max_cities)):
            break
    return queries


def _book_levels(levels: Any) -> List[Tuple[float, float]]:
    parsed: List[Tuple[float, float]] = []
    if not isinstance(levels, list):
        return parsed
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = _safe_float(level.get("price"))
        size = _safe_float(level.get("size"))
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        parsed.append((price, size))
    return parsed


def _depth_usdc_near_price(
    levels: Sequence[Tuple[float, float]],
    *,
    reference: Optional[float],
    cents: float,
    side: str,
) -> float:
    if reference is None:
        return 0.0
    if side == "ask":
        selected = [(price, size) for price, size in levels if price <= reference + cents]
    else:
        selected = [(price, size) for price, size in levels if price >= reference - cents]
    return round(sum(price * size for price, size in selected), 6)


def summarize_order_book(token_id: str, payload: Dict[str, Any], *, depth_cents: float = 0.03) -> OrderBookSummary:
    bids = _book_levels(payload.get("bids"))
    asks = _book_levels(payload.get("asks"))
    best_bid = max((price for price, _ in bids), default=None)
    best_ask = min((price for price, _ in asks), default=None)
    spread = round(best_ask - best_bid, 6) if best_bid is not None and best_ask is not None else None
    return OrderBookSummary(
        token_id=str(token_id),
        market=payload.get("market"),
        timestamp=str(payload.get("timestamp") or "") or None,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        bid_depth_usdc_3c=_depth_usdc_near_price(
            bids,
            reference=best_bid,
            cents=depth_cents,
            side="bid",
        ),
        ask_depth_usdc_3c=_depth_usdc_near_price(
            asks,
            reference=best_ask,
            cents=depth_cents,
            side="ask",
        ),
        bid_levels=len(bids),
        ask_levels=len(asks),
    )


class PolymarketReadonlyClient:
    def __init__(
        self,
        *,
        gamma_base_url: str = GAMMA_BASE_URL,
        clob_base_url: str = CLOB_BASE_URL,
        timeout: float = 20.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.clob_base_url = clob_base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get_json(self, base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{base_url}{path}"
        try:
            response = self.session.get(url, params=params or {}, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise PolymarketReadonlyError(f"GET {url} failed: {exc}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise PolymarketReadonlyError(f"GET {url} returned non-JSON response") from exc

    def public_search_events(self, query: str, *, limit: int = 25) -> List[Dict[str, Any]]:
        payload = self._get_json(
            self.gamma_base_url,
            "/public-search",
            {"q": query, "limit": max(1, int(limit))},
        )
        events = payload.get("events") if isinstance(payload, dict) else []
        return [event for event in events if isinstance(event, dict)]

    def get_market_by_id(self, market_id: str) -> Dict[str, Any]:
        identifier = str(market_id or "").strip()
        if not identifier:
            raise PolymarketReadonlyError("missing market_id")
        payload = self._get_json(self.gamma_base_url, f"/markets/{identifier}", {})
        if not isinstance(payload, dict):
            raise PolymarketReadonlyError("Gamma /markets/{id} returned non-object response")
        return payload

    def find_market_by_slug(self, market_slug: str, *, limit: int = 5) -> Optional[Dict[str, Any]]:
        slug = str(market_slug or "").strip()
        if not slug:
            return None
        for event in self.public_search_events(slug, limit=limit):
            markets = event.get("markets") if isinstance(event.get("markets"), list) else []
            for market in markets:
                if isinstance(market, dict) and str(market.get("slug") or "").strip() == slug:
                    return market
        return None

    def active_events_by_offset(
        self,
        *,
        limit: int = 500,
        page_size: int = 100,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        events: List[Dict[str, Any]] = []
        errors: List[str] = []
        max_limit = max(0, int(limit))
        page_size = max(1, min(100, int(page_size)))
        for offset in range(0, max_limit, page_size):
            payload = self._get_json(
                self.gamma_base_url,
                "/events",
                {
                    "active": "true",
                    "closed": "false",
                    "limit": page_size,
                    "offset": offset,
                },
            )
            if not isinstance(payload, list):
                errors.append("active_events_non_list_response")
                break
            if not payload:
                break
            events.extend(event for event in payload if isinstance(event, dict))
            if len(payload) < page_size:
                break
        return events, errors

    def collect_weather_events(
        self,
        *,
        queries: Iterable[str],
        search_limit_per_query: int = 25,
        include_city_temperature_queries: bool = False,
        city_search_limit_per_query: int = 5,
        max_city_temperature_queries: Optional[int] = None,
        active_scan_limit: int = 500,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        by_key: Dict[str, Dict[str, Any]] = {}
        query_specs: List[Tuple[str, int]] = [
            (str(query or "").strip(), max(1, int(search_limit_per_query)))
            for query in queries
            if str(query or "").strip()
        ]
        city_queries = (
            city_temperature_queries(max_cities=max_city_temperature_queries)
            if include_city_temperature_queries
            else []
        )
        query_specs.extend(
            (query, max(1, int(city_search_limit_per_query)))
            for query in city_queries
        )
        diagnostics: Dict[str, Any] = {
            "search_queries": [],
            "city_temperature_query_count": len(city_queries),
            "raw_search_events": 0,
            "raw_active_events": 0,
            "weather_events": 0,
            "errors": [],
        }
        for query_text, query_limit in query_specs:
            if not query_text:
                continue
            diagnostics["search_queries"].append(query_text)
            try:
                events = self.public_search_events(query_text, limit=query_limit)
            except PolymarketReadonlyError as exc:
                diagnostics["errors"].append(str(exc))
                continue
            diagnostics["raw_search_events"] += len(events)
            for event in events:
                key = _first_text(event.get("id"), event.get("slug"))
                if key:
                    by_key[key] = event

        try:
            active_events, active_errors = self.active_events_by_offset(limit=active_scan_limit)
        except PolymarketReadonlyError as exc:
            active_events = []
            active_errors = [str(exc)]
        diagnostics["raw_active_events"] = len(active_events)
        diagnostics["errors"].extend(active_errors)
        for event in active_events:
            key = _first_text(event.get("id"), event.get("slug"))
            if key:
                by_key[key] = event

        filtered = []
        for event in by_key.values():
            if event.get("closed") is True:
                continue
            if _safe_bool(event.get("active")) is False:
                continue
            if is_weather_like_event(event):
                filtered.append(event)
        diagnostics["weather_events"] = len(filtered)
        return filtered, diagnostics

    def collect_weather_events_from_search(
        self,
        *,
        queries: Iterable[str],
        search_limit_per_query: int = 25,
        include_city_temperature_queries: bool = False,
        city_search_limit_per_query: int = 5,
        max_city_temperature_queries: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        by_key: Dict[str, Dict[str, Any]] = {}
        query_specs: List[Tuple[str, int]] = [
            (str(query or "").strip(), max(1, int(search_limit_per_query)))
            for query in queries
            if str(query or "").strip()
        ]
        city_queries = (
            city_temperature_queries(max_cities=max_city_temperature_queries)
            if include_city_temperature_queries
            else []
        )
        query_specs.extend(
            (query, max(1, int(city_search_limit_per_query)))
            for query in city_queries
        )
        diagnostics: Dict[str, Any] = {
            "search_queries": [],
            "city_temperature_query_count": len(city_queries),
            "raw_search_events": 0,
            "weather_events": 0,
            "closed_weather_events": 0,
            "open_weather_events": 0,
            "errors": [],
        }
        for query_text, query_limit in query_specs:
            if not query_text:
                continue
            diagnostics["search_queries"].append(query_text)
            try:
                events = self.public_search_events(query_text, limit=query_limit)
            except PolymarketReadonlyError as exc:
                diagnostics["errors"].append(str(exc))
                continue
            diagnostics["raw_search_events"] += len(events)
            for event in events:
                key = _first_text(event.get("id"), event.get("slug"))
                if key and is_weather_like_event(event):
                    by_key[key] = event

        filtered = list(by_key.values())
        diagnostics["weather_events"] = len(filtered)
        diagnostics["closed_weather_events"] = len(
            [event for event in filtered if event.get("closed") is True]
        )
        diagnostics["open_weather_events"] = len(filtered) - diagnostics["closed_weather_events"]
        return filtered, diagnostics

    def get_order_book(self, token_id: str) -> OrderBookSummary:
        token = str(token_id or "").strip()
        if not token:
            raise PolymarketReadonlyError("missing token_id")
        payload = self._get_json(self.clob_base_url, "/book", {"token_id": token})
        if not isinstance(payload, dict):
            raise PolymarketReadonlyError("CLOB /book returned non-object response")
        return summarize_order_book(token, payload)

    def build_weather_market_payload(
        self,
        *,
        queries: Iterable[str] = DEFAULT_WEATHER_QUERIES,
        row_limit: int = 50,
        search_limit_per_query: int = 25,
        include_city_temperature_queries: bool = True,
        city_search_limit_per_query: int = 5,
        max_city_temperature_queries: Optional[int] = None,
        active_scan_limit: int = 500,
        include_order_books: bool = True,
        exclude_expired_markets: bool = True,
        exclude_not_accepting_orders: bool = True,
    ) -> Dict[str, Any]:
        generated_at_dt = datetime.now(timezone.utc).replace(microsecond=0)
        generated_at = generated_at_dt.isoformat().replace("+00:00", "Z")
        events, diagnostics = self.collect_weather_events(
            queries=queries,
            search_limit_per_query=search_limit_per_query,
            include_city_temperature_queries=include_city_temperature_queries,
            city_search_limit_per_query=city_search_limit_per_query,
            max_city_temperature_queries=max_city_temperature_queries,
            active_scan_limit=active_scan_limit,
        )
        rows: List[Dict[str, Any]] = []
        order_book_cache: Dict[str, OrderBookSummary] = {}
        order_book_errors: Dict[str, str] = {}
        markets_seen = 0
        markets_kept = 0
        expired_markets_skipped = 0
        not_accepting_orders_markets_skipped = 0

        for event in events:
            markets = event.get("markets") if isinstance(event.get("markets"), list) else []
            for market in markets:
                if not isinstance(market, dict):
                    continue
                markets_seen += 1
                if market.get("closed") is True:
                    continue
                if _safe_bool(market.get("active")) is False:
                    continue
                accepting_orders = _safe_bool(market.get("acceptingOrders"))
                if exclude_not_accepting_orders and accepting_orders is False:
                    not_accepting_orders_markets_skipped += 1
                    continue
                end_dt = _market_end_datetime(event, market)
                if exclude_expired_markets and end_dt is not None and end_dt <= generated_at_dt:
                    expired_markets_skipped += 1
                    continue
                if not is_weather_like_market(event, market):
                    continue
                markets_kept += 1
                rows.extend(
                    self.market_to_signal_rows(
                        event,
                        market,
                        include_order_books=include_order_books,
                        order_book_cache=order_book_cache,
                        order_book_errors=order_book_errors,
                    )
                )
                if len(rows) >= row_limit:
                    rows = rows[:row_limit]
                    break
            if len(rows) >= row_limit:
                break

        diagnostics.update(
            {
                "markets_seen": markets_seen,
                "markets_kept": markets_kept,
                "expired_markets_skipped": expired_markets_skipped,
                "not_accepting_orders_markets_skipped": not_accepting_orders_markets_skipped,
                "rows": len(rows),
                "order_book_requests": len(order_book_cache) + len(order_book_errors),
                "order_book_errors": len(order_book_errors),
                "order_book_error_tokens": sorted(order_book_errors)[:10],
            }
        )
        status = "ready" if rows else "no_current_weather_signal"
        if diagnostics.get("errors") and not rows:
            status = "partial_error"
        return {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": f"polymarket-readonly-{generated_at}",
            "generated_at": generated_at,
            "status": status,
            "source": "polymarket_readonly",
            "rows": rows,
            "diagnostics": diagnostics,
        }

    def build_closed_weather_market_payload(
        self,
        *,
        queries: Iterable[str] = DEFAULT_WEATHER_QUERIES,
        row_limit: int = 100,
        search_limit_per_query: int = 25,
        include_city_temperature_queries: bool = True,
        city_search_limit_per_query: int = 5,
        max_city_temperature_queries: Optional[int] = None,
    ) -> Dict[str, Any]:
        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        events, diagnostics = self.collect_weather_events_from_search(
            queries=queries,
            search_limit_per_query=search_limit_per_query,
            include_city_temperature_queries=include_city_temperature_queries,
            city_search_limit_per_query=city_search_limit_per_query,
            max_city_temperature_queries=max_city_temperature_queries,
        )
        rows: List[Dict[str, Any]] = []
        markets_seen = 0
        markets_kept = 0

        for event in events:
            markets = event.get("markets") if isinstance(event.get("markets"), list) else []
            for market in markets:
                if not isinstance(market, dict):
                    continue
                markets_seen += 1
                if market.get("closed") is not True and event.get("closed") is not True:
                    continue
                if not is_weather_like_market(event, market):
                    continue
                markets_kept += 1
                rows.extend(
                    self.market_to_signal_rows(
                        event,
                        market,
                        include_order_books=False,
                    )
                )
                if len(rows) >= row_limit:
                    rows = rows[:row_limit]
                    break
            if len(rows) >= row_limit:
                break

        diagnostics.update(
            {
                "markets_seen": markets_seen,
                "closed_markets_kept": markets_kept,
                "rows": len(rows),
            }
        )
        status = "ready" if rows else "no_closed_weather_markets"
        if diagnostics.get("errors") and not rows:
            status = "partial_error"
        return {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": f"polymarket-closed-readonly-{generated_at}",
            "generated_at": generated_at,
            "status": status,
            "source": "polymarket_closed_readonly",
            "rows": rows,
            "diagnostics": diagnostics,
        }

    def market_to_signal_rows(
        self,
        event: Dict[str, Any],
        market: Dict[str, Any],
        *,
        include_order_books: bool,
        order_book_cache: Optional[Dict[str, OrderBookSummary]] = None,
        order_book_errors: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        outcomes = [str(item) for item in _parse_json_list(market.get("outcomes"))]
        prices = [_safe_float(item) for item in _parse_json_list(market.get("outcomePrices"))]
        token_ids = [str(item) for item in _parse_json_list(market.get("clobTokenIds"))]
        order_book_cache = order_book_cache if order_book_cache is not None else {}
        order_book_errors = order_book_errors if order_book_errors is not None else {}
        rows: List[Dict[str, Any]] = []
        active = _safe_bool(market.get("active"))
        closed = _safe_bool(market.get("closed"))
        event_active = _safe_bool(event.get("active"))
        event_closed = _safe_bool(event.get("closed"))
        enable_order_book = _safe_bool(market.get("enableOrderBook"))
        if enable_order_book is None:
            enable_order_book = _safe_bool(event.get("enableOrderBook"))
        accepting_orders = _safe_bool(market.get("acceptingOrders"))

        for index, outcome in enumerate(outcomes):
            token_id = token_ids[index] if index < len(token_ids) else ""
            fallback_price = prices[index] if index < len(prices) else None
            book: Optional[OrderBookSummary] = None
            book_error: Optional[str] = None
            if include_order_books and token_id:
                if token_id in order_book_cache:
                    book = order_book_cache[token_id]
                elif token_id in order_book_errors:
                    book_error = order_book_errors[token_id]
                else:
                    try:
                        book = self.get_order_book(token_id)
                        order_book_cache[token_id] = book
                    except PolymarketReadonlyError as exc:
                        book_error = str(exc)
                        order_book_errors[token_id] = book_error

            book_dict = asdict(book) if book else {}
            best_ask = book.best_ask if book else None
            best_bid = book.best_bid if book else None
            spread = book.spread if book else None
            execution_liquidity = book.ask_depth_usdc_3c if book else None
            liquidity = _safe_float(
                market.get("liquidityNum")
                or market.get("liquidityClob")
                or market.get("liquidity")
                or event.get("liquidity")
            )
            tradable = bool(
                (active is not False)
                and (event_active is not False)
                and (closed is not True)
                and (event_closed is not True)
                and (enable_order_book is not False)
                and (accepting_orders is not False)
                and token_id
            )
            row = {
                "id": f"{market.get('id') or market.get('slug')}:{outcome.lower()}",
                "source": "polymarket_gamma_clob",
                "market_family": classify_weather_market_family(event, market),
                "event_id": event.get("id"),
                "event_slug": event.get("slug"),
                "event_title": event.get("title"),
                "market_id": market.get("id"),
                "market_slug": market.get("slug"),
                "question": market.get("question"),
                "side": outcome.lower(),
                "outcome": outcome,
                "token_id": token_id or None,
                "condition_id": market.get("conditionId"),
                "active": (active is not False) and (event_active is not False),
                "closed": (closed is True) or (event_closed is True),
                "tradable": tradable,
                "accepting_orders": accepting_orders if accepting_orders is not None else tradable,
                "enable_order_book": enable_order_book,
                "price": best_ask if best_ask is not None else fallback_price,
                "market_probability": fallback_price,
                "bid": best_bid,
                "ask": best_ask,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "execution_liquidity": execution_liquidity,
                "liquidity": liquidity,
                "liquidityNum": liquidity,
                "volume": _safe_float(market.get("volumeNum") or market.get("volume") or event.get("volume")),
                "end_date": _first_text(market.get("endDate"), event.get("endDate")) or None,
                "resolution_source": _first_text(market.get("resolutionSource"), event.get("resolutionSource")) or None,
                "order_book": book_dict or None,
                "order_book_error": book_error,
            }
            rows.append(row)
        return rows


def build_polymarket_weather_payload(
    *,
    queries: Iterable[str] = DEFAULT_WEATHER_QUERIES,
    row_limit: int = 50,
    search_limit_per_query: int = 25,
    include_city_temperature_queries: bool = True,
    city_search_limit_per_query: int = 5,
    max_city_temperature_queries: Optional[int] = None,
    active_scan_limit: int = 500,
    include_order_books: bool = True,
    exclude_expired_markets: bool = True,
    exclude_not_accepting_orders: bool = True,
    client: Optional[PolymarketReadonlyClient] = None,
) -> Dict[str, Any]:
    client = client or PolymarketReadonlyClient()
    return client.build_weather_market_payload(
        queries=queries,
        row_limit=row_limit,
        search_limit_per_query=search_limit_per_query,
        include_city_temperature_queries=include_city_temperature_queries,
        city_search_limit_per_query=city_search_limit_per_query,
        max_city_temperature_queries=max_city_temperature_queries,
        active_scan_limit=active_scan_limit,
        include_order_books=include_order_books,
        exclude_expired_markets=exclude_expired_markets,
        exclude_not_accepting_orders=exclude_not_accepting_orders,
    )


def build_polymarket_closed_weather_payload(
    *,
    queries: Iterable[str] = DEFAULT_WEATHER_QUERIES,
    row_limit: int = 100,
    search_limit_per_query: int = 25,
    include_city_temperature_queries: bool = True,
    city_search_limit_per_query: int = 5,
    max_city_temperature_queries: Optional[int] = None,
    client: Optional[PolymarketReadonlyClient] = None,
) -> Dict[str, Any]:
    client = client or PolymarketReadonlyClient()
    return client.build_closed_weather_market_payload(
        queries=queries,
        row_limit=row_limit,
        search_limit_per_query=search_limit_per_query,
        include_city_temperature_queries=include_city_temperature_queries,
        city_search_limit_per_query=city_search_limit_per_query,
        max_city_temperature_queries=max_city_temperature_queries,
    )
