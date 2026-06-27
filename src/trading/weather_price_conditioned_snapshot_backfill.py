from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR, _parse_utc_iso, write_paper_journal
from src.trading.weather_quality_surface import DEFAULT_PRICE_CONDITIONED_JOURNAL_DIR


PRICE_CONDITIONED_SNAPSHOT_BACKFILL_SCHEMA_VERSION = "polyweather_price_conditioned_snapshot_backfill.v1"
DEFAULT_PRICE_CONDITIONED_SNAPSHOT_BACKFILL_DIR = Path("data/trading/weather_price_conditioned_snapshot_backfill")


def _snapshot_sort_key(row: Tuple[Path, Dict[str, Any]]) -> Tuple[str, str]:
    path, payload = row
    return (str(payload.get("recorded_at") or ""), str(path))


def _load_snapshot_payloads(source_journal_dir: str | Path) -> List[Tuple[Path, Dict[str, Any]]]:
    root = Path(source_journal_dir)
    rows: List[Tuple[Path, Dict[str, Any]]] = []
    for path in sorted((root / "snapshots").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            rows.append((path, payload))
    return sorted(rows, key=_snapshot_sort_key)


def _report_items(report: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for bucket in ("candidates", "watch", "quarantine"):
        for item in report.get(bucket) or []:
            if isinstance(item, dict):
                yield item


def _annotate_snapshot_report(
    report: Dict[str, Any],
    *,
    snapshot_path: Path,
    source_run_id: Any,
    source_recorded_at: Any,
) -> Dict[str, Any]:
    annotated = copy.deepcopy(report)
    annotated["paper_backfill_from_snapshot"] = True
    annotated["counts_for_live_gate"] = False
    for bucket in ("candidates", "watch", "quarantine"):
        items = annotated.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item["paper_backfill_from_snapshot"] = True
            item["backfill_source_snapshot_path"] = str(snapshot_path)
            item["backfill_source_run_id"] = source_run_id
            item["backfill_source_recorded_at"] = source_recorded_at
            item["counts_for_live_gate"] = False
            item["live_gate_excluded"] = True
    summary = annotated.get("summary") if isinstance(annotated.get("summary"), dict) else {}
    summary["paper_backfill_from_snapshot"] = True
    summary["counts_for_live_gate"] = False
    summary["live_gate"] = False
    summary["live_authorization_pct"] = 0
    annotated["summary"] = summary
    return annotated


def replay_price_conditioned_snapshots_to_journal(
    *,
    source_journal_dir: str | Path = DEFAULT_PRICE_CONDITIONED_JOURNAL_DIR,
    target_journal_dir: str | Path = DEFAULT_PRICE_CONDITIONED_SNAPSHOT_BACKFILL_DIR,
    sample_interval_minutes: float = 60.0,
    max_snapshots: Optional[int] = None,
    profile: str = "price-conditioned-snapshot-backfill",
) -> Dict[str, Any]:
    snapshots = _load_snapshot_payloads(source_journal_dir)
    if max_snapshots is not None:
        snapshots = snapshots[: max(0, int(max_snapshots))]
    min_reentry_seconds = int(max(0.0, float(sample_interval_minutes)) * 60)
    writes: List[Dict[str, Any]] = []
    skipped_no_report = 0
    skipped_no_items = 0
    skipped_bad_time = 0
    for snapshot_path, payload in snapshots:
        report = payload.get("report") if isinstance(payload.get("report"), dict) else None
        if not isinstance(report, dict):
            skipped_no_report += 1
            continue
        if not list(_report_items(report)):
            skipped_no_items += 1
            continue
        recorded_at = payload.get("recorded_at")
        if _parse_utc_iso(recorded_at) is None:
            skipped_bad_time += 1
            continue
        annotated = _annotate_snapshot_report(
            report,
            snapshot_path=snapshot_path,
            source_run_id=payload.get("run_id"),
            source_recorded_at=recorded_at,
        )
        writes.append(
            write_paper_journal(
                annotated,
                journal_dir=target_journal_dir,
                profile=profile,
                include_candidates=True,
                include_watch=True,
                include_quarantine=True,
                max_fills=None,
                recorded_at=str(recorded_at),
                min_reentry_seconds=min_reentry_seconds,
            )
        )
    fill_count = sum(int(row.get("fill_count") or 0) for row in writes)
    duplicate_skipped_count = sum(int(row.get("duplicate_skipped_count") or 0) for row in writes)
    return {
        "schema_version": PRICE_CONDITIONED_SNAPSHOT_BACKFILL_SCHEMA_VERSION,
        "source_journal_dir": str(source_journal_dir),
        "target_journal_dir": str(target_journal_dir),
        "profile": profile,
        "paper_only": True,
        "counts_for_live_gate": False,
        "sample_interval_minutes": float(sample_interval_minutes),
        "min_reentry_seconds": min_reentry_seconds,
        "snapshot_count": len(snapshots),
        "write_attempt_count": len(writes),
        "fill_count": fill_count,
        "duplicate_skipped_count": duplicate_skipped_count,
        "skipped_no_report_count": skipped_no_report,
        "skipped_no_items_count": skipped_no_items,
        "skipped_bad_time_count": skipped_bad_time,
        "writes": writes,
    }


def dump_snapshot_backfill_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
