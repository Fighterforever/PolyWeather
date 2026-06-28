from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


SCHEMA_VERSION = "polyweather_maker_shadow_v2.v1"
QUOTE_SCHEMA_VERSION = "polyweather_maker_shadow_v2_quote.v1"
FILL_SCHEMA_VERSION = "polyweather_maker_shadow_v2_inferred_fill.v1"
MARKOUT_SCHEMA_VERSION = "polyweather_maker_shadow_v2_markout.v1"
STRATEGY_ID = "maker_shadow_v2"
SUPPORTED_SETTLEMENT_SOURCES = {"metar", "noaa", "wunderground", "aeroweb"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(value: Any, *, length: int = 20) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[: max(1, int(length))]


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _row_dict(row: Dict[str, Any], field: str) -> Dict[str, Any]:
    value = row.get(field)
    return value if isinstance(value, dict) else {}


def _first_text(row: Dict[str, Any], *fields: str) -> str:
    sources = (row, _row_dict(row, "settlement_spec"), _row_dict(row, "market_bucket"))
    for source in sources:
        for field in fields:
            text = str(source.get(field) or "").strip()
            if text:
                return text
    return ""


def _first_float(row: Dict[str, Any], *fields: str) -> Optional[float]:
    sources = (row, _row_dict(row, "settlement_spec"), _row_dict(row, "market_bucket"))
    for source in sources:
        for field in fields:
            value = _safe_float(source.get(field))
            if value is not None:
                return value
    return None


def maker_price_bucket(value: Any) -> str:
    price = _safe_float(value)
    if price is None:
        return "missing"
    if price < 0.005:
        return "price_lt_0_005"
    if price < 0.03:
        return "price_0_005_to_0_03"
    return "price_ge_0_03"


def _depth(row: Dict[str, Any], side: str) -> Optional[float]:
    if side == "bid":
        direct_fields = ("bid_depth", "bid_depth_usdc_3c")
        book_field = "bid_depth_usdc_3c"
    else:
        direct_fields = ("ask_depth", "ask_depth_usdc_3c", "execution_liquidity")
        book_field = "ask_depth_usdc_3c"
    value = _first_float(row, *direct_fields)
    if value is not None:
        return value
    book = _row_dict(row, "order_book")
    return _safe_float(book.get(book_field))


def _fair_value(row: Dict[str, Any]) -> tuple[Optional[float], str, bool]:
    side = _first_text(row, "side").lower()
    directional = False
    for field in (
        "station_adjusted_probability",
        "station_probability",
        "p_model",
        "p_lcb",
        "forecast_probability",
    ):
        value = _safe_float(row.get(field))
        if value is not None:
            directional = True
            if side == "no":
                value = 1.0 - value
            return max(0.0, min(1.0, value)), field, directional
    value = _safe_float(row.get("market_implied_de_vig_side_probability"))
    if value is not None:
        return max(0.0, min(1.0, value)), "market_implied_de_vig_side_probability", directional
    yes_value = _safe_float(row.get("market_implied_de_vig_yes_probability") or row.get("market_implied_yes_price"))
    if yes_value is not None:
        if side == "no":
            yes_value = 1.0 - yes_value
        return max(0.0, min(1.0, yes_value)), "market_implied_de_vig_yes_probability", directional
    bid = _safe_float(row.get("best_bid") or row.get("bid"))
    ask = _safe_float(row.get("best_ask") or row.get("ask") or row.get("price"))
    if bid is not None and ask is not None:
        return max(0.0, min(1.0, (bid + ask) / 2.0)), "orderbook_midpoint", directional
    return None, "missing_fair_value", directional


def _observation_anomaly_by_station(observations: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    anomalies: Dict[str, set[str]] = defaultdict(set)
    for row in observations:
        if not isinstance(row, dict):
            continue
        station = str(row.get("station_code") or "").strip().upper()
        if not station:
            continue
        flags = row.get("anomaly_flags") or row.get("quality_flags") or []
        for flag in flags if isinstance(flags, list) else [flags]:
            if str(flag or "").strip():
                anomalies[station].add(str(flag))
        if bool(row.get("active_metar_anomaly") or row.get("station_anomaly")):
            anomalies[station].add("active_metar_anomaly")
    return {station: sorted(flags) for station, flags in anomalies.items()}


def _material_update(row: Dict[str, Any], *, threshold_c: float) -> Optional[str]:
    if bool(row.get("metar_update_event") or row.get("metar_update")):
        change = abs(_safe_float(row.get("current_high_change_c")) or 0.0)
        if change >= float(threshold_c):
            return "metar_update_changed_current_high"
    if bool(row.get("station_anomaly") or row.get("active_metar_anomaly")):
        return "station_anomaly"
    return None


def _base_quote_payload(
    row: Dict[str, Any],
    *,
    generated_at: str,
    fair_value: float,
    fair_value_source: str,
    fair_value_directional: bool,
    quote_type: str,
    quote_price: float,
    edge_after_cost: float,
    maker_margin: float,
    adverse_selection_haircut: float,
    estimated_rebate_cents: float,
    cancel_reason: Optional[str],
) -> Dict[str, Any]:
    best_bid = _safe_float(row.get("best_bid") or row.get("bid"))
    best_ask = _safe_float(row.get("best_ask") or row.get("ask") or row.get("price"))
    spread = _safe_float(row.get("spread"))
    if spread is None and best_bid is not None and best_ask is not None:
        spread = best_ask - best_bid
    token_id = _first_text(row, "token_id")
    market_slug = _first_text(row, "market_slug")
    side = _first_text(row, "side").lower() or "yes"
    station_code = _first_text(row, "station_code").upper()
    target_date = _first_text(row, "target_date", "selected_date")
    bucket_type = _first_text(row, "bucket_type").lower()
    threshold = _first_float(row, "threshold")
    quote_id = "mkv2_" + _stable_hash(
        {
            "market_slug": market_slug,
            "token_id": token_id,
            "side": side,
            "quote_type": quote_type,
            "quote_price": round(quote_price, 6),
            "generated_at": generated_at,
        }
    )
    return {
        "schema_version": QUOTE_SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "platform": "polymarket",
        "quote_id": quote_id,
        "generated_at": generated_at,
        "market_slug": market_slug,
        "event_slug": _first_text(row, "event_slug"),
        "token_id": token_id,
        "side": side,
        "quote_type": quote_type,
        "fair_value": round(fair_value, 6),
        "fair_value_source": fair_value_source,
        "fair_value_directional": bool(fair_value_directional),
        "quote_price": round(float(quote_price), 6),
        "current_best_bid": best_bid,
        "current_best_ask": best_ask,
        "spread": round(float(spread), 6) if spread is not None else None,
        "station_code": station_code,
        "settlement_source": _first_text(row, "settlement_source").lower(),
        "target_date": target_date,
        "bucket_type": bucket_type,
        "threshold": threshold,
        "price_bucket": maker_price_bucket(best_ask if quote_type == "maker_bid" else best_bid),
        "bid_depth": _depth(row, "bid"),
        "ask_depth": _depth(row, "ask"),
        "orderbook_snapshot_id": row.get("orderbook_snapshot_id")
        or _row_dict(row, "order_book").get("timestamp")
        or row.get("snapshot_id"),
        "queue_proxy": "inside_spread_improves_best",
        "maker_margin": float(maker_margin),
        "adverse_selection_haircut": float(adverse_selection_haircut),
        "edge_after_cost": round(float(edge_after_cost), 6),
        "estimated_rebate": float(estimated_rebate_cents),
        "cancel_reason": cancel_reason,
        "touch_event": False,
        "inferred_fill": False,
        "fill_confidence": "none",
        "markout_1m": None,
        "markout_5m": None,
        "markout_15m": None,
        "resolved_pnl": None,
        "pnl_without_rebate": None,
        "pnl_with_rebate": None,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def build_maker_shadow_quotes(
    rows: Iterable[Dict[str, Any]],
    *,
    observations: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
    min_spread: float = 0.02,
    min_bid_depth: float = 1.0,
    min_ask_depth: float = 1.0,
    maker_margin: float = 0.006,
    adverse_selection_haircut: float = 0.002,
    cost_cents: float = 0.0,
    estimated_rebate_cents: float = 0.0,
    no_quote_before_resolution_minutes: float = 30.0,
    material_update_threshold_c: float = 0.5,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    del no_quote_before_resolution_minutes
    generated_at = generated_at or utc_now_iso()
    anomaly_by_station = _observation_anomaly_by_station(observations)
    quotes: List[Dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        market_family = _first_text(row, "market_family").lower()
        bucket_type = _first_text(row, "bucket_type").lower()
        side = _first_text(row, "side").lower()
        station_code = _first_text(row, "station_code").upper()
        source = _first_text(row, "settlement_source").lower()
        best_bid = _safe_float(row.get("best_bid") or row.get("bid"))
        best_ask = _safe_float(row.get("best_ask") or row.get("ask") or row.get("price"))
        spread = _safe_float(row.get("spread"))
        if spread is None and best_bid is not None and best_ask is not None:
            spread = best_ask - best_bid
        bid_depth = _depth(row, "bid")
        ask_depth = _depth(row, "ask")
        fair_value, fair_source, directional = _fair_value(row)
        cancel_reason = _material_update(row, threshold_c=material_update_threshold_c)
        if station_code in anomaly_by_station:
            cancel_reason = "station_anomaly"
        rejection: Optional[str] = None
        if market_family and market_family != "temperature":
            rejection = "not_temperature"
        elif bucket_type not in {"ge", "le"}:
            rejection = "bucket_not_ge_le"
        elif side not in {"yes", "no"}:
            rejection = "unsupported_side"
        elif source not in SUPPORTED_SETTLEMENT_SOURCES:
            rejection = "unsupported_settlement_source"
        elif not station_code:
            rejection = "missing_station_code"
        elif best_bid is None or best_ask is None or spread is None:
            rejection = "missing_orderbook"
        elif maker_price_bucket(best_ask) == "price_lt_0_005":
            rejection = "dust_price"
        elif spread < float(min_spread):
            rejection = "spread_below_min"
        elif bid_depth is None or bid_depth < float(min_bid_depth):
            rejection = "bid_depth_below_min"
        elif ask_depth is None or ask_depth < float(min_ask_depth):
            rejection = "ask_depth_below_min"
        elif cancel_reason is not None:
            rejection = cancel_reason
        elif fair_value is None:
            rejection = fair_source
        if rejection is not None:
            reason_counts[rejection] += 1
            continue
        assert fair_value is not None and best_bid is not None and best_ask is not None
        for quote_type, quote_price, edge in (
            ("maker_bid", fair_value - float(maker_margin), fair_value - (fair_value - float(maker_margin))),
            ("maker_ask", fair_value + float(maker_margin), (fair_value + float(maker_margin)) - fair_value),
        ):
            quote_price = max(0.001, min(0.999, quote_price))
            if not (best_bid < quote_price < best_ask):
                reason_counts[f"{quote_type}_not_inside_spread"] += 1
                continue
            edge_after_cost = float(edge) - float(adverse_selection_haircut) - float(cost_cents) / 100.0
            if edge_after_cost <= 0:
                reason_counts[f"{quote_type}_edge_after_cost_nonpositive"] += 1
                continue
            quotes.append(
                _base_quote_payload(
                    row,
                    generated_at=generated_at,
                    fair_value=fair_value,
                    fair_value_source=fair_source,
                    fair_value_directional=directional,
                    quote_type=quote_type,
                    quote_price=quote_price,
                    edge_after_cost=edge_after_cost,
                    maker_margin=maker_margin,
                    adverse_selection_haircut=adverse_selection_haircut,
                    estimated_rebate_cents=estimated_rebate_cents,
                    cancel_reason=None,
                )
            )
    return quotes, dict(sorted(reason_counts.items()))


def _latest_rows_by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_token: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        token = _first_text(row, "token_id")
        if token:
            by_token[token] = row
    return by_token


def simulate_maker_shadow_fills(
    quotes: Iterable[Dict[str, Any]],
    rows_after_quote: Iterable[Dict[str, Any]],
    *,
    generated_at: Optional[str] = None,
    max_adverse_move: float = 0.02,
    estimated_rebate_cents: float = 0.0,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    generated_at = generated_at or utc_now_iso()
    current_by_token = _latest_rows_by_token(rows_after_quote)
    fills: List[Dict[str, Any]] = []
    markouts: List[Dict[str, Any]] = []
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        token = str(quote.get("token_id") or "").strip()
        row = current_by_token.get(token)
        if row is None:
            continue
        best_bid = _safe_float(row.get("best_bid") or row.get("bid"))
        best_ask = _safe_float(row.get("best_ask") or row.get("ask") or row.get("price"))
        quote_price = _safe_float(quote.get("quote_price"))
        fair_after, fair_source_after, _ = _fair_value(row)
        if fair_after is None and best_bid is not None and best_ask is not None:
            fair_after = (best_bid + best_ask) / 2.0
            fair_source_after = "orderbook_midpoint"
        if quote_price is None or fair_after is None:
            continue
        quote_type = str(quote.get("quote_type") or "")
        touched = quote_type == "maker_bid" and best_ask is not None and best_ask <= quote_price
        touched = touched or (quote_type == "maker_ask" and best_bid is not None and best_bid >= quote_price)
        if not touched:
            continue
        if quote_type == "maker_bid":
            pnl_without_rebate = (fair_after - quote_price) * 100.0
            touch_distance = quote_price - float(best_ask if best_ask is not None else quote_price)
        else:
            pnl_without_rebate = (quote_price - fair_after) * 100.0
            touch_distance = float(best_bid if best_bid is not None else quote_price) - quote_price
        pnl_with_rebate = pnl_without_rebate + float(estimated_rebate_cents)
        fill_id = "mkv2fill_" + _stable_hash({"quote_id": quote.get("quote_id"), "generated_at": generated_at})
        confidence = "medium" if touch_distance >= float(max_adverse_move) / 2.0 else "low"
        fill = {
            **quote,
            "schema_version": FILL_SCHEMA_VERSION,
            "fill_id": fill_id,
            "fill_checked_at": generated_at,
            "touch_event": True,
            "inferred_fill": True,
            "fill_confidence": confidence,
            "best_bid_after": best_bid,
            "best_ask_after": best_ask,
            "fair_value_after": round(float(fair_after), 6),
            "fair_value_after_source": fair_source_after,
            "pnl_without_rebate": round(pnl_without_rebate, 6),
            "pnl_with_rebate": round(pnl_with_rebate, 6),
            "estimated_rebate": float(estimated_rebate_cents),
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        fills.append(fill)
        markouts.append(
            {
                "schema_version": MARKOUT_SCHEMA_VERSION,
                "strategy_id": STRATEGY_ID,
                "platform": "polymarket",
                "quote_id": quote.get("quote_id"),
                "fill_id": fill_id,
                "market_slug": quote.get("market_slug"),
                "token_id": token,
                "side": quote.get("side"),
                "quote_type": quote_type,
                "quote_price": quote_price,
                "fair_value_after": round(float(fair_after), 6),
                "fair_value_after_source": fair_source_after,
                "markout_1m": round(pnl_without_rebate, 6),
                "markout_5m": round(pnl_without_rebate, 6),
                "markout_15m": round(pnl_without_rebate, 6),
                "pnl_without_rebate": round(pnl_without_rebate, 6),
                "pnl_with_rebate": round(pnl_with_rebate, 6),
                "estimated_rebate": float(estimated_rebate_cents),
                "adverse_selection": pnl_without_rebate < 0,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    return fills, markouts


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def _group_summary(quotes: Sequence[Dict[str, Any]], fills: Sequence[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    quote_counts = Counter(str(row.get(field) or "missing") for row in quotes)
    fill_groups: Dict[str, List[float]] = defaultdict(list)
    for row in fills:
        key = str(row.get(field) or "missing")
        value = _safe_float(row.get("pnl_without_rebate"))
        if value is not None:
            fill_groups[key].append(value)
    rows: List[Dict[str, Any]] = []
    for key in sorted(set(quote_counts) | set(fill_groups)):
        rows.append(
            {
                field: key,
                "quote_count": quote_counts.get(key, 0),
                "inferred_fill_count": len(fill_groups.get(key) or []),
                "mean_markout_without_rebate": _mean(fill_groups.get(key) or []),
            }
        )
    return rows


def _maker_shadow_input_funnel(
    rows: Sequence[Dict[str, Any]],
    *,
    min_spread: float,
) -> Dict[str, Any]:
    total_scanned = len([row for row in rows if isinstance(row, dict)])
    ge_le_rows: List[Dict[str, Any]] = [
        row
        for row in rows
        if isinstance(row, dict) and _first_text(row, "bucket_type").lower() in {"ge", "le"}
    ]
    non_dust_rows: List[Dict[str, Any]] = [
        row
        for row in ge_le_rows
        if maker_price_bucket(row.get("best_ask") or row.get("ask") or row.get("price")) != "price_lt_0_005"
    ]
    orderbook_rows: List[Dict[str, Any]] = [
        row
        for row in non_dust_rows
        if _safe_float(row.get("best_bid") or row.get("bid")) is not None
        and _safe_float(row.get("best_ask") or row.get("ask") or row.get("price")) is not None
    ]
    spread_rows: List[Dict[str, Any]] = []
    for row in orderbook_rows:
        spread = _safe_float(row.get("spread"))
        if spread is None:
            bid = _safe_float(row.get("best_bid") or row.get("bid"))
            ask = _safe_float(row.get("best_ask") or row.get("ask") or row.get("price"))
            spread = ask - bid if bid is not None and ask is not None else None
        if spread is not None and spread >= float(min_spread):
            spread_rows.append(row)
    return {
        "total_scanned_rows": total_scanned,
        "total_ge_le_rows": len(ge_le_rows),
        "total_non_dust_rows": len(non_dust_rows),
        "total_orderbook_available_rows": len(orderbook_rows),
        "total_spread_sufficient_rows": len(spread_rows),
    }


def build_maker_shadow_v2_report(
    rows: Iterable[Dict[str, Any]],
    *,
    previous_quotes: Iterable[Dict[str, Any]] = (),
    observations: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
    **quote_kwargs: Any,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    row_list = [row for row in rows if isinstance(row, dict)]
    estimated_rebate_cents = float(quote_kwargs.get("estimated_rebate_cents", 0.0))
    min_spread = float(quote_kwargs.get("min_spread", 0.02))
    quotes, rejection_counts = build_maker_shadow_quotes(
        row_list,
        observations=observations,
        generated_at=generated_at,
        **quote_kwargs,
    )
    fills, markouts = simulate_maker_shadow_fills(
        previous_quotes,
        rows,
        generated_at=generated_at,
        estimated_rebate_cents=estimated_rebate_cents,
    )
    pnl_without = [
        float(value)
        for row in markouts
        for value in [_safe_float(row.get("pnl_without_rebate"))]
        if value is not None
    ]
    pnl_with = [
        float(value)
        for row in markouts
        for value in [_safe_float(row.get("pnl_with_rebate"))]
        if value is not None
    ]
    stale_quote_count = len([row for row in quotes if row.get("cancel_reason")])
    report = {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "platform": "polymarket",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "quote_count": len(quotes),
        **_maker_shadow_input_funnel(row_list, min_spread=min_spread),
        "inferred_fill_count": len(fills),
        "markout_count": len(markouts),
        "mean_markout_without_rebate": _mean(pnl_without),
        "mean_markout_with_rebate": _mean(pnl_with),
        "adverse_selection_count": len([value for value in pnl_without if value < 0]),
        "stale_quote_count": stale_quote_count,
        "estimated_rebate_cents": estimated_rebate_cents,
        "no_candidate_reason_counts": dict(sorted(rejection_counts.items())),
        "blocker_counts": dict(sorted(rejection_counts.items())),
        "by_station": _group_summary(quotes, fills, "station_code"),
        "by_bucket_type": _group_summary(quotes, fills, "bucket_type"),
        "by_price_bucket": _group_summary(quotes, fills, "price_bucket"),
        "quotes": quotes,
        "fills": fills,
        "markouts": markouts,
    }
    return report


def _report_generated_at(report: Dict[str, Any]) -> str:
    return str(report.get("generated_at") or "")


def _sum_report_int(reports: Sequence[Dict[str, Any]], field: str) -> int:
    return sum(_safe_int(report.get(field)) for report in reports if isinstance(report, dict))


def _merge_counts(reports: Sequence[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for report in reports:
        value = report.get(field) if isinstance(report, dict) else {}
        if isinstance(value, dict):
            for key, count in value.items():
                counts[str(key)] += _safe_int(count)
    return dict(sorted(counts.items()))


def _aggregate_report_groups(reports: Sequence[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"quote_count": 0, "inferred_fill_count": 0, "markouts": []})
    for report in reports:
        rows = report.get(field) if isinstance(report, dict) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            group_key_field = field.removeprefix("by_")
            key = str(row.get(group_key_field) or "missing")
            grouped[key]["quote_count"] += _safe_int(row.get("quote_count"))
            grouped[key]["inferred_fill_count"] += _safe_int(row.get("inferred_fill_count"))
            mean = _safe_float(row.get("mean_markout_without_rebate"))
            fills = _safe_int(row.get("inferred_fill_count"))
            if mean is not None and fills > 0:
                grouped[key]["markouts"].extend([mean] * fills)
    output: List[Dict[str, Any]] = []
    for key, payload in sorted(grouped.items()):
        output.append(
            {
                field.removeprefix("by_"): key,
                "quote_count": int(payload["quote_count"]),
                "inferred_fill_count": int(payload["inferred_fill_count"]),
                "mean_markout_without_rebate": _mean(payload["markouts"]),
            }
        )
    return output


def build_maker_shadow_v2_funnel_report(
    reports: Iterable[Dict[str, Any]],
    *,
    generated_at: Optional[str] = None,
    min_window_hours: float = 24.0,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    materialized = [report for report in reports if isinstance(report, dict)]
    timestamps = sorted(value for report in materialized for value in [_report_generated_at(report)] if value)
    markout_without: List[float] = []
    markout_with: List[float] = []
    for report in materialized:
        fills = _safe_int(report.get("inferred_fill_count"))
        mean_without = _safe_float(report.get("mean_markout_without_rebate"))
        mean_with = _safe_float(report.get("mean_markout_with_rebate"))
        if mean_without is not None and fills > 0:
            markout_without.extend([mean_without] * fills)
        if mean_with is not None and fills > 0:
            markout_with.extend([mean_with] * fills)
    total_quote_count = _sum_report_int(materialized, "quote_count")
    total_inferred_fill_count = _sum_report_int(materialized, "inferred_fill_count")
    mean_without = _mean(markout_without)
    if total_quote_count <= 0 and len(materialized) > 0:
        opportunity_status = "maker_shadow_no_current_opportunity_density"
    elif total_inferred_fill_count > 0 and mean_without is not None and mean_without < 0:
        opportunity_status = "maker_shadow_negative_adverse_selection"
    elif total_inferred_fill_count >= 30 and mean_without is not None and mean_without > 0:
        opportunity_status = "maker_shadow_continue_paper_research"
    else:
        opportunity_status = "maker_shadow_collect_more_shadow_evidence"
    total_scanned = _sum_report_int(materialized, "total_scanned_rows")
    return {
        "schema_version": "polyweather_maker_shadow_v2_24h_funnel.v1",
        "strategy_id": STRATEGY_ID,
        "platform": "polymarket",
        "generated_at": generated_at,
        "window_hours": float(min_window_hours),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "run_count": len(materialized),
        "first_run_at": timestamps[0] if timestamps else None,
        "last_run_at": timestamps[-1] if timestamps else None,
        "total_scanned_rows": total_scanned,
        "total_ge_le_rows": _sum_report_int(materialized, "total_ge_le_rows"),
        "total_non_dust_rows": _sum_report_int(materialized, "total_non_dust_rows"),
        "total_spread_sufficient_rows": _sum_report_int(materialized, "total_spread_sufficient_rows"),
        "total_orderbook_available_rows": _sum_report_int(materialized, "total_orderbook_available_rows"),
        "total_quote_count": total_quote_count,
        "total_inferred_fill_count": total_inferred_fill_count,
        "total_markout_count": _sum_report_int(materialized, "markout_count"),
        "mean_markout_without_rebate": mean_without,
        "mean_markout_with_rebate": _mean(markout_with),
        "blocker_counts": _merge_counts(materialized, "blocker_counts"),
        "quote_rate": round(total_quote_count / total_scanned, 6) if total_scanned > 0 else None,
        "inferred_fill_rate": round(total_inferred_fill_count / total_quote_count, 6) if total_quote_count > 0 else None,
        "by_station": _aggregate_report_groups(materialized, "by_station"),
        "by_bucket_type": _aggregate_report_groups(materialized, "by_bucket_type"),
        "opportunity_status": opportunity_status,
    }


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


__all__ = [
    "SCHEMA_VERSION",
    "STRATEGY_ID",
    "build_maker_shadow_quotes",
    "build_maker_shadow_v2_report",
    "build_maker_shadow_v2_funnel_report",
    "maker_price_bucket",
    "simulate_maker_shadow_fills",
    "write_json",
    "write_jsonl",
]
