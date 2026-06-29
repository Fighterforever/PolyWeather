from __future__ import annotations

import json
import math
import re
import urllib.request
import hashlib
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.binance_crypto_history import binance_pair_for_asset, fetch_binance_klines, verify_high_since_start
from src.trading.polymarket_alpha.crypto_market_semantics import classify_crypto_semantics, resolve_market_creation_time
from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl
from src.trading.polymarket_readonly import PolymarketReadonlyClient


SCHEMA_VERSION = "polyweather_polymarket_alpha_crypto_probability_model.v1"
CRYPTO_CATEGORIES = {"crypto", "bitcoin", "ethereum", "crypto_prices"}


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


def fetch_binance_spot(symbol: str) -> Optional[float]:
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}USDT"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return _safe_float(payload.get("price"))


def parse_crypto_threshold_market(market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text = " ".join(
        str(market.get(field) or "")
        for field in ("title", "question", "description", "market_slug")
    ).lower()
    asset = None
    if re.search(r"\b(btc|bitcoin)\b", text):
        asset = "BTC"
    elif re.search(r"\b(eth|ethereum)\b", text):
        asset = "ETH"
    if asset is None:
        return None
    direction = None
    if re.search(r"\b(above|over|greater than|hit|reach|exceed)\b", text):
        direction = "above"
    if re.search(r"\b(below|under|less than)\b", text):
        direction = "below"
    if direction is None:
        return None
    candidates = []
    for match in re.finditer(r"(\$?)\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?|[0-9]{1,8}(?:\.\d+)?)\s*([km]\b)?", text):
        has_currency = bool(match.group(1))
        suffix_token = str(match.group(3) or "").strip().lower()
        has_k_suffix = suffix_token == "k"
        has_m_suffix = suffix_token == "m"
        before = text[max(0, match.start() - 28): match.start()]
        after = text[match.end(): match.end() + 12]
        has_price_context = bool(re.search(r"(above|over|below|under|hit|reach|exceed|price|target|at\s+least|less\s+than)[^a-z0-9]{0,10}$", before))
        looks_like_date_year = bool(1900 <= float(match.group(2).replace(",", "")) <= 2100 and re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|by)[^a-z0-9-]{0,3}[- ]?(?:[0-3]?[0-9])?[- ]?$", before))
        if looks_like_date_year and not (has_currency or has_k_suffix or has_price_context):
            continue
        if not (has_currency or has_k_suffix or has_price_context):
            continue
        raw = match.group(2).replace(",", "")
        value = _safe_float(raw)
        if value is None:
            continue
        if has_k_suffix and value < 1000:
            value *= 1000
        if has_m_suffix and value < 1_000_000:
            value *= 1_000_000
        # Reject date/year artifacts unless the nearby wording clearly makes this a price level.
        if 1900 <= value <= 2100 and not (has_currency or has_k_suffix or re.search(r"(reach|hit|above|over|below|under|price|target)", before + after)):
            continue
        if (asset == "BTC" and value >= 10000) or (asset == "ETH" and value >= 500):
            candidates.append(value)
    if not candidates:
        return None
    threshold = max(candidates) if direction == "above" else min(candidates)
    target_time = _parse_utc(market.get("end_time"))
    return {
        "asset": asset,
        "direction": direction,
        "threshold": float(threshold),
        "target_time": target_time.isoformat().replace("+00:00", "Z") if target_time else None,
    }


def lognormal_probability_above(*, spot: float, threshold: float, annual_vol: float, years: float) -> float:
    if spot <= 0 or threshold <= 0:
        return 0.5
    sigma_t = max(1e-6, float(annual_vol) * math.sqrt(max(1e-8, float(years))))
    z = (math.log(float(spot) / float(threshold)) - 0.5 * sigma_t * sigma_t) / sigma_t
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def first_passage_probability_upper(*, spot: float, threshold: float, annual_vol: float, years: float) -> float:
    if spot <= 0 or threshold <= 0:
        return 0.5
    if spot >= threshold:
        return 1.0
    sigma_t = max(1e-6, float(annual_vol) * math.sqrt(max(1e-8, float(years))))
    z = math.log(float(threshold) / float(spot)) / sigma_t
    return max(0.0, min(1.0, 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))))


def fetch_binance_high_since_start(asset: str, start_time: str, end_time: str) -> Optional[float]:
    result = verify_high_since_start(
        asset=asset,
        threshold=0.0,
        market_creation_time=start_time,
        current_time=end_time,
    )
    return _safe_float(result.get("max_high_since_start"))


def _call_high_since_start_fetcher(
    fetcher: Any,
    *,
    asset: str,
    threshold: float,
    start_time: str,
    generated_at: str,
    cache_dir: str | Path,
) -> Dict[str, Any]:
    if fetcher is None:
        return verify_high_since_start(
            asset=asset,
            threshold=threshold,
            market_creation_time=start_time,
            current_time=generated_at,
            cache_dir=cache_dir,
        )
    try:
        result = fetcher(asset=asset, threshold=threshold, market_creation_time=start_time, current_time=generated_at)
    except TypeError:
        result = fetcher(asset, start_time, generated_at)
    if isinstance(result, dict):
        return result
    high = _safe_float(result)
    return {
        "asset": asset,
        "market_creation_time": start_time,
        "verification_start_time": start_time,
        "verification_end_time": generated_at,
        "max_high_since_start": high,
        "max_high_at": None,
        "barrier_already_touched": bool(high is not None and high >= threshold),
        "high_since_start_verified": high is not None,
        "kline_count": 0,
        "gap_reason": None if high is not None else "high_since_start_unverified",
        "data_source": "test_or_external_high_since_start_fetcher",
        "paper_only": True,
        "live_order_path": False,
    }


def _token_for_outcome(market: Dict[str, Any], outcome: str) -> Optional[str]:
    wanted = str(outcome or "").strip().lower()
    for label, token_id in (market.get("token_id_by_outcome") or {}).items():
        if str(label or "").strip().lower() == wanted:
            return str(token_id)
    return None


def _book_for_outcome(market: Dict[str, Any], outcome: str) -> Dict[str, Any]:
    token_id = _token_for_outcome(market, outcome)
    books = market.get("orderbooks") if isinstance(market.get("orderbooks"), dict) else {}
    return books.get(str(token_id)) if token_id and isinstance(books.get(str(token_id)), dict) else {}


def _normalize_book_payload(book: Any) -> Dict[str, Any]:
    if book is None:
        return {}
    if is_dataclass(book):
        return asdict(book)
    if isinstance(book, dict):
        return dict(book)
    return {}


def fetch_missing_orderbooks_for_markets(
    markets: Iterable[Dict[str, Any]],
    *,
    max_tokens: int = 200,
    fetcher: Optional[Any] = None,
) -> Dict[str, Any]:
    fetcher = fetcher or PolymarketReadonlyClient().get_order_book
    fetched: Dict[str, Dict[str, Any]] = {}
    errors: Dict[str, str] = {}
    attempted = 0
    for market in markets:
        if not isinstance(market, dict):
            continue
        books = market.setdefault("orderbooks", {})
        if not isinstance(books, dict):
            books = {}
            market["orderbooks"] = books
        for token in market.get("token_ids") or []:
            token_id = str(token)
            if not token_id or token_id in books or token_id in fetched or token_id in errors:
                continue
            if attempted >= int(max_tokens):
                break
            attempted += 1
            try:
                book = _normalize_book_payload(fetcher(token_id))
            except Exception as exc:
                errors[token_id] = str(exc)
                continue
            if book:
                fetched[token_id] = book
                books[token_id] = book
            else:
                errors[token_id] = "empty_orderbook_response"
    return {"attempted": attempted, "fetched": fetched, "errors": errors}


def _side_quote(market: Dict[str, Any], side: str) -> tuple[Optional[Dict[str, Any]], str]:
    token_id = _token_for_outcome(market, side)
    if not token_id:
        return None, f"missing_{side.lower()}_token_id"
    book = _book_for_outcome(market, side)
    if not book:
        return None, f"{side.lower()}_token_not_found_or_orderbook_missing"
    best_ask = _safe_float(book.get("best_ask"))
    best_bid = _safe_float(book.get("best_bid"))
    spread = _safe_float(book.get("spread"))
    depth = _safe_float(book.get("ask_depth_usdc_3c"))
    if best_ask is None:
        return None, f"{side.lower()}_no_ask_depth"
    if best_bid is None:
        return None, f"{side.lower()}_no_bid_depth"
    return {
        "token_id": str(token_id),
        "best_ask": best_ask,
        "best_bid": best_bid,
        "spread": spread,
        "depth": depth,
    }, ""


def _candidate_from_probability(
    *,
    market: Dict[str, Any],
    parsed: Dict[str, Any],
    p_yes: float,
    p_yes_lcb: float,
    p_yes_ucb: float,
    probability_semantics: str,
    probability_details: Optional[Dict[str, Any]],
    min_edge: float,
    cost: float,
    min_depth: float,
    max_spread: float,
) -> tuple[Optional[Dict[str, Any]], Optional[str], List[Dict[str, Any]], bool]:
    side_rows: List[Dict[str, Any]] = []
    executable_available = False
    p_no = 1.0 - p_yes
    p_no_lcb = max(0.0, 1.0 - p_yes_ucb)
    p_no_ucb = min(1.0, 1.0 - p_yes_lcb)
    for side in ("YES", "NO"):
        quote, gap = _side_quote(market, side)
        side_p = p_yes if side == "YES" else 1.0 - p_yes
        side_lcb = p_yes_lcb if side == "YES" else p_no_lcb
        side_ucb = p_yes_ucb if side == "YES" else p_no_ucb
        row = {
            "market_slug": market.get("market_slug"),
            "token_id": _token_for_outcome(market, side),
            "side": side,
            "asset": parsed.get("asset"),
            "threshold": parsed.get("threshold"),
            "target_time": parsed.get("target_time"),
            "p_yes_model": round(p_yes, 8),
            "p_yes_lcb": round(p_yes_lcb, 8),
            "p_yes_ucb": round(p_yes_ucb, 8),
            "p_no_model": round(p_no, 8),
            "p_no_lcb": round(p_no_lcb, 8),
            "p_no_ucb": round(p_no_ucb, 8),
            "p_trade_model": round(side_p, 8),
            "p_trade_lcb": round(side_lcb, 8),
            "p_trade_ucb": round(side_ucb, 8),
            "p_model": round(side_p, 8),
            "p_lcb": round(side_lcb, 8),
            "p_ucb": round(side_ucb, 8),
            "best_ask": None,
            "spread": None,
            "depth": None,
            "EV_safe": None,
            "blocker": gap or None,
            "probability_semantics": probability_semantics,
            **(probability_details or {}),
        }
        if quote:
            row.update({
                "token_id": quote.get("token_id"),
                "best_ask": quote.get("best_ask"),
                "spread": quote.get("spread"),
                "depth": quote.get("depth"),
            })
            if quote.get("best_ask") is not None:
                executable_available = True
            if quote["best_ask"] < 0.005:
                row["blocker"] = "dust_price"
            elif quote.get("depth") is None or float(quote.get("depth") or 0.0) < min_depth:
                row["blocker"] = "no_ask_depth"
            elif quote.get("spread") is None or float(quote.get("spread") or 999.0) > max_spread:
                row["blocker"] = "spread_too_wide"
            else:
                ev_safe = side_lcb - float(quote["best_ask"]) - cost
                row["EV_safe"] = round(ev_safe, 8)
                row["blocker"] = "ev_below_min" if ev_safe < min_edge else None
        side_rows.append(row)
    ranked = sorted(side_rows, key=lambda row: float(row.get("EV_safe") if row.get("EV_safe") is not None else -1e9), reverse=True)
    best = ranked[0] if ranked else {}
    if best.get("EV_safe") is None or float(best.get("EV_safe") or -1e9) < min_edge:
        return None, str(best.get("blocker") or "no_executable_side"), side_rows, executable_available
    side = str(best.get("side"))
    q_effective = float(best.get("best_ask"))
    yes_book = _side_quote(market, "YES")[0]
    market_price = None
    if yes_book and yes_book.get("best_bid") is not None and yes_book.get("best_ask") is not None:
        market_price = round((float(yes_book["best_bid"]) + float(yes_book["best_ask"])) / 2.0, 8)
    return {
        "market_slug": market.get("market_slug"),
        "token_id": best.get("token_id"),
        "category": market.get("category"),
        "side": side,
        "asset": parsed.get("asset"),
        "direction": parsed.get("direction"),
        "threshold": parsed.get("threshold"),
        "target_time": parsed.get("target_time"),
        "p_model": round(p_yes, 8),
        "p_lcb": round(p_yes_lcb, 8),
        "p_ucb": round(p_yes_ucb, 8),
        "p_yes_model": round(p_yes, 8),
        "p_yes_lcb": round(p_yes_lcb, 8),
        "p_yes_ucb": round(p_yes_ucb, 8),
        "p_no_model": round(p_no, 8),
        "p_no_lcb": round(p_no_lcb, 8),
        "p_no_ucb": round(p_no_ucb, 8),
        "p_trade_model": best.get("p_trade_model"),
        "p_trade_lcb": best.get("p_trade_lcb"),
        "p_trade_ucb": best.get("p_trade_ucb"),
        "market_price": market_price,
        "best_ask": q_effective,
        "q_effective": q_effective,
        "cost": cost,
        "EV_safe": round(float(best.get("EV_safe")), 8),
        "model_source": "crypto_lognormal_threshold_model",
        "probability_semantics": probability_semantics,
        **(probability_details or {}),
        "orderbook_snapshot": _book_for_outcome(market, side),
        "confidence": 0.35,
        "orderbook_snapshot_id": None,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }, None, side_rows, executable_available


def build_crypto_probability_edge_report(
    *,
    active_markets: Iterable[Dict[str, Any]],
    spot_prices: Optional[Dict[str, float]] = None,
    annual_vols: Optional[Dict[str, float]] = None,
    generated_at: Optional[str] = None,
    fetch_orderbooks: bool = False,
    orderbook_fetcher: Optional[Any] = None,
    max_orderbook_tokens: int = 200,
    min_edge: float = 0.02,
    cost: float = 0.01,
    min_depth: float = 10.0,
    max_spread: float = 0.15,
    model_haircut: float = 0.08,
    high_since_start_fetcher: Optional[Any] = None,
    high_since_start_cache_dir: str | Path = "evidence/polymarket_alpha/binance_klines",
) -> Dict[str, Any]:
    generated_dt = _parse_utc(generated_at) if generated_at else datetime.now(timezone.utc)
    generated_at = generated_dt.isoformat().replace("+00:00", "Z")
    spot_prices = spot_prices or {}
    annual_vols = annual_vols or {"BTC": 0.55, "ETH": 0.70}
    active_rows = [dict(row) for row in active_markets if isinstance(row, dict)]
    orderbook_fetch_summary = {"attempted": 0, "fetched": {}, "errors": {}}
    if fetch_orderbooks:
        crypto_fetch_rows = [
            row for row in active_rows
            if row.get("active")
            and str(row.get("category") or "") in CRYPTO_CATEGORIES
            and parse_crypto_threshold_market(row) is not None
        ]
        orderbook_fetch_summary = fetch_missing_orderbooks_for_markets(
            crypto_fetch_rows,
            max_tokens=int(max_orderbook_tokens),
            fetcher=orderbook_fetcher,
        )
    candidates: List[Dict[str, Any]] = []
    watch_rows: List[Dict[str, Any]] = []
    near_misses: List[Dict[str, Any]] = []
    gaps: Counter[str] = Counter()
    parsed_count = 0
    model_ready_count = 0
    executable_price_available_count = 0
    touch_barrier_market_count = 0
    high_since_start_verified_count = 0
    barrier_already_touched_count = 0
    verified_not_touched_count = 0
    near_miss_watch: List[Dict[str, Any]] = []
    near_miss_orderbook_snapshots: List[Dict[str, Any]] = []
    for market in active_rows:
        if not isinstance(market, dict) or not market.get("active"):
            continue
        category = str(market.get("category") or "")
        if category not in CRYPTO_CATEGORIES:
            continue
        parsed = parse_crypto_threshold_market(market)
        if parsed is None:
            gaps["unparseable_crypto_threshold"] += 1
            continue
        parsed_count += 1
        asset = str(parsed.get("asset"))
        spot = _safe_float(spot_prices.get(asset))
        target = _parse_utc(parsed.get("target_time"))
        if spot is None:
            gaps[f"missing_{asset.lower()}_spot_price"] += 1
            continue
        if target is None:
            gaps["missing_target_time"] += 1
            continue
        years = max(1 / 365, (target - generated_dt).total_seconds() / (365.0 * 86400.0))
        annual_vol = float(annual_vols.get(asset, 0.60))
        p_yes_terminal_above = lognormal_probability_above(
            spot=spot,
            threshold=float(parsed["threshold"]),
            annual_vol=annual_vol,
            years=years,
        )
        p_yes_terminal = p_yes_terminal_above if parsed.get("direction") == "above" else 1.0 - p_yes_terminal_above
        semantics_type = classify_crypto_semantics(market)
        creation = resolve_market_creation_time(
            market,
            generated_at=generated_at,
            end_time=parsed.get("target_time") or market.get("end_time"),
            allow_proxy=True,
        )
        start_time = creation.get("market_creation_time")
        high_since_start = None
        max_high_at = None
        high_kline_count = 0
        high_gap_reason = None
        high_verified = False
        barrier_already_touched = False
        probability_semantics = semantics_type if semantics_type != "ambiguous" else "ambiguous"
        p_yes_touch = None
        if semantics_type == "touch_barrier":
            touch_barrier_market_count += 1
            probability_semantics = "touch_barrier"
            if spot >= float(parsed["threshold"]):
                high_since_start = spot
                max_high_at = generated_at
                high_verified = True
                barrier_already_touched = True
                p_yes_touch = 1.0
            elif start_time:
                high_result = _call_high_since_start_fetcher(
                    high_since_start_fetcher,
                    asset=asset,
                    threshold=float(parsed["threshold"]),
                    start_time=start_time,
                    generated_at=generated_at,
                    cache_dir=high_since_start_cache_dir,
                )
                high_since_start = _safe_float(high_result.get("max_high_since_start"))
                max_high_at = high_result.get("max_high_at")
                high_kline_count = int(high_result.get("kline_count") or 0)
                high_gap_reason = high_result.get("gap_reason")
                high_verified = bool(high_result.get("high_since_start_verified"))
                barrier_already_touched = bool(high_result.get("barrier_already_touched"))
                if barrier_already_touched:
                    p_yes_touch = 1.0
            else:
                high_gap_reason = str(creation.get("gap_reason") or "start_time_unverified")
            if p_yes_touch is None:
                p_yes_touch = first_passage_probability_upper(
                    spot=spot,
                    threshold=float(parsed["threshold"]),
                    annual_vol=annual_vol,
                    years=years,
                )
            if not high_verified:
                gaps[str(high_gap_reason or "high_since_start_unverified")] += 1
            else:
                high_since_start_verified_count += 1
                if barrier_already_touched:
                    barrier_already_touched_count += 1
                    gaps["barrier_already_touched"] += 1
                else:
                    verified_not_touched_count += 1
        elif semantics_type == "ambiguous":
            gaps["ambiguous_crypto_market_semantics"] += 1
        p_yes = p_yes_touch if semantics_type == "touch_barrier" else p_yes_terminal
        haircut = float(model_haircut)
        p_lcb = max(0.0, p_yes - haircut)
        p_ucb = min(1.0, p_yes + haircut)
        model_ready_count += 1
        probability_details = {
            "semantics_type": semantics_type,
            "current_model_semantics": probability_semantics,
            "spot": spot,
            "annual_vol": annual_vol,
            "generated_at": generated_at,
            "p_yes_terminal": round(p_yes_terminal, 8),
            "p_yes_touch": round(p_yes_touch, 8) if p_yes_touch is not None else None,
            "p_no_touch": round(1.0 - p_yes_touch, 8) if p_yes_touch is not None else None,
            "probability_semantics": probability_semantics,
            "historical_high_since_start": high_since_start,
            "max_high_since_start": high_since_start,
            "max_high_at": max_high_at,
            "high_since_start_verified": high_verified,
            "barrier_already_touched": barrier_already_touched,
            "high_since_start_kline_count": high_kline_count,
            "high_since_start_gap_reason": high_gap_reason,
            "parsed_start_time": start_time,
            "market_creation_time": start_time,
            "creation_time_source": creation.get("creation_time_source"),
            "creation_time_proxy": creation.get("creation_time_proxy"),
            "can_generate_official_candidate": creation.get("can_generate_official_candidate"),
            "can_generate_shadow_watch": creation.get("can_generate_shadow_watch"),
        }
        if semantics_type == "touch_barrier" and (not high_verified or barrier_already_touched):
            candidate = None
            gap = "barrier_already_touched" if barrier_already_touched else str(high_gap_reason or "high_since_start_unverified")
            side_rows = []
            executable_available = any(_side_quote(market, side)[0] is not None for side in ("YES", "NO"))
            for side in ("YES", "NO"):
                quote, quote_gap = _side_quote(market, side)
                side_rows.append(
                    {
                        "market_slug": market.get("market_slug"),
                        "token_id": _token_for_outcome(market, side),
                        "side": side,
                        "asset": parsed.get("asset"),
                        "threshold": parsed.get("threshold"),
                        "target_time": parsed.get("target_time"),
                        "p_yes_model": round(p_yes, 8),
                        "p_yes_lcb": round(p_lcb, 8),
                        "p_yes_ucb": round(p_ucb, 8),
                        "p_no_model": round(1.0 - p_yes, 8),
                        "p_no_lcb": round(max(0.0, 1.0 - p_ucb), 8),
                        "p_no_ucb": round(min(1.0, 1.0 - p_lcb), 8),
                        "p_trade_model": round(p_yes if side == "YES" else 1.0 - p_yes, 8),
                        "p_trade_lcb": round(p_lcb if side == "YES" else max(0.0, 1.0 - p_ucb), 8),
                        "p_trade_ucb": round(p_ucb if side == "YES" else min(1.0, 1.0 - p_lcb), 8),
                        "best_ask": quote.get("best_ask") if quote else None,
                        "spread": quote.get("spread") if quote else None,
                        "depth": quote.get("depth") if quote else None,
                        "EV_safe": None,
                        "blocker": gap or quote_gap,
                        **probability_details,
                    }
                )
        else:
            candidate, gap, side_rows, executable_available = _candidate_from_probability(
                market=market,
                parsed=parsed,
                p_yes=p_yes,
                p_yes_lcb=p_lcb,
                p_yes_ucb=p_ucb,
                probability_semantics=probability_semantics,
                probability_details=probability_details,
                min_edge=float(min_edge),
                cost=float(cost),
                min_depth=float(min_depth),
                max_spread=float(max_spread),
            )
            if (
                candidate
                and semantics_type == "touch_barrier"
                and not bool(creation.get("can_generate_official_candidate"))
            ):
                candidate = None
                gap = "creation_time_proxy_not_official" if creation.get("creation_time_proxy") else str(creation.get("gap_reason") or "start_time_unverified")
        if executable_available:
            executable_price_available_count += 1
        for side_row in side_rows:
            near_row = {
                    "market_slug": market.get("market_slug"),
                    "token_id": side_row.get("token_id"),
                    "asset": side_row.get("asset"),
                    "threshold": side_row.get("threshold"),
                    "target_time": side_row.get("target_time"),
                    "side": side_row.get("side"),
                    "spot": side_row.get("spot"),
                    "annual_vol": side_row.get("annual_vol"),
                    "generated_at": side_row.get("generated_at"),
                    "p_model": side_row.get("p_model"),
                    "p_lcb": side_row.get("p_lcb"),
                    "p_ucb": side_row.get("p_ucb"),
                    "p_yes_model": side_row.get("p_yes_model"),
                    "p_yes_lcb": side_row.get("p_yes_lcb"),
                    "p_yes_ucb": side_row.get("p_yes_ucb"),
                    "p_no_model": side_row.get("p_no_model"),
                    "p_no_lcb": side_row.get("p_no_lcb"),
                    "p_no_ucb": side_row.get("p_no_ucb"),
                    "p_trade_model": side_row.get("p_trade_model"),
                    "p_trade_lcb": side_row.get("p_trade_lcb"),
                    "p_trade_ucb": side_row.get("p_trade_ucb"),
                    "best_ask": side_row.get("best_ask"),
                    "spread": side_row.get("spread"),
                    "depth": side_row.get("depth"),
                    "EV_safe": side_row.get("EV_safe"),
                    "blocker": side_row.get("blocker"),
                    "paper_only": True,
                    "live_order_path": False,
                    "semantics_type": side_row.get("semantics_type"),
                    "probability_semantics": side_row.get("probability_semantics"),
                    "historical_high_since_start": side_row.get("historical_high_since_start"),
                    "max_high_since_start": side_row.get("max_high_since_start"),
                    "max_high_at": side_row.get("max_high_at"),
                    "high_since_start_verified": side_row.get("high_since_start_verified"),
                    "barrier_already_touched": side_row.get("barrier_already_touched"),
                    "high_since_start_gap_reason": side_row.get("high_since_start_gap_reason"),
                    "market_creation_time": side_row.get("market_creation_time"),
                    "creation_time_source": side_row.get("creation_time_source"),
                    "creation_time_proxy": side_row.get("creation_time_proxy"),
                    "can_generate_official_candidate": side_row.get("can_generate_official_candidate"),
                    "can_generate_shadow_watch": side_row.get("can_generate_shadow_watch"),
                    "q_effective": side_row.get("best_ask"),
                }
            near_misses.append(near_row)
            ev_safe = _safe_float(near_row.get("EV_safe"))
            if (
                semantics_type == "touch_barrier"
                and bool(near_row.get("high_since_start_verified"))
                and not bool(near_row.get("barrier_already_touched"))
                and near_row.get("best_ask") is not None
                and near_row.get("depth") is not None
                and ev_safe is not None
                and -0.005 <= ev_safe < float(min_edge)
            ):
                snapshot = _near_miss_orderbook_snapshot(
                    market=market,
                    side=str(near_row.get("side") or ""),
                    near_row=near_row,
                    recorded_at=generated_at,
                )
                if snapshot:
                    near_miss_orderbook_snapshots.append(snapshot)
                orderbook_snapshot_id = snapshot.get("orderbook_snapshot_id") if snapshot else None
                watch_row = {
                    **near_row,
                    "watch_id": _stable_watch_id({**near_row, "entry_time": generated_at}),
                    "entry_time": generated_at,
                    "recorded_at": generated_at,
                    "orderbook_snapshot_id": orderbook_snapshot_id,
                    "probability_semantics": "touch_barrier",
                    "high_since_start_verified": True,
                    "barrier_already_touched": False,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
                near_miss_watch.append(
                    watch_row
                )
        watch_rows.append({
            "market_slug": market.get("market_slug"),
            "category": category,
            "asset": asset,
            "direction": parsed.get("direction"),
            "threshold": parsed.get("threshold"),
            "spot": spot,
            "p_model": round(p_yes, 8),
            "p_lcb": round(p_lcb, 8),
            "p_ucb": round(p_ucb, 8),
            "p_yes_terminal": probability_details.get("p_yes_terminal"),
            "p_yes_touch": probability_details.get("p_yes_touch"),
            "p_no_touch": probability_details.get("p_no_touch"),
            "probability_semantics": probability_semantics,
            "semantics_type": semantics_type,
            "historical_high_since_start": high_since_start,
            "max_high_since_start": high_since_start,
            "max_high_at": max_high_at,
            "high_since_start_verified": high_verified,
            "barrier_already_touched": barrier_already_touched,
            "high_since_start_gap_reason": high_gap_reason,
            "market_creation_time": start_time,
            "creation_time_source": creation.get("creation_time_source"),
            "creation_time_proxy": creation.get("creation_time_proxy"),
            "can_generate_official_candidate": creation.get("can_generate_official_candidate"),
            "can_generate_shadow_watch": creation.get("can_generate_shadow_watch"),
            "gap": gap,
            "executable_price_available": executable_available,
            "paper_only": True,
            "live_order_path": False,
        })
        if candidate:
            candidates.append(candidate)
        elif gap:
            gaps[gap] += 1
    candidates = sorted(candidates, key=lambda row: float(row.get("EV_safe") or -1e9), reverse=True)
    near_misses = sorted(
        near_misses,
        key=lambda row: float(row.get("EV_safe") if row.get("EV_safe") is not None else -1e9),
        reverse=True,
    )
    near_miss_watch = sorted(
        near_miss_watch,
        key=lambda row: float(row.get("EV_safe") if row.get("EV_safe") is not None else -1e9),
        reverse=True,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "parsed_crypto_market_count": parsed_count,
        "model_ready_count": model_ready_count,
        "executable_price_available_count": executable_price_available_count,
        "candidate_count": len(candidates),
        "blocker_counts": [{"reason": key, "count": value} for key, value in sorted(gaps.items())],
        "gap_reasons": [{"reason": key, "count": value} for key, value in sorted(gaps.items())],
        "orderbook_fetch_attempt_count": orderbook_fetch_summary.get("attempted", 0),
        "orderbook_fetch_success_count": len(orderbook_fetch_summary.get("fetched", {})),
        "orderbook_fetch_error_count": len(orderbook_fetch_summary.get("errors", {})),
        "top_10_near_misses": near_misses[:10],
        "near_miss_watch_count": len(near_miss_watch),
        "best_near_miss_EV_safe": near_miss_watch[0].get("EV_safe") if near_miss_watch else None,
        "near_miss_watch_by_asset": _count_by(near_miss_watch, "asset"),
        "near_miss_watch_by_side": _count_by(near_miss_watch, "side"),
        "near_miss_orderbook_snapshot_count": len(near_miss_orderbook_snapshots),
        "near_miss_orderbook_snapshot_id_null_count": sum(1 for row in near_miss_watch if not row.get("orderbook_snapshot_id")),
        "touch_barrier_market_count": touch_barrier_market_count,
        "high_since_start_verified_count": high_since_start_verified_count,
        "barrier_already_touched_count": barrier_already_touched_count,
        "verified_not_touched_count": verified_not_touched_count,
        "touch_barrier_candidate_count": len([row for row in candidates if row.get("semantics_type") == "touch_barrier"]),
        "terminal_candidate_count": len([row for row in candidates if row.get("semantics_type") in {"terminal_above", "terminal_below", "close_above", "close_below"}]),
        "invalidated_due_semantics_count": int(
            sum(
                value
                for key, value in gaps.items()
                if key in {"high_since_start_unverified", "start_time_unverified", "ambiguous_crypto_market_semantics", "barrier_already_touched"}
                or str(key).startswith("binance_fetch_error")
            )
        ),
        "watch_rows": watch_rows,
        "near_misses": near_misses,
        "near_miss_watch": near_miss_watch,
        "near_miss_orderbook_snapshots": near_miss_orderbook_snapshots,
        "candidates": candidates,
    }


def _near_miss_orderbook_snapshot(
    *,
    market: Dict[str, Any],
    side: str,
    near_row: Dict[str, Any],
    recorded_at: str,
) -> Dict[str, Any]:
    book = _book_for_outcome(market, side)
    token_id = str(near_row.get("token_id") or "")
    if not token_id or not book:
        return {}
    snapshot_payload = {
        "token_id": token_id,
        "best_bid": _safe_float(book.get("best_bid")),
        "best_ask": _safe_float(book.get("best_ask")),
        "recorded_at": recorded_at,
        "source": "crypto_touch_near_miss_watch",
    }
    snapshot_id = hashlib.sha256(
        json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "schema_version": f"{SCHEMA_VERSION}.near_miss_orderbook_snapshot",
        "orderbook_snapshot_id": snapshot_id,
        "market_slug": market.get("market_slug"),
        "token_id": token_id,
        "side": side,
        "best_bid": snapshot_payload["best_bid"],
        "best_ask": snapshot_payload["best_ask"],
        "bid_ladder": book.get("bid_ladder") or book.get("bids") or [],
        "ask_ladder": book.get("ask_ladder") or book.get("asks") or [],
        "spread": _safe_float(book.get("spread")) if book.get("spread") is not None else near_row.get("spread"),
        "depth": _safe_float(book.get("ask_depth_usdc_3c")) if book.get("ask_depth_usdc_3c") is not None else near_row.get("depth"),
        "recorded_at": recorded_at,
        "source": "crypto_touch_near_miss_watch",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _stable_watch_id(row: Dict[str, Any]) -> str:
    text = json.dumps(
        {
            "market_slug": row.get("market_slug"),
            "token_id": row.get("token_id"),
            "side": row.get("side"),
            "market_creation_time": row.get("market_creation_time"),
            "target_time": row.get("target_time"),
            "entry_time": row.get("entry_time") or row.get("recorded_at"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _implied_touch_vol(
    *,
    spot: float,
    threshold: float,
    years: float,
    target_probability: float,
) -> Optional[float]:
    if spot <= 0 or threshold <= 0 or years <= 0:
        return None
    target_probability = max(0.0, min(1.0, float(target_probability)))
    if target_probability <= 0.0 or target_probability >= 1.0:
        return None
    low = 1e-6
    high = 5.0
    low_p = first_passage_probability_upper(spot=spot, threshold=threshold, annual_vol=low, years=years)
    high_p = first_passage_probability_upper(spot=spot, threshold=threshold, annual_vol=high, years=years)
    if not (low_p <= target_probability <= high_p):
        return None
    for _ in range(80):
        mid = (low + high) / 2.0
        mid_p = first_passage_probability_upper(spot=spot, threshold=threshold, annual_vol=mid, years=years)
        if mid_p < target_probability:
            low = mid
        else:
            high = mid
    return round((low + high) / 2.0, 8)


def _realized_vol_from_klines(klines: Iterable[Dict[str, Any]]) -> Optional[float]:
    closes: List[float] = []
    for row in klines:
        close = _safe_float(row.get("close"))
        if close is not None and close > 0:
            closes.append(close)
    if len(closes) < 3:
        return None
    returns: List[float] = []
    for previous, current in zip(closes, closes[1:]):
        if previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    if len(returns) < 2:
        return None
    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    return round(math.sqrt(variance) * math.sqrt(365.0 * 24.0 * 60.0), 8)


def _realized_vol_windows(
    *,
    asset: str,
    end_time: datetime,
    cache_dir: str | Path,
    allow_fetch: bool = False,
) -> Dict[str, Any]:
    pair = binance_pair_for_asset(asset)
    if pair is None:
        return {
            "realized_vol_7d": None,
            "realized_vol_30d": None,
            "realized_vol_90d": None,
            "realized_vol_gap_reason": "unsupported_binance_pair",
        }
    result: Dict[str, Any] = {}
    gaps: List[str] = []
    for days in (7, 30, 90):
        start_time = (end_time - timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        end_iso = end_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        klines = fetch_binance_klines(
            pair=pair,
            start_time=start_time,
            end_time=end_iso,
            interval="1m",
            cache_dir=cache_dir,
            fetcher=None if allow_fetch else (lambda _url: []),
            use_cache=True,
        )
        vol = _realized_vol_from_klines(klines.get("klines") or [])
        result[f"realized_vol_{days}d"] = vol
        if vol is None:
            gaps.append(f"{days}d:{klines.get('gap_reason') or 'insufficient_kline_closes'}")
    result["realized_vol_gap_reason"] = ";".join(gaps) if gaps else None
    return result


def build_crypto_touch_sensitivity_report(
    *,
    active_markets: Iterable[Dict[str, Any]],
    crypto_probability_report: Optional[Dict[str, Any]] = None,
    spot_prices: Optional[Dict[str, float]] = None,
    annual_vols: Optional[Dict[str, float]] = None,
    generated_at: Optional[str] = None,
    vol_multipliers: Iterable[float] = (0.8, 1.0, 1.2),
    cost_scenarios: Iterable[float] = (0.005, 0.01, 0.015),
    haircuts: Iterable[float] = (0.05, 0.08, 0.10),
    **kwargs: Any,
) -> Dict[str, Any]:
    active_rows = [dict(row) for row in active_markets if isinstance(row, dict)]
    base_vols = annual_vols or {"BTC": 0.55, "ETH": 0.70}
    base_report = crypto_probability_report if isinstance(crypto_probability_report, dict) and crypto_probability_report else None
    if base_report is None:
        base_report = build_crypto_probability_edge_report(
            active_markets=active_rows,
            spot_prices=spot_prices,
            annual_vols=base_vols,
            generated_at=generated_at,
            model_haircut=0.08,
            **kwargs,
        )
    generated_dt = _parse_utc(base_report.get("generated_at") or generated_at) or datetime.now(timezone.utc)
    cost = float(kwargs.get("cost", 0.01))
    realized_cache_dir = kwargs.get("high_since_start_cache_dir") or kwargs.get("cache_dir") or Path("evidence/polymarket_alpha/binance_klines")
    fetch_realized_vol = bool(kwargs.get("fetch_realized_vol", False))
    realized_vol_cache: Dict[str, Dict[str, Any]] = {}
    variant_rows: Dict[str, Dict[str, Any]] = {}
    base_key = "vol_1.0_haircut_0.08"
    sensitivity_source_rows: List[Dict[str, Any]] = []
    seen_sensitivity_rows: set[str] = set()
    for key in ("near_misses", "top_10_near_misses", "candidates", "near_miss_watch"):
        for row in base_report.get(key) or []:
            if not isinstance(row, dict):
                continue
            row_key = "|".join(str(row.get(field) or "") for field in ("market_slug", "token_id", "side"))
            if row_key in seen_sensitivity_rows:
                continue
            seen_sensitivity_rows.add(row_key)
            sensitivity_source_rows.append(row)
    for row in sensitivity_source_rows:
        if not (
            row.get("semantics_type") == "touch_barrier"
            and row.get("high_since_start_verified") is True
            and row.get("barrier_already_touched") is False
            and row.get("best_ask") is not None
        ):
            continue
        spot = _safe_float(row.get("spot"))
        threshold = _safe_float(row.get("threshold"))
        target_dt = _parse_utc(row.get("target_time"))
        asset = str(row.get("asset") or "")
        side = str(row.get("side") or "")
        q_effective = _safe_float(row.get("q_effective") or row.get("best_ask"))
        if spot is None or threshold is None or target_dt is None or q_effective is None:
            continue
        years = max(1 / 365, (target_dt - generated_dt).total_seconds() / (365.0 * 86400.0))
        row_key = "|".join(str(row.get(field) or "") for field in ("market_slug", "token_id", "side"))
        model_vol = float(base_vols.get(asset, row.get("annual_vol") or 0.60))
        realized_key = f"{asset}|{generated_dt.replace(microsecond=0).isoformat()}"
        realized = realized_vol_cache.setdefault(
            realized_key,
            _realized_vol_windows(
                asset=asset,
                end_time=generated_dt,
                cache_dir=realized_cache_dir,
                allow_fetch=fetch_realized_vol,
            ),
        )
        market_yes_probability = q_effective if side == "YES" else 1.0 - q_effective
        implied_vol = _implied_touch_vol(
            spot=spot,
            threshold=threshold,
            years=years,
            target_probability=market_yes_probability,
        )
        target = variant_rows.setdefault(
            row_key,
            {
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "asset": row.get("asset"),
                "side": row.get("side"),
                "threshold": row.get("threshold"),
                "target_time": row.get("target_time"),
                "market_creation_time": row.get("market_creation_time"),
                "max_high_since_start": row.get("max_high_since_start"),
                "probability_semantics": "touch_barrier",
                "realized_vol_7d": realized.get("realized_vol_7d"),
                "realized_vol_30d": realized.get("realized_vol_30d"),
                "realized_vol_90d": realized.get("realized_vol_90d"),
                "realized_vol_gap_reason": realized.get("realized_vol_gap_reason"),
                "model_vol": model_vol,
                "market_implied_touch_vol": implied_vol,
                "vol_edge": round(model_vol - implied_vol, 8) if implied_vol is not None else None,
                "q_effective": q_effective,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
                "variant_EV_safe": {},
                "EV_safe_by_vol_multiplier": {},
                "EV_safe_by_cost": {},
            },
        )
        for vol_multiplier in vol_multipliers:
            annual_vol = model_vol * float(vol_multiplier)
            p_yes = first_passage_probability_upper(
                spot=spot,
                threshold=threshold,
                annual_vol=annual_vol,
                years=years,
            )
            for haircut in haircuts:
                p_yes_lcb = max(0.0, p_yes - float(haircut))
                p_yes_ucb = min(1.0, p_yes + float(haircut))
                p_no_lcb = max(0.0, 1.0 - p_yes_ucb)
                p_trade_lcb = p_yes_lcb if side == "YES" else p_no_lcb
                ev_safe = round(p_trade_lcb - q_effective - cost, 8)
                variant_key = f"vol_{float(vol_multiplier):.1f}_haircut_{float(haircut):.2f}"
                target["variant_EV_safe"][variant_key] = ev_safe
                if variant_key == base_key:
                    target["base_EV_safe"] = ev_safe
                    target["p_trade_lcb"] = round(p_trade_lcb, 8)
            p_yes_base_haircut = max(0.0, p_yes - 0.08)
            p_yes_base_ucb = min(1.0, p_yes + 0.08)
            p_no_base_lcb = max(0.0, 1.0 - p_yes_base_ucb)
            p_trade_base_lcb = p_yes_base_haircut if side == "YES" else p_no_base_lcb
            target["EV_safe_by_vol_multiplier"][f"{float(vol_multiplier):.1f}x"] = round(p_trade_base_lcb - q_effective - cost, 8)
        p_trade_lcb = _safe_float(target.get("p_trade_lcb"))
        if p_trade_lcb is not None:
            for cost_scenario in cost_scenarios:
                target["EV_safe_by_cost"][f"{float(cost_scenario):.3f}"] = round(p_trade_lcb - q_effective - float(cost_scenario), 8)
    rows = list(variant_rows.values())
    for row in rows:
        values = [
            float(value)
            for value in (row.get("variant_EV_safe") or {}).values()
            if value is not None
        ]
        span = max(values) - min(values) if values else None
        row["model_confidence"] = round(max(0.0, 1.0 - min(1.0, span * 10.0)), 6) if span is not None else None
        scenario_values = list((row.get("EV_safe_by_vol_multiplier") or {}).values()) + list((row.get("EV_safe_by_cost") or {}).values())
        base_ev = _safe_float(row.get("base_EV_safe"))
        row["sensitivity_fragile"] = bool(
            base_ev is not None
            and base_ev > 0
            and any(_safe_float(value) is not None and float(value) <= 0 for value in scenario_values)
        )
    rows.sort(key=lambda row: float(row.get("base_EV_safe") if row.get("base_EV_safe") is not None else -1e9), reverse=True)
    for index, row in enumerate(rows, start=1):
        row["sensitivity_rank"] = index
    return {
        "schema_version": f"{SCHEMA_VERSION}.touch_sensitivity",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": base_report.get("generated_at") or generated_at,
        "verified_not_touched_market_side_count": len(rows),
        "base_model_ready_count": base_report.get("model_ready_count"),
        "base_near_miss_watch_count": base_report.get("near_miss_watch_count"),
        "vol_multipliers": list(vol_multipliers),
        "cost_scenarios": list(cost_scenarios),
        "haircuts": list(haircuts),
        "sensitivity_fragile_count": sum(1 for row in rows if row.get("sensitivity_fragile")),
        "rows": rows,
    }


def _count_by(rows: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    counts: Counter[str] = Counter(str(row.get(key) or "missing") for row in rows if isinstance(row, dict))
    return [{"bucket": bucket, "count": counts[bucket]} for bucket in sorted(counts)]


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
    "build_crypto_probability_edge_report",
    "build_crypto_touch_sensitivity_report",
    "fetch_binance_spot",
    "fetch_binance_high_since_start",
    "fetch_missing_orderbooks_for_markets",
    "first_passage_probability_upper",
    "_implied_touch_vol",
    "load_jsonl",
    "lognormal_probability_above",
    "parse_crypto_threshold_market",
    "write_json",
    "write_jsonl",
]
