#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.weather.metar_intraday_collector import (  # noqa: E402
    DEFAULT_USER_AGENT,
    collect_metar_intraday_history,
)
from src.weather.weather_observations import OfficialIntradayObservationRepository  # noqa: E402
from src.weather.weather_sources import utc_iso  # noqa: E402


DEFAULT_OUTPUT = Path("evidence/official_observations/metar_intraday_history.jsonl")
DEFAULT_MANIFEST = Path("evidence/official_observations/metar_intraday_history_manifest.json")
DEFAULT_GAP_REPORT = Path("evidence/official_observations/metar_intraday_gap_report.json")


def _default_start_date() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=14)).isoformat()


def _default_end_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _date_coverage(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        station = str(row.get("station_code") or "").strip().upper()
        target_date = str(row.get("target_date_local") or row.get("target_date") or "").strip()
        if station and target_date:
            counts[(station, target_date)] += 1
    return [
        {"station_code": station, "target_date": target_date, "observation_count": count}
        for (station, target_date), count in sorted(counts.items())
    ]


def build_manifest(
    report: Dict[str, Any],
    *,
    output_path: str | Path,
    manifest_path: str | Path,
    gap_report_path: str | Path,
    written_count: int,
) -> Dict[str, Any]:
    observations = [row for row in report.get("observations") or [] if isinstance(row, dict)]
    station_codes = sorted({str(row.get("station_code") or "").strip().upper() for row in observations if row.get("station_code")})
    latest: Dict[str, str] = {}
    for row in observations:
        station = str(row.get("station_code") or "").strip().upper()
        observed_at = str(row.get("observed_at") or "").strip()
        if station and observed_at:
            latest[station] = max([value for value in (latest.get(station), observed_at) if value])
    return {
        "schema_version": "polyweather_metar_intraday_history_manifest.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "source": report.get("source"),
        "fetched_at": report.get("fetched_at"),
        "start_date": report.get("start_date"),
        "end_date": report.get("end_date"),
        "requested_hours": report.get("requested_hours"),
        "output_path": str(output_path),
        "manifest_path": str(manifest_path),
        "gap_report_path": str(gap_report_path),
        "station_codes": station_codes,
        "station_count": len(station_codes),
        "observation_count": len(observations),
        "written_count": written_count,
        "date_coverage": _date_coverage(observations),
        "latest_observation_at_by_station": latest,
        "gap_count": len(report.get("gaps") or []),
    }


def build_gap_report(report: Dict[str, Any], *, date_coverage: List[Dict[str, Any]]) -> Dict[str, Any]:
    gaps = [dict(row) for row in report.get("gaps") or [] if isinstance(row, dict)]
    requested_stations = [str(code).strip().upper() for code in report.get("station_codes") or [] if str(code).strip()]
    covered = {(row["station_code"], row["target_date"]) for row in date_coverage}
    start_date = str(report.get("start_date") or "")
    end_date = str(report.get("end_date") or "")
    if start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        cursor = start
        while cursor <= end:
            for station in requested_stations:
                if (station, cursor.isoformat()) not in covered:
                    gaps.append(
                        {
                            "station_code": station,
                            "target_date": cursor.isoformat(),
                            "settlement_source": "metar",
                            "source": report.get("source"),
                            "gap_reason": "missing_station_date_metar_rows",
                        }
                    )
            cursor += timedelta(days=1)
    return {
        "schema_version": "polyweather_metar_intraday_gap_report.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "source": report.get("source"),
        "fetched_at": report.get("fetched_at"),
        "start_date": start_date,
        "end_date": end_date,
        "gap_count": len(gaps),
        "gaps": gaps,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill paper-only AviationWeather METAR intraday history.")
    parser.add_argument("--station-code", action="append", dest="station_codes", default=None)
    parser.add_argument("--start-date", default=_default_start_date())
    parser.add_argument("--end-date", default=_default_end_date())
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--gap-report", default=str(DEFAULT_GAP_REPORT))
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--rate-limit-seconds", type=float, default=0.0)
    parser.add_argument("--conservative-available-delay-minutes", type=float, default=10.0)
    parser.add_argument("--fetched-at", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    station_codes = args.station_codes or ["LTAC", "UUWW", "EGLC"]
    fetched_at = args.fetched_at or utc_iso(datetime.now(timezone.utc))
    try:
        report = collect_metar_intraday_history(
            station_codes=station_codes,
            start_date=args.start_date,
            end_date=args.end_date,
            fetched_at=fetched_at,
            timeout=int(args.timeout),
            user_agent=args.user_agent,
            conservative_available_delay_minutes=float(args.conservative_available_delay_minutes),
        )
    except Exception as exc:
        report = {
            "schema_version": "polyweather_metar_intraday_history_backfill.v1",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "source": "aviationweather_metar_history",
            "fetched_at": fetched_at,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "station_codes": station_codes,
            "observations": [],
            "observation_count": 0,
            "station_count": 0,
            "gaps": [
                {
                    "station_code": str(station).strip().upper(),
                    "settlement_source": "metar",
                    "source": "aviationweather_metar_history",
                    "gap_reason": "source_fetch_error",
                    "gap_detail": str(exc),
                }
                for station in station_codes
            ],
        }
    output_path = Path(args.output)
    manifest_path = Path(args.manifest)
    gap_report_path = Path(args.gap_report)
    repo = OfficialIntradayObservationRepository(output_path)
    written_count = repo.append(report.get("observations") or [])
    manifest = build_manifest(
        report,
        output_path=output_path,
        manifest_path=manifest_path,
        gap_report_path=gap_report_path,
        written_count=written_count,
    )
    gap_report = build_gap_report(report, date_coverage=manifest["date_coverage"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    gap_report_path.parent.mkdir(parents=True, exist_ok=True)
    gap_report_path.write_text(json.dumps(gap_report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"manifest": manifest, "gap_report": gap_report}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
