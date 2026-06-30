from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl
from src.trading.polymarket_alpha.reward_programs import extract_liquidity_reward_metadata, raw_reward_field_names


SCHEMA_VERSION = "polyweather_polymarket_alpha_reward_metadata.v1"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _condition_id(row: Dict[str, Any], gamma_market: Optional[Dict[str, Any]] = None) -> str:
    gamma_market = gamma_market or {}
    for key in ("condition_id", "conditionId", "conditionID"):
        value = _text(row.get(key)) or _text(gamma_market.get(key))
        if value:
            return value
    return ""


def _token_ids(row: Dict[str, Any], gamma_market: Optional[Dict[str, Any]] = None, clob_market: Optional[Dict[str, Any]] = None) -> List[str]:
    gamma_market = gamma_market or {}
    clob_market = clob_market or {}
    values = [
        row.get("yes_token_id"),
        row.get("no_token_id"),
        row.get("token_id"),
        row.get("clobTokenIds"),
        row.get("token_ids"),
        gamma_market.get("clobTokenIds"),
        gamma_market.get("token_ids"),
    ]
    output: List[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            output.extend(_text(item) for item in value if _text(item))
        else:
            parsed = _parse_json_list(value)
            if parsed:
                output.extend(_text(item) for item in parsed if _text(item))
            elif _text(value):
                output.append(_text(value))
    for token in clob_market.get("tokens") or []:
        if isinstance(token, dict) and _text(token.get("token_id")):
            output.append(_text(token.get("token_id")))
    seen: set[str] = set()
    deduped: List[str] = []
    for token in output:
        if token and token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped


class PolymarketRewardMetadataClient:
    def __init__(
        self,
        *,
        gamma_base_url: str = GAMMA_BASE_URL,
        clob_base_url: str = CLOB_BASE_URL,
        timeout: float = 15.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.clob_base_url = clob_base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get_json(self, base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        response = self.session.get(f"{base_url}{path}", params=params or {}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_gamma_market(self, market_id: str) -> Dict[str, Any]:
        return self._get_json(self.gamma_base_url, f"/markets/{market_id}", {})

    def get_clob_market_by_condition_id(self, condition_id: str) -> Dict[str, Any]:
        return self._get_json(self.clob_base_url, f"/markets/{condition_id}", {})

    def get_clob_market_search(self, condition_id: str) -> Dict[str, Any]:
        payload = self._get_json(self.clob_base_url, "/markets", {"condition_id": condition_id})
        if isinstance(payload, dict) and isinstance(payload.get("data"), list) and payload["data"]:
            first = payload["data"][0]
            return first if isinstance(first, dict) else {}
        return {}


def _merge_reward_metadata(*payloads: Dict[str, Any]) -> Dict[str, Any]:
    best: Dict[str, Any] = {}
    field_names: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict) or not payload:
            continue
        meta = extract_liquidity_reward_metadata(payload)
        field_names.update(meta.get("raw_field_names_found") or [])
        if meta.get("reward_program_type") == "liquidity_reward":
            best = meta
            break
        if not best or (best.get("reward_program_type") != "liquidity_reward" and meta.get("raw_field_names_found")):
            best = meta
    if not best:
        best = extract_liquidity_reward_metadata({})
    best["raw_field_names_found"] = sorted(set(best.get("raw_field_names_found") or []) | field_names)
    return best


def audit_weather_reward_metadata(
    *,
    markets: Iterable[Dict[str, Any]],
    client: Optional[PolymarketRewardMetadataClient] = None,
    fetch: bool = True,
    max_markets: int = 0,
) -> Dict[str, Any]:
    client = client or PolymarketRewardMetadataClient()
    rows: List[Dict[str, Any]] = []
    raw_samples: List[Dict[str, Any]] = []
    source_attempt_counter: Counter[str] = Counter()
    limit = int(max_markets or 0)
    for index, market in enumerate(row for row in markets if isinstance(row, dict)):
        if limit > 0 and index >= limit:
            break
        gamma_market: Dict[str, Any] = {}
        clob_market: Dict[str, Any] = {}
        clob_search_market: Dict[str, Any] = {}
        attempts: List[Dict[str, Any]] = []
        market_id = _text(market.get("market_id"))
        condition_id = _condition_id(market)
        if fetch and market_id:
            source_attempt_counter["gamma_market_object"] += 1
            try:
                gamma_market = client.get_gamma_market(market_id)
                attempts.append({"source": "gamma_market_object", "status": "ok", "raw_field_names_found": raw_reward_field_names(gamma_market)})
            except requests.HTTPError as exc:
                attempts.append({"source": "gamma_market_object", "status": "failed", "error": f"http_{exc.response.status_code if exc.response is not None else 'unknown'}"})
            except requests.RequestException as exc:
                attempts.append({"source": "gamma_market_object", "status": "failed", "error": type(exc).__name__})
        condition_id = _condition_id(market, gamma_market)
        if fetch and condition_id:
            source_attempt_counter["clob_market_full_object"] += 1
            try:
                clob_market = client.get_clob_market_by_condition_id(condition_id)
                attempts.append({"source": "clob_market_full_object", "status": "ok", "raw_field_names_found": raw_reward_field_names(clob_market)})
            except requests.HTTPError as exc:
                attempts.append({"source": "clob_market_full_object", "status": "failed", "error": f"http_{exc.response.status_code if exc.response is not None else 'unknown'}"})
            except requests.RequestException as exc:
                attempts.append({"source": "clob_market_full_object", "status": "failed", "error": type(exc).__name__})
            source_attempt_counter["clob_market_info_search"] += 1
            try:
                clob_search_market = client.get_clob_market_search(condition_id)
                attempts.append({"source": "clob_market_info_search", "status": "ok", "raw_field_names_found": raw_reward_field_names(clob_search_market)})
            except requests.HTTPError as exc:
                attempts.append({"source": "clob_market_info_search", "status": "failed", "error": f"http_{exc.response.status_code if exc.response is not None else 'unknown'}"})
            except requests.RequestException as exc:
                attempts.append({"source": "clob_market_info_search", "status": "failed", "error": type(exc).__name__})
        elif fetch:
            attempts.append({"source": "clob_market_full_object", "status": "skipped", "error": "no_condition_id"})

        metadata = _merge_reward_metadata(clob_market, clob_search_market, gamma_market, market)
        source_found = None
        for source, payload in (
            ("clob_market_full_object", clob_market),
            ("clob_market_info_search", clob_search_market),
            ("gamma_market_object", gamma_market),
            ("input_market_row", market),
        ):
            if payload and extract_liquidity_reward_metadata(payload).get("reward_program_type") == "liquidity_reward":
                source_found = source
                break

        gap_reason = metadata.get("gap_reason")
        if not condition_id:
            gap_reason = "no_condition_id"
        elif attempts and all(row.get("status") == "failed" for row in attempts if row.get("source") != "gamma_market_object"):
            gap_reason = "market_info_fetch_failed"
        elif metadata.get("reward_program_type") != "liquidity_reward" and metadata.get("raw_field_names_found"):
            gap_reason = metadata.get("gap_reason") or "not_reward_eligible"
        elif metadata.get("reward_program_type") != "liquidity_reward":
            gap_reason = "no_reward_fields_in_response"

        output = {
            "schema_version": f"{SCHEMA_VERSION}.row",
            "market_slug": market.get("market_slug"),
            "condition_id": condition_id or None,
            "market_id": market_id or None,
            "clob_token_ids": _token_ids(market, gamma_market, clob_market or clob_search_market),
            "event_slug": market.get("event_slug"),
            "city": market.get("city"),
            "active": clob_market.get("active", gamma_market.get("active", market.get("active"))),
            "feesEnabled": metadata.get("feesEnabled"),
            "min_incentive_size": metadata.get("min_incentive_size"),
            "max_incentive_spread": metadata.get("max_incentive_spread"),
            "max_incentive_spread_raw": metadata.get("max_incentive_spread_raw"),
            "reward_allocation": metadata.get("reward_allocation"),
            "rewards_daily_rate": metadata.get("rewards_daily_rate"),
            "rewards_epoch": metadata.get("rewards_epoch"),
            "total_market_q_score": metadata.get("total_market_q_score"),
            "reward_program_type": metadata.get("reward_program_type"),
            "source_found": source_found,
            "raw_field_names_found": metadata.get("raw_field_names_found") or [],
            "endpoint_attempts": attempts,
            "gap_reason": gap_reason,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        rows.append(output)
        raw_samples.append(
            {
                "market_slug": market.get("market_slug"),
                "condition_id": condition_id or None,
                "gamma_keys": sorted(gamma_market.keys()) if gamma_market else [],
                "clob_keys": sorted(clob_market.keys()) if clob_market else [],
                "clob_search_keys": sorted(clob_search_market.keys()) if clob_search_market else [],
                "gamma_reward_fields": {key: gamma_market.get(key) for key in raw_reward_field_names(gamma_market)} if gamma_market else {},
                "clob_reward_fields": {key: clob_market.get(key) for key in raw_reward_field_names(clob_market)} if clob_market else {},
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )

    gap_counts = Counter(str(row.get("gap_reason") or "none") for row in rows)
    available = [row for row in rows if row.get("reward_program_type") == "liquidity_reward"]
    minmax = [row for row in available if row.get("min_incentive_size") is not None and row.get("max_incentive_spread") is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "market_count": len(rows),
        "reward_metadata_available_count": len(available),
        "min_incentive_size_found_count": len([row for row in rows if row.get("min_incentive_size") is not None]),
        "max_incentive_spread_found_count": len([row for row in rows if row.get("max_incentive_spread") is not None]),
        "min_max_incentive_found_count": len(minmax),
        "source_attempt_counts": [{"source": key, "count": value} for key, value in sorted(source_attempt_counter.items())],
        "gap_counts": [{"reason": key, "count": value} for key, value in sorted(gap_counts.items())],
        "rows": rows,
        "raw_samples": raw_samples[:100],
    }


def audit_reward_allocation_conversion(
    *,
    reward_metadata_rows: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    update_by_slug: Dict[str, float] = {}
    for row in quote_updates:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("market_slug") or "")
        if not slug:
            continue
        update_by_slug[slug] = max(
            update_by_slug.get(slug, 0.0),
            float(row.get("cumulative_reward_points_proxy") or row.get("q_min_proxy") or 0.0),
        )
    rows: List[Dict[str, Any]] = []
    for row in reward_metadata_rows:
        if not isinstance(row, dict):
            continue
        allocation = row.get("reward_allocation")
        allocation_value: Optional[float]
        try:
            allocation_value = float(allocation) if allocation is not None else None
        except (TypeError, ValueError):
            allocation_value = None
        total_score = row.get("total_market_q_score")
        try:
            total_score_value = float(total_score) if total_score is not None else None
        except (TypeError, ValueError):
            total_score_value = None
        slug = str(row.get("market_slug") or "")
        our_score = update_by_slug.get(slug)
        estimated_share = None
        estimated_cents = None
        gap_reason = None
        if allocation_value is None:
            gap_reason = "reward_allocation_unavailable_in_market_objects"
        elif total_score_value is None or total_score_value <= 0:
            gap_reason = "total_competitor_q_score_unavailable"
        elif our_score is None:
            gap_reason = "our_q_min_proxy_unavailable"
        else:
            estimated_share = min(1.0, max(0.0, float(our_score) / total_score_value))
            estimated_cents = round(estimated_share * allocation_value, 8)
        rows.append(
            {
                "schema_version": f"{SCHEMA_VERSION}.allocation_row",
                "market_slug": row.get("market_slug"),
                "condition_id": row.get("condition_id"),
                "fields_found": row.get("raw_field_names_found") or row.get("fields_found") or [],
                "reward_allocation_raw": allocation,
                "rewards_daily_rate": row.get("rewards_daily_rate"),
                "reward_epoch": row.get("reward_epoch") or row.get("rewards_epoch"),
                "rewards_epoch": row.get("rewards_epoch") or row.get("reward_epoch"),
                "total_market_q_score_available": total_score_value is not None,
                "total_market_score_available": total_score_value is not None,
                "total_competitor_score_available": total_score_value is not None,
                "our_q_min_proxy": our_score,
                "estimated_share_if_total_available": estimated_share,
                "estimated_reward_cents_proxy": estimated_cents,
                "gap_reason": gap_reason,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    gap_counts = Counter(str(row.get("gap_reason") or "none") for row in rows)
    return {
        "schema_version": f"{SCHEMA_VERSION}.allocation_audit",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "market_count": len(rows),
        "reward_allocation_available_count": len([row for row in rows if row.get("reward_allocation_raw") is not None]),
        "total_market_q_score_available_count": len([row for row in rows if row.get("total_market_q_score_available")]),
        "estimated_reward_cents_available_count": len([row for row in rows if row.get("estimated_reward_cents_proxy") is not None]),
        "estimated_reward_cents_proxy": None,
        "estimated_reward_cents_proxy_gap_reason": "requires_reward_allocation_and_total_competitor_q_score",
        "gap_counts": [{"reason": key, "count": value} for key, value in sorted(gap_counts.items())],
        "rows": rows,
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


__all__ = [
    "SCHEMA_VERSION",
    "PolymarketRewardMetadataClient",
    "audit_weather_reward_metadata",
    "audit_reward_allocation_conversion",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
