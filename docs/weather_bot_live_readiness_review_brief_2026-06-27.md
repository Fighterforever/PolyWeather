# Weather Bot Live Readiness Review Brief

Date: 2026-06-27

This branch contains a paper-only Polymarket weather trading system extension for PolyWeather. The goal is to turn the weather-market research loop into a real, evidence-gated trading system. It is not live-ready yet.

## Current Hard Conclusion

- Live readiness: 18.33%
- Distance to live: 81.67%
- `live_gate`: false
- Hard conclusion: paper only

Latest local report:

- `logs/weather_cycle_reports/production_cycle_tail_taker_20260627.json` (not committed; local evidence file)

Key blockers from that report:

- No current strict weather signal.
- Main paper fills: 19/30.
- Main markouts: 14/30.
- Mean forward markout is negative.
- Markout win rate is below 55%.
- Resolved audits: 0/10.
- Live permission is false.

## What Was Added

Core trading modules are under `src/trading/`.

CLI tools are under `scripts/weather_market_*.py`.

Tests are under `tests/test_weather_*.py` and related CLI tests.

Important loops:

- Current signal scan and risk filtering.
- Paper journal and manifest recording.
- Forward markout collection.
- Resolved audit skeleton.
- Quarantine and targeted shadow validation.
- Maker quote / missed fill / adverse selection diagnostics.
- Temperature execution experiment.
- Temperature taker paper validation.
- Live readiness scoring.

## Most Recent Strategy Fix

The production paper cycle now restricts `current-signal taker` probing to tail threshold buckets by default:

- allowed bucket types: `le`, `ge`
- exact temperature buckets `eq` are excluded from this taker probe

Reason: local evidence showed broad current-signal taker was dominated by `eq` buckets and had negative markout:

- 20 current-signal taker paper fills
- 40 markout observations
- mean markout: -2.15 cents
- win rate: 0%
- resolved count: 0

This is not a live promotion. It only makes future paper evidence collection narrower and closer to weather-market practice.

## External Benchmark Direction

Public weather-market bot descriptions generally emphasize:

- Ensemble weather forecasts, not point forecasts.
- Official resolution station calibration.
- Per-bucket probability distribution.
- Order-book and liquidity filtering.
- EV / Kelly sizing with caps.
- Paper/live mode separation.
- Settlement and calibration history.
- Kalshi / Polymarket cross-platform comparison where available.

Relevant public references:

- https://github.com/zscdaoo/polymarket-weather-trading-bot
- https://github.com/suislanchez/polymarket-kalshi-weather-bot
- https://github.com/alteregoeth-ai/weatherbot
- https://docs.kalshi.com/api-reference/market/get-market-orderbook.md

## Review Questions

Please review this branch as a senior quant engineer and propose a clear path to live readiness.

Focus on:

1. Whether the readiness scoring is too strict, too loose, or missing key components.
2. How to build enough historical and forward evidence without waiting weeks.
3. How to calibrate official station bias, rounding, and bucket mapping.
4. How to redesign signal generation so it does not rely on fragile raw edge.
5. How to decide between maker and taker execution using missed-fill and adverse-selection data.
6. How to add Kalshi / Polymarket cross-market checks without mixing uncalibrated evidence.
7. What exact gates should be required before tiny-live.

Do not recommend live trading unless the evidence gates are satisfied.
