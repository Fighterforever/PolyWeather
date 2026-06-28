from __future__ import annotations

import src.trading.polymarket_alpha.payoff_matrix_arbitrage as payoff
from src.trading.polymarket_alpha.payoff_matrix_arbitrage import (
    build_payoff_arbitrage_report,
    solve_family_payoff_arbitrage,
)


def _family(legs, *, state_count=2, exhaustive=True, family_type="mutually_exclusive_outcomes"):
    return {
        "family_id": "fam-1",
        "category": "test",
        "family_type": family_type,
        "is_exhaustive": exhaustive,
        "outcome_states": [{"name": f"s{index}"} for index in range(state_count)],
        "legs": legs,
    }


def _leg(name, payoff, ask, depth=10.0):
    return {
        "market_slug": name,
        "token_id": f"{name}-token",
        "side": name.upper(),
        "best_ask": ask,
        "ask_depth": depth,
        "spread": 0.02,
        "payoff_vector": payoff,
    }


def test_payoff_solver_finds_buy_all_yes_when_positive():
    family = _family([_leg("yes-a", [1, 0], 0.4), _leg("yes-b", [0, 1], 0.4)])

    row = solve_family_payoff_arbitrage(family, min_edge_cents=1.0)

    assert row["candidate"] is True
    assert row["strategy_id"] == "buy_all_yes"
    assert row["edge_cents"] == 20.0
    assert row["completeness_certified"] is True
    assert row["live_order_path"] is False


def test_payoff_solver_finds_buy_all_no_when_positive():
    family = _family(
        [
            _leg("no-a", [0, 1, 1], 0.3),
            _leg("no-b", [1, 0, 1], 0.3),
            _leg("no-c", [1, 1, 0], 0.3),
        ],
        state_count=3,
    )

    row = solve_family_payoff_arbitrage(family, min_edge_cents=1.0)

    assert row["candidate"] is True
    assert row["strategy_id"] == "buy_all_no"
    assert row["edge_cents"] == 55.0


def test_payoff_solver_finds_partial_combo_not_found_by_full_basket():
    family = _family(
        [
            _leg("yes-a", [1, 0], 0.6),
            _leg("yes-b", [0, 1], 0.6),
            _leg("cover", [1, 1], 0.8),
        ]
    )

    row = solve_family_payoff_arbitrage(family, min_edge_cents=1.0)

    assert row["candidate"] is True
    assert row["strategy_id"] == "generalized_min_cost_cover"
    assert row["leg_count"] == 1
    assert row["edge_cents"] == 20.0


def test_payoff_solver_rejects_incomplete_family():
    row = solve_family_payoff_arbitrage(_family([_leg("yes-a", [1, 0], 0.4)], exhaustive=False))

    assert row["candidate"] is False
    assert row["no_candidate_reason"] == "incomplete_family"
    assert row["completeness_certified"] is False


def test_payoff_solver_respects_depth_constraints():
    row = solve_family_payoff_arbitrage(
        _family([_leg("yes-a", [1, 0], 0.4, depth=0.1), _leg("yes-b", [0, 1], 0.4, depth=0.1)]),
        min_depth=1.0,
    )

    assert row["candidate"] is False
    assert row["no_candidate_reason"] == "no_tradable_legs"


def test_payoff_solver_marks_scipy_unavailable_as_incomplete_proof_when_fallback_disabled(monkeypatch):
    monkeypatch.setattr(payoff, "scipy_available", lambda: False)

    row = solve_family_payoff_arbitrage(
        _family([_leg("yes-a", [1, 0], 0.4), _leg("yes-b", [0, 1], 0.4)]),
        allow_bruteforce_without_scipy=False,
    )

    assert row["candidate"] is False
    assert row["no_candidate_reason"] == "not_proven_complete_scipy_unavailable"
    assert row["live_order_path"] is False


def test_payoff_report_never_live_eligible():
    report = build_payoff_arbitrage_report({"families": [_family([_leg("yes-a", [1, 0], 0.4), _leg("yes-b", [0, 1], 0.4)])]})

    assert report["structural_candidate_count"] == 1
    assert report["candidates"][0]["live_order_path"] is False
    assert report["live_order_path"] is False
