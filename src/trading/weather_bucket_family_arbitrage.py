from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = "polyweather_weather_bucket_family_arbitrage.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _leg(
    bucket: Dict[str, Any],
    *,
    side: str,
) -> Dict[str, Any]:
    prefix = "yes" if side.lower() == "yes" else "no"
    return {
        "market_slug": bucket.get("market_slug"),
        "bucket_type": bucket.get("bucket_type"),
        "threshold": bucket.get("threshold"),
        "side": prefix.upper(),
        "token_id": bucket.get(f"{prefix}_token_id"),
        "best_ask": _safe_float(bucket.get(f"{prefix}_best_ask")),
        "ask_depth": _safe_float(bucket.get(f"{prefix}_ask_depth")),
        "spread": _safe_float(bucket.get(f"{prefix}_spread")),
        "orderbook_snapshot_id": bucket.get(f"{prefix}_orderbook_snapshot_id"),
    }


def _basket_row(
    family: Dict[str, Any],
    *,
    strategy_id: str,
    side: str,
    min_edge_cents: float,
    min_leg_depth: float,
    cost_cents: float,
) -> Dict[str, Any]:
    buckets = [row for row in family.get("buckets") or [] if isinstance(row, dict)]
    legs = [_leg(bucket, side=side) for bucket in buckets]
    asks = [leg["best_ask"] for leg in legs if leg.get("best_ask") is not None]
    depths = [leg["ask_depth"] for leg in legs if leg.get("ask_depth") is not None]
    spreads = [leg["spread"] for leg in legs if leg.get("spread") is not None]
    missing_leg_count = len([leg for leg in legs if leg.get("best_ask") is None or leg.get("token_id") is None])
    depth_blocker_count = len([depth for depth in depths if depth < float(min_leg_depth)])
    total_cost = round(sum(float(value) for value in asks) + float(cost_cents) / 100.0, 8) if len(asks) == len(legs) else None
    worst_case_payout = 1.0 if strategy_id == "bucket_family_buy_all_yes" else max(0.0, float(len(legs) - 1))
    edge_cents = (
        round(100.0 * (float(worst_case_payout) - float(total_cost)), 6)
        if total_cost is not None
        else None
    )
    blockers: List[str] = []
    if not family.get("is_partition_candidate"):
        blockers.append("incomplete_partition")
    if missing_leg_count:
        blockers.append("missing_ask_or_token")
    if len(depths) != len(legs):
        blockers.append("missing_depth")
    if depth_blocker_count:
        blockers.append("depth_below_min")
    if edge_cents is None or edge_cents <= float(min_edge_cents):
        blockers.append("edge_below_min")
    candidate = not blockers
    return {
        "schema_version": "polyweather_bucket_family_basket_arbitrage.v1.row",
        "strategy_id": strategy_id,
        "event_slug": family.get("event_slug"),
        "station_code": family.get("station_code"),
        "target_date": family.get("target_date"),
        "settlement_source": family.get("settlement_source"),
        "bucket_count": len(legs),
        "legs": legs,
        "total_cost": total_cost,
        "worst_case_payout": worst_case_payout,
        "edge_cents": edge_cents,
        "min_leg_depth": round(min(depths), 8) if depths else None,
        "max_leg_spread": round(max(spreads), 8) if spreads else None,
        "missing_leg_count": missing_leg_count,
        "depth_blocker_count": depth_blocker_count,
        "candidate": candidate,
        "blockers": sorted(set(blockers)),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _monotonic_pair_rows(
    family: Dict[str, Any],
    *,
    min_edge_cents: float,
    min_leg_depth: float,
    cost_cents: float,
) -> List[Dict[str, Any]]:
    buckets = [row for row in family.get("buckets") or [] if isinstance(row, dict)]
    rows: List[Dict[str, Any]] = []
    for pair_type in ("ge", "le"):
        scoped = sorted(
            [row for row in buckets if row.get("bucket_type") == pair_type],
            key=lambda row: float(row.get("threshold") or 0.0),
        )
        for index, low in enumerate(scoped):
            for high in scoped[index + 1 :]:
                if pair_type == "ge":
                    superset = low
                    subset = high
                else:
                    superset = high
                    subset = low
                yes_leg = _leg(superset, side="yes")
                no_leg = _leg(subset, side="no")
                asks = [yes_leg.get("best_ask"), no_leg.get("best_ask")]
                depths = [yes_leg.get("ask_depth"), no_leg.get("ask_depth")]
                missing = len([value for value in asks if value is None]) + len(
                    [leg for leg in (yes_leg, no_leg) if not leg.get("token_id")]
                )
                depth_blocker = len([depth for depth in depths if depth is not None and depth < float(min_leg_depth)])
                total_cost = (
                    round(sum(float(value) for value in asks if value is not None) + float(cost_cents) / 100.0, 8)
                    if all(value is not None for value in asks)
                    else None
                )
                edge_cents = round(100.0 * (1.0 - float(total_cost)), 6) if total_cost is not None else None
                blockers: List[str] = []
                if missing:
                    blockers.append("missing_ask_or_token")
                if any(depth is None for depth in depths):
                    blockers.append("missing_depth")
                if depth_blocker:
                    blockers.append("depth_below_min")
                if edge_cents is None or edge_cents <= float(min_edge_cents):
                    blockers.append("edge_below_min")
                rows.append(
                    {
                        "schema_version": "polyweather_bucket_family_monotonic_pair.v1.row",
                        "strategy_id": "monotonic_threshold_pair",
                        "event_slug": family.get("event_slug"),
                        "station_code": family.get("station_code"),
                        "target_date": family.get("target_date"),
                        "settlement_source": family.get("settlement_source"),
                        "pair_type": pair_type,
                        "superset_market_slug": superset.get("market_slug"),
                        "subset_market_slug": subset.get("market_slug"),
                        "yes_superset_ask": yes_leg.get("best_ask"),
                        "no_subset_ask": no_leg.get("best_ask"),
                        "worst_case_payout": 1.0,
                        "total_cost": total_cost,
                        "edge_cents": edge_cents,
                        "depth": round(min([float(depth) for depth in depths if depth is not None]), 8) if all(depth is not None for depth in depths) else None,
                        "legs": [yes_leg, no_leg],
                        "candidate": not blockers,
                        "blockers": sorted(set(blockers)),
                        "paper_only": True,
                        "counts_for_live_gate": False,
                        "live_order_path": False,
                    }
                )
    return rows


def build_bucket_family_arbitrage_report(
    catalog: Dict[str, Any],
    *,
    min_edge_cents: float = 1.0,
    min_leg_depth: float = 1.0,
    cost_cents: float = 0.0,
    max_candidates: int = 20,
) -> Dict[str, Any]:
    families = [row for row in catalog.get("families") or [] if isinstance(row, dict)]
    basket_rows: List[Dict[str, Any]] = []
    monotonic_rows: List[Dict[str, Any]] = []
    blocker_counts: Counter[str] = Counter()
    for family in families:
        yes_row = _basket_row(
            family,
            strategy_id="bucket_family_buy_all_yes",
            side="yes",
            min_edge_cents=min_edge_cents,
            min_leg_depth=min_leg_depth,
            cost_cents=cost_cents,
        )
        no_row = _basket_row(
            family,
            strategy_id="bucket_family_buy_all_no",
            side="no",
            min_edge_cents=min_edge_cents,
            min_leg_depth=min_leg_depth,
            cost_cents=cost_cents,
        )
        basket_rows.extend([yes_row, no_row])
        monotonic_rows.extend(
            _monotonic_pair_rows(
                family,
                min_edge_cents=min_edge_cents,
                min_leg_depth=min_leg_depth,
                cost_cents=cost_cents,
            )
        )
    for row in basket_rows + monotonic_rows:
        if not row.get("candidate"):
            for blocker in row.get("blockers") or ["unknown"]:
                blocker_counts[str(blocker)] += 1
    candidates = [row for row in basket_rows + monotonic_rows if row.get("candidate")]
    candidates = sorted(candidates, key=lambda row: float(row.get("edge_cents") or -999999), reverse=True)
    top_near_misses = sorted(
        [row for row in basket_rows + monotonic_rows if not row.get("candidate")],
        key=lambda row: float(row.get("edge_cents") if row.get("edge_cents") is not None else -999999),
        reverse=True,
    )[:10]
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "family_count": len(families),
        "partition_family_count": len([row for row in families if row.get("is_partition_candidate")]),
        "basket_row_count": len(basket_rows),
        "buy_all_yes_candidate_count": len([row for row in candidates if row.get("strategy_id") == "bucket_family_buy_all_yes"]),
        "buy_all_no_candidate_count": len([row for row in candidates if row.get("strategy_id") == "bucket_family_buy_all_no"]),
        "monotonic_pair_count": len(monotonic_rows),
        "monotonic_pair_candidate_count": len([row for row in candidates if row.get("strategy_id") == "monotonic_threshold_pair"]),
        "candidate_count": len(candidates),
        "best_edge_cents": candidates[0].get("edge_cents") if candidates else None,
        "missing_leg_count": sum(int(row.get("missing_leg_count") or 0) for row in basket_rows),
        "no_candidate_blocker_counts": [{"blocker": key, "count": count} for key, count in sorted(blocker_counts.items())],
        "top_near_miss_baskets": top_near_misses,
        "basket_rows": basket_rows,
        "monotonic_rows": monotonic_rows,
        "candidates": candidates[: max(0, int(max_candidates))],
    }


def load_bucket_family_catalog(path: str | Path) -> Dict[str, Any]:
    return _load_json(path)


__all__ = ["SCHEMA_VERSION", "build_bucket_family_arbitrage_report", "load_bucket_family_catalog"]
