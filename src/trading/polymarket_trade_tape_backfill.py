from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from src.trading.weather_paper_journal import stable_json_hash
from src.weather.weather_sources import parse_utc


SCHEMA_VERSION = "polyweather_polymarket_trade_tape.v1"
DATA_API_BASE_URL = "https://data-api.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso_from_epoch_or_text(value: Any) -> Optional[str]:
    number = _safe_int(value)
    if number is not None:
        return datetime.fromtimestamp(number, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    parsed = parse_utc(value)
    if parsed is not None:
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return None


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [dict(row) for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
            handle.write("\n")
    return len(materialized)


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def locked_signal_rows_from_report(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("rows", "all_locked_signal_triage", "locked_signal_rows"):
        rows = report.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _market_key(row: Dict[str, Any]) -> str:
    return _text(row.get("market_slug") or row.get("slug") or row.get("market_id"))


def _closed_by_slug(closed_markets: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}
    for row in closed_markets:
        if not isinstance(row, dict):
            continue
        slug = _text(row.get("market_slug") or row.get("slug"))
        if slug and slug not in selected:
            selected[slug] = row
    return selected


def token_id_by_outcome(record: Dict[str, Any]) -> Dict[str, str]:
    explicit = record.get("token_id_by_outcome") if isinstance(record.get("token_id_by_outcome"), dict) else {}
    if explicit:
        return {str(key).title(): _text(value) for key, value in explicit.items() if _text(value)}
    outcomes = [_text(item).title() for item in _parse_json_list(record.get("outcomes"))]
    token_ids = [_text(item) for item in _parse_json_list(record.get("clobTokenIds"))]
    return {outcome: token for outcome, token in zip(outcomes, token_ids) if outcome and token}


def locked_side_token_id(signal: Dict[str, Any], closed_record: Optional[Dict[str, Any]]) -> Optional[str]:
    side = _text(signal.get("locked_side") or signal.get("side") or signal.get("outcome")).upper()
    if closed_record:
        token_map = token_id_by_outcome(closed_record)
        if side == "YES" and token_map.get("Yes"):
            return token_map["Yes"]
        if side == "NO" and token_map.get("No"):
            return token_map["No"]
    if side == "YES":
        return _text(signal.get("token_id")) or None
    return None


def _condition_id(record: Dict[str, Any]) -> Optional[str]:
    for field in ("conditionId", "condition_id", "conditionID", "questionID"):
        value = _text(record.get(field))
        if value:
            return value
    spec = record.get("settlement_spec") if isinstance(record.get("settlement_spec"), dict) else {}
    for field in ("conditionId", "condition_id"):
        value = _text(spec.get(field))
        if value:
            return value
    return None


class PolymarketTradeTapeClient:
    def __init__(
        self,
        *,
        data_api_base_url: str = DATA_API_BASE_URL,
        gamma_base_url: str = GAMMA_BASE_URL,
        timeout: float = 20.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.data_api_base_url = data_api_base_url.rstrip("/")
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def get_gamma_market(self, market_id: str) -> Dict[str, Any]:
        response = self.session.get(f"{self.gamma_base_url}/markets/{market_id}", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def get_trades(self, condition_id: str, *, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        response = self.session.get(
            f"{self.data_api_base_url}/trades",
            params={"market": condition_id, "limit": int(limit), "offset": int(offset)},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            rows = payload.get("trades") or payload.get("data") or payload.get("results") or []
            return [row for row in rows if isinstance(row, dict)]
        return []


TradeFetcher = Callable[[Dict[str, Any], Optional[str]], Tuple[List[Dict[str, Any]], Optional[str], List[str]]]


def _fetch_market_trades(
    client: PolymarketTradeTapeClient,
    record: Dict[str, Any],
    *,
    max_pages: int = 6,
    page_size: int = 500,
) -> Tuple[List[Dict[str, Any]], Optional[str], List[str]]:
    gaps: List[str] = []
    condition_id = _condition_id(record)
    if not condition_id and _text(record.get("market_id")):
        try:
            gamma_market = client.get_gamma_market(_text(record.get("market_id")))
        except Exception:
            gamma_market = {}
            gaps.append("source_error")
        condition_id = _condition_id(gamma_market) or condition_id
        if gamma_market:
            record.update(
                {
                    "conditionId": _condition_id(gamma_market),
                    "outcomes": record.get("outcomes") or gamma_market.get("outcomes"),
                    "clobTokenIds": record.get("clobTokenIds") or gamma_market.get("clobTokenIds"),
                }
            )
    if not condition_id:
        return [], None, gaps + ["token_not_found"]
    all_rows: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for page in range(max(1, int(max_pages))):
        offset = page * int(page_size)
        try:
            rows = client.get_trades(condition_id, limit=page_size, offset=offset)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            reason = "no_trade_endpoint" if status in {401, 403, 404} else "source_error"
            return all_rows, condition_id, gaps + [reason]
        except requests.RequestException:
            return all_rows, condition_id, gaps + ["source_error"]
        if not rows:
            break
        new_count = 0
        for row in rows:
            row_id = _text(row.get("transactionHash") or row.get("id") or row.get("trade_id")) or stable_json_hash(row)
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            all_rows.append(row)
            new_count += 1
        if len(rows) < int(page_size) or new_count == 0:
            break
    return all_rows, condition_id, gaps


def _normalize_trade_row(
    row: Dict[str, Any],
    *,
    record: Dict[str, Any],
    condition_id: Optional[str],
    generated_at: str,
) -> Dict[str, Any]:
    token_id = _text(row.get("asset") or row.get("token_id") or row.get("tokenId"))
    timestamp = _iso_from_epoch_or_text(row.get("timestamp") or row.get("createdAt") or row.get("created_at"))
    price = _safe_float(row.get("price"))
    size = _safe_float(row.get("size") or row.get("amount"))
    source_trade_id = _text(row.get("transactionHash") or row.get("id") or row.get("trade_id"))
    if not source_trade_id:
        source_trade_id = stable_json_hash(
            {
                "condition_id": condition_id,
                "token_id": token_id,
                "timestamp": timestamp,
                "price": price,
                "size": size,
                "side": row.get("side"),
            }
        )
    taker_side = _text(row.get("side")).upper() or None
    direction_confidence = "reported_by_polymarket_data_api" if taker_side else "unknown"
    return {
        "schema_version": f"{SCHEMA_VERSION}.trade",
        "generated_at": generated_at,
        "market_slug": row.get("slug") or record.get("market_slug") or record.get("slug"),
        "market_id": record.get("market_id") or record.get("id"),
        "condition_id": condition_id,
        "token_id": token_id or None,
        "side": row.get("outcome"),
        "outcome": row.get("outcome"),
        "price": price,
        "size": size,
        "timestamp": timestamp,
        "raw_timestamp": row.get("timestamp"),
        "source": "polymarket_data_api_trades",
        "source_trade_id": source_trade_id,
        "direction_source": "polymarket_data_api_side" if taker_side else "unknown",
        "taker_side": taker_side,
        "direction_confidence": direction_confidence,
        "executable_proxy": True,
        "exact_orderbook_depth_available": False,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def build_trade_tape_backfill_report(
    *,
    locked_signals: Iterable[Dict[str, Any]],
    closed_markets: Iterable[Dict[str, Any]],
    client: Optional[PolymarketTradeTapeClient] = None,
    trade_fetcher: Optional[TradeFetcher] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    signals = [row for row in locked_signals if isinstance(row, dict)]
    closed_by_slug = _closed_by_slug(closed_markets)
    client = client or PolymarketTradeTapeClient()
    trades: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    attempted_market_slugs: set[str] = set()
    attempted_tokens: List[str] = []
    seen_trade_ids: set[str] = set()
    market_records: Dict[str, Dict[str, Any]] = {}
    for signal in signals:
        slug = _text(signal.get("market_slug"))
        record = dict(closed_by_slug.get(slug) or {})
        if slug and not record:
            record = {"market_slug": slug, "market_id": signal.get("market_id")}
        token = locked_side_token_id(signal, record)
        if token:
            attempted_tokens.append(token)
        if slug:
            market_records.setdefault(slug, record)
    for slug, record in market_records.items():
        attempted_market_slugs.add(slug)
        if trade_fetcher is not None:
            raw_trades, condition_id, fetch_gaps = trade_fetcher(record, _condition_id(record))
        else:
            raw_trades, condition_id, fetch_gaps = _fetch_market_trades(client, record)
        if not raw_trades and "no_trades_in_window" not in fetch_gaps and not any(
            reason in fetch_gaps for reason in ("no_trade_endpoint", "token_not_found", "source_error")
        ):
            fetch_gaps = list(fetch_gaps) + ["no_trades_in_window"]
        for reason in sorted(set(fetch_gaps)):
            gaps.append(
                {
                    "market_slug": slug,
                    "market_id": record.get("market_id") or record.get("id"),
                    "condition_id": condition_id,
                    "gap_reason": reason,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                }
            )
        for raw in raw_trades:
            normalized = _normalize_trade_row(raw, record=record, condition_id=condition_id, generated_at=generated_at)
            if not normalized.get("token_id") or normalized.get("price") is None or normalized.get("timestamp") is None:
                gaps.append(
                    {
                        "market_slug": slug,
                        "market_id": record.get("market_id") or record.get("id"),
                        "condition_id": condition_id,
                        "gap_reason": "source_error",
                        "paper_only": True,
                        "counts_for_live_gate": False,
                    }
                )
                continue
            if normalized.get("direction_confidence") == "unknown":
                gaps.append(
                    {
                        "market_slug": slug,
                        "market_id": record.get("market_id") or record.get("id"),
                        "condition_id": condition_id,
                        "gap_reason": "trade_direction_unknown",
                        "paper_only": True,
                        "counts_for_live_gate": False,
                    }
                )
            key = _text(normalized.get("source_trade_id"))
            if key in seen_trade_ids:
                continue
            seen_trade_ids.add(key)
            trades.append(normalized)
    source_counts = Counter(_text(row.get("source")) or "unknown" for row in trades)
    gap_counts = Counter(_text(row.get("gap_reason")) or "unknown" for row in gaps)
    manifest = {
        "schema_version": f"{SCHEMA_VERSION}.manifest",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "attempted_locked_signal_count": len(signals),
        "token_attempt_count": len(attempted_tokens),
        "token_count": len(set(attempted_tokens)),
        "market_slug_count": len(attempted_market_slugs),
        "trade_count": len(trades),
        "source_counts": [{"source": key, "count": count} for key, count in sorted(source_counts.items())],
        "gap_counts": [{"gap_reason": key, "count": count} for key, count in sorted(gap_counts.items())],
        "no_price_history_used_as_trades": True,
        "sample_trades": trades[:5],
        "sample_gaps": gaps[:20],
    }
    gap_report = {
        "schema_version": f"{SCHEMA_VERSION}.gap_report",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "gap_count": len(gaps),
        "gap_counts": manifest["gap_counts"],
        "gaps": gaps,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "trades": sorted(trades, key=lambda row: (row.get("market_slug") or "", row.get("timestamp") or "")),
        "manifest": manifest,
        "gap_report": gap_report,
    }


def load_locked_signals_report(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    return locked_signal_rows_from_report(payload if isinstance(payload, dict) else {})
