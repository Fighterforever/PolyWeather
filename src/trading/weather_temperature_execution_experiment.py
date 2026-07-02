from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_maker_quote_journal import (
    MAKER_QUOTE_SCHEMA_VERSION,
    summarize_maker_quote_journal,
    summarize_maker_quote_strata,
)
from src.trading.weather_paper_journal import (
    _append_jsonl,
    _safe_float,
    load_jsonl,
    markout_open_paper_fills,
    stable_json_hash,
    summarize_markout_strata,
    summarize_paper_journal,
    utc_now_iso,
    write_paper_journal,
)
from src.trading.weather_signal_risk_filter import fine_time_to_expiry_bucket, time_to_expiry_bucket


TEMPERATURE_EXECUTION_EXPERIMENT_SCHEMA_VERSION = (
    "polyweather_weather_temperature_execution_experiment.v1"
)
TEMPERATURE_EXECUTION_SHADOW_VALIDATION_SCHEMA_VERSION = (
    "polyweather_weather_temperature_execution_shadow_validation.v1"
)
DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR = Path("data/trading/weather_temperature_execution_paper")
DEFAULT_TEMPERATURE_TAKER_JOURNAL_DIR = Path("data/trading/weather_temperature_taker_paper")
TEMPERATURE_TAKER_PAPER_SCHEMA_VERSION = "polyweather_weather_temperature_taker_paper.v1"
TEMPERATURE_TAKER_PAPER_VALIDATION_SCHEMA_VERSION = (
    "polyweather_weather_temperature_taker_paper_validation.v1"
)


def _normalized_offsets(offset_cents: Optional[Iterable[float]]) -> List[float]:
    raw = offset_cents if offset_cents is not None else (0.0, 0.5, 1.0, 2.0, 3.0)
    offsets: List[float] = []
    seen = set()
    for value in raw:
        parsed = _safe_float(value)
        if parsed is None or parsed < 0:
            continue
        normalized = round(parsed, 6)
        key = f"{normalized:.6f}"
        if key in seen:
            continue
        seen.add(key)
        offsets.append(normalized)
    return offsets or [0.0]


def _maker_evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    for detail in row.get("maker_hit_details") or []:
        if not isinstance(detail, dict):
            continue
        evidence = detail.get("evidence")
        if isinstance(evidence, dict):
            return evidence
    return {}


def _maker_failure_reasons(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    for detail in row.get("maker_hit_details") or []:
        if not isinstance(detail, dict):
            continue
        for reason in detail.get("failure_reasons") or []:
            text = str(reason or "").strip()
            if text and text not in reasons:
                reasons.append(text)
    return reasons


def _round_or_none(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 6)


def _strategy_name(offset: float) -> str:
    if abs(float(offset)) < 1e-9:
        return "maker_bid"
    text = ("%g" % float(offset)).replace(".", "_")
    return f"maker_bid_minus_{text}c"


def _offset_row(
    row: Dict[str, Any],
    *,
    offset_cents: float,
    adverse_gap_cents: Optional[float],
    min_expected_markout_cents: float,
) -> Dict[str, Any]:
    bid = _safe_float(row.get("bid"))
    ask = _safe_float(row.get("ask") or row.get("price"))
    quote_price = None if bid is None else round(bid - (float(offset_cents) / 100.0), 6)
    valid_price = quote_price is not None and quote_price > 0.0
    quote_savings_vs_bid_cents = float(offset_cents)
    quote_savings_vs_taker_cents = (
        round((ask - quote_price) * 100.0, 6)
        if ask is not None and quote_price is not None
        else None
    )
    required_improvement = None
    if adverse_gap_cents is not None:
        required_improvement = round(abs(adverse_gap_cents) + float(min_expected_markout_cents), 6)
    improvement_covers_adverse = (
        valid_price
        and required_improvement is not None
        and quote_savings_vs_bid_cents >= required_improvement
    )
    return {
        "strategy": _strategy_name(offset_cents),
        "entry_mode": "maker_offset_ladder",
        "quote_offset_cents": float(offset_cents),
        "quote_price": quote_price,
        "valid_price": bool(valid_price),
        "requires_shadow_quote_fill_evidence": True,
        "counts_for_live_gate": False,
        "paper_only": True,
        "quote_savings_vs_bid_cents": _round_or_none(quote_savings_vs_bid_cents),
        "quote_savings_vs_taker_cents": quote_savings_vs_taker_cents,
        "required_improvement_cents": required_improvement,
        "improvement_covers_observed_maker_adverse_selection": bool(improvement_covers_adverse),
        "promotion_ready": False,
        "promotion_blockers": [
            "offset_fill_probability_unproven",
            "offset_markout_unproven",
            "counts_for_live_gate_false",
        ],
    }


def _taker_row(
    row: Dict[str, Any],
    *,
    evidence: Dict[str, Any],
    min_expected_markout_cents: float,
    min_win_rate: float,
) -> Dict[str, Any]:
    missed_taker_markout = _safe_float(evidence.get("mean_missed_taker_markout_cents"))
    positive_proxy = missed_taker_markout is not None and missed_taker_markout >= float(min_expected_markout_cents)
    blockers = ["taker_specific_forward_paper_missing", "counts_for_live_gate_false"]
    if not positive_proxy:
        blockers.append("missed_taker_proxy_not_positive")
    return {
        "strategy": "taker_cross_current_ask",
        "entry_mode": "taker_cross",
        "entry_price": _safe_float(row.get("ask") or row.get("price")),
        "spread_cents_paid": (
            round(float(row.get("spread")) * 100.0, 6)
            if _safe_float(row.get("spread")) is not None
            else None
        ),
        "proxy_mean_markout_cents": missed_taker_markout,
        "proxy_source": "maker_quote_resting_unfilled_missed_taker_markout",
        "min_expected_markout_cents": float(min_expected_markout_cents),
        "min_win_rate": float(min_win_rate),
        "requires_formal_taker_paper": True,
        "counts_for_live_gate": False,
        "paper_only": True,
        "promotion_ready": False,
        "promotion_blockers": blockers,
    }


def _experiment_for_row(
    row: Dict[str, Any],
    *,
    offset_cents: Iterable[float],
    min_expected_markout_cents: float,
    min_win_rate: float,
) -> Dict[str, Any]:
    evidence = _maker_evidence(row)
    mean_maker = _safe_float(evidence.get("mean_maker_markout_cents"))
    maker_win_rate = _safe_float(evidence.get("maker_markout_win_rate"))
    inferred_fills = int(evidence.get("inferred_fill_count") or 0)
    fill_rate = _safe_float(evidence.get("fill_inference_rate"))
    taker = _taker_row(
        row,
        evidence=evidence,
        min_expected_markout_cents=min_expected_markout_cents,
        min_win_rate=min_win_rate,
    )
    offsets = [
        _offset_row(
            row,
            offset_cents=offset,
            adverse_gap_cents=mean_maker,
            min_expected_markout_cents=min_expected_markout_cents,
        )
        for offset in offset_cents
    ]
    valid_offsets = [item for item in offsets if item["valid_price"]]
    offsets_covering_adverse = [
        item
        for item in valid_offsets
        if item["improvement_covers_observed_maker_adverse_selection"]
    ]
    if taker["proxy_mean_markout_cents"] is not None and taker["proxy_mean_markout_cents"] >= float(
        min_expected_markout_cents
    ):
        next_action = "collect_formal_taker_paper"
    elif offsets_covering_adverse:
        next_action = "collect_offset_ladder_shadow_quotes"
    else:
        next_action = "skip_until_surface_changes_or_settlement"
    return {
        "market_slug": row.get("market_slug"),
        "market_id": row.get("market_id"),
        "token_id": row.get("token_id"),
        "question": row.get("question"),
        "city": row.get("city"),
        "side": row.get("side"),
        "outcome": row.get("outcome"),
        "bucket_label": row.get("bucket_label"),
        "bucket_type": row.get("bucket_type"),
        "price": row.get("price"),
        "bid": row.get("bid"),
        "ask": row.get("ask"),
        "spread": row.get("spread"),
        "liquidity": row.get("liquidity"),
        "edge_percent": row.get("edge_percent"),
        "model_probability": row.get("model_probability"),
        "market_probability": row.get("market_probability"),
        "end_date": row.get("end_date"),
        "horizon_hours": row.get("horizon_hours"),
        "maker_evidence": {
            "inferred_fill_count": inferred_fills,
            "fill_inference_rate": fill_rate,
            "mean_maker_markout_cents": mean_maker,
            "maker_markout_win_rate": maker_win_rate,
            "mean_missed_taker_markout_cents": _safe_float(
                evidence.get("mean_missed_taker_markout_cents")
            ),
            "failure_reasons": _maker_failure_reasons(row),
        },
        "taker_cross": taker,
        "maker_offset_ladder": offsets,
        "offsets_covering_adverse_count": len(offsets_covering_adverse),
        "formal_paper_eligible": False,
        "counts_for_live_gate": False,
        "paper_only": True,
        "next_action": next_action,
    }


def _execution_quote_key(record: Dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record.get("market_slug") or "").strip(),
        str(record.get("token_id") or "").strip(),
        str(record.get("quote_strategy") or "").strip(),
        str(record.get("quote_price") or "").strip(),
    )


def _filter_duplicate_open_execution_quotes(
    records: Iterable[Dict[str, Any]],
    *,
    existing_quotes: Iterable[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int]:
    existing_keys = {
        _execution_quote_key(row)
        for row in existing_quotes
        if isinstance(row, dict) and row.get("status") == "open"
    }
    filtered: List[Dict[str, Any]] = []
    duplicate_count = 0
    seen_new = set()
    for record in records:
        key = _execution_quote_key(record)
        if key in existing_keys or key in seen_new:
            duplicate_count += 1
            continue
        filtered.append(record)
        seen_new.add(key)
    return filtered, duplicate_count


def build_temperature_execution_quote_records(
    execution_experiment_report: Dict[str, Any],
    *,
    quote_size: float = 1.0,
    recorded_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    recorded_at = recorded_at or utc_now_iso()
    size = max(0.0, float(quote_size))
    experiment_id = execution_experiment_report.get("experiment_id")
    records: List[Dict[str, Any]] = []
    for experiment in execution_experiment_report.get("experiments") or []:
        if not isinstance(experiment, dict):
            continue
        token_id = str(experiment.get("token_id") or "").strip()
        if not token_id:
            continue
        for quote in experiment.get("maker_offset_ladder") or []:
            if not isinstance(quote, dict) or not quote.get("valid_price"):
                continue
            quote_price = _safe_float(quote.get("quote_price"))
            if quote_price is None or quote_price <= 0.0:
                continue
            quote_strategy = str(quote.get("strategy") or "maker_bid").strip()
            quote_identity = {
                "experiment_id": experiment_id,
                "market_slug": experiment.get("market_slug"),
                "token_id": token_id,
                "quote_strategy": quote_strategy,
                "quote_price": quote_price,
            }
            quote_id = stable_json_hash(quote_identity, length=24)
            records.append(
                {
                    "schema_version": MAKER_QUOTE_SCHEMA_VERSION,
                    "quote_id": quote_id,
                    "fill_id": None,
                    "run_id": experiment_id,
                    "recorded_at": recorded_at,
                    "paper_only": True,
                    "status": "open",
                    "counts_for_live_gate": False,
                    "temperature_execution_experiment": True,
                    "source_experiment_id": experiment_id,
                    "source_experiment_next_action": experiment.get("next_action"),
                    "quote_strategy": quote_strategy,
                    "quote_side": "buy",
                    "quote_reference_price": _safe_float(experiment.get("bid")),
                    "quote_offset_cents": _safe_float(quote.get("quote_offset_cents")),
                    "quote_price": quote_price,
                    "quote_size": size,
                    "quote_notional_usdc": round(quote_price * size, 6),
                    "city": experiment.get("city"),
                    "market_family": "temperature",
                    "question": experiment.get("question"),
                    "market_id": experiment.get("market_id"),
                    "market_slug": experiment.get("market_slug"),
                    "token_id": token_id,
                    "side": experiment.get("side"),
                    "outcome": experiment.get("outcome"),
                    "bucket_label": experiment.get("bucket_label"),
                    "bucket_type": experiment.get("bucket_type"),
                    "signal_bucket": "temperature_execution_experiment",
                    "decision": "quarantine",
                    "entry_ask": _safe_float(experiment.get("ask") or experiment.get("price")),
                    "entry_bid": _safe_float(experiment.get("bid")),
                    "entry_spread": _safe_float(experiment.get("spread")),
                    "entry_liquidity": _safe_float(experiment.get("liquidity")),
                    "edge_percent": _safe_float(experiment.get("edge_percent")),
                    "model_probability": _safe_float(experiment.get("model_probability")),
                    "market_probability": _safe_float(experiment.get("market_probability")),
                    "end_date": experiment.get("end_date"),
                    "time_to_expiry_bucket": time_to_expiry_bucket(
                        experiment.get("end_date"),
                        now=recorded_at,
                    ),
                    "fine_time_to_expiry_bucket": fine_time_to_expiry_bucket(
                        experiment.get("end_date"),
                        now=recorded_at,
                    ),
                    "maker_evidence": experiment.get("maker_evidence") or {},
                    "taker_cross": experiment.get("taker_cross") or {},
                    "required_improvement_cents": quote.get("required_improvement_cents"),
                    "improvement_covers_observed_maker_adverse_selection": quote.get(
                        "improvement_covers_observed_maker_adverse_selection"
                    ),
                }
            )
    return records


def write_temperature_execution_quotes(
    execution_experiment_report: Dict[str, Any],
    *,
    journal_dir: str | Path = DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR,
    quote_size: float = 1.0,
    max_quotes: Optional[int] = None,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    recorded_at = recorded_at or utc_now_iso()
    records = build_temperature_execution_quote_records(
        execution_experiment_report,
        quote_size=quote_size,
        recorded_at=recorded_at,
    )
    if max_quotes is not None:
        records = records[: max(0, int(max_quotes))]
    quotes_path = journal_root / "maker_quotes.jsonl"
    records, duplicate_skipped_count = _filter_duplicate_open_execution_quotes(
        records,
        existing_quotes=load_jsonl(quotes_path),
    )
    written = _append_jsonl(quotes_path, records)
    return {
        "schema_version": MAKER_QUOTE_SCHEMA_VERSION,
        "journal_dir": str(journal_root),
        "quotes_path": str(quotes_path),
        "recorded_at": recorded_at,
        "experiment_id": execution_experiment_report.get("experiment_id"),
        "experiment_count": int(execution_experiment_report.get("experiment_count") or 0),
        "candidate_quote_count": len(records) + duplicate_skipped_count,
        "quote_records_written": written,
        "duplicate_skipped_count": duplicate_skipped_count,
        "quote_size": max(0.0, float(quote_size)),
        "paper_only": True,
        "counts_for_live_gate": False,
    }


def build_temperature_execution_shadow_validation_report(
    *,
    journal_dir: str | Path = DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR,
    min_quote_markouts: int = 5,
    min_inferred_fills: int = 3,
    min_fill_inference_rate: float = 0.05,
    min_mean_maker_markout_cents: float = 0.0,
    min_maker_win_rate: float = 0.55,
    min_missed_taker_markout_cents: float = 0.0,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    journal_root = Path(journal_dir)
    summary = summarize_maker_quote_journal(journal_root, latest_only=True)
    strata = summarize_maker_quote_strata(journal_root, latest_only=True, min_count=1)
    quote_markouts = int(summary.get("quote_markout_count") or 0)
    inferred_fills = int(summary.get("inferred_fill_count") or 0)
    fill_rate = _safe_float(summary.get("fill_inference_rate"))
    maker_mean = _safe_float(summary.get("mean_maker_markout_cents"))
    maker_win_rate = _safe_float(summary.get("maker_markout_win_rate"))
    missed_taker_mean = _safe_float(summary.get("mean_missed_taker_markout_cents"))

    blockers: List[str] = []
    next_action = "collect_more_shadow_markouts"
    if quote_markouts < max(1, int(min_quote_markouts)):
        hard_conclusion = "temperature_execution_shadow_collect_more"
        blockers.append(f"insufficient_quote_markouts_{quote_markouts}_of_{max(1, int(min_quote_markouts))}")
    elif (
        inferred_fills >= max(1, int(min_inferred_fills))
        and fill_rate is not None
        and fill_rate >= float(min_fill_inference_rate)
        and maker_mean is not None
        and maker_mean >= float(min_mean_maker_markout_cents)
        and maker_win_rate is not None
        and maker_win_rate >= float(min_maker_win_rate)
    ):
        hard_conclusion = "temperature_execution_shadow_maker_offset_promotable"
        next_action = "collect_formal_maker_offset_paper"
    elif (
        inferred_fills == 0
        and missed_taker_mean is not None
        and missed_taker_mean >= float(min_missed_taker_markout_cents)
    ):
        hard_conclusion = "temperature_execution_shadow_taker_proxy_positive_maker_unfilled"
        next_action = "collect_formal_taker_paper"
        blockers.append("maker_offset_fill_probability_unproven")
    elif inferred_fills == 0:
        hard_conclusion = "temperature_execution_shadow_maker_no_fill"
        next_action = "continue_offset_ladder_or_skip_surface"
        blockers.append("maker_offset_fill_probability_unproven")
    else:
        hard_conclusion = "temperature_execution_shadow_negative_or_inconclusive"
        next_action = "keep_blocked_until_positive_execution_evidence"
        if maker_mean is None or maker_mean < float(min_mean_maker_markout_cents):
            blockers.append("maker_mean_markout_below_threshold")
        if maker_win_rate is None or maker_win_rate < float(min_maker_win_rate):
            blockers.append("maker_win_rate_below_threshold")

    identity = {
        "journal_dir": str(journal_root),
        "quote_markouts": quote_markouts,
        "inferred_fills": inferred_fills,
        "next_action": next_action,
    }
    return {
        "schema_version": TEMPERATURE_EXECUTION_SHADOW_VALIDATION_SCHEMA_VERSION,
        "generated_at": generated_at,
        "validation_id": stable_json_hash(identity, length=20),
        "journal_dir": str(journal_root),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "config": {
            "min_quote_markouts": max(1, int(min_quote_markouts)),
            "min_inferred_fills": max(1, int(min_inferred_fills)),
            "min_fill_inference_rate": float(min_fill_inference_rate),
            "min_mean_maker_markout_cents": float(min_mean_maker_markout_cents),
            "min_maker_win_rate": float(min_maker_win_rate),
            "min_missed_taker_markout_cents": float(min_missed_taker_markout_cents),
        },
        "summary": {
            "quote_count": summary.get("quote_count"),
            "quote_markout_count": quote_markouts,
            "inferred_fill_count": inferred_fills,
            "fill_inference_rate": fill_rate,
            "resting_unfilled_count": summary.get("resting_unfilled_count"),
            "mean_maker_markout_cents": maker_mean,
            "maker_markout_win_rate": maker_win_rate,
            "mean_missed_taker_markout_cents": missed_taker_mean,
        },
        "strategy_strata": strata.get("by_quote_strategy") or [],
        "strategy_time_spread_strata": strata.get("by_strategy_and_fine_time_to_expiry_and_spread") or [],
        "blockers": blockers,
        "next_action": next_action,
        "hard_conclusion": hard_conclusion,
    }


def _latest_execution_quotes_by_market(quotes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        if not bool(quote.get("temperature_execution_experiment")):
            continue
        key = (
            str(quote.get("market_slug") or "").strip(),
            str(quote.get("token_id") or "").strip(),
            str(quote.get("side") or "").strip().lower(),
        )
        if not all(key):
            continue
        current = latest.get(key)
        if current is None or str(quote.get("recorded_at") or "") >= str(current.get("recorded_at") or ""):
            latest[key] = quote
    return list(latest.values())


def build_temperature_taker_paper_signal_report(
    *,
    execution_journal_dir: str | Path = DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR,
    validation_report: Optional[Dict[str, Any]] = None,
    max_items: int = 20,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    execution_root = Path(execution_journal_dir)
    validation = validation_report or build_temperature_execution_shadow_validation_report(
        journal_dir=execution_root,
        generated_at=generated_at,
    )
    next_action = str(validation.get("next_action") or "")
    quotes = _latest_execution_quotes_by_market(load_jsonl(execution_root / "maker_quotes.jsonl"))
    gated = next_action == "collect_formal_taker_paper"
    rows: List[Dict[str, Any]] = []
    if gated:
        for quote in quotes[: max(1, int(max_items))]:
            entry_ask = _safe_float(quote.get("entry_ask"))
            token_id = str(quote.get("token_id") or "").strip()
            if not token_id or entry_ask is None or entry_ask <= 0:
                continue
            rows.append(
                {
                    "decision": "quarantine",
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_gate_excluded": True,
                    "formal_taker_paper": True,
                    "execution_mode": "taker_cross",
                    "source_execution_quote_id": quote.get("quote_id"),
                    "source_execution_run_id": quote.get("run_id"),
                    "source_execution_validation_id": validation.get("validation_id"),
                    "source_execution_next_action": next_action,
                    "source_execution_hard_conclusion": validation.get("hard_conclusion"),
                    "market_family": "temperature",
                    "city": quote.get("city"),
                    "question": quote.get("question"),
                    "market_id": quote.get("market_id"),
                    "market_slug": quote.get("market_slug"),
                    "token_id": token_id,
                    "side": quote.get("side"),
                    "outcome": quote.get("outcome"),
                    "bucket_label": quote.get("bucket_label"),
                    "bucket_type": quote.get("bucket_type"),
                    "price": entry_ask,
                    "bid": _safe_float(quote.get("entry_bid")),
                    "ask": entry_ask,
                    "spread": _safe_float(quote.get("entry_spread")),
                    "liquidity": _safe_float(quote.get("entry_liquidity")),
                    "edge_percent": _safe_float(quote.get("edge_percent")),
                    "model_probability": _safe_float(quote.get("model_probability")),
                    "market_probability": _safe_float(quote.get("market_probability")),
                    "end_date": quote.get("end_date"),
                    "risk_rule_hits": [],
                    "blockers": ["formal_taker_paper_not_live_calibrated"],
                    "warnings": ["paper_only_execution_probe"],
                    "quarantine_reason": "formal_taker_execution_probe",
                    "quarantine_blocker_scope": "execution_probe",
                    "quarantine_non_risk_blockers": [],
                    "quarantine_non_risk_blocker_categories": [],
                    "would_be_decision_without_risk_rules": "candidate",
                    "would_be_decision_without_quarantine_blockers": "candidate",
                }
            )
    if not quotes:
        hard_conclusion = "temperature_taker_paper_no_execution_quotes"
    elif not gated:
        hard_conclusion = "temperature_taker_paper_wait_for_shadow_validation"
    elif rows:
        hard_conclusion = "temperature_taker_paper_collect_formal"
    else:
        hard_conclusion = "temperature_taker_paper_no_valid_taker_rows"
    identity = {
        "execution_journal_dir": str(execution_root),
        "validation_id": validation.get("validation_id"),
        "row_keys": [
            {
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "side": row.get("side"),
                "price": row.get("price"),
            }
            for row in rows
        ],
    }
    return {
        "schema_version": TEMPERATURE_TAKER_PAPER_SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": "temperature_execution_shadow_validation",
        "source_execution_journal_dir": str(execution_root),
        "source_validation": validation,
        "source_snapshot_id": validation.get("validation_id"),
        "run_seed_id": stable_json_hash(identity, length=20),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "live_authorization_pct": 0,
        "summary": {
            "candidate_count": 0,
            "watch_count": 0,
            "quarantine_count": len(rows),
            "formal_taker_paper_count": len(rows),
            "live_gate": False,
            "live_authorization_pct": 0,
            "live_blockers": [
                "formal_taker_paper_only",
                "taker_execution_forward_evidence_unproven",
                "live_permission_false",
            ],
        },
        "candidates": [],
        "watch": [],
        "quarantine": rows,
        "hard_conclusion": hard_conclusion,
    }


def _horizon_rows_by_label(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("markout_horizon") or "").strip()
        if label:
            result[label] = row
    return result


def build_temperature_taker_paper_validation_report(
    *,
    journal_dir: str | Path = DEFAULT_TEMPERATURE_TAKER_JOURNAL_DIR,
    min_marked_count: int = 10,
    min_mean_markout_cents: float = 0.0,
    min_win_rate: float = 0.55,
    required_horizons: Iterable[str] = ("0-5m", "5-15m", "15-30m"),
    min_horizon_count: int = 3,
    min_resolved_count: int = 1,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    journal_root = Path(journal_dir)
    journal_summary = summarize_paper_journal(journal_root)
    latest_strata = summarize_markout_strata(journal_root, latest_only=True, min_count=1)
    all_strata = summarize_markout_strata(journal_root, latest_only=False, min_count=1)
    resolved_audits = [
        row
        for row in load_jsonl(journal_root / "resolved_audits.jsonl")
        if isinstance(row, dict) and row.get("status") == "resolved"
    ]
    required = [str(value).strip() for value in required_horizons if str(value).strip()]
    horizon_rows = _horizon_rows_by_label(all_strata.get("by_horizon") or [])

    marked_count = int(latest_strata.get("marked_count") or 0)
    mean_markout = _safe_float(latest_strata.get("mean_markout_cents"))
    win_rate = _safe_float(latest_strata.get("win_rate"))
    resolved_count = len(resolved_audits)

    horizon_status: List[Dict[str, Any]] = []
    missing_horizons: List[str] = []
    weak_horizons: List[str] = []
    for horizon in required:
        row = horizon_rows.get(horizon)
        count = int((row or {}).get("count") or 0)
        horizon_mean = _safe_float((row or {}).get("mean_markout_cents"))
        horizon_win = _safe_float((row or {}).get("win_rate"))
        blockers: List[str] = []
        if count < max(1, int(min_horizon_count)):
            blockers.append(f"insufficient_horizon_count_{count}_of_{max(1, int(min_horizon_count))}")
            missing_horizons.append(horizon)
        if count > 0 and (horizon_mean is None or horizon_mean < float(min_mean_markout_cents)):
            blockers.append("horizon_mean_markout_below_threshold")
            weak_horizons.append(horizon)
        if count > 0 and (horizon_win is None or horizon_win < float(min_win_rate)):
            blockers.append("horizon_win_rate_below_threshold")
            weak_horizons.append(horizon)
        horizon_status.append(
            {
                "markout_horizon": horizon,
                "count": count,
                "win_rate": horizon_win,
                "mean_markout_cents": horizon_mean,
                "blockers": sorted(set(blockers)),
            }
        )

    blockers: List[str] = []
    if marked_count < max(1, int(min_marked_count)):
        blockers.append(f"insufficient_marked_count_{marked_count}_of_{max(1, int(min_marked_count))}")
    if mean_markout is None:
        blockers.append("mean_markout_missing")
    elif mean_markout < float(min_mean_markout_cents):
        blockers.append("mean_markout_below_threshold")
    if win_rate is None:
        blockers.append("win_rate_missing")
    elif win_rate < float(min_win_rate):
        blockers.append("win_rate_below_threshold")
    if missing_horizons:
        blockers.append("required_horizon_samples_missing")
    if weak_horizons:
        blockers.append("required_horizon_samples_weak")
    if resolved_count < max(0, int(min_resolved_count)):
        blockers.append(f"insufficient_resolved_count_{resolved_count}_of_{max(0, int(min_resolved_count))}")

    if marked_count == 0:
        hard_conclusion = "temperature_taker_validation_no_markouts"
        next_action = "collect_formal_taker_paper"
    elif marked_count < max(1, int(min_marked_count)):
        hard_conclusion = "temperature_taker_validation_collect_more_markouts"
        next_action = "continue_taker_paper_markouts"
    elif mean_markout is not None and mean_markout < float(min_mean_markout_cents):
        hard_conclusion = "temperature_taker_validation_negative"
        next_action = "keep_taker_blocked"
    elif win_rate is not None and win_rate < float(min_win_rate):
        hard_conclusion = "temperature_taker_validation_low_win_rate"
        next_action = "keep_taker_blocked"
    elif weak_horizons:
        hard_conclusion = "temperature_taker_validation_weak_horizons"
        next_action = "keep_taker_blocked"
    elif missing_horizons:
        hard_conclusion = "temperature_taker_validation_collect_required_horizons"
        next_action = "continue_taker_paper_markouts"
    elif resolved_count < max(0, int(min_resolved_count)):
        hard_conclusion = "temperature_taker_validation_wait_resolved_audit"
        next_action = "collect_resolved_audit"
    else:
        hard_conclusion = "temperature_taker_validation_promotable_to_tiny_live_review"
        next_action = "tiny_live_review_only"

    identity = {
        "journal_dir": str(journal_root),
        "marked_count": marked_count,
        "mean_markout": mean_markout,
        "win_rate": win_rate,
        "horizons": horizon_status,
        "resolved_count": resolved_count,
    }
    return {
        "schema_version": TEMPERATURE_TAKER_PAPER_VALIDATION_SCHEMA_VERSION,
        "generated_at": generated_at,
        "validation_id": stable_json_hash(identity, length=20),
        "journal_dir": str(journal_root),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "config": {
            "min_marked_count": max(1, int(min_marked_count)),
            "min_mean_markout_cents": float(min_mean_markout_cents),
            "min_win_rate": float(min_win_rate),
            "required_horizons": required,
            "min_horizon_count": max(1, int(min_horizon_count)),
            "min_resolved_count": max(0, int(min_resolved_count)),
        },
        "summary": {
            "paper_fill_count": journal_summary.get("paper_fill_count"),
            "markout_observation_count": latest_strata.get("markout_observation_count"),
            "selected_markout_count": latest_strata.get("selected_markout_count"),
            "marked_count": marked_count,
            "mean_markout_cents": mean_markout,
            "win_rate": win_rate,
            "resolved_count": resolved_count,
        },
        "horizon_status": horizon_status,
        "missing_horizons": sorted(set(missing_horizons)),
        "weak_horizons": sorted(set(weak_horizons)),
        "latest_strata_summary": {
            "by_horizon": latest_strata.get("by_horizon") or [],
            "by_entry_spread_bucket": latest_strata.get("by_entry_spread_bucket") or [],
            "do_not_live_rules": latest_strata.get("do_not_live_rules") or [],
        },
        "all_observation_horizon_summary": all_strata.get("by_horizon") or [],
        "blockers": blockers,
        "next_action": next_action,
        "hard_conclusion": hard_conclusion,
    }


def run_temperature_taker_paper_cycle(
    *,
    execution_journal_dir: str | Path = DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR,
    taker_journal_dir: str | Path = DEFAULT_TEMPERATURE_TAKER_JOURNAL_DIR,
    max_items: int = 20,
    max_fills: int = 20,
    markout_max_fills: Optional[int] = None,
    min_reentry_seconds: Optional[int] = None,
    min_markout_interval_seconds: float = 600.0,
    validation_min_marked_count: int = 10,
    validation_min_mean_markout_cents: float = 0.0,
    validation_min_win_rate: float = 0.55,
    validation_required_horizons: Iterable[str] = ("0-5m", "5-15m", "15-30m"),
    validation_min_horizon_count: int = 3,
    validation_min_resolved_count: int = 1,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    validation = build_temperature_execution_shadow_validation_report(
        journal_dir=execution_journal_dir,
        generated_at=generated_at,
    )
    signal_report = build_temperature_taker_paper_signal_report(
        execution_journal_dir=execution_journal_dir,
        validation_report=validation,
        max_items=max_items,
        generated_at=generated_at,
    )
    journal = write_paper_journal(
        signal_report,
        journal_dir=taker_journal_dir,
        profile="temperature-taker-paper",
        include_candidates=False,
        include_watch=False,
        include_quarantine=True,
        max_fills=max_fills,
        recorded_at=generated_at,
        min_reentry_seconds=min_reentry_seconds,
    )
    markout = markout_open_paper_fills(
        journal_dir=taker_journal_dir,
        max_fills=markout_max_fills,
        min_markout_interval_seconds=float(min_markout_interval_seconds),
        recorded_at=generated_at,
    )
    taker_validation = build_temperature_taker_paper_validation_report(
        journal_dir=taker_journal_dir,
        min_marked_count=validation_min_marked_count,
        min_mean_markout_cents=validation_min_mean_markout_cents,
        min_win_rate=validation_min_win_rate,
        required_horizons=validation_required_horizons,
        min_horizon_count=validation_min_horizon_count,
        min_resolved_count=validation_min_resolved_count,
        generated_at=generated_at,
    )
    return {
        "schema_version": TEMPERATURE_TAKER_PAPER_SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "execution_journal_dir": str(Path(execution_journal_dir)),
        "taker_journal_dir": str(Path(taker_journal_dir)),
        "validation": validation,
        "taker_validation": taker_validation,
        "signal_summary": signal_report.get("summary"),
        "hard_conclusion": signal_report.get("hard_conclusion"),
        "journal": journal,
        "markout": {
            key: value
            for key, value in markout.items()
            if key != "records"
        },
        "signal_report": signal_report,
    }


def build_temperature_execution_experiment_report(
    temperature_opportunity_report: Dict[str, Any],
    *,
    offset_cents: Optional[Iterable[float]] = None,
    min_expected_markout_cents: float = 0.0,
    min_win_rate: float = 0.55,
    max_items: int = 20,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build paper-only execution diagnostics for high-edge temperature rows blocked by maker evidence."""

    generated = generated_at or utc_now_iso()
    offsets = _normalized_offsets(offset_cents)
    source_rows = [
        row
        for row in temperature_opportunity_report.get("blocked_by_negative_maker_evidence") or []
        if isinstance(row, dict)
    ][: max(1, int(max_items))]
    experiments = [
        _experiment_for_row(
            row,
            offset_cents=offsets,
            min_expected_markout_cents=min_expected_markout_cents,
            min_win_rate=min_win_rate,
        )
        for row in source_rows
    ]
    action_counts: Dict[str, int] = {}
    for row in experiments:
        action = str(row.get("next_action") or "unknown")
        action_counts[action] = action_counts.get(action, 0) + 1

    collect_taker_count = action_counts.get("collect_formal_taker_paper", 0)
    collect_ladder_count = action_counts.get("collect_offset_ladder_shadow_quotes", 0)
    skip_count = action_counts.get("skip_until_surface_changes_or_settlement", 0)
    if collect_taker_count > 0:
        hard_conclusion = "temperature_execution_experiment_collect_taker_paper"
    elif collect_ladder_count > 0:
        hard_conclusion = "temperature_execution_experiment_collect_offset_ladder"
    elif skip_count > 0:
        hard_conclusion = "temperature_execution_experiment_skip_negative_maker_surface"
    else:
        hard_conclusion = "temperature_execution_experiment_no_blocked_temperature_opportunities"

    identity = {
        "source_opportunity_id": temperature_opportunity_report.get("opportunity_id"),
        "offset_cents": offsets,
        "actions": action_counts,
    }
    return {
        "schema_version": TEMPERATURE_EXECUTION_EXPERIMENT_SCHEMA_VERSION,
        "generated_at": generated,
        "experiment_id": stable_json_hash(identity, length=20),
        "source_opportunity_id": temperature_opportunity_report.get("opportunity_id"),
        "source_snapshot_id": temperature_opportunity_report.get("source_snapshot_id"),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "config": {
            "offset_cents": offsets,
            "min_expected_markout_cents": float(min_expected_markout_cents),
            "min_win_rate": float(min_win_rate),
            "max_items": max(1, int(max_items)),
        },
        "blocked_negative_maker_source_count": len(source_rows),
        "experiment_count": len(experiments),
        "action_counts": [
            {"action": action, "count": count}
            for action, count in sorted(action_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "collect_formal_taker_paper_count": collect_taker_count,
        "collect_offset_ladder_shadow_quote_count": collect_ladder_count,
        "skip_current_surface_count": skip_count,
        "experiments": experiments,
        "hard_conclusion": hard_conclusion,
    }
