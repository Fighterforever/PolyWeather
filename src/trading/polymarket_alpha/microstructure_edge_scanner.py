from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_microstructure_edge.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _parse_utc(value: Any) -> Optional[datetime]:
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


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_id(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def _iter_token_books(market: Dict[str, Any]) -> Iterable[tuple[str, str, Dict[str, Any]]]:
    books = market.get("orderbooks") if isinstance(market.get("orderbooks"), dict) else {}
    token_to_outcome = {str(token): str(outcome) for outcome, token in (market.get("token_id_by_outcome") or {}).items()}
    for token_id, book in books.items():
        if isinstance(book, dict):
            yield str(token_id), token_to_outcome.get(str(token_id), ""), book


def _depth_imbalance(book: Dict[str, Any]) -> Optional[float]:
    bid_depth = _safe_float(book.get("bid_depth_usdc_3c") or book.get("bid_depth") or book.get("bid_depth_usdc"))
    ask_depth = _safe_float(book.get("ask_depth_usdc_3c") or book.get("ask_depth") or book.get("ask_depth_usdc"))
    if bid_depth is None or ask_depth is None or (bid_depth + ask_depth) <= 0:
        return None
    return round((bid_depth - ask_depth) / (bid_depth + ask_depth), 8)


def _snapshot_for_watch(row: Dict[str, Any]) -> Dict[str, Any]:
    book = row.get("orderbook") if isinstance(row.get("orderbook"), dict) else {}
    recorded_at = row.get("entry_time") or row.get("recorded_at") or _iso_now()
    payload = {
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "recorded_at": recorded_at,
        "best_bid": book.get("best_bid"),
        "best_ask": book.get("best_ask"),
    }
    return {
        "schema_version": f"{SCHEMA_VERSION}.orderbook_snapshot",
        "orderbook_snapshot_id": _stable_id(payload),
        "watch_id": row.get("watch_id"),
        "fill_id": row.get("fill_id"),
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "recorded_at": recorded_at,
        "timestamp": recorded_at,
        "best_bid": _safe_float(book.get("best_bid")),
        "best_ask": _safe_float(book.get("best_ask")),
        "bid_ladder": book.get("bid_ladder") if isinstance(book.get("bid_ladder"), list) else [],
        "ask_ladder": book.get("ask_ladder") if isinstance(book.get("ask_ladder"), list) else [],
        "spread": _safe_float(book.get("spread")),
        "depth": _safe_float(book.get("ask_depth_usdc_3c") or book.get("depth")),
        "source": "microstructure_edge_scanner",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _market_category_allowed(category: str, allowed_categories: Optional[set[str]]) -> bool:
    return not allowed_categories or category in allowed_categories


def scan_microstructure_edges(
    *,
    active_markets: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
    allowed_categories: Optional[Iterable[str]] = None,
    min_depth: float = 25.0,
    min_spread: float = 0.02,
    max_spread: float = 0.18,
    min_price: float = 0.02,
    max_price: float = 0.98,
    imbalance_threshold: float = 0.55,
    paper_mode: str = "watch_only",
) -> Dict[str, Any]:
    generated_at = generated_at or _iso_now()
    allowed = {str(item) for item in allowed_categories or [] if str(item).strip()} or None
    markets = [row for row in active_markets if isinstance(row, dict) and row.get("active")]
    blockers: Counter[str] = Counter()
    watch_rows: List[Dict[str, Any]] = []
    paper_fills: List[Dict[str, Any]] = []
    snapshots: List[Dict[str, Any]] = []
    category_counts: Counter[str] = Counter()

    for market in markets:
        category = str(market.get("category") or "uncategorized")
        if not _market_category_allowed(category, allowed):
            blockers["category_not_in_microstructure_universe"] += 1
            continue
        books = list(_iter_token_books(market))
        if not books:
            blockers["missing_orderbook"] += 1
            continue
        for token_id, outcome, book in books:
            best_bid = _safe_float(book.get("best_bid"))
            best_ask = _safe_float(book.get("best_ask"))
            spread = _safe_float(book.get("spread"))
            ask_depth = _safe_float(book.get("ask_depth_usdc_3c") or book.get("depth"))
            bid_depth = _safe_float(book.get("bid_depth_usdc_3c") or book.get("bid_depth"))
            if best_bid is None or best_ask is None:
                blockers["missing_bid_ask"] += 1
                continue
            price_mid = (best_bid + best_ask) / 2.0
            if price_mid < float(min_price) or price_mid > float(max_price):
                blockers["dust_or_extreme_price"] += 1
                continue
            if ask_depth is None or ask_depth < float(min_depth):
                blockers["ask_depth_insufficient"] += 1
                continue
            if spread is None:
                blockers["missing_spread"] += 1
                continue
            if spread < float(min_spread):
                blockers["spread_too_tight"] += 1
                continue
            if spread > float(max_spread):
                blockers["spread_too_wide"] += 1
                continue
            imbalance = _depth_imbalance(book)
            signals: List[str] = []
            if spread >= float(min_spread):
                signals.append("spread_capture_watch")
            if imbalance is not None and abs(imbalance) >= float(imbalance_threshold):
                signals.append("imbalance_follow_watch")
            momentum_5m = _safe_float((market.get("trade_tape") or {}).get("price_momentum_5m") or market.get("price_momentum_5m"))
            if momentum_5m is not None and abs(momentum_5m) >= 0.05:
                signals.append("mean_reversion_watch")
            if not signals:
                blockers["no_microstructure_signal"] += 1
                continue
            signal_type = signals[0]
            row = {
                "schema_version": f"{SCHEMA_VERSION}.watch_row",
                "watch_id": _stable_id({"market_slug": market.get("market_slug"), "token_id": token_id, "generated_at": generated_at, "signal": signal_type}),
                "market_slug": market.get("market_slug"),
                "event_slug": market.get("event_slug"),
                "token_id": token_id,
                "category": category,
                "outcome_label": outcome,
                "side": "YES",
                "signal_type": signal_type,
                "all_signal_types": signals,
                "entry_time": generated_at,
                "recorded_at": generated_at,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "q_effective": best_ask,
                "price_mid": round(price_mid, 8),
                "spread": spread,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "depth": ask_depth,
                "depth_imbalance": imbalance,
                "recent_trade_count": (market.get("trade_tape") or {}).get("trade_count"),
                "price_momentum_5m": momentum_5m,
                "time_to_close": market.get("end_time"),
                "liquidity": market.get("liquidity"),
                "volume": market.get("volume"),
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
                "orderbook": book,
            }
            snapshot = _snapshot_for_watch(row)
            row["orderbook_snapshot_id"] = snapshot["orderbook_snapshot_id"]
            snapshots.append(snapshot)
            watch_rows.append({key: value for key, value in row.items() if key != "orderbook"})
            category_counts[category] += 1
            if str(paper_mode) == "paper_fill":
                fill = dict(watch_rows[-1])
                fill["schema_version"] = f"{SCHEMA_VERSION}.paper_fill"
                fill["fill_id"] = _stable_id({"watch_id": fill.get("watch_id"), "q_effective": fill.get("q_effective")})
                fill["source_watch_id"] = fill.get("watch_id")
                paper_fills.append(fill)

    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "paper_mode": paper_mode,
        "scanned_market_count": len(markets),
        "candidate_count": len(watch_rows),
        "watch_count": len(watch_rows),
        "paper_fill_count": len(paper_fills),
        "orderbook_snapshot_count": len(snapshots),
        "by_category": [{"category": key, "candidate_count": value} for key, value in sorted(category_counts.items())],
        "blocker_counts": [{"reason": key, "count": value} for key, value in sorted(blockers.items())],
        "top_candidates": sorted(watch_rows, key=lambda row: (float(row.get("spread") or 0.0), float(row.get("depth") or 0.0)), reverse=True)[:25],
        "watch_rows": watch_rows,
        "paper_fills": paper_fills,
        "orderbook_snapshots": snapshots,
    }


def _by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict) and row.get("token_id"):
            grouped[str(row["token_id"])].append(row)
    for members in grouped.values():
        members.sort(key=lambda row: str(row.get("timestamp") or row.get("recorded_at") or row.get("entry_time") or ""))
    return grouped


def _nearest(rows: List[Dict[str, Any]], target: datetime, tolerance_seconds: int) -> tuple[Optional[Dict[str, Any]], Optional[float]]:
    best: Optional[Dict[str, Any]] = None
    best_lag: Optional[float] = None
    for row in rows:
        ts = _parse_utc(row.get("timestamp") or row.get("recorded_at") or row.get("entry_time"))
        if ts is None:
            continue
        lag = (ts - target).total_seconds()
        if abs(lag) > tolerance_seconds:
            continue
        if best_lag is None or abs(lag) < abs(best_lag):
            best = row
            best_lag = lag
    return best, best_lag


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 8) if values else None


def _bucket_summary(rows: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        value = _safe_float(row.get("markout_cents"))
        if value is not None:
            grouped[str(row.get(key) or "missing")].append(value)
    return [{"bucket": key, "sample_count": len(values), "mean_markout_cents": _mean(values)} for key, values in sorted(grouped.items())]


def build_microstructure_markout_report(
    *,
    watch_rows: Iterable[Dict[str, Any]],
    paper_fills: Iterable[Dict[str, Any]] = (),
    orderbook_snapshots: Iterable[Dict[str, Any]] = (),
    horizons: Tuple[int, ...] = (300, 900, 3600, 21600),
) -> Dict[str, Any]:
    rows = [row for row in list(paper_fills or []) + list(watch_rows or []) if isinstance(row, dict)]
    snapshots = _by_token(orderbook_snapshots)
    markouts: List[Dict[str, Any]] = []
    tolerance = {300: 90, 900: 180, 3600: 600, 21600: 1800}
    for row in rows:
        token_id = str(row.get("token_id") or "")
        entry_time = _parse_utc(row.get("entry_time") or row.get("recorded_at") or row.get("timestamp"))
        entry_price = _safe_float(row.get("q_effective") or row.get("best_ask"))
        for horizon in horizons:
            base = {
                "schema_version": f"{SCHEMA_VERSION}.markout",
                "watch_id": row.get("watch_id"),
                "fill_id": row.get("fill_id"),
                "market_slug": row.get("market_slug"),
                "token_id": token_id,
                "category": row.get("category"),
                "signal_type": row.get("signal_type"),
                "horizon_seconds": horizon,
                "entry_time": row.get("entry_time") or row.get("recorded_at") or row.get("timestamp"),
                "q_effective": entry_price,
                "target_time": None,
                "matched_snapshot_time": None,
                "match_lag_seconds": None,
                "exit_bid_or_mid": None,
                "markout_cents": None,
                "missing_snapshot_reason": None,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
            if not token_id:
                markouts.append({**base, "missing_snapshot_reason": "missing_token_id"})
                continue
            if entry_time is None:
                markouts.append({**base, "missing_snapshot_reason": "missing_entry_time"})
                continue
            if entry_price is None:
                markouts.append({**base, "missing_snapshot_reason": "missing_entry_price"})
                continue
            target = entry_time + timedelta(seconds=int(horizon))
            matched, lag = _nearest(snapshots.get(token_id) or [], target, tolerance.get(int(horizon), 300))
            target_iso = target.isoformat().replace("+00:00", "Z")
            if matched is None:
                markouts.append({**base, "target_time": target_iso, "missing_snapshot_reason": "missing_snapshot_for_horizon"})
                continue
            exit_price = _safe_float(matched.get("best_bid") or matched.get("price_mid") or matched.get("price"))
            if exit_price is None:
                markouts.append({**base, "target_time": target_iso, "matched_snapshot_time": matched.get("timestamp") or matched.get("recorded_at"), "match_lag_seconds": lag, "missing_snapshot_reason": "missing_exit_bid_or_mid"})
                continue
            markouts.append(
                {
                    **base,
                    "target_time": target_iso,
                    "matched_snapshot_time": matched.get("timestamp") or matched.get("recorded_at"),
                    "match_lag_seconds": lag,
                    "exit_bid_or_mid": exit_price,
                    "markout_cents": round((exit_price - entry_price) * 100.0, 6),
                }
            )
    available = [row for row in markouts if row.get("markout_cents") is not None]
    missing = [row for row in markouts if row.get("markout_cents") is None]
    all_values = [float(row["markout_cents"]) for row in available]
    return {
        "schema_version": f"{SCHEMA_VERSION}.markout_report",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "candidate_count": len(rows),
        "available_markout_count": len(available),
        "mean_markout_cents": _mean(all_values),
        "mean_markout_by_horizon": _bucket_summary(available, "horizon_seconds"),
        "by_signal_type": _bucket_summary(available, "signal_type"),
        "by_category": _bucket_summary(available, "category"),
        "missing_snapshot_reason_counts": [{"reason": key, "count": value} for key, value in sorted(Counter(str(row.get("missing_snapshot_reason") or "none") for row in missing).items())],
        "markouts": markouts,
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


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


__all__ = [
    "SCHEMA_VERSION",
    "build_microstructure_markout_report",
    "load_json",
    "load_jsonl",
    "scan_microstructure_edges",
    "write_json",
    "write_jsonl",
]
