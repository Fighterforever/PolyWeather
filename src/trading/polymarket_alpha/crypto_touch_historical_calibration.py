from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.binance_crypto_history import fetch_binance_klines
from src.trading.polymarket_alpha.crypto_market_semantics import classify_crypto_semantics, resolve_market_creation_time
from src.trading.polymarket_alpha.crypto_probability_model import (
    first_passage_probability_upper,
    parse_crypto_threshold_market,
)
from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_crypto_touch_historical_calibration.v1"


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


def _log_loss(p: float, y: float) -> float:
    p = min(1 - 1e-9, max(1e-9, p))
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 8) if values else None


def _by_slug(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("market_slug") or "")
        if slug:
            grouped[slug].append(row)
    return grouped


def build_crypto_touch_calibration_report(
    *,
    closed_markets: Iterable[Dict[str, Any]],
    decision_snapshots: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
    annual_vols: Optional[Dict[str, float]] = None,
    cache_dir: str | Path = "evidence/polymarket_alpha/binance_klines",
    kline_fetcher: Optional[Any] = None,
    max_markets: int = 50,
) -> Dict[str, Any]:
    annual_vols = annual_vols or {"BTC": 0.55, "ETH": 0.70}
    snapshots_by_slug = _by_slug(decision_snapshots)
    rows: List[Dict[str, Any]] = []
    gaps: Counter[str] = Counter()
    no_lookahead_violation_count = 0
    market_count = 0
    for market in closed_markets:
        if not isinstance(market, dict):
            continue
        parsed = parse_crypto_threshold_market(market)
        if parsed is None or classify_crypto_semantics(market) != "touch_barrier":
            continue
        market_count += 1
        if max_markets and market_count > int(max_markets):
            break
        creation = resolve_market_creation_time(market, generated_at=generated_at, end_time=parsed.get("target_time") or market.get("end_time"))
        creation_time = creation.get("market_creation_time")
        if not creation_time:
            gaps[str(creation.get("gap_reason") or "missing_creation_time")] += 1
            continue
        end_dt = _parse_utc(parsed.get("target_time") or market.get("end_time"))
        if end_dt is None:
            gaps["missing_end_time"] += 1
            continue
        market_snapshots = snapshots_by_slug.get(str(market.get("market_slug") or "")) or []
        if not market_snapshots:
            gaps["missing_decision_snapshots"] += 1
            continue
        for snap in market_snapshots:
            if str(snap.get("outcome_label") or "").lower() not in {"yes", "y"}:
                continue
            decision_dt = _parse_utc(snap.get("decision_time") or snap.get("timestamp"))
            if decision_dt is None:
                gaps["missing_decision_time"] += 1
                continue
            if decision_dt >= end_dt:
                no_lookahead_violation_count += 1
                gaps["decision_time_after_market_end"] += 1
                continue
            resolved_payout = _safe_float(snap.get("resolved_payout"))
            if resolved_payout is None:
                gaps["missing_resolved_payout"] += 1
                continue
            price_mid = _safe_float(snap.get("price_mid"))
            if price_mid is None:
                gaps["missing_price_mid"] += 1
                continue
            klines = fetch_binance_klines(
                pair=f"{parsed['asset']}USDT",
                start_time=str(creation_time),
                end_time=decision_dt.isoformat().replace("+00:00", "Z"),
                interval="1m",
                cache_dir=cache_dir,
                fetcher=kline_fetcher,
            )
            if not klines.get("ok") or not klines.get("klines"):
                gaps[str(klines.get("gap_reason") or "missing_binance_klines_to_decision")] += 1
                continue
            highs = [_safe_float(row.get("high")) for row in klines.get("klines") or [] if isinstance(row, dict)]
            highs = [value for value in highs if value is not None]
            closes = [_safe_float(row.get("close")) for row in klines.get("klines") or [] if isinstance(row, dict)]
            closes = [value for value in closes if value is not None]
            if not highs or not closes:
                gaps["incomplete_binance_klines_to_decision"] += 1
                continue
            max_high = max(highs)
            spot = closes[-1]
            already_touched = max_high >= float(parsed["threshold"])
            years = max(1 / 365, (end_dt - decision_dt).total_seconds() / (365.0 * 86400.0))
            p_yes = 1.0 if already_touched else first_passage_probability_upper(
                spot=spot,
                threshold=float(parsed["threshold"]),
                annual_vol=float(annual_vols.get(str(parsed["asset"]), 0.60)),
                years=years,
            )
            rows.append(
                {
                    "market_slug": market.get("market_slug"),
                    "asset": parsed.get("asset"),
                    "threshold": parsed.get("threshold"),
                    "decision_time": decision_dt.isoformat().replace("+00:00", "Z"),
                    "market_creation_time": creation_time,
                    "price_mid": price_mid,
                    "p_model": round(p_yes, 8),
                    "resolved_payout": resolved_payout,
                    "already_touched_at_decision": already_touched,
                    "max_high_to_decision": max_high,
                    "spot_at_decision": spot,
                    "brier_market": round((price_mid - resolved_payout) ** 2, 8),
                    "brier_model": round((p_yes - resolved_payout) ** 2, 8),
                    "logloss_market": round(_log_loss(price_mid, resolved_payout), 8),
                    "logloss_model": round(_log_loss(p_yes, resolved_payout), 8),
                    "no_lookahead": True,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
    brier_market_values = [float(row["brier_market"]) for row in rows]
    brier_model_values = [float(row["brier_model"]) for row in rows]
    logloss_market_values = [float(row["logloss_market"]) for row in rows]
    logloss_model_values = [float(row["logloss_model"]) for row in rows]
    report = {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "sample_count": len(rows),
        "unique_market_count": len({row.get("market_slug") for row in rows}),
        "brier_market": _mean(brier_market_values),
        "brier_model": _mean(brier_model_values),
        "logloss_market": _mean(logloss_market_values),
        "logloss_model": _mean(logloss_model_values),
        "calibration_by_probability_bucket": _calibration_buckets(rows),
        "EV_proxy_after_ask_if_available": None,
        "no_lookahead_violation_count": no_lookahead_violation_count,
        "gap_reason_counts": [{"reason": reason, "count": gaps[reason]} for reason in sorted(gaps)],
        "status": "calibration_ready" if rows else "insufficient_crypto_touch_calibration_samples",
        "rows": rows,
    }
    return report


def _calibration_buckets(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        p = _safe_float(row.get("p_model"))
        if p is None:
            continue
        bucket = f"{int(min(9, max(0, math.floor(p * 10)))) / 10:.1f}-{(int(min(9, max(0, math.floor(p * 10)))) + 1) / 10:.1f}"
        buckets[bucket].append(row)
    output = []
    for bucket, members in sorted(buckets.items()):
        output.append(
            {
                "bucket": bucket,
                "sample_count": len(members),
                "mean_p_model": _mean([float(row["p_model"]) for row in members]),
                "mean_outcome": _mean([float(row["resolved_payout"]) for row in members]),
            }
        )
    return output


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
    "build_crypto_touch_calibration_report",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
