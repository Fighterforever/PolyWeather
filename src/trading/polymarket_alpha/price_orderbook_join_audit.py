from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.crypto_probability_model import (
    fetch_missing_orderbooks_for_markets,
    parse_crypto_threshold_market,
)
from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_price_orderbook_join_audit.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _token_for_outcome(market: Dict[str, Any], outcome: str) -> Optional[str]:
    wanted = str(outcome or "").strip().lower()
    for label, token_id in (market.get("token_id_by_outcome") or {}).items():
        if str(label or "").strip().lower() == wanted:
            return str(token_id)
    return None


def _book(market: Dict[str, Any], outcome: str) -> Dict[str, Any]:
    token_id = _token_for_outcome(market, outcome)
    books = market.get("orderbooks") if isinstance(market.get("orderbooks"), dict) else {}
    return books.get(str(token_id)) if token_id and isinstance(books.get(str(token_id)), dict) else {}


def _book_reason(market: Dict[str, Any], outcome: str) -> str:
    token_id = _token_for_outcome(market, outcome)
    if not token_id:
        return f"missing_{outcome.lower()}_token_id"
    book = _book(market, outcome)
    if not book:
        return f"{outcome.lower()}_token_not_found_or_orderbook_missing"
    if _safe_float(book.get("best_ask")) is None:
        return f"{outcome.lower()}_no_ask_depth"
    if _safe_float(book.get("best_bid")) is None:
        return f"{outcome.lower()}_no_bid_depth"
    return "available"


def build_price_orderbook_join_audit_report(
    *,
    active_markets: Iterable[Dict[str, Any]],
    snapshot_rows: Iterable[Dict[str, Any]],
    price_history_rows: Iterable[Dict[str, Any]],
    fetch_orderbooks: bool = False,
    max_orderbook_tokens: int = 200,
    orderbook_fetcher: Optional[Any] = None,
) -> Dict[str, Any]:
    active = [dict(row) for row in active_markets if isinstance(row, dict) and row.get("active")]
    snapshots = [row for row in snapshot_rows if isinstance(row, dict)]
    price_rows = [row for row in price_history_rows if isinstance(row, dict)]
    requested_tokens = {str(row.get("token_id")) for row in snapshots if row.get("token_id")}
    found_tokens = {str(row.get("token_id")) for row in price_rows if row.get("token_id") in requested_tokens}
    timestamp_window_miss_count = len([row for row in snapshots if row.get("data_source") == "active_gamma_snapshot_price"])
    crypto_markets = [row for row in active if parse_crypto_threshold_market(row) is not None]
    orderbook_fetch_summary = {"attempted": 0, "fetched": {}, "errors": {}}
    if fetch_orderbooks:
        orderbook_fetch_summary = fetch_missing_orderbooks_for_markets(
            crypto_markets + [row for row in active if row not in crypto_markets],
            max_tokens=int(max_orderbook_tokens),
            fetcher=orderbook_fetcher,
        )
    active_token_count = sum(len(row.get("token_ids") or []) for row in active)
    best_bid_ask_available_count = 0
    orderbook_reason_counts: Counter[str] = Counter()
    for market in active:
        books = market.get("orderbooks") if isinstance(market.get("orderbooks"), dict) else {}
        for token in market.get("token_ids") or []:
            book = books.get(str(token)) if isinstance(books.get(str(token)), dict) else {}
            if _safe_float(book.get("best_bid")) is not None and _safe_float(book.get("best_ask")) is not None:
                best_bid_ask_available_count += 1
            elif not book:
                orderbook_reason_counts["token_not_found_or_orderbook_missing"] += 1
            elif _safe_float(book.get("best_ask")) is None:
                orderbook_reason_counts["no_ask_depth"] += 1
            elif _safe_float(book.get("best_bid")) is None:
                orderbook_reason_counts["no_bid_depth"] += 1
    crypto_token_count = sum(len(row.get("token_ids") or []) for row in crypto_markets)
    crypto_orderbook_fetch_success_count = len([
        token for token in orderbook_fetch_summary.get("fetched", {})
        if any(str(token) in {str(t) for t in market.get("token_ids") or []} for market in crypto_markets)
    ])
    crypto_yes_available = 0
    crypto_no_available = 0
    crypto_samples: List[Dict[str, Any]] = []
    for market in crypto_markets:
        for outcome in ("Yes", "No"):
            reason = _book_reason(market, outcome)
            if reason == "available":
                if outcome == "Yes":
                    crypto_yes_available += 1
                else:
                    crypto_no_available += 1
                continue
            if len(crypto_samples) < 20:
                crypto_samples.append(
                    {
                        "market_slug": market.get("market_slug"),
                        "token_id": _token_for_outcome(market, outcome),
                        "outcome": outcome,
                        "side": outcome.upper(),
                        "reason": reason,
                    }
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "historical_price_join": {
            "requested_price_history_count": len(requested_tokens),
            "price_history_found_count": len(found_tokens),
            "missing_price_by_reason": [
                {"reason": "timestamp_window_miss_or_active_snapshot_fallback", "count": timestamp_window_miss_count},
                {"reason": "token_id_mismatch_or_missing_history", "count": max(0, len(requested_tokens) - len(found_tokens))},
            ],
            "token_id_mismatch_count": max(0, len(requested_tokens) - len(found_tokens)),
            "timestamp_window_miss_count": timestamp_window_miss_count,
            "CLOB_price_history_error_count": 0,
        },
        "active_orderbook_join": {
            "active_token_count": active_token_count,
            "orderbook_fetch_attempt_count": int(orderbook_fetch_summary.get("attempted") or 0),
            "orderbook_fetch_success_count": len(orderbook_fetch_summary.get("fetched", {})),
            "best_bid_ask_available_count": best_bid_ask_available_count,
            "missing_orderbook_by_reason": [{"reason": key, "count": value} for key, value in sorted(orderbook_reason_counts.items())],
        },
        "crypto_specific_diagnostics": {
            "parsed_crypto_market_count": len(crypto_markets),
            "model_ready_count": len(crypto_markets),
            "crypto_token_count": crypto_token_count,
            "crypto_orderbook_fetch_success_count": crypto_orderbook_fetch_success_count,
            "crypto_yes_best_ask_available_count": crypto_yes_available,
            "crypto_no_best_ask_available_count": crypto_no_available,
            "crypto_missing_executable_price_samples": crypto_samples,
        },
    }


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


__all__ = ["SCHEMA_VERSION", "build_price_orderbook_join_audit_report", "load_jsonl", "write_json"]
