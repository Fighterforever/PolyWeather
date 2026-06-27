from __future__ import annotations

import json

from scripts import weather_market_signal_report as signal_cli
from src.trading.weather_market_signal import (
    WeatherMarketSignalConfig,
    assess_weather_market_row,
    build_weather_market_signal_report,
)


def _settlement(
    *,
    city: str = "seoul",
    bucket_type: str = "ge",
    threshold: float = 28.0,
    station_code: str = "RKSI",
) -> dict:
    return {
        "p_model": 0.72,
        "p_lcb": 0.67,
        "q_effective": 0.42,
        "cost": 0.01,
        "ev_safe": 0.24,
        "settlement_spec_status": "supported",
        "settlement_spec": {
            "schema_version": "polyweather_weather_settlement_spec.v1",
            "status": "supported",
            "platform": "polymarket",
            "market_family": "temperature",
            "city": city,
            "city_display_name": city.title(),
            "station_code": station_code,
            "station_label": station_code,
            "settlement_source": "metar",
            "target_date": "2026-06-27",
            "timezone": "UTC+09:00",
            "metric": "daily_high_temperature",
            "unit": "C",
            "bucket_type": bucket_type,
            "threshold": threshold,
            "upper_threshold": None,
            "rounding": "integer_nearest",
            "rule_hash": f"rule-{city}-{bucket_type}-{threshold}",
            "rule_text": "test settlement rule",
            "end_time": "2026-06-27T12:00:00Z",
        },
    }


def test_assess_weather_market_row_accepts_strict_candidate():
    row = {
        "id": "hong-kong-28c-yes",
        "city": "hong kong",
        "market_slug": "highest-temperature-in-hong-kong-on-june-27-2026-28c",
        "side": "yes",
        "bucket_label": "28C",
        "active": True,
        "closed": False,
        "tradable": True,
        "accepting_orders": True,
        "price": 0.12,
        "spread": 0.01,
        "execution_liquidity": 1500,
        "edge_percent": 18.5,
        "model_probability": 0.305,
        "final_score": 80,
        "metar_status": {"available_for_today": True, "stale_for_today": False},
        **_settlement(city="hong kong", bucket_type="eq", threshold=28.0, station_code="HKO"),
    }

    assessment = assess_weather_market_row(row)

    assert assessment["decision"] == "candidate"
    assert assessment["blockers"] == []
    assert assessment["warnings"] == []
    assert assessment["score"] > 0
    assert assessment["strategy_id"] == "eq_exact_shadow"
    assert assessment["strategy_live_eligible"] is False
    assert assessment["counts_for_live_gate"] is False
    assert assessment["risk_caps"]["max_position_usdc"] > 0
    assert assessment["target_date"] == "2026-06-27"
    assert assessment["end_time"] == "2026-06-27T12:00:00Z"
    assert assessment["end_date"] == "2026-06-27T12:00:00Z"
    assert assessment["settlement_station_code"] == "HKO"
    assert assessment["settlement_source"] == "metar"
    assert assessment["settlement_rule_hash"] == "rule-hong kong-eq-28.0"


def test_assess_weather_market_row_rejects_incomplete_supported_settlement_spec():
    settlement = _settlement(bucket_type="ge", threshold=28.0)
    settlement["settlement_spec"] = dict(settlement["settlement_spec"])
    settlement["settlement_spec"].pop("end_time")
    row = {
        "id": "seoul-28c-yes",
        "market_family": "temperature",
        "city": "seoul",
        "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
        "side": "yes",
        "bucket_label": ">= 28°C",
        "active": True,
        "closed": False,
        "tradable": True,
        "accepting_orders": True,
        "price": 0.30,
        "spread": 0.01,
        "execution_liquidity": 1500,
        "edge_percent": 20.0,
        "model_probability": 0.60,
        **settlement,
    }

    assessment = assess_weather_market_row(row)

    assert assessment["decision"] == "reject"
    assert "missing_settlement_spec_field:end_time" in assessment["blockers"]


def test_assess_weather_market_row_rejects_temperature_without_settlement_spec():
    row = {
        "id": "seoul-28c-yes",
        "market_family": "temperature",
        "city": "seoul",
        "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
        "side": "yes",
        "bucket_label": ">= 28°C",
        "active": True,
        "closed": False,
        "tradable": True,
        "accepting_orders": True,
        "price": 0.30,
        "spread": 0.01,
        "execution_liquidity": 1500,
        "edge_percent": 20.0,
        "model_probability": 0.60,
        "p_lcb": 0.55,
        "q_effective": 0.30,
        "cost": 0.01,
        "ev_safe": 0.24,
    }

    assessment = assess_weather_market_row(row)

    assert assessment["decision"] == "reject"
    assert "missing_settlement_spec" in assessment["blockers"]


def test_assess_weather_market_row_rejects_temperature_without_positive_ev_safe():
    row = {
        "id": "seoul-28c-yes",
        "market_family": "temperature",
        "city": "seoul",
        "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
        "side": "yes",
        "bucket_label": ">= 28°C",
        "active": True,
        "closed": False,
        "tradable": True,
        "accepting_orders": True,
        "price": 0.54,
        "spread": 0.01,
        "execution_liquidity": 1500,
        "edge_percent": 6.0,
        "model_probability": 0.60,
        **_settlement(bucket_type="ge", threshold=28.0),
        "p_lcb": 0.50,
        "q_effective": 0.54,
        "cost": 0.01,
        "ev_safe": -0.05,
    }

    assessment = assess_weather_market_row(row)

    assert assessment["decision"] == "reject"
    assert "ev_safe_below_min" in assessment["blockers"]


def test_signal_ranking_uses_ev_safe_before_legacy_raw_edge():
    payload = {
        "rows": [
            {
                "id": "high-raw-edge-negative-ev",
                "city": "seoul",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-35c-or-above",
                "side": "yes",
                "bucket_label": ">= 35°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.20,
                "spread": 0.01,
                "execution_liquidity": 2000,
                "bid_depth_usdc_3c": 80,
                "ask_depth_usdc_3c": 80,
                "edge_percent": 80.0,
                "final_score": 999.0,
                "model_probability": 0.90,
                **_settlement(bucket_type="ge", threshold=35.0),
                "p_lcb": 0.19,
                "q_effective": 0.20,
                "cost": 0.01,
                "ev_safe": -0.02,
            },
            {
                "id": "lower-raw-edge-positive-ev",
                "city": "seoul",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-31c-or-above",
                "side": "yes",
                "bucket_label": ">= 31°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.40,
                "spread": 0.01,
                "execution_liquidity": 1000,
                "bid_depth_usdc_3c": 40,
                "ask_depth_usdc_3c": 40,
                "edge_percent": 6.0,
                "final_score": 1.0,
                "model_probability": 0.48,
                **_settlement(bucket_type="ge", threshold=31.0),
                "p_lcb": 0.45,
                "q_effective": 0.40,
                "cost": 0.01,
                "ev_safe": 0.04,
            },
        ]
    }

    report = build_weather_market_signal_report(
        payload,
        config=WeatherMarketSignalConfig(
            min_liquidity=10,
            min_edge_percent=0,
            require_ev_safe=False,
        ),
    )

    assert report["summary"]["candidate_count"] == 2
    assert report["candidates"][0]["row_id"] == "lower-raw-edge-positive-ev"
    assert report["candidates"][0]["ev_safe"] == 0.04
    assert report["candidates"][1]["row_id"] == "high-raw-edge-negative-ev"
    assert report["candidates"][1]["edge_percent"] == 80.0


def test_assess_weather_market_row_can_require_quality_surface():
    row = {
        "id": "seoul-eq-yes",
        "city": "seoul",
        "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c",
        "side": "yes",
        "bucket_label": "= 28°C",
        "active": True,
        "closed": False,
        "tradable": True,
        "accepting_orders": True,
        "price": 0.12,
        "spread": 0.01,
        "execution_liquidity": 12,
        "order_book": {
            "bid_depth_usdc_3c": 4,
            "ask_depth_usdc_3c": 12,
        },
        "edge_percent": 18.5,
        "model_probability": 0.305,
        **_settlement(bucket_type="eq", threshold=28.0),
    }

    assessment = assess_weather_market_row(
        row,
        config=WeatherMarketSignalConfig(
            min_liquidity=10,
            excluded_bucket_types=("eq",),
            allowed_sides=("no",),
            min_bid_depth_usdc_3c=10,
            min_ask_depth_usdc_3c=10,
        ),
    )

    assert assessment["decision"] == "reject"
    assert "bucket_type_excluded:eq" in assessment["blockers"]
    assert "side_not_allowed" in assessment["blockers"]
    assert "bid_depth_below_min" in assessment["blockers"]
    assert assessment["bucket_type"] == "eq"
    assert assessment["bid_depth_usdc_3c"] == 4
    assert assessment["ask_depth_usdc_3c"] == 12


def test_signal_report_rejects_rows_without_tradeable_market_surface():
    payload = {
        "snapshot_id": "scan-quick",
        "status": "ready",
        "rows": [
            {
                "id": "paris:today",
                "city": "paris",
                "tradable": False,
                "accepting_orders": False,
                "model_probability": 0.42,
                "final_score": 22,
            }
        ],
    }

    report = build_weather_market_signal_report(payload)

    assert report["schema_version"] == "polyweather_weather_market_signal_report.v1"
    assert report["summary"]["candidate_count"] == 0
    assert report["summary"]["reject_count"] == 1
    assert report["summary"]["live_gate"] is False
    assert report["summary"]["live_authorization_pct"] == 0
    reasons = {item["reason"] for item in report["rejection_reason_counts"]}
    assert "row_not_tradable" in reasons
    assert "not_accepting_orders" in reasons
    assert "missing_market_price" in reasons
    assert "missing_edge" in reasons
    sample_reasons = {item["reason"]: item["samples"] for item in report["rejection_reason_samples"]}
    assert sample_reasons["missing_edge"][0]["city"] == "paris"
    assert sample_reasons["missing_edge"][0]["price"] is None


def test_signal_report_keeps_watch_separate_from_candidate():
    payload = {
        "snapshot_id": "scan-watch",
        "status": "ready",
        "rows": [
            {
                "id": "watch-row",
                "city": "seoul",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-30c",
                "side": "yes",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "market_probability": 0.20,
                "spread": 0.01,
                "execution_liquidity": 900,
                "edge_percent": 7.0,
                "model_probability": 0.03,
                **_settlement(bucket_type="eq", threshold=30.0),
            }
        ],
    }

    report = build_weather_market_signal_report(payload)

    assert report["summary"]["candidate_count"] == 0
    assert report["summary"]["watch_count"] == 1
    assert report["watch"][0]["warnings"] == ["model_probability_too_low"]
    assert report["summary"]["live_gate"] is False


def test_signal_report_exposes_market_family_model_coverage_without_unlocking_trade():
    payload = {
        "snapshot_id": "coverage-scan",
        "status": "ready",
        "rows": [
            {
                "id": "temp-row",
                "market_family": "temperature",
                "city": "seoul",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-30c",
                "side": "yes",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "market_probability": 0.20,
                "price": 0.20,
                "spread": 0.01,
                "execution_liquidity": 900,
                "edge_percent": 7.0,
                "model_probability": 0.20,
                "model_join_status": "joined",
                **_settlement(bucket_type="eq", threshold=30.0),
            },
            {
                "id": "hurricane-row",
                "market_family": "hurricane",
                "market_slug": "will-any-category-4-hurricane-make-landfall",
                "side": "yes",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.34,
                "spread": 0.01,
                "execution_liquidity": 900,
                "model_join_status": "unsupported_market_type",
            },
        ],
    }

    report = build_weather_market_signal_report(payload)

    coverage = report["coverage_diagnostics"]
    assert report["summary"]["candidate_count"] == 1
    assert {row["market_family"]: row["count"] for row in coverage["market_family_counts"]} == {
        "hurricane": 1,
        "temperature": 1,
    }
    assert coverage["unsupported_market_family_counts"] == [{"market_family": "hurricane", "count": 1}]
    assert any(
        row["market_family"] == "hurricane"
        and row["reject_count"] == 1
        and row["candidate_count"] == 0
        for row in coverage["decision_by_market_family"]
    )
    rejection_reasons = {row["reason"] for row in report["rejection_reason_counts"]}
    assert "missing_edge" in rejection_reasons
    assert "unsupported_model_family:hurricane" in rejection_reasons
    categories = {
        row["category"]: row["count"]
        for row in report["candidate_gap_report"]["non_risk_blocker_category_counts"]
    }
    assert categories["model_coverage"] == 1


def test_signal_report_rejects_candidate_matching_negative_markout_rule():
    payload = {
        "snapshot_id": "risk-scan",
        "status": "ready",
        "rows": [
            {
                "id": "ankara-eq-no",
                "city": "ankara",
                "market_slug": "highest-temperature-in-ankara-on-june-28-2026-28c",
                "side": "no",
                "bucket_label": "= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.58,
                "spread": 0.01,
                "execution_liquidity": 1200,
                "edge_percent": 33.0,
                "model_probability": 0.91,
                **_settlement(city="ankara", bucket_type="eq", threshold=28.0, station_code="LTAC"),
            }
        ],
    }

    report = build_weather_market_signal_report(
        payload,
        risk_rules=[
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_city",
                "dimensions": {"city": "ankara"},
                "count": 7,
                "win_rate": 0.0,
                "mean_markout_cents": -1.6,
            }
        ],
    )

    assert report["summary"]["candidate_count"] == 0
    assert report["summary"]["reject_count"] == 1
    assert report["risk_filter"]["enabled"] is True
    assert report["risk_filter"]["hit_count"] == 1
    assert report["risk_filter"]["risk_rule_reject_count"] == 1
    assert report["risk_filter"]["risk_only_reject_count"] == 1
    assert report["risk_filter"]["would_be_candidate_without_risk_rule_count"] == 1
    assert report["summary"]["quarantine_count"] == 1
    assert report["summary"]["quarantine_candidate_without_risk_count"] == 1
    assert report["quarantine"][0]["decision"] == "quarantine"
    assert report["quarantine"][0]["original_decision"] == "reject"
    assert report["quarantine"][0]["would_be_decision_without_risk_rules"] == "candidate"
    assert report["quarantine"][0]["counts_for_live_gate"] is False
    assert report["summary"]["live_blockers"][0] == "no_strict_candidate"
    assert report["rejection_reason_counts"][0]["reason"].startswith("negative_markout_rule:")


def test_signal_report_surfaces_saturated_risk_rules():
    payload = {
        "snapshot_id": "risk-saturation",
        "status": "ready",
        "rows": [
            {
                "id": f"row-{index}",
                "market_family": "temperature",
                "city": "ankara" if index == 0 else "seoul",
                "market_slug": f"highest-temperature-{index}",
                "side": "yes",
                "bucket_label": ">= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.2,
                "spread": 0.01,
                "execution_liquidity": 1000,
                "edge_percent": 20.0,
                "model_probability": 0.5,
                **_settlement(city="ankara" if index == 0 else "seoul", bucket_type="ge"),
            }
            for index in range(3)
        ],
    }

    report = build_weather_market_signal_report(
        payload,
        risk_rules=[
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_market_family",
                "dimensions": {"market_family": "temperature"},
            }
        ],
    )

    saturated = report["risk_filter"]["saturated_rules"]
    assert report["risk_filter"]["saturated_rule_count"] == 1
    assert saturated[0]["reason"] == "negative_markout_rule:by_market_family:market_family=temperature"
    assert saturated[0]["coverage"] == 1.0
    assert saturated[0]["scope"] == "broad"


def test_signal_report_can_suppress_saturated_broad_rules_without_removing_specific_rules():
    payload = {
        "snapshot_id": "risk-saturation-suppress",
        "status": "ready",
        "rows": [
            {
                "id": f"row-{index}",
                "market_family": "temperature",
                "city": "ankara" if index == 0 else "seoul",
                "market_slug": f"highest-temperature-{index}",
                "side": "yes",
                "bucket_label": ">= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.2,
                "spread": 0.01,
                "execution_liquidity": 1000,
                "edge_percent": 20.0,
                "model_probability": 0.5,
                **_settlement(city="ankara" if index == 0 else "seoul", bucket_type="ge"),
            }
            for index in range(3)
        ],
    }

    report = build_weather_market_signal_report(
        payload,
        config=WeatherMarketSignalConfig(suppress_saturated_broad_risk_rules=True),
        risk_rules=[
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_market_family",
                "dimensions": {"market_family": "temperature"},
            },
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_city",
                "dimensions": {"city": "ankara"},
            },
        ],
    )

    risk_filter = report["risk_filter"]
    assert risk_filter["source_rule_count"] == 2
    assert risk_filter["initial_rule_count"] == 2
    assert risk_filter["rule_count"] == 1
    assert risk_filter["suppressed_saturated_rule_count"] == 1
    assert risk_filter["suppressed_saturated_rules"][0]["group"] == "by_market_family"
    assert report["summary"]["candidate_count"] == 2
    assert report["summary"]["reject_count"] == 1
    assert report["rejection_reason_counts"] == [
        {"reason": "negative_markout_rule:by_city:city=ankara", "count": 1}
    ]


def test_signal_report_can_suppress_partition_saturated_rules():
    payload = {
        "snapshot_id": "risk-partition-suppress",
        "status": "ready",
        "rows": [
            {
                "id": f"row-{index}",
                "market_family": "temperature",
                "city": "ankara" if index == 0 else "seoul",
                "market_slug": f"highest-temperature-{index}",
                "side": "yes" if index % 2 == 0 else "no",
                "bucket_label": ">= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.2,
                "spread": 0.01,
                "execution_liquidity": 1000,
                "edge_percent": 20.0,
                "model_probability": 0.5,
                **_settlement(city="ankara" if index == 0 else "seoul", bucket_type="ge"),
            }
            for index in range(4)
        ],
    }

    report = build_weather_market_signal_report(
        payload,
        config=WeatherMarketSignalConfig(suppress_saturated_partition_risk_rules=True),
        risk_rules=[
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_side",
                "dimensions": {"side": "yes"},
            },
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_side",
                "dimensions": {"side": "no"},
            },
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_city",
                "dimensions": {"city": "ankara"},
            },
        ],
    )

    risk_filter = report["risk_filter"]
    assert risk_filter["suppressed_saturated_group_rule_count"] == 2
    assert {row["group"] for row in risk_filter["suppressed_saturated_group_rules"]} == {"by_side"}
    assert risk_filter["pre_suppression_saturated_groups"][0]["group"] == "by_side"
    assert risk_filter["pre_suppression_saturated_groups"][0]["reason_count"] == 2
    assert report["summary"]["candidate_count"] == 3
    assert report["summary"]["reject_count"] == 1
    assert report["rejection_reason_counts"] == [
        {"reason": "negative_markout_rule:by_city:city=ankara", "count": 1}
    ]


def test_signal_report_does_not_suppress_single_reason_medium_saturation_as_partition():
    payload = {
        "snapshot_id": "single-reason-medium-saturation",
        "status": "ready",
        "rows": [
            {
                "id": f"row-{index}",
                "market_family": "temperature",
                "city": "ankara",
                "market_slug": f"highest-temperature-{index}",
                "side": "yes",
                "bucket_label": "= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.2,
                "spread": 0.01,
                "execution_liquidity": 1000,
                "edge_percent": 20.0,
                "model_probability": 0.5,
            }
            for index in range(3)
        ],
    }

    report = build_weather_market_signal_report(
        payload,
        config=WeatherMarketSignalConfig(suppress_saturated_partition_risk_rules=True),
        risk_rules=[
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_bucket_type",
                "dimensions": {"bucket_type": "eq"},
            },
        ],
    )

    risk_filter = report["risk_filter"]
    assert risk_filter["saturated_rule_count"] == 1
    assert risk_filter["saturated_groups"][0]["reason_count"] == 1
    assert risk_filter["suppressed_saturated_group_rule_count"] == 0
    assert report["summary"]["candidate_count"] == 0
    assert report["summary"]["reject_count"] == 3


def test_signal_report_explains_candidate_gap_without_changing_decisions():
    payload = {
        "snapshot_id": "gap-scan",
        "status": "ready",
        "rows": [
            {
                "id": "risk-only",
                "city": "ankara",
                "market_slug": "highest-temperature-in-ankara-on-june-28-2026-28c",
                "side": "yes",
                "bucket_label": "= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.12,
                "spread": 0.01,
                "execution_liquidity": 1200,
                "edge_percent": 20.0,
                "model_probability": 0.33,
                **_settlement(city="ankara", bucket_type="eq", threshold=28.0, station_code="LTAC"),
            },
            {
                "id": "edge-only",
                "city": "paris",
                "market_slug": "highest-temperature-in-paris-on-june-28-2026-28c",
                "side": "yes",
                "bucket_label": "= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.12,
                "spread": 0.01,
                "execution_liquidity": 1200,
                "edge_percent": 2.0,
                "model_probability": 0.33,
                **_settlement(city="paris", bucket_type="eq", threshold=28.0, station_code="LFPB"),
            },
            {
                "id": "spread-only",
                "city": "seoul",
                "market_slug": "highest-temperature-in-seoul-on-june-28-2026-28c",
                "side": "yes",
                "bucket_label": "= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.12,
                "spread": 0.04,
                "execution_liquidity": 1200,
                "edge_percent": 20.0,
                "model_probability": 0.33,
                **_settlement(bucket_type="eq", threshold=28.0),
            },
        ],
    }

    report = build_weather_market_signal_report(
        payload,
        risk_rules=[
            {
                "action": "do_not_live_until_positive_markout",
                "group": "maker_quote_by_city",
                "dimensions": {"city": "ankara"},
            }
        ],
    )

    gap = report["candidate_gap_report"]
    assert report["summary"]["candidate_count"] == 0
    assert gap["total_rejected"] == 3
    assert gap["risk_only_reject_count"] == 1
    assert gap["risk_only_candidate_without_risk_count"] == 1
    assert gap["single_non_risk_blocker_count"] == 2
    assert gap["edge_only_reject_count"] == 1
    assert gap["spread_only_reject_count"] == 1
    assert gap["maker_rule_only_reject_count"] == 1
    assert gap["risk_only_with_maker_rule_count"] == 1
    assert gap["risk_rule_scope_counts"] == [{"scope": "medium", "count": 1}]
    assert gap["risk_only_rule_scope_counts"] == [{"scope": "medium", "count": 1}]
    assert gap["near_candidates"][0]["city"] == "ankara"
    assert gap["near_candidates"][0]["risk_rule_scope_counts"] == [{"scope": "medium", "count": 1}]
    assert gap["near_candidates"][0]["would_be_decision_without_risk_rules"] == "candidate"
    categories = {item["category"]: item["count"] for item in gap["non_risk_blocker_category_counts"]}
    assert categories == {"edge": 1, "spread": 1}


def test_signal_report_strict_gate_diagnostics_focus_live_eligible_rows():
    payload = {
        "snapshot_id": "strict-gates",
        "status": "ready",
        "rows": [
            {
                "id": "eq-shadow",
                "city": "ankara",
                "market_slug": "highest-temperature-in-ankara-on-june-28-2026-28c",
                "side": "yes",
                "bucket_label": "= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.12,
                "spread": 0.01,
                "execution_liquidity": 1200,
                "bid_depth_usdc_3c": 20,
                "ask_depth_usdc_3c": 20,
                "edge_percent": 20.0,
                "model_probability": 0.33,
                **_settlement(city="ankara", bucket_type="eq", threshold=28.0, station_code="LTAC"),
            },
            {
                "id": "bad-ev",
                "city": "seoul",
                "market_slug": "highest-temperature-in-seoul-on-june-28-2026-29c-or-above",
                "side": "yes",
                "bucket_label": ">= 29°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.25,
                "spread": 0.01,
                "execution_liquidity": 1200,
                "bid_depth_usdc_3c": 20,
                "ask_depth_usdc_3c": 20,
                "edge_percent": 20.0,
                "model_probability": 0.40,
                **_settlement(city="seoul", bucket_type="ge", threshold=29.0, station_code="RKSI"),
                "p_lcb": 0.24,
                "q_effective": 0.25,
                "cost": 0.01,
                "ev_safe": -0.02,
            },
            {
                "id": "thin-depth",
                "city": "seoul",
                "market_slug": "highest-temperature-in-seoul-on-june-28-2026-30c-or-above",
                "side": "yes",
                "bucket_label": ">= 30°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.25,
                "spread": 0.01,
                "execution_liquidity": 1200,
                "bid_depth_usdc_3c": 20,
                "ask_depth_usdc_3c": 3,
                "edge_percent": 20.0,
                "model_probability": 0.40,
                **_settlement(city="seoul", bucket_type="ge", threshold=30.0, station_code="RKSI"),
            },
            {
                "id": "strict-candidate",
                "city": "seoul",
                "market_slug": "highest-temperature-in-seoul-on-june-28-2026-31c-or-above",
                "side": "yes",
                "bucket_label": ">= 31°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.25,
                "spread": 0.01,
                "execution_liquidity": 1200,
                "bid_depth_usdc_3c": 20,
                "ask_depth_usdc_3c": 20,
                "edge_percent": 20.0,
                "model_probability": 0.40,
                **_settlement(city="seoul", bucket_type="ge", threshold=31.0, station_code="RKSI"),
            },
        ],
    }

    report = build_weather_market_signal_report(
        payload,
        config=WeatherMarketSignalConfig(
            min_liquidity=10,
            min_bid_depth_usdc_3c=10,
            min_ask_depth_usdc_3c=10,
        ),
    )

    strict = report["strict_gate_diagnostics"]
    assert strict["live_eligible_row_count"] == 3
    assert strict["paper_only_row_count"] == 1
    assert strict["live_eligible_candidate_count"] == 1
    assert strict["live_eligible_reject_count"] == 2
    categories = {row["category"]: row["count"] for row in strict["non_risk_blocker_category_counts"]}
    assert categories == {"depth": 1, "edge": 1}
    assert strict["by_strategy"] == [
        {
            "strategy_id": "tail_threshold",
            "row_count": 3,
            "candidate_count": 1,
            "watch_count": 0,
            "reject_count": 2,
            "top_non_risk_categories": [
                {"category": "depth", "count": 1},
                {"category": "edge", "count": 1},
            ],
        }
    ]
    sample_ids = {row["market_slug"] for row in strict["top_live_eligible_reject_samples"]}
    assert "highest-temperature-in-seoul-on-june-28-2026-29c-or-above" in sample_ids
    assert "highest-temperature-in-seoul-on-june-28-2026-30c-or-above" in sample_ids
    queues = strict["targeted_paper_queues"]
    assert queues["ev_calibration"]["paper_only"] is True
    assert queues["ev_calibration"]["counts_for_live_gate"] is False
    assert queues["ev_calibration"]["row_count"] == 1
    assert queues["ev_calibration"]["items"][0]["market_slug"] == (
        "highest-temperature-in-seoul-on-june-28-2026-29c-or-above"
    )
    assert queues["ev_calibration"]["items"][0]["queue_reasons"] == ["ev_safe_below_min"]
    assert queues["execution_depth_price"]["row_count"] == 1
    assert queues["execution_depth_price"]["items"][0]["market_slug"] == (
        "highest-temperature-in-seoul-on-june-28-2026-30c-or-above"
    )
    assert queues["execution_depth_price"]["items"][0]["queue_reasons"] == ["ask_depth_below_min"]
    assert queues["risk_rule_review"]["row_count"] == 0


def test_signal_report_can_quarantine_single_non_risk_near_misses_without_live_signal():
    payload = {
        "snapshot_id": "near-miss-scan",
        "status": "ready",
        "rows": [
            {
                "id": "spread-only",
                "city": "seoul",
                "market_slug": "highest-temperature-in-seoul-on-june-28-2026-28c",
                "side": "yes",
                "bucket_label": "= 28°C",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.12,
                "spread": 0.04,
                "execution_liquidity": 1200,
                "edge_percent": 20.0,
                "model_probability": 0.33,
                **_settlement(bucket_type="eq", threshold=28.0),
            },
            {
                "id": "unsupported",
                "market_family": "hurricane",
                "market_slug": "hurricane-season-2026",
                "side": "yes",
                "bucket_label": "Yes",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.12,
                "spread": 0.01,
                "execution_liquidity": 1200,
                "edge_percent": 20.0,
                "model_probability": 0.33,
                "model_join_status": "unsupported_market_type",
            },
        ],
    }

    report = build_weather_market_signal_report(
        payload,
        config=WeatherMarketSignalConfig(quarantine_near_miss_categories=("spread",)),
    )

    assert report["summary"]["candidate_count"] == 0
    assert report["summary"]["quarantine_count"] == 1
    assert report["summary"]["quarantine_near_miss_count"] == 1
    assert report["summary"]["quarantine_risk_only_count"] == 0
    assert report["quarantine"][0]["decision"] == "quarantine"
    assert report["quarantine"][0]["quarantine_reason"] == "single_non_risk_blocker:spread"
    assert report["quarantine"][0]["quarantine_non_risk_blocker_categories"] == ["spread"]
    assert report["quarantine"][0]["counts_for_live_gate"] is False
    assert report["quarantine"][0]["live_gate_excluded"] is True
    assert report["coverage_diagnostics"]["quarantine_by_market_family"] == [
        {"market_family": "temperature", "count": 1}
    ]
    assert report["coverage_diagnostics"]["quarantine_by_reason"] == [
        {"quarantine_reason": "single_non_risk_blocker:spread", "count": 1}
    ]
    assert {row["reason"] for row in report["rejection_reason_counts"]} >= {
        "spread_above_max",
        "unsupported_model_family:hurricane",
    }


def test_signal_report_exploration_mode_does_not_apply_broad_city_rule():
    payload = {
        "snapshot_id": "risk-scan",
        "status": "ready",
        "rows": [
            {
                "id": "new-york-range-no",
                "city": "new york",
                "market_slug": "highest-temperature-in-nyc-on-june-27-2026-between-78-79f",
                "side": "no",
                "bucket_label": "78-79°F",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.61,
                "spread": 0.02,
                "execution_liquidity": 1200,
                "edge_percent": 20.0,
                "model_probability": 0.81,
                **_settlement(city="new york", bucket_type="range", threshold=78.0, station_code="KLGA"),
            }
        ],
    }
    risk_rules = [
        {
            "action": "do_not_live_until_positive_markout",
            "group": "by_city",
            "dimensions": {"city": "new york"},
        }
    ]

    live_report = build_weather_market_signal_report(payload, risk_rules=risk_rules)
    exploration_report = build_weather_market_signal_report(
        payload,
        risk_rules=risk_rules,
        risk_rule_mode="exploration",
    )

    assert live_report["summary"]["candidate_count"] == 0
    assert live_report["risk_filter"]["rule_count"] == 1
    assert exploration_report["summary"]["candidate_count"] == 1
    assert exploration_report["risk_filter"]["rule_count"] == 0
    assert exploration_report["risk_filter"]["source_rule_count"] == 1


def test_signal_cli_builds_report_from_scan_payload(monkeypatch, capsys):
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "scan-cli",
            "status": "ready",
            "rows": [
                {
                    "id": "row",
                    "market_slug": "m",
                    "side": "yes",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.20,
                    "spread": 0.01,
                    "execution_liquidity": 1000,
                    "edge_percent": 9.0,
                    "model_probability": 0.25,
                    **_settlement(bucket_type="eq", threshold=28.0),
                }
            ],
        },
    )

    signal_cli.main(
        [
            "--filters-json",
            '{"limit": 3}',
            "--min-edge-percent",
            "5",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["source_snapshot_id"] == "scan-cli"
    assert output["summary"]["candidate_count"] == 1
    assert output["summary"]["live_gate"] is False


def test_signal_cli_builds_report_from_polymarket_payload(monkeypatch, capsys):
    calls = {}

    def fake_payload(**kwargs):
        calls.update(kwargs)
        return {
            "snapshot_id": "poly-cli",
            "status": "ready",
            "rows": [
                {
                    "id": "poly-row",
                    "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c",
                    "side": "yes",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.20,
                    "spread": 0.01,
                        "execution_liquidity": 1000,
                        "edge_percent": 9.0,
                        "model_probability": 0.25,
                        **_settlement(bucket_type="eq", threshold=28.0),
                    }
            ],
        }

    monkeypatch.setattr(signal_cli, "build_polymarket_weather_payload", fake_payload)

    signal_cli.main(
        [
            "--source",
            "polymarket",
            "--polymarket-query",
            "temperature",
            "--polymarket-row-limit",
            "7",
            "--min-edge-percent",
            "5",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["queries"] == ["temperature"]
    assert calls["row_limit"] == 7
    assert calls["include_order_books"] is True
    assert calls["exclude_expired_markets"] is True
    assert calls["exclude_not_accepting_orders"] is True
    assert output["source_snapshot_id"] == "poly-cli"
    assert output["summary"]["candidate_count"] == 1
    assert output["summary"]["live_gate"] is False


def test_signal_cli_can_join_polymarket_to_scan_models(monkeypatch, capsys):
    monkeypatch.setattr(
        signal_cli,
        "build_polymarket_weather_payload",
        lambda **kwargs: {
            "snapshot_id": "poly-cli",
            "status": "ready",
            "source": "polymarket_readonly",
            "diagnostics": {},
            "rows": [
                {
                    "id": "yes-row",
                    "event_title": "Highest temperature in Seoul on June 27?",
                    "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
                    "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28corabove",
                    "end_date": "2026-06-27T12:00:00Z",
                    "outcome": "Yes",
                    "side": "yes",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.52,
                    "spread": 0.01,
                    "execution_liquidity": 1000,
                }
            ],
        },
    )
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "scan-cli",
            "status": "ready",
            "rows": [
                {
                    "id": "seoul:2026-06-27",
                    "city": "seoul",
                    "local_date": "2026-06-27",
                    "distribution_full": [
                        {"value": 27, "probability": 0.30},
                        {"value": 28, "probability": 0.45},
                        {"value": 29, "probability": 0.25},
                    ],
                }
            ],
        },
    )

    signal_cli.main(
        [
            "--source",
            "polymarket",
            "--join-scan-models",
            "--no-analysis-fallback",
            "--min-edge-percent",
            "5",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["candidate_count"] == 1
    assert output["source_diagnostics"]["model_join"]["joined"] == 1
    assert output["candidates"][0]["edge_percent"] == 18.0
    assert output["candidates"][0]["model_probability"] == 0.70


def test_signal_cli_join_can_use_analysis_fallback(monkeypatch, capsys):
    monkeypatch.setattr(
        signal_cli,
        "build_polymarket_weather_payload",
        lambda **kwargs: {
            "snapshot_id": "poly-cli",
            "status": "ready",
            "source": "polymarket_readonly",
            "diagnostics": {},
            "rows": [
                {
                    "id": "yes-row",
                    "event_title": "Highest temperature in Seoul on June 27?",
                    "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
                    "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28corabove",
                    "end_date": "2026-06-27T12:00:00Z",
                    "outcome": "Yes",
                    "side": "yes",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.52,
                    "spread": 0.01,
                    "execution_liquidity": 1000,
                }
            ],
        },
    )
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "empty-scan",
            "status": "ready",
            "rows": [],
        },
    )
    monkeypatch.setattr(
        signal_cli,
        "build_analysis_model_payload_for_targets",
        lambda targets, force_refresh=False, detail_mode="panel": {
            "snapshot_id": "fallback-model",
            "status": "ready",
            "rows": [
                {
                    "id": "seoul:2026-06-27",
                    "city": "seoul",
                    "selected_date": "2026-06-27",
                    "distribution_full": [
                        {"value": 27, "probability": 0.30},
                        {"value": 28, "probability": 0.45},
                        {"value": 29, "probability": 0.25},
                    ],
                }
            ],
            "diagnostics": {"model_rows": 1},
        },
    )

    signal_cli.main(
        [
            "--source",
            "polymarket",
            "--join-scan-models",
            "--min-edge-percent",
            "5",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["candidate_count"] == 1
    assert output["source_diagnostics"]["model_join"]["joined"] == 1
    assert output["source_diagnostics"]["model_join"]["scan_rows"] == 1
    assert output["candidates"][0]["edge_percent"] == 18.0


def test_signal_cli_paper_tail_profile_keeps_live_gate_closed(monkeypatch, capsys):
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "scan-tail",
            "status": "ready",
            "rows": [
                {
                    "id": "tail-row",
                    "market_slug": "highest-temperature-in-seoul-on-june-27-2026-31corhigher",
                    "side": "yes",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.029,
                    "spread": 0.006,
                    "execution_liquidity": 0.65,
                    "edge_percent": 7.8,
                    "model_probability": 0.108,
                    **_settlement(bucket_type="ge", threshold=31.0),
                }
            ],
        },
    )

    signal_cli.main(
        [
            "--filters-json",
            '{"limit": 1}',
            "--paper-tail-profile",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["candidate_count"] == 1
    assert output["summary"]["live_gate"] is False
    assert output["summary"]["live_authorization_pct"] == 0
    assert output["config"]["min_price"] == 0.001
    assert output["config"]["min_liquidity"] == 0.0
    assert output["config"]["require_order_book"] is False


def test_signal_cli_tight_exploration_profile_keeps_spread_strict(monkeypatch, capsys):
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "scan-tight",
            "status": "ready",
            "rows": [
                {
                    "id": "tight-row",
                    "market_slug": "highest-temperature-in-seoul-on-june-28-2026-24c",
                    "side": "yes",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.009,
                    "spread": 0.002,
                    "execution_liquidity": 1.0,
                    "edge_percent": 12.5,
                    "model_probability": 0.134,
                    **_settlement(bucket_type="eq", threshold=24.0),
                }
            ],
        },
    )

    signal_cli.main(
        [
            "--filters-json",
            '{"limit": 1}',
            "--tight-exploration-profile",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["candidate_count"] == 1
    assert output["config"]["min_price"] == 0.001
    assert output["config"]["min_liquidity"] == 0.0
    assert output["config"]["max_spread"] == 0.005
    assert output["config"]["require_order_book"] is True


def test_signal_cli_quality_surface_profile_filters_to_deeper_non_eq_books(monkeypatch, capsys):
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "scan-quality",
            "status": "ready",
            "rows": [
                {
                    "id": "range-no",
                    "market_slug": "highest-temperature-in-seoul-on-june-28-2026-between-28-29c",
                    "side": "no",
                    "bucket_label": "28-29°C",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.20,
                    "spread": 0.01,
                    "execution_liquidity": 20.0,
                    "order_book": {
                        "bid_depth_usdc_3c": 12.0,
                        "ask_depth_usdc_3c": 20.0,
                    },
                    "edge_percent": 12.5,
                    "model_probability": 0.34,
                    **_settlement(bucket_type="range", threshold=28.0),
                },
                {
                    "id": "eq-yes",
                    "market_slug": "highest-temperature-in-seoul-on-june-28-2026-28c",
                    "side": "yes",
                    "bucket_label": "= 28°C",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.20,
                    "spread": 0.01,
                    "execution_liquidity": 20.0,
                    "order_book": {
                        "bid_depth_usdc_3c": 12.0,
                        "ask_depth_usdc_3c": 20.0,
                    },
                    "edge_percent": 12.5,
                    "model_probability": 0.34,
                    **_settlement(bucket_type="eq", threshold=28.0),
                },
            ],
        },
    )

    signal_cli.main(
        [
            "--filters-json",
            '{"limit": 2}',
            "--quality-surface-profile",
            "--allowed-side",
            "no",
            "--max-candidates",
            "5",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["candidate_count"] == 1
    assert output["candidates"][0]["side"] == "no"
    assert output["candidates"][0]["bucket_type"] == "range"
    assert output["config"]["min_price"] == 0.03
    assert output["config"]["min_liquidity"] == 10.0
    assert output["config"]["max_spread"] == 0.02
    assert output["config"]["require_order_book"] is True
    assert output["config"]["excluded_bucket_types"] == ["eq"]
    assert output["config"]["allowed_sides"] == ["no"]
    assert output["config"]["min_bid_depth_usdc_3c"] == 10.0
    assert output["config"]["min_ask_depth_usdc_3c"] == 10.0
    reasons = {item["reason"] for item in output["rejection_reason_counts"]}
    assert "bucket_type_excluded:eq" in reasons


def test_signal_cli_can_include_full_assessments(monkeypatch, capsys):
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "scan-assessments",
            "status": "ready",
            "rows": [
                {
                    "id": "row",
                    "market_slug": "m",
                    "side": "yes",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.20,
                    "spread": 0.01,
                    "execution_liquidity": 1000,
                    "edge_percent": 9.0,
                    "model_probability": 0.25,
                }
            ],
        },
    )

    signal_cli.main(
        [
            "--filters-json",
            '{"limit": 1}',
            "--include-assessments",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert len(output["assessments"]) == 1
    assert output["assessments"][0]["row_id"] == "row"


def test_signal_cli_can_apply_markout_risk_rules(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "scan-risk",
            "status": "ready",
            "rows": [
                {
                    "id": "ankara-risk",
                    "city": "ankara",
                    "market_slug": "m",
                    "side": "no",
                    "bucket_label": "= 28°C",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.58,
                    "spread": 0.01,
                    "execution_liquidity": 1000,
                    "edge_percent": 33.0,
                    "model_probability": 0.91,
                }
            ],
        },
    )
    calls = {}

    def fake_risk_rules(**kwargs):
        calls["risk_rules"] = kwargs
        return {
            "rules": [
                {
                    "action": "do_not_live_until_positive_markout",
                    "group": "by_city",
                    "dimensions": {"city": "ankara"},
                }
            ]
        }

    monkeypatch.setattr(signal_cli, "build_risk_rules_from_journal", fake_risk_rules)

    signal_cli.main(
        [
            "--filters-json",
            '{"limit": 1}',
            "--paper-journal-dir",
            str(tmp_path),
            "--apply-markout-risk-rules",
            "--risk-rule-min-count",
            "4",
            "--risk-rule-mode",
            "live",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["candidate_count"] == 0
    assert output["risk_filter"]["hit_count"] == 1
    assert output["risk_filter"]["mode"] == "live"
    assert calls["risk_rules"]["journal_dir"] == str(tmp_path)
    assert calls["risk_rules"]["min_count"] == 4
    assert calls["risk_rules"]["include_quarantine_surface_rules"] is False


def test_signal_cli_can_apply_quarantine_surface_risk_rules(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "scan-quarantine-risk",
            "status": "ready",
            "rows": [
                {
                    "id": "ankara-risk",
                    "city": "ankara",
                    "market_slug": "m",
                    "side": "no",
                    "bucket_label": "= 28°C",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.58,
                    "spread": 0.01,
                    "execution_liquidity": 1000,
                    "edge_percent": 33.0,
                    "model_probability": 0.91,
                }
            ],
        },
    )
    calls = {}

    def fake_risk_rules(**kwargs):
        calls["risk_rules"] = kwargs
        return {
            "rules": [
                {
                    "action": "do_not_live_until_positive_markout",
                    "group": "by_city",
                    "dimensions": {"city": "ankara"},
                    "source": "quarantine_surface",
                }
            ]
        }

    monkeypatch.setattr(signal_cli, "build_risk_rules_from_journal", fake_risk_rules)

    signal_cli.main(
        [
            "--filters-json",
            '{"limit": 1}',
            "--paper-journal-dir",
            str(tmp_path / "formal"),
            "--apply-quarantine-surface-risk-rules",
            "--quarantine-journal-dir",
            str(tmp_path / "quarantine"),
            "--quarantine-surface-min-decision-count",
            "2",
            "--quarantine-surface-min-promote-count",
            "3",
            "--suppress-saturated-broad-risk-rules",
            "--suppress-saturated-partition-risk-rules",
            "--saturated-risk-rule-min-coverage",
            "0.75",
            "--partition-saturated-risk-rule-min-coverage",
            "0.85",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["candidate_count"] == 0
    assert output["risk_filter"]["hit_count"] == 1
    assert output["config"]["suppress_saturated_broad_risk_rules"] is True
    assert output["config"]["suppress_saturated_partition_risk_rules"] is True
    assert output["config"]["saturated_risk_rule_min_coverage"] == 0.75
    assert output["config"]["partition_saturated_risk_rule_min_coverage"] == 0.85
    assert calls["risk_rules"]["include_quarantine_surface_rules"] is True
    assert calls["risk_rules"]["quarantine_journal_dir"] == str(tmp_path / "quarantine")
    assert calls["risk_rules"]["quarantine_surface_min_decision_count"] == 2
    assert calls["risk_rules"]["quarantine_surface_min_promote_count"] == 3


def test_signal_cli_can_write_paper_journal(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        signal_cli,
        "build_scan_terminal_payload",
        lambda filters, force_refresh=False: {
            "snapshot_id": "scan-paper",
            "status": "ready",
            "source": "test",
            "rows": [
                {
                    "id": "tail-row",
                    "market_slug": "highest-temperature-in-seoul-on-june-27-2026-31corhigher",
                    "token_id": "token",
                    "side": "yes",
                    "outcome": "Yes",
                    "active": True,
                    "closed": False,
                    "tradable": True,
                    "accepting_orders": True,
                    "price": 0.029,
                    "spread": 0.006,
                    "execution_liquidity": 0.65,
                        "edge_percent": 7.8,
                        "model_probability": 0.108,
                        **_settlement(bucket_type="ge", threshold=31.0),
                    }
                ],
        },
    )

    signal_cli.main(
        [
            "--filters-json",
            '{"limit": 1}',
            "--paper-tail-profile",
            "--write-paper-journal",
            "--paper-journal-dir",
            str(tmp_path),
            "--paper-journal-profile",
            "tail-paper",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["candidate_count"] == 1
    assert output["paper_journal"]["fill_count"] == 1
    fills_path = tmp_path / "paper_fills.jsonl"
    assert fills_path.exists()
    fill = json.loads(fills_path.read_text().splitlines()[0])
    assert fill["paper_only"] is True
    assert fill["token_id"] == "token"
    assert fill["live_gate_at_record"] is False


def test_config_can_allow_missing_order_book_as_watch_not_reject():
    row = {
        "id": "no-spread",
        "market_slug": "market",
        "side": "yes",
        "active": True,
        "closed": False,
        "tradable": True,
        "accepting_orders": True,
        "price": 0.20,
        "execution_liquidity": 900,
        "edge_percent": 9.0,
        "model_probability": 0.25,
    }

    assessment = assess_weather_market_row(
        row,
        config=WeatherMarketSignalConfig(require_order_book=False),
    )

    assert assessment["decision"] == "watch"
    assert assessment["blockers"] == []
    assert assessment["warnings"] == ["missing_spread"]
