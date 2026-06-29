from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import price_bucket, spread_bucket, time_to_close_bucket, write_json, write_jsonl
from src.trading.polymarket_alpha.probability_edge_journal import build_formal_fill_followup_orderbook_snapshots


SCHEMA_VERSION = "polyweather_polymarket_alpha_active_probability_edge.v2"


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


def _horizon_label(seconds: int) -> str:
    return {
        60: "60s",
        300: "5m",
        900: "15m",
        3600: "1h",
        21600: "6h",
        86400: "24h",
    }.get(int(seconds), f"{int(seconds)}s")


def _horizon_tolerance_seconds(seconds: int) -> int:
    return {
        60: 30,
        300: 90,
        900: 180,
        3600: 600,
        21600: 1800,
        86400: 7200,
    }.get(int(seconds), max(30, int(seconds) // 10))


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


def _candidate_identity(row: Dict[str, Any]) -> str:
    return "|".join(str(row.get(field) or "") for field in ("market_slug", "token_id", "side"))


def _surface_index(surface_report: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(surface_report, dict):
        return {}
    rows: List[Dict[str, Any]] = []
    for key in ("rows", "top_relative_value_rows", "shadow_relative_value_rows"):
        for row in surface_report.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    return {
        _candidate_identity(row): row
        for row in rows
        if _candidate_identity(row) != "||"
    }


def _sensitivity_index(sensitivity_report: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(sensitivity_report, dict):
        return {}
    return {
        _candidate_identity(row): row
        for row in sensitivity_report.get("rows") or []
        if isinstance(row, dict) and _candidate_identity(row) != "||"
    }


def _gate_crypto_candidates(
    candidates: Iterable[Dict[str, Any]],
    *,
    surface_report: Optional[Dict[str, Any]] = None,
    sensitivity_report: Optional[Dict[str, Any]] = None,
    crypto_touch_formal_fill_mode: str = "disabled",
    min_edge: float = 0.01,
    stricter_edge: float = 0.02,
) -> Dict[str, Any]:
    mode = str(crypto_touch_formal_fill_mode or "disabled").strip().lower()
    if mode not in {"disabled", "strict", "legacy"}:
        mode = "disabled"
    surface_by_key = _surface_index(surface_report)
    sensitivity_by_key = _sensitivity_index(sensitivity_report)
    enforce_surface = bool(surface_by_key)
    enforce_sensitivity = bool(sensitivity_by_key)
    raw_candidates = [row for row in candidates if isinstance(row, dict)]
    accepted: List[Dict[str, Any]] = []
    watch_rows: List[Dict[str, Any]] = []
    downgraded_surface = 0
    downgraded_sensitivity = 0
    for candidate in raw_candidates:
        row = dict(candidate)
        key = _candidate_identity(row)
        ev_safe = _safe_float(row.get("EV_safe")) or 0.0
        surface_row = surface_by_key.get(key)
        group_size = int((surface_row or {}).get("group_member_count") or 0)
        surface_valid = (surface_row or {}).get("surface_valid")
        surface_too_sparse = enforce_surface and (surface_row is None or group_size < 4 or surface_valid is False)
        if not enforce_surface:
            surface_supported = True
        elif mode == "legacy" and surface_too_sparse:
            surface_supported = True
        else:
            surface_supported = bool((surface_row or {}).get("surface_supports_model_direction")) and surface_valid is not False and group_size >= 4
        sensitivity_row = sensitivity_by_key.get(key)
        sensitivity_fragile = bool((sensitivity_row or {}).get("sensitivity_fragile")) if enforce_sensitivity else False
        vol_supports_trade = (sensitivity_row or {}).get("vol_supports_trade")
        vol_supported = True if vol_supports_trade is None else bool(vol_supports_trade)
        sensitivity_allowed = vol_supported and ((not sensitivity_fragile) or ev_safe >= float(stricter_edge))
        row.update(
            {
                "surface_support": bool(surface_supported),
                "surface_group_too_sparse": bool(surface_too_sparse),
                "surface_group_member_count": group_size if enforce_surface else None,
                "surface_valid": surface_valid,
                "surface_invalid_reason": (surface_row or {}).get("surface_invalid_reason"),
                "surface_residual_z_score": (surface_row or {}).get("residual_z_score"),
                "surface_residual_supports_model_direction": (surface_row or {}).get("surface_supports_model_direction"),
                "sensitivity_fragile": bool(sensitivity_fragile),
                "sensitivity_base_EV_safe": (sensitivity_row or {}).get("base_EV_safe"),
                "sensitivity_model_confidence": (sensitivity_row or {}).get("model_confidence"),
                "vol_supports_trade": vol_supports_trade,
                "vol_support_reason": (sensitivity_row or {}).get("vol_support_reason"),
                "stricter_edge": float(stricter_edge),
            }
        )
        reasons: List[str] = []
        if ev_safe < float(min_edge):
            reasons.append("ev_below_min")
        if mode == "disabled":
            reasons.append("formal_fill_paused_pending_surface_recalibration")
        if not surface_supported:
            downgraded_surface += 1
            reasons.append("surface_unsupported")
        if not sensitivity_allowed:
            downgraded_sensitivity += 1
            if not vol_supported:
                reasons.append("vol_support_rejected_below_stricter_edge")
            else:
                reasons.append("sensitivity_fragile_below_stricter_edge")
        if reasons:
            watch_rows.append(
                {
                    **row,
                    "candidate_downgraded_to_watch": True,
                    "downgrade_reasons": reasons,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
            continue
        accepted.append(row)
    return {
        "accepted_candidates": accepted,
        "watch_rows": watch_rows,
        "old_formal_fill_count": len(raw_candidates),
        "new_formal_fill_count": len(accepted),
        "downgraded_due_surface_count": downgraded_surface,
        "downgraded_due_sensitivity_count": downgraded_sensitivity,
        "watch_count": len(watch_rows),
        "surface_gate_enforced": enforce_surface,
        "sensitivity_gate_enforced": enforce_sensitivity,
        "formal_fill_mode": mode,
        "new_formal_fill_generation_enabled": mode in {"strict", "legacy"},
        "stricter_edge": float(stricter_edge),
    }


def _fill_identity(row: Dict[str, Any]) -> str:
    return str(row.get("fill_id") or f"{row.get('market_slug')}|{row.get('token_id')}|{row.get('side')}")


def _snapshots_for_fill(fill: Dict[str, Any], snapshots: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fill_id = str(fill.get("fill_id") or "")
    token_id = str(fill.get("token_id") or "")
    matched: List[Dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        if fill_id and str(snapshot.get("fill_id") or "") == fill_id:
            matched.append(snapshot)
        elif token_id and str(snapshot.get("token_id") or "") == token_id:
            matched.append(snapshot)
    matched.sort(key=lambda row: str(row.get("timestamp") or row.get("recorded_at") or ""))
    return matched


def build_formal_fill_followup_snapshot_coverage_report(
    *,
    fills: Iterable[Dict[str, Any]],
    followup_snapshots: Iterable[Dict[str, Any]],
    snapshot_path: str | Path = "evidence/polymarket_alpha/probability_edge_paper/fill_followup_orderbook_snapshots.jsonl",
    watcher_status: Optional[Dict[str, Any]] = None,
    horizons: tuple[int, ...] = (60, 300, 900, 3600, 21600, 86400),
) -> Dict[str, Any]:
    materialized_fills = [row for row in fills if isinstance(row, dict)]
    materialized_snapshots = [row for row in followup_snapshots if isinstance(row, dict)]
    count_by_fill: List[Dict[str, Any]] = []
    missing_by_fill: List[Dict[str, Any]] = []
    coverage: Dict[str, Dict[str, Any]] = {
        _horizon_label(horizon): {"covered_fill_count": 0, "missing_fill_count": 0, "eligible_elapsed_fill_count": 0, "matched_rows": [], "gap_reason_counts": {}}
        for horizon in horizons
    }
    first_by_fill: List[Dict[str, Any]] = []
    last_by_fill: List[Dict[str, Any]] = []
    fill_tokens: List[Dict[str, Any]] = []
    for fill in materialized_fills:
        identity = _fill_identity(fill)
        token_id = str(fill.get("token_id") or "")
        entry_time = _parse_utc(fill.get("entry_time") or fill.get("timestamp") or fill.get("generated_at"))
        snapshots = _snapshots_for_fill(fill, materialized_snapshots)
        after_entry = []
        for snapshot in snapshots:
            snap_time = _parse_utc(snapshot.get("timestamp") or snapshot.get("recorded_at"))
            if snap_time is not None and entry_time is not None and snap_time > entry_time:
                after_entry.append((snap_time, snapshot))
        fill_tokens.append({"fill_id": fill.get("fill_id"), "market_slug": fill.get("market_slug"), "token_id": token_id})
        count_by_fill.append({"fill_id": fill.get("fill_id"), "token_id": token_id, "count": len(after_entry)})
        if after_entry:
            first_by_fill.append({"fill_id": fill.get("fill_id"), "token_id": token_id, "timestamp": after_entry[0][0].isoformat().replace("+00:00", "Z")})
            last_by_fill.append({"fill_id": fill.get("fill_id"), "token_id": token_id, "timestamp": after_entry[-1][0].isoformat().replace("+00:00", "Z")})
        missing_horizons: List[str] = []
        for horizon in horizons:
            label = _horizon_label(horizon)
            matched = None
            lag = None
            target = None
            tolerance = _horizon_tolerance_seconds(int(horizon))
            if entry_time is not None:
                target = entry_time + timedelta(seconds=int(horizon))
                if after_entry and after_entry[-1][0] >= target - timedelta(seconds=tolerance):
                    coverage[label]["eligible_elapsed_fill_count"] += 1
                for snap_time, snapshot in after_entry:
                    candidate_lag = (snap_time - target).total_seconds()
                    if abs(candidate_lag) <= tolerance and (lag is None or abs(candidate_lag) < abs(lag)):
                        matched = snapshot
                        lag = candidate_lag
            if matched is None:
                coverage[label]["missing_fill_count"] += 1
                missing_horizons.append(label)
                if entry_time is None:
                    gap_reason = "missing_entry_time"
                elif not after_entry:
                    gap_reason = "no_followup_snapshot_after_entry"
                elif target is not None and after_entry[-1][0] < target - timedelta(seconds=tolerance):
                    gap_reason = "horizon_not_reached_by_latest_snapshot"
                elif target is not None and after_entry[0][0] > target + timedelta(seconds=tolerance):
                    gap_reason = "watcher_started_after_horizon_tolerance"
                else:
                    gap_reason = "no_snapshot_within_tolerance"
                coverage[label]["gap_reason_counts"][gap_reason] = coverage[label]["gap_reason_counts"].get(gap_reason, 0) + 1
            else:
                coverage[label]["covered_fill_count"] += 1
                coverage[label]["matched_rows"].append(
                    {
                        "fill_id": fill.get("fill_id"),
                        "token_id": token_id,
                        "matched_snapshot_time": matched.get("timestamp") or matched.get("recorded_at"),
                        "match_lag_seconds": lag,
                    }
                )
        missing_by_fill.append({"fill_id": fill.get("fill_id"), "token_id": token_id, "missing_horizons": missing_horizons})
    coverage_gap_reason_by_horizon = {
        horizon: dict(values.get("gap_reason_counts") or {})
        for horizon, values in coverage.items()
    }
    return {
        "schema_version": f"{SCHEMA_VERSION}.formal_fill_followup_snapshot_coverage",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fill_count": len(materialized_fills),
        "fill_tokens": fill_tokens,
        "followup_snapshot_count": len(materialized_snapshots),
        "followup_snapshot_count_by_fill": count_by_fill,
        "first_snapshot_after_entry": first_by_fill,
        "last_snapshot_after_entry": last_by_fill,
        "coverage_by_horizon": coverage,
        "coverage_gap_reason_by_horizon": coverage_gap_reason_by_horizon,
        "missing_horizon_by_fill": missing_by_fill,
        "snapshot_path": str(snapshot_path),
        "watcher_status": watcher_status or {"state": "unknown"},
    }


def scan_active_probability_edges(
    *,
    active_markets: Iterable[Dict[str, Any]],
    model_report: Dict[str, Any],
    focus_report: Optional[Dict[str, Any]] = None,
    crypto_report: Optional[Dict[str, Any]] = None,
    surface_report: Optional[Dict[str, Any]] = None,
    sensitivity_report: Optional[Dict[str, Any]] = None,
    crypto_touch_formal_fill_mode: str = "disabled",
    min_edge: float = 0.02,
    stricter_edge: float = 0.02,
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
    raw_crypto_candidates = [
        {**row, "priority_rank": 1}
        for row in ((crypto_report or {}).get("candidates") or [])
        if isinstance(row, dict) and row.get("live_order_path") is False
    ]
    crypto_gate = _gate_crypto_candidates(
        raw_crypto_candidates,
        surface_report=surface_report,
        sensitivity_report=sensitivity_report,
        crypto_touch_formal_fill_mode=crypto_touch_formal_fill_mode,
        min_edge=float(min_edge),
        stricter_edge=float(stricter_edge),
    )
    crypto_candidates = [{**row, "priority_rank": 1} for row in crypto_gate["accepted_candidates"]]
    crypto_watch_rows = crypto_gate["watch_rows"]
    candidates.extend(crypto_candidates)
    watch_rows.extend(crypto_watch_rows)
    model_ready = int((crypto_report or {}).get("model_ready_count") or len(crypto_candidates))
    lane_reports: Dict[str, Dict[str, Any]] = {
        "crypto_model_lane": {
            "scanned_count": int((crypto_report or {}).get("parsed_crypto_market_count") or 0),
            "model_ready_count": int((crypto_report or {}).get("model_ready_count") or 0),
            "executable_price_available_count": int((crypto_report or {}).get("executable_price_available_count") or 0),
            "candidate_count": len(crypto_candidates),
            "paper_fill_count": len(crypto_candidates),
            "blocker_counts": crypto_blockers,
            "top_watch_rows": (crypto_watch_rows + ((crypto_report or {}).get("top_10_near_misses") or []))[:10],
            "top_candidates": crypto_candidates[:10],
            "old_formal_fill_count": crypto_gate["old_formal_fill_count"],
            "new_formal_fill_count": crypto_gate["new_formal_fill_count"],
            "downgraded_due_surface_count": crypto_gate["downgraded_due_surface_count"],
            "downgraded_due_sensitivity_count": crypto_gate["downgraded_due_sensitivity_count"],
            "watch_count": crypto_gate["watch_count"],
            "surface_gate_enforced": crypto_gate["surface_gate_enforced"],
            "sensitivity_gate_enforced": crypto_gate["sensitivity_gate_enforced"],
            "formal_fill_mode": crypto_gate["formal_fill_mode"],
            "new_formal_fill_generation_enabled": crypto_gate["new_formal_fill_generation_enabled"],
            "stricter_edge": crypto_gate["stricter_edge"],
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
        "old_formal_fill_count": crypto_gate["old_formal_fill_count"],
        "new_formal_fill_count": crypto_gate["new_formal_fill_count"],
        "downgraded_due_surface_count": crypto_gate["downgraded_due_surface_count"],
        "downgraded_due_sensitivity_count": crypto_gate["downgraded_due_sensitivity_count"],
        "watch_count": crypto_gate["watch_count"],
        "formal_fill_mode": crypto_gate["formal_fill_mode"],
        "new_formal_fill_generation_enabled": crypto_gate["new_formal_fill_generation_enabled"],
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


__all__ = [
    "SCHEMA_VERSION",
    "build_formal_fill_followup_orderbook_snapshots",
    "build_formal_fill_followup_snapshot_coverage_report",
    "load_json",
    "load_jsonl",
    "scan_active_probability_edges",
    "write_json",
    "write_jsonl",
]
