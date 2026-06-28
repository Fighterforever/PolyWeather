from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = "polyweather_polymarket_alpha_opportunity_density_scoreboard.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _median(values: Iterable[Any]) -> Optional[float]:
    parsed = [_safe_float(value) for value in values]
    parsed = [value for value in parsed if value is not None]
    return round(float(median(parsed)), 6) if parsed else None


def _depth(row: Dict[str, Any]) -> Optional[float]:
    depths: List[float] = []
    books = row.get("orderbooks") if isinstance(row.get("orderbooks"), dict) else {}
    for book in books.values():
        if not isinstance(book, dict):
            continue
        value = _safe_float(book.get("ask_depth_usdc_3c"))
        if value is not None:
            depths.append(value)
    return max(depths) if depths else _safe_float(row.get("liquidity"))


def build_opportunity_density_scoreboard(
    *,
    market_discovery_report: Dict[str, Any],
    active_markets: Iterable[Dict[str, Any]],
    family_catalog: Dict[str, Any],
    payoff_arbitrage_report: Dict[str, Any],
) -> Dict[str, Any]:
    active_rows = [row for row in active_markets if isinstance(row, dict)]
    families = [row for row in family_catalog.get("families") or [] if isinstance(row, dict)]
    candidates = [row for row in payoff_arbitrage_report.get("candidates") or [] if isinstance(row, dict)]
    near_misses = [row for row in payoff_arbitrage_report.get("near_misses") or [] if isinstance(row, dict)]
    by_category: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"markets": [], "families": [], "candidates": [], "near_misses": []})
    for row in active_rows:
        by_category[str(row.get("category") or "uncategorized")]["markets"].append(row)
    for row in families:
        by_category[str(row.get("category") or "uncategorized")]["families"].append(row)
    for row in candidates:
        by_category[str(row.get("category") or "uncategorized")]["candidates"].append(row)
    for row in near_misses:
        by_category[str(row.get("category") or "uncategorized")]["near_misses"].append(row)

    rows: List[Dict[str, Any]] = []
    for category, payload in sorted(by_category.items()):
        markets = payload["markets"]
        category_families = payload["families"]
        category_candidates = payload["candidates"]
        category_near_misses = payload["near_misses"]
        active_market_count = len(markets)
        family_count = len(category_families)
        median_family_size = _median(row.get("market_count") for row in category_families)
        median_spread = _median(row.get("spread") for row in markets)
        median_depth = _median(_depth(row) for row in markets)
        trade_count = sum(_safe_int((row.get("trade_tape") or {}).get("trade_count")) for row in markets)
        trade_tape_density = round(trade_count / max(1, active_market_count), 6)
        structural_candidate_count = len(category_candidates)
        near_miss_count = len(category_near_misses)
        structural_candidate_rate = structural_candidate_count / max(1, family_count)
        near_miss_rate = min(1.0, near_miss_count / max(1, family_count))
        spread_score = 0.0 if median_spread is None else max(0.0, min(1.0, float(median_spread) / 0.15))
        depth_score = 0.0 if median_depth is None else max(0.0, min(1.0, float(median_depth) / 250.0))
        spread_depth_score = round(0.5 * spread_score + 0.5 * depth_score, 6)
        trade_tape_density_score = max(0.0, min(1.0, trade_tape_density / 20.0))
        resolution_speed_score = 0.3 if any(row.get("end_time") for row in markets) else 0.0
        complexity_score = 0.2 + min(0.8, (median_family_size or 1.0) / 20.0)
        complexity_penalty = round(complexity_score * 0.35, 6)
        opportunity_density_score = round(
            structural_candidate_rate
            + near_miss_rate
            + spread_depth_score
            + trade_tape_density_score
            + resolution_speed_score
            - complexity_penalty,
            6,
        )
        rows.append(
            {
                "category": category,
                "active_market_count": active_market_count,
                "family_count": family_count,
                "median_family_size": median_family_size,
                "median_spread": median_spread,
                "median_depth": median_depth,
                "trade_tape_density": trade_tape_density,
                "structural_candidate_count": structural_candidate_count,
                "near_miss_count": near_miss_count,
                "maker_shadow_quote_opportunity_count": 0,
                "resolution_speed_score": resolution_speed_score,
                "complexity_score": round(complexity_score, 6),
                "opportunity_density_score": opportunity_density_score,
                "score_components": {
                    "structural_candidate_rate": round(structural_candidate_rate, 6),
                    "near_miss_rate": round(near_miss_rate, 6),
                    "spread_depth_score": spread_depth_score,
                    "trade_tape_density_score": round(trade_tape_density_score, 6),
                    "resolution_speed_score": resolution_speed_score,
                    "complexity_penalty": complexity_penalty,
                },
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    ranked = sorted(rows, key=lambda row: float(row.get("opportunity_density_score") or -1e9), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "active_market_count": _safe_int(market_discovery_report.get("active_market_count")) or len(active_rows),
        "category_count": len(rows),
        "score_formula": "structural_candidate_rate + near_miss_rate + spread_depth_score + trade_tape_density_score + resolution_speed_score - complexity_penalty",
        "categories": ranked,
        "top_categories": ranked[:5],
        "top_focus_categories": [row.get("category") for row in ranked[:5]],
    }


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


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


__all__ = ["SCHEMA_VERSION", "build_opportunity_density_scoreboard", "load_json", "load_jsonl", "write_json"]
