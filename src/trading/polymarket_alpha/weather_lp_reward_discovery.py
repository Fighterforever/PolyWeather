from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl
from src.trading.polymarket_alpha.reward_programs import extract_liquidity_reward_metadata


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_reward_discovery.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _minute(value: Optional[str]) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.minute


def _reward_raw(row: Dict[str, Any]) -> Optional[float]:
    for key in ("reward_rate_raw", "reward_rate", "rewardRate", "rewardsDailyRate", "liquidityReward", "makerRewardRate"):
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    rewards = row.get("rewards")
    if isinstance(rewards, dict):
        for key in ("rate", "dailyRate", "score"):
            value = _safe_float(rewards.get(key))
            if value is not None:
                return value
    return None


def _metadata_by_slug(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("market_slug") or "").strip()
        if slug:
            output[slug] = row
    return output


def _flatten_family_catalog(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for family in payload.get("families") or []:
        if not isinstance(family, dict):
            continue
        for bucket in family.get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            rows.append({**bucket, "family_event_slug": family.get("event_slug"), "family_bucket_count": family.get("bucket_count")})
    return rows


def _row_from_bucket(bucket: Dict[str, Any], *, generated_at: str, reward_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    reward_metadata = reward_metadata or {}
    if reward_metadata:
        meta = {
            "feesEnabled": reward_metadata.get("feesEnabled"),
            "min_incentive_size": reward_metadata.get("min_incentive_size"),
            "max_incentive_spread": reward_metadata.get("max_incentive_spread"),
            "reward_allocation": reward_metadata.get("reward_allocation"),
            "reward_program_type": reward_metadata.get("reward_program_type"),
            "raw_field_names_found": reward_metadata.get("raw_field_names_found") or [],
            "source_found": reward_metadata.get("source_found"),
            "gap_reason": reward_metadata.get("gap_reason"),
        }
    else:
        meta = extract_liquidity_reward_metadata(bucket)
    reward = _reward_raw(bucket)
    minute = _minute(generated_at)
    ask = _safe_float(bucket.get("yes_best_ask"))
    bid = _safe_float(bucket.get("yes_best_bid"))
    spread = _safe_float(bucket.get("yes_spread"))
    reward_program_type = str(meta.get("reward_program_type") or "unknown")
    reward_available = reward_program_type == "liquidity_reward"
    gap = None if reward_available else str(meta.get("gap_reason") or "liquidity_reward_metadata_missing")
    return {
        "schema_version": f"{SCHEMA_VERSION}.market",
        "market_slug": bucket.get("market_slug"),
        "market_id": bucket.get("market_id"),
        "condition_id": bucket.get("condition_id") or bucket.get("conditionId"),
        "event_slug": bucket.get("event_slug") or bucket.get("family_event_slug"),
        "question": bucket.get("question") or bucket.get("title"),
        "city": bucket.get("city"),
        "station_code": bucket.get("station_code"),
        "target_date": bucket.get("target_date"),
        "bucket_type": bucket.get("bucket_type"),
        "threshold": _safe_float(bucket.get("threshold")),
        "token_id": bucket.get("yes_token_id") or bucket.get("token_id"),
        "yes_token_id": bucket.get("yes_token_id"),
        "no_token_id": bucket.get("no_token_id"),
        "best_bid": bid,
        "best_ask": ask,
        "yes_best_bid": bid,
        "yes_best_ask": ask,
        "no_best_bid": _safe_float(bucket.get("no_best_bid")),
        "no_best_ask": _safe_float(bucket.get("no_best_ask")),
        "spread": spread,
        "bid_depth": _safe_float(bucket.get("yes_bid_depth")),
        "ask_depth": _safe_float(bucket.get("yes_ask_depth")),
        "yes_bid_depth": _safe_float(bucket.get("yes_bid_depth")),
        "yes_ask_depth": _safe_float(bucket.get("yes_ask_depth")),
        "no_bid_depth": _safe_float(bucket.get("no_bid_depth")),
        "no_ask_depth": _safe_float(bucket.get("no_ask_depth")),
        "liquidity": _safe_float(bucket.get("liquidity")),
        "volume": _safe_float(bucket.get("volume")),
        "reward_available": bool(reward_available),
        "reward_metadata_available": bool(reward_available),
        "reward_program_type": reward_program_type,
        "feesEnabled": meta.get("feesEnabled"),
        "min_incentive_size": meta.get("min_incentive_size"),
        "max_incentive_spread": meta.get("max_incentive_spread"),
        "reward_allocation": meta.get("reward_allocation"),
        "reward_metadata_source": meta.get("source_found"),
        "reward_raw_field_names_found": meta.get("raw_field_names_found") or [],
        "reward_rate_raw": reward,
        "reward_score": round(float(reward), 8) if reward is not None else (meta.get("max_incentive_spread") if reward_available else None),
        "reward_window_detected": bool(reward_available and minute is not None),
        "minute_of_hour": minute,
        "data_source": "weather_bucket_family_catalog",
        "gap_reason": gap,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def build_weather_lp_reward_discovery(
    *,
    family_catalog: Dict[str, Any],
    reward_metadata_rows: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or _utc_now_iso()
    metadata = _metadata_by_slug(reward_metadata_rows)
    markets = [
        _row_from_bucket(row, generated_at=generated_at, reward_metadata=metadata.get(str(row.get("market_slug") or "")))
        for row in _flatten_family_catalog(family_catalog)
    ]
    gap_counts = Counter(str(row.get("gap_reason") or "none") for row in markets)
    reward_markets = [row for row in markets if row.get("reward_available")]
    minmax = [
        row
        for row in markets
        if row.get("min_incentive_size") is not None and row.get("max_incentive_spread") is not None
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "scope": "polymarket_weather_lp_rewards",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "market_count": len(markets),
        "reward_market_count": len(reward_markets),
        "reward_available_count": len(reward_markets),
        "reward_metadata_available_count": len(reward_markets),
        "reward_metadata_missing_count": len([row for row in markets if not row.get("reward_metadata_available")]),
        "min_incentive_size_found_count": len([row for row in markets if row.get("min_incentive_size") is not None]),
        "max_incentive_spread_found_count": len([row for row in markets if row.get("max_incentive_spread") is not None]),
        "min_max_incentive_found_count": len(minmax),
        "reward_program_type_counts": [
            {"reward_program_type": key, "count": value}
            for key, value in sorted(Counter(str(row.get("reward_program_type") or "unknown") for row in markets).items())
        ],
        "reward_by_city": [{"city": city, "count": count} for city, count in sorted(Counter(str(row.get("city") or "missing") for row in reward_markets).items())],
        "gap_counts": [{"reason": key, "count": value} for key, value in sorted(gap_counts.items())],
        "markets": markets,
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


__all__ = ["SCHEMA_VERSION", "build_weather_lp_reward_discovery", "load_json", "load_jsonl", "write_json", "write_jsonl"]
