from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_crypto_market_semantics.v1"
METADATA_CREATION_FIELDS = ("createdAt", "created_at", "creationTime", "creation_time", "created", "start_time", "startDate")
PROXY_CREATION_FIELDS = ("earliest_trade_timestamp", "earliest_trade_time", "earliest_price_history_timestamp", "earliest_price_history_time")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_utc(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def market_rule_text(market: Dict[str, Any]) -> str:
    return " ".join(
        _text(market.get(field))
        for field in ("description", "rules", "resolution_rule", "resolution_rules", "resolution_source", "question", "title", "market_slug")
    ).strip()


def classify_crypto_semantics(market: Dict[str, Any]) -> str:
    text = market_rule_text(market).lower()
    compact_slug = _text(market.get("market_slug")).lower().replace("_", "-")
    if re.search(r"\b(any|highest|high)\b.*\b(candle|price)\b", text) or re.search(r"\b(reach|hit)\b", text) or "-reach-" in compact_slug or "-hit-" in compact_slug:
        return "touch_barrier"
    if re.search(r"\bclose[sd]?\b.*\b(above|over)\b", text):
        return "close_above"
    if re.search(r"\bclose[sd]?\b.*\b(below|under)\b", text):
        return "close_below"
    if re.search(r"\b(above|over|greater than)\b.*\b(on|by|at)\b", text):
        return "terminal_above"
    if re.search(r"\b(below|under|less than)\b.*\b(on|by|at)\b", text):
        return "terminal_below"
    return "ambiguous"


def classify_crypto_parse_gap(market: Dict[str, Any]) -> str:
    text = market_rule_text(market).lower()
    has_asset = bool(re.search(r"\b(btc|bitcoin|eth|ethereum)\b", text))
    if not has_asset:
        return "unsupported_asset"
    has_threshold_word = bool(re.search(r"\b(reach|hit|above|over|below|under|greater than|less than|price|target)\b", text))
    has_currency_number = bool(re.search(r"\$\s*[0-9]", text))
    if not has_threshold_word and not has_currency_number:
        return "non_threshold_crypto_market"
    threshold_numbers = re.findall(r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?|[0-9]{3,8}(?:\.\d+)?)\s*(?:k|m)?\b", text)
    if not threshold_numbers:
        return "missing_threshold"
    if len(set(threshold_numbers)) > 3:
        return "ambiguous_threshold"
    if classify_crypto_semantics(market) == "ambiguous":
        return "unsupported_semantics"
    if not re.search(r"\b(by|before|on|at)\b", text):
        return "date_parse_failed"
    return "malformed_title"


def parse_start_time(market: Dict[str, Any], *, generated_at: Optional[str] = None, end_time: Optional[str] = None) -> Optional[str]:
    resolved = resolve_market_creation_time(market, generated_at=generated_at, end_time=end_time, allow_proxy=False)
    if resolved.get("market_creation_time"):
        return str(resolved["market_creation_time"])


def resolve_market_creation_time(
    market: Dict[str, Any],
    *,
    generated_at: Optional[str] = None,
    end_time: Optional[str] = None,
    allow_proxy: bool = True,
) -> Dict[str, Any]:
    end_dt = _parse_utc(end_time or market.get("end_time") or market.get("endDate"))
    metadata_missing_reasons: List[str] = []
    for field in METADATA_CREATION_FIELDS:
        parsed = _parse_utc(market.get(field))
        if parsed is not None:
            if end_dt is not None and parsed > end_dt:
                return {
                    "market_creation_time": None,
                    "creation_time_source": field,
                    "creation_time_proxy": False,
                    "can_generate_official_candidate": False,
                    "can_generate_shadow_watch": False,
                    "gap_reason": "creation_time_after_end_time",
                    "metadata_missing_reason": None,
                }
            return {
                "market_creation_time": _iso(parsed),
                "creation_time_source": field,
                "creation_time_proxy": False,
                "can_generate_official_candidate": True,
                "can_generate_shadow_watch": True,
                "gap_reason": None,
                "metadata_missing_reason": None,
            }
        metadata_missing_reasons.append(f"missing_{field}")
    slug = _text(market.get("market_slug")).lower()
    match = re.search(r"from-([a-z]+)-([0-9]{1,2})", slug)
    if not match:
        if allow_proxy:
            proxy = _resolve_proxy_creation_time(market, end_dt=end_dt)
            if proxy.get("market_creation_time"):
                return proxy
        return {
            "market_creation_time": None,
            "creation_time_source": None,
            "creation_time_proxy": False,
            "can_generate_official_candidate": False,
            "can_generate_shadow_watch": False,
            "gap_reason": "start_time_unverified",
            "metadata_missing_reason": "metadata_creation_time_missing",
            "metadata_missing_fields": metadata_missing_reasons,
        }
    month_name, day_text = match.groups()
    month_lookup = {
        "january": 1,
        "jan": 1,
        "february": 2,
        "feb": 2,
        "march": 3,
        "mar": 3,
        "april": 4,
        "apr": 4,
        "may": 5,
        "june": 6,
        "jun": 6,
        "july": 7,
        "jul": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sep": 9,
        "october": 10,
        "oct": 10,
        "november": 11,
        "nov": 11,
        "december": 12,
        "dec": 12,
    }
    month = month_lookup.get(month_name)
    if month is None:
        return {
            "market_creation_time": None,
            "creation_time_source": "slug_from_date",
            "creation_time_proxy": False,
            "can_generate_official_candidate": False,
            "can_generate_shadow_watch": False,
            "gap_reason": "slug_from_date_parse_failed",
            "metadata_missing_reason": "metadata_creation_time_missing",
            "metadata_missing_fields": metadata_missing_reasons,
        }
    generated_dt = _parse_utc(generated_at)
    candidate_years: List[int] = []
    if generated_dt is not None:
        candidate_years.append(generated_dt.year)
    if end_dt is not None:
        candidate_years.extend([end_dt.year, end_dt.year - 1])
    candidate_years.append(datetime.now(timezone.utc).year)
    seen: set[int] = set()
    for year in candidate_years:
        if year in seen:
            continue
        seen.add(year)
        try:
            candidate_dt = datetime(int(year), int(month), int(day_text), tzinfo=timezone.utc)
        except ValueError:
            continue
        if end_dt is None or candidate_dt <= end_dt:
            return {
                "market_creation_time": _iso(candidate_dt),
                "creation_time_source": "slug_from_date",
                "creation_time_proxy": False,
                "can_generate_official_candidate": True,
                "can_generate_shadow_watch": True,
                "gap_reason": None,
                "metadata_missing_reason": "metadata_creation_time_missing",
                "metadata_missing_fields": metadata_missing_reasons,
            }
    try:
        fallback_year = (end_dt.year - 1) if end_dt is not None else datetime.now(timezone.utc).year
        fallback = datetime(int(fallback_year), int(month), int(day_text), tzinfo=timezone.utc)
        if end_dt is not None and fallback > end_dt:
            raise ValueError("slug start after end")
        return {
            "market_creation_time": _iso(fallback),
            "creation_time_source": "slug_from_date",
            "creation_time_proxy": False,
            "can_generate_official_candidate": True,
            "can_generate_shadow_watch": True,
            "gap_reason": None,
            "metadata_missing_reason": "metadata_creation_time_missing",
            "metadata_missing_fields": metadata_missing_reasons,
        }
    except ValueError:
        return {
            "market_creation_time": None,
            "creation_time_source": "slug_from_date",
            "creation_time_proxy": False,
            "can_generate_official_candidate": False,
            "can_generate_shadow_watch": False,
            "gap_reason": "creation_time_after_end_time",
            "metadata_missing_reason": "metadata_creation_time_missing",
            "metadata_missing_fields": metadata_missing_reasons,
        }


def _resolve_proxy_creation_time(market: Dict[str, Any], *, end_dt: Optional[datetime]) -> Dict[str, Any]:
    for field in PROXY_CREATION_FIELDS:
        parsed = _parse_utc(market.get(field))
        if parsed is None:
            continue
        if end_dt is not None and parsed > end_dt:
            return {
                "market_creation_time": None,
                "creation_time_source": field,
                "creation_time_proxy": True,
                "can_generate_official_candidate": False,
                "can_generate_shadow_watch": False,
                "gap_reason": "creation_time_after_end_time",
                "metadata_missing_reason": "metadata_creation_time_missing",
            }
        return {
            "market_creation_time": _iso(parsed),
            "creation_time_source": "earliest_trade_proxy" if "trade" in field else "earliest_price_history_proxy",
            "creation_time_proxy": True,
            "can_generate_official_candidate": False,
            "can_generate_shadow_watch": True,
            "gap_reason": None,
            "metadata_missing_reason": "metadata_creation_time_missing",
        }
    price_history = market.get("price_history")
    if isinstance(price_history, list):
        parsed_rows = [_parse_utc((row or {}).get("timestamp")) for row in price_history if isinstance(row, dict)]
        parsed_rows = [row for row in parsed_rows if row is not None]
        if parsed_rows:
            first = min(parsed_rows)
            if end_dt is not None and first > end_dt:
                gap = "creation_time_after_end_time"
                first_iso = None
            else:
                gap = None
                first_iso = _iso(first)
            return {
                "market_creation_time": first_iso,
                "creation_time_source": "earliest_price_history_proxy",
                "creation_time_proxy": True,
                "can_generate_official_candidate": False,
                "can_generate_shadow_watch": first_iso is not None,
                "gap_reason": gap,
                "metadata_missing_reason": "metadata_creation_time_missing",
            }
    trade_tape = market.get("trade_tape") if isinstance(market.get("trade_tape"), dict) else {}
    trades = market.get("trades") or trade_tape.get("trades")
    if isinstance(trades, list):
        parsed_trades = [_parse_utc((row or {}).get("timestamp")) for row in trades if isinstance(row, dict)]
        parsed_trades = [row for row in parsed_trades if row is not None]
        if parsed_trades:
            first = min(parsed_trades)
            if end_dt is not None and first > end_dt:
                gap = "creation_time_after_end_time"
                first_iso = None
            else:
                gap = None
                first_iso = _iso(first)
            return {
                "market_creation_time": first_iso,
                "creation_time_source": "earliest_trade_proxy",
                "creation_time_proxy": True,
                "can_generate_official_candidate": False,
                "can_generate_shadow_watch": first_iso is not None,
                "gap_reason": gap,
                "metadata_missing_reason": "metadata_creation_time_missing",
            }
    return {
        "market_creation_time": None,
        "creation_time_source": None,
        "creation_time_proxy": False,
        "can_generate_official_candidate": False,
        "can_generate_shadow_watch": False,
        "gap_reason": "start_time_unverified",
        "metadata_missing_reason": "metadata_creation_time_missing",
    }


def merge_market_metadata(market: Dict[str, Any], metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(market)
    if not isinstance(metadata, dict):
        return merged
    field_map = {
        "createdAt": "createdAt",
        "created_at": "created_at",
        "creationTime": "creationTime",
        "startDate": "startDate",
        "endDate": "end_time",
        "description": "description",
        "resolutionSource": "resolution_source",
        "rules": "rules",
    }
    for source, target in field_map.items():
        if not merged.get(target) and metadata.get(source):
            merged[target] = metadata.get(source)
    return merged


def build_crypto_semantics_audit_report(
    *,
    fills: Iterable[Dict[str, Any]],
    active_markets: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    markets_by_slug = {
        str(row.get("market_slug")): row
        for row in active_markets
        if isinstance(row, dict) and row.get("market_slug")
    }
    rows: List[Dict[str, Any]] = []
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        market = markets_by_slug.get(str(fill.get("market_slug"))) or {}
        semantics = classify_crypto_semantics(market or fill)
        current_model_semantics = str(fill.get("probability_semantics") or "terminal_above")
        semantics_match = semantics == current_model_semantics or (
            semantics in {"terminal_above", "close_above"} and current_model_semantics in {"terminal_above", "close_above"}
        )
        action = "keep" if semantics_match else "reprice_with_barrier_model" if semantics == "touch_barrier" else "invalidate_fill_until_rule_verified"
        rows.append(
            {
                "market_slug": fill.get("market_slug"),
                "token_id": fill.get("token_id"),
                "question": market.get("question") or market.get("title") or fill.get("market_slug"),
                "resolution_rule_text": market_rule_text(market),
                "parsed_asset": fill.get("asset"),
                "parsed_threshold": fill.get("threshold"),
                "parsed_start_time": parse_start_time(market, generated_at=generated_at, end_time=fill.get("target_time") or market.get("end_time")),
                "parsed_end_time": fill.get("target_time") or market.get("end_time"),
                "semantics_type": semantics,
                "side": fill.get("side"),
                "current_model_semantics": current_model_semantics,
                "semantics_match": semantics_match,
                "action": action,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fill_count": len(rows),
        "touch_barrier_count": len([row for row in rows if row.get("semantics_type") == "touch_barrier"]),
        "semantics_mismatch_count": len([row for row in rows if row.get("semantics_match") is False]),
        "invalidated_due_semantics_count": len([row for row in rows if row.get("action") != "keep"]),
        "invalidated_fill_count": len([row for row in rows if row.get("action") != "keep"]),
        "valid_fill_count": len([row for row in rows if row.get("action") == "keep"]),
        "invalidated_reason": "semantics_mismatch_touch_barrier"
        if any(row.get("action") == "reprice_with_barrier_model" for row in rows)
        else None,
        "rows": rows,
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
    "build_crypto_semantics_audit_report",
    "classify_crypto_semantics",
    "classify_crypto_parse_gap",
    "load_jsonl",
    "market_rule_text",
    "merge_market_metadata",
    "parse_start_time",
    "resolve_market_creation_time",
    "write_json",
]
