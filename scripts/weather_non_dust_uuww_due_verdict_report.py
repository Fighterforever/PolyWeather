#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_RUN_AFTER = "2026-06-30T03:05:00Z"
DEFAULT_AFTER_DUE = Path("evidence/due_pipeline_non_dust_uuww_after_due.json")
DEFAULT_PENDING = Path("evidence/due_pipeline_non_dust_uuww_pending.json")
DEFAULT_OUTPUT = Path("evidence/non_dust_uuww_due_verdict.json")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    try:
        parsed = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nested(report: Dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = report
        found = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                found = False
                break
            current = current[part]
        if found:
            return current
    return None


def _alpha_conclusion(*, resolved_pnl: Optional[float], resolved_fill_count: Optional[int], after_due_exists: bool, due_reached: bool) -> str:
    if not due_reached or not after_due_exists:
        return "non_dust_threshold_cdf_waiting_resolution"
    if not resolved_fill_count:
        return "non_dust_threshold_cdf_waiting_resolution"
    if resolved_pnl is None:
        return "non_dust_threshold_cdf_waiting_resolution"
    if resolved_pnl < 0:
        return "non_dust_threshold_cdf_failed"
    if resolved_pnl > 0:
        return "non_dust_threshold_cdf_positive_single_sample_needs_more_forward"
    return "non_dust_threshold_cdf_waiting_resolution"


def build_non_dust_uuww_due_verdict(
    *,
    run_after_utc: str = DEFAULT_RUN_AFTER,
    after_due_summary_path: str | Path = DEFAULT_AFTER_DUE,
    pending_summary_path: str | Path = DEFAULT_PENDING,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or _utc_now_iso()
    now_dt = _parse_utc(generated_at)
    run_after_dt = _parse_utc(run_after_utc)
    due_reached = bool(now_dt is not None and run_after_dt is not None and now_dt >= run_after_dt)
    seconds_until_run = None
    if now_dt is not None and run_after_dt is not None and now_dt < run_after_dt:
        seconds_until_run = int((run_after_dt - now_dt).total_seconds())

    after_path = Path(after_due_summary_path)
    pending_path = Path(pending_summary_path)
    after = _load_json(after_path)
    pending = _load_json(pending_path)
    source = after if after_path.exists() else pending

    resolved_pnl = _safe_float(
        _nested(
            source,
            "strict_replay.resolved_pnl_cents",
            "strict_replay_summary.resolved_pnl_cents",
            "summary.strict_replay.resolved_pnl_cents",
            "resolved_pnl_cents",
        )
    )
    resolved_fill_count = _safe_int(
        _nested(
            source,
            "strict_replay.resolved_fill_count",
            "strict_replay_summary.resolved_fill_count",
            "summary.strict_replay.resolved_fill_count",
            "resolved_fill_count",
        )
    )
    fill_count = _safe_int(
        _nested(
            source,
            "strict_replay.fill_count",
            "strict_replay_summary.fill_count",
            "summary.strict_replay.fill_count",
            "fill_count",
        )
    )
    matched = _safe_int(
        _nested(
            source,
            "after.matched_closed_archived_token_count",
            "closed_backfill.after.matched_closed_archived_token_count",
            "matched_closed_archived_token_count",
        )
    )
    probability_samples = _safe_int(
        _nested(
            source,
            "settlement_calibration.probability_score_sample_count",
            "settlement_calibration_summary.probability_score_sample_count",
            "summary.settlement_calibration.probability_score_sample_count",
        )
    )
    resolved_pnl_samples = _safe_int(
        _nested(
            source,
            "settlement_calibration.resolved_pnl_sample_count",
            "settlement_calibration_summary.resolved_pnl_sample_count",
            "summary.settlement_calibration.resolved_pnl_sample_count",
        )
    )

    conclusion = _alpha_conclusion(
        resolved_pnl=resolved_pnl,
        resolved_fill_count=resolved_fill_count,
        after_due_exists=after_path.exists(),
        due_reached=due_reached,
    )
    return {
        "schema_version": "polyweather_non_dust_uuww_due_verdict.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "run_after_utc": run_after_utc,
        "due_reached": due_reached,
        "seconds_until_run": seconds_until_run,
        "status": "after_due_artifact_ready" if after_path.exists() else ("due_reached_missing_artifact" if due_reached else "waiting_due"),
        "target_market_slug": "highest-temperature-in-moscow-on-june-29-2026-23corhigher",
        "station_code": "UUWW",
        "settlement_source": "metar",
        "price_bucket": "price_ge_0_03",
        "after_due_summary_path": str(after_path),
        "after_due_summary_exists": after_path.exists(),
        "pending_summary_path": str(pending_path),
        "pending_summary_exists": pending_path.exists(),
        "closed_backfill_executed": bool(_nested(source, "closed_backfill_executed") or _nested(source, "summary.closed_backfill_executed") or False),
        "matched_closed_archived_token_count": matched,
        "strict_replay": {
            "fill_count": fill_count,
            "resolved_fill_count": resolved_fill_count,
            "resolved_pnl_cents": resolved_pnl,
            "brier_score": _safe_float(_nested(source, "strict_replay.brier_score", "strict_replay_summary.brier_score")),
            "log_loss": _safe_float(_nested(source, "strict_replay.log_loss", "strict_replay_summary.log_loss")),
        },
        "settlement_calibration": {
            "probability_score_sample_count": probability_samples,
            "resolved_pnl_sample_count": resolved_pnl_samples,
        },
        "alpha_conclusion": conclusion,
        "unresolved_pnl_semantics": "resolved_pnl_cents_null_until_resolved",
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only non-dust UUWW due verdict.")
    parser.add_argument("--run-after-utc", default=DEFAULT_RUN_AFTER)
    parser.add_argument("--after-due-summary", default=str(DEFAULT_AFTER_DUE))
    parser.add_argument("--pending-summary", default=str(DEFAULT_PENDING))
    parser.add_argument("--summary-output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    report = build_non_dust_uuww_due_verdict(
        run_after_utc=args.run_after_utc,
        after_due_summary_path=args.after_due_summary,
        pending_summary_path=args.pending_summary,
        generated_at=args.generated_at,
    )
    output = Path(args.summary_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
