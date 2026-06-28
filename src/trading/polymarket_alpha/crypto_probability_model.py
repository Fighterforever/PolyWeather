from __future__ import annotations

import json
import math
import re
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


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
    for match in re.finditer(r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?|[0-9]{3,6}(?:\.\d+)?)\s*(?:k\b)?", text):
        raw = match.group(1).replace(",", "")
        value = _safe_float(raw)
        if value is None:
            continue
        suffix = text[match.end(): match.end() + 2]
        if "k" in suffix and value < 1000:
            value *= 1000
        if (asset == "BTC" and value >= 1000) or (asset == "ETH" and value >= 100):
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


def _book_for_yes(market: Dict[str, Any]) -> Dict[str, Any]:
    token_id = (market.get("token_id_by_outcome") or {}).get("Yes")
    books = market.get("orderbooks") if isinstance(market.get("orderbooks"), dict) else {}
    return books.get(str(token_id)) if token_id and isinstance(books.get(str(token_id)), dict) else {}


def _candidate_from_probability(
    *,
    market: Dict[str, Any],
    parsed: Dict[str, Any],
    p_yes: float,
    p_lcb: float,
    p_ucb: float,
    min_edge: float,
    cost: float,
    min_depth: float,
    max_spread: float,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    book = _book_for_yes(market)
    best_ask = _safe_float(book.get("best_ask"))
    best_bid = _safe_float(book.get("best_bid"))
    spread = _safe_float(book.get("spread"))
    depth = _safe_float(book.get("ask_depth_usdc_3c"))
    if best_ask is None or best_bid is None:
        return None, "missing_executable_yes_bid_ask"
    if best_ask < 0.005:
        return None, "dust_price"
    if depth is None or depth < min_depth:
        return None, "depth_insufficient"
    if spread is None or spread > max_spread:
        return None, "spread_too_wide"
    yes_ev = p_lcb - best_ask - cost
    no_ask = 1.0 - best_bid
    no_ev = (1.0 - p_ucb) - no_ask - cost
    side = "YES" if yes_ev >= no_ev else "NO"
    ev_safe = yes_ev if side == "YES" else no_ev
    q_effective = best_ask if side == "YES" else no_ask
    if ev_safe < min_edge:
        return None, "ev_below_min"
    return {
        "market_slug": market.get("market_slug"),
        "token_id": (market.get("token_id_by_outcome") or {}).get("Yes"),
        "category": market.get("category"),
        "side": side,
        "asset": parsed.get("asset"),
        "direction": parsed.get("direction"),
        "threshold": parsed.get("threshold"),
        "target_time": parsed.get("target_time"),
        "p_model": round(p_yes, 8),
        "p_lcb": round(p_lcb, 8),
        "p_ucb": round(p_ucb, 8),
        "market_price": round((best_bid + best_ask) / 2.0, 8),
        "best_ask": q_effective,
        "q_effective": q_effective,
        "cost": cost,
        "EV_safe": round(ev_safe, 8),
        "model_source": "crypto_lognormal_threshold_model",
        "confidence": 0.35,
        "orderbook_snapshot_id": None,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }, None


def build_crypto_probability_edge_report(
    *,
    active_markets: Iterable[Dict[str, Any]],
    spot_prices: Optional[Dict[str, float]] = None,
    annual_vols: Optional[Dict[str, float]] = None,
    generated_at: Optional[str] = None,
    min_edge: float = 0.02,
    cost: float = 0.01,
    min_depth: float = 10.0,
    max_spread: float = 0.15,
) -> Dict[str, Any]:
    generated_dt = _parse_utc(generated_at) if generated_at else datetime.now(timezone.utc)
    generated_at = generated_dt.isoformat().replace("+00:00", "Z")
    spot_prices = spot_prices or {}
    annual_vols = annual_vols or {"BTC": 0.55, "ETH": 0.70}
    candidates: List[Dict[str, Any]] = []
    watch_rows: List[Dict[str, Any]] = []
    gaps: Counter[str] = Counter()
    parsed_count = 0
    model_ready_count = 0
    for market in active_markets:
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
        p_above = lognormal_probability_above(
            spot=spot,
            threshold=float(parsed["threshold"]),
            annual_vol=float(annual_vols.get(asset, 0.60)),
            years=years,
        )
        p_yes = p_above if parsed.get("direction") == "above" else 1.0 - p_above
        haircut = 0.08
        p_lcb = max(0.0, p_yes - haircut)
        p_ucb = min(1.0, p_yes + haircut)
        model_ready_count += 1
        candidate, gap = _candidate_from_probability(
            market=market,
            parsed=parsed,
            p_yes=p_yes,
            p_lcb=p_lcb,
            p_ucb=p_ucb,
            min_edge=float(min_edge),
            cost=float(cost),
            min_depth=float(min_depth),
            max_spread=float(max_spread),
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
            "gap": gap,
            "paper_only": True,
            "live_order_path": False,
        })
        if candidate:
            candidates.append(candidate)
        elif gap:
            gaps[gap] += 1
    candidates = sorted(candidates, key=lambda row: float(row.get("EV_safe") or -1e9), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "parsed_crypto_market_count": parsed_count,
        "model_ready_count": model_ready_count,
        "candidate_count": len(candidates),
        "gap_reasons": [{"reason": key, "count": value} for key, value in sorted(gaps.items())],
        "watch_rows": watch_rows,
        "candidates": candidates,
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


__all__ = [
    "SCHEMA_VERSION",
    "build_crypto_probability_edge_report",
    "fetch_binance_spot",
    "load_jsonl",
    "lognormal_probability_above",
    "parse_crypto_threshold_market",
    "write_json",
    "write_jsonl",
]
