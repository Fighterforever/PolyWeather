from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.crypto_probability_model import (
    fetch_binance_spot,
    fetch_missing_orderbooks_for_markets,
    lognormal_probability_above,
)
from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_crypto_terminal_probability.v1"
CRYPTO_CATEGORIES = {"crypto", "bitcoin", "ethereum", "crypto_prices"}
TOUCH_WORD_RE = re.compile(r"\b(reach|hit|touch|highest|high\s+candle)\b", re.I)


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


def _market_text(market: Dict[str, Any]) -> str:
    return " ".join(
        str(market.get(field) or "")
        for field in ("title", "question", "description", "rules", "resolution_rule", "market_slug")
    ).strip()


def _parse_asset(text: str) -> Optional[str]:
    lowered = text.lower()
    if re.search(r"\b(btc|bitcoin)\b", lowered):
        return "BTC"
    if re.search(r"\b(eth|ethereum)\b", lowered):
        return "ETH"
    return None


def _parse_threshold(text: str, asset: str) -> Optional[float]:
    candidates: List[float] = []
    lowered = text.lower()
    for match in re.finditer(r"(\$?)\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?|[0-9]{3,8}(?:\.\d+)?)\s*([km]\b)?", lowered):
        before = lowered[max(0, match.start() - 36) : match.start()]
        has_context = bool(re.search(r"(above|over|below|under|price|close|end|greater|less|at least)[^a-z0-9]{0,12}$", before))
        if not (match.group(1) or match.group(3) or has_context):
            continue
        value = _safe_float(match.group(2).replace(",", ""))
        if value is None:
            continue
        suffix = str(match.group(3) or "").lower()
        if suffix == "k" and value < 1000:
            value *= 1000
        elif suffix == "m" and value < 1_000_000:
            value *= 1_000_000
        if asset == "BTC" and value >= 10_000:
            candidates.append(value)
        elif asset == "ETH" and value >= 500:
            candidates.append(value)
    return max(candidates) if candidates else None


def classify_terminal_semantics(market: Dict[str, Any]) -> str:
    text = _market_text(market).lower()
    if TOUCH_WORD_RE.search(text):
        return "touch_barrier_excluded"
    if re.search(r"\b(close|settle|closing)\b.*\b(above|over|greater than)\b", text):
        return "close_above"
    if re.search(r"\b(close|settle|closing)\b.*\b(below|under|less than)\b", text):
        return "close_below"
    if re.search(r"\b(end of|end-of|eod|end the day|by end of)\b.*\b(above|over|greater than)\b", text):
        return "end_of_day_above"
    if re.search(r"\b(end of|end-of|eod|end the day|by end of)\b.*\b(below|under|less than)\b", text):
        return "end_of_day_below"
    if re.search(r"\b(above|over|greater than)\b.*\b(on|at)\b", text) or re.search(r"\b(on|at)\b.*\b(above|over|greater than)\b", text):
        return "terminal_above"
    if re.search(r"\b(below|under|less than)\b.*\b(on|at)\b", text) or re.search(r"\b(on|at)\b.*\b(below|under|less than)\b", text):
        return "terminal_below"
    return "ambiguous"


def parse_crypto_terminal_market(market: Dict[str, Any]) -> Dict[str, Any]:
    text = _market_text(market)
    asset = _parse_asset(text)
    if asset is None:
        return {"parsed": False, "gap_reason": "unsupported_asset"}
    semantics = classify_terminal_semantics(market)
    if semantics == "touch_barrier_excluded":
        return {"parsed": False, "asset": asset, "semantics_type": semantics, "gap_reason": "touch_barrier_excluded"}
    if semantics == "ambiguous":
        return {"parsed": False, "asset": asset, "semantics_type": semantics, "gap_reason": "ambiguous_terminal_semantics"}
    threshold = _parse_threshold(text, asset)
    if threshold is None:
        return {"parsed": False, "asset": asset, "semantics_type": semantics, "gap_reason": "missing_threshold"}
    target_time = _parse_utc(market.get("end_time") or market.get("endDate") or market.get("resolution_time"))
    if target_time is None:
        return {"parsed": False, "asset": asset, "semantics_type": semantics, "threshold": threshold, "gap_reason": "missing_target_time"}
    return {
        "parsed": True,
        "asset": asset,
        "threshold": float(threshold),
        "semantics_type": semantics,
        "target_time": target_time.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
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


def _quote_for_side(market: Dict[str, Any], side: str) -> tuple[Optional[Dict[str, Any]], str]:
    token_id = _token_for_outcome(market, side)
    if not token_id:
        return None, f"missing_{side.lower()}_token_id"
    book = _book_for_outcome(market, side)
    if not book:
        return None, f"{side.lower()}_orderbook_missing"
    best_ask = _safe_float(book.get("best_ask"))
    best_bid = _safe_float(book.get("best_bid"))
    spread = _safe_float(book.get("spread"))
    depth = _safe_float(book.get("ask_depth_usdc_3c") or book.get("depth"))
    if best_ask is None:
        return None, f"{side.lower()}_no_ask_depth"
    return {
        "token_id": token_id,
        "best_ask": best_ask,
        "best_bid": best_bid,
        "spread": spread,
        "depth": depth,
        "orderbook_snapshot": book,
    }, ""


def _terminal_yes_probability(parsed: Dict[str, Any], *, spot: float, annual_vol: float, years: float) -> float:
    p_above = lognormal_probability_above(
        spot=float(spot),
        threshold=float(parsed["threshold"]),
        annual_vol=float(annual_vol),
        years=max(float(years), 1e-8),
    )
    if str(parsed.get("semantics_type")) in {"terminal_below", "close_below", "end_of_day_below"}:
        return 1.0 - p_above
    return p_above


def _candidate_rows_for_market(
    market: Dict[str, Any],
    parsed: Dict[str, Any],
    *,
    spot: float,
    annual_vol: float,
    generated_at: str,
    min_edge: float,
    cost: float,
    min_depth: float,
    max_spread: float,
    model_haircut: float,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    target = _parse_utc(parsed.get("target_time"))
    generated_dt = _parse_utc(generated_at) or datetime.now(timezone.utc)
    years = max(1 / 365, ((target or generated_dt) - generated_dt).total_seconds() / (365.0 * 86400.0))
    p_yes = _terminal_yes_probability(parsed, spot=spot, annual_vol=annual_vol, years=years)
    p_yes_lcb = max(0.0, p_yes - float(model_haircut))
    p_yes_ucb = min(1.0, p_yes + float(model_haircut))
    p_no = 1.0 - p_yes
    p_no_lcb = max(0.0, 1.0 - p_yes_ucb)
    p_no_ucb = min(1.0, 1.0 - p_yes_lcb)
    candidates: List[Dict[str, Any]] = []
    watch: List[Dict[str, Any]] = []
    executable = False
    for side in ("YES", "NO"):
        quote, gap = _quote_for_side(market, side)
        side_lcb = p_yes_lcb if side == "YES" else p_no_lcb
        side_ucb = p_yes_ucb if side == "YES" else p_no_ucb
        side_model = p_yes if side == "YES" else p_no
        row = {
            "market_slug": market.get("market_slug"),
            "token_id": _token_for_outcome(market, side),
            "category": market.get("category"),
            "side": side,
            "asset": parsed.get("asset"),
            "threshold": parsed.get("threshold"),
            "target_time": parsed.get("target_time"),
            "semantics_type": parsed.get("semantics_type"),
            "spot": spot,
            "annual_vol": annual_vol,
            "generated_at": generated_at,
            "p_yes_model": round(p_yes, 8),
            "p_yes_lcb": round(p_yes_lcb, 8),
            "p_yes_ucb": round(p_yes_ucb, 8),
            "p_no_model": round(p_no, 8),
            "p_no_lcb": round(p_no_lcb, 8),
            "p_no_ucb": round(p_no_ucb, 8),
            "p_trade_model": round(side_model, 8),
            "p_trade_lcb": round(side_lcb, 8),
            "p_trade_ucb": round(side_ucb, 8),
            "best_ask": None,
            "spread": None,
            "depth": None,
            "q_effective": None,
            "EV_safe": None,
            "model_source": "crypto_terminal_lognormal_model",
            "blocker": gap or None,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        if quote:
            executable = True
            best_ask = quote["best_ask"]
            ev_safe = side_lcb - best_ask - float(cost)
            row.update(
                {
                    "token_id": quote.get("token_id"),
                    "best_ask": best_ask,
                    "spread": quote.get("spread"),
                    "depth": quote.get("depth"),
                    "q_effective": best_ask,
                    "EV_safe": round(ev_safe, 8),
                    "orderbook_snapshot": quote.get("orderbook_snapshot"),
                }
            )
            if best_ask < 0.005:
                row["blocker"] = "dust_price"
            elif quote.get("depth") is None or float(quote.get("depth") or 0.0) < float(min_depth):
                row["blocker"] = "no_ask_depth"
            elif quote.get("spread") is None or float(quote.get("spread") or 999.0) > float(max_spread):
                row["blocker"] = "spread_too_wide"
            elif ev_safe < float(min_edge):
                row["blocker"] = "ev_below_min"
            else:
                row["blocker"] = None
        watch.append(row)
        if row.get("blocker") is None and row.get("EV_safe") is not None:
            candidates.append(row)
    return candidates, watch, executable


def build_crypto_terminal_edge_report(
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
) -> Dict[str, Any]:
    generated_dt = _parse_utc(generated_at) if generated_at else datetime.now(timezone.utc)
    generated_at = generated_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    spot_prices = spot_prices or {}
    annual_vols = annual_vols or {"BTC": 0.55, "ETH": 0.70}
    active_rows = [dict(row) for row in active_markets if isinstance(row, dict) and row.get("active")]
    parsed_candidates = [row for row in active_rows if str(row.get("category") or "") in CRYPTO_CATEGORIES and parse_crypto_terminal_market(row).get("parsed")]
    orderbook_summary = {"attempted": 0, "fetched": {}, "errors": {}}
    if fetch_orderbooks:
        orderbook_summary = fetch_missing_orderbooks_for_markets(
            parsed_candidates,
            max_tokens=int(max_orderbook_tokens),
            fetcher=orderbook_fetcher,
        )

    candidates: List[Dict[str, Any]] = []
    watch_rows: List[Dict[str, Any]] = []
    blockers: Counter[str] = Counter()
    parsed_count = 0
    model_ready_count = 0
    executable_count = 0
    terminal_rows = 0

    for market in active_rows:
        if str(market.get("category") or "") not in CRYPTO_CATEGORIES:
            continue
        parsed = parse_crypto_terminal_market(market)
        if not parsed.get("parsed"):
            blockers[str(parsed.get("gap_reason") or "unparseable_terminal_market")] += 1
            continue
        parsed_count += 1
        terminal_rows += 1
        asset = str(parsed.get("asset") or "")
        spot = _safe_float(spot_prices.get(asset))
        if spot is None:
            blockers[f"missing_{asset.lower()}_spot_price"] += 1
            continue
        model_ready_count += 1
        market_candidates, market_watch, executable = _candidate_rows_for_market(
            market,
            parsed,
            spot=spot,
            annual_vol=float(annual_vols.get(asset, 0.60)),
            generated_at=generated_at,
            min_edge=float(min_edge),
            cost=float(cost),
            min_depth=float(min_depth),
            max_spread=float(max_spread),
            model_haircut=float(model_haircut),
        )
        if executable:
            executable_count += 1
        candidates.extend(market_candidates)
        watch_rows.extend(market_watch)
        if not market_candidates:
            best = max(
                market_watch,
                key=lambda row: float(row.get("EV_safe") if row.get("EV_safe") is not None else -1e9),
                default={},
            )
            blockers[str(best.get("blocker") or "no_terminal_candidate")] += 1

    candidates.sort(key=lambda row: float(row.get("EV_safe") or -1e9), reverse=True)
    near_misses = sorted(
        [
            row
            for row in watch_rows
            if row.get("EV_safe") is not None and row.get("blocker") == "ev_below_min"
        ],
        key=lambda row: float(row.get("EV_safe") or -1e9),
        reverse=True,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "parsed_terminal_market_count": parsed_count,
        "terminal_market_count": terminal_rows,
        "model_ready_count": model_ready_count,
        "executable_price_available_count": executable_count,
        "candidate_count": len(candidates),
        "paper_fill_count": 0,
        "near_miss_count": len(near_misses),
        "blocker_counts": [{"reason": key, "count": value} for key, value in sorted(blockers.items())],
        "orderbook_fetch_attempt_count": orderbook_summary.get("attempted", 0),
        "orderbook_fetch_success_count": len(orderbook_summary.get("fetched", {})),
        "orderbook_fetch_error_count": len(orderbook_summary.get("errors", {})),
        "top_candidates": candidates[:10],
        "top_near_misses": near_misses[:10],
        "candidates": candidates,
        "watch_rows": watch_rows,
    }


__all__ = [
    "SCHEMA_VERSION",
    "build_crypto_terminal_edge_report",
    "classify_terminal_semantics",
    "fetch_binance_spot",
    "parse_crypto_terminal_market",
    "write_json",
    "write_jsonl",
]
