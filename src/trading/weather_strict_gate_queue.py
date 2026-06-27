from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_paper_journal import (
    DEFAULT_PAPER_JOURNAL_DIR,
    _append_jsonl,
    _safe_float,
    _write_json_atomic,
    load_jsonl,
    stable_json_hash,
    utc_now_iso,
)


STRICT_GATE_QUEUE_SCHEMA_VERSION = "polyweather_weather_strict_gate_queue.v1"
DEFAULT_STRICT_GATE_QUEUE_DIR = DEFAULT_PAPER_JOURNAL_DIR / "strict_gate_queues"


def default_strict_gate_queue_dir(journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR) -> Path:
    return Path(journal_dir) / "strict_gate_queues"


def _strict_gate(signal_report: Dict[str, Any]) -> Dict[str, Any]:
    strict = signal_report.get("strict_gate_diagnostics")
    return strict if isinstance(strict, dict) else {}


def _queue_items(signal_report: Dict[str, Any]) -> Iterable[tuple[str, Dict[str, Any], Dict[str, Any]]]:
    strict = _strict_gate(signal_report)
    queues = strict.get("targeted_paper_queues")
    if not isinstance(queues, dict):
        return []
    rows: List[tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    for queue_name, queue in queues.items():
        if not isinstance(queue, dict):
            continue
        for item in queue.get("items") or []:
            if isinstance(item, dict):
                rows.append((str(queue_name), queue, item))
    return rows


def _record_from_queue_item(
    signal_report: Dict[str, Any],
    *,
    queue_name: str,
    queue: Dict[str, Any],
    item: Dict[str, Any],
    generated_at: str,
    source: str,
) -> Dict[str, Any]:
    queue_reasons = [str(reason) for reason in item.get("queue_reasons") or [] if str(reason)]
    record_id = stable_json_hash(
        {
            "source_snapshot_id": signal_report.get("source_snapshot_id"),
            "generated_at": generated_at,
            "queue_name": queue_name,
            "market_slug": item.get("market_slug"),
            "market_id": item.get("market_id"),
            "token_id": item.get("token_id"),
            "side": item.get("side"),
            "queue_reasons": sorted(queue_reasons),
        },
        length=24,
    )
    return {
        "schema_version": STRICT_GATE_QUEUE_SCHEMA_VERSION,
        "queue_record_id": record_id,
        "generated_at": generated_at,
        "source": source,
        "source_snapshot_id": signal_report.get("source_snapshot_id"),
        "source_status": signal_report.get("source_status"),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate_excluded": True,
        "diagnostic_only": True,
        "queue_name": queue_name,
        "queue_description": queue.get("description"),
        "queue_reasons": sorted(set(queue_reasons)),
        "decision": item.get("decision"),
        "market_family": item.get("market_family"),
        "city": item.get("city"),
        "question": item.get("question"),
        "market_id": item.get("market_id"),
        "market_slug": item.get("market_slug"),
        "token_id": item.get("token_id"),
        "side": item.get("side"),
        "outcome": item.get("outcome"),
        "bucket_label": item.get("bucket_label"),
        "bucket_type": item.get("bucket_type"),
        "strategy_id": item.get("strategy_id"),
        "execution_style": item.get("execution_style"),
        "strategy_live_eligible": item.get("strategy_live_eligible"),
        "price": _safe_float(item.get("price")),
        "bid": _safe_float(item.get("bid")),
        "ask": _safe_float(item.get("ask")),
        "spread": _safe_float(item.get("spread")),
        "liquidity": _safe_float(item.get("liquidity")),
        "bid_depth_usdc_3c": _safe_float(item.get("bid_depth_usdc_3c")),
        "ask_depth_usdc_3c": _safe_float(item.get("ask_depth_usdc_3c")),
        "edge_percent": _safe_float(item.get("edge_percent")),
        "model_probability": _safe_float(item.get("model_probability")),
        "market_probability": _safe_float(item.get("market_probability")),
        "p_lcb": _safe_float(item.get("p_lcb")),
        "q_effective": _safe_float(item.get("q_effective")),
        "cost": _safe_float(item.get("cost")),
        "ev_safe": _safe_float(item.get("ev_safe")),
        "score": _safe_float(item.get("score")),
        "non_risk_blockers": item.get("non_risk_blockers") or [],
        "non_risk_blocker_categories": item.get("non_risk_blocker_categories") or [],
        "risk_rule_hits": item.get("risk_rule_hits") or [],
        "risk_rule_scope_counts": item.get("risk_rule_scope_counts") or [],
        "settlement_rule_hash": item.get("settlement_rule_hash"),
        "settlement_station_code": item.get("settlement_station_code"),
        "settlement_source": item.get("settlement_source"),
    }


def build_strict_gate_queue_records(
    signal_report: Dict[str, Any],
    *,
    generated_at: Optional[str] = None,
    source: str = "paper_cycle",
    max_records_per_queue: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(signal_report, dict):
        raise TypeError("signal_report must be a dict")
    generated_at = generated_at or str(signal_report.get("generated_at") or utc_now_iso())
    per_queue_seen: Dict[str, int] = {}
    records: List[Dict[str, Any]] = []
    for queue_name, queue, item in _queue_items(signal_report):
        seen = per_queue_seen.get(queue_name, 0)
        if max_records_per_queue is not None and seen >= max(0, int(max_records_per_queue)):
            continue
        records.append(
            _record_from_queue_item(
                signal_report,
                queue_name=queue_name,
                queue=queue,
                item=item,
                generated_at=generated_at,
                source=source,
            )
        )
        per_queue_seen[queue_name] = seen + 1
    return records


def _queue_counts(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for record in records:
        queue_name = str(record.get("queue_name") or "unknown")
        counts[queue_name] = counts.get(queue_name, 0) + 1
    return [
        {"queue_name": queue_name, "count": count}
        for queue_name, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def write_strict_gate_queue_journal(
    signal_report: Dict[str, Any],
    *,
    queue_dir: str | Path = DEFAULT_STRICT_GATE_QUEUE_DIR,
    generated_at: Optional[str] = None,
    source: str = "paper_cycle",
    max_records_per_queue: Optional[int] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or str(signal_report.get("generated_at") or utc_now_iso())
    queue_root = Path(queue_dir)
    run_id = stable_json_hash(
        {
            "schema_version": STRICT_GATE_QUEUE_SCHEMA_VERSION,
            "generated_at": generated_at,
            "source": source,
            "source_snapshot_id": signal_report.get("source_snapshot_id"),
            "strict_gate": _strict_gate(signal_report),
        },
        length=20,
    )
    snapshot_path = queue_root / "snapshots" / f"{run_id}.json"
    records_path = queue_root / "strict_gate_queue.jsonl"
    manifest_path = queue_root / "manifest.jsonl"
    records = build_strict_gate_queue_records(
        signal_report,
        generated_at=generated_at,
        source=source,
        max_records_per_queue=max_records_per_queue,
    )
    _write_json_atomic(
        snapshot_path,
        {
            "schema_version": STRICT_GATE_QUEUE_SCHEMA_VERSION,
            "run_id": run_id,
            "generated_at": generated_at,
            "source": source,
            "signal_report": signal_report,
            "records": records,
        },
    )
    written = _append_jsonl(records_path, records)
    manifest_record = {
        "schema_version": STRICT_GATE_QUEUE_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "source": source,
        "source_snapshot_id": signal_report.get("source_snapshot_id"),
        "snapshot_path": str(snapshot_path),
        "records_path": str(records_path),
        "record_count": written,
        "queue_counts": _queue_counts(records),
        "paper_only": True,
        "counts_for_live_gate": False,
    }
    _append_jsonl(manifest_path, [manifest_record])
    return {
        **manifest_record,
        "queue_dir": str(queue_root),
        "manifest_path": str(manifest_path),
    }


def summarize_strict_gate_queue_journal(
    queue_dir: str | Path = DEFAULT_STRICT_GATE_QUEUE_DIR,
) -> Dict[str, Any]:
    queue_root = Path(queue_dir)
    records = load_jsonl(queue_root / "strict_gate_queue.jsonl")
    manifest = load_jsonl(queue_root / "manifest.jsonl")
    return {
        "schema_version": STRICT_GATE_QUEUE_SCHEMA_VERSION,
        "queue_dir": str(queue_root),
        "manifest_count": len(manifest),
        "record_count": len(records),
        "queue_counts": _queue_counts(records),
        "paper_only": True,
        "counts_for_live_gate": False,
    }


def strict_gate_queue_records_as_replay_candidates(
    records: Iterable[Dict[str, Any]],
    *,
    queue_names: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    allowed = (
        {
            str(queue_name or "").strip()
            for queue_name in queue_names
            if str(queue_name or "").strip()
        }
        if queue_names is not None
        else None
    )
    candidates: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if allowed is not None and str(record.get("queue_name") or "") not in allowed:
            continue
        if not str(record.get("token_id") or "").strip():
            continue
        candidates.append(
            {
                **record,
                "available_at": record.get("generated_at"),
                "row_id": record.get("queue_record_id"),
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_gate_excluded": True,
            }
        )
    return candidates
