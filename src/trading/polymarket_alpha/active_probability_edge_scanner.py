from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import price_bucket, spread_bucket, time_to_close_bucket, write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_active_probability_edge.v2"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _model_oos_passed(model_report: Dict[str, Any]) -> bool:
    overall = model_report.get("overall") if isinstance(model_report.get("overall"), dict) else {}
    return (
        int(model_report.get("validate_row_count") or 0) > 0
        and (overall.get("brier_improvement") or 0) > 0
        and (overall.get("log_loss_improvement") or 0) > 0
    )


def _focused_categories(focus_report: Dict[str, Any]) -> set[str]:
    rows = focus_report.get("top_focus_categories") if isinstance(focus_report.get("top_focus_categories"), list) else []
    return {str(row.get("category")) for row in rows if isinstance(row, dict) and row.get("recommendation") == "focus_forward_paper"}


def _predict(row: Dict[str, Any], model_report: Dict[str, Any], price: float) -> Dict[str, Any]:
    model = model_report.get("model") if isinstance(model_report.get("model"), dict) else {}
    bins = model.get("bins") if isinstance(model.get("bins"), dict) else {}
    price_key = price_bucket(price)
    time_key = time_to_close_bucket(row.get("time_to_close_seconds"))
    spread_key = spread_bucket(row.get("spread"))
    category = str(row.get("category") or "uncategorized")
    keys = [
        f"category|{category}|{price_key}",
        f"global|{price_key}|{time_key}|{spread_key}",
        f"global|{price_key}|{time_key}|*",
        f"global|{price_key}|*|*",
        "global|*|*|*",
    ]
    for key in keys:
        value = bins.get(key)
        if isinstance(value, dict):
            return {
                "model_key": key,
                "p": float(value.get("p") or price),
                "p_lcb": float(value.get("p_lcb") if value.get("p_lcb") is not None else value.get("p") or price),
                "p_ucb": float(value.get("p_ucb") if value.get("p_ucb") is not None else value.get("p") or price),
                "sample_count": int(value.get("sample_count") or 0),
            }
    return {"model_key": "market_price_baseline", "p": price, "p_lcb": price, "p_ucb": price, "sample_count": 0}


def _books(row: Dict[str, Any]) -> List[tuple[str, Dict[str, Any], str]]:
    books = row.get("orderbooks") if isinstance(row.get("orderbooks"), dict) else {}
    token_to_outcome = {str(token): outcome for outcome, token in (row.get("token_id_by_outcome") or {}).items()}
    return [(token_id, book, token_to_outcome.get(str(token_id), "")) for token_id, book in books.items() if isinstance(book, dict)]


def _source_counts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts = Counter(str(row.get("model_source") or "missing") for row in rows)
    return [{"model_source": key, "count": value} for key, value in sorted(counts.items())]


def scan_active_probability_edges(
    *,
    active_markets: Iterable[Dict[str, Any]],
    model_report: Dict[str, Any],
    focus_report: Optional[Dict[str, Any]] = None,
    crypto_report: Optional[Dict[str, Any]] = None,
    min_edge: float = 0.02,
    min_depth: float = 10.0,
    max_spread: float = 0.15,
    cost: float = 0.01,
) -> Dict[str, Any]:
    focus = _focused_categories(focus_report or {})
    active = [row for row in active_markets if isinstance(row, dict) and row.get("active")]
    candidates: List[Dict[str, Any]] = []
    watch_rows: List[Dict[str, Any]] = []
    blockers: Counter[str] = Counter()

    crypto_blockers = [
        {"reason": row.get("reason"), "count": row.get("count")}
        for row in ((crypto_report or {}).get("blocker_counts") or (crypto_report or {}).get("gap_reasons") or [])
        if isinstance(row, dict)
    ]
    crypto_candidates = [
        {**row, "priority_rank": 1}
        for row in ((crypto_report or {}).get("candidates") or [])
        if isinstance(row, dict) and row.get("live_order_path") is False
    ]
    candidates.extend(crypto_candidates)
    model_ready = int((crypto_report or {}).get("model_ready_count") or len(crypto_candidates))
    lane_reports: Dict[str, Dict[str, Any]] = {
        "crypto_model_lane": {
            "scanned_count": int((crypto_report or {}).get("parsed_crypto_market_count") or 0),
            "model_ready_count": int((crypto_report or {}).get("model_ready_count") or 0),
            "executable_price_available_count": int((crypto_report or {}).get("executable_price_available_count") or 0),
            "candidate_count": len(crypto_candidates),
            "paper_fill_count": len(crypto_candidates),
            "blocker_counts": crypto_blockers,
            "top_watch_rows": ((crypto_report or {}).get("top_10_near_misses") or [])[:10],
            "top_candidates": crypto_candidates[:10],
            "live_order_path": False,
        },
        "global_oos_model_lane": {
            "scanned_count": len(active),
            "model_ready_count": 0,
            "executable_price_available_count": 0,
            "candidate_count": 0,
            "paper_fill_count": 0,
            "blocker_counts": [],
            "top_watch_rows": [],
            "top_candidates": [],
            "live_order_path": False,
        },
        "maker_focus_lane": {
            "scanned_count": 0,
            "model_ready_count": 0,
            "executable_price_available_count": 0,
            "candidate_count": 0,
            "paper_fill_count": 0,
            "blocker_counts": [{"reason": "maker_focus_report_not_input_to_probability_scanner", "count": 1}],
            "top_watch_rows": [],
            "top_candidates": [],
            "live_order_path": False,
        },
        "structural_lane": {
            "scanned_count": 0,
            "model_ready_count": 0,
            "executable_price_available_count": 0,
            "candidate_count": 0,
            "paper_fill_count": 0,
            "blocker_counts": [{"reason": "structural_report_not_input_to_probability_scanner", "count": 1}],
            "top_watch_rows": [],
            "top_candidates": [],
            "live_order_path": False,
        },
    }

    if not _model_oos_passed(model_report):
        blockers["global_oos_model_not_passed"] += len(active)
        lane_reports["global_oos_model_lane"]["blocker_counts"] = [{"reason": "global_oos_model_not_passed", "count": len(active)}]
    else:
        model_ready += len(active)
        lane_reports["global_oos_model_lane"]["model_ready_count"] = len(active)
        for market in active:
            category = str(market.get("category") or "uncategorized")
            if focus and category not in focus:
                blockers["category_not_focus_forward_paper"] += 1
                continue
            market_books = _books(market)
            if not market_books:
                blockers["missing_orderbook"] += 1
                continue
            for token_id, book, outcome_label in market_books:
                best_ask = _safe_float(book.get("best_ask"))
                best_bid = _safe_float(book.get("best_bid"))
                spread = _safe_float(book.get("spread"))
                depth = _safe_float(book.get("ask_depth_usdc_3c"))
                if best_ask is None or best_bid is None:
                    blockers["missing_bid_ask"] += 1
                    continue
                if best_ask < 0.005:
                    blockers["dust"] += 1
                    continue
                if depth is None or depth < float(min_depth):
                    blockers["depth_insufficient"] += 1
                    continue
                if spread is None or spread > float(max_spread):
                    blockers["spread_too_wide"] += 1
                    continue
                price = round((best_bid + best_ask) / 2.0, 8)
                if price < 0.05 or price > 0.95:
                    blockers["extreme_price_not_candidate"] += 1
                    continue
                pred = _predict({**market, "token_id": token_id, "spread": spread}, model_report, price)
                p_lcb = float(pred["p_lcb"])
                p_ucb = float(pred["p_ucb"])
                p_yes_model = float(pred["p"])
                p_yes_lcb = p_lcb
                p_yes_ucb = p_ucb
                p_no_model = 1.0 - p_yes_model
                p_no_lcb = max(0.0, 1.0 - p_yes_ucb)
                p_no_ucb = min(1.0, 1.0 - p_yes_lcb)
                yes_ev = p_yes_lcb - best_ask - float(cost)
                no_ask = 1.0 - best_bid
                no_ev = p_no_lcb - no_ask - float(cost)
                side = "YES" if yes_ev >= no_ev else "NO"
                ev_safe = yes_ev if side == "YES" else no_ev
                p_trade_model = p_yes_model if side == "YES" else p_no_model
                p_trade_lcb = p_yes_lcb if side == "YES" else p_no_lcb
                p_trade_ucb = p_yes_ucb if side == "YES" else p_no_ucb
                watch = {
                    "market_slug": market.get("market_slug"),
                    "token_id": token_id,
                    "category": category,
                    "side": side,
                    "outcome_label": outcome_label,
                    "p_model": round(float(pred["p"]), 8),
                    "p_lcb": round(p_lcb, 8),
                    "p_ucb": round(p_ucb, 8),
                    "p_yes_model": round(p_yes_model, 8),
                    "p_yes_lcb": round(p_yes_lcb, 8),
                    "p_yes_ucb": round(p_yes_ucb, 8),
                    "p_no_model": round(p_no_model, 8),
                    "p_no_lcb": round(p_no_lcb, 8),
                    "p_no_ucb": round(p_no_ucb, 8),
                    "p_trade_model": round(p_trade_model, 8),
                    "p_trade_lcb": round(p_trade_lcb, 8),
                    "p_trade_ucb": round(p_trade_ucb, 8),
                    "market_price": price,
                    "best_ask": best_ask if side == "YES" else no_ask,
                    "q_effective": best_ask if side == "YES" else no_ask,
                    "cost": float(cost),
                    "EV_safe": round(ev_safe, 8),
                    "model_source": "global_oos_probability_model",
                    "confidence": min(1.0, math.sqrt(float(pred.get("sample_count") or 0) / 100.0)),
                    "orderbook_snapshot_id": None,
                    "price_bucket": price_bucket(price),
                    "spread_bucket": spread_bucket(spread),
                    "time_to_close_bucket": time_to_close_bucket(None),
                    "neutral_political_stats_only": category in {"politics", "elections", "world_elections", "trump"},
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
                watch_rows.append(watch)
                if ev_safe >= float(min_edge):
                    candidates.append({**watch, "priority_rank": 2})
                    lane_reports["global_oos_model_lane"]["top_candidates"].append(watch)
                else:
                    blockers["ev_below_min"] += 1
                lane_reports["global_oos_model_lane"]["top_watch_rows"].append(watch)
        lane_reports["global_oos_model_lane"]["candidate_count"] = len(lane_reports["global_oos_model_lane"]["top_candidates"])
        lane_reports["global_oos_model_lane"]["paper_fill_count"] = lane_reports["global_oos_model_lane"]["candidate_count"]
        lane_reports["global_oos_model_lane"]["executable_price_available_count"] = len(watch_rows)
        lane_reports["global_oos_model_lane"]["blocker_counts"] = [
            {"reason": key, "count": count}
            for key, count in sorted(blockers.items())
            if key != "global_oos_model_not_passed"
        ]
        lane_reports["global_oos_model_lane"]["top_watch_rows"] = lane_reports["global_oos_model_lane"]["top_watch_rows"][:10]
        lane_reports["global_oos_model_lane"]["top_candidates"] = sorted(
            lane_reports["global_oos_model_lane"]["top_candidates"],
            key=lambda row: float(row.get("EV_safe") or -1e9),
            reverse=True,
        )[:10]
    candidates = sorted(candidates, key=lambda row: (int(row.get("priority_rank") or 99), -float(row.get("EV_safe") or -1e9)))
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "scanned_active_market_count": len(active),
        "model_ready_count": model_ready,
        "executable_candidate_count": len(candidates),
        "candidate_count": len(candidates),
        "paper_fill_count": len(candidates),
        "by_model_source": _source_counts(candidates),
        "lane_reports": lane_reports,
        "focus_categories": sorted(focus),
        "watch_row_count": len(watch_rows),
        "blocker_counts": [{"reason": key, "count": count} for key, count in sorted(blockers.items())],
        "top_candidates": candidates[:25],
        "candidates": candidates,
        "watch_rows": watch_rows,
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


__all__ = ["SCHEMA_VERSION", "load_json", "load_jsonl", "scan_active_probability_edges", "write_json", "write_jsonl"]
