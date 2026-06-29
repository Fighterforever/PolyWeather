from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_reward_window.v1"


def _minute(value: Any) -> Optional[int]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed.astimezone(timezone.utc).minute


def build_weather_lp_reward_window_report(observations: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [row for row in observations if isinstance(row, dict)]
    by_minute: Dict[int, int] = defaultdict(int)
    support = 0
    for row in rows:
        minute = row.get("minute_of_hour")
        if minute is None:
            minute = _minute(row.get("generated_at"))
        if minute is None:
            continue
        count = int(row.get("reward_available_count") or row.get("reward_market_count") or 0)
        by_minute[int(minute)] += count
        if 40 <= int(minute) <= 51 and count > 0:
            support += 1
    confidence = "insufficient_window_observations" if len(rows) < 12 else "observed"
    return {
        "schema_version": SCHEMA_VERSION,
        "observations_count": len(rows),
        "reward_by_minute_of_hour": [{"minute": key, "reward_available_count": value} for key, value in sorted(by_minute.items())],
        "candidate_reward_window": "40-51" if support > 0 else None,
        "40_to_51_minute_support_count": support,
        "hour_boundary_cancel_recommendation": "unvalidated_cancel_at_hour_boundary" if confidence == "insufficient_window_observations" else "consider_cancel_at_hour_boundary",
        "confidence": confidence,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
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


__all__ = ["SCHEMA_VERSION", "build_weather_lp_reward_window_report", "load_jsonl", "write_json", "write_jsonl"]
