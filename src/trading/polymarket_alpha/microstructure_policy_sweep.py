from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_microstructure_policy_sweep.v1"


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


def _stable_id(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def _market_by_slug(active_markets: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("market_slug")): row
        for row in active_markets
        if isinstance(row, dict) and row.get("market_slug")
    }


def _outcome_token(market: Dict[str, Any], outcome: str) -> Optional[str]:
    wanted = str(outcome or "").strip().lower()
    for label, token_id in (market.get("token_id_by_outcome") or {}).items():
        if str(label or "").strip().lower() == wanted:
            return str(token_id)
    return None


def _book_for_token(market: Dict[str, Any], token_id: Any) -> Dict[str, Any]:
    books = market.get("orderbooks") if isinstance(market.get("orderbooks"), dict) else {}
    book = books.get(str(token_id))
    return dict(book) if isinstance(book, dict) else {}


def _direct_side_quote(market: Dict[str, Any], side: str) -> tuple[Optional[Dict[str, Any]], str]:
    token_id = _outcome_token(market, "Yes" if side.upper() == "YES" else "No")
    if not token_id:
        return None, f"missing_{side.lower()}_token"
    book = _book_for_token(market, token_id)
    if not book:
        return None, f"missing_{side.lower()}_book"
    best_ask = _safe_float(book.get("best_ask"))
    best_bid = _safe_float(book.get("best_bid"))
    spread = _safe_float(book.get("spread"))
    ask_depth = _safe_float(book.get("ask_depth_usdc_3c") or book.get("ask_depth") or book.get("depth"))
    bid_depth = _safe_float(book.get("bid_depth_usdc_3c") or book.get("bid_depth"))
    if best_ask is None:
        return None, f"missing_{side.lower()}_ask"
    return {
        "token_id": token_id,
        "best_ask": best_ask,
        "best_bid": best_bid,
        "spread": spread,
        "ask_depth": ask_depth,
        "bid_depth": bid_depth,
        "book": book,
    }, ""


def _imbalance(row: Dict[str, Any]) -> Optional[float]:
    existing = _safe_float(row.get("imbalance") or row.get("depth_imbalance"))
    if existing is not None:
        return existing
    bid_depth = _safe_float(row.get("bid_depth"))
    ask_depth = _safe_float(row.get("ask_depth") or row.get("depth"))
    if bid_depth is None or ask_depth is None or bid_depth + ask_depth <= 0:
        return None
    return round((bid_depth - ask_depth) / (bid_depth + ask_depth), 8)


def _maker_quote_price(row: Dict[str, Any], *, maker_margin: float) -> Optional[float]:
    best_bid = _safe_float(row.get("best_bid"))
    best_ask = _safe_float(row.get("best_ask"))
    if best_bid is None or best_ask is None or best_ask <= best_bid:
        return None
    return round(min(best_ask - 0.001, best_bid + max(0.001, min(float(maker_margin), (best_ask - best_bid) / 2.0))), 8)


def _base_candidate(
    row: Dict[str, Any],
    *,
    policy_id: str,
    action: str,
    side: str,
    token_id: str,
    entry_price: float,
    q_effective: float,
    spread: Optional[float],
    bid_depth: Optional[float],
    ask_depth: Optional[float],
    orderbook_snapshot_id: Optional[str],
    expected_horizon: str = "1h",
) -> Dict[str, Any]:
    payload = {
        "watch_id": row.get("watch_id"),
        "policy_id": policy_id,
        "action": action,
        "token_id": token_id,
        "entry_price": entry_price,
    }
    return {
        "schema_version": f"{SCHEMA_VERSION}.candidate",
        "candidate_id": _stable_id(payload),
        "policy_id": policy_id,
        "market_slug": row.get("market_slug"),
        "event_slug": row.get("event_slug"),
        "category": row.get("category"),
        "source_watch_id": row.get("watch_id"),
        "token_id": token_id,
        "action": action,
        "side": side,
        "entry_time": row.get("entry_time") or row.get("recorded_at") or row.get("timestamp"),
        "entry_price": entry_price,
        "q_effective": q_effective,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "depth": ask_depth,
        "spread": spread,
        "imbalance": _imbalance(row),
        "price_momentum_5m": row.get("price_momentum_5m"),
        "price_momentum_15m": row.get("price_momentum_15m"),
        "orderbook_snapshot_id": orderbook_snapshot_id,
        "expected_horizon": expected_horizon,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def build_microstructure_policy_sweep(
    *,
    watch_rows: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]] = (),
    active_markets: Iterable[Dict[str, Any]] = (),
    imbalance_threshold: float = 0.55,
    momentum_threshold: float = 0.05,
    min_spread: float = 0.02,
    max_spread: float = 0.18,
    min_depth: float = 25.0,
    maker_margin: float = 0.01,
    include_inverse: bool = True,
    include_baseline: bool = True,
) -> Dict[str, Any]:
    watches = [row for row in watch_rows if isinstance(row, dict)]
    markets = _market_by_slug(active_markets)
    candidates: List[Dict[str, Any]] = []
    taker_fills: List[Dict[str, Any]] = []
    maker_quotes: List[Dict[str, Any]] = []
    blockers: Counter[str] = Counter()
    by_policy: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    snapshot_by_id = {
        str(row.get("orderbook_snapshot_id")): row
        for row in orderbook_snapshots
        if isinstance(row, dict) and row.get("orderbook_snapshot_id")
    }

    for row in watches:
        market = markets.get(str(row.get("market_slug") or "")) or {}
        if not market:
            blockers["missing_active_market_metadata"] += 1
        imbalance = _imbalance(row)
        bid_depth = _safe_float(row.get("bid_depth"))
        ask_depth = _safe_float(row.get("ask_depth") or row.get("depth"))
        spread = _safe_float(row.get("spread"))

        # Policy 1: taker follow imbalance.
        follow_side: Optional[str] = None
        if imbalance is None:
            blockers["taker_follow_imbalance:missing_imbalance"] += 1
        elif abs(imbalance) < float(imbalance_threshold):
            blockers["taker_follow_imbalance:imbalance_below_threshold"] += 1
        else:
            side = "YES" if imbalance >= float(imbalance_threshold) else "NO"
            follow_side = side
            quote, gap = _direct_side_quote(market, side)
            if quote is None:
                blockers[f"taker_follow_imbalance:{gap}"] += 1
            elif quote.get("best_ask") is not None and (
                quote["best_ask"] < 0.005
                or (quote.get("ask_depth") is not None and float(quote.get("ask_depth") or 0.0) < float(min_depth))
                or quote.get("spread") is None
                or float(quote.get("spread") or 999.0) > float(max_spread)
            ):
                reason = (
                    "dust_price"
                    if quote["best_ask"] < 0.005
                    else "depth_insufficient"
                    if quote.get("ask_depth") is not None and float(quote.get("ask_depth") or 0.0) < float(min_depth)
                    else "spread_too_wide_or_missing"
                )
                blockers[f"taker_follow_imbalance:{reason}"] += 1
            else:
                candidate = _base_candidate(
                    row,
                    policy_id="taker_follow_imbalance",
                    action=f"buy_{side.lower()}",
                    side=side,
                    token_id=str(quote["token_id"]),
                    entry_price=float(quote["best_ask"]),
                    q_effective=float(quote["best_ask"]),
                    spread=quote.get("spread"),
                    bid_depth=quote.get("bid_depth"),
                    ask_depth=quote.get("ask_depth"),
                    orderbook_snapshot_id=row.get("orderbook_snapshot_id"),
                )
                candidates.append(candidate)
                fill = {**candidate, "schema_version": f"{SCHEMA_VERSION}.taker_fill", "fill_id": candidate["candidate_id"], "source": "microstructure_policy_sweep"}
                taker_fills.append(fill)

        # Policy 2: inverse of the same imbalance signal.
        if include_inverse:
            if imbalance is None:
                blockers["taker_inverse_imbalance:missing_imbalance"] += 1
            elif abs(imbalance) < float(imbalance_threshold):
                blockers["taker_inverse_imbalance:imbalance_below_threshold"] += 1
            else:
                side = "NO" if (follow_side or ("YES" if imbalance >= float(imbalance_threshold) else "NO")) == "YES" else "YES"
                quote, gap = _direct_side_quote(market, side)
                if quote is None:
                    blockers[f"taker_inverse_imbalance:{gap}"] += 1
                elif quote.get("best_ask") is not None and (
                    quote["best_ask"] < 0.005
                    or (quote.get("ask_depth") is not None and float(quote.get("ask_depth") or 0.0) < float(min_depth))
                    or quote.get("spread") is None
                    or float(quote.get("spread") or 999.0) > float(max_spread)
                ):
                    reason = (
                        "dust_price"
                        if quote["best_ask"] < 0.005
                        else "depth_insufficient"
                        if quote.get("ask_depth") is not None and float(quote.get("ask_depth") or 0.0) < float(min_depth)
                        else "spread_too_wide_or_missing"
                    )
                    blockers[f"taker_inverse_imbalance:{reason}"] += 1
                else:
                    candidate = _base_candidate(
                        row,
                        policy_id="taker_inverse_imbalance",
                        action=f"buy_{side.lower()}",
                        side=side,
                        token_id=str(quote["token_id"]),
                        entry_price=float(quote["best_ask"]),
                        q_effective=float(quote["best_ask"]),
                        spread=quote.get("spread"),
                        bid_depth=quote.get("bid_depth"),
                        ask_depth=quote.get("ask_depth"),
                        orderbook_snapshot_id=row.get("orderbook_snapshot_id"),
                    )
                    candidates.append(candidate)
                    taker_fills.append({**candidate, "schema_version": f"{SCHEMA_VERSION}.taker_fill", "fill_id": candidate["candidate_id"], "source": "microstructure_policy_sweep_counterfactual"})

        # Policy 3: taker mean reversion.
        momentum = _safe_float(row.get("price_momentum_5m") or row.get("price_momentum_15m"))
        if momentum is None:
            blockers["taker_mean_reversion:missing_momentum"] += 1
        elif abs(momentum) < float(momentum_threshold):
            blockers["taker_mean_reversion:momentum_below_threshold"] += 1
        else:
            side = "NO" if momentum > 0 else "YES"
            quote, gap = _direct_side_quote(market, side)
            if quote is None:
                blockers[f"taker_mean_reversion:{gap}"] += 1
            else:
                candidate = _base_candidate(
                    row,
                    policy_id="taker_mean_reversion",
                    action=f"buy_{side.lower()}",
                    side=side,
                    token_id=str(quote["token_id"]),
                    entry_price=float(quote["best_ask"]),
                    q_effective=float(quote["best_ask"]),
                    spread=quote.get("spread"),
                    bid_depth=quote.get("bid_depth"),
                    ask_depth=quote.get("ask_depth"),
                    orderbook_snapshot_id=row.get("orderbook_snapshot_id"),
                )
                candidates.append(candidate)
                taker_fills.append({**candidate, "schema_version": f"{SCHEMA_VERSION}.taker_fill", "fill_id": candidate["candidate_id"], "source": "microstructure_policy_sweep"})

        # Policy 4: no-trade spread baseline. This deliberately creates no fill.
        if include_baseline:
            if spread is None:
                blockers["spread_only_no_direction:missing_spread"] += 1
            elif spread < float(min_spread):
                blockers["spread_only_no_direction:spread_below_min"] += 1
            else:
                by_policy["spread_only_no_direction"] += 1

        # Policy 5: maker spread capture.
        if spread is None or spread < float(min_spread):
            blockers["maker_spread_capture:spread_below_min"] += 1
        elif ask_depth is None or ask_depth < float(min_depth):
            blockers["maker_spread_capture:depth_insufficient"] += 1
        else:
            quote_price = _maker_quote_price(row, maker_margin=float(maker_margin))
            if quote_price is None:
                blockers["maker_spread_capture:cannot_quote_inside_spread"] += 1
            else:
                side = str(row.get("side") or "YES").upper()
                token_id = str(row.get("token_id") or "")
                candidate = _base_candidate(
                    row,
                    policy_id="maker_spread_capture",
                    action="maker_bid",
                    side=side,
                    token_id=token_id,
                    entry_price=quote_price,
                    q_effective=quote_price,
                    spread=spread,
                    bid_depth=bid_depth,
                    ask_depth=ask_depth,
                    orderbook_snapshot_id=row.get("orderbook_snapshot_id"),
                )
                candidate["diagnostic_only"] = True
                quote_id = candidate["candidate_id"]
                quote = {
                    **candidate,
                    "schema_version": f"{SCHEMA_VERSION}.maker_quote",
                    "quote_id": quote_id,
                    "quote_price": quote_price,
                    "current_best_bid": row.get("best_bid"),
                    "current_best_ask": row.get("best_ask"),
                    "inferred_fill": False,
                    "fill_confidence": 0.0,
                    "source": "microstructure_policy_sweep",
                }
                candidates.append(candidate)
                maker_quotes.append(quote)

    for candidate in candidates:
        by_policy[str(candidate.get("policy_id") or "missing")] += 1
        by_category[str(candidate.get("category") or "missing")] += 1
    evaluated_variants = 3 + (1 if include_inverse else 0) + (1 if include_baseline else 0)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "input_watch_count": len(watches),
        "policy_variant_count": len(watches) * evaluated_variants,
        "policy_candidate_count": len(candidates),
        "taker_candidate_count": len(taker_fills),
        "inverse_candidate_count": len([row for row in candidates if row.get("policy_id") == "taker_inverse_imbalance"]),
        "maker_candidate_count": len(maker_quotes),
        "taker_fill_count": len(taker_fills),
        "maker_quote_count": len(maker_quotes),
        "maker_inferred_fill_count": 0,
        "blocker_counts": [{"reason": key, "count": value} for key, value in sorted(blockers.items())],
        "by_policy": [{"policy_id": key, "candidate_count": value} for key, value in sorted(by_policy.items())],
        "by_category": [{"category": key, "candidate_count": value} for key, value in sorted(by_category.items())],
        "top_candidates": sorted(candidates, key=lambda item: (item.get("policy_id") == "taker_follow_imbalance", float(item.get("spread") or 0.0)), reverse=True)[:25],
        "candidates": candidates,
        "taker_fills": taker_fills,
        "maker_quotes": maker_quotes,
        "snapshot_lookup_count": len(snapshot_by_id),
    }


def build_microstructure_followup_snapshots(
    *,
    active_markets: Iterable[Dict[str, Any]],
    taker_fills: Iterable[Dict[str, Any]] = (),
    maker_quotes: Iterable[Dict[str, Any]] = (),
    watch_rows: Iterable[Dict[str, Any]] = (),
    recorded_at: Optional[str] = None,
    max_watch_age_hours: float = 24.0,
) -> Dict[str, Any]:
    recorded_at = recorded_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    recorded_dt = _parse_utc(recorded_at) or datetime.now(timezone.utc)
    market_lookup = _market_by_slug(active_markets)
    rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    sources = [
        ("taker_fill", list(taker_fills or [])),
        ("maker_quote", list(maker_quotes or [])),
        ("watch_row", list(watch_rows or [])),
    ]
    for source, items in sources:
        for item in items:
            if not isinstance(item, dict):
                continue
            entry_dt = _parse_utc(item.get("entry_time") or item.get("recorded_at") or item.get("timestamp"))
            if source == "watch_row" and entry_dt is not None and (recorded_dt - entry_dt).total_seconds() > float(max_watch_age_hours) * 3600.0:
                skipped.append({"source": source, "market_slug": item.get("market_slug"), "token_id": item.get("token_id"), "reason": "watch_row_too_old"})
                continue
            market = market_lookup.get(str(item.get("market_slug") or "")) or {}
            book = _book_for_token(market, item.get("token_id"))
            if not book:
                skipped.append({"source": source, "candidate_id": item.get("candidate_id"), "market_slug": item.get("market_slug"), "token_id": item.get("token_id"), "reason": "missing_active_orderbook"})
                continue
            payload = {
                "source": source,
                "candidate_id": item.get("candidate_id"),
                "fill_id": item.get("fill_id"),
                "quote_id": item.get("quote_id"),
                "watch_id": item.get("watch_id") or item.get("source_watch_id"),
                "market_slug": item.get("market_slug"),
                "token_id": item.get("token_id"),
                "recorded_at": recorded_at,
                "best_bid": book.get("best_bid"),
                "best_ask": book.get("best_ask"),
            }
            rows.append(
                {
                    "schema_version": f"{SCHEMA_VERSION}.followup_orderbook_snapshot",
                    "orderbook_snapshot_id": _stable_id(payload),
                    "candidate_id": item.get("candidate_id"),
                    "fill_id": item.get("fill_id"),
                    "quote_id": item.get("quote_id"),
                    "watch_id": item.get("watch_id") or item.get("source_watch_id"),
                    "market_slug": item.get("market_slug"),
                    "token_id": item.get("token_id"),
                    "recorded_at": recorded_at,
                    "timestamp": recorded_at,
                    "best_bid": _safe_float(book.get("best_bid")),
                    "best_ask": _safe_float(book.get("best_ask")),
                    "bid_ladder": book.get("bid_ladder") if isinstance(book.get("bid_ladder"), list) else book.get("bids") if isinstance(book.get("bids"), list) else [],
                    "ask_ladder": book.get("ask_ladder") if isinstance(book.get("ask_ladder"), list) else book.get("asks") if isinstance(book.get("asks"), list) else [],
                    "spread": _safe_float(book.get("spread")),
                    "source": f"microstructure_{source}_followup",
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
    return {
        "schema_version": f"{SCHEMA_VERSION}.followup_snapshot_report",
        "snapshot_count": len(rows),
        "skipped_count": len(skipped),
        "snapshots": rows,
        "skipped": skipped,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict) and row.get("token_id"):
            grouped[str(row["token_id"])].append(row)
    for members in grouped.values():
        members.sort(key=lambda row: str(row.get("timestamp") or row.get("recorded_at") or ""))
    return grouped


def _nearest(rows: List[Dict[str, Any]], target: datetime, tolerance_seconds: int) -> tuple[Optional[Dict[str, Any]], Optional[float]]:
    best: Optional[Dict[str, Any]] = None
    best_lag: Optional[float] = None
    for row in rows:
        ts = _parse_utc(row.get("timestamp") or row.get("recorded_at"))
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


def build_microstructure_policy_markout_report(
    *,
    taker_fills: Iterable[Dict[str, Any]],
    maker_quotes: Iterable[Dict[str, Any]] = (),
    orderbook_snapshots: Iterable[Dict[str, Any]] = (),
    horizons: Tuple[int, ...] = (300, 900, 3600, 21600),
) -> Dict[str, Any]:
    fills = [row for row in taker_fills if isinstance(row, dict)]
    quotes = [row for row in maker_quotes if isinstance(row, dict)]
    snapshots = _by_token(orderbook_snapshots)
    markouts: List[Dict[str, Any]] = []
    tolerance = {300: 90, 900: 180, 3600: 600, 21600: 1800}
    for fill in fills:
        token_id = str(fill.get("token_id") or "")
        entry_dt = _parse_utc(fill.get("entry_time") or fill.get("recorded_at") or fill.get("timestamp"))
        q_effective = _safe_float(fill.get("q_effective") or fill.get("entry_price"))
        for horizon in horizons:
            target_dt = entry_dt + timedelta(seconds=int(horizon)) if entry_dt else None
            base = {
                "schema_version": f"{SCHEMA_VERSION}.markout",
                "candidate_id": fill.get("candidate_id"),
                "fill_id": fill.get("fill_id"),
                "quote_id": None,
                "market_slug": fill.get("market_slug"),
                "token_id": token_id,
                "action": fill.get("action"),
                "side": fill.get("side"),
                "policy_id": fill.get("policy_id"),
                "category": fill.get("category"),
                "horizon_seconds": horizon,
                "entry_time": fill.get("entry_time"),
                "q_effective": q_effective,
                "target_time": target_dt.isoformat().replace("+00:00", "Z") if target_dt else None,
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
            if entry_dt is None:
                markouts.append({**base, "missing_snapshot_reason": "missing_entry_time"})
                continue
            if q_effective is None:
                markouts.append({**base, "missing_snapshot_reason": "missing_entry_price"})
                continue
            matched, lag = _nearest(snapshots.get(token_id) or [], target_dt, tolerance.get(int(horizon), 300))
            if matched is None:
                markouts.append({**base, "missing_snapshot_reason": "missing_later_snapshot"})
                continue
            exit_price = _safe_float(matched.get("best_bid") or matched.get("price_mid") or matched.get("price"))
            if exit_price is None:
                markouts.append({**base, "matched_snapshot_time": matched.get("recorded_at"), "match_lag_seconds": lag, "missing_snapshot_reason": "missing_exit_bid_or_mid"})
                continue
            markouts.append(
                {
                    **base,
                    "matched_snapshot_time": matched.get("recorded_at") or matched.get("timestamp"),
                    "match_lag_seconds": lag,
                    "exit_bid_or_mid": exit_price,
                    "markout_cents": round((exit_price - q_effective) * 100.0, 6),
                }
            )

    for quote in quotes:
        quote_id = quote.get("quote_id") or quote.get("candidate_id")
        if not quote.get("inferred_fill"):
            markouts.append(
                {
                    "schema_version": f"{SCHEMA_VERSION}.maker_quote_markout",
                    "candidate_id": quote.get("candidate_id"),
                    "quote_id": quote_id,
                    "market_slug": quote.get("market_slug"),
                    "token_id": quote.get("token_id"),
                    "policy_id": quote.get("policy_id"),
                    "category": quote.get("category"),
                    "horizon_seconds": None,
                    "q_effective": quote.get("q_effective"),
                    "markout_cents": None,
                    "missing_snapshot_reason": "quote_not_filled",
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
    available = [row for row in markouts if row.get("markout_cents") is not None]
    missing = [row for row in markouts if row.get("markout_cents") is None]
    values = [float(row["markout_cents"]) for row in available]
    return {
        "schema_version": f"{SCHEMA_VERSION}.markout_report",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "taker_fill_count": len(fills),
        "maker_quote_count": len(quotes),
        "maker_inferred_fill_count": len([row for row in quotes if row.get("inferred_fill")]),
        "available_markout_count": len(available),
        "mean_markout_cents": _mean(values),
        "mean_markout_by_horizon": _bucket_summary(available, "horizon_seconds"),
        "by_policy": _bucket_summary(available, "policy_id"),
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
    "build_microstructure_followup_snapshots",
    "build_microstructure_policy_markout_report",
    "build_microstructure_policy_sweep",
    "load_json",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
