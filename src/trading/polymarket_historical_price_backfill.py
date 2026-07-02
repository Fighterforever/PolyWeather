from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = "polyweather_polymarket_price_history.v1"
CLOB_PRICE_HISTORY_URL = "https://clob.polymarket.com/prices-history"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_from_timestamp(value: Any) -> Optional[str]:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    if numeric > 10_000_000_000:
        numeric /= 1000.0
    return datetime.fromtimestamp(numeric, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_polymarket_price_history(
    token_id: str,
    *,
    interval: str = "all",
    fidelity: int = 60,
    timeout: int = 20,
) -> Dict[str, Any]:
    query = urllib.parse.urlencode({"market": str(token_id), "interval": interval, "fidelity": int(fidelity)})
    request = urllib.request.Request(
        f"{CLOB_PRICE_HISTORY_URL}?{query}",
        headers={"User-Agent": "PolyWeatherResearch/1.0 paper-only polymarket-price-backfill"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Polymarket price history returned non-object payload")
    return payload


def normalize_price_history_rows(
    payload: Dict[str, Any],
    *,
    token_id: str,
    market_slug: Optional[str] = None,
) -> List[Dict[str, Any]]:
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    rows: List[Dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        timestamp = _iso_from_timestamp(item.get("t") or item.get("timestamp"))
        price = _safe_float(item.get("p") if item.get("p") is not None else item.get("price"))
        if timestamp is None or price is None:
            continue
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
                "token_id": str(token_id),
                "market_slug": market_slug,
                "timestamp": timestamp,
                "price": float(price),
                "source": "polymarket_clob_prices_history",
                "executable_depth_available": False,
                "price_semantics": "historical_trade_or_market_probability_not_orderbook_depth",
                "can_compute_taker_pnl": False,
            }
        )
    return rows


def backfill_polymarket_price_history(
    markets: Iterable[Dict[str, Any]],
    *,
    interval: str = "all",
    fidelity: int = 60,
    timeout: int = 20,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    token_specs: List[tuple[str, Optional[str]]] = []
    seen: set[str] = set()
    for market in markets:
        token_id = _text(market.get("token_id") or (market.get("token_id_by_outcome") or {}).get("Yes"))
        if not token_id or token_id in seen:
            continue
        seen.add(token_id)
        token_specs.append((token_id, _text(market.get("market_slug")) or None))
        if max_tokens is not None and len(token_specs) >= int(max_tokens):
            break
    for token_id, market_slug in token_specs:
        try:
            payload = fetch_polymarket_price_history(token_id, interval=interval, fidelity=fidelity, timeout=timeout)
            token_rows = normalize_price_history_rows(payload, token_id=token_id, market_slug=market_slug)
            if not token_rows:
                gaps.append({"token_id": token_id, "market_slug": market_slug, "gap_reason": "empty_price_history"})
            rows.extend(token_rows)
        except Exception as exc:
            gaps.append({"token_id": token_id, "market_slug": market_slug, "gap_reason": "source_fetch_error", "gap_detail": str(exc)})
    return {
        "schema_version": f"{SCHEMA_VERSION}.report",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "token_count": len(token_specs),
        "price_row_count": len(rows),
        "gap_count": len(gaps),
        "rows": rows,
        "gaps": gaps,
    }


def write_price_history_artifacts(
    report: Dict[str, Any],
    *,
    output_path: str | Path,
    gap_report_path: str | Path,
) -> Dict[str, Any]:
    rows = [row for row in report.get("rows") or [] if isinstance(row, dict)]
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    gap_report = {
        "schema_version": "polyweather_polymarket_price_history_gap_report.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "output_path": str(output_path),
        "price_row_count": len(rows),
        "token_count": report.get("token_count"),
        "gap_count": len(report.get("gaps") or []),
        "gaps": report.get("gaps") or [],
    }
    Path(gap_report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(gap_report_path).write_text(json.dumps(gap_report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return gap_report
