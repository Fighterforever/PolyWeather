from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


SCHEMA_VERSION = "polyweather_polymarket_alpha_rule_confusion.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _features(row: Dict[str, Any]) -> List[str]:
    title = _text(row.get("title") or row.get("question"))
    description = _text(row.get("description"))
    combined = f"{title} {description}".lower()
    features: List[str] = []
    if re.search(r"\b(by|before|on|during|through)\b", combined) and re.search(r"\b(et|utc|local|pst|est|gmt)\b", combined) is None:
        features.append("date_time_ambiguity")
    if re.search(r"\baccording to\b|\bsource\b|\bofficial\b", combined) and len(re.findall(r"\b(according to|source|official|reported by)\b", combined)) > 1:
        features.append("source_ambiguity")
    if re.search(r"\b(team|candidate|company|token|coin|city|country)\b", combined) and re.search(r"\bor\b|/", title.lower()):
        features.append("entity_ambiguity")
    if re.search(r"\d", combined) and re.search(r"\b(over|under|above|below|at least|more than|less than)\b", combined):
        features.append("threshold_ambiguity")
    if title and description and title.lower() not in description.lower() and re.search(r"\bnot\b|\bunless\b|\bexcluding\b", description.lower()):
        features.append("title_resolution_mismatch")
    if re.search(r"\b(celsius|fahrenheit|feet|meters|percent|bps|million|billion)\b", combined) and re.search(r"\b(c|f|%|\$|usd)\b", combined):
        features.append("units_mismatch")
    if re.search(r"\bby\b", title.lower()) and re.search(r"\bon\b", description.lower()):
        features.append("by_vs_on_wording")
    return sorted(set(features))


def scan_rule_confusion(markets: Iterable[Dict[str, Any]], *, min_liquidity: float = 0.0) -> Dict[str, Any]:
    market_rows = [row for row in markets if isinstance(row, dict)]
    candidates: List[Dict[str, Any]] = []
    blocker_counts: Dict[str, int] = {}

    def block(reason: str) -> None:
        blocker_counts[reason] = blocker_counts.get(reason, 0) + 1

    for row in market_rows:
        features = _features(row)
        if not features:
            block("no_confusion_feature")
            continue
        liquidity = _safe_float(row.get("liquidity"))
        if liquidity < float(min_liquidity):
            block("liquidity_below_min")
            continue
        severity = len(features) + min(2.0, liquidity / 5000.0)
        candidates.append(
            {
                "market_id": row.get("market_id"),
                "market_slug": row.get("market_slug"),
                "event_slug": row.get("event_slug"),
                "category": row.get("category"),
                "title": row.get("title") or row.get("question"),
                "confusion_features": features,
                "confusion_severity": round(severity, 6),
                "liquidity": liquidity,
                "volume": row.get("volume"),
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    candidates = sorted(candidates, key=lambda row: (float(row.get("confusion_severity") or 0.0), float(row.get("liquidity") or 0.0)), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "input_market_count": len(market_rows),
        "candidate_count": len(candidates),
        "no_candidate_reason_counts": [{"reason": key, "count": value} for key, value in sorted(blocker_counts.items())],
        "top_candidates": candidates[:25],
        "candidates": candidates,
    }


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


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


__all__ = ["SCHEMA_VERSION", "load_jsonl", "scan_rule_confusion", "write_json", "write_jsonl"]
