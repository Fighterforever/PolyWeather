from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List


SCHEMA_VERSION = "polyweather_kalshi_weather_crosscheck.v1"
KALSHI_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
WEATHER_INCLUDE_RE = re.compile(
    r"("
    r"temperature|weather|high\s+temperature|highest\s+temperature|daily\s+high|"
    r"\bmetar\b|\bstation\b|\bforecast\b|\brain\b|\bsnow\b|\bclimate\b|"
    r"\bheat\b|\bcelsius\b|\bfahrenheit\b|\bdegrees?\b"
    r")",
    re.IGNORECASE,
)
SPORTS_EXCLUDE_RE = re.compile(
    r"("
    r"\bWNBA\b|\bNBA\b|\bNFL\b|\bMLB\b|\bNHL\b|\bplayer\b|\brebounds?\b|"
    r"\bpoints?\b|\bassists?\b|\bgoals?\b|\btouchdowns?\b|\bstrikeouts?\b|"
    r"\bbasketball\b|\bbaseball\b|\bfootball\b|\bsoccer\b"
    r")",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def fetch_kalshi_markets(search: str, *, status: str = "closed", limit: int = 100, timeout: int = 20) -> Dict[str, Any]:
    query = urllib.parse.urlencode({"search": search, "status": status, "limit": max(1, int(limit))})
    request = urllib.request.Request(
        f"{KALSHI_MARKETS_URL}?{query}",
        headers={"User-Agent": "PolyWeatherResearch/1.0 paper-only kalshi-crosscheck"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Kalshi markets endpoint returned non-object payload")
    return payload


def kalshi_market_family(item: Dict[str, Any]) -> str:
    text = " ".join(
        _text(item.get(field))
        for field in (
            "ticker",
            "title",
            "subtitle",
            "category",
            "event_ticker",
            "series_ticker",
            "rules_primary",
            "rules_secondary",
        )
    )
    if SPORTS_EXCLUDE_RE.search(text):
        return "excluded_sports"
    if WEATHER_INCLUDE_RE.search(text):
        return "weather"
    return "unknown"


def kalshi_weather_filter(item: Dict[str, Any]) -> Dict[str, Any]:
    family = kalshi_market_family(item)
    if family == "weather":
        return {"weather_filter_passed": True, "kalshi_market_family": family, "exclusion_reason": None}
    reason = "non_weather_kalshi_market" if family == "unknown" else "excluded_sports_kalshi_market"
    return {"weather_filter_passed": False, "kalshi_market_family": family, "exclusion_reason": reason}


def build_kalshi_weather_crosscheck(
    closed_markets: Iterable[Dict[str, Any]],
    *,
    timeout: int = 20,
    limit: int = 100,
    max_queries: int | None = None,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    keys = []
    seen = set()
    for market in closed_markets:
        station = _text(market.get("station_code"))
        target_date = _text(market.get("target_date"))
        threshold = _text(market.get("threshold"))
        if not station or not target_date:
            continue
        key = (station, target_date, threshold)
        if key in seen:
            continue
        seen.add(key)
        keys.append((market, key))
        if max_queries is not None and len(keys) >= int(max_queries):
            break
    for market, (station, target_date, threshold) in keys:
        search = " ".join(part for part in (station, target_date, threshold, "temperature") if part)
        try:
            payload = fetch_kalshi_markets(search, limit=limit, timeout=timeout)
            markets = payload.get("markets") if isinstance(payload.get("markets"), list) else []
        except Exception as exc:
            gaps.append(
                {
                    "station_code": station,
                    "target_date": target_date,
                    "threshold": threshold,
                    "gap_reason": "source_fetch_error",
                    "gap_detail": str(exc),
                }
            )
            continue
        if not markets:
            gaps.append(
                {
                    "station_code": station,
                    "target_date": target_date,
                    "threshold": threshold,
                    "gap_reason": "no_matching_kalshi_market",
                }
            )
            continue
        accepted_for_query = 0
        for item in markets[:5]:
            if not isinstance(item, dict):
                continue
            filter_result = kalshi_weather_filter(item)
            if not filter_result["weather_filter_passed"]:
                gaps.append(
                    {
                        "station_code": station,
                        "target_date": target_date,
                        "threshold": threshold,
                        "kalshi_ticker": item.get("ticker"),
                        "kalshi_title": item.get("title"),
                        **filter_result,
                        "gap_reason": filter_result["exclusion_reason"],
                    }
                )
                continue
            yes_bid = item.get("yes_bid")
            yes_ask = item.get("yes_ask")
            try:
                kalshi_mid = ((float(yes_bid) + float(yes_ask)) / 2.0) / 100.0
            except (TypeError, ValueError):
                kalshi_mid = None
            poly_price = None
            probabilities = market.get("settled_probability_by_outcome") if isinstance(market.get("settled_probability_by_outcome"), dict) else {}
            try:
                poly_price = float(probabilities.get("Yes") if "Yes" in probabilities else probabilities.get("yes"))
            except (TypeError, ValueError):
                poly_price = None
            rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                    "polymarket_market_slug": market.get("market_slug"),
                    "kalshi_ticker": item.get("ticker"),
                    "kalshi_title": item.get("title"),
                    **filter_result,
                    "station_code": station,
                    "target_date": target_date,
                    "threshold": threshold,
                    "polymarket_probability": poly_price,
                    "kalshi_probability": kalshi_mid,
                    "cross_market_probability_diff": (
                        round(float(poly_price) - float(kalshi_mid), 6)
                        if poly_price is not None and kalshi_mid is not None
                        else None
                    ),
                    "diagnostic_only": True,
                }
            )
            accepted_for_query += 1
        if accepted_for_query == 0 and markets:
            gaps.append(
                {
                    "station_code": station,
                    "target_date": target_date,
                    "threshold": threshold,
                    "gap_reason": "no_weather_kalshi_market_after_filter",
                }
            )
    return {
        "schema_version": f"{SCHEMA_VERSION}.report",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "row_count": len(rows),
        "weather_reference_count": len(rows),
        "non_weather_kalshi_excluded_count": len(
            [
                row
                for row in gaps
                if row.get("gap_reason") in {"non_weather_kalshi_market", "excluded_sports_kalshi_market"}
            ]
        ),
        "gap_count": len(gaps),
        "rows": rows,
        "gaps": gaps,
    }


def write_kalshi_crosscheck_artifacts(report: Dict[str, Any], *, output_path: str | Path, gap_report_path: str | Path) -> Dict[str, Any]:
    rows = [row for row in report.get("rows") or [] if isinstance(row, dict)]
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    gap_report = {
        "schema_version": "polyweather_kalshi_weather_crosscheck_gap_report.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "row_count": len(rows),
        "weather_reference_count": len(rows),
        "non_weather_kalshi_excluded_count": report.get("non_weather_kalshi_excluded_count"),
        "gap_count": len(report.get("gaps") or []),
        "gaps": report.get("gaps") or [],
    }
    Path(gap_report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(gap_report_path).write_text(json.dumps(gap_report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return gap_report
