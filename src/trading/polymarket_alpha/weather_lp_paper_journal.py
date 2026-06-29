from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl
from src.trading.polymarket_alpha.liquidity_reward_score import compute_liquidity_reward_score


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_paper_journal.v1"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _stable_id(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _quote_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(row.get("market_slug") or ""),
        str(row.get("token_id") or ""),
        str(row.get("strategy_variant") or row.get("strategy_id") or ""),
    )


def _market_lookup(markets: Iterable[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_token: Dict[str, Dict[str, Any]] = {}
    by_slug: Dict[str, Dict[str, Any]] = {}
    for row in markets:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("market_slug") or "")
        if slug:
            by_slug[slug] = row
        for key in ("token_id", "yes_token_id", "no_token_id"):
            token = str(row.get(key) or "")
            if token:
                by_token[token] = row
    return by_token, by_slug


def _current_book(row: Dict[str, Any]) -> Dict[str, Optional[float]]:
    bid = _safe_float(row.get("best_bid") or row.get("yes_best_bid"))
    ask = _safe_float(row.get("best_ask") or row.get("yes_best_ask"))
    midpoint = None
    spread = None
    if bid is not None and ask is not None:
        midpoint = round((bid + ask) / 2.0, 8)
        spread = round(max(0.0, ask - bid), 8)
    return {"best_bid": bid, "best_ask": ask, "midpoint": midpoint, "spread": spread}


def _latest_update_by_quote(updates: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in updates:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        when = _parse_utc(row.get("update_time") or row.get("generated_at"))
        if not quote_id or when is None:
            continue
        previous = latest.get(quote_id)
        previous_when = _parse_utc(previous.get("update_time") or previous.get("generated_at")) if previous else None
        if previous_when is None or when >= previous_when:
            latest[quote_id] = row
    return latest


def _sum_increment(updates: Iterable[Dict[str, Any]], quote_id: str) -> float:
    total = 0.0
    for row in updates:
        if isinstance(row, dict) and str(row.get("quote_id") or "") == quote_id:
            total += float(row.get("reward_points_increment_proxy") or row.get("reward_points_delta_proxy") or 0.0)
    return round(total, 8)


def build_weather_lp_quote_update_ledger(
    *,
    quotes: Iterable[Dict[str, Any]],
    reward_markets: Iterable[Dict[str, Any]] = (),
    existing_updates: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or _now()
    now_dt = _parse_utc(generated_at) or datetime.now(timezone.utc)
    quote_rows = [row for row in quotes if isinstance(row, dict)]
    existing = [row for row in existing_updates if isinstance(row, dict)]
    latest_by_quote = _latest_update_by_quote(existing)
    by_token, by_slug = _market_lookup(reward_markets)
    new_updates: List[Dict[str, Any]] = []

    for quote in quote_rows:
        quote_id = str(quote.get("quote_id") or "")
        if not quote_id:
            continue
        previous = latest_by_quote.get(quote_id)
        previous_time = _parse_utc(previous.get("update_time") or previous.get("generated_at")) if previous else None
        if previous_time is not None and previous_time >= now_dt:
            continue
        market = by_token.get(str(quote.get("token_id") or "")) or by_slug.get(str(quote.get("market_slug") or "")) or {}
        book = _current_book(market)
        quote_price = _safe_float(quote.get("quote_price"))
        quote_size = _safe_float(quote.get("quote_size") or quote.get("size"))
        midpoint = book.get("midpoint")
        score = compute_liquidity_reward_score(
            midpoint=midpoint if midpoint is not None else quote.get("midpoint"),
            order_price=quote_price,
            order_size=quote_size,
            side=str(quote.get("side") or "YES"),
            max_incentive_spread=quote.get("max_incentive_spread"),
            min_incentive_size=quote.get("min_incentive_size"),
        )
        start = _parse_utc(quote.get("quote_start_time")) or now_dt
        base_time = previous_time or start
        elapsed_since_previous = max(0.0, (now_dt - base_time).total_seconds())
        if previous is None and elapsed_since_previous <= 0:
            elapsed_since_previous = 300.0
        time_on_book = max(0.0, (now_dt - start).total_seconds())
        points_rate = float(score.get("normalized_score_proxy") or 0.0)
        points_increment = round(points_rate * (elapsed_since_previous / 300.0), 8) if score.get("qualifies_for_reward") else 0.0
        previous_cumulative = _sum_increment(existing, quote_id)
        cumulative = round(previous_cumulative + points_increment, 8)
        current_midpoint = _safe_float(score.get("midpoint"))
        price_markout = None
        if current_midpoint is not None and quote_price is not None:
            price_markout = round((current_midpoint - quote_price) * 100.0, 8)
        quote_touched = False
        if quote_price is not None and book.get("best_ask") is not None:
            quote_touched = bool(float(book["best_ask"]) <= quote_price)
        adverse = bool(price_markout is not None and price_markout < -1.0)
        blockers = list(score.get("blockers") or [])
        cancellation_recommended = False
        cancellation_reason = None
        if not score.get("qualifies_for_reward"):
            cancellation_recommended = True
            cancellation_reason = ",".join(blockers) or "reward_disqualified"
        elif now_dt.minute >= 58:
            cancellation_recommended = True
            cancellation_reason = "near_hour_boundary"
        update = {
            "schema_version": f"{SCHEMA_VERSION}.quote_update.v2",
            "quote_id": quote_id,
            "market_slug": quote.get("market_slug"),
            "token_id": quote.get("token_id"),
            "city": quote.get("city"),
            "station_code": quote.get("station_code"),
            "strategy_id": quote.get("strategy_id"),
            "strategy_variant": quote.get("strategy_variant"),
            "update_time": generated_at,
            "generated_at": generated_at,
            "minute_of_hour": now_dt.minute,
            "quote_price": quote_price,
            "quote_size": quote_size,
            "current_midpoint": current_midpoint,
            "current_best_bid": book.get("best_bid"),
            "current_best_ask": book.get("best_ask"),
            "current_spread": book.get("spread"),
            "max_incentive_spread": _safe_float(quote.get("max_incentive_spread")),
            "min_incentive_size": _safe_float(quote.get("min_incentive_size")),
            "spread_from_midpoint": score.get("spread_from_midpoint"),
            "qualifies_for_reward": bool(score.get("qualifies_for_reward")),
            "still_qualifies_for_reward": bool(score.get("qualifies_for_reward")),
            "reward_score_at_update": score,
            "q_one_proxy": score.get("q_one"),
            "q_two_proxy": score.get("q_two"),
            "q_min_proxy": score.get("q_min"),
            "reward_points_increment_proxy": points_increment,
            "reward_points_delta_proxy": points_increment,
            "cumulative_reward_points_proxy": cumulative,
            "time_on_book_seconds": round(time_on_book, 3),
            "price_markout_from_entry": price_markout,
            "price_markout_cents": price_markout,
            "quote_touched": quote_touched,
            "inferred_fill": quote_touched,
            "adverse_selection_flag": adverse,
            "adverse_selection": adverse,
            "cancellation_recommended": cancellation_recommended,
            "cancellation_reason": cancellation_reason,
            "non_qualification_reason": ",".join(blockers) if blockers else None,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        new_updates.append(update)

    all_updates = existing + new_updates
    latest = _latest_update_by_quote(all_updates)
    latest_rows = list(latest.values())
    cumulative_total = round(sum(float(row.get("cumulative_reward_points_proxy") or 0.0) for row in latest_rows), 8)
    markouts = [float(row.get("price_markout_from_entry")) for row in latest_rows if row.get("price_markout_from_entry") is not None]
    return {
        "schema_version": f"{SCHEMA_VERSION}.quote_update_report",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "active_quote_count": len(quote_rows),
        "quote_update_count": len(all_updates),
        "new_update_count": len(new_updates),
        "quote_with_update_count": len(latest_rows),
        "cumulative_reward_points_proxy": cumulative_total,
        "estimated_reward_cents_proxy": None,
        "estimated_reward_cents_proxy_gap_reason": "missing_reward_allocation_or_total_market_q_score",
        "mean_current_markout": round(sum(markouts) / len(markouts), 8) if markouts else None,
        "adverse_selection_count": len([row for row in latest_rows if row.get("adverse_selection_flag")]),
        "cancellation_recommended_count": len([row for row in latest_rows if row.get("cancellation_recommended")]),
        "non_qualification_reason_counts": [
            {"reason": key, "count": value}
            for key, value in sorted(Counter(str(row.get("non_qualification_reason") or "none") for row in latest_rows).items())
        ],
        "by_city": _group_update_rows(latest_rows, "city"),
        "by_strategy_variant": _group_update_rows(latest_rows, "strategy_variant"),
        "new_updates": new_updates,
        "quote_updates": all_updates,
    }


def _group_update_rows(rows: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(key) or "missing")].append(row)
    output: List[Dict[str, Any]] = []
    for bucket, bucket_rows in sorted(buckets.items()):
        markouts = [float(row.get("price_markout_from_entry")) for row in bucket_rows if row.get("price_markout_from_entry") is not None]
        output.append(
            {
                key: bucket,
                "quote_count": len(bucket_rows),
                "reward_points_proxy": round(sum(float(row.get("cumulative_reward_points_proxy") or 0.0) for row in bucket_rows), 8),
                "mean_current_markout": round(sum(markouts) / len(markouts), 8) if markouts else None,
                "adverse_selection_count": len([row for row in bucket_rows if row.get("adverse_selection_flag")]),
            }
        )
    return output


def build_weather_lp_paper_cycle(
    *,
    candidates: Iterable[Dict[str, Any]],
    existing_quotes: Iterable[Dict[str, Any]] = (),
    existing_updates: Iterable[Dict[str, Any]] = (),
    reward_markets: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or _now()
    quotes: List[Dict[str, Any]] = []
    fills: List[Dict[str, Any]] = []
    markouts: List[Dict[str, Any]] = []
    previous_by_key = {_quote_key(row): row for row in existing_quotes if isinstance(row, dict)}
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("decision") != "paper_quote":
            continue
        key = (
            str(candidate.get("market_slug") or ""),
            str(candidate.get("token_id") or ""),
            str(candidate.get("strategy_variant") or candidate.get("strategy_id") or ""),
        )
        previous = previous_by_key.get(key) or {}
        quote_id = str(previous.get("quote_id") or _stable_id({"market_slug": candidate.get("market_slug"), "token_id": candidate.get("token_id"), "strategy_variant": candidate.get("strategy_variant") or candidate.get("strategy_id")}))
        reward_points = float(candidate.get("reward_estimate") or 0.0)
        reward_allocation = candidate.get("reward_allocation")
        estimated_reward_cents = None
        if isinstance(reward_allocation, (int, float)) and reward_allocation > 0:
            estimated_reward_cents = reward_points * float(reward_allocation)
        quote = {
            "schema_version": f"{SCHEMA_VERSION}.quote",
            "quote_id": quote_id,
            "strategy_id": candidate.get("strategy_id"),
            "market_slug": candidate.get("market_slug"),
            "token_id": candidate.get("token_id"),
            "side": candidate.get("side"),
            "bucket_type": candidate.get("bucket_type"),
            "threshold": candidate.get("threshold"),
            "quote_price": candidate.get("quote_price"),
            "quote_size": candidate.get("quote_size") or candidate.get("min_incentive_size"),
            "size": candidate.get("quote_size") or candidate.get("min_incentive_size"),
            "midpoint": candidate.get("midpoint"),
            "spread_from_midpoint": candidate.get("spread_from_midpoint"),
            "max_incentive_spread": candidate.get("max_incentive_spread"),
            "min_incentive_size": candidate.get("min_incentive_size"),
            "reward_score_at_entry": candidate.get("reward_score_at_entry"),
            "q_min_proxy": (candidate.get("reward_score_at_entry") or {}).get("q_min") if isinstance(candidate.get("reward_score_at_entry"), dict) else None,
            "quote_start_time": previous.get("quote_start_time") or generated_at,
            "quote_end_time": None,
            "minute_of_hour": datetime.fromisoformat((previous.get("quote_start_time") or generated_at).replace("Z", "+00:00")).minute,
            "intended_reward_window": candidate.get("time_window"),
            "cancel_at_hour_boundary": True,
            "city": candidate.get("city"),
            "station_code": candidate.get("station_code"),
            "strategy_variant": candidate.get("strategy_variant"),
            "reward_score": candidate.get("reward_score"),
            "basket_cost": candidate.get("basket_total_cost"),
            "orderbook_snapshot_id": None,
            "reward_window_presence": bool(candidate.get("reward_score") is not None),
            "time_on_book_seconds": 0,
            "estimated_reward_points": reward_points,
            "estimated_reward_cents": estimated_reward_cents,
            "estimated_reward_cents_proxy_only": estimated_reward_cents is not None,
            "quote_touched": False,
            "inferred_fill": False,
            "estimated_reward_cents_separate_from_markout": True,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        quotes.append(quote)
    update_report = build_weather_lp_quote_update_ledger(
        quotes=quotes,
        reward_markets=reward_markets,
        existing_updates=existing_updates,
        generated_at=generated_at,
    )
    quote_updates = update_report.get("quote_updates") or []
    reward_cents_values = [row.get("estimated_reward_cents") for row in quotes if row.get("estimated_reward_cents") is not None]
    return {
        "schema_version": f"{SCHEMA_VERSION}.report",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "paper_quote_count": len(quotes),
        "active_quote_count": len(quotes),
        "inferred_fill_count": len(fills),
        "markout_count": len(markouts),
        "quote_update_count": update_report.get("quote_update_count", 0),
        "new_quote_update_count": update_report.get("new_update_count", 0),
        "estimated_reward_points": update_report.get("cumulative_reward_points_proxy") if quote_updates else round(sum(float(row.get("estimated_reward_points") or 0.0) for row in quotes), 8),
        "reward_points_proxy": update_report.get("cumulative_reward_points_proxy") if quote_updates else round(sum(float(row.get("estimated_reward_points") or 0.0) for row in quotes), 8),
        "cumulative_reward_points_proxy": update_report.get("cumulative_reward_points_proxy"),
        "estimated_reward_cents": round(sum(float(value) for value in reward_cents_values), 8) if reward_cents_values else None,
        "estimated_reward_cents_proxy": round(sum(float(value) for value in reward_cents_values), 8) if reward_cents_values else None,
        "adverse_selection_count": 0,
        "net_estimated_pnl_without_reward": 0.0 if quotes else None,
        "net_estimated_pnl_with_reward": round(sum(float(value) for value in reward_cents_values), 8) if reward_cents_values else None,
        "net_estimated_pnl_with_reward_proxy": round(sum(float(value) for value in reward_cents_values), 8) if reward_cents_values else None,
        "reward_is_guaranteed": False,
        "quotes": quotes,
        "quote_updates": quote_updates,
        "quote_update_report": {k: v for k, v in update_report.items() if k not in {"new_updates", "quote_updates"}},
        "fills": fills,
        "markouts": markouts,
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
    "build_weather_lp_paper_cycle",
    "build_weather_lp_quote_update_ledger",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
