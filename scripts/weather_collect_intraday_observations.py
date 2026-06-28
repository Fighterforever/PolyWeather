#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.weather.metar_intraday_collector import (  # noqa: E402
    SOURCE_NAME,
    collect_metar_intraday_observations,
)
from src.weather.weather_observations import OfficialIntradayObservationRepository  # noqa: E402
from src.weather.weather_sources import utc_iso  # noqa: E402


def _write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _failure_report(station_codes: list[str], *, fetched_at: str, error: Exception) -> Dict[str, Any]:
    return {
        "schema_version": "polyweather_metar_intraday_collection.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "source": SOURCE_NAME,
        "fetched_at": fetched_at,
        "station_codes": sorted(station_codes),
        "observations": [],
        "observation_count": 0,
        "station_count": 0,
        "gaps": [
            {
                "station_code": station,
                "settlement_source": "metar",
                "source": SOURCE_NAME,
                "gap_reason": "source_fetch_error",
                "gap_detail": str(error),
            }
            for station in sorted(station_codes)
        ],
    }


def run_collection(args: argparse.Namespace) -> Dict[str, Any]:
    fetched_at = args.fetched_at or utc_iso(datetime.now(timezone.utc))
    station_codes = [str(code).strip().upper() for code in args.station_codes if str(code).strip()]
    try:
        report = collect_metar_intraday_observations(
            station_codes=station_codes,
            fetched_at=fetched_at,
        )
    except Exception as exc:
        report = _failure_report(station_codes, fetched_at=fetched_at, error=exc)
    output = Path(args.output)
    repo = OfficialIntradayObservationRepository(output)
    written_count = repo.append(report.get("observations") or [])
    manifest = {
        "schema_version": "polyweather_intraday_observation_manifest.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "source": SOURCE_NAME,
        "output_path": str(output),
        "manifest_path": str(args.manifest),
        "fetched_at": fetched_at,
        "station_codes": station_codes,
        "station_count": int(report.get("station_count") or 0),
        "observation_count": int(report.get("observation_count") or 0),
        "written_count": written_count,
        "gaps": report.get("gaps") or [],
        "latest_observation_at_by_station": _latest_by_station(report.get("observations") or []),
    }
    _write_json(args.manifest, manifest)
    return {"collection": report, "manifest": manifest}


def _latest_by_station(rows: list[dict]) -> dict:
    latest: Dict[str, str] = {}
    for row in rows:
        station = str(row.get("station_code") or "").strip().upper()
        observed_at = str(row.get("observed_at") or "").strip()
        if station and observed_at:
            latest[station] = max([value for value in (latest.get(station), observed_at) if value])
    return latest


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect paper-only official intraday METAR observations.")
    parser.add_argument("--station-code", action="append", dest="station_codes", default=[])
    parser.add_argument("--output", default="evidence/official_observations/intraday_observations.jsonl")
    parser.add_argument("--manifest", default="evidence/official_observations/manifest.json")
    parser.add_argument("--fetched-at", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    payload = run_collection(parse_args(argv))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
