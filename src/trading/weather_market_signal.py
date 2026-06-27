from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_signal_risk_filter import (
    normalize_risk_rules,
    risk_blockers_for_row,
    risk_rules_for_mode,
)
from src.trading.polymarket_readonly import classify_weather_market_family_from_row
from src.trading.weather_strategies import assign_weather_strategy


SCHEMA_VERSION = "polyweather_weather_market_signal_report.v1"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(row: Dict[str, Any], fields: Iterable[str]) -> Optional[float]:
    for field in fields:
        value = _safe_float(row.get(field))
        if value is not None:
            return value
    return None


def _first_text(row: Dict[str, Any], fields: Iterable[str]) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class WeatherMarketSignalConfig:
    min_edge_percent: float = 5.0
    min_liquidity: float = 500.0
    min_model_probability: float = 0.08
    min_price: float = 0.03
    max_price: float = 0.85
    max_spread: float = 0.03
    max_candidates: int = 10
    require_order_book: bool = True
    allowed_sides: Tuple[str, ...] = ()
    excluded_bucket_types: Tuple[str, ...] = ()
    min_bid_depth_usdc_3c: float = 0.0
    min_ask_depth_usdc_3c: float = 0.0
    max_quarantine: int = 30
    quarantine_near_miss_categories: Tuple[str, ...] = ()
    suppress_saturated_broad_risk_rules: bool = False
    suppress_saturated_partition_risk_rules: bool = False
    saturated_risk_rule_min_coverage: float = 0.80
    partition_saturated_risk_rule_min_coverage: float = 0.80
    require_settlement_spec: bool = True
    require_ev_safe: bool = True
    min_ev_safe: float = 0.0


def _price_from_row(row: Dict[str, Any]) -> Optional[float]:
    return _first_float(
        row,
        (
            "price",
            "ask",
            "best_ask",
            "market_probability",
            "yes_price",
            "no_price",
        ),
    )


def _spread_from_row(row: Dict[str, Any]) -> Optional[float]:
    return _first_float(row, ("spread", "entry_spread", "yes_spread", "no_spread"))


def _liquidity_from_row(row: Dict[str, Any]) -> Optional[float]:
    return _first_float(
        row,
        (
            "execution_liquidity",
            "book_liquidity",
            "liquidity",
            "liquidityNum",
            "liquidityClob",
            "volume",
        ),
    )


def _bucket_type_from_label(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith(">="):
        return "ge"
    if text.startswith("<="):
        return "le"
    if text.startswith("="):
        return "eq"
    if "-" in text:
        return "range"
    return "unknown"


def _bucket_type_from_row(row: Dict[str, Any]) -> str:
    bucket = row.get("market_bucket") if isinstance(row.get("market_bucket"), dict) else {}
    value = str(bucket.get("bucket_type") or "").strip().lower()
    if value:
        return value
    spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
    value = str(spec.get("bucket_type") or "").strip().lower()
    if value:
        return value
    return _bucket_type_from_label(row.get("bucket_label"))


def _order_book_depth(row: Dict[str, Any], field: str) -> Optional[float]:
    value = _safe_float(row.get(field))
    if value is not None:
        return value
    order_book = row.get("order_book") if isinstance(row.get("order_book"), dict) else {}
    return _safe_float(order_book.get(field))


def _settlement_text(
    row: Dict[str, Any],
    settlement_spec: Optional[Dict[str, Any]],
    *fields: str,
) -> Optional[str]:
    for field in fields:
        value = row.get(field)
        if value is None and settlement_spec:
            value = settlement_spec.get(field)
        text = str(value or "").strip()
        if text:
            return text
    return None


def _row_is_tradable(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    blockers: List[str] = []
    if row.get("active") is False:
        blockers.append("market_inactive")
    if row.get("closed") is True:
        blockers.append("market_closed")
    if row.get("tradable") is False:
        blockers.append("row_not_tradable")
    if row.get("accepting_orders") is False:
        blockers.append("not_accepting_orders")
    return not blockers, blockers


def assess_weather_market_row(
    row: Dict[str, Any],
    *,
    config: WeatherMarketSignalConfig = WeatherMarketSignalConfig(),
    risk_rules: Optional[Iterable[Dict[str, Any]]] = None,
    risk_rule_mode: str = "live",
) -> Dict[str, Any]:
    """Audit one scan-terminal row as a paper-only trade candidate."""

    market_slug = _first_text(row, ("market_slug", "slug", "market_key", "id"))
    market_family = _first_text(row, ("market_family",)) or classify_weather_market_family_from_row(row)
    side = _first_text(row, ("side",))
    city = _first_text(row, ("city", "city_display_name"))
    bucket_label = _first_text(row, ("bucket_label",))
    bucket_type = _bucket_type_from_row(row)
    price = _price_from_row(row)
    spread = _spread_from_row(row)
    liquidity = _liquidity_from_row(row)
    bid_depth = _order_book_depth(row, "bid_depth_usdc_3c")
    ask_depth = _order_book_depth(row, "ask_depth_usdc_3c")
    edge_percent = _first_float(row, ("edge_percent", "edge"))
    ev_safe = _first_float(row, ("ev_safe",))
    p_lcb = _first_float(row, ("p_lcb",))
    q_effective = _first_float(row, ("q_effective",))
    cost = _first_float(row, ("cost",))
    model_probability = _first_float(row, ("model_probability",))
    score = _first_float(row, ("final_score", "signal_confidence"))
    strategy = assign_weather_strategy(row, bucket_type=bucket_type)

    blockers: List[str] = []
    warnings: List[str] = []
    _, tradable_blockers = _row_is_tradable(row)
    blockers.extend(tradable_blockers)

    if not market_slug:
        blockers.append("missing_market_id")
    if not side:
        warnings.append("missing_side")
    elif config.allowed_sides and side.lower() not in {value.lower() for value in config.allowed_sides}:
        blockers.append("side_not_allowed")
    if bucket_type in {value.lower() for value in config.excluded_bucket_types}:
        blockers.append(f"bucket_type_excluded:{bucket_type}")
    if price is None:
        blockers.append("missing_market_price")
    elif price < config.min_price:
        blockers.append("price_below_min")
    elif price > config.max_price:
        blockers.append("price_above_max")

    model_join_status = str(row.get("model_join_status") or "").strip()
    if model_join_status == "unsupported_market_type":
        blockers.append(f"unsupported_model_family:{market_family}")
    elif model_join_status == "unsupported_settlement_spec":
        reasons = [
            str(reason)
            for reason in row.get("settlement_spec_unsupported_reasons") or []
            if str(reason)
        ]
        blockers.append(
            "unsupported_settlement_spec"
            if not reasons
            else f"unsupported_settlement_spec:{','.join(sorted(set(reasons)))}"
        )
    elif model_join_status and model_join_status != "joined":
        blockers.append(f"model_not_joined:{model_join_status}")

    settlement_spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else None
    settlement_status = str(row.get("settlement_spec_status") or (settlement_spec or {}).get("status") or "").strip()
    if config.require_settlement_spec and market_family == "temperature":
        if not settlement_spec:
            blockers.append("missing_settlement_spec")
        elif settlement_status != "supported":
            reasons = [
                str(reason)
                for reason in (
                    row.get("settlement_spec_unsupported_reasons")
                    or settlement_spec.get("unsupported_reasons")
                    or []
                )
                if str(reason)
            ]
            blockers.append(
                "unsupported_settlement_spec"
                if not reasons
                else f"unsupported_settlement_spec:{','.join(sorted(set(reasons)))}"
            )
        else:
            required_spec_fields = (
                "station_code",
                "settlement_source",
                "target_date",
                "timezone",
                "metric",
                "unit",
                "bucket_type",
                "threshold",
                "rounding",
                "rule_hash",
                "end_time",
            )
            for field in required_spec_fields:
                value = settlement_spec.get(field)
                if value is None or str(value).strip() == "":
                    blockers.append(f"missing_settlement_spec_field:{field}")

    if edge_percent is None:
        blockers.append("missing_edge")
    elif edge_percent < config.min_edge_percent:
        blockers.append("edge_below_min")

    if config.require_ev_safe and market_family == "temperature":
        if ev_safe is None:
            blockers.append("missing_ev_safe")
        elif ev_safe <= config.min_ev_safe:
            blockers.append("ev_safe_below_min")

    if liquidity is None:
        blockers.append("missing_liquidity")
    elif liquidity < config.min_liquidity:
        blockers.append("liquidity_below_min")

    if spread is None:
        if config.require_order_book:
            blockers.append("missing_spread")
        else:
            warnings.append("missing_spread")
    elif spread > config.max_spread:
        blockers.append("spread_above_max")
    if bid_depth is None:
        if config.min_bid_depth_usdc_3c > 0:
            blockers.append("missing_bid_depth")
    elif bid_depth < config.min_bid_depth_usdc_3c:
        blockers.append("bid_depth_below_min")
    if ask_depth is None:
        if config.min_ask_depth_usdc_3c > 0:
            blockers.append("missing_ask_depth")
    elif ask_depth < config.min_ask_depth_usdc_3c:
        blockers.append("ask_depth_below_min")

    if model_probability is None:
        warnings.append("missing_model_probability")
    elif model_probability < config.min_model_probability:
        warnings.append("model_probability_too_low")

    metar_status = row.get("metar_status") if isinstance(row.get("metar_status"), dict) else {}
    if metar_status.get("stale_for_today") is True:
        warnings.append("stale_observation")
    if metar_status.get("available_for_today") is False:
        warnings.append("missing_same_day_observation")
    if row.get("order_book_error"):
        warnings.append("order_book_unavailable")
    if model_join_status and model_join_status != "joined":
        warnings.append(f"model_join_{model_join_status}")

    risk_rule_hits = risk_blockers_for_row(row, risk_rules or [], mode=risk_rule_mode)
    blockers.extend(risk_rule_hits)

    computed_score = _candidate_score(
        score=score,
        edge_percent=edge_percent,
        ev_safe=ev_safe,
        liquidity=liquidity,
        price=price,
        spread=spread,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
    )
    decision = "candidate" if not blockers else "reject"
    if decision == "candidate" and warnings:
        decision = "watch"

    return {
        "decision": decision,
        "score": round(computed_score, 6),
        "blockers": blockers,
        "warnings": warnings,
        "risk_rule_hits": risk_rule_hits,
        "row_id": row.get("id"),
        "market_family": market_family,
        "city": city or None,
        "event_title": row.get("event_title"),
        "question": row.get("question"),
        "market_id": row.get("market_id"),
        "market_slug": market_slug or None,
        "token_id": row.get("token_id"),
        "side": side or None,
        "outcome": row.get("outcome"),
        "bucket_label": bucket_label or None,
        "bucket_type": bucket_type,
        "strategy_id": strategy.strategy_id,
        "execution_style": strategy.execution_style,
        "why_now": strategy.why_now,
        "strategy_live_eligible": strategy.live_eligible,
        "counts_for_live_gate": strategy.counts_for_live_gate,
        "risk_caps": strategy.risk_caps,
        "price": price,
        "bid": _first_float(row, ("bid", "best_bid")),
        "ask": _first_float(row, ("ask", "best_ask")),
        "spread": spread,
        "liquidity": liquidity,
        "bid_depth_usdc_3c": bid_depth,
        "ask_depth_usdc_3c": ask_depth,
        "edge_percent": edge_percent,
        "p_lcb": p_lcb,
        "q_effective": q_effective,
        "cost": cost,
        "ev_safe": ev_safe,
        "model_probability": model_probability,
        "market_probability": _first_float(row, ("market_probability",)),
        "market_implied_yes_price": _first_float(row, ("market_implied_yes_price",)),
        "market_implied_side_price": _first_float(row, ("market_implied_side_price",)),
        "market_implied_de_vig_yes_probability": _first_float(row, ("market_implied_de_vig_yes_probability",)),
        "market_implied_de_vig_side_probability": _first_float(row, ("market_implied_de_vig_side_probability",)),
        "market_implied_cdf": _first_float(row, ("market_implied_cdf",)),
        "market_implied_cdf_raw": _first_float(row, ("market_implied_cdf_raw",)),
        "market_implied_bucket_family": row.get("market_implied_bucket_family"),
        "market_implied_de_vig_status": row.get("market_implied_de_vig_status"),
        "target_date": _settlement_text(row, settlement_spec, "target_date"),
        "end_time": _settlement_text(row, settlement_spec, "end_time", "endTime", "end_date", "endDate"),
        "end_date": _settlement_text(row, settlement_spec, "end_date", "endDate", "end_time", "endTime"),
        "source_final_score": score,
        "settlement_spec_status": settlement_status or None,
        "settlement_spec_unsupported_reasons": row.get("settlement_spec_unsupported_reasons"),
        "settlement_rule_hash": (settlement_spec or {}).get("rule_hash"),
        "settlement_rule_text": (settlement_spec or {}).get("rule_text"),
        "settlement_station_code": (settlement_spec or {}).get("station_code"),
        "settlement_station_label": (settlement_spec or {}).get("station_label"),
        "settlement_source": (settlement_spec or {}).get("settlement_source"),
        "settlement_timezone": (settlement_spec or {}).get("timezone"),
        "settlement_metric": (settlement_spec or {}).get("metric"),
        "settlement_unit": (settlement_spec or {}).get("unit"),
        "settlement_spec": settlement_spec,
    }


def _candidate_score(
    *,
    score: Optional[float],
    edge_percent: Optional[float],
    ev_safe: Optional[float],
    liquidity: Optional[float],
    price: Optional[float],
    spread: Optional[float],
    bid_depth: Optional[float],
    ask_depth: Optional[float],
) -> float:
    # Legacy raw edge and source final_score are kept on the row as diagnostics,
    # but ranking must be driven by executable, cost-adjusted EV.
    ev_component = float(ev_safe) * 1000.0 if ev_safe is not None else -1000.0
    depth_component = min(
        40.0,
        max(0.0, float(bid_depth or 0.0)) / 25.0
        + max(0.0, float(ask_depth or 0.0)) / 25.0,
    )
    liquidity_component = min(10.0, max(0.0, float(liquidity or 0.0)) / 1000.0)
    price_penalty = abs(float(price or 0.0) - 0.35) * 10.0 if price is not None else 5.0
    spread_penalty = float(spread or 0.0) * 500.0 if spread is not None else 10.0
    return ev_component + depth_component + liquidity_component - price_penalty - spread_penalty


def _candidate_rank_key(item: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
    ev_safe = _safe_float(item.get("ev_safe"))
    ask_depth = _safe_float(item.get("ask_depth_usdc_3c"))
    bid_depth = _safe_float(item.get("bid_depth_usdc_3c"))
    spread = _safe_float(item.get("spread"))
    liquidity = _safe_float(item.get("liquidity"))
    return (
        float(ev_safe) if ev_safe is not None else -999.0,
        float(ask_depth) if ask_depth is not None else -1.0,
        float(bid_depth) if bid_depth is not None else -1.0,
        -(float(spread) if spread is not None else 999.0),
        float(liquidity) if liquidity is not None else -1.0,
    )


def _reason_counts(items: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for item in items:
        for reason in item.get(key) or []:
            text = str(reason or "").strip()
            if not text:
                continue
            counts[text] = counts.get(text, 0) + 1
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _non_risk_blockers(item: Dict[str, Any]) -> List[str]:
    risk_hits = {str(reason) for reason in item.get("risk_rule_hits") or []}
    return [
        str(reason)
        for reason in item.get("blockers") or []
        if str(reason) not in risk_hits
    ]


def _count_items(values: Iterable[str], *, key_name: str) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    return [
        {key_name: value, "count": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _count_by_field(
    rows: Iterable[Dict[str, Any]],
    field: str,
    *,
    key_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return _count_items(
        (str(row.get(field) or "unknown") for row in rows if isinstance(row, dict)),
        key_name=key_name or field,
    )


def _decision_by_market_family(assessments: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matrix: Dict[str, Dict[str, int]] = {}
    for item in assessments:
        family = str(item.get("market_family") or "unknown")
        decision = str(item.get("decision") or "unknown")
        row = matrix.setdefault(
            family,
            {
                "market_family": family,
                "row_count": 0,
                "candidate_count": 0,
                "watch_count": 0,
                "reject_count": 0,
                "quarantine_count": 0,
            },
        )
        row["row_count"] += 1
        if decision == "candidate":
            row["candidate_count"] += 1
        elif decision == "watch":
            row["watch_count"] += 1
        elif decision == "reject":
            row["reject_count"] += 1
            if item.get("risk_rule_hits") and not _non_risk_blockers(item):
                row["quarantine_count"] += 1
    return sorted(matrix.values(), key=lambda row: (-int(row["row_count"]), row["market_family"]))


def _decision_by_strategy(assessments: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matrix: Dict[str, Dict[str, int]] = {}
    for item in assessments:
        strategy_id = str(item.get("strategy_id") or "unknown")
        decision = str(item.get("decision") or "unknown")
        row = matrix.setdefault(
            strategy_id,
            {
                "strategy_id": strategy_id,
                "row_count": 0,
                "candidate_count": 0,
                "watch_count": 0,
                "reject_count": 0,
                "live_eligible_count": 0,
            },
        )
        row["row_count"] += 1
        if item.get("strategy_live_eligible") is True:
            row["live_eligible_count"] += 1
        if decision == "candidate":
            row["candidate_count"] += 1
        elif decision == "watch":
            row["watch_count"] += 1
        elif decision == "reject":
            row["reject_count"] += 1
    return sorted(matrix.values(), key=lambda row: (-int(row["row_count"]), row["strategy_id"]))


def _blocker_category(reason: str) -> str:
    text = str(reason or "")
    if text.startswith("negative_markout_rule:maker_quote_"):
        return "maker_quote_rule"
    if text.startswith("negative_markout_rule:"):
        return "markout_rule"
    if text in {"edge_below_min", "missing_edge", "missing_ev_safe", "ev_safe_below_min"}:
        return "edge"
    if text in {"spread_above_max", "missing_spread"}:
        return "spread"
    if text in {"price_below_min", "price_above_max", "missing_market_price"}:
        return "price"
    if text in {"liquidity_below_min", "missing_liquidity"}:
        return "liquidity"
    if text in {
        "missing_bid_depth",
        "missing_ask_depth",
        "bid_depth_below_min",
        "ask_depth_below_min",
    }:
        return "depth"
    if text == "side_not_allowed":
        return "side"
    if text.startswith("bucket_type_excluded:"):
        return "bucket_type"
    if text.startswith("unsupported_model_family:") or text.startswith("model_not_joined:"):
        return "model_coverage"
    if text in {
        "market_inactive",
        "market_closed",
        "row_not_tradable",
        "not_accepting_orders",
        "missing_market_id",
    }:
        return "tradability"
    return "other"


def _risk_rule_group(reason: str) -> str:
    text = str(reason or "").strip()
    prefix = "negative_markout_rule:"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    return text.split(":", 1)[0]


def _risk_rule_scope(reason: str) -> str:
    text = str(reason or "").strip()
    if not text.startswith("negative_markout_rule:"):
        return "non_risk"
    group = _risk_rule_group(text)
    dimensions = text.split(":", 2)[2] if text.count(":") >= 2 else ""
    if group in {
        "by_market_family",
        "by_quarantine_blocker_scope",
        "by_quarantine_reason",
        "maker_quote_by_market_family",
        "maker_quote_by_quarantine_blocker_scope",
        "maker_quote_by_quarantine_reason",
        "maker_quote_by_quote_strategy",
    }:
        return "broad"
    if "_and_" in group or "," in dimensions:
        return "specific"
    return "medium"


def _risk_rule_scope_counts(reasons: Iterable[str]) -> List[Dict[str, Any]]:
    return _count_items(
        [
            _risk_rule_scope(str(reason))
            for reason in reasons
            if str(reason).startswith("negative_markout_rule:")
        ],
        key_name="scope",
    )


def _blocker_combo_counts(
    rows: Iterable[Dict[str, Any]],
    *,
    key: Optional[str] = None,
    reason_getter: Optional[Any] = None,
    max_rows: int = 15,
) -> List[Dict[str, Any]]:
    counts: Dict[Tuple[str, ...], int] = {}
    for item in rows:
        raw_reasons = reason_getter(item) if reason_getter is not None else item.get(key or "")
        blockers = tuple(sorted({str(reason) for reason in raw_reasons or [] if str(reason)}))
        if not blockers:
            continue
        counts[blockers] = counts.get(blockers, 0) + 1
    return [
        {"blockers": list(blockers), "count": count}
        for blockers, count in sorted(
            counts.items(),
            key=lambda pair: (-pair[1], len(pair[0]), pair[0]),
        )[: max(0, int(max_rows))]
    ]


def _candidate_gap_item(item: Dict[str, Any]) -> Dict[str, Any]:
    risk_hits = [str(reason) for reason in item.get("risk_rule_hits") or [] if str(reason)]
    non_risk = _non_risk_blockers(item)
    sample = _sample_for_diagnostics(item)
    sample["non_risk_blockers"] = non_risk
    sample["risk_rule_hits"] = risk_hits
    sample["risk_rule_scope_counts"] = _risk_rule_scope_counts(risk_hits)
    sample["would_be_decision_without_risk_rules"] = (
        "watch" if item.get("warnings") else "candidate"
    )
    return sample


def _non_risk_blocker_categories(blockers: Iterable[str]) -> List[str]:
    return [
        _blocker_category(str(reason))
        for reason in blockers
        if str(reason or "").strip()
    ]


def build_candidate_gap_report(
    assessments: Iterable[Dict[str, Any]],
    *,
    max_near_candidates: int = 10,
) -> Dict[str, Any]:
    """Explain why a scan produced no strict candidates without changing decisions."""

    rows = [item for item in assessments if isinstance(item, dict)]
    rejected = [item for item in rows if item.get("decision") == "reject"]
    risk_only = [
        item
        for item in rejected
        if item.get("risk_rule_hits") and not _non_risk_blockers(item)
    ]
    one_non_risk = [
        item
        for item in rejected
        if len(set(_non_risk_blockers(item))) == 1
    ]
    zero_or_one_non_risk = [
        item
        for item in rejected
        if len(set(_non_risk_blockers(item))) <= 1
    ]

    non_risk_reasons = [
        reason
        for item in rejected
        for reason in _non_risk_blockers(item)
    ]
    risk_reasons = [
        str(reason)
        for item in rejected
        for reason in item.get("risk_rule_hits") or []
        if str(reason)
    ]
    risk_only_reasons = [
        str(reason)
        for item in risk_only
        for reason in item.get("risk_rule_hits") or []
        if str(reason)
    ]
    non_risk_categories = [_blocker_category(reason) for reason in non_risk_reasons]

    near_candidates = sorted(
        zero_or_one_non_risk,
        key=lambda item: (
            len(set(_non_risk_blockers(item))),
            len(set(item.get("risk_rule_hits") or [])),
            -float(item.get("score") or 0.0),
        ),
    )[: max(0, int(max_near_candidates))]

    return {
        "total_rejected": len(rejected),
        "risk_only_reject_count": len(risk_only),
        "risk_only_candidate_without_risk_count": len(
            [item for item in risk_only if not item.get("warnings")]
        ),
        "risk_only_watch_without_risk_count": len(
            [item for item in risk_only if item.get("warnings")]
        ),
        "single_non_risk_blocker_count": len(one_non_risk),
        "zero_or_one_non_risk_blocker_count": len(zero_or_one_non_risk),
        "edge_only_reject_count": len(
            [
                item
                for item in one_non_risk
                if _blocker_category(_non_risk_blockers(item)[0]) == "edge"
            ]
        ),
        "spread_only_reject_count": len(
            [
                item
                for item in one_non_risk
                if _blocker_category(_non_risk_blockers(item)[0]) == "spread"
            ]
        ),
        "price_only_reject_count": len(
            [
                item
                for item in one_non_risk
                if _blocker_category(_non_risk_blockers(item)[0]) == "price"
            ]
        ),
        "maker_rule_only_reject_count": len(
            [
                item
                for item in risk_only
                if item.get("risk_rule_hits")
                and all("maker_quote_" in str(reason) for reason in item.get("risk_rule_hits") or [])
            ]
        ),
        "risk_only_with_maker_rule_count": len(
            [
                item
                for item in risk_only
                if any("maker_quote_" in str(reason) for reason in item.get("risk_rule_hits") or [])
            ]
        ),
        "non_risk_blocker_counts": _count_items(non_risk_reasons, key_name="reason"),
        "non_risk_blocker_category_counts": _count_items(
            non_risk_categories,
            key_name="category",
        ),
        "risk_rule_hit_counts": _count_items(risk_reasons, key_name="reason"),
        "risk_only_rule_hit_counts": _count_items(risk_only_reasons, key_name="reason"),
        "risk_rule_scope_counts": _risk_rule_scope_counts(risk_reasons),
        "risk_only_rule_scope_counts": _risk_rule_scope_counts(risk_only_reasons),
        "top_blocker_combinations": _blocker_combo_counts(
            rejected,
            key="blockers",
        ),
        "top_non_risk_blocker_combinations": _blocker_combo_counts(
            rejected,
            reason_getter=_non_risk_blockers,
        ),
        "top_risk_only_rule_combinations": _blocker_combo_counts(
            risk_only,
            key="risk_rule_hits",
        ),
        "near_candidates": [_candidate_gap_item(item) for item in near_candidates],
    }


def build_strict_gate_diagnostics(
    assessments: Iterable[Dict[str, Any]],
    *,
    max_samples: int = 10,
) -> Dict[str, Any]:
    """Summarize why live-eligible rows failed strict candidate gates.

    This intentionally excludes calibration-only strategies such as
    ``eq_exact_shadow`` so exact buckets do not hide actionable gate failures in
    threshold/near-lock rows.
    """

    rows = [item for item in assessments if isinstance(item, dict)]
    live_rows = [
        item
        for item in rows
        if item.get("strategy_live_eligible") is True
        and item.get("counts_for_live_gate") is not False
    ]
    paper_only_rows = [item for item in rows if item not in live_rows]
    live_rejected = [item for item in live_rows if item.get("decision") == "reject"]
    live_candidates = [item for item in live_rows if item.get("decision") == "candidate"]
    live_watch = [item for item in live_rows if item.get("decision") == "watch"]
    non_risk_reasons = [
        reason
        for item in live_rejected
        for reason in _non_risk_blockers(item)
    ]
    non_risk_categories = [_blocker_category(reason) for reason in non_risk_reasons]
    risk_reasons = [
        str(reason)
        for item in live_rejected
        for reason in item.get("risk_rule_hits") or []
        if str(reason)
    ]
    by_strategy: Dict[str, Dict[str, Any]] = {}
    for item in live_rows:
        strategy_id = str(item.get("strategy_id") or "unknown")
        row = by_strategy.setdefault(
            strategy_id,
            {
                "strategy_id": strategy_id,
                "row_count": 0,
                "candidate_count": 0,
                "watch_count": 0,
                "reject_count": 0,
                "top_non_risk_categories": {},
            },
        )
        row["row_count"] += 1
        decision = str(item.get("decision") or "")
        if decision == "candidate":
            row["candidate_count"] += 1
        elif decision == "watch":
            row["watch_count"] += 1
        elif decision == "reject":
            row["reject_count"] += 1
            for reason in _non_risk_blockers(item):
                category = _blocker_category(reason)
                categories = row["top_non_risk_categories"]
                categories[category] = int(categories.get(category) or 0) + 1

    strategy_rows: List[Dict[str, Any]] = []
    for row in by_strategy.values():
        category_counts = [
            {"category": category, "count": count}
            for category, count in sorted(
                row.pop("top_non_risk_categories").items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        ]
        row["top_non_risk_categories"] = category_counts
        strategy_rows.append(row)

    sample_rows = sorted(
        live_rejected,
        key=lambda item: (
            len(set(_non_risk_blockers(item))),
            len(set(item.get("risk_rule_hits") or [])),
            -float(item.get("ev_safe") or -999.0),
            -float(item.get("score") or 0.0),
        ),
    )[: max(0, int(max_samples))]
    samples: List[Dict[str, Any]] = []
    for item in sample_rows:
        sample = _sample_for_diagnostics(item)
        sample["strategy_id"] = item.get("strategy_id")
        sample["execution_style"] = item.get("execution_style")
        sample["non_risk_blockers"] = _non_risk_blockers(item)
        sample["non_risk_blocker_categories"] = _non_risk_blocker_categories(
            sample["non_risk_blockers"]
        )
        sample["risk_rule_hits"] = item.get("risk_rule_hits") or []
        sample["risk_rule_scope_counts"] = _risk_rule_scope_counts(sample["risk_rule_hits"])
        sample["p_lcb"] = item.get("p_lcb")
        sample["q_effective"] = item.get("q_effective")
        sample["cost"] = item.get("cost")
        samples.append(sample)

    return {
        "schema_version": "polyweather_weather_strict_gate_diagnostics.v1",
        "total_rows": len(rows),
        "live_eligible_row_count": len(live_rows),
        "paper_only_row_count": len(paper_only_rows),
        "live_eligible_candidate_count": len(live_candidates),
        "live_eligible_watch_count": len(live_watch),
        "live_eligible_reject_count": len(live_rejected),
        "non_risk_blocker_counts": _count_items(non_risk_reasons, key_name="reason"),
        "non_risk_blocker_category_counts": _count_items(
            non_risk_categories,
            key_name="category",
        ),
        "risk_rule_hit_counts": _count_items(risk_reasons, key_name="reason"),
        "risk_rule_scope_counts": _risk_rule_scope_counts(risk_reasons),
        "by_strategy": sorted(
            strategy_rows,
            key=lambda row: (-int(row.get("row_count") or 0), str(row.get("strategy_id") or "")),
        ),
        "top_live_eligible_reject_samples": samples,
        "targeted_paper_queues": _strict_gate_targeted_paper_queues(live_rejected),
    }


def _strict_gate_queue_item(
    item: Dict[str, Any],
    *,
    queue_name: str,
    queue_reasons: Iterable[str],
) -> Dict[str, Any]:
    sample = _sample_for_diagnostics(item)
    non_risk = _non_risk_blockers(item)
    risk_hits = [str(reason) for reason in item.get("risk_rule_hits") or [] if str(reason)]
    sample.update(
        {
            "queue_name": queue_name,
            "queue_reasons": sorted({str(reason) for reason in queue_reasons if str(reason)}),
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_gate_excluded": True,
            "strategy_id": item.get("strategy_id"),
            "execution_style": item.get("execution_style"),
            "non_risk_blockers": non_risk,
            "non_risk_blocker_categories": _non_risk_blocker_categories(non_risk),
            "risk_rule_hits": risk_hits,
            "risk_rule_scope_counts": _risk_rule_scope_counts(risk_hits),
            "p_lcb": item.get("p_lcb"),
            "q_effective": item.get("q_effective"),
            "cost": item.get("cost"),
            "ev_safe": item.get("ev_safe"),
        }
    )
    return sample


def _strict_gate_targeted_paper_queues(
    live_rejected: Iterable[Dict[str, Any]],
    *,
    max_items_per_queue: int = 10,
) -> Dict[str, Any]:
    rows = [item for item in live_rejected if isinstance(item, dict)]
    queue_defs = {
        "ev_calibration": {
            "description": "live-eligible rows rejected by model edge / ev_safe gates; use for probability calibration, not trading",
            "categories": {"edge"},
        },
        "execution_depth_price": {
            "description": "live-eligible rows rejected by executable price, spread, depth, or liquidity gates; use for orderbook/execution diagnostics",
            "categories": {"price", "depth", "spread", "liquidity"},
        },
        "risk_rule_review": {
            "description": "live-eligible rows hit risk rules after strict gates; use to audit whether rules are overbroad, not to override them",
            "categories": set(),
        },
    }
    queues: Dict[str, Dict[str, Any]] = {}
    for queue_name, config in queue_defs.items():
        queues[queue_name] = {
            "queue_name": queue_name,
            "description": config["description"],
            "paper_only": True,
            "counts_for_live_gate": False,
            "row_count": 0,
            "items": [],
        }

    for item in rows:
        non_risk = _non_risk_blockers(item)
        categories = set(_non_risk_blocker_categories(non_risk))
        risk_hits = [str(reason) for reason in item.get("risk_rule_hits") or [] if str(reason)]
        memberships: List[Tuple[str, List[str]]] = []
        if categories & queue_defs["ev_calibration"]["categories"]:
            memberships.append(
                (
                    "ev_calibration",
                    [reason for reason in non_risk if _blocker_category(reason) == "edge"],
                )
            )
        if categories & queue_defs["execution_depth_price"]["categories"]:
            memberships.append(
                (
                    "execution_depth_price",
                    [
                        reason
                        for reason in non_risk
                        if _blocker_category(reason) in queue_defs["execution_depth_price"]["categories"]
                    ],
                )
            )
        if risk_hits:
            memberships.append(("risk_rule_review", risk_hits))

        for queue_name, reasons in memberships:
            queue = queues[queue_name]
            queue["row_count"] += 1
            queue["items"].append(
                _strict_gate_queue_item(
                    item,
                    queue_name=queue_name,
                    queue_reasons=reasons,
                )
            )

    for queue in queues.values():
        queue["items"] = sorted(
            queue["items"],
            key=lambda item: (
                -float(item.get("ev_safe") or -999.0),
                -float(item.get("edge_percent") or 0.0),
                float(item.get("spread") or 999.0),
            ),
        )[: max(0, int(max_items_per_queue))]
    return queues


def _risk_filter_diagnostics(assessments: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    rows = [item for item in assessments if isinstance(item, dict)]
    risk_rejected = [
        item
        for item in rows
        if item.get("decision") == "reject" and item.get("risk_rule_hits")
    ]
    risk_only = [
        item
        for item in risk_rejected
        if not _non_risk_blockers(item)
    ]
    return {
        "risk_rule_reject_count": len(risk_rejected),
        "risk_only_reject_count": len(risk_only),
        "would_be_candidate_without_risk_rule_count": len(
            [item for item in risk_only if not item.get("warnings")]
        ),
        "would_be_watch_without_risk_rule_count": len(
            [item for item in risk_only if item.get("warnings")]
        ),
    }


def _risk_rule_saturation(
    assessments: Iterable[Dict[str, Any]],
    *,
    min_coverage: float = 0.8,
) -> List[Dict[str, Any]]:
    rows = [item for item in assessments if isinstance(item, dict)]
    total = len(rows)
    if total <= 0:
        return []
    counts: Dict[str, int] = {}
    for item in rows:
        for reason in set(str(reason) for reason in item.get("risk_rule_hits") or [] if str(reason)):
            counts[reason] = counts.get(reason, 0) + 1
    saturated: List[Dict[str, Any]] = []
    for reason, count in counts.items():
        coverage = count / total
        if coverage < float(min_coverage):
            continue
        saturated.append(
            {
                "reason": reason,
                "count": count,
                "total_rows": total,
                "coverage": round(coverage, 6),
                "scope": _risk_rule_scope(reason),
            }
        )
    return sorted(
        saturated,
        key=lambda row: (
            -float(row.get("coverage") or 0.0),
            str(row.get("scope") or ""),
            str(row.get("reason") or ""),
        ),
    )


def _risk_rule_group_saturation(
    assessments: Iterable[Dict[str, Any]],
    *,
    min_coverage: float = 0.8,
) -> List[Dict[str, Any]]:
    rows = [item for item in assessments if isinstance(item, dict)]
    total = len(rows)
    if total <= 0:
        return []
    group_hits: Dict[str, set[int]] = {}
    group_reasons: Dict[str, set[str]] = {}
    group_scopes: Dict[str, set[str]] = {}
    for index, item in enumerate(rows):
        for reason in set(str(reason) for reason in item.get("risk_rule_hits") or [] if str(reason)):
            group = _risk_rule_group(reason)
            group_hits.setdefault(group, set()).add(index)
            group_reasons.setdefault(group, set()).add(reason)
            group_scopes.setdefault(group, set()).add(_risk_rule_scope(reason))
    saturated: List[Dict[str, Any]] = []
    for group, indexes in group_hits.items():
        coverage = len(indexes) / total
        if coverage < float(min_coverage):
            continue
        scopes = sorted(group_scopes.get(group) or [])
        scope = "specific" if "specific" in scopes else ("broad" if "broad" in scopes else "medium")
        saturated.append(
            {
                "group": group,
                "count": len(indexes),
                "total_rows": total,
                "coverage": round(coverage, 6),
                "scope": scope,
                "reason_count": len(group_reasons.get(group) or []),
                "sample_reasons": sorted(group_reasons.get(group) or [])[:5],
            }
        )
    return sorted(
        saturated,
        key=lambda row: (
            -float(row.get("coverage") or 0.0),
            str(row.get("scope") or ""),
            str(row.get("group") or ""),
        ),
    )


def _risk_rule_reason_from_rule(rule: Dict[str, Any]) -> str:
    group = str(rule.get("group") or "markout").strip() or "markout"
    dimensions = rule.get("dimensions") if isinstance(rule.get("dimensions"), dict) else {}
    dimension_text = ",".join(f"{key}={value}" for key, value in sorted(dimensions.items()))
    return f"negative_markout_rule:{group}:{dimension_text}"


def _suppress_saturated_broad_rules(
    rules: Iterable[Dict[str, Any]],
    saturated_rules: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    saturated_reasons = {
        str(row.get("reason") or "")
        for row in saturated_rules
        if str(row.get("scope") or "") == "broad"
    }
    if not saturated_reasons:
        return list(rules), []
    kept: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for rule in rules:
        reason = _risk_rule_reason_from_rule(rule)
        if reason in saturated_reasons:
            suppressed.append({**rule, "saturation_reason": reason})
            continue
        kept.append(rule)
    return kept, suppressed


def _suppress_saturated_partition_rules(
    rules: Iterable[Dict[str, Any]],
    saturated_groups: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    suppress_by_group = {
        str(row.get("group") or "")
        for row in saturated_groups
        if str(row.get("scope") or "") in {"broad", "medium"}
        and int(row.get("reason_count") or 0) >= 2
    }
    if not suppress_by_group:
        return list(rules), []
    group_meta = {
        str(row.get("group") or ""): row
        for row in saturated_groups
        if str(row.get("group") or "")
    }
    kept: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for rule in rules:
        group = str(rule.get("group") or "markout").strip() or "markout"
        if group in suppress_by_group:
            meta = group_meta.get(group) or {}
            suppressed.append(
                {
                    **rule,
                    "saturation_group": group,
                    "saturation_group_coverage": meta.get("coverage"),
                    "saturation_group_reason_count": meta.get("reason_count"),
                }
            )
            continue
        kept.append(rule)
    return kept, suppressed


def _quarantine_item(
    item: Dict[str, Any],
    *,
    quarantine_reason: str = "risk_rule_only_reject",
    non_risk_blockers: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    non_risk = [str(reason) for reason in non_risk_blockers or [] if str(reason)]
    categories = _non_risk_blocker_categories(non_risk)
    would_be = "watch" if item.get("warnings") else "candidate"
    quarantined = dict(item)
    quarantined["decision"] = "quarantine"
    quarantined["original_decision"] = item.get("decision")
    quarantined["quarantine_reason"] = quarantine_reason
    quarantined["quarantine_blocker_scope"] = (
        "risk_rule_only"
        if quarantine_reason == "risk_rule_only_reject"
        else ("single_non_risk_plus_risk" if item.get("risk_rule_hits") else "single_non_risk_only")
    )
    quarantined["quarantine_non_risk_blockers"] = non_risk
    quarantined["quarantine_non_risk_blocker_categories"] = categories
    quarantined["would_be_decision_without_risk_rules"] = would_be
    quarantined["would_be_decision_without_quarantine_blockers"] = would_be
    quarantined["paper_only"] = True
    quarantined["live_gate_excluded"] = True
    quarantined["counts_for_live_gate"] = False
    return quarantined


def _quarantine_candidates(
    rejected: Iterable[Dict[str, Any]],
    *,
    near_miss_categories: Iterable[str],
) -> List[Dict[str, Any]]:
    allowed_categories = {
        str(category or "").strip().lower()
        for category in near_miss_categories
        if str(category or "").strip()
    }
    rows: List[Dict[str, Any]] = []
    seen: set[Tuple[Any, Any, Any]] = set()
    for item in rejected:
        non_risk = sorted(set(_non_risk_blockers(item)))
        key = (item.get("market_slug"), item.get("token_id"), item.get("side"))
        if item.get("risk_rule_hits") and not non_risk:
            rows.append(_quarantine_item(item))
            seen.add(key)
            continue
        if key in seen or not allowed_categories or len(non_risk) != 1:
            continue
        category = _blocker_category(non_risk[0])
        if category not in allowed_categories:
            continue
        rows.append(
            _quarantine_item(
                item,
                quarantine_reason=f"single_non_risk_blocker:{category}",
                non_risk_blockers=non_risk,
            )
        )
        seen.add(key)
    return rows


def _sample_for_diagnostics(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "decision": item.get("decision"),
        "city": item.get("city"),
        "question": item.get("question"),
        "market_id": item.get("market_id"),
        "market_slug": item.get("market_slug"),
        "token_id": item.get("token_id"),
        "side": item.get("side"),
        "outcome": item.get("outcome"),
        "bucket_label": item.get("bucket_label"),
        "bucket_type": item.get("bucket_type"),
        "market_family": item.get("market_family"),
        "price": item.get("price"),
        "bid": item.get("bid"),
        "ask": item.get("ask"),
        "spread": item.get("spread"),
        "liquidity": item.get("liquidity"),
        "bid_depth_usdc_3c": item.get("bid_depth_usdc_3c"),
        "ask_depth_usdc_3c": item.get("ask_depth_usdc_3c"),
        "edge_percent": item.get("edge_percent"),
        "model_probability": item.get("model_probability"),
        "market_probability": item.get("market_probability"),
        "ev_safe": item.get("ev_safe"),
        "strategy_id": item.get("strategy_id"),
        "execution_style": item.get("execution_style"),
        "strategy_live_eligible": item.get("strategy_live_eligible"),
        "settlement_rule_hash": item.get("settlement_rule_hash"),
        "settlement_station_code": item.get("settlement_station_code"),
        "settlement_source": item.get("settlement_source"),
        "score": item.get("score"),
    }


def _reason_samples(
    items: Iterable[Dict[str, Any]],
    key: str,
    *,
    max_per_reason: int = 5,
) -> List[Dict[str, Any]]:
    samples: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        for reason in item.get(key) or []:
            text = str(reason or "").strip()
            if not text:
                continue
            bucket = samples.setdefault(text, [])
            if len(bucket) < max(0, int(max_per_reason)):
                bucket.append(_sample_for_diagnostics(item))
    return [
        {"reason": reason, "samples": rows}
        for reason, rows in sorted(samples.items(), key=lambda pair: pair[0])
    ]


def build_weather_market_signal_report(
    payload: Dict[str, Any],
    *,
    config: WeatherMarketSignalConfig = WeatherMarketSignalConfig(),
    risk_rules: Optional[Iterable[Dict[str, Any]]] = None,
    risk_rule_mode: str = "live",
    generated_at: Optional[str] = None,
    include_assessments: bool = False,
) -> Dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload, dict) else []
    risk_rules_list = normalize_risk_rules(risk_rules or [])
    active_risk_rules = risk_rules_for_mode(risk_rules_list, mode=risk_rule_mode)
    payload_rows = [row for row in rows if isinstance(row, dict)]

    def assess_with_rules(rules_for_assessment: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            assess_weather_market_row(
                row,
                config=config,
                risk_rules=rules_for_assessment,
                risk_rule_mode="live",
            )
            for row in payload_rows
        ]

    initial_assessments = assess_with_rules(active_risk_rules)
    initial_saturation = _risk_rule_saturation(
        initial_assessments,
        min_coverage=float(config.saturated_risk_rule_min_coverage),
    )
    initial_group_saturation = _risk_rule_group_saturation(
        initial_assessments,
        min_coverage=float(config.partition_saturated_risk_rule_min_coverage),
    )
    suppressed_saturated_rules: List[Dict[str, Any]] = []
    if bool(config.suppress_saturated_broad_risk_rules):
        active_risk_rules, suppressed_saturated_rules = _suppress_saturated_broad_rules(
            active_risk_rules,
            initial_saturation,
        )
    assessments_after_broad_suppression = (
        assess_with_rules(active_risk_rules)
        if suppressed_saturated_rules
        else initial_assessments
    )
    partition_saturation = _risk_rule_group_saturation(
        assessments_after_broad_suppression,
        min_coverage=float(config.partition_saturated_risk_rule_min_coverage),
    )
    suppressed_saturated_group_rules: List[Dict[str, Any]] = []
    if bool(config.suppress_saturated_partition_risk_rules):
        active_risk_rules, suppressed_saturated_group_rules = _suppress_saturated_partition_rules(
            active_risk_rules,
            partition_saturation,
        )
    assessments = (
        assess_with_rules(active_risk_rules)
        if suppressed_saturated_group_rules
        else assessments_after_broad_suppression
    )
    candidates = [item for item in assessments if item["decision"] == "candidate"]
    watch = [item for item in assessments if item["decision"] == "watch"]
    rejected = [item for item in assessments if item["decision"] == "reject"]
    quarantine = _quarantine_candidates(
        rejected,
        near_miss_categories=config.quarantine_near_miss_categories,
    )
    ranked_candidates = sorted(
        candidates,
        key=_candidate_rank_key,
        reverse=True,
    )[: max(0, int(config.max_candidates))]
    ranked_watch = sorted(
        watch,
        key=_candidate_rank_key,
        reverse=True,
    )[: max(0, int(config.max_candidates))]
    ranked_quarantine = sorted(
        quarantine,
        key=_candidate_rank_key,
        reverse=True,
    )[: max(0, int(config.max_quarantine))]
    risk_diagnostics = _risk_filter_diagnostics(assessments)
    risk_rule_saturation = _risk_rule_saturation(
        assessments,
        min_coverage=float(config.saturated_risk_rule_min_coverage),
    )
    risk_rule_group_saturation = _risk_rule_group_saturation(
        assessments,
        min_coverage=float(config.partition_saturated_risk_rule_min_coverage),
    )
    candidate_gap_report = build_candidate_gap_report(assessments)
    strict_gate_diagnostics = build_strict_gate_diagnostics(assessments)
    coverage_diagnostics = {
        "market_family_counts": _count_by_field(assessments, "market_family", key_name="market_family"),
        "quarantine_by_market_family": _count_by_field(
            quarantine,
            "market_family",
            key_name="market_family",
        ),
        "quarantine_by_reason": _count_by_field(
            quarantine,
            "quarantine_reason",
            key_name="quarantine_reason",
        ),
        "quarantine_by_blocker_scope": _count_by_field(
            quarantine,
            "quarantine_blocker_scope",
            key_name="quarantine_blocker_scope",
        ),
        "model_join_status_counts": _count_by_field(
            payload_rows,
            "model_join_status",
            key_name="model_join_status",
        ),
        "unsupported_market_family_counts": _count_by_field(
            [
                row
                for row in payload_rows
                if str(row.get("model_join_status") or "") == "unsupported_market_type"
            ],
            "market_family",
            key_name="market_family",
        ),
        "decision_by_market_family": _decision_by_market_family(assessments),
        "decision_by_strategy": _decision_by_strategy(assessments),
    }

    live_blockers = [
        "paper_evidence_missing",
        "resolved_audit_missing",
        "order_execution_not_configured",
    ]
    if not ranked_candidates:
        live_blockers.insert(0, "no_strict_candidate")

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_snapshot_id": payload.get("snapshot_id"),
        "source_status": payload.get("status"),
        "source": payload.get("source"),
        "source_diagnostics": payload.get("diagnostics"),
        "config": asdict(config),
        "risk_filter": {
            "enabled": bool(active_risk_rules),
            "mode": risk_rule_mode,
            "rule_count": len(active_risk_rules),
            "source_rule_count": len(risk_rules_list),
            "initial_rule_count": len(risk_rules_for_mode(risk_rules_list, mode=risk_rule_mode)),
            "hit_count": len([item for item in assessments if item.get("risk_rule_hits")]),
            "saturation_min_coverage": float(config.saturated_risk_rule_min_coverage),
            "partition_saturation_min_coverage": float(config.partition_saturated_risk_rule_min_coverage),
            "saturated_broad_suppression_enabled": bool(config.suppress_saturated_broad_risk_rules),
            "saturated_partition_suppression_enabled": bool(config.suppress_saturated_partition_risk_rules),
            "pre_suppression_saturated_rule_count": len(initial_saturation),
            "pre_suppression_saturated_rules": initial_saturation,
            "pre_suppression_saturated_group_count": len(initial_group_saturation),
            "pre_suppression_saturated_groups": initial_group_saturation,
            "suppressed_saturated_rule_count": len(suppressed_saturated_rules),
            "suppressed_saturated_rules": suppressed_saturated_rules,
            "suppressed_saturated_group_rule_count": len(suppressed_saturated_group_rules),
            "suppressed_saturated_group_rules": suppressed_saturated_group_rules,
            "saturated_rule_count": len(risk_rule_saturation),
            "saturated_rules": risk_rule_saturation,
            "saturated_group_count": len(risk_rule_group_saturation),
            "saturated_groups": risk_rule_group_saturation,
            **risk_diagnostics,
        },
        "coverage_diagnostics": coverage_diagnostics,
        "summary": {
            "total_rows": len(assessments),
            "candidate_count": len(candidates),
            "watch_count": len(watch),
            "reject_count": len(rejected),
            "quarantine_count": len(quarantine),
            "quarantine_risk_only_count": len(
                [
                    item
                    for item in quarantine
                    if item.get("quarantine_reason") == "risk_rule_only_reject"
                ]
            ),
            "quarantine_near_miss_count": len(
                [
                    item
                    for item in quarantine
                    if str(item.get("quarantine_reason") or "").startswith("single_non_risk_blocker:")
                ]
            ),
            "quarantine_candidate_without_risk_count": len(
                [
                    item
                    for item in quarantine
                    if item.get("would_be_decision_without_risk_rules") == "candidate"
                ]
            ),
            "quarantine_watch_without_risk_count": len(
                [
                    item
                    for item in quarantine
                    if item.get("would_be_decision_without_risk_rules") == "watch"
                ]
            ),
            "top_candidate_score": (
                ranked_candidates[0]["score"] if ranked_candidates else None
            ),
            "live_gate": False,
            "live_authorization_pct": 0,
            "live_blockers": live_blockers,
        },
        "candidates": ranked_candidates,
        "watch": ranked_watch,
        "quarantine": ranked_quarantine,
        "rejection_reason_counts": _reason_counts(rejected, "blockers"),
        "warning_reason_counts": _reason_counts(assessments, "warnings"),
        "rejection_reason_samples": _reason_samples(rejected, "blockers"),
        "warning_reason_samples": _reason_samples(assessments, "warnings"),
        "candidate_gap_report": candidate_gap_report,
        "strict_gate_diagnostics": strict_gate_diagnostics,
    }
    if include_assessments:
        report["assessments"] = assessments
    return report
