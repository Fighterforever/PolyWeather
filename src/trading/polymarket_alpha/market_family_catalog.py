from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA_VERSION = "polyweather_polymarket_alpha_market_family_catalog.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number_tokens(text: str) -> List[float]:
    values: List[float] = []
    for match in re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", text):
        parsed = _safe_float(match)
        if parsed is not None:
            values.append(parsed)
    return values


def _entity_key(row: Dict[str, Any]) -> str:
    text = _text(row.get("title") or row.get("question") or row.get("market_slug"))
    text = re.sub(r"\b(yes|no|will|be|by|on|in|the|a|an|at|over|under|above|below|or|more|less|than)\b", " ", text, flags=re.I)
    text = re.sub(r"\d+(?:\.\d+)?", " ", text)
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:80] or _text(row.get("event_slug") or row.get("market_slug"))


def _family_base_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        _text(row.get("event_slug") or row.get("market_slug")),
        _text(row.get("category") or "uncategorized"),
        _entity_key(row),
    )


def _family_type(rows: List[Dict[str, Any]]) -> str:
    if len(rows) == 1:
        outcomes = rows[0].get("outcomes") if isinstance(rows[0].get("outcomes"), list) else []
        return "mutually_exclusive_outcomes" if len(outcomes) > 2 else "isolated_binary"
    numeric = [threshold for row in rows for threshold in _number_tokens(_text(row.get("title") or row.get("question")))]
    if len(set(numeric)) >= 2:
        return "threshold_monotonic_family"
    outcome_sets = {tuple(str(item).lower() for item in (row.get("outcomes") or [])) for row in rows}
    if len(outcome_sets) == 1 and len(next(iter(outcome_sets), ())) > 2:
        return "mutually_exclusive_outcomes"
    if len({_entity_key(row) for row in rows}) <= max(1, len(rows) // 2):
        return "duplicate_or_near_duplicate"
    return "unresolved_unknown"


def _monotonic_direction(rows: List[Dict[str, Any]]) -> Optional[str]:
    text = " ".join(_text(row.get("title") or row.get("question")).lower() for row in rows)
    if re.search(r"\b(over|above|at least|greater than|higher)\b", text):
        return "increasing_threshold_yes_decreases"
    if re.search(r"\b(under|below|less than|lower)\b", text):
        return "increasing_threshold_yes_increases"
    return None


def _completeness(family_type: str, rows: List[Dict[str, Any]], thresholds: List[float]) -> Tuple[bool, float, List[str]]:
    reasons: List[str] = []
    confidence = 0.35
    exhaustive = False
    if family_type == "mutually_exclusive_outcomes":
        exhaustive = True
        confidence = 0.9
    elif family_type == "threshold_monotonic_family":
        confidence = 0.55 if len(set(thresholds)) >= 2 else 0.3
        reasons.append("threshold_family_not_certified_exhaustive")
    elif family_type == "duplicate_or_near_duplicate":
        confidence = 0.5
        reasons.append("duplicate_family_not_exhaustive")
    elif family_type == "isolated_binary":
        reasons.append("isolated_binary_no_family_structure")
    else:
        reasons.append("unresolved_family_structure")
    if not rows:
        reasons.append("empty_family")
    return exhaustive, confidence, sorted(set(reasons))


def _compact_market(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "market_id": row.get("market_id"),
        "condition_id": row.get("condition_id"),
        "event_slug": row.get("event_slug"),
        "market_slug": row.get("market_slug"),
        "title": row.get("title") or row.get("question"),
        "category": row.get("category"),
        "outcomes": row.get("outcomes") if isinstance(row.get("outcomes"), list) else [],
        "token_ids": row.get("token_ids") if isinstance(row.get("token_ids"), list) else [],
        "outcome_prices": row.get("outcome_prices") if isinstance(row.get("outcome_prices"), list) else [],
        "volume": row.get("volume"),
        "liquidity": row.get("liquidity"),
        "spread": row.get("spread"),
        "best_bid": row.get("best_bid"),
        "best_ask": row.get("best_ask"),
        "orderbooks": row.get("orderbooks") if isinstance(row.get("orderbooks"), dict) else {},
        "active": row.get("active"),
        "closed": row.get("closed"),
        "end_time": row.get("end_time"),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _family_summary(family: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "family_id": family.get("family_id"),
        "family_type": family.get("family_type"),
        "category": family.get("category"),
        "event_slug": family.get("event_slug"),
        "market_count": family.get("market_count"),
        "outcome_count": family.get("outcome_count"),
        "numeric_thresholds": family.get("numeric_thresholds"),
        "is_exhaustive": family.get("is_exhaustive"),
        "completeness_confidence": family.get("completeness_confidence"),
        "gap_reasons": family.get("gap_reasons"),
        "total_volume": family.get("total_volume"),
        "market_slugs": family.get("market_slugs"),
        "token_ids": family.get("token_ids"),
    }


def build_market_family_catalog(markets: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    market_rows = [row for row in markets if isinstance(row, dict)]
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in market_rows:
        grouped[_family_base_key(row)].append(row)

    families: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    assigned: set[str] = set()
    for index, (key, rows) in enumerate(sorted(grouped.items(), key=lambda item: (item[0], len(item[1])))):
        event_slug, category, entity = key
        family_type = _family_type(rows)
        thresholds = sorted(set(threshold for row in rows for threshold in _number_tokens(_text(row.get("title") or row.get("question")))))
        exhaustive, confidence, gap_reasons = _completeness(family_type, rows, thresholds)
        market_slugs = [_text(row.get("market_slug")) for row in rows if _text(row.get("market_slug"))]
        token_ids = sorted(
            {
                str(token)
                for row in rows
                for token in (row.get("token_ids") or [])
                if str(token).strip()
            }
        )
        outcome_count = max((len(row.get("outcomes") or []) for row in rows), default=0)
        total_volume = round(sum(float(row.get("volume") or 0.0) for row in rows), 6)
        family = {
            "schema_version": "polyweather_polymarket_alpha_market_family.v1",
            "family_id": f"pmfam-{index:06d}",
            "family_type": family_type,
            "category": category,
            "event_slug": event_slug,
            "entity_key": entity,
            "market_count": len(rows),
            "outcome_count": outcome_count,
            "market_slugs": market_slugs,
            "token_ids": token_ids,
            "numeric_thresholds": thresholds,
            "monotonic_direction": _monotonic_direction(rows),
            "is_exhaustive": exhaustive,
            "completeness_confidence": confidence,
            "gap_reasons": gap_reasons,
            "total_volume": total_volume,
            "markets": [_compact_market(row) for row in rows] if exhaustive else [],
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        families.append(family)
        assigned.update(market_slugs)
        if gap_reasons:
            gaps.append(
                {
                    "family_id": family["family_id"],
                    "family_type": family_type,
                    "category": category,
                    "event_slug": event_slug,
                    "market_count": len(rows),
                    "gap_reasons": gap_reasons,
                    "market_slugs": market_slugs[:10],
                }
            )

    top = sorted(families, key=lambda row: (int(row.get("market_count") or 0), float(row.get("total_volume") or 0.0)), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "input_market_count": len(market_rows),
        "assigned_market_count": len(assigned),
        "family_count": len(families),
        "family_type_counts": [
            {"family_type": key, "count": count}
            for key, count in sorted(Counter(row.get("family_type") for row in families).items())
        ],
        "top_families": [_family_summary(row) for row in top[:50]],
        "families": families,
        "gaps": gaps,
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


__all__ = ["SCHEMA_VERSION", "build_market_family_catalog", "load_json", "load_jsonl", "write_json", "write_jsonl"]
