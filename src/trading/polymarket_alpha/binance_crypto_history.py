from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_binance_crypto_history.v1"
BINANCE_BASE_URL = "https://api.binance.com"
DEFAULT_CACHE_DIR = Path("evidence/polymarket_alpha/binance_klines")


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
        return float(value)
    except (TypeError, ValueError):
        return None


def binance_pair_for_asset(asset: str) -> Optional[str]:
    asset_key = str(asset or "").upper()
    if asset_key in {"BTC", "BITCOIN"}:
        return "BTCUSDT"
    if asset_key in {"ETH", "ETHEREUM"}:
        return "ETHUSDT"
    return None


def _cache_name(pair: str, interval: str, start_ms: int, end_ms: int) -> str:
    safe = re.sub(r"[^A-Z0-9_]+", "_", f"{pair}_{interval}_{start_ms}_{end_ms}".upper())
    return f"{safe}.json"


def fetch_binance_klines(
    *,
    pair: str,
    start_time: str,
    end_time: str,
    interval: str = "1m",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    fetcher: Optional[Callable[[str], Any]] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    start_dt = _parse_utc(start_time)
    end_dt = _parse_utc(end_time)
    if start_dt is None or end_dt is None:
        return {"ok": False, "gap_reason": "invalid_time_range", "klines": []}
    if end_dt <= start_dt:
        return {"ok": False, "gap_reason": "end_not_after_start", "klines": []}
    pair = str(pair or "").upper()
    if pair not in {"BTCUSDT", "ETHUSDT"}:
        return {"ok": False, "gap_reason": "unsupported_binance_pair", "klines": []}
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    cache_path = Path(cache_dir) / _cache_name(pair, interval, start_ms, end_ms)
    if use_cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = {}
        if isinstance(cached, dict) and cached.get("schema_version") == SCHEMA_VERSION:
            return cached

    fetcher = fetcher or _fetch_url_json
    cursor = start_ms
    rows: List[Dict[str, Any]] = []
    request_count = 0
    gap_reason = None
    while cursor < end_ms:
        params = urllib.parse.urlencode(
            {
                "symbol": pair,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        url = f"{BINANCE_BASE_URL}/api/v3/klines?{params}"
        request_count += 1
        try:
            payload = fetcher(url)
        except Exception as exc:
            gap_reason = f"binance_fetch_error:{type(exc).__name__}"
            break
        if not isinstance(payload, list) or not payload:
            break
        parsed_batch = [_parse_kline(row) for row in payload]
        parsed_batch = [row for row in parsed_batch if row is not None]
        if not parsed_batch:
            break
        rows.extend(parsed_batch)
        last_open_ms = int(parsed_batch[-1]["open_time_ms"])
        next_cursor = last_open_ms + 60_000
        if next_cursor <= cursor:
            gap_reason = "pagination_stalled"
            break
        cursor = next_cursor
        if len(payload) < 1000:
            break
    result = {
        "schema_version": SCHEMA_VERSION,
        "ok": gap_reason is None,
        "gap_reason": gap_reason,
        "pair": pair,
        "interval": interval,
        "start_time": _iso(start_dt),
        "end_time": _iso(end_dt),
        "request_count": request_count,
        "kline_count": len(rows),
        "klines": rows,
        "data_source": "binance_1m_klines" if interval == "1m" else f"binance_{interval}_klines",
        "paper_only": True,
        "live_order_path": False,
    }
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    return result


def _fetch_url_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_kline(row: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(row, list) or len(row) < 5:
        return None
    high = _safe_float(row[2])
    open_time_ms = int(row[0]) if str(row[0]).isdigit() else None
    close_time_ms = int(row[6]) if len(row) > 6 and str(row[6]).isdigit() else None
    if high is None or open_time_ms is None:
        return None
    open_dt = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    close_dt = datetime.fromtimestamp((close_time_ms or open_time_ms) / 1000, tz=timezone.utc)
    return {
        "open_time_ms": open_time_ms,
        "open_time": _iso(open_dt),
        "close_time_ms": close_time_ms,
        "close_time": _iso(close_dt),
        "high": high,
    }


def verify_high_since_start(
    *,
    asset: str,
    threshold: float,
    market_creation_time: str,
    current_time: str,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    fetcher: Optional[Callable[[str], Any]] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    pair = binance_pair_for_asset(asset)
    if pair is None:
        return {
            "asset": asset,
            "pair": None,
            "high_since_start_verified": False,
            "barrier_already_touched": False,
            "gap_reason": "unsupported_asset",
            "kline_count": 0,
            "paper_only": True,
            "live_order_path": False,
        }
    klines_result = fetch_binance_klines(
        pair=pair,
        start_time=market_creation_time,
        end_time=current_time,
        interval="1m",
        cache_dir=cache_dir,
        fetcher=fetcher,
        use_cache=use_cache,
    )
    if not klines_result.get("ok"):
        return {
            "asset": asset,
            "pair": pair,
            "market_creation_time": market_creation_time,
            "verification_start_time": market_creation_time,
            "verification_end_time": current_time,
            "max_high_since_start": None,
            "max_high_at": None,
            "barrier_already_touched": False,
            "high_since_start_verified": False,
            "kline_count": int(klines_result.get("kline_count") or 0),
            "gap_reason": klines_result.get("gap_reason") or "binance_klines_unavailable",
            "data_source": "binance_1m_klines",
            "paper_only": True,
            "live_order_path": False,
        }
    max_row = None
    for row in klines_result.get("klines") or []:
        high = _safe_float(row.get("high")) if isinstance(row, dict) else None
        if high is None:
            continue
        if max_row is None or high > float(max_row["high"]):
            max_row = row
    max_high = _safe_float(max_row.get("high")) if isinstance(max_row, dict) else None
    touched = max_high is not None and max_high >= float(threshold)
    return {
        "asset": asset,
        "pair": pair,
        "market_creation_time": market_creation_time,
        "verification_start_time": market_creation_time,
        "verification_end_time": current_time,
        "max_high_since_start": max_high,
        "max_high_at": max_row.get("open_time") if isinstance(max_row, dict) else None,
        "barrier_already_touched": bool(touched),
        "high_since_start_verified": bool(max_high is not None),
        "kline_count": int(klines_result.get("kline_count") or 0),
        "gap_reason": None if max_high is not None else "empty_binance_klines",
        "data_source": "binance_1m_klines",
        "paper_only": True,
        "live_order_path": False,
    }


def summarize_high_since_start_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    materialized = [row for row in rows if isinstance(row, dict)]
    return {
        "touch_barrier_market_count": len(materialized),
        "high_since_start_verified_count": len([row for row in materialized if row.get("high_since_start_verified")]),
        "barrier_already_touched_count": len([row for row in materialized if row.get("barrier_already_touched")]),
        "verified_not_touched_count": len([
            row for row in materialized
            if row.get("high_since_start_verified") and not row.get("barrier_already_touched")
        ]),
        "gap_reason_counts": _count_by(materialized, "gap_reason"),
    }


def _count_by(rows: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "none")
        counts[value] = counts.get(value, 0) + 1
    return [{"reason": key, "count": counts[key]} for key in sorted(counts)]


__all__ = [
    "SCHEMA_VERSION",
    "binance_pair_for_asset",
    "fetch_binance_klines",
    "summarize_high_since_start_rows",
    "verify_high_since_start",
    "write_json",
]
