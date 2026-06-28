from __future__ import annotations

import itertools
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np


SCHEMA_VERSION = "polyweather_weather_bucket_family_lp_arbitrage.v1"
STRATEGY_ID = "bucket_family_payoff_matrix_arbitrage"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _leg_from_bucket(bucket: Dict[str, Any], *, side: str, outcome_index: int, state_count: int) -> Dict[str, Any]:
    prefix = "yes" if side.lower() == "yes" else "no"
    payoff = [0.0] * state_count
    for index in range(state_count):
        if prefix == "yes":
            payoff[index] = 1.0 if index == outcome_index else 0.0
        else:
            payoff[index] = 0.0 if index == outcome_index else 1.0
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
        "payoff_vector": payoff,
        "outcome_index": outcome_index,
    }


def _state_rows(family: Dict[str, Any]) -> List[Dict[str, Any]]:
    states: List[Dict[str, Any]] = []
    for index, bucket in enumerate([row for row in family.get("buckets") or [] if isinstance(row, dict)]):
        states.append(
            {
                "outcome_index": index,
                "market_slug": bucket.get("market_slug"),
                "bucket_type": bucket.get("bucket_type"),
                "threshold": bucket.get("threshold"),
                "bucket_label": bucket.get("bucket_label"),
            }
        )
    return states


def _tradable_legs(
    family: Dict[str, Any],
    *,
    min_leg_depth: float,
) -> tuple[List[Dict[str, Any]], int, Dict[str, int]]:
    buckets = [row for row in family.get("buckets") or [] if isinstance(row, dict)]
    state_count = len(buckets)
    legs: List[Dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    missing_leg_count = 0
    for index, bucket in enumerate(buckets):
        for side in ("yes", "no"):
            leg = _leg_from_bucket(bucket, side=side, outcome_index=index, state_count=state_count)
            reasons: List[str] = []
            if not leg.get("token_id"):
                reasons.append("missing_token")
            if leg.get("best_ask") is None:
                reasons.append("missing_ask")
            if leg.get("ask_depth") is None:
                reasons.append("missing_depth")
            elif float(leg["ask_depth"]) < float(min_leg_depth):
                reasons.append("depth_below_min")
            if reasons:
                missing_leg_count += 1
                for reason in reasons:
                    reason_counts[reason] += 1
                continue
            legs.append(leg)
    return legs, missing_leg_count, dict(sorted(reason_counts.items()))


def _payoff_matrix(legs: Sequence[Dict[str, Any]], state_count: int) -> np.ndarray:
    if not legs:
        return np.zeros((state_count, 0), dtype=float)
    return np.array([leg["payoff_vector"] for leg in legs], dtype=float).T


def _solution_from_quantities(
    *,
    family: Dict[str, Any],
    states: List[Dict[str, Any]],
    legs: Sequence[Dict[str, Any]],
    quantities: Sequence[float],
    solver_method: str,
    missing_leg_count: int,
    cost_cents: float,
) -> Optional[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    payoff = [0.0] * len(states)
    raw_total_cost = 0.0
    for leg, quantity in zip(legs, quantities):
        qty = 0.0 if abs(float(quantity)) < 1e-9 else float(quantity)
        if qty <= 0:
            continue
        ask = _safe_float(leg.get("best_ask"))
        if ask is None:
            return None
        leg_cost = float(ask) * qty
        raw_total_cost += leg_cost
        for index, value in enumerate(leg.get("payoff_vector") or []):
            payoff[index] += float(value) * qty
        selected.append(
            {
                "market_slug": leg.get("market_slug"),
                "bucket_type": leg.get("bucket_type"),
                "threshold": leg.get("threshold"),
                "side": leg.get("side"),
                "token_id": leg.get("token_id"),
                "best_ask": ask,
                "quantity": round(qty, 10),
                "leg_cost": round(leg_cost, 10),
                "ask_depth": leg.get("ask_depth"),
                "spread": leg.get("spread"),
                "orderbook_snapshot_id": leg.get("orderbook_snapshot_id"),
                "payoff_vector": leg.get("payoff_vector"),
            }
        )
    if not selected:
        return None
    worst_case_payout = min(payoff) if payoff else 0.0
    if worst_case_payout < 1.0 - 1e-7:
        return None
    total_cost = raw_total_cost + float(cost_cents) / 100.0
    outcome_payoff_vector = [
        {
            **state,
            "payout": round(float(payoff[index]), 10),
        }
        for index, state in enumerate(states)
    ]
    depths = [_safe_float(row.get("ask_depth")) for row in selected]
    return {
        "schema_version": "polyweather_bucket_family_lp_arbitrage_solution.v1",
        "strategy_id": STRATEGY_ID,
        "event_slug": family.get("event_slug"),
        "station_code": family.get("station_code"),
        "target_date": family.get("target_date"),
        "settlement_source": family.get("settlement_source"),
        "solver_method": solver_method,
        "leg_count": len(selected),
        "bucket_count": len(states),
        "legs": selected,
        "raw_total_cost": round(raw_total_cost, 10),
        "cost_cents": float(cost_cents),
        "total_cost": round(total_cost, 10),
        "worst_case_payout": round(float(worst_case_payout), 10),
        "edge_cents": round(100.0 * (float(worst_case_payout) - float(total_cost)), 6),
        "min_leg_depth": round(min(depth for depth in depths if depth is not None), 8) if all(depth is not None for depth in depths) else None,
        "missing_leg_count": int(missing_leg_count),
        "outcome_payoff_vector": outcome_payoff_vector,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _better_solution(current: Optional[Dict[str, Any]], candidate: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if candidate is None:
        return current
    if current is None:
        return candidate
    current_cost = float(current.get("total_cost") or 1e9)
    candidate_cost = float(candidate.get("total_cost") or 1e9)
    if candidate_cost < current_cost - 1e-9:
        return candidate
    if abs(candidate_cost - current_cost) <= 1e-9 and int(candidate.get("leg_count") or 0) < int(current.get("leg_count") or 0):
        return candidate
    return current


def _active_set_solution(
    *,
    family: Dict[str, Any],
    states: List[Dict[str, Any]],
    legs: List[Dict[str, Any]],
    missing_leg_count: int,
    cost_cents: float,
    max_enumerations: int,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    state_count = len(states)
    variable_count = len(legs)
    payoff = _payoff_matrix(legs, state_count)
    best: Optional[Dict[str, Any]] = None
    enumerated = 0
    skipped = 0
    max_m = min(state_count, variable_count)
    for active_count in range(1, max_m + 1):
        estimate = math.comb(variable_count, active_count) * math.comb(state_count, active_count)
        if enumerated + estimate > int(max_enumerations):
            skipped += estimate
            continue
        for variable_indexes in itertools.combinations(range(variable_count), active_count):
            sub_payoff = payoff[:, variable_indexes]
            for state_indexes in itertools.combinations(range(state_count), active_count):
                active_matrix = sub_payoff[list(state_indexes), :]
                if np.linalg.matrix_rank(active_matrix) < active_count:
                    continue
                try:
                    quantities = np.linalg.solve(active_matrix, np.ones(active_count))
                except np.linalg.LinAlgError:
                    continue
                enumerated += 1
                if np.any(quantities < -1e-8):
                    continue
                full_quantities = [0.0] * variable_count
                for index, quantity in zip(variable_indexes, quantities):
                    full_quantities[index] = max(0.0, float(quantity))
                solution = _solution_from_quantities(
                    family=family,
                    states=states,
                    legs=legs,
                    quantities=full_quantities,
                    solver_method="active_set_bruteforce",
                    missing_leg_count=missing_leg_count,
                    cost_cents=cost_cents,
                )
                best = _better_solution(best, solution)
    return best, {
        "active_set_enumerated_count": enumerated,
        "active_set_skipped_estimate_count": skipped,
        "active_set_max_enumerations": int(max_enumerations),
    }


def _structured_fallback_solutions(
    *,
    family: Dict[str, Any],
    states: List[Dict[str, Any]],
    legs: List[Dict[str, Any]],
    missing_leg_count: int,
    cost_cents: float,
) -> List[Dict[str, Any]]:
    solutions: List[Dict[str, Any]] = []
    state_count = len(states)
    yes_by_state = {int(leg["outcome_index"]): index for index, leg in enumerate(legs) if leg.get("side") == "YES"}
    no_by_state = {int(leg["outcome_index"]): index for index, leg in enumerate(legs) if leg.get("side") == "NO"}

    def emit(name: str, quantities: List[float]) -> None:
        solution = _solution_from_quantities(
            family=family,
            states=states,
            legs=legs,
            quantities=quantities,
            solver_method=name,
            missing_leg_count=missing_leg_count,
            cost_cents=cost_cents,
        )
        if solution is not None:
            solutions.append(solution)

    if len(yes_by_state) == state_count:
        quantities = [0.0] * len(legs)
        for index in yes_by_state.values():
            quantities[index] = 1.0
        emit("structured_buy_all_yes", quantities)

    if state_count > 1 and len(no_by_state) == state_count:
        quantities = [0.0] * len(legs)
        for index in no_by_state.values():
            quantities[index] = 1.0 / float(state_count - 1)
        emit("structured_scaled_buy_all_no", quantities)

    no_items = list(no_by_state.items())
    for (_, first_index), (_, second_index) in itertools.combinations(no_items, 2):
        quantities = [0.0] * len(legs)
        quantities[first_index] = 1.0
        quantities[second_index] = 1.0
        emit("structured_two_no_cover", quantities)

    for state_index in range(state_count):
        yes_index = yes_by_state.get(state_index)
        no_index = no_by_state.get(state_index)
        if yes_index is None or no_index is None:
            continue
        quantities = [0.0] * len(legs)
        quantities[yes_index] = 1.0
        quantities[no_index] = 1.0
        emit("structured_same_market_yes_no", quantities)
    return solutions


def solve_family_payoff_matrix_arbitrage(
    family: Dict[str, Any],
    *,
    min_edge_cents: float = 1.0,
    min_leg_depth: float = 1.0,
    cost_cents: float = 0.0,
    max_active_set_enumerations: int = 250_000,
) -> Dict[str, Any]:
    states = _state_rows(family)
    if not family.get("is_partition_candidate"):
        return {
            "schema_version": "polyweather_bucket_family_lp_arbitrage_family.v1",
            "event_slug": family.get("event_slug"),
            "station_code": family.get("station_code"),
            "target_date": family.get("target_date"),
            "settlement_source": family.get("settlement_source"),
            "bucket_count": len(states),
            "candidate": False,
            "no_candidate_reason": "incomplete_partition",
            "partition_gap_reasons": family.get("partition_gap_reasons") or [],
            "missing_leg_count": 0,
            "solver_method": "not_run_incomplete_partition",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
    legs, missing_leg_count, missing_reason_counts = _tradable_legs(family, min_leg_depth=min_leg_depth)
    if not states or not legs:
        return {
            "schema_version": "polyweather_bucket_family_lp_arbitrage_family.v1",
            "event_slug": family.get("event_slug"),
            "station_code": family.get("station_code"),
            "target_date": family.get("target_date"),
            "settlement_source": family.get("settlement_source"),
            "bucket_count": len(states),
            "candidate": False,
            "no_candidate_reason": "no_tradable_legs",
            "missing_leg_count": missing_leg_count,
            "missing_reason_counts": missing_reason_counts,
            "solver_method": "not_run_no_tradable_legs",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
    exact, exact_diagnostics = _active_set_solution(
        family=family,
        states=states,
        legs=legs,
        missing_leg_count=missing_leg_count,
        cost_cents=cost_cents,
        max_enumerations=max_active_set_enumerations,
    )
    best = exact
    structured = _structured_fallback_solutions(
        family=family,
        states=states,
        legs=legs,
        missing_leg_count=missing_leg_count,
        cost_cents=cost_cents,
    )
    for solution in structured:
        best = _better_solution(best, solution)

    base = {
        "schema_version": "polyweather_bucket_family_lp_arbitrage_family.v1",
        "event_slug": family.get("event_slug"),
        "station_code": family.get("station_code"),
        "target_date": family.get("target_date"),
        "settlement_source": family.get("settlement_source"),
        "bucket_count": len(states),
        "tradable_leg_count": len(legs),
        "missing_leg_count": missing_leg_count,
        "missing_reason_counts": missing_reason_counts,
        "solver_diagnostics": {
            **exact_diagnostics,
            "structured_fallback_solution_count": len(structured),
            "solver_methods_considered": sorted(
                set(
                    ["active_set_bruteforce"]
                    + [str(row.get("solver_method")) for row in structured if row.get("solver_method")]
                )
            ),
        },
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }
    if best is None:
        return {
            **base,
            "candidate": False,
            "no_candidate_reason": "no_feasible_payoff_cover",
            "solver_method": "active_set_bruteforce_and_structured_fallback",
        }
    candidate = float(best.get("edge_cents") or -1e9) > float(min_edge_cents)
    return {
        **base,
        **best,
        "candidate": candidate,
        "no_candidate_reason": None if candidate else "edge_below_min",
        "min_edge_cents": float(min_edge_cents),
    }


def build_bucket_family_lp_arbitrage_report(
    catalog: Dict[str, Any],
    *,
    min_edge_cents: float = 1.0,
    min_leg_depth: float = 1.0,
    cost_cents: float = 0.0,
    max_candidates: int = 20,
    max_active_set_enumerations: int = 250_000,
) -> Dict[str, Any]:
    families = [row for row in catalog.get("families") or [] if isinstance(row, dict)]
    family_rows: List[Dict[str, Any]] = []
    blocker_counts: Counter[str] = Counter()
    for family in families:
        row = solve_family_payoff_matrix_arbitrage(
            family,
            min_edge_cents=min_edge_cents,
            min_leg_depth=min_leg_depth,
            cost_cents=cost_cents,
            max_active_set_enumerations=max_active_set_enumerations,
        )
        family_rows.append(row)
        if not row.get("candidate"):
            blocker_counts[str(row.get("no_candidate_reason") or "unknown")] += 1
    candidates = sorted(
        [row for row in family_rows if row.get("candidate")],
        key=lambda row: float(row.get("edge_cents") or -1e9),
        reverse=True,
    )
    near_misses = sorted(
        [row for row in family_rows if not row.get("candidate") and row.get("edge_cents") is not None],
        key=lambda row: float(row.get("edge_cents") or -1e9),
        reverse=True,
    )
    partition_count = len([row for row in families if row.get("is_partition_candidate")])
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "family_count": len(families),
        "lp_family_count": partition_count,
        "partition_family_count": partition_count,
        "lp_candidate_count": len(candidates),
        "candidate_count": len(candidates),
        "best_lp_edge_cents": candidates[0].get("edge_cents") if candidates else None,
        "lp_near_miss_count": len(near_misses),
        "missing_leg_count": sum(int(row.get("missing_leg_count") or 0) for row in family_rows),
        "no_candidate_reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(blocker_counts.items())
        ],
        "family_rows": family_rows,
        "near_misses": near_misses[: max(0, int(max_candidates))],
        "candidates": candidates[: max(0, int(max_candidates))],
    }


def load_bucket_family_catalog(path: str | Path) -> Dict[str, Any]:
    return _load_json(path)


__all__ = [
    "SCHEMA_VERSION",
    "STRATEGY_ID",
    "build_bucket_family_lp_arbitrage_report",
    "load_bucket_family_catalog",
    "solve_family_payoff_matrix_arbitrage",
]
