from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from scripts.polymarket_alpha_weather_lp_experiment_controller import build_report


def test_weather_lp_controller_requires_reward_metadata(tmp_path: Path):
    discovery = tmp_path / "discovery.json"
    strategy = tmp_path / "strategy.json"
    paper = tmp_path / "paper.json"
    window = tmp_path / "window.json"
    holder = tmp_path / "holder.json"
    updates = tmp_path / "updates.json"
    risk = tmp_path / "risk.json"
    lifecycle = tmp_path / "lifecycle.json"
    cohort = tmp_path / "cohort.json"
    share = tmp_path / "share.json"
    allocation = tmp_path / "allocation.json"
    dollarization = tmp_path / "dollarization.json"
    dashboard = tmp_path / "dashboard.json"
    cancellation = tmp_path / "cancellation.json"
    optimizer = tmp_path / "optimizer.json"
    quote_updates = tmp_path / "updates.jsonl"
    discovery.write_text(json.dumps({"reward_metadata_available_count": 0, "reward_market_count": 0}), encoding="utf-8")
    strategy.write_text(json.dumps({}), encoding="utf-8")
    paper.write_text(json.dumps({"paper_quote_count": 0}), encoding="utf-8")
    updates.write_text(json.dumps({}), encoding="utf-8")
    risk.write_text(json.dumps({}), encoding="utf-8")
    lifecycle.write_text(json.dumps({}), encoding="utf-8")
    cohort.write_text(json.dumps({}), encoding="utf-8")
    share.write_text(json.dumps({}), encoding="utf-8")
    allocation.write_text(json.dumps({}), encoding="utf-8")
    dollarization.write_text(json.dumps({}), encoding="utf-8")
    dashboard.write_text(json.dumps({}), encoding="utf-8")
    cancellation.write_text(json.dumps({}), encoding="utf-8")
    optimizer.write_text(json.dumps({}), encoding="utf-8")
    quote_updates.write_text("", encoding="utf-8")
    window.write_text(json.dumps({"observations_count": 0}), encoding="utf-8")
    holder.write_text(json.dumps({"smart_holder_signal_count": 0}), encoding="utf-8")

    report = build_report(
        Namespace(
            discovery_report=discovery,
            strategy_report=strategy,
            paper_cycle_report=paper,
            quote_update_report=updates,
            reward_risk_report=risk,
            lifecycle_audit_report=lifecycle,
            measurement_cohort_report=cohort,
            reward_share_report=share,
            reward_allocation_audit_report=allocation,
            reward_dollarization_report=dollarization,
            profitability_dashboard=dashboard,
            cancellation_policy_report=cancellation,
            quote_optimizer_report=optimizer,
            quote_updates=quote_updates,
            reward_window_report=window,
            smart_holder_report=holder,
        )
    )

    assert report["recommendation"] == "reward_metadata_pipeline_broken_or_no_rewards"
    assert report["live_order_path"] is False


def test_weather_lp_controller_requires_enough_quote_updates(tmp_path: Path):
    discovery = tmp_path / "discovery.json"
    strategy = tmp_path / "strategy.json"
    paper = tmp_path / "paper.json"
    updates = tmp_path / "updates.json"
    risk = tmp_path / "risk.json"
    lifecycle = tmp_path / "lifecycle.json"
    cohort = tmp_path / "cohort.json"
    share = tmp_path / "share.json"
    allocation = tmp_path / "allocation.json"
    dollarization = tmp_path / "dollarization.json"
    dashboard = tmp_path / "dashboard.json"
    cancellation = tmp_path / "cancellation.json"
    optimizer = tmp_path / "optimizer.json"
    quote_updates = tmp_path / "updates.jsonl"
    window = tmp_path / "window.json"
    holder = tmp_path / "holder.json"
    discovery.write_text(json.dumps({"reward_metadata_available_count": 89, "reward_market_count": 89}), encoding="utf-8")
    strategy.write_text(json.dumps({"reward_qualified_quote_count": 20}), encoding="utf-8")
    paper.write_text(json.dumps({"paper_quote_count": 20, "active_quote_count": 20}), encoding="utf-8")
    updates.write_text(json.dumps({"quote_update_count": 20, "cumulative_reward_points_proxy": 3.0}), encoding="utf-8")
    risk.write_text(json.dumps({"quote_update_count": 20, "cumulative_reward_points_proxy": 3.0, "mean_current_markout": -0.2}), encoding="utf-8")
    lifecycle.write_text(json.dumps({"conclusion": "missing_horizon_updates_after_entry", "unique_quote_id_count": 20, "updates_per_quote_median": 1}), encoding="utf-8")
    cohort.write_text(json.dumps({"active_cohort_count": 20, "cohort_created_count": 20}), encoding="utf-8")
    share.write_text(json.dumps({"visible_reward_share_median": 0.01, "quotes_where_visible_share_exceeds_break_even": 0}), encoding="utf-8")
    allocation.write_text(json.dumps({"estimated_reward_cents_available_count": 0, "gap_counts": []}), encoding="utf-8")
    dollarization.write_text(json.dumps({}), encoding="utf-8")
    dashboard.write_text(json.dumps({}), encoding="utf-8")
    cancellation.write_text(json.dumps({"cancellation_policy_recommendation": "continue_collecting_policy_updates"}), encoding="utf-8")
    optimizer.write_text(json.dumps({"selected_quote_count": 20, "rejected_expensive_basket_count": 0}), encoding="utf-8")
    quote_updates.write_text('{"update_time":"2026-06-29T10:00:00Z"}\n{"update_time":"2026-06-29T10:05:00Z"}\n', encoding="utf-8")
    window.write_text(json.dumps({"observations_count": 3, "window_confidence": "insufficient_observations"}), encoding="utf-8")
    holder.write_text(json.dumps({"smart_holder_signal_count": 0}), encoding="utf-8")

    report = build_report(
        Namespace(
            discovery_report=discovery,
            strategy_report=strategy,
            paper_cycle_report=paper,
            quote_update_report=updates,
            reward_risk_report=risk,
            lifecycle_audit_report=lifecycle,
            measurement_cohort_report=cohort,
            reward_share_report=share,
            reward_allocation_audit_report=allocation,
            reward_dollarization_report=dollarization,
            profitability_dashboard=dashboard,
            cancellation_policy_report=cancellation,
            quote_optimizer_report=optimizer,
            quote_updates=quote_updates,
            reward_window_report=window,
            smart_holder_report=holder,
        )
    )

    assert report["quote_update_count"] == 20
    assert report["unique_quote_id_count"] == 20
    assert report["visible_reward_share_median"] == 0.01
    assert report["recommendation"] == "continue_weather_lp_paper_insufficient_updates"
    assert report["cancellation_policy_recommendation"] == "continue_collecting_policy_updates"
    assert report["quote_optimizer_selected_count"] == 20


def test_weather_lp_controller_flags_missing_strict_horizon_after_many_updates(tmp_path: Path):
    discovery = tmp_path / "discovery.json"
    strategy = tmp_path / "strategy.json"
    paper = tmp_path / "paper.json"
    updates = tmp_path / "updates.json"
    risk = tmp_path / "risk.json"
    lifecycle = tmp_path / "lifecycle.json"
    cohort = tmp_path / "cohort.json"
    share = tmp_path / "share.json"
    allocation = tmp_path / "allocation.json"
    dollarization = tmp_path / "dollarization.json"
    dashboard = tmp_path / "dashboard.json"
    cancellation = tmp_path / "cancellation.json"
    optimizer = tmp_path / "optimizer.json"
    quote_updates = tmp_path / "updates.jsonl"
    window = tmp_path / "window.json"
    holder = tmp_path / "holder.json"
    discovery.write_text(json.dumps({"reward_metadata_available_count": 89, "reward_market_count": 89}), encoding="utf-8")
    strategy.write_text(json.dumps({"reward_qualified_quote_count": 20}), encoding="utf-8")
    paper.write_text(json.dumps({"paper_quote_count": 20}), encoding="utf-8")
    updates.write_text(json.dumps({"quote_update_count": 120}), encoding="utf-8")
    risk.write_text(
        json.dumps(
            {
                "quote_update_count": 120,
                "mean_current_markout": 0.1,
                "valid_markout_count_by_horizon": [
                    {"horizon": "5m", "count": 0},
                    {"horizon": "15m", "count": 0},
                    {"horizon": "1h", "count": 0},
                    {"horizon": "current", "count": 20},
                ],
            }
        ),
        encoding="utf-8",
    )
    lifecycle.write_text(json.dumps({"conclusion": "missing_horizon_updates_after_entry"}), encoding="utf-8")
    cohort.write_text(json.dumps({"active_cohort_count": 20}), encoding="utf-8")
    share.write_text(json.dumps({}), encoding="utf-8")
    allocation.write_text(json.dumps({"estimated_reward_cents_available_count": 0}), encoding="utf-8")
    dollarization.write_text(json.dumps({}), encoding="utf-8")
    dashboard.write_text(json.dumps({}), encoding="utf-8")
    cancellation.write_text(json.dumps({}), encoding="utf-8")
    optimizer.write_text(json.dumps({}), encoding="utf-8")
    quote_updates.write_text("", encoding="utf-8")
    window.write_text(json.dumps({}), encoding="utf-8")
    holder.write_text(json.dumps({}), encoding="utf-8")

    report = build_report(
        Namespace(
            discovery_report=discovery,
            strategy_report=strategy,
            paper_cycle_report=paper,
            quote_update_report=updates,
            reward_risk_report=risk,
            lifecycle_audit_report=lifecycle,
            measurement_cohort_report=cohort,
            reward_share_report=share,
            reward_allocation_audit_report=allocation,
            reward_dollarization_report=dollarization,
            profitability_dashboard=dashboard,
            cancellation_policy_report=cancellation,
            quote_optimizer_report=optimizer,
            quote_updates=quote_updates,
            reward_window_report=window,
            smart_holder_report=holder,
        )
    )

    assert report["recommendation"] == "markout_pipeline_still_broken"


def test_weather_lp_controller_waits_for_young_cohort_horizon(tmp_path: Path):
    discovery = tmp_path / "discovery.json"
    strategy = tmp_path / "strategy.json"
    paper = tmp_path / "paper.json"
    updates = tmp_path / "updates.json"
    risk = tmp_path / "risk.json"
    lifecycle = tmp_path / "lifecycle.json"
    cohort = tmp_path / "cohort.json"
    share = tmp_path / "share.json"
    allocation = tmp_path / "allocation.json"
    dollarization = tmp_path / "dollarization.json"
    dashboard = tmp_path / "dashboard.json"
    cancellation = tmp_path / "cancellation.json"
    optimizer = tmp_path / "optimizer.json"
    quote_updates = tmp_path / "updates.jsonl"
    window = tmp_path / "window.json"
    holder = tmp_path / "holder.json"
    discovery.write_text(json.dumps({"reward_metadata_available_count": 89}), encoding="utf-8")
    strategy.write_text(json.dumps({}), encoding="utf-8")
    paper.write_text(json.dumps({"paper_quote_count": 20}), encoding="utf-8")
    updates.write_text(json.dumps({"quote_update_count": 120}), encoding="utf-8")
    risk.write_text(
        json.dumps(
            {
                "quote_update_count": 120,
                "active_cohort_count": 20,
                "valid_markout_count_by_horizon": [{"horizon": "5m", "count": 0}, {"horizon": "15m", "count": 0}, {"horizon": "1h", "count": 0}],
                "cohort_not_old_enough_count_by_horizon": [{"horizon": "5m", "count": 20}, {"horizon": "15m", "count": 20}, {"horizon": "1h", "count": 20}],
            }
        ),
        encoding="utf-8",
    )
    lifecycle.write_text(json.dumps({"conclusion": "missing_horizon_updates_after_entry"}), encoding="utf-8")
    cohort.write_text(json.dumps({"active_cohort_count": 20, "cohort_created_count": 20, "next_expected_5m_markout_time": "2026-06-30T10:05:00Z"}), encoding="utf-8")
    share.write_text(json.dumps({}), encoding="utf-8")
    allocation.write_text(json.dumps({}), encoding="utf-8")
    dollarization.write_text(json.dumps({}), encoding="utf-8")
    dashboard.write_text(json.dumps({}), encoding="utf-8")
    cancellation.write_text(json.dumps({}), encoding="utf-8")
    optimizer.write_text(json.dumps({}), encoding="utf-8")
    quote_updates.write_text("", encoding="utf-8")
    window.write_text(json.dumps({}), encoding="utf-8")
    holder.write_text(json.dumps({}), encoding="utf-8")

    report = build_report(
        Namespace(
            discovery_report=discovery,
            strategy_report=strategy,
            paper_cycle_report=paper,
            quote_update_report=updates,
            reward_risk_report=risk,
            lifecycle_audit_report=lifecycle,
            measurement_cohort_report=cohort,
            reward_share_report=share,
            reward_allocation_audit_report=allocation,
            reward_dollarization_report=dollarization,
            profitability_dashboard=dashboard,
            cancellation_policy_report=cancellation,
            quote_optimizer_report=optimizer,
            quote_updates=quote_updates,
            reward_window_report=window,
            smart_holder_report=holder,
        )
    )

    assert report["active_cohort_count"] == 20
    assert report["recommendation"] == "waiting_for_cohort_horizon_markout"
    assert report["next_expected_5m_markout_time"] == "2026-06-30T10:05:00Z"


def test_weather_lp_controller_uses_dollarization_scenario_recommendation(tmp_path: Path):
    discovery = tmp_path / "discovery.json"
    strategy = tmp_path / "strategy.json"
    paper = tmp_path / "paper.json"
    updates = tmp_path / "updates.json"
    risk = tmp_path / "risk.json"
    lifecycle = tmp_path / "lifecycle.json"
    cohort = tmp_path / "cohort.json"
    share = tmp_path / "share.json"
    allocation = tmp_path / "allocation.json"
    dollarization = tmp_path / "dollarization.json"
    dashboard = tmp_path / "dashboard.json"
    cancellation = tmp_path / "cancellation.json"
    optimizer = tmp_path / "optimizer.json"
    quote_updates = tmp_path / "updates.jsonl"
    window = tmp_path / "window.json"
    holder = tmp_path / "holder.json"
    discovery.write_text(json.dumps({"reward_metadata_available_count": 89}), encoding="utf-8")
    strategy.write_text(json.dumps({}), encoding="utf-8")
    paper.write_text(json.dumps({"paper_quote_count": 20}), encoding="utf-8")
    updates.write_text(json.dumps({"quote_update_count": 200}), encoding="utf-8")
    risk.write_text(
        json.dumps(
            {
                "quote_update_count": 200,
                "mean_current_markout": 0.5,
                "valid_markout_count_by_horizon": [{"horizon": "5m", "count": 20}, {"horizon": "15m", "count": 20}, {"horizon": "1h", "count": 20}],
            }
        ),
        encoding="utf-8",
    )
    lifecycle.write_text(json.dumps({"conclusion": "lifecycle_ok", "unique_quote_id_count": 20}), encoding="utf-8")
    cohort.write_text(json.dumps({"active_cohort_count": 20}), encoding="utf-8")
    share.write_text(json.dumps({"visible_reward_share_median": 0.02}), encoding="utf-8")
    allocation.write_text(json.dumps({"estimated_reward_cents_available_count": 0}), encoding="utf-8")
    dollarization.write_text(
        json.dumps(
            {
                "exact_reward_cents_available_count": 0,
                "scenario_total_reward_if_daily_allocation_1": 0.1,
                "scenario_total_reward_if_daily_allocation_5": 0.5,
                "scenario_total_reward_if_daily_allocation_10": 1.0,
                "observed_markout_total_cents": 50.0,
                "break_even_daily_allocation_median": 12.0,
            }
        ),
        encoding="utf-8",
    )
    dashboard.write_text(json.dumps({"recommendation": "continue_weather_lp_paper"}), encoding="utf-8")
    cancellation.write_text(json.dumps({}), encoding="utf-8")
    optimizer.write_text(json.dumps({}), encoding="utf-8")
    quote_updates.write_text("", encoding="utf-8")
    window.write_text(json.dumps({}), encoding="utf-8")
    holder.write_text(json.dumps({}), encoding="utf-8")

    report = build_report(
        Namespace(
            discovery_report=discovery,
            strategy_report=strategy,
            paper_cycle_report=paper,
            quote_update_report=updates,
            reward_risk_report=risk,
            lifecycle_audit_report=lifecycle,
            measurement_cohort_report=cohort,
            reward_share_report=share,
            reward_allocation_audit_report=allocation,
            reward_dollarization_report=dollarization,
            profitability_dashboard=dashboard,
            cancellation_policy_report=cancellation,
            quote_optimizer_report=optimizer,
            quote_updates=quote_updates,
            reward_window_report=window,
            smart_holder_report=holder,
        )
    )

    assert report["exact_reward_available"] is False
    assert report["scenario_reward_daily_allocation_10"] == 1.0
    assert report["observed_markout_total_cents"] == 50.0
    assert report["recommendation"] == "continue_weather_lp_paper_exact_reward_unknown_but_scenarios_positive"


def test_weather_lp_controller_surfaces_manual_review_packet(tmp_path: Path):
    paths = {}
    for name in (
        "discovery",
        "strategy",
        "paper",
        "updates",
        "risk",
        "lifecycle",
        "cohort",
        "share",
        "allocation",
        "dollarization",
        "dashboard",
        "cancellation",
        "optimizer",
        "window",
        "holder",
        "profitability",
        "tiny_gap",
        "position",
        "kill_policy",
        "payout",
        "manual_sheet",
        "impact",
        "manual_kill",
        "manual_packet",
    ):
        paths[name] = tmp_path / f"{name}.json"
    quote_updates = tmp_path / "updates.jsonl"
    paths["discovery"].write_text(json.dumps({"reward_metadata_available_count": 89}), encoding="utf-8")
    paths["strategy"].write_text(json.dumps({}), encoding="utf-8")
    paths["paper"].write_text(json.dumps({"paper_quote_count": 20}), encoding="utf-8")
    paths["updates"].write_text(json.dumps({"quote_update_count": 600}), encoding="utf-8")
    paths["risk"].write_text(
        json.dumps(
            {
                "quote_update_count": 600,
                "mean_current_markout": 0.5,
                "valid_markout_count_by_horizon": [
                    {"horizon": "5m", "count": 20},
                    {"horizon": "15m", "count": 20},
                    {"horizon": "1h", "count": 20},
                ],
            }
        ),
        encoding="utf-8",
    )
    paths["lifecycle"].write_text(json.dumps({"conclusion": "lifecycle_ok"}), encoding="utf-8")
    paths["cohort"].write_text(json.dumps({"active_cohort_count": 20}), encoding="utf-8")
    paths["share"].write_text(json.dumps({"visible_reward_share_median": 0.09}), encoding="utf-8")
    paths["allocation"].write_text(json.dumps({}), encoding="utf-8")
    paths["dollarization"].write_text(json.dumps({"exact_reward_cents_available_count": 0}), encoding="utf-8")
    paths["dashboard"].write_text(json.dumps({}), encoding="utf-8")
    paths["cancellation"].write_text(json.dumps({}), encoding="utf-8")
    paths["optimizer"].write_text(json.dumps({}), encoding="utf-8")
    paths["window"].write_text(json.dumps({}), encoding="utf-8")
    paths["holder"].write_text(json.dumps({}), encoding="utf-8")
    paths["profitability"].write_text(
        json.dumps({"exact_reward_available": False, "scenario_status": {"base_positive": True}, "scenario_table": {"base_net": 100, "conservative_net": -50}}),
        encoding="utf-8",
    )
    paths["tiny_gap"].write_text(json.dumps({"final_status": "manual_review_artifacts_partial_exact_payout_or_kill_switch_blocked"}), encoding="utf-8")
    paths["position"].write_text(json.dumps({}), encoding="utf-8")
    paths["kill_policy"].write_text(json.dumps({}), encoding="utf-8")
    paths["payout"].write_text(json.dumps({"audit_status": "manual_audit_required"}), encoding="utf-8")
    paths["manual_sheet"].write_text(json.dumps({"suggested_manual_quote_count": 2, "total_capital_at_risk_if_all_manual_quotes_used": 12.0}), encoding="utf-8")
    paths["impact"].write_text(json.dumps({"impact_simulation_ready": True}), encoding="utf-8")
    paths["manual_kill"].write_text(json.dumps({"ready": True, "checklist_status": "ready"}), encoding="utf-8")
    paths["manual_packet"].write_text(
        json.dumps(
            {
                "manual_review_packet_ready": True,
                "suggested_human_decision": "prepare_manual_tiny_live_review_but_reward_payout_audit_required",
                "remaining_blockers": ["exact_reward_payout_not_verified"],
            }
        ),
        encoding="utf-8",
    )
    quote_updates.write_text("", encoding="utf-8")

    report = build_report(
        Namespace(
            discovery_report=paths["discovery"],
            strategy_report=paths["strategy"],
            paper_cycle_report=paths["paper"],
            quote_update_report=paths["updates"],
            reward_risk_report=paths["risk"],
            lifecycle_audit_report=paths["lifecycle"],
            measurement_cohort_report=paths["cohort"],
            reward_share_report=paths["share"],
            reward_allocation_audit_report=paths["allocation"],
            reward_dollarization_report=paths["dollarization"],
            profitability_dashboard=paths["dashboard"],
            cancellation_policy_report=paths["cancellation"],
            quote_optimizer_report=paths["optimizer"],
            quote_updates=quote_updates,
            reward_window_report=paths["window"],
            smart_holder_report=paths["holder"],
            profitability_simulation_report=paths["profitability"],
            tiny_live_gap_report=paths["tiny_gap"],
            position_sizing_report=paths["position"],
            kill_switch_policy_report=paths["kill_policy"],
            reward_payout_audit_report=paths["payout"],
            manual_order_sheet_report=paths["manual_sheet"],
            impact_simulator_report=paths["impact"],
            manual_kill_switch_checklist=paths["manual_kill"],
            manual_review_packet=paths["manual_packet"],
        )
    )

    assert report["manual_review_packet_ready"] is True
    assert report["kill_switch_ready"] is True
    assert report["final_stage"] == "manual_review_ready_but_live_disabled"
    assert report["suggested_human_decision"] == "prepare_manual_tiny_live_review_but_reward_payout_audit_required"
    assert report["recommendation"] == "manual_tiny_live_review_artifacts_ready_but_live_disabled__reward_payout_audit_required"
    assert report["live_order_path"] is False
