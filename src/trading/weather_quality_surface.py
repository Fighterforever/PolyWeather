from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR
from src.trading.weather_paper_journal import (
    DEFAULT_PAPER_JOURNAL_DIR,
    _safe_float,
    load_jsonl,
    price_bucket,
    spread_bucket,
    stable_json_hash,
    utc_now_iso,
)
from src.trading.weather_maker_quote_journal import summarize_maker_quote_journal, summarize_maker_quote_strata
from src.trading.weather_paper_journal import summarize_markout_strata
from src.trading.weather_quarantine_validation import DEFAULT_QUARANTINE_JOURNAL_DIR
from src.trading.weather_resolved_audit import summarize_resolved_audits


QUALITY_SURFACE_SCHEMA_VERSION = "polyweather_weather_quality_surface.v1"
DEFAULT_PRICE_CONDITIONED_JOURNAL_DIR = Path("data/trading/weather_price_conditioned_paper")


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _mean(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def bucket_type_from_label(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith(">="):
        return "ge"
    if text.startswith("<="):
        return "le"
    if text.startswith("="):
        return "eq"
    if "-" in text:
        return "range"
    return "unknown"


def _latest_by_id(records: Iterable[Dict[str, Any]], key_field: str) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        key = str(record.get(key_field) or f"row:{index}")
        latest[key] = record
    return latest


def _snapshot_item_key(
    *,
    run_id: Any,
    bucket: Any,
    row_id: Any,
    market_slug: Any,
    token_id: Any,
    side: Any,
) -> Tuple[str, str, str, str, str, str]:
    return (
        str(run_id or ""),
        str(bucket or ""),
        str(row_id or ""),
        str(market_slug or ""),
        str(token_id or ""),
        str(side or "").lower(),
    )


def _load_snapshot_item_index(journal_dir: str | Path) -> Dict[Tuple[str, str, str, str, str, str], Dict[str, Any]]:
    root = Path(journal_dir)
    index: Dict[Tuple[str, str, str, str, str, str], Dict[str, Any]] = {}
    for snapshot_path in sorted((root / "snapshots").glob("*.json")):
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(snapshot, dict):
            continue
        run_id = snapshot.get("run_id")
        report = snapshot.get("report") if isinstance(snapshot.get("report"), dict) else {}
        for bucket in ("candidates", "watch", "quarantine"):
            for item in report.get(bucket) or []:
                if not isinstance(item, dict):
                    continue
                index[
                    _snapshot_item_key(
                        run_id=run_id,
                        bucket=bucket,
                        row_id=item.get("row_id"),
                        market_slug=item.get("market_slug"),
                        token_id=item.get("token_id"),
                        side=item.get("side"),
                    )
                ] = item
    return index


def _snapshot_item_for_fill(
    fill: Dict[str, Any],
    index: Dict[Tuple[str, str, str, str, str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    return index.get(
        _snapshot_item_key(
            run_id=fill.get("run_id"),
            bucket=fill.get("signal_bucket"),
            row_id=fill.get("row_id"),
            market_slug=fill.get("market_slug"),
            token_id=fill.get("token_id"),
            side=fill.get("side"),
        ),
        {},
    )


def _entry_field(fill: Dict[str, Any], snapshot_item: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if fill.get(name) is not None:
            return fill.get(name)
    snapshot_names = {
        "entry_bid_depth_usdc_3c": "bid_depth_usdc_3c",
        "entry_ask_depth_usdc_3c": "ask_depth_usdc_3c",
        "entry_price": "price",
        "entry_spread": "spread",
        "entry_liquidity": "liquidity",
    }
    for name in names:
        snapshot_name = snapshot_names.get(name, name)
        if snapshot_item.get(snapshot_name) is not None:
            return snapshot_item.get(snapshot_name)
    return None


def quality_surface_blockers(
    record: Dict[str, Any],
    *,
    allowed_sides: Iterable[str] = (),
    excluded_bucket_types: Iterable[str] = ("eq",),
    min_price: float = 0.03,
    max_price: float = 0.85,
    max_spread: float = 0.02,
    min_liquidity: float = 10.0,
    min_bid_depth_usdc_3c: float = 10.0,
    min_ask_depth_usdc_3c: float = 10.0,
) -> List[str]:
    blockers: List[str] = []
    side = str(record.get("side") or "").strip().lower()
    allowed = {str(value).strip().lower() for value in allowed_sides if str(value).strip()}
    if allowed and side not in allowed:
        blockers.append("side_not_allowed")
    bucket_type = str(record.get("bucket_type") or bucket_type_from_label(record.get("bucket_label"))).strip().lower()
    if bucket_type in {str(value).strip().lower() for value in excluded_bucket_types if str(value).strip()}:
        blockers.append(f"bucket_type_excluded:{bucket_type}")
    entry_price = _safe_float(record.get("entry_price"))
    if entry_price is None:
        blockers.append("missing_entry_price")
    elif entry_price < min_price:
        blockers.append("price_below_min")
    elif entry_price > max_price:
        blockers.append("price_above_max")
    entry_spread = _safe_float(record.get("entry_spread"))
    if entry_spread is None:
        blockers.append("missing_entry_spread")
    elif entry_spread > max_spread:
        blockers.append("spread_above_max")
    liquidity = _safe_float(record.get("entry_liquidity"))
    if liquidity is None:
        blockers.append("missing_liquidity")
    elif liquidity < min_liquidity:
        blockers.append("liquidity_below_min")
    bid_depth = _safe_float(record.get("entry_bid_depth_usdc_3c"))
    if bid_depth is None:
        blockers.append("missing_bid_depth")
    elif bid_depth < min_bid_depth_usdc_3c:
        blockers.append("bid_depth_below_min")
    ask_depth = _safe_float(record.get("entry_ask_depth_usdc_3c"))
    if ask_depth is None:
        blockers.append("missing_ask_depth")
    elif ask_depth < min_ask_depth_usdc_3c:
        blockers.append("ask_depth_below_min")
    return blockers


def _journal_quality_records(journal_dir: str | Path, *, journal_name: str) -> List[Dict[str, Any]]:
    root = Path(journal_dir)
    fills = load_jsonl(root / "paper_fills.jsonl")
    markouts_by_fill_id = _latest_by_id(load_jsonl(root / "markouts.jsonl"), "fill_id")
    snapshot_index = _load_snapshot_item_index(root)
    records: List[Dict[str, Any]] = []
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        snapshot_item = _snapshot_item_for_fill(fill, snapshot_index)
        markout = markouts_by_fill_id.get(str(fill.get("fill_id") or "")) or {}
        bucket_label = fill.get("bucket_label") or snapshot_item.get("bucket_label")
        bucket_type = (
            fill.get("bucket_type")
            or markout.get("bucket_type")
            or snapshot_item.get("bucket_type")
            or bucket_type_from_label(bucket_label)
        )
        record = {
            "journal": journal_name,
            "fill_id": fill.get("fill_id"),
            "signal_bucket": fill.get("signal_bucket"),
            "profile": fill.get("profile"),
            "city": fill.get("city") or snapshot_item.get("city"),
            "market_slug": fill.get("market_slug"),
            "question": fill.get("question") or snapshot_item.get("question"),
            "side": str(fill.get("side") or snapshot_item.get("side") or "").lower(),
            "bucket_label": bucket_label,
            "bucket_type": str(bucket_type or "unknown").lower(),
            "entry_price": _safe_float(_entry_field(fill, snapshot_item, "entry_price")),
            "entry_bid": _safe_float(_entry_field(fill, snapshot_item, "entry_bid", "bid")),
            "entry_ask": _safe_float(_entry_field(fill, snapshot_item, "entry_ask", "ask")),
            "entry_spread": _safe_float(_entry_field(fill, snapshot_item, "entry_spread")),
            "entry_liquidity": _safe_float(_entry_field(fill, snapshot_item, "entry_liquidity")),
            "entry_bid_depth_usdc_3c": _safe_float(
                _entry_field(fill, snapshot_item, "entry_bid_depth_usdc_3c")
            ),
            "entry_ask_depth_usdc_3c": _safe_float(
                _entry_field(fill, snapshot_item, "entry_ask_depth_usdc_3c")
            ),
            "edge_percent": _safe_float(fill.get("edge_percent") or snapshot_item.get("edge_percent")),
            "model_probability": _safe_float(
                fill.get("model_probability") or snapshot_item.get("model_probability")
            ),
            "markout_status": markout.get("status"),
            "markout_cents": _safe_float(markout.get("markout_cents")),
            "markout_horizon": markout.get("markout_horizon"),
            "risk_rule_hits": fill.get("risk_rule_hits") or [],
            "quarantine_reason": fill.get("quarantine_reason"),
            "quarantine_blocker_scope": fill.get("quarantine_blocker_scope"),
            "quarantine_non_risk_blockers": fill.get("quarantine_non_risk_blockers") or [],
            "quarantine_non_risk_blocker_categories": fill.get("quarantine_non_risk_blocker_categories") or [],
            "would_be_decision_without_risk_rules": fill.get("would_be_decision_without_risk_rules"),
        }
        records.append(record)
    return records


def _summarize_markout_group(records: Iterable[Dict[str, Any]], fields: Tuple[str, ...]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    for record in records:
        if record.get("markout_status") != "marked":
            continue
        markout = _safe_float(record.get("markout_cents"))
        if markout is None:
            continue
        key = tuple(str(record.get(field) or "unknown") for field in fields)
        groups.setdefault(key, []).append(record)
    rows: List[Dict[str, Any]] = []
    for key, group in groups.items():
        values = [float(record["markout_cents"]) for record in group]
        wins = [value for value in values if value > 0]
        row = {field: key[index] for index, field in enumerate(fields)}
        row.update(
            {
                "count": len(values),
                "win_count": len(wins),
                "win_rate": _ratio(len(wins), len(values)),
                "mean_markout_cents": _mean(values),
                "min_markout_cents": round(min(values), 6),
                "max_markout_cents": round(max(values), 6),
            }
        )
        rows.append(row)
    return sorted(rows, key=lambda row: (-int(row.get("count") or 0), float(row.get("mean_markout_cents") or 0.0)))


def _summarize_quality_records(records: List[Dict[str, Any]], **quality_kwargs: Any) -> Dict[str, Any]:
    assessed: List[Dict[str, Any]] = []
    for record in records:
        blockers = quality_surface_blockers(record, **quality_kwargs)
        assessed.append({**record, "quality_surface_blockers": blockers, "quality_surface_pass": not blockers})
    passed = [record for record in assessed if record.get("quality_surface_pass")]
    marked = [
        record
        for record in passed
        if record.get("markout_status") == "marked" and _safe_float(record.get("markout_cents")) is not None
    ]
    values = [float(record["markout_cents"]) for record in marked]
    blocker_counts: Dict[str, int] = {}
    for record in assessed:
        for blocker in record.get("quality_surface_blockers") or []:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
    return {
        "record_count": len(assessed),
        "quality_surface_count": len(passed),
        "quality_marked_count": len(marked),
        "quality_mean_markout_cents": _mean(values),
        "quality_win_rate": _ratio(len([value for value in values if value > 0]), len(values)),
        "quality_blocker_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(blocker_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "by_journal": _summarize_markout_group(passed, ("journal",)),
        "by_side": _summarize_markout_group(passed, ("side",)),
        "by_bucket_type": _summarize_markout_group(passed, ("bucket_type",)),
        "by_side_and_bucket_type": _summarize_markout_group(passed, ("side", "bucket_type")),
        "by_price_and_spread": _summarize_markout_group(
            [
                {
                    **record,
                    "entry_price_bucket": price_bucket(record.get("entry_price")),
                    "entry_spread_bucket": spread_bucket(record.get("entry_spread")),
                }
                for record in passed
            ],
            ("entry_price_bucket", "entry_spread_bucket"),
        ),
        "examples": sorted(
            passed,
            key=lambda row: float(row.get("edge_percent") or 0.0),
            reverse=True,
        )[:10],
    }


def _closed_base_rate_rows(backfill_dir: str | Path, *, excluded_bucket_types: Iterable[str]) -> List[Dict[str, Any]]:
    excluded = {str(value).strip().lower() for value in excluded_bucket_types if str(value).strip()}
    rows: List[Dict[str, Any]] = []
    for record in load_jsonl(Path(backfill_dir) / "closed_markets.jsonl"):
        if not isinstance(record, dict) or record.get("status") != "resolved":
            continue
        spec = record.get("parsed_temperature_spec")
        if not isinstance(spec, dict):
            continue
        bucket_type = str(spec.get("comparator") or bucket_type_from_label(record.get("bucket_label"))).lower()
        if bucket_type in excluded:
            continue
        for side in ("yes", "no"):
            rows.append(
                {
                    "side": side,
                    "bucket_type": bucket_type,
                    "city": record.get("city"),
                    "won": str(record.get("winning_side") or "").lower() == side,
                }
            )
    return rows


def _summarize_closed_base_rates(rows: List[Dict[str, Any]], fields: Tuple[str, ...]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "unknown") for field in fields)
        groups.setdefault(key, []).append(row)
    output: List[Dict[str, Any]] = []
    for key, group in groups.items():
        wins = [row for row in group if row.get("won") is True]
        item = {field: key[index] for index, field in enumerate(fields)}
        item.update({"count": len(group), "win_count": len(wins), "win_rate": _ratio(len(wins), len(group))})
        output.append(item)
    return sorted(output, key=lambda row: (-int(row.get("count") or 0), -float(row.get("win_rate") or 0.0)))


def _closed_base_rate_index(rows: List[Dict[str, Any]], *, prior_count: int = 20) -> Dict[Tuple[str, str], Dict[str, Any]]:
    indexed: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in _summarize_closed_base_rates(rows, ("side", "bucket_type")):
        count = int(row.get("count") or 0)
        wins = int(row.get("win_count") or 0)
        shrunk = (wins + max(0, int(prior_count)) * 0.5) / (count + max(0, int(prior_count))) if count + max(0, int(prior_count)) > 0 else None
        indexed[(str(row.get("side") or ""), str(row.get("bucket_type") or ""))] = {
            **row,
            "prior_count": max(0, int(prior_count)),
            "shrunk_win_rate": round(shrunk, 6) if shrunk is not None else None,
        }
    return indexed


def _assessment_quality_record(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "row_id": item.get("row_id"),
        "city": item.get("city"),
        "event_title": item.get("event_title"),
        "market_id": item.get("market_id"),
        "market_slug": item.get("market_slug"),
        "token_id": item.get("token_id"),
        "question": item.get("question"),
        "side": str(item.get("side") or "").lower(),
        "outcome": item.get("outcome"),
        "bucket_label": item.get("bucket_label"),
        "bucket_type": str(item.get("bucket_type") or bucket_type_from_label(item.get("bucket_label"))).lower(),
        "entry_price": _safe_float(item.get("price")),
        "entry_bid": _safe_float(item.get("bid")),
        "entry_ask": _safe_float(item.get("ask")),
        "entry_spread": _safe_float(item.get("spread")),
        "entry_liquidity": _safe_float(item.get("liquidity")),
        "entry_bid_depth_usdc_3c": _safe_float(item.get("bid_depth_usdc_3c")),
        "entry_ask_depth_usdc_3c": _safe_float(item.get("ask_depth_usdc_3c")),
        "edge_percent": _safe_float(item.get("edge_percent")),
        "model_probability": _safe_float(item.get("model_probability")),
        "market_probability": _safe_float(item.get("market_probability")),
        "score": _safe_float(item.get("score")),
        "source_final_score": _safe_float(item.get("source_final_score")),
        "end_date": item.get("end_date"),
        "decision": item.get("decision"),
        "warnings": item.get("warnings") or [],
        "blockers": item.get("blockers") or [],
        "risk_rule_hits": item.get("risk_rule_hits") or [],
    }


def _signal_assessments(signal_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(signal_report.get("assessments"), list):
        return [row for row in signal_report.get("assessments") or [] if isinstance(row, dict)]
    rows: List[Dict[str, Any]] = []
    seen = set()
    for section in ("candidates", "watch", "quarantine"):
        for item in signal_report.get(section) or []:
            if not isinstance(item, dict):
                continue
            key = (item.get("row_id"), item.get("market_slug"), item.get("side"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
    for item in ((signal_report.get("candidate_gap_report") or {}).get("near_candidates") or []):
        if not isinstance(item, dict):
            continue
        key = (item.get("row_id"), item.get("market_slug"), item.get("side"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def build_price_conditioned_quality_search(
    signal_report: Dict[str, Any],
    *,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    allowed_sides: Iterable[str] = (),
    excluded_bucket_types: Iterable[str] = ("eq",),
    min_price: float = 0.03,
    max_price: float = 0.85,
    max_spread: float = 0.02,
    min_liquidity: float = 10.0,
    min_bid_depth_usdc_3c: float = 10.0,
    min_ask_depth_usdc_3c: float = 10.0,
    base_rate_prior_count: int = 20,
    base_rate_haircut: float = 0.20,
    min_base_rate_count: int = 10,
    min_conservative_base_rate: float = 0.55,
    min_discount_cents: float = 1.0,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    quality_kwargs = {
        "allowed_sides": tuple(allowed_sides),
        "excluded_bucket_types": tuple(excluded_bucket_types),
        "min_price": float(min_price),
        "max_price": float(max_price),
        "max_spread": float(max_spread),
        "min_liquidity": float(min_liquidity),
        "min_bid_depth_usdc_3c": float(min_bid_depth_usdc_3c),
        "min_ask_depth_usdc_3c": float(min_ask_depth_usdc_3c),
    }
    closed_rows = _closed_base_rate_rows(backfill_dir, excluded_bucket_types=excluded_bucket_types)
    base_index = _closed_base_rate_index(closed_rows, prior_count=base_rate_prior_count)
    opportunities: List[Dict[str, Any]] = []
    seen_opportunities: set[Tuple[str, str, str]] = set()
    quality_rows = 0
    for item in _signal_assessments(signal_report):
        record = _assessment_quality_record(item)
        quality_blockers = quality_surface_blockers(record, **quality_kwargs)
        if quality_blockers:
            continue
        quality_rows += 1
        key = (str(record.get("side") or ""), str(record.get("bucket_type") or ""))
        base_rate = base_index.get(key)
        entry_price = _safe_float(record.get("entry_price"))
        shrunk = _safe_float((base_rate or {}).get("shrunk_win_rate"))
        conservative = (
            max(0.0, min(1.0, shrunk - float(base_rate_haircut)))
            if shrunk is not None
            else None
        )
        discount_cents = (
            round((conservative - entry_price) * 100.0, 6)
            if conservative is not None and entry_price is not None
            else None
        )
        risk_hits = [str(reason) for reason in record.get("risk_rule_hits") or [] if str(reason)]
        risk_hit_set = set(risk_hits)
        non_risk_blockers = [
            str(reason)
            for reason in record.get("blockers") or []
            if str(reason) not in risk_hit_set
        ]
        price_conditioned_pass = bool(
            base_rate
            and int(base_rate.get("count") or 0) >= max(1, int(min_base_rate_count))
            and conservative is not None
            and conservative >= float(min_conservative_base_rate)
            and discount_cents is not None
            and discount_cents >= float(min_discount_cents)
            and not non_risk_blockers
        )
        if not price_conditioned_pass:
            continue
        opportunity_key = (
            str(record.get("market_slug") or record.get("market_id") or ""),
            str(record.get("side") or ""),
            str(record.get("entry_price") or ""),
        )
        if opportunity_key in seen_opportunities:
            continue
        seen_opportunities.add(opportunity_key)
        status = "strict_candidate"
        if risk_hits:
            status = "risk_only_quarantine"
        elif record.get("warnings"):
            status = "watch"
        opportunities.append(
            {
                **record,
                "price_conditioned_status": status,
                "quality_surface_blockers": quality_blockers,
                "non_risk_blockers": non_risk_blockers,
                "base_rate_reference": base_rate,
                "base_rate_note": "Closed base-rate is a resolved-side reference only; it is not historical PnL because closed records lack entry price, spread, and depth.",
                "base_rate_haircut": float(base_rate_haircut),
                "conservative_base_rate_fair_price": round(conservative, 6) if conservative is not None else None,
                "price_discount_cents": discount_cents,
                "counts_for_live_gate": False,
                "paper_only": True,
            }
        )
    opportunities = sorted(
        opportunities,
        key=lambda row: (
            0 if row.get("price_conditioned_status") == "strict_candidate" else 1,
            -float(row.get("price_discount_cents") or 0.0),
            -float(row.get("edge_percent") or 0.0),
        ),
    )
    return {
        "schema_version": QUALITY_SURFACE_SCHEMA_VERSION,
        "report_type": "price_conditioned_quality_search",
        "generated_at": generated_at,
        "source_signal_snapshot_id": signal_report.get("source_snapshot_id"),
        "paper_only": True,
        "counts_for_live_gate": False,
        "quality_config": {
            **{
                key: list(value) if isinstance(value, tuple) else value
                for key, value in quality_kwargs.items()
            },
            "base_rate_prior_count": max(0, int(base_rate_prior_count)),
            "base_rate_haircut": float(base_rate_haircut),
            "min_base_rate_count": max(1, int(min_base_rate_count)),
            "min_conservative_base_rate": float(min_conservative_base_rate),
            "min_discount_cents": float(min_discount_cents),
        },
        "closed_base_rate_row_count": len(closed_rows),
        "assessed_row_count": len(_signal_assessments(signal_report)),
        "quality_surface_row_count": quality_rows,
        "opportunity_count": len(opportunities),
        "strict_candidate_count": len(
            [row for row in opportunities if row.get("price_conditioned_status") == "strict_candidate"]
        ),
        "risk_only_quarantine_count": len(
            [row for row in opportunities if row.get("price_conditioned_status") == "risk_only_quarantine"]
        ),
        "opportunities": opportunities[:50],
        "hard_conclusion": (
            "price_conditioned_strict_candidate_found"
            if any(row.get("price_conditioned_status") == "strict_candidate" for row in opportunities)
            else (
                "price_conditioned_risk_only_quarantine_found"
                if opportunities
                else "no_price_conditioned_quality_opportunity"
            )
        ),
    }


def _price_conditioned_paper_item(opportunity: Dict[str, Any]) -> Dict[str, Any]:
    status = str(opportunity.get("price_conditioned_status") or "").strip().lower()
    warnings = [str(reason) for reason in opportunity.get("warnings") or [] if str(reason)]
    risk_hits = [str(reason) for reason in opportunity.get("risk_rule_hits") or [] if str(reason)]
    non_risk_blockers = [
        str(reason)
        for reason in opportunity.get("non_risk_blockers") or []
        if str(reason)
    ]
    blockers = list(dict.fromkeys([*non_risk_blockers, *risk_hits]))
    would_be_without_risk = "watch" if warnings else "candidate"
    if status == "strict_candidate":
        bucket = "candidates"
        decision = "candidate"
        quarantine_reason = None
    elif status == "watch":
        bucket = "watch"
        decision = "watch"
        quarantine_reason = None
    else:
        bucket = "quarantine"
        decision = "quarantine"
        quarantine_reason = "price_conditioned_risk_only"

    return {
        "decision": decision,
        "original_decision": opportunity.get("decision"),
        "paper_signal_bucket": bucket,
        "row_id": opportunity.get("row_id"),
        "city": opportunity.get("city"),
        "event_title": opportunity.get("event_title"),
        "question": opportunity.get("question"),
        "market_id": opportunity.get("market_id"),
        "market_slug": opportunity.get("market_slug"),
        "token_id": opportunity.get("token_id"),
        "side": opportunity.get("side"),
        "outcome": opportunity.get("outcome"),
        "bucket_label": opportunity.get("bucket_label"),
        "bucket_type": opportunity.get("bucket_type"),
        "price": _safe_float(opportunity.get("entry_price")),
        "bid": _safe_float(opportunity.get("entry_bid")),
        "ask": _safe_float(opportunity.get("entry_ask")),
        "spread": _safe_float(opportunity.get("entry_spread")),
        "liquidity": _safe_float(opportunity.get("entry_liquidity")),
        "bid_depth_usdc_3c": _safe_float(opportunity.get("entry_bid_depth_usdc_3c")),
        "ask_depth_usdc_3c": _safe_float(opportunity.get("entry_ask_depth_usdc_3c")),
        "edge_percent": _safe_float(opportunity.get("edge_percent")),
        "model_probability": _safe_float(opportunity.get("model_probability")),
        "market_probability": _safe_float(opportunity.get("market_probability")),
        "score": _safe_float(opportunity.get("score")),
        "source_final_score": _safe_float(opportunity.get("source_final_score")),
        "end_date": opportunity.get("end_date"),
        "blockers": blockers,
        "warnings": warnings,
        "risk_rule_hits": risk_hits,
        "quarantine_reason": quarantine_reason,
        "would_be_decision_without_risk_rules": would_be_without_risk,
        "live_gate_excluded": True,
        "counts_for_live_gate": False,
        "paper_only": True,
        "price_conditioned_status": status,
        "base_rate_reference": opportunity.get("base_rate_reference"),
        "base_rate_note": opportunity.get("base_rate_note"),
        "base_rate_haircut": _safe_float(opportunity.get("base_rate_haircut")),
        "conservative_base_rate_fair_price": _safe_float(
            opportunity.get("conservative_base_rate_fair_price")
        ),
        "price_discount_cents": _safe_float(opportunity.get("price_discount_cents")),
    }


def build_price_conditioned_paper_report(
    price_search_report: Dict[str, Any],
    *,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    items = [
        _price_conditioned_paper_item(row)
        for row in price_search_report.get("opportunities") or []
        if isinstance(row, dict)
    ]
    candidates = [item for item in items if item.get("paper_signal_bucket") == "candidates"]
    watch = [item for item in items if item.get("paper_signal_bucket") == "watch"]
    quarantine = [item for item in items if item.get("paper_signal_bucket") == "quarantine"]
    source_snapshot_id = stable_json_hash(
        {
            "report_type": price_search_report.get("report_type"),
            "source_signal_snapshot_id": price_search_report.get("source_signal_snapshot_id"),
            "price_search_generated_at": price_search_report.get("generated_at"),
            "items": [
                {
                    "row_id": item.get("row_id"),
                    "market_slug": item.get("market_slug"),
                    "token_id": item.get("token_id"),
                    "side": item.get("side"),
                    "price": item.get("price"),
                    "bucket": item.get("paper_signal_bucket"),
                }
                for item in items
            ],
        },
        length=20,
    )
    return {
        "schema_version": "polyweather_weather_market_signal_report.v1",
        "report_type": "price_conditioned_paper_signal_report",
        "generated_at": generated_at,
        "source_snapshot_id": f"price-conditioned:{source_snapshot_id}",
        "source_signal_snapshot_id": price_search_report.get("source_signal_snapshot_id"),
        "source_status": price_search_report.get("hard_conclusion"),
        "source": "price_conditioned_quality_search",
        "paper_only": True,
        "counts_for_live_gate": False,
        "quality_config": price_search_report.get("quality_config") or {},
        "summary": {
            "candidate_count": len(candidates),
            "watch_count": len(watch),
            "quarantine_count": len(quarantine),
            "reject_count": 0,
            "live_gate": False,
            "live_authorization_pct": 0,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_blockers": [
                "price_conditioned_paper_only",
                "risk_rule_blocked" if quarantine else "paper_evidence_missing",
                "live_permission_false",
            ],
        },
        "candidate_gap_report": {
            "source": "price_conditioned_quality_search",
            "opportunity_count": price_search_report.get("opportunity_count"),
            "strict_candidate_count": price_search_report.get("strict_candidate_count"),
            "risk_only_quarantine_count": price_search_report.get("risk_only_quarantine_count"),
            "hard_conclusion": price_search_report.get("hard_conclusion"),
        },
        "candidates": candidates,
        "watch": watch,
        "quarantine": quarantine,
        "assessments": items,
        "live_gate": False,
        "live_authorization_pct": 0,
    }


def _price_conditioned_taker_status(
    *,
    marked_count: int,
    mean_markout_cents: Optional[float],
    win_rate: Optional[float],
    min_marked_count: int,
    min_mean_markout_cents: float,
    min_win_rate: float,
) -> str:
    if marked_count <= 0:
        return "no_taker_markout_evidence"
    if marked_count < max(1, int(min_marked_count)):
        return "collect_more_taker_markout"
    if mean_markout_cents is None or win_rate is None:
        return "collect_more_taker_markout"
    if mean_markout_cents >= float(min_mean_markout_cents) and win_rate >= float(min_win_rate):
        return "taker_positive_enough_for_formal_paper_review"
    return "taker_negative_or_unreliable"


def _price_conditioned_maker_status(
    *,
    quote_markout_count: int,
    inferred_fill_count: int,
    mean_maker_markout_cents: Optional[float],
    maker_markout_win_rate: Optional[float],
    mean_missed_taker_markout_cents: Optional[float],
    min_quote_markout_count: int,
    min_inferred_fills: int,
    min_mean_maker_markout_cents: float,
    min_maker_win_rate: float,
) -> str:
    if quote_markout_count <= 0:
        return "no_maker_quote_evidence"
    if quote_markout_count < max(1, int(min_quote_markout_count)):
        return "collect_more_maker_quotes"
    if inferred_fill_count < max(1, int(min_inferred_fills)):
        if mean_missed_taker_markout_cents is not None and mean_missed_taker_markout_cents > 0.0:
            return "maker_no_fill_missed_positive_taker"
        if mean_missed_taker_markout_cents is not None and mean_missed_taker_markout_cents <= 0.0:
            return "maker_no_fill_protected_from_negative_taker"
        return "maker_no_fill_unknown"
    if mean_maker_markout_cents is None or maker_markout_win_rate is None:
        return "collect_more_maker_quotes"
    if (
        mean_maker_markout_cents >= float(min_mean_maker_markout_cents)
        and maker_markout_win_rate >= float(min_maker_win_rate)
    ):
        return "maker_positive_enough_for_formal_paper_review"
    return "maker_adverse_selection"


def _price_conditioned_resolved_status(
    *,
    resolved_count: int,
    total_pnl_cents: Optional[float],
    win_rate: Optional[float],
    min_resolved_count: int,
    min_resolved_total_pnl_cents: float,
    min_resolved_win_rate: float,
) -> str:
    if min_resolved_count <= 0:
        return "resolved_not_required_for_this_review"
    if resolved_count < int(min_resolved_count):
        return "collect_more_resolved_outcomes"
    if total_pnl_cents is None or win_rate is None:
        return "collect_more_resolved_outcomes"
    if total_pnl_cents >= float(min_resolved_total_pnl_cents) and win_rate >= float(min_resolved_win_rate):
        return "resolved_positive"
    return "resolved_negative_or_unreliable"


def _price_conditioned_hard_conclusion(
    *,
    taker_status: str,
    maker_status: str,
    resolved_status: str,
) -> str:
    if taker_status == "taker_positive_enough_for_formal_paper_review":
        if resolved_status == "resolved_negative_or_unreliable":
            return "price_conditioned_taker_positive_but_resolved_blocked"
        if maker_status == "maker_positive_enough_for_formal_paper_review":
            return "price_conditioned_taker_and_maker_ready_for_formal_paper_review"
        if maker_status == "maker_no_fill_missed_positive_taker":
            return "price_conditioned_taker_positive_maker_missed_upside"
        if maker_status == "maker_adverse_selection":
            return "price_conditioned_taker_positive_maker_adverse"
        return "price_conditioned_taker_candidate_collect_more_execution"
    if taker_status in {"no_taker_markout_evidence", "collect_more_taker_markout"}:
        return "collect_more_price_conditioned_evidence"
    return "keep_price_conditioned_quarantine"


def _price_conditioned_recommended_action(hard_conclusion: str) -> str:
    if hard_conclusion == "price_conditioned_taker_and_maker_ready_for_formal_paper_review":
        return "review_formal_paper_with_maker_execution"
    if hard_conclusion == "price_conditioned_taker_positive_maker_missed_upside":
        return "review_taker_only_formal_paper_maker_would_miss"
    if hard_conclusion == "price_conditioned_taker_positive_maker_adverse":
        return "review_taker_only_formal_paper_keep_maker_blocked"
    if hard_conclusion == "price_conditioned_taker_candidate_collect_more_execution":
        return "collect_more_price_conditioned_execution_evidence"
    if hard_conclusion == "collect_more_price_conditioned_evidence":
        return "collect_more_price_conditioned_taker_markouts"
    return "keep_price_conditioned_paper_only"


def build_price_conditioned_validation_report(
    *,
    journal_dir: str | Path = DEFAULT_PRICE_CONDITIONED_JOURNAL_DIR,
    min_marked_count: int = 5,
    min_mean_markout_cents: float = 0.0,
    min_win_rate: float = 0.55,
    min_maker_quote_markouts: int = 3,
    min_maker_inferred_fills: int = 1,
    min_mean_maker_markout_cents: float = 0.0,
    min_maker_win_rate: float = 0.55,
    min_resolved_count: int = 0,
    min_resolved_total_pnl_cents: float = 0.0,
    min_resolved_win_rate: float = 0.55,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    markout = summarize_markout_strata(journal_dir, latest_only=True, min_count=1)
    maker = summarize_maker_quote_journal(journal_dir, latest_only=True)
    resolved = summarize_resolved_audits(journal_dir)
    marked_count = int(markout.get("marked_count") or 0)
    mean_markout = _safe_float(markout.get("mean_markout_cents"))
    win_rate = _safe_float(markout.get("win_rate"))
    quote_markout_count = int(maker.get("quote_markout_count") or 0)
    inferred_fill_count = int(maker.get("inferred_fill_count") or 0)
    maker_mean = _safe_float(maker.get("mean_maker_markout_cents"))
    maker_win_rate = _safe_float(maker.get("maker_markout_win_rate"))
    missed_taker_mean = _safe_float(maker.get("mean_missed_taker_markout_cents"))
    resolved_count = int(resolved.get("resolved_count") or 0)
    resolved_total_pnl = _safe_float(resolved.get("total_pnl_cents"))
    resolved_win_rate = _safe_float(resolved.get("win_rate"))
    taker_status = _price_conditioned_taker_status(
        marked_count=marked_count,
        mean_markout_cents=mean_markout,
        win_rate=win_rate,
        min_marked_count=min_marked_count,
        min_mean_markout_cents=min_mean_markout_cents,
        min_win_rate=min_win_rate,
    )
    maker_status = _price_conditioned_maker_status(
        quote_markout_count=quote_markout_count,
        inferred_fill_count=inferred_fill_count,
        mean_maker_markout_cents=maker_mean,
        maker_markout_win_rate=maker_win_rate,
        mean_missed_taker_markout_cents=missed_taker_mean,
        min_quote_markout_count=min_maker_quote_markouts,
        min_inferred_fills=min_maker_inferred_fills,
        min_mean_maker_markout_cents=min_mean_maker_markout_cents,
        min_maker_win_rate=min_maker_win_rate,
    )
    resolved_status = _price_conditioned_resolved_status(
        resolved_count=resolved_count,
        total_pnl_cents=resolved_total_pnl,
        win_rate=resolved_win_rate,
        min_resolved_count=min_resolved_count,
        min_resolved_total_pnl_cents=min_resolved_total_pnl_cents,
        min_resolved_win_rate=min_resolved_win_rate,
    )
    hard_conclusion = _price_conditioned_hard_conclusion(
        taker_status=taker_status,
        maker_status=maker_status,
        resolved_status=resolved_status,
    )
    return {
        "schema_version": QUALITY_SURFACE_SCHEMA_VERSION,
        "report_type": "price_conditioned_validation",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "journal_dir": str(journal_dir),
        "thresholds": {
            "min_marked_count": max(1, int(min_marked_count)),
            "min_mean_markout_cents": float(min_mean_markout_cents),
            "min_win_rate": float(min_win_rate),
            "min_maker_quote_markouts": max(1, int(min_maker_quote_markouts)),
            "min_maker_inferred_fills": max(1, int(min_maker_inferred_fills)),
            "min_mean_maker_markout_cents": float(min_mean_maker_markout_cents),
            "min_maker_win_rate": float(min_maker_win_rate),
            "min_resolved_count": max(0, int(min_resolved_count)),
            "min_resolved_total_pnl_cents": float(min_resolved_total_pnl_cents),
            "min_resolved_win_rate": float(min_resolved_win_rate),
        },
        "taker_entry_evidence": {
            "status": taker_status,
            "marked_count": marked_count,
            "mean_markout_cents": mean_markout,
            "win_rate": win_rate,
            "by_bucket_type": markout.get("by_bucket_type"),
            "by_side": markout.get("by_side"),
            "by_entry_price_bucket": markout.get("by_entry_price_bucket"),
            "by_entry_spread_bucket": markout.get("by_entry_spread_bucket"),
        },
        "maker_execution_evidence": {
            "status": maker_status,
            "quote_count": maker.get("quote_count"),
            "quote_markout_count": quote_markout_count,
            "inferred_fill_count": inferred_fill_count,
            "resting_unfilled_count": maker.get("resting_unfilled_count"),
            "fill_inference_rate": maker.get("fill_inference_rate"),
            "mean_maker_markout_cents": maker_mean,
            "maker_markout_win_rate": maker_win_rate,
            "mean_missed_taker_markout_cents": missed_taker_mean,
        },
        "resolved_evidence": {
            "status": resolved_status,
            "audit_count": resolved.get("audit_count"),
            "resolved_count": resolved_count,
            "unresolved_count": resolved.get("unresolved_count"),
            "win_rate": resolved_win_rate,
            "total_pnl_cents": resolved_total_pnl,
            "mean_pnl_cents": resolved.get("mean_pnl_cents"),
        },
        "recommended_action": _price_conditioned_recommended_action(hard_conclusion),
        "hard_conclusion": hard_conclusion,
    }


def build_quality_surface_report(
    *,
    paper_journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    quarantine_journal_dir: str | Path = DEFAULT_QUARANTINE_JOURNAL_DIR,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    allowed_sides: Iterable[str] = (),
    excluded_bucket_types: Iterable[str] = ("eq",),
    min_price: float = 0.03,
    max_price: float = 0.85,
    max_spread: float = 0.02,
    min_liquidity: float = 10.0,
    min_bid_depth_usdc_3c: float = 10.0,
    min_ask_depth_usdc_3c: float = 10.0,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    quality_kwargs = {
        "allowed_sides": tuple(allowed_sides),
        "excluded_bucket_types": tuple(excluded_bucket_types),
        "min_price": float(min_price),
        "max_price": float(max_price),
        "max_spread": float(max_spread),
        "min_liquidity": float(min_liquidity),
        "min_bid_depth_usdc_3c": float(min_bid_depth_usdc_3c),
        "min_ask_depth_usdc_3c": float(min_ask_depth_usdc_3c),
    }
    journal_records = [
        *_journal_quality_records(paper_journal_dir, journal_name="formal"),
        *_journal_quality_records(quarantine_journal_dir, journal_name="quarantine"),
    ]
    quality_summary = _summarize_quality_records(journal_records, **quality_kwargs)
    closed_rows = _closed_base_rate_rows(backfill_dir, excluded_bucket_types=excluded_bucket_types)
    return {
        "schema_version": QUALITY_SURFACE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "paper_journal_dir": str(paper_journal_dir),
        "quarantine_journal_dir": str(quarantine_journal_dir),
        "backfill_dir": str(backfill_dir),
        "quality_config": {
            "allowed_sides": list(quality_kwargs["allowed_sides"]),
            "excluded_bucket_types": list(quality_kwargs["excluded_bucket_types"]),
            "min_price": quality_kwargs["min_price"],
            "max_price": quality_kwargs["max_price"],
            "max_spread": quality_kwargs["max_spread"],
            "min_liquidity": quality_kwargs["min_liquidity"],
            "min_bid_depth_usdc_3c": quality_kwargs["min_bid_depth_usdc_3c"],
            "min_ask_depth_usdc_3c": quality_kwargs["min_ask_depth_usdc_3c"],
        },
        "quality_markout_summary": quality_summary,
        "closed_base_rate_summary": {
            "note": "Closed markets only provide resolved side/base-rate evidence; they do not include historical entry price, spread, or depth.",
            "row_count": len(closed_rows),
            "by_side": _summarize_closed_base_rates(closed_rows, ("side",)),
            "by_bucket_type": _summarize_closed_base_rates(closed_rows, ("bucket_type",)),
            "by_side_and_bucket_type": _summarize_closed_base_rates(closed_rows, ("side", "bucket_type")),
        },
        "hard_conclusion": (
            "quality_surface_positive_paper_pool"
            if (quality_summary.get("quality_marked_count") or 0) >= 10
            and (quality_summary.get("quality_mean_markout_cents") or 0) >= 0
            and (quality_summary.get("quality_win_rate") or 0) >= 0.55
            else "quality_surface_needs_more_or_better_evidence"
        ),
    }


def _quality_threshold_failure_reasons(
    *,
    marked_count: int,
    mean_markout_cents: Optional[float],
    win_rate: Optional[float],
    min_marked_count: int,
    min_mean_markout_cents: float,
    min_win_rate: float,
) -> List[str]:
    reasons: List[str] = []
    if marked_count < max(1, int(min_marked_count)):
        reasons.append(f"insufficient_marked_count_{marked_count}_of_{max(1, int(min_marked_count))}")
    if mean_markout_cents is None:
        reasons.append("mean_markout_missing")
    elif mean_markout_cents < float(min_mean_markout_cents):
        reasons.append("mean_markout_below_threshold")
    if win_rate is None:
        reasons.append("win_rate_missing")
    elif win_rate < float(min_win_rate):
        reasons.append("win_rate_below_threshold")
    return reasons


def _calibration_sort_key(row: Dict[str, Any]) -> Tuple[int, float, float, int, int]:
    return (
        0 if row.get("eligible_for_paper_profile") else 1,
        -float(row.get("quality_mean_markout_cents") or -9999.0),
        -float(row.get("quality_win_rate") or 0.0),
        -int(row.get("quality_marked_count") or 0),
        -int(row.get("quality_surface_count") or 0),
    )


def _lookup_group_rows(rows: Iterable[Dict[str, Any]], fields: Tuple[str, ...]) -> Dict[Tuple[str, ...], Dict[str, Any]]:
    lookup: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = tuple(str(row.get(field) or "unknown") for field in fields)
        lookup[key] = row
    return lookup


def _promotion_failure_reasons(
    *,
    marked_count: int,
    mean_markout_cents: Optional[float],
    win_rate: Optional[float],
    maker_row: Optional[Dict[str, Any]],
    min_marked_count: int,
    min_mean_markout_cents: float,
    min_win_rate: float,
    min_maker_markout_count: int,
    min_maker_mean_markout_cents: float,
) -> List[str]:
    reasons = _quality_threshold_failure_reasons(
        marked_count=marked_count,
        mean_markout_cents=mean_markout_cents,
        win_rate=win_rate,
        min_marked_count=min_marked_count,
        min_mean_markout_cents=min_mean_markout_cents,
        min_win_rate=min_win_rate,
    )
    if min_maker_markout_count > 0:
        maker_count = int((maker_row or {}).get("inferred_fill_count") or 0)
        maker_mean = _safe_float((maker_row or {}).get("mean_maker_markout_cents"))
        if maker_count < min_maker_markout_count:
            reasons.append(f"insufficient_maker_markouts_{maker_count}_of_{min_maker_markout_count}")
        elif maker_mean is None:
            reasons.append("maker_markout_missing")
        elif maker_mean < float(min_maker_mean_markout_cents):
            reasons.append("maker_mean_markout_below_threshold")
    return reasons


def _promotion_action(failure_reasons: List[str]) -> str:
    if not failure_reasons:
        return "promote_to_formal_paper_review"
    if any(reason.startswith("insufficient_") for reason in failure_reasons):
        return "collect_more_paper_evidence"
    if "maker_mean_markout_below_threshold" in failure_reasons:
        return "keep_quarantine_maker_adverse"
    return "keep_quarantine_negative_forward_edge"


def _promotion_group_sort_key(row: Dict[str, Any]) -> Tuple[int, float, float, int]:
    return (
        0 if row.get("action") == "promote_to_formal_paper_review" else 1,
        -float(row.get("mean_markout_cents") or -9999.0),
        -float(row.get("win_rate") or 0.0),
        -int(row.get("marked_count") or 0),
    )


def _current_quarantine_counts(
    signal_report: Optional[Dict[str, Any]],
    *,
    promotable_reason_keys: set[Tuple[str, ...]],
    promotable_bucket_type_keys: set[Tuple[str, ...]],
) -> Dict[str, Any]:
    if not isinstance(signal_report, dict):
        return {
            "signal_report_seen": False,
            "current_quarantine_count": None,
            "current_promotable_count": None,
            "by_quarantine_reason": [],
            "by_bucket_type": [],
        }
    reason_counts: Dict[str, int] = {}
    bucket_counts: Dict[str, int] = {}
    promotable = 0
    examples: List[Dict[str, Any]] = []
    quarantine = [row for row in signal_report.get("quarantine") or [] if isinstance(row, dict)]
    for row in quarantine:
        reason = str(row.get("quarantine_reason") or "unknown")
        bucket_type = str(row.get("bucket_type") or bucket_type_from_label(row.get("bucket_label")) or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        bucket_counts[bucket_type] = bucket_counts.get(bucket_type, 0) + 1
        reason_key = (reason,)
        bucket_key = (bucket_type,)
        if reason_key in promotable_reason_keys or bucket_key in promotable_bucket_type_keys:
            promotable += 1
            examples.append(
                {
                    "market_slug": row.get("market_slug"),
                    "city": row.get("city"),
                    "side": row.get("side"),
                    "bucket_type": bucket_type,
                    "quarantine_reason": reason,
                    "edge_percent": _safe_float(row.get("edge_percent")),
                    "price": _safe_float(row.get("price")),
                    "spread": _safe_float(row.get("spread")),
                    "counts_for_live_gate": False,
                }
            )
    return {
        "signal_report_seen": True,
        "current_quarantine_count": len(quarantine),
        "current_promotable_count": promotable,
        "by_quarantine_reason": [
            {"quarantine_reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "by_bucket_type": [
            {"bucket_type": bucket_type, "count": count}
            for bucket_type, count in sorted(bucket_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "promotable_examples": examples[:10],
    }


def build_quarantine_promotion_report(
    *,
    paper_journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    quarantine_journal_dir: str | Path = DEFAULT_QUARANTINE_JOURNAL_DIR,
    signal_report: Optional[Dict[str, Any]] = None,
    min_marked_count: int = 5,
    min_mean_markout_cents: float = 0.0,
    min_win_rate: float = 0.55,
    min_maker_markout_count: int = 3,
    min_maker_mean_markout_cents: float = 0.0,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Rank quarantine buckets for possible formal-paper promotion without changing live gate."""

    generated_at = generated_at or utc_now_iso()
    formal = summarize_markout_strata(paper_journal_dir, min_count=1)
    quarantine = summarize_markout_strata(quarantine_journal_dir, min_count=1)
    maker = summarize_maker_quote_strata(quarantine_journal_dir, min_count=1)
    maker_group_names = {
        "by_quarantine_reason": ("quarantine_reason",),
        "by_quarantine_blocker_scope": ("quarantine_blocker_scope",),
        "by_bucket_type": ("bucket_type",),
    }
    group_specs = {
        "by_quarantine_reason": ("quarantine_reason",),
        "by_quarantine_blocker_scope": ("quarantine_blocker_scope",),
        "by_bucket_type": ("bucket_type",),
        "by_entry_price_bucket": ("entry_price_bucket",),
        "by_entry_spread_bucket": ("entry_spread_bucket",),
    }
    groups: Dict[str, List[Dict[str, Any]]] = {}
    promotable_reason_keys: set[Tuple[str, ...]] = set()
    promotable_bucket_type_keys: set[Tuple[str, ...]] = set()
    for group_name, fields in group_specs.items():
        maker_lookup = _lookup_group_rows(
            maker.get(group_name) or [],
            maker_group_names[group_name],
        ) if group_name in maker_group_names else {}
        rows: List[Dict[str, Any]] = []
        for row in quarantine.get(group_name) or []:
            if not isinstance(row, dict):
                continue
            key = tuple(str(row.get(field) or "unknown") for field in fields)
            maker_row = maker_lookup.get(key)
            marked_count = int(row.get("count") or 0)
            mean_markout = _safe_float(row.get("mean_markout_cents"))
            win_rate = _safe_float(row.get("win_rate"))
            failure_reasons = _promotion_failure_reasons(
                marked_count=marked_count,
                mean_markout_cents=mean_markout,
                win_rate=win_rate,
                maker_row=maker_row,
                min_marked_count=min_marked_count,
                min_mean_markout_cents=min_mean_markout_cents,
                min_win_rate=min_win_rate,
                min_maker_markout_count=min_maker_markout_count,
                min_maker_mean_markout_cents=min_maker_mean_markout_cents,
            )
            action = _promotion_action(failure_reasons)
            item = {
                "group_key": {field: key[index] for index, field in enumerate(fields)},
                "marked_count": marked_count,
                "win_rate": win_rate,
                "mean_markout_cents": mean_markout,
                "min_markout_cents": _safe_float(row.get("min_markout_cents")),
                "max_markout_cents": _safe_float(row.get("max_markout_cents")),
                "maker_quote_count": int((maker_row or {}).get("count") or 0),
                "maker_inferred_fill_count": int((maker_row or {}).get("inferred_fill_count") or 0),
                "maker_fill_inference_rate": _safe_float((maker_row or {}).get("fill_inference_rate")),
                "mean_maker_markout_cents": _safe_float((maker_row or {}).get("mean_maker_markout_cents")),
                "mean_missed_taker_markout_cents": _safe_float(
                    (maker_row or {}).get("mean_missed_taker_markout_cents")
                ),
                "failure_reasons": failure_reasons,
                "action": action,
                "counts_for_live_gate": False,
            }
            if action == "promote_to_formal_paper_review":
                if group_name == "by_quarantine_reason":
                    promotable_reason_keys.add(key)
                elif group_name == "by_bucket_type":
                    promotable_bucket_type_keys.add(key)
            rows.append(item)
        groups[group_name] = sorted(rows, key=_promotion_group_sort_key)

    promotable_groups = [
        {"group_name": group_name, **row}
        for group_name, rows in groups.items()
        for row in rows
        if row.get("action") == "promote_to_formal_paper_review"
    ]
    current = _current_quarantine_counts(
        signal_report,
        promotable_reason_keys=promotable_reason_keys,
        promotable_bucket_type_keys=promotable_bucket_type_keys,
    )
    current_promotable = current.get("current_promotable_count")
    return {
        "schema_version": QUALITY_SURFACE_SCHEMA_VERSION,
        "report_type": "quarantine_promotion",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "paper_journal_dir": str(paper_journal_dir),
        "quarantine_journal_dir": str(quarantine_journal_dir),
        "thresholds": {
            "min_marked_count": max(1, int(min_marked_count)),
            "min_mean_markout_cents": float(min_mean_markout_cents),
            "min_win_rate": float(min_win_rate),
            "min_maker_markout_count": max(0, int(min_maker_markout_count)),
            "min_maker_mean_markout_cents": float(min_maker_mean_markout_cents),
        },
        "evidence": {
            "formal_marked_count": formal.get("marked_count"),
            "formal_mean_markout_cents": formal.get("mean_markout_cents"),
            "formal_win_rate": formal.get("win_rate"),
            "quarantine_marked_count": quarantine.get("marked_count"),
            "quarantine_mean_markout_cents": quarantine.get("mean_markout_cents"),
            "quarantine_win_rate": quarantine.get("win_rate"),
            "quarantine_maker_quote_count": maker.get("markout_observation_count"),
            "quarantine_mean_maker_markout_cents": maker.get("mean_maker_markout_cents"),
        },
        "promotion_group_count": len(promotable_groups),
        "promotable_groups": sorted(promotable_groups, key=_promotion_group_sort_key)[:20],
        "groups": groups,
        "current_quarantine": current,
        "hard_conclusion": (
            "quarantine_group_ready_for_formal_paper"
            if promotable_groups and (current_promotable or 0) > 0
            else (
                "historical_quarantine_group_ready_but_no_current_match"
                if promotable_groups
                else "no_quarantine_group_ready_for_formal_paper"
            )
        ),
    }


def _normalized_exclusion_sets(values: Optional[Iterable[Iterable[str]]]) -> List[Tuple[str, ...]]:
    if values is None:
        return [(), ("eq",), ("eq", "range")]
    normalized: List[Tuple[str, ...]] = []
    for item in values:
        row = tuple(sorted({str(value).strip().lower() for value in item if str(value).strip()}))
        if row not in normalized:
            normalized.append(row)
    return normalized or [()]


def build_quality_threshold_calibration_report(
    *,
    paper_journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    quarantine_journal_dir: str | Path = DEFAULT_QUARANTINE_JOURNAL_DIR,
    bucket_type_exclusion_sets: Optional[Iterable[Iterable[str]]] = None,
    min_price_options: Iterable[float] = (0.001, 0.03, 0.05, 0.10),
    max_spread_options: Iterable[float] = (0.005, 0.01, 0.02, 0.03),
    min_liquidity_options: Iterable[float] = (0.0, 10.0, 50.0),
    min_depth_options: Iterable[float] = (0.0, 10.0, 50.0),
    max_price: float = 0.85,
    min_marked_count: int = 10,
    min_mean_markout_cents: float = 0.0,
    min_win_rate: float = 0.55,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Grid-search paper evidence thresholds without changing live-gate policy."""

    generated_at = generated_at or utc_now_iso()
    min_price_values = tuple(float(value) for value in min_price_options)
    max_spread_values = tuple(float(value) for value in max_spread_options)
    min_liquidity_values = tuple(float(value) for value in min_liquidity_options)
    min_depth_values = tuple(float(value) for value in min_depth_options)
    records = [
        *_journal_quality_records(paper_journal_dir, journal_name="formal"),
        *_journal_quality_records(quarantine_journal_dir, journal_name="quarantine"),
    ]
    exclusion_sets = _normalized_exclusion_sets(bucket_type_exclusion_sets)
    rows: List[Dict[str, Any]] = []
    for excluded_bucket_types in exclusion_sets:
        for min_price in min_price_values:
            for max_spread in max_spread_values:
                for min_liquidity in min_liquidity_values:
                    for min_depth in min_depth_values:
                        quality_kwargs = {
                            "excluded_bucket_types": excluded_bucket_types,
                            "min_price": float(min_price),
                            "max_price": float(max_price),
                            "max_spread": float(max_spread),
                            "min_liquidity": float(min_liquidity),
                            "min_bid_depth_usdc_3c": float(min_depth),
                            "min_ask_depth_usdc_3c": float(min_depth),
                        }
                        summary = _summarize_quality_records(records, **quality_kwargs)
                        marked_count = int(summary.get("quality_marked_count") or 0)
                        mean_markout = _safe_float(summary.get("quality_mean_markout_cents"))
                        win_rate = _safe_float(summary.get("quality_win_rate"))
                        failure_reasons = _quality_threshold_failure_reasons(
                            marked_count=marked_count,
                            mean_markout_cents=mean_markout,
                            win_rate=win_rate,
                            min_marked_count=min_marked_count,
                            min_mean_markout_cents=min_mean_markout_cents,
                            min_win_rate=min_win_rate,
                        )
                        row = {
                            "profile_id": stable_json_hash(quality_kwargs, length=16),
                            "quality_config": {
                                **quality_kwargs,
                                "excluded_bucket_types": list(excluded_bucket_types),
                            },
                            "record_count": summary.get("record_count"),
                            "quality_surface_count": summary.get("quality_surface_count"),
                            "quality_marked_count": marked_count,
                            "quality_mean_markout_cents": mean_markout,
                            "quality_win_rate": win_rate,
                            "eligible_for_paper_profile": not failure_reasons,
                            "failure_reasons": failure_reasons,
                            "quality_blocker_counts": (summary.get("quality_blocker_counts") or [])[:10],
                            "by_journal": summary.get("by_journal") or [],
                            "by_bucket_type": summary.get("by_bucket_type") or [],
                            "by_price_and_spread": summary.get("by_price_and_spread") or [],
                        }
                        rows.append(row)
    ranked = sorted(rows, key=_calibration_sort_key)
    eligible = [row for row in ranked if row.get("eligible_for_paper_profile")]
    return {
        "schema_version": QUALITY_SURFACE_SCHEMA_VERSION,
        "report_type": "quality_threshold_calibration",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "paper_journal_dir": str(paper_journal_dir),
        "quarantine_journal_dir": str(quarantine_journal_dir),
        "evidence": {
            "record_count": len(records),
            "formal_record_count": len([row for row in records if row.get("journal") == "formal"]),
            "quarantine_record_count": len([row for row in records if row.get("journal") == "quarantine"]),
        },
        "thresholds": {
            "min_marked_count": max(1, int(min_marked_count)),
            "min_mean_markout_cents": float(min_mean_markout_cents),
            "min_win_rate": float(min_win_rate),
        },
        "grid": {
            "profile_count": len(rows),
            "bucket_type_exclusion_sets": [list(row) for row in exclusion_sets],
            "min_price_options": list(min_price_values),
            "max_spread_options": list(max_spread_values),
            "min_liquidity_options": list(min_liquidity_values),
            "min_depth_options": list(min_depth_values),
            "max_price": float(max_price),
        },
        "eligible_profile_count": len(eligible),
        "recommended_profile": eligible[0] if eligible else None,
        "top_profiles": ranked[:20],
        "hard_conclusion": (
            "threshold_profile_ready_for_paper_review"
            if eligible
            else "no_threshold_profile_passed_evidence_gate"
        ),
    }


def dump_quality_surface_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
