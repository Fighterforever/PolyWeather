from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl
from src.trading.polymarket_alpha.weather_lp_paper_journal import stable_quote_key


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_measurement_cohort.v1"
HORIZONS_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "24h": 86400}
HORIZON_TOLERANCE_SECONDS = {"5m": 90, "15m": 180, "1h": 600, "6h": 1800, "24h": 7200}


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


def _updates_by_quote(updates: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in updates:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        if quote_id:
            buckets[quote_id].append(row)
    for rows in buckets.values():
        rows.sort(key=lambda row: _parse_utc(row.get("update_time") or row.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return buckets


def _latest_at_or_before(rows: List[Dict[str, Any]], when: datetime) -> Dict[str, Any]:
    selected: Dict[str, Any] = {}
    for row in rows:
        row_time = _parse_utc(row.get("update_time") or row.get("generated_at"))
        if row_time is not None and row_time <= when:
            selected = row
    return selected


def _active_quotes(quotes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row for row in quotes if isinstance(row, dict) and str(row.get("quote_status") or "active") == "active"]


def _targets(start: datetime) -> Dict[str, str]:
    return {f"target_{horizon}": _iso(start + timedelta(seconds=seconds)) for horizon, seconds in HORIZONS_SECONDS.items()}


def _cohort_key(quote: Dict[str, Any]) -> str:
    return str(quote.get("stable_quote_key") or stable_quote_key(quote))


def build_weather_lp_measurement_cohort_report(
    *,
    quotes: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]],
    existing_cohorts: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
    max_active_age_hours: float = 24.0,
) -> Dict[str, Any]:
    generated_at = generated_at or _now()
    now_dt = _parse_utc(generated_at) or datetime.now(timezone.utc)
    active_quotes = _active_quotes(quotes)
    updates_by_quote = _updates_by_quote(quote_updates)
    previous = [row for row in existing_cohorts if isinstance(row, dict)]
    cohorts: List[Dict[str, Any]] = []
    active_by_key: Dict[str, Dict[str, Any]] = {}
    active_quote_keys = {_cohort_key(row) for row in active_quotes}
    closed_count = 0

    for cohort in previous:
        status = str(cohort.get("status") or "active")
        key = str(cohort.get("stable_quote_key") or "")
        start = _parse_utc(cohort.get("cohort_start_time"))
        too_old = bool(start is not None and (now_dt - start).total_seconds() >= max_active_age_hours * 3600)
        row = dict(cohort)
        if status == "active" and key not in active_quote_keys:
            row["status"] = "expired"
            row["close_time"] = generated_at
            row["close_reason"] = "quote_no_longer_active"
            closed_count += 1
        elif status == "active" and too_old:
            row["status"] = "complete"
            row["close_time"] = generated_at
            row["close_reason"] = "cohort_24h_complete"
            closed_count += 1
        if row.get("status") == "active":
            active_by_key[key] = row
        cohorts.append(row)

    created: List[Dict[str, Any]] = []
    already_existing = 0
    for quote in active_quotes:
        key = _cohort_key(quote)
        if key in active_by_key:
            already_existing += 1
            continue
        quote_id = str(quote.get("quote_id") or "")
        latest = _latest_at_or_before(updates_by_quote.get(quote_id, []), now_dt)
        entry_mid = _safe_float(latest.get("current_midpoint") or quote.get("entry_midpoint") or quote.get("midpoint"))
        entry_bid = _safe_float(latest.get("current_best_bid") or quote.get("entry_best_bid"))
        entry_ask = _safe_float(latest.get("current_best_ask") or quote.get("entry_best_ask"))
        entry_spread = _safe_float(latest.get("current_spread") or quote.get("entry_spread"))
        cohort = {
            "schema_version": f"{SCHEMA_VERSION}.cohort",
            "cohort_id": _stable_id({"stable_quote_key": key, "cohort_start_time": generated_at}),
            "quote_id": quote_id,
            "stable_quote_key": key,
            "market_slug": quote.get("market_slug"),
            "token_id": quote.get("token_id"),
            "city": quote.get("city"),
            "station_code": quote.get("station_code"),
            "strategy_variant": quote.get("strategy_variant"),
            "side": quote.get("side"),
            "quote_price": quote.get("quote_price"),
            "quote_size": quote.get("quote_size") or quote.get("size"),
            "cohort_start_time": generated_at,
            "entry_midpoint_at_cohort_start": entry_mid,
            "entry_best_bid_at_cohort_start": entry_bid,
            "entry_best_ask_at_cohort_start": entry_ask,
            "entry_spread_at_cohort_start": entry_spread,
            "reward_score_at_cohort_start": latest.get("reward_score_at_update") or quote.get("reward_score_at_entry"),
            "cumulative_reward_points_at_start": latest.get("cumulative_reward_points_proxy"),
            **_targets(now_dt),
            "status": "active",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        cohorts.append(cohort)
        created.append(cohort)

    active_cohorts = [row for row in cohorts if row.get("status") == "active"]
    next_times: Dict[str, Optional[str]] = {}
    for horizon in HORIZONS_SECONDS:
        candidates = sorted(str(row.get(f"target_{horizon}") or "") for row in active_cohorts if row.get(f"target_{horizon}"))
        next_times[f"next_expected_{horizon}_markout_time"] = candidates[0] if candidates else None
    status_counts = Counter(str(row.get("status") or "missing") for row in cohorts)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "active_quote_count": len(active_quotes),
        "active_cohort_count": len(active_cohorts),
        "cohort_count": len(cohorts),
        "cohorts_created_count": len(created),
        "cohort_created_count": len(created),
        "cohorts_already_existing_count": already_existing,
        "cohorts_closed_count": closed_count,
        "status_counts": [{"status": key, "count": value} for key, value in sorted(status_counts.items())],
        **next_times,
        "cohorts": cohorts,
        "new_cohorts": created,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def build_weather_lp_cohort_markout_rows(
    *,
    cohorts: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    cohort_rows = [row for row in cohorts if isinstance(row, dict)]
    updates_by_quote = _updates_by_quote(quote_updates)
    rows: List[Dict[str, Any]] = []
    for cohort in cohort_rows:
        quote_id = str(cohort.get("quote_id") or "")
        history = updates_by_quote.get(quote_id, [])
        start = _parse_utc(cohort.get("cohort_start_time"))
        entry_mid = _safe_float(cohort.get("entry_midpoint_at_cohort_start"))
        quote_price = _safe_float(cohort.get("quote_price"))
        start_reward = _safe_float(cohort.get("cumulative_reward_points_at_start")) or 0.0
        latest = history[-1] if history else {}
        latest_time = _parse_utc(latest.get("update_time") or latest.get("generated_at")) if latest else None
        for horizon, seconds in {**HORIZONS_SECONDS, "current": 0}.items():
            target = start + timedelta(seconds=seconds) if start and horizon != "current" else None
            base = {
                "schema_version": f"{SCHEMA_VERSION}.markout_row",
                "cohort_id": cohort.get("cohort_id"),
                "quote_id": quote_id,
                "stable_quote_key": cohort.get("stable_quote_key"),
                "market_slug": cohort.get("market_slug"),
                "token_id": cohort.get("token_id"),
                "city": cohort.get("city"),
                "station_code": cohort.get("station_code"),
                "strategy_variant": cohort.get("strategy_variant"),
                "horizon": horizon,
                "cohort_start_time": _iso(start) if start else None,
                "target_time": _iso(target) if target else None,
                "matched_update_time": None,
                "actual_elapsed_seconds": None,
                "horizon_match_status": "missing_cohort_start_time" if start is None else "missing_update_for_horizon",
                "entry_midpoint_at_cohort_start": entry_mid,
                "matched_midpoint": None,
                "quote_price": quote_price,
                "current_best_bid": None,
                "current_best_ask": None,
                "markout_from_cohort_midpoint": None,
                "markout_from_quote_price": None,
                "markout_cents": None,
                "reward_points_increment_since_cohort_start": None,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
            if start is None:
                rows.append(base)
                continue
            selected: Dict[str, Any] = {}
            selected_time: Optional[datetime] = None
            if horizon == "current":
                selected = latest
                selected_time = latest_time
                base["horizon_match_status"] = "current_latest" if selected else "missing_update_for_horizon"
            else:
                assert target is not None
                if latest_time is None or latest_time < target - timedelta(seconds=HORIZON_TOLERANCE_SECONDS[horizon]):
                    base["horizon_match_status"] = "cohort_not_old_enough"
                else:
                    candidates: List[Tuple[float, Dict[str, Any], datetime]] = []
                    for update in history:
                        when = _parse_utc(update.get("update_time") or update.get("generated_at"))
                        if when is None or when < start:
                            continue
                        lag = abs((when - target).total_seconds())
                        if lag <= HORIZON_TOLERANCE_SECONDS[horizon]:
                            candidates.append((lag, update, when))
                    if candidates:
                        _, selected, selected_time = sorted(candidates, key=lambda item: item[0])[0]
                        base["horizon_match_status"] = "within_tolerance"
            if selected and selected_time is not None:
                matched_mid = _safe_float(selected.get("current_midpoint"))
                cumulative = _safe_float(selected.get("cumulative_reward_points_proxy"))
                base["matched_update_time"] = _iso(selected_time)
                base["actual_elapsed_seconds"] = round((selected_time - start).total_seconds(), 3)
                base["matched_midpoint"] = matched_mid
                base["current_best_bid"] = selected.get("current_best_bid")
                base["current_best_ask"] = selected.get("current_best_ask")
                base["reward_points_increment_since_cohort_start"] = round(float(cumulative or 0.0) - start_reward, 8) if cumulative is not None else None
                if matched_mid is not None and entry_mid is not None:
                    base["markout_from_cohort_midpoint"] = round((matched_mid - entry_mid) * 100.0, 8)
                if matched_mid is not None and quote_price is not None:
                    base["markout_from_quote_price"] = round((matched_mid - quote_price) * 100.0, 8)
                    base["markout_cents"] = base["markout_from_quote_price"]
            rows.append(base)
    return rows


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
    "HORIZONS_SECONDS",
    "HORIZON_TOLERANCE_SECONDS",
    "build_weather_lp_measurement_cohort_report",
    "build_weather_lp_cohort_markout_rows",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
