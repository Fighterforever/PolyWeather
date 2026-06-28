from __future__ import annotations

import json
import math
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_historical_price_backfill import (
    fetch_polymarket_price_history,
    normalize_price_history_rows,
)


SCHEMA_VERSION = "polyweather_polymarket_alpha_probability_dataset.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_utc(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _token_specs(markets: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    specs: Dict[str, Dict[str, Any]] = {}
    for market in markets:
        if not isinstance(market, dict):
            continue
        outcomes = market.get("outcomes") if isinstance(market.get("outcomes"), list) else []
        token_ids = market.get("token_ids") if isinstance(market.get("token_ids"), list) else []
        outcome_prices = market.get("outcome_prices") if isinstance(market.get("outcome_prices"), list) else []
        for index, token_id in enumerate(token_ids):
            token = _text(token_id)
            if not token:
                continue
            terminal_price = _safe_float(outcome_prices[index]) if index < len(outcome_prices) else None
            resolved_payout = None
            if market.get("resolved") and terminal_price is not None:
                if terminal_price >= 0.999:
                    resolved_payout = 1.0
                elif terminal_price <= 0.001:
                    resolved_payout = 0.0
            specs[token] = {
                "market_id": market.get("market_id"),
                "condition_id": market.get("condition_id"),
                "market_slug": market.get("market_slug"),
                "category": market.get("category"),
                "tags": market.get("tags") if isinstance(market.get("tags"), list) else [],
                "event_slug": market.get("event_slug"),
                "token_id": token,
                "outcome_label": str(outcomes[index]) if index < len(outcomes) else None,
                "volume": _safe_float(market.get("volume")),
                "liquidity": _safe_float(market.get("liquidity")),
                "snapshot_price": terminal_price,
                "end_time": market.get("end_time"),
                "resolved": bool(market.get("resolved")),
                "resolved_payout": resolved_payout,
                "orderbooks": market.get("orderbooks") if isinstance(market.get("orderbooks"), dict) else {},
            }
    return specs


def _bucket(value: Optional[float], cuts: List[float], labels: List[str]) -> str:
    if value is None:
        return "missing"
    for cut, label in zip(cuts, labels):
        if value < cut:
            return label
    return labels[-1]


def price_bucket(price: Optional[float]) -> str:
    return _bucket(price, [0.05, 0.15, 0.35, 0.65, 0.85, 0.95], ["lt_0_05", "0_05_0_15", "0_15_0_35", "0_35_0_65", "0_65_0_85", "0_85_0_95", "ge_0_95"])


def spread_bucket(spread: Optional[float]) -> str:
    return _bucket(spread, [0.01, 0.03, 0.07, 0.15], ["lt_1c", "1c_3c", "3c_7c", "7c_15c", "ge_15c"])


def liquidity_bucket(liquidity: Optional[float]) -> str:
    return _bucket(liquidity, [100, 1000, 10000, 100000], ["lt_100", "100_1k", "1k_10k", "10k_100k", "ge_100k"])


def time_to_close_bucket(seconds: Optional[float]) -> str:
    if seconds is None:
        return "missing"
    if seconds < 0:
        return "after_close"
    if seconds < 3600:
        return "lt_1h"
    if seconds < 86400:
        return "1h_1d"
    if seconds < 604800:
        return "1d_7d"
    if seconds < 2592000:
        return "7d_30d"
    return "ge_30d"


def _book_for_token(spec: Dict[str, Any]) -> Dict[str, Any]:
    books = spec.get("orderbooks") if isinstance(spec.get("orderbooks"), dict) else {}
    return books.get(spec.get("token_id")) if isinstance(books.get(spec.get("token_id")), dict) else {}


def _price_momentum(history: List[Dict[str, Any]], index: int, seconds: int) -> Optional[float]:
    current_price = _safe_float(history[index].get("price"))
    current_time = _parse_utc(history[index].get("timestamp"))
    if current_price is None or current_time is None:
        return None
    target = current_time.timestamp() - int(seconds)
    previous = None
    for prior in reversed(history[:index]):
        prior_time = _parse_utc(prior.get("timestamp"))
        prior_price = _safe_float(prior.get("price"))
        if prior_time is None or prior_price is None:
            continue
        if prior_time.timestamp() <= target:
            previous = prior_price
            break
    return round(current_price - previous, 8) if previous is not None else None


def _recent_trade_count(trades_by_token: Dict[str, List[Dict[str, Any]]], token_id: str, timestamp: str, window_seconds: int = 3600) -> int:
    current = _parse_utc(timestamp)
    if current is None:
        return 0
    lower = current.timestamp() - int(window_seconds)
    count = 0
    for trade in trades_by_token.get(token_id, []):
        trade_time = _parse_utc(trade.get("timestamp"))
        if trade_time is None:
            continue
        if lower <= trade_time.timestamp() <= current.timestamp():
            count += 1
    return count


def _row_from_spec(
    spec: Dict[str, Any],
    *,
    timestamp: str,
    price_mid: Optional[float],
    best_bid: Optional[float],
    best_ask: Optional[float],
    spread: Optional[float],
    trade_count_recent: int,
    price_momentum_5m: Optional[float],
    price_momentum_1h: Optional[float],
    data_source: str,
    executable_depth_available: bool,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    end_dt = _parse_utc(spec.get("end_time"))
    ts_dt = _parse_utc(timestamp)
    if ts_dt is None:
        return None, "missing_timestamp"
    seconds = None
    if end_dt is not None:
        seconds = (end_dt - ts_dt).total_seconds()
        if seconds < 0 and spec.get("resolved_payout") is not None:
            return None, "no_lookahead_after_close"
    if price_mid is None:
        return None, "missing_price"
    row = {
        "schema_version": SCHEMA_VERSION,
        "market_id": spec.get("market_id"),
        "condition_id": spec.get("condition_id"),
        "market_slug": spec.get("market_slug"),
        "category": spec.get("category") or "uncategorized",
        "tags": spec.get("tags") or [],
        "event_slug": spec.get("event_slug"),
        "token_id": spec.get("token_id"),
        "outcome_label": spec.get("outcome_label"),
        "timestamp": timestamp,
        "time_to_close_seconds": seconds,
        "price_mid": price_mid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "volume": spec.get("volume"),
        "liquidity": spec.get("liquidity"),
        "trade_count_recent": int(trade_count_recent),
        "price_momentum_5m": price_momentum_5m,
        "price_momentum_1h": price_momentum_1h,
        "price_bucket": price_bucket(price_mid),
        "spread_bucket": spread_bucket(spread),
        "time_to_close_bucket": time_to_close_bucket(seconds),
        "liquidity_bucket": liquidity_bucket(_safe_float(spec.get("liquidity"))),
        "resolved_payout": spec.get("resolved_payout"),
        "resolved": spec.get("resolved_payout") is not None,
        "data_source": data_source,
        "executable_depth_available": bool(executable_depth_available),
        "no_lookahead": True,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }
    return row, None


def fetch_price_history_for_closed_markets(
    closed_markets: Iterable[Dict[str, Any]],
    *,
    max_tokens: int = 0,
    fidelity: int = 60,
) -> List[Dict[str, Any]]:
    if int(max_tokens) <= 0:
        return []
    fetched: List[Dict[str, Any]] = []
    candidates_by_category: Dict[str, deque[Tuple[str, Dict[str, Any]]]] = defaultdict(deque)
    candidate_seen: set[str] = set()
    for market in closed_markets:
        if not isinstance(market, dict) or not market.get("resolved"):
            continue
        for token_id in market.get("token_ids") or []:
            token = _text(token_id)
            if not token or token in candidate_seen:
                continue
            candidate_seen.add(token)
            category = str(market.get("category") or "uncategorized")
            candidates_by_category[category].append((token, market))
    attempted: set[str] = set()
    categories = deque(sorted(candidates_by_category))
    while categories and len(attempted) < int(max_tokens):
        category = categories.popleft()
        bucket = candidates_by_category[category]
        if not bucket:
            continue
        token, market = bucket.popleft()
        if token in attempted:
            if bucket:
                categories.append(category)
            continue
        attempted.add(token)
        try:
            payload = fetch_polymarket_price_history(token, interval="all", fidelity=fidelity)
            fetched.extend(normalize_price_history_rows(payload, token_id=token, market_slug=market.get("market_slug")))
        except Exception:
            pass
        if bucket:
            categories.append(category)
    return fetched


def build_probability_dataset(
    *,
    active_markets: Iterable[Dict[str, Any]],
    closed_markets: Iterable[Dict[str, Any]],
    price_history_rows: Iterable[Dict[str, Any]] = (),
    trade_rows: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    active = [row for row in active_markets if isinstance(row, dict)]
    closed = [row for row in closed_markets if isinstance(row, dict)]
    all_specs = _token_specs(active + closed)
    trades_by_token: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in trade_rows:
        token = _text(trade.get("token_id"))
        if token:
            trades_by_token[token].append(trade)
    for rows in trades_by_token.values():
        rows.sort(key=lambda row: row.get("timestamp") or "")

    rows: List[Dict[str, Any]] = []
    gaps: Counter[str] = Counter()
    history_by_token: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for price_row in price_history_rows:
        token = _text(price_row.get("token_id"))
        if token and token in all_specs:
            history_by_token[token].append(price_row)
    for token, history in history_by_token.items():
        history.sort(key=lambda row: row.get("timestamp") or "")
        spec = all_specs[token]
        for index, price_row in enumerate(history):
            row, gap = _row_from_spec(
                spec,
                timestamp=_text(price_row.get("timestamp")),
                price_mid=_safe_float(price_row.get("price")),
                best_bid=None,
                best_ask=None,
                spread=None,
                trade_count_recent=_recent_trade_count(trades_by_token, token, _text(price_row.get("timestamp"))),
                price_momentum_5m=_price_momentum(history, index, 300),
                price_momentum_1h=_price_momentum(history, index, 3600),
                data_source=str(price_row.get("source") or "polymarket_price_history"),
                executable_depth_available=False,
            )
            if row:
                rows.append(row)
            elif gap:
                gaps[gap] += 1

    for token, spec in all_specs.items():
        if spec.get("resolved_payout") is not None:
            continue
        book = _book_for_token(spec)
        best_bid = _safe_float(book.get("best_bid"))
        best_ask = _safe_float(book.get("best_ask"))
        spread = _safe_float(book.get("spread"))
        price_mid = round((best_bid + best_ask) / 2.0, 8) if best_bid is not None and best_ask is not None else None
        executable_depth_available = True
        if price_mid is None:
            price_mid = _safe_float(spec.get("snapshot_price"))
            executable_depth_available = False
            if price_mid is None and token not in history_by_token:
                gaps["missing_active_orderbook_price"] += 1
                continue
        row, gap = _row_from_spec(
            spec,
            timestamp=generated_at,
            price_mid=price_mid,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            trade_count_recent=_recent_trade_count(trades_by_token, token, generated_at),
            price_momentum_5m=None,
            price_momentum_1h=None,
            data_source="active_orderbook_snapshot" if executable_depth_available else "active_gamma_snapshot_price",
            executable_depth_available=executable_depth_available,
        )
        if row:
            rows.append(row)
        elif gap:
            gaps[gap] += 1

    category_counts = Counter(str(row.get("category") or "uncategorized") for row in rows)
    category_resolved_counts = Counter(str(row.get("category") or "uncategorized") for row in rows if row.get("resolved"))
    manifest = {
        "schema_version": f"{SCHEMA_VERSION}.manifest",
        "generated_at": generated_at,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "row_count": len(rows),
        "resolved_row_count": len([row for row in rows if row.get("resolved")]),
        "category_counts": [{"category": key, "count": count} for key, count in sorted(category_counts.items())],
        "category_resolved_counts": [
            {"category": key, "count": count} for key, count in sorted(category_resolved_counts.items())
        ],
        "missing_outcome_count": len([row for row in rows if row.get("resolved_payout") is None]),
        "missing_price_count": int(gaps.get("missing_price", 0) + gaps.get("missing_active_orderbook_price", 0)),
        "no_lookahead_violation_count": 0,
        "no_lookahead_blocked_after_close_count": int(gaps.get("no_lookahead_after_close", 0)),
        "gap_counts": [{"reason": key, "count": count} for key, count in sorted(gaps.items())],
    }
    return {"rows": sorted(rows, key=lambda row: (row.get("timestamp") or "", row.get("market_slug") or "")), "manifest": manifest}


__all__ = [
    "SCHEMA_VERSION",
    "build_probability_dataset",
    "fetch_price_history_for_closed_markets",
    "load_jsonl",
    "price_bucket",
    "spread_bucket",
    "time_to_close_bucket",
    "liquidity_bucket",
    "write_json",
    "write_jsonl",
]
