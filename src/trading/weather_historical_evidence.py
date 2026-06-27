from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_paper_journal import _append_jsonl, _safe_float, load_jsonl


HISTORICAL_EVIDENCE_SCHEMA_VERSION = "polyweather_weather_historical_evidence.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_float(row: Dict[str, Any], fields: Iterable[str]) -> Optional[float]:
    for field in fields:
        value = _safe_float(row.get(field))
        if value is not None:
            return float(value)
    return None


def _first_text(row: Dict[str, Any], fields: Iterable[str]) -> Optional[str]:
    for field in fields:
        value = _text(row.get(field))
        if value:
            return value
    return None


def _fill_gap_reason(fill: Dict[str, Any]) -> Optional[str]:
    if not _text(fill.get("token_id")):
        return "missing_token_id"
    if fill.get("missed_fill") is True or fill.get("fully_filled") is False:
        return "missed_or_partial_fill"
    if _first_float(fill, ("entry_price", "historical_entry_price", "paper_entry_price")) is None:
        return "missing_entry_price"
    if _first_float(fill, ("p_lcb", "p_model", "model_probability")) is None:
        return "missing_prediction_probability"
    if not _first_text(fill, ("available_at", "generated_at", "recorded_at", "orderbook_recorded_at")):
        return "missing_available_at"
    return None


def historical_evidence_from_replay_fills(
    fills: Iterable[Dict[str, Any]],
    *,
    source: str = "strict_gate_replay",
) -> Dict[str, Any]:
    rows = [row for row in fills if isinstance(row, dict)]
    supplements: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    for fill in rows:
        gap_reason = _fill_gap_reason(fill)
        if gap_reason:
            gaps.append(
                {
                    "gap_reason": gap_reason,
                    "market_slug": fill.get("market_slug"),
                    "token_id": fill.get("token_id"),
                    "candidate_id": fill.get("candidate_id"),
                    "orderbook_snapshot_id": fill.get("orderbook_snapshot_id"),
                }
            )
            continue
        probability = _first_float(fill, ("p_model", "model_probability", "p_lcb"))
        entry_price = _first_float(fill, ("entry_price", "historical_entry_price", "paper_entry_price"))
        supplements.append(
            {
                "schema_version": HISTORICAL_EVIDENCE_SCHEMA_VERSION,
                "paper_only": True,
                "diagnostic_only": True,
                "counts_for_live_gate": False,
                "source": source,
                "market_slug": fill.get("market_slug"),
                "market_id": fill.get("market_id"),
                "token_id": fill.get("token_id"),
                "side": fill.get("side") or "yes",
                "strategy_id": fill.get("strategy_id"),
                "queue_name": fill.get("queue_name"),
                "city": fill.get("city"),
                "bucket_type": fill.get("bucket_type"),
                "available_at": _first_text(
                    fill,
                    ("available_at", "generated_at", "recorded_at", "orderbook_recorded_at"),
                ),
                "model_probability": probability,
                "p_lcb": _safe_float(fill.get("p_lcb")),
                "p_model": _safe_float(fill.get("p_model")),
                "entry_price": entry_price,
                "entry_price_source": "replay_taker_depth_walk",
                "orderbook_snapshot_id": fill.get("orderbook_snapshot_id"),
                "orderbook_recorded_at": fill.get("orderbook_recorded_at"),
                "requested_size": fill.get("requested_size"),
                "filled_size": fill.get("filled_size"),
                "fully_filled": fill.get("fully_filled"),
                "missed_fill": fill.get("missed_fill"),
                "q_effective": _safe_float(fill.get("q_effective")),
                "ev_safe": _safe_float(fill.get("ev_safe")),
            }
        )
    return {
        "schema_version": HISTORICAL_EVIDENCE_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "source": source,
        "input_fill_count": len(rows),
        "supplement_count": len(supplements),
        "gap_count": len(gaps),
        "gaps_by_reason": _count_by(gaps, "gap_reason", key_name="reason"),
        "supplements": supplements,
        "gap_samples": gaps[:20],
    }


def _count_by(records: Iterable[Dict[str, Any]], field: str, *, key_name: Optional[str] = None) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for record in records:
        value = _text(record.get(field)) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return [
        {key_name or field: value, "count": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def historical_evidence_from_replay_report(
    report: Dict[str, Any],
    *,
    source: str = "strict_gate_replay",
) -> Dict[str, Any]:
    replay = report.get("replay") if isinstance(report.get("replay"), dict) else {}
    fills = replay.get("fills") if isinstance(replay.get("fills"), list) else []
    result = historical_evidence_from_replay_fills(fills, source=source)
    result["replay_schema_version"] = report.get("schema_version")
    result["replay_time"] = report.get("replay_time") or replay.get("replay_time")
    result["replay_hard_conclusion"] = report.get("hard_conclusion")
    return result


def write_historical_evidence_supplements(
    report: Dict[str, Any],
    *,
    output_path: str | Path,
    source: str = "strict_gate_replay",
) -> Dict[str, Any]:
    evidence = historical_evidence_from_replay_report(report, source=source)
    written = _append_jsonl(Path(output_path), evidence.get("supplements") or [])
    return {
        "schema_version": HISTORICAL_EVIDENCE_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "output_path": str(output_path),
        "written_count": written,
        "supplement_count": evidence.get("supplement_count"),
        "gap_count": evidence.get("gap_count"),
        "gaps_by_reason": evidence.get("gaps_by_reason"),
    }


def load_historical_evidence_report(path: str | Path) -> Dict[str, Any]:
    rows = load_jsonl(path)
    return historical_evidence_from_replay_fills(rows, source="historical_evidence_jsonl")
