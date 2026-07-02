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
    by_minute: Dict[int, Dict[str, Any]] = defaultdict(
        lambda: {
            "reward_metadata_available_count": 0,
            "reward_qualified_quote_count": 0,
            "paper_quote_count": 0,
            "reward_points_proxy": 0.0,
            "observation_count": 0,
        }
    )
    support = 0
    outside_support = 0
    metadata_available_total = 0
    qualified_total = 0
    for row in rows:
        minute = row.get("minute_of_hour")
        if minute is None:
            minute = _minute(row.get("generated_at"))
        if minute is None:
            continue
        metadata_count = int(row.get("reward_metadata_available_count") or row.get("reward_available_count") or row.get("reward_market_count") or 0)
        qualified_count = int(row.get("reward_qualified_quote_count") or 0)
        paper_quote_count = int(row.get("paper_quote_count") or row.get("paper_quote_candidate_count") or 0)
        reward_points = float(row.get("mean_reward_points_proxy") or row.get("reward_points_proxy") or 0.0)
        by_minute[int(minute)]["reward_metadata_available_count"] += metadata_count
        by_minute[int(minute)]["reward_qualified_quote_count"] += qualified_count
        by_minute[int(minute)]["paper_quote_count"] += paper_quote_count
        by_minute[int(minute)]["reward_points_proxy"] += reward_points
        by_minute[int(minute)]["observation_count"] += 1
        metadata_available_total += metadata_count
        qualified_total += qualified_count
        if 40 <= int(minute) <= 51 and qualified_count > 0:
            support += 1
        elif qualified_count > 0:
            outside_support += 1
    if len(rows) < 12:
        recommendation = "insufficient_observations"
        confidence = "insufficient_observations"
    elif metadata_available_total <= 0:
        recommendation = "reward_metadata_unavailable"
        confidence = "reward_metadata_unavailable"
    elif support > 0 and outside_support == 0:
        recommendation = "40_51_window_supported"
        confidence = "40_51_window_supported"
    elif support > 0 and outside_support > 0:
        recommendation = "reward_always_available"
        confidence = "reward_always_available"
    else:
        recommendation = "40_51_window_not_supported"
        confidence = "40_51_window_not_supported"
    return {
        "schema_version": SCHEMA_VERSION,
        "observation_count": len(rows),
        "observations_count": len(rows),
        "reward_metadata_available_observation_total": metadata_available_total,
        "reward_qualified_quote_observation_total": qualified_total,
        "by_minute_of_hour": [
            {
                "minute": key,
                **value,
                "mean_reward_points_proxy": round(float(value["reward_points_proxy"]) / max(1, int(value["observation_count"])), 8),
            }
            for key, value in sorted(by_minute.items())
        ],
        "reward_by_minute_of_hour": [
            {"minute": key, "reward_available_count": value["reward_metadata_available_count"]}
            for key, value in sorted(by_minute.items())
        ],
        "candidate_reward_window": "40-51" if support > 0 else None,
        "40_to_51_minute_support_count": support,
        "outside_window_support_count": outside_support,
        "hour_boundary_cancel_recommendation": "unvalidated_cancel_at_hour_boundary" if confidence == "insufficient_observations" else "consider_cancel_at_hour_boundary",
        "window_confidence": confidence,
        "confidence": confidence,
        "recommendation": recommendation,
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
