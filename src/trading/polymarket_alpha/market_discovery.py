from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.trading.polymarket_readonly import (
    OrderBookSummary,
    PolymarketReadonlyClient,
    PolymarketReadonlyError,
)


SCHEMA_VERSION = "polyweather_polymarket_alpha_market_discovery.v1"
MARKET_ROW_SCHEMA_VERSION = "polyweather_polymarket_alpha_market_row.v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


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


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _tags(event: Dict[str, Any], market: Dict[str, Any]) -> List[str]:
    output: List[str] = []
    for source in (event.get("tags"), market.get("tags")):
        for item in _parse_json_list(source):
            if isinstance(item, dict):
                text = _first_text(item.get("label"), item.get("name"), item.get("slug"))
            else:
                text = _text(item)
            if text:
                output.append(text)
    seen: set[str] = set()
    deduped: List[str] = []
    for tag in output:
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(tag)
    return deduped


def infer_category(event: Dict[str, Any], market: Dict[str, Any]) -> str:
    for source in (market, event):
        value = source.get("category")
        if isinstance(value, dict):
            text = _first_text(value.get("label"), value.get("name"), value.get("slug"))
        else:
            text = _text(value)
        if text:
            return text.lower().replace(" ", "_")
    tags = _tags(event, market)
    return tags[0].lower().replace(" ", "_") if tags else "uncategorized"


def _condition_id(market: Dict[str, Any]) -> Optional[str]:
    for field in ("conditionId", "condition_id", "conditionID", "questionID"):
        text = _text(market.get(field))
        if text:
            return text
    return None


def _book_payload(book: OrderBookSummary) -> Dict[str, Any]:
    return {
        "token_id": book.token_id,
        "best_bid": book.best_bid,
        "best_ask": book.best_ask,
        "spread": book.spread,
        "bid_depth_usdc_3c": book.bid_depth_usdc_3c,
        "ask_depth_usdc_3c": book.ask_depth_usdc_3c,
        "bid_levels": book.bid_levels,
        "ask_levels": book.ask_levels,
        "bid_ladder": book.bid_ladder,
        "ask_ladder": book.ask_ladder,
        "timestamp": book.timestamp,
    }


def _spread_from_books(books: Sequence[Dict[str, Any]]) -> Optional[float]:
    spreads = [_safe_float(book.get("spread")) for book in books]
    spreads = [spread for spread in spreads if spread is not None]
    return round(float(median(spreads)), 6) if spreads else None


def _best_bid_ask_from_books(books: Sequence[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    bids = [_safe_float(book.get("best_bid")) for book in books]
    asks = [_safe_float(book.get("best_ask")) for book in books]
    bids = [value for value in bids if value is not None]
    asks = [value for value in asks if value is not None]
    return (max(bids) if bids else None, min(asks) if asks else None)


def normalize_market(
    event: Dict[str, Any],
    market: Dict[str, Any],
    *,
    generated_at: Optional[str] = None,
    order_books: Optional[Dict[str, Dict[str, Any]]] = None,
    trade_tape_stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    outcomes = [str(item) for item in _parse_json_list(market.get("outcomes"))]
    token_ids = [str(item) for item in _parse_json_list(market.get("clobTokenIds"))]
    prices = [_safe_float(item) for item in _parse_json_list(market.get("outcomePrices"))]
    token_id_by_outcome = {
        outcome: token_id
        for outcome, token_id in zip(outcomes, token_ids)
        if outcome and token_id
    }
    order_books = order_books or {}
    market_books = [book for token_id, book in order_books.items() if token_id in set(token_ids)]
    best_bid, best_ask = _best_bid_ask_from_books(market_books)
    spread = _spread_from_books(market_books)
    volume = _safe_float(market.get("volumeNum") or market.get("volume") or event.get("volume")) or 0.0
    liquidity = _safe_float(
        market.get("liquidityNum")
        or market.get("liquidityClob")
        or market.get("liquidity")
        or event.get("liquidity")
    )
    active = (_safe_bool(market.get("active")) is not False) and (_safe_bool(event.get("active")) is not False)
    closed = (_safe_bool(market.get("closed")) is True) or (_safe_bool(event.get("closed")) is True)
    resolved = bool(closed and any(price in {0.0, 1.0} for price in prices if price is not None))
    trade_tape_stats = trade_tape_stats or {}
    return {
        "schema_version": MARKET_ROW_SCHEMA_VERSION,
        "snapshot_at": generated_at,
        "market_id": _first_text(market.get("id"), market.get("marketId")),
        "condition_id": _condition_id(market),
        "event_id": _text(event.get("id")) or None,
        "event_slug": _text(event.get("slug")) or None,
        "market_slug": _text(market.get("slug")) or None,
        "title": _first_text(market.get("question"), event.get("title")),
        "question": _text(market.get("question")) or None,
        "description": _first_text(market.get("description"), event.get("description")) or None,
        "category": infer_category(event, market),
        "tags": _tags(event, market),
        "outcomes": outcomes,
        "token_ids": token_ids,
        "token_id_by_outcome": token_id_by_outcome,
        "outcome_prices": prices,
        "active": bool(active and not closed),
        "closed": bool(closed),
        "resolved": resolved,
        "volume": round(float(volume), 6),
        "liquidity": liquidity,
        "spread": spread,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "orderbooks": order_books,
        "orderbook_available": bool(market_books),
        "trade_tape_available": bool(trade_tape_stats.get("trade_count")),
        "trade_tape": trade_tape_stats,
        "resolution_time": _first_text(market.get("resolvedTime"), market.get("resolutionTime")) or None,
        "end_time": _first_text(market.get("endDate"), event.get("endDate")) or None,
        "neg_risk": bool(_safe_bool(market.get("negRisk") or event.get("negRisk")) or False),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def fetch_gamma_events(
    client: PolymarketReadonlyClient,
    *,
    closed: bool,
    limit: int = 500,
    page_size: int = 100,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    events: List[Dict[str, Any]] = []
    errors: List[str] = []
    max_limit = max(0, int(limit))
    page_size = max(1, min(100, int(page_size)))
    for offset in range(0, max_limit, page_size):
        params = {
            "closed": "true" if closed else "false",
            "active": "false" if closed else "true",
            "limit": page_size,
            "offset": offset,
        }
        try:
            payload = client._get_json(client.gamma_base_url, "/events", params)
        except PolymarketReadonlyError as exc:
            errors.append(str(exc))
            break
        if not isinstance(payload, list):
            errors.append("gamma_events_non_list_response")
            break
        if not payload:
            break
        events.extend(event for event in payload if isinstance(event, dict))
        if len(payload) < page_size:
            break
    return events, errors


def _collect_market_rows_from_events(
    events: Iterable[Dict[str, Any]],
    *,
    generated_at: str,
    client: Optional[PolymarketReadonlyClient] = None,
    include_order_books: bool = False,
    max_orderbook_markets: int = 50,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    event_rows = [event for event in events if isinstance(event, dict)]
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    orderbook_market_count = 0
    orderbook_token_count = 0
    orderbook_cache: Dict[str, Dict[str, Any]] = {}
    for event in event_rows:
        markets = event.get("markets") if isinstance(event.get("markets"), list) else []
        for market in markets:
            if not isinstance(market, dict):
                continue
            token_ids = [str(item) for item in _parse_json_list(market.get("clobTokenIds")) if str(item).strip()]
            order_books: Dict[str, Dict[str, Any]] = {}
            if include_order_books and client is not None and orderbook_market_count < int(max_orderbook_markets):
                for token_id in token_ids:
                    if token_id in orderbook_cache:
                        order_books[token_id] = orderbook_cache[token_id]
                        continue
                    try:
                        book = _book_payload(client.get_order_book(token_id))
                    except PolymarketReadonlyError as exc:
                        errors.append(f"orderbook:{token_id}:{exc}")
                        continue
                    orderbook_cache[token_id] = book
                    order_books[token_id] = book
                    orderbook_token_count += 1
                orderbook_market_count += 1
            rows.append(normalize_market(event, market, generated_at=generated_at, order_books=order_books))
    diagnostics = {
        "event_count": len(event_rows),
        "market_count": len(rows),
        "orderbook_market_request_count": orderbook_market_count,
        "orderbook_token_request_count": orderbook_token_count,
        "orderbook_error_count": len(errors),
        "orderbook_errors": errors[:20],
    }
    return rows, diagnostics


def _category_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[str(row.get("category") or "uncategorized")].append(row)
    output: List[Dict[str, Any]] = []
    for category, members in sorted(by_category.items()):
        spreads = [_safe_float(row.get("spread")) for row in members]
        spreads = [value for value in spreads if value is not None]
        event_counts = Counter(str(row.get("event_slug") or row.get("market_slug") or "") for row in members)
        trade_count = sum(int((row.get("trade_tape") or {}).get("trade_count") or 0) for row in members)
        output.append(
            {
                "category": category,
                "active_market_count": len([row for row in members if row.get("active")]),
                "closed_market_count": len([row for row in members if row.get("closed")]),
                "market_count": len(members),
                "total_volume": round(sum(float(row.get("volume") or 0.0) for row in members), 6),
                "median_spread": round(float(median(spreads)), 6) if spreads else None,
                "event_family_size": round(sum(event_counts.values()) / max(1, len(event_counts)), 6),
                "trade_tape_density": round(trade_count / max(1, len(members)), 6),
                "orderbook_available_count": len([row for row in members if row.get("orderbook_available")]),
            }
        )
    return output


def _rank_categories(category_rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    def top(key: str, reverse: bool = True) -> List[Dict[str, Any]]:
        return sorted(
            [dict(row) for row in category_rows],
            key=lambda row: (row.get(key) is not None, float(row.get(key) or 0.0)),
            reverse=reverse,
        )[:10]

    return {
        "by_active_market_count": top("active_market_count"),
        "by_total_volume": top("total_volume"),
        "by_median_spread": top("median_spread", reverse=False),
        "by_event_family_size": top("event_family_size"),
        "by_trade_tape_density": top("trade_tape_density"),
    }


def build_market_discovery_report(
    *,
    active_rows: Iterable[Dict[str, Any]],
    closed_rows: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    active = [dict(row, paper_only=True, live_order_path=False) for row in active_rows if isinstance(row, dict)]
    closed = [dict(row, paper_only=True, live_order_path=False) for row in closed_rows if isinstance(row, dict)]
    all_rows = active + closed
    category_rows = _category_rows(all_rows)
    category_counts = [{"category": row["category"], "count": row["market_count"]} for row in category_rows]
    high_volume_threshold = median([row.get("volume") or 0.0 for row in all_rows]) if all_rows else 0.0
    high_volume_counts = Counter(
        str(row.get("category") or "uncategorized")
        for row in all_rows
        if float(row.get("volume") or 0.0) >= float(high_volume_threshold)
    )
    threshold_like_re = re.compile(r"(\b(over|under|above|below|at least|greater than|less than)\b|[<>]=?|\d+)", re.I)
    event_family_counts = Counter(str(row.get("event_slug") or row.get("market_slug") or "") for row in all_rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "active_market_count": len(active),
        "closed_market_count": len(closed),
        "category_counts": category_counts,
        "high_volume_category_counts": [
            {"category": category, "count": count} for category, count in sorted(high_volume_counts.items())
        ],
        "multi_outcome_event_count": len(
            {row.get("event_slug") for row in all_rows if len(row.get("outcomes") or []) > 2 and row.get("event_slug")}
        ),
        "threshold_like_market_count": len(
            [row for row in all_rows if threshold_like_re.search(str(row.get("title") or row.get("question") or ""))]
        ),
        "orderbook_available_count": len([row for row in all_rows if row.get("orderbook_available")]),
        "trade_tape_available_count": len([row for row in all_rows if row.get("trade_tape_available")]),
        "category_stats": category_rows,
        "category_rankings": _rank_categories(category_rows),
        "top_categories": _rank_categories(category_rows)["by_total_volume"][:5],
        "multi_market_event_count": len([count for count in event_family_counts.values() if count > 1]),
        "active_markets": active,
        "closed_markets": closed,
    }


def collect_market_discovery(
    *,
    active_limit: int = 500,
    closed_limit: int = 300,
    include_order_books: bool = True,
    max_orderbook_markets: int = 60,
    client: Optional[PolymarketReadonlyClient] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    client = client or PolymarketReadonlyClient()
    active_events, active_errors = fetch_gamma_events(client, closed=False, limit=active_limit)
    closed_events, closed_errors = fetch_gamma_events(client, closed=True, limit=closed_limit)
    active_rows, active_diag = _collect_market_rows_from_events(
        active_events,
        generated_at=generated_at,
        client=client,
        include_order_books=include_order_books,
        max_orderbook_markets=max_orderbook_markets,
    )
    closed_rows, closed_diag = _collect_market_rows_from_events(
        closed_events,
        generated_at=generated_at,
        client=client,
        include_order_books=False,
    )
    report = build_market_discovery_report(active_rows=active_rows, closed_rows=closed_rows, generated_at=generated_at)
    report["diagnostics"] = {
        "active_errors": active_errors,
        "closed_errors": closed_errors,
        "active_collection": active_diag,
        "closed_collection": closed_diag,
    }
    return report


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "SCHEMA_VERSION",
    "build_market_discovery_report",
    "collect_market_discovery",
    "fetch_gamma_events",
    "infer_category",
    "load_json",
    "normalize_market",
    "utc_now_iso",
    "write_json",
    "write_jsonl",
]
