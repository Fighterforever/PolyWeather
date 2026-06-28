from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_crypto_market_semantics.v1"


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


def parse_start_time(market: Dict[str, Any], *, generated_at: Optional[str] = None) -> Optional[str]:
    for field in ("start_time", "created_at", "createdAt", "creation_time", "created"):
        parsed = _parse_utc(market.get(field))
        if parsed is not None:
            return _iso(parsed)
    slug = _text(market.get("market_slug")).lower()
    match = re.search(r"from-([a-z]+)-([0-9]{1,2})", slug)
    if not match:
        return None
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
        return None
    year = None
    for candidate in (market.get("end_time"), generated_at):
        parsed = _parse_utc(candidate)
        if parsed is not None:
            year = parsed.year
            break
    if year is None:
        year = datetime.now(timezone.utc).year
    try:
        return _iso(datetime(int(year), int(month), int(day_text), tzinfo=timezone.utc))
    except ValueError:
        return None


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
                "parsed_start_time": parse_start_time(market, generated_at=generated_at),
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
    "load_jsonl",
    "market_rule_text",
    "parse_start_time",
    "write_json",
]
