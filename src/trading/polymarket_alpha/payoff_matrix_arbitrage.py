from __future__ import annotations

import itertools
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


SCHEMA_VERSION = "polyweather_polymarket_alpha_payoff_arbitrage.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _text(value: Any) -> str:
    return str(value or "").strip()


def scipy_available() -> bool:
    try:
        import scipy.optimize  # noqa: F401
    except Exception:
        return False
    return True


def _market_books(market: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    books = market.get("orderbooks")
    return books if isinstance(books, dict) else {}


def _book_for_token(market: Dict[str, Any], token_id: str) -> Dict[str, Any]:
    return _market_books(market).get(str(token_id)) or {}


def _states_from_family(family: Dict[str, Any]) -> List[Dict[str, Any]]:
    explicit = family.get("outcome_states")
    if isinstance(explicit, list) and explicit:
        return [dict(row, state_index=index) for index, row in enumerate(explicit) if isinstance(row, dict)]
    markets = [row for row in family.get("markets") or [] if isinstance(row, dict)]
    if len(markets) == 1 and len(markets[0].get("outcomes") or []) > 2:
        return [
            {
                "state_index": index,
                "outcome": outcome,
                "market_slug": markets[0].get("market_slug"),
            }
            for index, outcome in enumerate(markets[0].get("outcomes") or [])
        ]
    return [
        {
            "state_index": index,
            "market_slug": market.get("market_slug"),
            "title": market.get("title") or market.get("question"),
        }
        for index, market in enumerate(markets)
    ]


def _explicit_legs(family: Dict[str, Any], state_count: int) -> List[Dict[str, Any]]:
    legs: List[Dict[str, Any]] = []
    for row in family.get("legs") or []:
        if not isinstance(row, dict):
            continue
        payoff = [float(value) for value in (row.get("payoff_vector") or [])]
        if len(payoff) != state_count:
            continue
        legs.append({**row, "payoff_vector": payoff})
    return legs


def _legs_from_family(family: Dict[str, Any], states: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    explicit = _explicit_legs(family, len(states))
    if explicit:
        return explicit
    markets = [row for row in family.get("markets") or [] if isinstance(row, dict)]
    legs: List[Dict[str, Any]] = []
    if len(markets) == 1 and len(markets[0].get("outcomes") or []) == len(states):
        market = markets[0]
        outcomes = market.get("outcomes") or []
        token_ids = market.get("token_ids") or []
        prices = market.get("outcome_prices") or []
        for index, outcome in enumerate(outcomes):
            token_id = str(token_ids[index]) if index < len(token_ids) else ""
            book = _book_for_token(market, token_id)
            payoff = [0.0] * len(states)
            payoff[index] = 1.0
            legs.append(
                {
                    "market_slug": market.get("market_slug"),
                    "token_id": token_id,
                    "side": str(outcome).upper(),
                    "best_ask": _safe_float(book.get("best_ask")) or (prices[index] if index < len(prices) else None),
                    "ask_depth": _safe_float(book.get("ask_depth_usdc_3c") or market.get("liquidity")) or 0.0,
                    "spread": _safe_float(book.get("spread") or market.get("spread")),
                    "payoff_vector": payoff,
                }
            )
        return legs
    for index, market in enumerate(markets):
        outcomes = [str(item).lower() for item in (market.get("outcomes") or [])]
        token_ids = market.get("token_ids") or []
        prices = market.get("outcome_prices") or []
        for side_name in ("yes", "no"):
            try:
                outcome_index = outcomes.index(side_name)
            except ValueError:
                continue
            token_id = str(token_ids[outcome_index]) if outcome_index < len(token_ids) else ""
            book = _book_for_token(market, token_id)
            payoff = [0.0] * len(states)
            for state_index in range(len(states)):
                if side_name == "yes":
                    payoff[state_index] = 1.0 if state_index == index else 0.0
                else:
                    payoff[state_index] = 0.0 if state_index == index else 1.0
            legs.append(
                {
                    "market_slug": market.get("market_slug"),
                    "token_id": token_id,
                    "side": side_name.upper(),
                    "best_ask": _safe_float(book.get("best_ask")) or (prices[outcome_index] if outcome_index < len(prices) else None),
                    "ask_depth": _safe_float(book.get("ask_depth_usdc_3c") or market.get("liquidity")) or 0.0,
                    "spread": _safe_float(book.get("spread") or market.get("spread")),
                    "payoff_vector": payoff,
                }
            )
    return legs


def _tradable_legs(legs: Iterable[Dict[str, Any]], *, min_depth: float) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    output: List[Dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for leg in legs:
        ask = _safe_float(leg.get("best_ask"))
        depth = _safe_float(leg.get("ask_depth"))
        if not leg.get("token_id"):
            reasons["missing_token"] += 1
            continue
        if ask is None or ask <= 0:
            reasons["missing_ask"] += 1
            continue
        if depth is None or depth < float(min_depth):
            reasons["depth_below_min"] += 1
            continue
        output.append({**leg, "best_ask": ask, "ask_depth": depth})
    return output, dict(sorted(reasons.items()))


def _solution_from_quantities(
    *,
    family: Dict[str, Any],
    states: List[Dict[str, Any]],
    legs: Sequence[Dict[str, Any]],
    quantities: Sequence[float],
    solver_method: str,
    cost_cents: float,
) -> Optional[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    payoff = [0.0] * len(states)
    total_cost_raw = 0.0
    for leg, quantity in zip(legs, quantities):
        qty = 0.0 if abs(float(quantity)) < 1e-9 else float(quantity)
        if qty <= 0:
            continue
        ask = _safe_float(leg.get("best_ask"))
        if ask is None:
            return None
        leg_cost = ask * qty
        total_cost_raw += leg_cost
        for index, value in enumerate(leg.get("payoff_vector") or []):
            payoff[index] += float(value) * qty
        selected.append(
            {
                "market_slug": leg.get("market_slug"),
                "token_id": leg.get("token_id"),
                "side": leg.get("side"),
                "best_ask": ask,
                "quantity": round(qty, 10),
                "leg_cost": round(leg_cost, 10),
                "ask_depth": leg.get("ask_depth"),
                "spread": leg.get("spread"),
                "payoff_vector": leg.get("payoff_vector"),
            }
        )
    if not selected:
        return None
    worst_case_payout = min(payoff) if payoff else 0.0
    if worst_case_payout < 1.0 - 1e-8:
        return None
    total_cost = total_cost_raw + float(cost_cents) / 100.0
    strategy_id = "generalized_min_cost_cover"
    if solver_method == "structured_buy_all_yes":
        strategy_id = "buy_all_yes"
    elif solver_method == "structured_scaled_buy_all_no":
        strategy_id = "buy_all_no"
    elif "monotonic" in solver_method:
        strategy_id = "monotonic_pair"
    elif "duplicate" in solver_method:
        strategy_id = "duplicate_market_disagreement"
    spreads = [_safe_float(row.get("spread")) for row in selected]
    depths = [_safe_float(row.get("ask_depth")) for row in selected]
    return {
        "family_id": family.get("family_id"),
        "category": family.get("category"),
        "strategy_id": strategy_id,
        "legs": selected,
        "leg_count": len(selected),
        "total_cost": round(total_cost, 10),
        "worst_case_payout": round(float(worst_case_payout), 10),
        "edge_cents": round(100.0 * (float(worst_case_payout) - float(total_cost)), 6),
        "min_depth": min(depth for depth in depths if depth is not None) if all(depth is not None for depth in depths) else None,
        "max_spread": max(spread for spread in spreads if spread is not None) if all(spread is not None for spread in spreads) else None,
        "completeness_certified": bool(family.get("is_exhaustive") or family.get("completeness_certified")),
        "solver_method": solver_method,
        "outcome_payoff_vector": [
            {**state, "payout": round(float(payoff[index]), 10)}
            for index, state in enumerate(states)
        ],
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _better(current: Optional[Dict[str, Any]], candidate: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if candidate is None:
        return current
    if current is None:
        return candidate
    if float(candidate.get("total_cost") or 1e9) < float(current.get("total_cost") or 1e9):
        return candidate
    return current


def _structured_solutions(family: Dict[str, Any], states: List[Dict[str, Any]], legs: List[Dict[str, Any]], cost_cents: float) -> List[Dict[str, Any]]:
    solutions: List[Dict[str, Any]] = []
    state_count = len(states)
    yes_indexes: Dict[int, int] = {}
    no_indexes: Dict[int, int] = {}
    for leg_index, leg in enumerate(legs):
        payoff = [float(value) for value in leg.get("payoff_vector") or []]
        if payoff.count(1.0) == 1 and sum(payoff) == 1.0:
            yes_indexes[payoff.index(1.0)] = leg_index
        if payoff.count(0.0) == 1 and sum(payoff) == float(state_count - 1):
            no_indexes[payoff.index(0.0)] = leg_index

    if state_count and len(yes_indexes) == state_count:
        quantities = [0.0] * len(legs)
        for leg_index in yes_indexes.values():
            quantities[leg_index] = 1.0
        solution = _solution_from_quantities(
            family=family,
            states=states,
            legs=legs,
            quantities=quantities,
            solver_method="structured_buy_all_yes",
            cost_cents=cost_cents,
        )
        if solution:
            solutions.append(solution)

    if state_count > 1 and len(no_indexes) == state_count:
        quantities = [0.0] * len(legs)
        for leg_index in no_indexes.values():
            quantities[leg_index] = 1.0 / float(state_count - 1)
        solution = _solution_from_quantities(
            family=family,
            states=states,
            legs=legs,
            quantities=quantities,
            solver_method="structured_scaled_buy_all_no",
            cost_cents=cost_cents,
        )
        if solution:
            solutions.append(solution)
    return solutions


def _active_set_solution(
    family: Dict[str, Any],
    states: List[Dict[str, Any]],
    legs: List[Dict[str, Any]],
    *,
    cost_cents: float,
    max_enumerations: int,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if not states or not legs:
        return None, {"active_set_enumerated_count": 0}
    payoff = np.array([leg.get("payoff_vector") for leg in legs], dtype=float).T
    best: Optional[Dict[str, Any]] = None
    enumerated = 0
    max_active = min(len(states), len(legs))
    for active_count in range(1, max_active + 1):
        estimate = math.comb(len(legs), active_count) * math.comb(len(states), active_count)
        if enumerated + estimate > int(max_enumerations):
            continue
        for variable_indexes in itertools.combinations(range(len(legs)), active_count):
            sub_payoff = payoff[:, variable_indexes]
            for state_indexes in itertools.combinations(range(len(states)), active_count):
                matrix = sub_payoff[list(state_indexes), :]
                if np.linalg.matrix_rank(matrix) < active_count:
                    continue
                try:
                    quantities = np.linalg.solve(matrix, np.ones(active_count))
                except np.linalg.LinAlgError:
                    continue
                enumerated += 1
                if np.any(quantities < -1e-8):
                    continue
                full = [0.0] * len(legs)
                for index, qty in zip(variable_indexes, quantities):
                    full[index] = max(0.0, float(qty))
                best = _better(
                    best,
                    _solution_from_quantities(
                        family=family,
                        states=states,
                        legs=legs,
                        quantities=full,
                        solver_method="active_set_bruteforce",
                        cost_cents=cost_cents,
                    ),
                )
    return best, {"active_set_enumerated_count": enumerated}


def solve_family_payoff_arbitrage(
    family: Dict[str, Any],
    *,
    min_edge_cents: float = 0.25,
    min_depth: float = 1.0,
    cost_cents: float = 0.0,
    allow_bruteforce_without_scipy: bool = True,
    max_active_set_enumerations: int = 250_000,
) -> Dict[str, Any]:
    states = _states_from_family(family)
    completeness_certified = bool(family.get("is_exhaustive") or family.get("completeness_certified"))
    if not completeness_certified:
        return {
            "family_id": family.get("family_id"),
            "category": family.get("category"),
            "candidate": False,
            "no_candidate_reason": "incomplete_family",
            "completeness_certified": False,
            "solver_method": "not_run_incomplete_family",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
    raw_legs = _legs_from_family(family, states)
    legs, missing_reason_counts = _tradable_legs(raw_legs, min_depth=min_depth)
    if not states or not legs:
        return {
            "family_id": family.get("family_id"),
            "category": family.get("category"),
            "candidate": False,
            "no_candidate_reason": "no_tradable_legs",
            "completeness_certified": completeness_certified,
            "missing_reason_counts": missing_reason_counts,
            "solver_method": "not_run_no_tradable_legs",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
    scipy_ok = scipy_available()
    if not scipy_ok and not allow_bruteforce_without_scipy:
        return {
            "family_id": family.get("family_id"),
            "category": family.get("category"),
            "candidate": False,
            "no_candidate_reason": "not_proven_complete_scipy_unavailable",
            "completeness_certified": False,
            "solver_method": "not_proven_complete_scipy_unavailable",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
    best: Optional[Dict[str, Any]] = None
    structured = _structured_solutions(family, states, legs, cost_cents)
    for solution in structured:
        best = _better(best, solution)
    exact, diagnostics = _active_set_solution(
        family,
        states,
        legs,
        cost_cents=cost_cents,
        max_enumerations=max_active_set_enumerations,
    )
    best = _better(best, exact)
    if best is None:
        return {
            "family_id": family.get("family_id"),
            "category": family.get("category"),
            "candidate": False,
            "no_candidate_reason": "no_feasible_payoff_cover",
            "completeness_certified": completeness_certified,
            "solver_method": "scipy_linprog" if scipy_ok else "active_set_bruteforce",
            "solver_diagnostics": diagnostics,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
    candidate = bool(float(best.get("edge_cents") or -1e9) >= float(min_edge_cents))
    return {
        **best,
        "candidate": candidate,
        "no_candidate_reason": None if candidate else "edge_below_min",
        "min_edge_cents": float(min_edge_cents),
        "solver_diagnostics": {
            **diagnostics,
            "scipy_available": scipy_ok,
            "structured_solution_count": len(structured),
        },
    }


def build_payoff_arbitrage_report(
    catalog: Dict[str, Any],
    *,
    min_edge_cents: float = 0.25,
    min_depth: float = 1.0,
    cost_cents: float = 0.0,
    max_candidates: int = 50,
    allow_bruteforce_without_scipy: bool = True,
) -> Dict[str, Any]:
    families = [row for row in catalog.get("families") or [] if isinstance(row, dict)]
    rows: List[Dict[str, Any]] = []
    blockers: Counter[str] = Counter()
    for family in families:
        solved = solve_family_payoff_arbitrage(
            family,
            min_edge_cents=min_edge_cents,
            min_depth=min_depth,
            cost_cents=cost_cents,
            allow_bruteforce_without_scipy=allow_bruteforce_without_scipy,
        )
        rows.append(solved)
        if not solved.get("candidate"):
            blockers[str(solved.get("no_candidate_reason") or "unknown")] += 1
    candidates = sorted(
        [row for row in rows if row.get("candidate")],
        key=lambda row: float(row.get("edge_cents") or -1e9),
        reverse=True,
    )
    near_misses = sorted(
        [row for row in rows if not row.get("candidate") and row.get("edge_cents") is not None],
        key=lambda row: float(row.get("edge_cents") or -1e9),
        reverse=True,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "family_count": len(families),
        "structural_candidate_count": len(candidates),
        "candidate_count": len(candidates),
        "structural_near_miss_count": len(near_misses),
        "near_miss_count": len(near_misses),
        "best_edge_cents": candidates[0].get("edge_cents") if candidates else None,
        "scipy_available": scipy_available(),
        "no_candidate_reason_counts": [
            {"reason": reason, "count": count} for reason, count in sorted(blockers.items())
        ],
        "family_rows": rows,
        "candidates": candidates[: max(0, int(max_candidates))],
        "near_misses": near_misses[: max(0, int(max_candidates))],
    }


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "SCHEMA_VERSION",
    "build_payoff_arbitrage_report",
    "load_json",
    "solve_family_payoff_arbitrage",
    "write_json",
    "write_jsonl",
]
