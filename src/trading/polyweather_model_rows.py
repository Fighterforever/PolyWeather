from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.data_collection.city_registry import CITY_REGISTRY


AnalysisRunner = Callable[[str, bool, str], Dict[str, Any]]


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _distribution_from_daily_entry(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(entry, dict):
        return []
    distribution = entry.get("probabilities_all") or entry.get("probabilities") or []
    return [item for item in distribution if isinstance(item, dict)]


def _distribution_from_analysis(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    probabilities = data.get("probabilities") if isinstance(data, dict) else {}
    if not isinstance(probabilities, dict):
        return []
    distribution = probabilities.get("distribution_all") or probabilities.get("distribution") or []
    return [item for item in distribution if isinstance(item, dict)]


def _best_probability(distribution: Iterable[Dict[str, Any]]) -> Optional[float]:
    values = [_safe_float(item.get("probability")) for item in distribution if isinstance(item, dict)]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _model_row_from_daily_entry(
    *,
    city: str,
    data: Dict[str, Any],
    target_date: str,
    entry: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    distribution = _distribution_from_daily_entry(entry)
    if not distribution:
        return None
    deb = entry.get("deb") if isinstance(entry.get("deb"), dict) else {}
    best_model_prob = _best_probability(distribution)
    return {
        "id": f"{city}:{target_date}",
        "source": "polyweather_analysis_fallback",
        "city": city,
        "city_display_name": data.get("display_name") or (CITY_REGISTRY.get(city) or {}).get("name") or city,
        "local_date": target_date,
        "selected_date": target_date,
        "local_time": data.get("local_time"),
        "temp_symbol": data.get("temp_symbol"),
        "deb_prediction": deb.get("prediction"),
        "model_cluster_sources": entry.get("models") if isinstance(entry.get("models"), dict) else {},
        "distribution_preview": distribution[:6],
        "distribution_full": distribution,
        "probability_engine": "polyweather_analysis",
        "probability_calibration_mode": None,
        "model_probability": best_model_prob,
        "final_score": _safe_float(deb.get("prediction")) or 0.0,
        "active": True,
        "closed": False,
        "tradable": False,
        "accepting_orders": False,
    }


def analysis_payload_to_model_rows(
    city: str,
    data: Dict[str, Any],
    *,
    target_dates: Optional[Iterable[Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    city_key = str(city or "").strip().lower()
    if not city_key or not isinstance(data, dict):
        return []
    dates = [str(date or "").strip() for date in (target_dates or []) if str(date or "").strip()]
    if not dates:
        local_date = str(data.get("local_date") or "").strip()
        if local_date:
            dates = [local_date]

    multi_model_daily = data.get("multi_model_daily") if isinstance(data.get("multi_model_daily"), dict) else {}
    rows: List[Dict[str, Any]] = []
    seen = set()
    for target_date in dates:
        entry = multi_model_daily.get(target_date) if isinstance(multi_model_daily, dict) else None
        row = (
            _model_row_from_daily_entry(
                city=city_key,
                data=data,
                target_date=target_date,
                entry=entry,
            )
            if isinstance(entry, dict)
            else None
        )
        if row is not None:
            rows.append(row)
            seen.add(target_date)

    local_date = str(data.get("local_date") or "").strip()
    if local_date and local_date not in seen and (not dates or local_date in dates):
        distribution = _distribution_from_analysis(data)
        if distribution:
            deb = data.get("deb") if isinstance(data.get("deb"), dict) else {}
            rows.append(
                {
                    "id": f"{city_key}:{local_date}",
                    "source": "polyweather_analysis_fallback",
                    "city": city_key,
                    "city_display_name": data.get("display_name") or (CITY_REGISTRY.get(city_key) or {}).get("name") or city_key,
                    "local_date": local_date,
                    "selected_date": local_date,
                    "local_time": data.get("local_time"),
                    "temp_symbol": data.get("temp_symbol"),
                    "deb_prediction": deb.get("prediction"),
                    "model_cluster_sources": (
                        (data.get("multi_model") or {}).get("forecasts")
                        if isinstance(data.get("multi_model"), dict)
                        else {}
                    ),
                    "distribution_preview": distribution[:6],
                    "distribution_full": distribution,
                    "probability_engine": (data.get("probabilities") or {}).get("engine")
                    if isinstance(data.get("probabilities"), dict)
                    else "polyweather_analysis",
                    "probability_calibration_mode": (data.get("probabilities") or {}).get("calibration_mode")
                    if isinstance(data.get("probabilities"), dict)
                    else None,
                    "model_probability": _best_probability(distribution),
                    "final_score": _safe_float(deb.get("prediction")) or 0.0,
                    "active": True,
                    "closed": False,
                    "tradable": False,
                    "accepting_orders": False,
                }
            )
    return rows


def _default_analysis_runner(city: str, force_refresh: bool, detail_mode: str) -> Dict[str, Any]:
    from loguru import logger

    logger.disable("src")
    logger.disable("web")
    try:
        from web.analysis_service import _analyze

        return _analyze(city, force_refresh=force_refresh, detail_mode=detail_mode)
    finally:
        logger.enable("src")
        logger.enable("web")


def build_analysis_model_payload_for_targets(
    targets: Dict[str, Iterable[Optional[str]]],
    *,
    force_refresh: bool = False,
    detail_mode: str = "panel",
    analysis_runner: Optional[AnalysisRunner] = None,
) -> Dict[str, Any]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    runner = analysis_runner or _default_analysis_runner
    rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    normalized_targets: Dict[str, List[Optional[str]]] = {}
    for city, dates in (targets or {}).items():
        city_key = str(city or "").strip().lower()
        if not city_key or city_key not in CITY_REGISTRY:
            continue
        deduped_dates: List[Optional[str]] = []
        for date in dates or [None]:
            normalized_date = str(date or "").strip() or None
            if normalized_date not in deduped_dates:
                deduped_dates.append(normalized_date)
        normalized_targets[city_key] = deduped_dates or [None]

    for city, dates in normalized_targets.items():
        try:
            data = runner(city, bool(force_refresh), detail_mode)
        except Exception as exc:
            errors.append({"city": city, "error": str(exc)})
            continue
        rows.extend(analysis_payload_to_model_rows(city, data, target_dates=dates))

    return {
        "schema_version": "polyweather_analysis_model_rows.v1",
        "snapshot_id": f"polyweather-analysis-model-{generated_at}",
        "generated_at": generated_at,
        "status": "ready" if rows else "failed",
        "source": "polyweather_analysis_fallback",
        "rows": rows,
        "diagnostics": {
            "target_city_count": len(normalized_targets),
            "target_row_count": sum(len(dates) for dates in normalized_targets.values()),
            "model_rows": len(rows),
            "errors": errors,
        },
    }


def merge_scan_model_payloads(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    primary_rows = primary.get("rows") if isinstance(primary, dict) else []
    fallback_rows = fallback.get("rows") if isinstance(fallback, dict) else []
    rows = [
        row
        for row in (primary_rows or [])
        if isinstance(row, dict)
    ] + [
        row
        for row in (fallback_rows or [])
        if isinstance(row, dict)
    ]
    diagnostics = {
        "primary_status": primary.get("status") if isinstance(primary, dict) else None,
        "primary_snapshot_id": primary.get("snapshot_id") if isinstance(primary, dict) else None,
        "primary_rows": len(primary_rows or []) if isinstance(primary, dict) else 0,
        "fallback_status": fallback.get("status") if isinstance(fallback, dict) else None,
        "fallback_snapshot_id": fallback.get("snapshot_id") if isinstance(fallback, dict) else None,
        "fallback_rows": len(fallback_rows or []) if isinstance(fallback, dict) else 0,
        "fallback_diagnostics": fallback.get("diagnostics") if isinstance(fallback, dict) else None,
    }
    return {
        "schema_version": "polyweather_merged_model_rows.v1",
        "snapshot_id": diagnostics["primary_snapshot_id"] or diagnostics["fallback_snapshot_id"],
        "generated_at": primary.get("generated_at") if isinstance(primary, dict) else fallback.get("generated_at"),
        "status": "ready" if rows else "failed",
        "source": "scan_terminal_plus_analysis_fallback",
        "rows": rows,
        "diagnostics": diagnostics,
    }
