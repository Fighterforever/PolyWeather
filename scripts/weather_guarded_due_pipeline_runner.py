#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIRM_TOKEN = "PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH"
SCHEMA_VERSION = "polyweather_guarded_due_pipeline_runner.v1"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _derive_pending_output(summary_output: Path) -> Path:
    if summary_output.name.endswith("_after_due.json"):
        return summary_output.with_name(summary_output.name.replace("_after_due.json", "_pending.json"))
    return summary_output.with_name(f"{summary_output.stem}_pending{summary_output.suffix or '.json'}")


def _derive_failure_output(summary_output: Path) -> Path:
    return summary_output.with_name(f"{summary_output.stem}_failure{summary_output.suffix or '.json'}")


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _lock_created_at(lock_file: Path) -> Optional[datetime]:
    try:
        payload = json.loads(lock_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("created_at_utc"):
        return None
    try:
        return _parse_utc(str(payload.get("created_at_utc")))
    except ValueError:
        return None


def _lock_is_active(lock_file: Path, *, now: datetime, ttl_seconds: int) -> bool:
    if not lock_file.exists():
        return False
    created_at = _lock_created_at(lock_file)
    if created_at is None:
        return True
    return now - created_at < timedelta(seconds=max(1, int(ttl_seconds)))


def _acquire_lock(lock_file: Path, *, now: datetime, ttl_seconds: int) -> bool:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    if lock_file.exists() and not _lock_is_active(lock_file, now=now, ttl_seconds=ttl_seconds):
        lock_file.unlink()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "pid": os.getpid(),
        "created_at_utc": _iso(now),
        "expires_at_utc": _iso(now + timedelta(seconds=max(1, int(ttl_seconds)))),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(lock_file, flags)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return True


def _pipeline_command(args: argparse.Namespace, *, generated_at: str) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "weather_run_due_evidence_pipeline.py"),
        "--paper-journal-dir",
        str(args.paper_journal_dir),
        "--strict-gate-queue-dir",
        str(args.strict_gate_queue_dir),
        "--orderbook-archive-dir",
        str(args.orderbook_archive_dir),
        "--backfill-dir",
        str(args.backfill_dir),
        "--generated-at",
        generated_at,
        "--replay-time",
        generated_at,
        "--execute-closed-backfill",
        "--confirm",
        str(args.confirm),
        "--summary-output",
        str(args.summary_output),
    ]
    for source in args.include_settlement_sources or []:
        command.extend(["--include-settlement-source", str(source)])
    for station in args.include_station_codes or []:
        command.extend(["--include-station-code", str(station)])
    if args.allow_partial_official_truth:
        command.append("--allow-partial-official-truth")
    if args.fetch_external_official_values:
        command.append("--fetch-external-official-values")
    return command


def _summary_base(args: argparse.Namespace, *, now: datetime, status: str) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "status": status,
        "now_utc": _iso(now),
        "run_after_utc": args.run_after_utc,
        "summary_output": str(args.summary_output),
        "strict_gate_queue_dir": str(args.strict_gate_queue_dir),
        "lock_file": str(args.lock_file),
        "done_file": str(args.done_file),
        "include_settlement_source": list(args.include_settlement_sources or []),
        "include_station_code": list(args.include_station_codes or []),
        "include_market_slug": list(args.include_market_slugs or []),
    }


def _validate_summary_no_unresolved_zero_pnl(summary_output: Path) -> Optional[str]:
    try:
        summary = json.loads(summary_output.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"summary_read_error:{exc}"
    strict_replay = summary.get("strict_replay") if isinstance(summary.get("strict_replay"), dict) else {}
    if int(strict_replay.get("resolved_fill_count") or 0) == 0 and strict_replay.get("resolved_pnl_cents") in {0, 0.0}:
        return "unresolved_pnl_cents_must_be_null_not_zero"
    if summary.get("live_order_path") is not False:
        return "live_order_path_must_remain_false"
    return None


def run_guarded_due_pipeline(
    args: argparse.Namespace,
    *,
    subprocess_run=subprocess.run,
) -> int:
    now = _parse_utc(args.now_utc) if args.now_utc else _utc_now()
    run_after = _parse_utc(args.run_after_utc)
    args.summary_output = Path(args.summary_output)
    args.pending_output = Path(args.pending_output) if args.pending_output else _derive_pending_output(args.summary_output)
    args.failure_output = Path(args.failure_output) if args.failure_output else _derive_failure_output(args.summary_output)
    args.lock_file = Path(args.lock_file)
    args.done_file = Path(args.done_file)
    args.backfill_dir = Path(args.backfill_dir)
    args.backfill_dir.mkdir(parents=True, exist_ok=True)

    if now < run_after:
        payload = _summary_base(args, now=now, status="pending")
        payload["seconds_until_run"] = max(0, int((run_after - now).total_seconds()))
        _write_json(args.pending_output, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.done_file.exists():
        payload = _summary_base(args, now=now, status="already_done")
        payload["done_file_exists"] = True
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if _lock_is_active(args.lock_file, now=now, ttl_seconds=args.lock_ttl_seconds):
        payload = _summary_base(args, now=now, status="already_running")
        payload["lock_file_exists"] = True
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.check_only:
        command = _pipeline_command(args, generated_at=_iso(now))
        payload = _summary_base(args, now=now, status="ready_to_run")
        payload["command_preview"] = command
        _write_json(args.pending_output, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if not _acquire_lock(args.lock_file, now=now, ttl_seconds=args.lock_ttl_seconds):
        payload = _summary_base(args, now=now, status="already_running")
        payload["lock_file_exists"] = True
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    command = _pipeline_command(args, generated_at=_iso(now))
    try:
        result = subprocess_run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            payload = _summary_base(args, now=now, status="pipeline_failed")
            payload.update(
                {
                    "returncode": result.returncode,
                    "command": command,
                    "stdout_tail": _tail(result.stdout or ""),
                    "stderr_tail": _tail(result.stderr or ""),
                }
            )
            _write_json(args.failure_output, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return result.returncode or 1
        if not args.summary_output.exists():
            payload = _summary_base(args, now=now, status="pipeline_failed")
            payload.update(
                {
                    "returncode": 1,
                    "command": command,
                    "failure_reason": "summary_output_missing",
                    "stdout_tail": _tail(result.stdout or ""),
                    "stderr_tail": _tail(result.stderr or ""),
                }
            )
            _write_json(args.failure_output, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 1
        validation_error = _validate_summary_no_unresolved_zero_pnl(args.summary_output)
        if validation_error:
            payload = _summary_base(args, now=now, status="pipeline_failed")
            payload.update(
                {
                    "returncode": 1,
                    "command": command,
                    "failure_reason": validation_error,
                    "stdout_tail": _tail(result.stdout or ""),
                    "stderr_tail": _tail(result.stderr or ""),
                }
            )
            _write_json(args.failure_output, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 1
        args.done_file.parent.mkdir(parents=True, exist_ok=True)
        done_payload = _summary_base(args, now=now, status="completed")
        done_payload.update(
            {
                "completed_at_utc": _iso(_utc_now()),
                "command": command,
                "summary_output_exists": True,
            }
        )
        _write_json(args.done_file, done_payload)
        print(json.dumps(done_payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        try:
            args.lock_file.unlink()
        except FileNotFoundError:
            pass


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Guarded paper-only Polymarket weather due evidence runner.")
    parser.add_argument("--run-after-utc", required=True)
    parser.add_argument("--paper-journal-dir", required=True)
    parser.add_argument("--strict-gate-queue-dir", required=True)
    parser.add_argument("--orderbook-archive-dir", required=True)
    parser.add_argument("--backfill-dir", required=True)
    parser.add_argument("--summary-output", required=True)
    parser.add_argument("--pending-output", default=None)
    parser.add_argument("--failure-output", default=None)
    parser.add_argument("--lock-file", required=True)
    parser.add_argument("--done-file", required=True)
    parser.add_argument("--include-station-code", action="append", dest="include_station_codes", default=[])
    parser.add_argument("--include-settlement-source", action="append", dest="include_settlement_sources", default=[])
    parser.add_argument("--include-market-slug", action="append", dest="include_market_slugs", default=[])
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--allow-partial-official-truth", action="store_true")
    parser.add_argument("--fetch-external-official-values", action="store_true")
    parser.add_argument("--lock-ttl-seconds", type=int, default=7200)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--now-utc", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    raise SystemExit(run_guarded_due_pipeline(parse_args(argv)))


if __name__ == "__main__":
    main()
