from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from src.trading.weather_paper_journal import (
    DEFAULT_PAPER_JOURNAL_DIR,
    _append_jsonl,
    _write_json_atomic,
    load_jsonl,
    stable_json_hash,
    utc_now_iso,
)


CURRENT_SIGNAL_SNAPSHOT_SCHEMA_VERSION = "polyweather_weather_current_signal_snapshot.v1"
DEFAULT_CURRENT_SIGNAL_REPORT_DIR = DEFAULT_PAPER_JOURNAL_DIR / "current_signal_reports"


def default_current_signal_report_dir(journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR) -> Path:
    return Path(journal_dir) / "current_signal_reports"


def _safe_summary_int(report: Dict[str, Any], field: str) -> int:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    try:
        return int(summary.get(field) or 0)
    except (TypeError, ValueError):
        return 0


def _snapshot_filename(generated_at: str, snapshot_id: str) -> str:
    safe_time = (
        str(generated_at or "")
        .replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace("Z", "Z")
    )
    safe_time = "".join(ch for ch in safe_time if ch.isalnum() or ch in {"_", "Z", "T"})
    return f"signal_report_{safe_time}_{snapshot_id}.json"


def write_current_signal_report(
    signal_report: Dict[str, Any],
    *,
    report_dir: str | Path = DEFAULT_CURRENT_SIGNAL_REPORT_DIR,
    generated_at: Optional[str] = None,
    source: str = "paper_cycle",
) -> Dict[str, Any]:
    if not isinstance(signal_report, dict):
        raise TypeError("signal_report must be a dict")
    generated_at = generated_at or str(signal_report.get("generated_at") or utc_now_iso())
    report_root = Path(report_dir)
    snapshot_id = stable_json_hash(
        {
            "generated_at": generated_at,
            "source": source,
            "signal_report": signal_report,
        },
        length=16,
    )
    snapshot_path = report_root / _snapshot_filename(generated_at, snapshot_id)
    latest_path = report_root / "latest_signal_report.json"
    payload = dict(signal_report)
    _write_json_atomic(snapshot_path, payload)
    _write_json_atomic(latest_path, payload)
    manifest_record = {
        "schema_version": CURRENT_SIGNAL_SNAPSHOT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": source,
        "snapshot_id": snapshot_id,
        "snapshot_path": str(snapshot_path),
        "latest_path": str(latest_path),
        "candidate_count": _safe_summary_int(payload, "candidate_count"),
        "watch_count": _safe_summary_int(payload, "watch_count"),
        "quarantine_count": _safe_summary_int(payload, "quarantine_count"),
        "reject_count": _safe_summary_int(payload, "reject_count"),
        "source_snapshot_id": payload.get("source_snapshot_id"),
        "paper_only": True,
        "counts_for_live_gate": False,
    }
    _append_jsonl(report_root / "manifest.jsonl", [manifest_record])
    return manifest_record


def load_latest_current_signal_report(
    *,
    report_dir: str | Path = DEFAULT_CURRENT_SIGNAL_REPORT_DIR,
) -> Optional[Dict[str, Any]]:
    report_root = Path(report_dir)
    latest_path = report_root / "latest_signal_report.json"
    if latest_path.exists():
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None

    manifest = load_jsonl(report_root / "manifest.jsonl")
    for row in reversed(manifest):
        if not isinstance(row, dict):
            continue
        snapshot_path = Path(str(row.get("snapshot_path") or ""))
        if not snapshot_path.exists():
            continue
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    return None
