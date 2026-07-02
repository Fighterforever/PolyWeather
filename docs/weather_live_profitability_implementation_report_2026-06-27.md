# Weather Live Profitability Implementation Report

Date: 2026-06-27

## Summary

This round moved the paper-only Polymarket weather stack toward the requested profitability loop:

`settlement spec -> station weather truth -> executable price -> ev_safe -> orderbook evidence -> replay -> resolved payout audit`

It did not make the system live-ready. Real order paths remain hard-disabled and the latest local readiness report still has `live_gate=false`.

## Follow-up Evidence-Gate Tightening

This follow-up did not raise readiness thresholds or enable live trading. It tightened the semantics around evidence:

- `weather_live_readiness.py` now exposes `evidence_gate_passed`, `legacy_readiness_pct`, and `live_gate_deprecated` so downstream reviewers do not treat the legacy percent score as live authorization.
- Current signal availability continues to prefer `strict_gate_diagnostics.live_eligible_candidate_count`; a raw `summary.candidate_count` no longer helps the hard gate when strict live-eligible count is zero.
- `weather_strict_gate_replay.py` now emits an explicit EV audit summary:
  - `resolved_fill_count`
  - `resolved_fill_coverage`
  - `positive_ev_safe_but_negative_pnl_count`
  - `by_strategy_bucket`
- Strict replay now fails with `strict_gate_replay_negative_resolved_pnl` whenever resolved replay PnL is negative, even if orderbook coverage, fills, and token outcomes are present.
- `weather_archived_orderbook_due_refresh_report.py` now reports before/after closed/archive token overlap for targeted archived-orderbook closed-backfill refreshes.
- `weather_live_evidence_bundle.py` only runs official-value backfill for closed records that overlap archived Yes-side orderbook tokens. This prevents broad closed-market truth backfill from masquerading as executable replay evidence.
- Official value backfill now caches local station/date/source lookups, so multiple buckets sharing the same settlement station/date/source reuse one observation-store request.
- Strict replay historical evidence and settlement calibration now expose no-lookahead status; future evidence remains rejected.
- Weather signal ranking now prioritizes `ev_safe`, then executable depth/spread/liquidity. `edge_percent` and source `final_score` remain diagnostic/display fields and no longer drive candidate ranking.

Validation:

```text
PYTHONPATH=src .venv/bin/python -m pytest -q
926 passed, 26 warnings
```

Remaining hard blockers are unchanged in substance: resolved strict replay PnL, settlement calibration probability score, and official station truth coverage must all be present and non-negative before the system can move beyond paper-only review.

## Completed Gates

### P0 paper-only boundary

- `scripts/weather_market_paper_cycle.py` still returns `paper_only=true` and `live_order_path=false`.
- Added/kept a test proving even `--live-permission` cannot create a live order path.
- `weather_live_readiness.py` now has a module-level hard-disabled live order path. Even if a caller requests `live_order_path_available=true`, the report records it as `requested_live_order_path_available=true` but keeps effective `live_order_path_available=false`, `live_authorization_pct=0`, and `hard_conclusion=只能继续 paper`.
- Added orderbook archive integration as paper-only evidence only; archive records have `counts_for_live_gate=false`.

### P1 canonical settlement spec

- Added `src/trading/weather_market_catalog.py`.
- Added `SettlementSpec`, `MarketBucket`, and `ResolvedOutcome` schemas.
- `weather_market_enrichment.py` and `polymarket_readonly.py` now attach settlement specs and market buckets for parsed temperature markets.
- Temperature markets missing city/station/source/date/end time are marked `unsupported_settlement_spec` and cannot become strict candidates.
- Improved slug/date parsing for `highest-temperature-in-seoul-on-june-27-2026-28c-or-above` style markets.

### P1 resolved market truth

- Closed backfill records now preserve:
  - `token_id_by_outcome`
  - `winning_token_id`
  - `resolution_source`
  - `rule_text`
  - `rule_hash`
  - `settlement_spec`
  - `official_final_value`
- Resolved audit records now expose `winning_token_id`, `resolved_outcome`, `resolution_rule_hash`, and `official_final_value`.
- Backfill market reconstruction now preserves all token ids, so losing tokens can be audited by `token_id` instead of falling back to outcome text.

### P1 orderbook archive

- Added `src/trading/polymarket_orderbook_archive.py`.
- Readonly orderbook summaries now keep bid/ask ladders, not just top-of-book aggregates.
- Paper cycle archives current orderbooks into JSONL manifests when orderbook collection is enabled.
- Added depth-walk taker buy/sell probes for effective executable price diagnostics.

### P1 station-level weather store

- Added thin `src/weather/` contract layer:
  - `station_registry.py`
  - `weather_sources.py`
  - `weather_observations.py`
  - `settlement_truth.py`
- Station specs map cities to settlement station/source/timezone.
- Weather snapshots require `available_at`.
- Replay filters only snapshots visible at `replay_time`, preventing lookahead.
- Official intraday observations can now be converted into an audited daily-high settlement value without using market outcome inference.
- Added `src/weather/official_value_backfill.py` to generate paper-only official value supplements for closed markets. The flow prioritizes the local official observation store and can optionally query Wunderground historical daily highs only for markets whose settlement source is Wunderground.

### P2 raw edge replacement

- Added `src/trading/weather_probability_model.py`.
- Enrichment now emits:
  - `p_model`
  - `p_lcb`
  - `q_effective`
  - `cost`
  - `ev_safe`
- `WeatherMarketSignalConfig` now requires positive `ev_safe` for temperature candidates by default.
- `edge_percent` and `final_score` remain diagnostic/display fields only.

### P2 no-lookahead replay skeleton

- Added:
  - `src/trading/weather_execution_sim.py`
  - `src/trading/weather_replay.py`
- Replay can simulate taker buys from visible orderbook snapshots and output:
  - resolved PnL
  - fill rate
  - missed fills
  - drawdown
  - Brier score
  - log loss
- Tests prove future candidates/orderbooks are skipped.

### P2 market-implied distribution / de-vig

- Added `src/trading/weather_market_implied.py`.
- Mutually-exclusive `eq` / `range` bucket families now get overround/underround diagnostics and de-vig probabilities.
- `le` / `ge` threshold families now get raw CDF, monotonic CDF fit, and monotonic violation diagnostics.
- Enrichment and readonly payloads attach market-implied diagnostics before signal generation.

### P2 strategy layer

- Added `src/trading/weather_strategies/`.
- Candidates are assigned a `strategy_id`, `execution_style`, `why_now`, `risk_caps`, and live-gate eligibility flags.
- `eq_exact` is explicitly `shadow_only`, `strategy_live_eligible=false`, and `counts_for_live_gate=false`.
- Paper fills and maker quote records persist strategy and market-implied fields for downstream evidence grouping.

### P3 readiness ledger / authorization split

- `weather_live_readiness.py` now emits an `evidence_ledger` grouped by `strategy_id`, `city`, `bucket_type`, and `execution_style`.
- Ledger groups are classified as `paper-only`, `needs-evidence`, or `tiny-live-eligible`.
- Global evidence counts now exclude `strategy_live_eligible=false` fills.
- Current-signal availability now prefers `strict_gate_diagnostics.live_eligible_candidate_count` over the raw report `summary.candidate_count`. This prevents `eq_exact_shadow` or other paper-only candidates from inflating live signal availability.
- Legacy paper fills without strategy metadata are normalized at report time from `bucket_label`, `recorded_at`, and `end_date`; old exact-temperature fills now land in `eq_exact_shadow` instead of `unknown`.
- Resolved gap reporting uses the same strategy-aware live-gate filter, so calibration-only exact buckets no longer inflate live settlement audit requirements.
- `live_gate`, `live_permission`, and effective `live_order_path_available` are separated. The current system hard-disables the live order path, so `live_authorization_pct` stays `0` even if evidence and permission were hypothetically satisfied.

### P3 hard-gate readiness summary

- `weather_live_readiness.py` now emits `hard_gate_summary` at the top level and under `evidence`.
- The hard-gate summary makes the legacy `readiness_pct` explicitly diagnostic instead of authoritative:
  - `readiness_pct_is_legacy_diagnostic=true`
  - `evidence_gate_passed`
  - `live_authorization_gate_passed`
  - `overall_state`
  - per-gate `required`, `observed`, and `blockers`
- The current hard gates are:
  - current live-eligible signal availability;
  - forward paper markout quality;
  - resolved PnL audit;
  - strategy evidence ledger with at least one tiny-live-eligible group;
  - orderbook archive / execution diagnostics;
  - no-lookahead strict-gate replay with resolved outcomes, no missed fills, Brier/log-loss, and non-negative resolved PnL;
  - settlement calibration with official truth, probability scores, resolved PnL samples, no mismatches, Brier/log-loss, and non-negative mean resolved PnL;
  - paper-only runtime safety boundary;
  - explicit live authorization.
- Tests now cover the important separation: evidence gates can pass while `live_authorization_gate_passed=false` and `overall_state=paper-only`, because the live order path is hard-disabled.
- `live_gate` is now driven by the hard-gate evidence summary, not by the legacy count-only evidence gate. Having enough paper fills / markouts / resolved audits is no longer sufficient unless replay, execution, and settlement calibration gates also pass.
- `scripts/weather_market_readiness_report.py` accepts optional:
  - `--strict-gate-replay-report`
  - `--settlement-calibration-report`
  - `--orderbook-archive-coverage-report`
  These load existing JSON reports into readiness hard gates without running heavyweight replay/calibration inside the readiness command.
- `--orderbook-archive-coverage-report` accepts either a standalone active orderbook coverage JSON or a full `weather_market_paper_cycle` JSON containing `orderbook_archive_coverage`.
- `scripts/weather_market_readiness_report.py` also accepts `--live-evidence-bundle-report`, which extracts the nested strict-gate replay and settlement calibration reports from one bundle file.

### Live evidence bundle

- Added `src/trading/weather_live_evidence_bundle.py`.
- Added `scripts/weather_live_evidence_bundle_report.py`.
- The bundle is paper-only and chains the evidence path in memory:
  1. strict-gate queue records + archived orderbooks + resolved token outcomes -> strict-gate replay;
  2. strict-gate replay fills -> calibration-ready historical evidence supplements;
  3. closed backfill + official-value supplements + replay-derived historical evidence -> settlement calibration;
  4. nested replay and calibration reports -> readiness hard-gate inputs.
- The bundle reports:
  - `strict_gate_replay_report`
  - `strict_gate_historical_evidence_report`
  - `closed_historical_replay_report`
  - `historical_evidence_report`
  - `official_value_backfill_report`
  - `settlement_calibration_report`
  - `gap_summary`
  - `blockers`
  - `hard_conclusion`
- `gap_summary` reports the current primary blocker, ordered gap list, observed/required counts, and next action for:
  - strict-gate queue coverage;
  - tokenized replay candidates;
  - orderbook archive coverage;
  - replay fills;
  - historical evidence supplements;
  - official truth samples;
  - probability score samples;
  - resolved PnL samples;
  - settlement truth mismatches.
- `gap_summary.unresolved_replay` now breaks unresolved replay fills down by strategy, queue, city, and bucket type, with sample rows. This makes the current blocker actionable: when replay fills exist but all have `payout=null`, the next required evidence is official settlement / token payout attachment, not more generic queue plumbing.
- The bundle now also runs inline official-value supplementation before settlement calibration:
  - explicit `--official-value-supplements` are applied first;
  - local official observation-store values are attempted by default;
  - external official fetches require explicit `--fetch-external-official-values`;
  - `--allow-wunderground-proxy` remains disabled unless explicitly requested.
- `official_value_backfill_report` records `ready_count`, `gap_count`, and `gaps_by_reason`, so the bundle can distinguish "no official truth because no supplement path was supplied" from "no official truth because the local station/date observation store has no points."
- `official_value_backfill_report.official_observation_backfill_plan` deduplicates those gaps by settlement source, station, target date, and unit. This turns market-level gaps into station/date requests that can be fetched or populated once and then reused across all buckets in the same event.
- The bundle now also runs closed historical replay against closed-market snapshots and merges any valid no-lookahead historical evidence into settlement calibration.
  - `strict_gate_historical_evidence_report` tracks current active strict-gate replay evidence.
  - `closed_historical_replay_report` tracks closed-market pre-resolution snapshot evidence.
  - `preresolution_orderbook_replay_report` tracks closed-market evidence from separately archived active orderbooks.
  - `historical_evidence_report` is the merged calibration input.
- `--summary-only` drops replay fills, historical evidence supplement rows, and calibration rows from stdout for review-friendly reports without mutating repository data.
- `--summary-only` also drops row-level official-value supplements and official-value gap samples.
- This removes the previous manual JSONL handoff between replay and calibration while preserving paper-only/no-lookahead behavior.
- `scripts/weather_market_paper_cycle.py` now emits `live_evidence_bundle_hint` in the cycle output and under `effective_profile`.
  - It records the exact paper journal, backfill, strict-gate queue, and orderbook archive directories used by that cycle.
  - It includes a recommended `weather_live_evidence_bundle_report.py --summary-only` command.
  - This is paper-only metadata and cannot submit orders or unlock live trading.
  - Purpose: prevent the next evidence-bundle run from accidentally reading the wrong default journal path after a production-profile paper cycle.

Current local default bundle check, written only to `/tmp`:

```text
hard_conclusion: live_evidence_bundle_needs_more_evidence
primary_blocker: strict_gate_queue_missing
gap_count: 8
queue_record_count: 0
replay_candidate_count: 0
orderbook_snapshot_count: 0
replay_fill_count: 0
historical_evidence_supplement_count: 0
official_truth_sample_count: 0
probability_score_sample_count: 0
resolved_pnl_sample_count: 0
```

Interpretation: the default repository evidence has closed token payouts, but no current strict-gate queue or pre-resolution orderbook archive in the default paper journal path. The next operational evidence step is to run paper collection with strict queue journaling and orderbook archiving before market end.

Latest production-profile goal-cycle bundle check, also written only to `/tmp`:

```text
paper_cycle total_rows: 240
paper_cycle candidate_count: 1
paper_cycle strict queue records: 20
paper_cycle orderbook snapshots: 240
paper_cycle orderbook coverage: 120/120 pre-resolution markets
bundle hard_conclusion: live_evidence_bundle_needs_more_evidence
bundle primary_blocker: resolved_outcome_missing_for_replay_fills
bundle gap_count: 5
queue_record_count: 20
replay_candidate_count: 20
orderbook_snapshot_count: 240
replay_fill_count: 20
missed_fill_count: 0
no_visible_orderbook_count: 0
missing_resolution_count: 20
historical_evidence_supplement_count: 20
official_value_ready_count: 0
official_value_gap_count: 257
official_truth_sample_count: 0
probability_score_sample_count: 0
resolved_pnl_sample_count: 0
official_value_gaps_by_reason:
  missing_observation_points: 219
  unsupported_non_temperature_market: 38
official_observation_backfill_plan:
  request_count: 21
  returned_request_count: 21
  truncated: false
  records_covered_count: 219
  skipped_gap_count: 38
  by_settlement_source:
    metar: 13
    aeroweb: 4
    noaa: 2
    wunderground: 2
```

Unresolved replay distribution from that bundle:

```text
by_strategy: near_lock=13, tail_threshold=7
by_queue: ev_calibration=10, execution_depth_price=10
by_bucket_type: le=11, ge=9
by_city: ankara=6, moscow=6, istanbul=4, london=4
```

Interpretation: the operational paper-only path now has a usable strict queue, archived orderbooks, executable depth fills, and historical-evidence supplements. The next blocker is narrower: these active fills are not resolved yet, and the current closed-market official-value pass found zero ready official values. The local closed sample has 219 temperature records missing station/date observation points and 38 unsupported non-temperature records. The 219 temperature gaps collapse to 21 unique official-observation requests, so the next concrete work is to populate those station/date observations and rerun the same bundle.

The same bundle with explicit external official-value fetching, also written only to `/tmp`, produced:

```text
official_value_ready_count: 71
official_value_gap_count: 186
official_truth_sample_count: 71
official_truth_coverage: 0.276265
probability_score_sample_count: 0
resolved_pnl_sample_count: 0
closed_historical_replay:
  hard_conclusion: closed_historical_replay_no_preresolution_snapshots
  snapshot_row_count: 580
  candidate_count: 0
  gaps_by_reason:
    post_resolution_snapshot: 256
    missing_market_end_time: 1
historical_evidence:
  strict_gate_historical_evidence_supplement_count: 20
  closed_historical_evidence_supplement_count: 0
  preresolution_orderbook_evidence_supplement_count: 0
preresolution_orderbook_replay:
  hard_conclusion: preresolution_orderbook_replay_missing_archive_coverage
  archived_orderbook_count: 240
  candidate_count: 0
  gaps_by_reason:
    missing_archived_orderbook: 256
    missing_market_end_time: 1
remaining_official_observation_backfill_plan:
  request_count: 14
  records_covered_count: 148
  skipped_gap_count: 38
  by_settlement_source:
    metar: 8
    aeroweb: 4
    noaa: 2
```

Interpretation: supported external sources can already turn part of the closed sample into official truth inside the bundle. The latest integrated bundle has 71 official-truth samples, but it still has zero probability-score and resolved-PnL samples because the closed corpus does not yet have no-lookahead historical executable entry evidence. The closed snapshot corpus is currently unusable for calibration: 580 snapshot rows exist, but 256/257 closed markets only have post-resolution snapshots. The `/tmp` active orderbook archive has 240 pre-resolution rows, but those token ids do not yet match the current closed corpus, so archived-orderbook replay also has zero candidates. The next profitability blocker is now historical pre-resolution orderbook/signal evidence.

### Current signal snapshots

- Added `src/trading/weather_current_signal.py`.
- Paper cycle can persist the current signal report as `latest_signal_report.json` plus `manifest.jsonl`; production profile writes this by default and non-production can opt in with `--write-current-signal-report`.
- Readiness CLI now auto-loads `<paper-journal-dir>/current_signal_reports/latest_signal_report.json` when `--signal-report` is not passed.
- A fresh read-only current scan was generated to `/tmp` and used for this report; no repository data cache was added.

### Strict gate diagnostics

- `weather_market_signal.py` now emits `strict_gate_diagnostics`.
- This diagnostic excludes `eq_exact_shadow` / calibration-only rows and focuses on live-eligible strategies.
- Readiness now surfaces the compact strict-gate summary under `current_signal_diagnostics.strict_gate`.
- Strict gate diagnostics now produce paper-only targeted queues:
  - `ev_calibration`
  - `execution_depth_price`
  - `risk_rule_review`
- Queue items are explicitly `paper_only=true` and `counts_for_live_gate=false`.

### Strict gate targeted queue journal

- Added `src/trading/weather_strict_gate_queue.py`.
- Strict-gate rejected rows can now be written to a dedicated paper-only JSONL journal instead of staying only inside the signal report.
- Paper cycle production profile writes this queue by default unless `--no-strict-gate-queue` is passed; non-production can opt in with `--write-strict-gate-queue`.
- Queue records preserve market/token/side, strategy, EV surface, executable price fields, depth/spread/liquidity, non-risk blockers, risk-rule hits, and source snapshot metadata.
- Queue records are `diagnostic_only=true`, `paper_only=true`, `counts_for_live_gate=false`, and `live_gate_excluded=true`.
- Readiness now attaches `strict_gate_queue_summary` under `evidence`, but this does not add readiness points or unlock live trading.
- A helper converts strict queue records with token ids into replay candidates for later no-lookahead/orderbook simulation.

### Strict gate replay / orderbook diagnostics

- Added `src/trading/weather_strict_gate_replay.py`.
- Added `scripts/weather_strict_gate_replay_report.py`.
- Strict-gate queue records can now be replayed against archived orderbook snapshots and resolved audit outcomes.
- Strict replay can also consume closed-market backfill records directly: it reconstructs token-level payouts from `token_id_by_outcome` and `settled_probability_by_outcome`, while preferring explicit resolved audit rows when both sources contain the same token.
- Strict replay now uses the same snapshot supplement path as closed replay seed reporting, so older `closed_markets.jsonl` summaries that only stored the winning token can be repaired from the original read-only payload snapshots without mutating cached data.
- The replay report is paper-only and emits:
  - queue record count
  - tokenized replay candidate count
  - visible orderbook coverage
  - taker depth-fill count and missed-fill count
  - missing resolved outcome count
  - resolved outcome source counts
  - resolved PnL / Brier / log loss when outcomes exist
  - execution-cost diagnostics such as `mean_entry_minus_q_effective_cents`
- Strict replay now also emits `performance_summary`, stratified by:
  - `strategy_id`
  - `queue_name`
  - `bucket_type`
  - `city`
  - `strategy_id|bucket_type`
- Each stratum reports fill count, resolved coverage, missed fill count, win rate, total/mean resolved PnL, mean entry price, mean `q_effective`, mean `ev_safe`, Brier score, and log loss. This makes it possible to identify which strategy/bucket/city slices have positive evidence instead of relying on one global replay number.
- Readiness compacts this strict replay `performance_summary` under `evidence.strict_gate_replay_summary`, so the hard-gate report can show where replay evidence is coming from without loading the full replay fill list.
- `weather_execution_sim.py` now preserves queue, strategy, bucket, city, `q_effective`, and `ev_safe` on simulated fills, so execution results can be grouped by queue and strategy.

### Closed replay seed / settlement truth diagnostics

- Added `src/trading/weather_closed_replay_seed.py`.
- Added `scripts/weather_closed_replay_seed_report.py`.
- Closed backfill can now be converted into token-level, paper-only replay seed rows:
  - `paper_only=true`
  - `counts_for_live_gate=false`
  - `live_gate_excluded=true`
  - `diagnostic_only=true`
- The report distinguishes:
  - no resolved markets
  - no token payouts
  - partial token coverage
  - missing rule hash
  - missing official final value
  - ready seed coverage
- Older backfill records are repaired read-only from `data/trading/weather_backfill/snapshots/*.json` when the snapshot contains complete Yes/No token ids.
- Parsed temperature records can rebuild canonical `rule_hash` / `settlement_spec` from the snapshot-supplemented end time plus the local station registry.
- Exact-bucket winner families can emit `market_inferred_final_value`, but this does not replace `official_final_value`.
- Added optional official-observation supplementation via `--use-observation-store`.
  - It reads the local `OfficialIntradayObservationRepository`.
  - It only fills `official_final_value` when city/date/station/source has stored official intraday observations.
  - Missing coverage is reported as `official_final_value_gap_reason`; no value is inferred from market payouts.
- Added `scripts/weather_official_value_backfill_report.py`.
  - Default mode is diagnostic-only and local-store-only.
  - `--fetch-external-official-values` can produce supplement rows from supported external official sources:
    Wunderground historical data for Wunderground-settled markets and recent AviationWeather METAR for METAR-settled markets.
  - `--supplements-only` emits ready supplements as JSONL.
  - `weather_closed_replay_seed_report.py --official-value-supplements <path>` can consume those supplements without mutating `closed_markets.jsonl`.
  - `weather_strict_gate_replay_report.py --official-value-supplements <path>` now uses the same supplements for token-level resolved outcome truth.
  - Source-level external fetch caches are keyed by station/date/unit but supplement rows preserve each market bucket's own slug/id, avoiding cross-bucket identity contamination.
- Added `src/trading/weather_settlement_truth_audit.py` and `scripts/weather_settlement_truth_audit_report.py`.
  - Computes the expected Yes payout from `official_final_value`, settlement rounding, and bucket type.
  - Compares expected payout against closed Polymarket `settled_probability_by_outcome`.
  - Emits mismatch/gap samples before official values are used for calibration or resolved PnL evidence.
- Current local closed replay seed status:

```text
backfill_record_count: 257
resolved_market_count: 257
parsed_temperature_count: 219
snapshot_supplemented_market_count: 257
complete_token_market_count: 257
replay_seed_token_count: 514
missing_token_map_market_count: 0
missing_payout_market_count: 0
missing_rule_hash_count: 38
temperature_missing_rule_hash_count: 0
missing_official_final_value_count: 257
temperature_missing_official_final_value_count: 219
market_inferred_final_value_count: 163
unsupported_closed_market_count: 38
hard_conclusion: closed_replay_seed_missing_official_final_value
```

Interpretation: token-level payout reconstruction is no longer the bottleneck for historical closed markets, and canonical rule hashes are now rebuilt for parsed temperature markets. The remaining closed-market blocker is official observation truth: `official_final_value` is still missing for all parsed temperature markets, while 163 rows have only market-inferred final values from exact-bucket winners.

Current local closed replay seed status with `--use-observation-store`:

```text
official_observation_supplemented_market_count: 0
official_observation_gap_count: 257
official_observation_gaps_by_reason:
  missing_observation_points: 219
  unsupported_non_temperature_market: 38
hard_conclusion: closed_replay_seed_missing_official_final_value
```

Interpretation: the補全 path works and is tested, but the local official observation store does not yet cover the closed market sample cities/dates. The next work is data coverage, not using market-implied winners as official truth.

Current official value backfill report in local-store-only mode:

```text
input_record_count: 257
supplement_count: 257
ready_count: 0
gap_count: 257
gaps_by_reason:
  missing_observation_points: 219
  unsupported_non_temperature_market: 38
hard_conclusion: official_value_backfill_has_gaps
```

Interpretation: the supplement path is now reproducible and composable, but the local evidence store still has no official final values for the closed temperature sample.

Supplement ingestion smoke:

```text
weather_closed_replay_seed_report.py --official-value-supplements <jsonl>
hard_conclusion: closed_replay_seed_ready
official_value_supplemented_market_count: 1
missing_official_final_value_count: 0
```

Interpretation: once official-value supplement rows exist, closed replay seeds can consume them without mutating raw closed backfill.

External official-value supplement run:

```text
weather_official_value_backfill_report.py --fetch-external-official-values --supplements-only
ready supplements: 71
covered sources:
  aviationweather_metar_recent: 51
  wunderground_historical: 20
covered dates:
  2026-06-25 -> ankara/LTAC 29.0 C, london/EGLC 29.0 C, moscow/UUWW 19.0 C, new york/KLGA 84.0 F
  2026-06-26 -> seoul/RKSI 25.0 C
  2026-04-17 -> official_final_value 19.0 C
  2026-06-26 -> official_final_value 27.0 C
```

Closed replay seed after consuming the external official-value supplements:

```text
official_value_supplemented_market_count: 71
missing_official_final_value_count: 186
temperature_missing_official_final_value_count: 148
hard_conclusion: closed_replay_seed_missing_official_final_value
```

Strict replay resolved-outcome truth after consuming the external official-value supplements:

```text
token_outcome_count: 514
token_outcome_with_official_final_value: 142
```

Interpretation: 71 closed markets are now official-value complete at market level, producing 142 token-level resolved outcomes with official final value. The remaining temperature gap is 148 markets, mostly older METAR dates outside the 72-hour recent window plus AEROWEB/NOAA sources that still need source-specific historical adapters.

Settlement truth audit after consuming the external official-value supplements:

```text
audited_with_official_count: 71
pass_count: 71
mismatch_count: 0
gap_count: 186
by_settlement_source:
  metar: 51
  wunderground: 20
by_bucket_type:
  eq: 53
  le: 7
  ge: 6
  range: 5
hard_conclusion: settlement_truth_audit_has_gaps
```

Interpretation: every market with an official final value currently matches Polymarket's settled Yes/No payout. The blocker is coverage, not contradiction in the supplemented official truth.

Settlement calibration report:

- Added `src/trading/weather_settlement_calibration.py` and `scripts/weather_settlement_calibration_report.py`.
- It converts official-value audit passes into paper-only calibration groups by settlement source, city, station, bucket type, source/bucket, city/bucket, and station/bucket.
- It reports `yes_rate`, official final value ranges, optional Brier/log-loss when historical prediction probabilities exist, and optional resolved PnL when historical entry prices exist.
- It deliberately does not infer historical probability or entry price from settled payouts.
- Added `--historical-evidence-supplements` for JSON/JSONL pre-resolution evidence rows keyed by market or token.
  - Evidence rows must be Yes-side or side-neutral.
  - Evidence rows must expose `available_at` / `generated_at` / `recorded_at`.
  - Evidence rows are rejected if their timestamp is later than `settlement_spec.end_time` / `end_date`, so closed calibration cannot accidentally use post-resolution information.
- `weather_execution_sim.py` now preserves `p_model`, `p_lcb`, `model_probability`, `entry_price`, `available_at`, and `orderbook_snapshot_id` on simulated taker fills. Those replay fills can be passed directly as historical evidence supplements for settlement calibration once closed-market historical replay data exists.
- Added `src/trading/weather_historical_evidence.py`.
  - Converts strict replay fills into calibration-ready historical evidence supplements.
  - Exports only fully filled rows with token id, entry price, timestamp, and prediction probability.
  - Missed/partial fills, missing probability, missing entry price, and missing timestamp are counted as gaps.
  - `p_model` remains the primary calibration probability; `p_lcb` is retained separately for conservative risk review.
- `scripts/weather_strict_gate_replay_report.py` now supports:
  - `--historical-evidence-supplements-only` to print replay fills as JSONL supplements.
  - `--historical-evidence-output <path>` to append those supplements to a JSONL file.

Current settlement calibration with the same external official-value supplements:

```text
record_count: 257
official_truth_sample_count: 71
official_truth_coverage: 0.276265
mismatch_count: 0
gap_count: 186
probability_score_sample_count: 0
resolved_pnl_sample_count: 0
global yes_rate: 0.070423
global mean_official_final_value: 30.676056
global sample_count: 71
hard_conclusion: official_truth_coverage_below_min
blockers:
  official_truth_coverage_below_min
  insufficient_probability_score_samples_0_of_30
  insufficient_resolved_pnl_samples_0_of_10
```

Interpretation: official settlement truth is internally consistent where covered, but it is still not sufficient trading evidence. The supplement interface for historical probabilities and executable prices now exists and is no-lookahead tested, but the current closed-market corpus still needs real historical replay fills before Brier score, log loss, and resolved PnL can become live-gate evidence.

### Closed historical replay inputs

- Added `src/trading/weather_closed_historical_replay.py` and `scripts/weather_closed_historical_replay_report.py`.
- The module tries to build no-lookahead replay candidates and orderbook snapshots from closed-market snapshot archives.
- It only accepts rows where:
  - snapshot timestamp is not later than market `settlement_spec.end_time` / `end_date`;
  - the market was still tradable / accepting orders;
  - the Yes token id is present;
  - a real orderbook ask ladder is present.
- It then reuses the existing taker replay and historical-evidence export path, so pre-resolution closed snapshots can flow into settlement calibration as Brier/log-loss/resolved-PnL evidence.
- It does not use resolved 0/1 prices as entry prices.

Current local closed historical replay status:

```text
closed_record_count: 257
snapshot_row_count: 580
candidate_count: 0
orderbook_snapshot_count: 0
historical_evidence_supplement_count: 0
hard_conclusion: closed_historical_replay_no_preresolution_snapshots
gaps_by_reason:
  post_resolution_snapshot: 256
  missing_market_end_time: 1
```

Interpretation: the closed snapshot archive is useful for payout/token reconstruction, but it was captured after market end, so it cannot prove historical executable entry prices. The system now reports this explicitly instead of silently outputting zero or, worse, using settled prices as historical fills.

### Pre-resolution orderbook archive promotion

- Added `scripts/weather_preresolution_orderbook_replay_report.py`.
- Extended `src/trading/weather_closed_historical_replay.py` with a direct promotion path from active-market orderbook archives to closed-market replay evidence.
- The promotion path joins closed markets to archived orderbook snapshots by Yes token id, then selects the latest snapshot at or before market end time.
- It computes:
  - executable taker entry from archived ask depth;
  - a market-implied probability baseline from the archived bid/ask midpoint;
  - resolved PnL from closed token payout;
  - calibration-ready historical evidence supplements.
- It rejects missing archives, post-resolution archive rows, missing timestamps, missing ask depth, and missing best ask.

Current local pre-resolution archive promotion status:

```text
closed_record_count: 257
archived_orderbook_count: 0
candidate_count: 0
orderbook_snapshot_count: 0
historical_evidence_supplement_count: 0
hard_conclusion: preresolution_orderbook_replay_no_archive
gaps_by_reason:
  missing_archived_orderbook: 256
  missing_market_end_time: 1
```

Interpretation: the code path that will convert active pre-resolution orderbook archives into closed replay evidence now exists and is tested. The remaining blocker is operational evidence collection: the default active orderbook archive is empty, so the system must run the paper cycle before market end often enough to populate `data/trading/polymarket_orderbooks/orderbook_snapshots.jsonl`.

### Active orderbook archive coverage monitor

- Added `src/trading/weather_orderbook_archive_coverage.py`.
- `scripts/weather_market_paper_cycle.py` now emits `orderbook_archive_coverage` on every cycle.
- `weather_live_readiness.py` now includes `orderbook_archive_coverage_summary` under `evidence` and adds non-ready coverage conclusions as diagnostic blockers only.
- The monitor checks whether active Yes-side weather tokens have:
  - future market end time;
  - active/tradable/accepting status;
  - current ask depth and best ask;
  - a pre-resolution archived orderbook row for the same token.
- New orderbook archive rows now preserve `end_time`, `end_date`, `target_date`, `settlement_spec`, `market_bucket`, station/source, rule hash, bucket type, threshold, and unit. Without these fields, archived active markets cannot be cleanly scheduled for closed-backfill refresh after market end.
- It is explicitly `paper_only`, `diagnostic_only`, and `counts_for_live_gate=false`; it cannot unlock live trading.

Interpretation: the system now has a direct monitor for the exact operational blocker found above: active orderbook archive coverage must exist before closed markets can later produce no-lookahead historical replay evidence.

### Closed/archive token-overlap diagnostic

- Extended `src/trading/weather_orderbook_archive_coverage.py` with `build_orderbook_closed_token_coverage_report`.
- The live evidence bundle now includes `orderbook_closed_token_coverage_report`.
- The report compares resolved closed-market Yes token ids with archived active orderbook token ids and separates:
  - closed markets missing an archived pre-resolution orderbook;
  - archived active-market orderbooks that are still pending closed backfill/resolution;
  - pending archived markets that have passed end time and should trigger closed-backfill refresh;
  - legacy archived rows that are missing end-time metadata and cannot be scheduled reliably;
  - true closed/archive token overlap that can produce resolved replay evidence.
- This is explicitly diagnostic-only and cannot unlock live trading.

Latest `/tmp` evidence bundle with external official-value fetch:

```text
closed_yes_token_count: 257
archived_unique_token_count: 120
matched_closed_archived_token_count: 0
unmatched_closed_token_count: 257
unmatched_archived_token_count: 120
pending_closed_backfill_due_token_count: 0
pending_closed_backfill_await_market_end_token_count: 0
pending_closed_backfill_missing_end_time_token_count: 120
closed_backfill_followup_plan.next_action: preserve_end_time_in_future_orderbook_archives
hard_conclusion: orderbook_closed_token_coverage_no_overlap
gaps_by_reason:
  closed_market_missing_archived_orderbook: 257
  archived_orderbook_pending_closed_backfill_missing_end_time: 120
```

Interpretation: this removes an ambiguity in the prior blocker. The `/tmp` active archive is not useless, but it was produced before archive rows preserved end-time metadata, so the system cannot yet schedule those 120 archived tokens for a reliable closed-backfill refresh. Future archive rows will carry settlement/end-time metadata and will be classified as `await_market_end` or `refresh_closed_backfill_due`. Conversely, the current closed corpus has token-level payouts but lacks archived pre-resolution orderbooks. Resolved PnL remains unavailable until token overlap exists between archived pre-resolution books and resolved closed backfill.

Fresh paper-only verification after adding archive metadata:

```text
cycle_output: /tmp/polyweather_newschema_cycle_report.json
bundle_output: /tmp/polyweather_newschema_bundle.json
orderbook_snapshot_count: 240
with_supported_settlement_metadata: 240
eligible_preresolution_yes_tokens: 120
covered_preresolution_yes_tokens: 120
orderbook_archive_coverage: orderbook_archive_coverage_ready
first_supported_archive:
  market_slug: highest-temperature-in-ankara-on-june-27-2026-24corbelow
  end_time: 2026-06-27T12:00:00Z
  settlement_station_code: LTAC
  settlement_source: metar
  bucket_type: le
  threshold: 24.0
```

The refreshed bundle now classifies pending archived tokens correctly:

```text
closed_yes_token_count: 257
archived_unique_token_count: 120
matched_closed_archived_token_count: 0
pending_closed_backfill_due_token_count: 0
pending_closed_backfill_await_market_end_token_count: 120
pending_closed_backfill_missing_end_time_token_count: 0
closed_backfill_followup_plan.next_action: wait_for_archived_markets_to_reach_end_time
gaps_by_reason:
  closed_market_missing_archived_orderbook: 257
  archived_orderbook_waiting_for_market_end: 120
```

Interpretation: the archive schema problem is fixed for new evidence. The active market archive is now usable as a pre-resolution entry record, but it still cannot generate resolved PnL until those markets pass end time and a closed-backfill refresh brings the same token ids into the resolved corpus.

### Archived-orderbook closed-backfill plan

- Added `scripts/weather_orderbook_closed_backfill_plan_report.py`.
- Added `scripts/weather_archived_orderbook_due_refresh_report.py`.
- `scripts/weather_market_closed_backfill.py` now supports exact `--market-slug` arguments and routes them through a targeted closed-backfill runner.
- Added `build_targeted_closed_weather_payload_from_market_slugs` and `run_targeted_closed_weather_backfill_from_market_slugs`.
- The report is paper-only/read-only: it loads closed backfill plus archived orderbooks and emits the same closed/archive token-overlap diagnostic without writing cache data.
- For archived markets whose `end_time <= generated_at` and whose token id is not yet in closed backfill, it emits a targeted `weather_market_closed_backfill.py` command with exact `--market-slug <market_slug>` arguments.
- For archived markets that have not reached `end_time`, the plan now emits `next_await_market_end`, `next_refresh_check_after`, and the number of tokens/queries that will become due at that next checkpoint.
- Bundle `--summary-only` strips long query lists and row samples, while the standalone plan report keeps `market_queries` and `recommended_command` for operational use.
- Unit coverage proves the targeted closed backfill output can feed `preresolution_orderbook_replay` and produce resolved PnL when the archived token id matches the closed token id.
- The due-refresh orchestration script keeps execution dry-run by default. It only writes closed-backfill data when called with both `--execute` and `--confirm PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH`.
- When confirmed and due markets exist, it chains: closed/archive overlap plan -> exact-slug closed backfill -> pre-resolution orderbook replay summary.

Standalone due-plan verification using the new-schema `/tmp` archive:

```text
plan_output: /tmp/polyweather_newschema_backfill_plan_due.json
generated_at: 2026-06-27T13:00:00Z
archived_unique_token_count: 120
pending_closed_backfill_due_token_count: 44
pending_closed_backfill_await_market_end_token_count: 76
pending_closed_backfill_missing_end_time_token_count: 0
market_query_count: 44
next_action: refresh_closed_weather_backfill_for_due_archived_markets
recommended_command:
  PYTHONPATH=src .venv/bin/python scripts/weather_market_closed_backfill.py
  --backfill-dir data/trading/weather_backfill
  --no-city-temperature-queries
  --polymarket-search-limit 5
  --market-slug <44 due market slugs>
```

Interpretation: the system no longer needs broad closed-market search for this evidence bridge. Once archived markets pass their end time, it can generate a precise closed-backfill refresh command for the due market slugs, then rerun pre-resolution orderbook replay to test whether token overlap produces resolved PnL.

Current-time scheduling check on the same new-schema `/tmp` archive:

```text
plan_output: /tmp/polyweather_newschema_backfill_plan_now.json
generated_at: 2026-06-27T09:29:38Z
pending_closed_backfill_due_token_count: 0
pending_closed_backfill_await_market_end_token_count: 120
pending_closed_backfill_missing_end_time_token_count: 0
next_await_market_end: 2026-06-27T12:00:00Z
next_refresh_check_after: 2026-06-27T12:00:00Z
next_await_market_end_token_count: 44
next_await_market_query_count: 44
next_action: wait_for_archived_markets_to_reach_end_time
```

Interpretation: at the actual verification time, none of the newly archived markets were due yet. The first actionable refresh checkpoint is `2026-06-27T12:00:00Z`, when 44 archived token ids should be checked with the targeted closed-backfill command.

Due-refresh orchestration dry-run on the same current-time state:

```text
dryrun_output: /tmp/polyweather_due_refresh_dryrun_now.json
generated_at: 2026-06-27T09:32:54Z
hard_conclusion: archived_orderbook_due_refresh_not_executed
execution.status: dry_run
execution.confirm_required: PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH
execution.due_market_query_count: 0
execution.loaded_market_query_count: 0
pending_closed_backfill_due_token_count: 0
pending_closed_backfill_await_market_end_token_count: 120
pending_closed_backfill_missing_end_time_token_count: 0
next_refresh_check_after: 2026-06-27T12:00:00Z
next_await_market_end_token_count: 44
```

Interpretation: the orchestration path is now testable without mutating evidence. It correctly refuses to execute before markets reach end time, and it still records the next exact checkpoint for the first 44 archived token ids.

### Paper-cycle subjournal isolation

- Production profile no longer routes automatically enabled paper-only sub-journals to global `data/trading/weather_*_paper` defaults when `--paper-journal-dir` is overridden.
- Unless a sub-journal path is explicitly supplied, these now resolve under `<paper-journal-dir>/subjournals/`:
  - quarantine
  - targeted shadow
  - current-signal taker
  - eq shadow
  - maker focus
  - temperature execution
  - temperature taker
- This keeps `/tmp` verification runs reproducible and prevents diagnostic paper artifacts from mixing with the repository's default evidence cache.

### Production scan coverage fix

- `scripts/weather_market_paper_cycle.py` now defaults `--polymarket-active-scan-limit` to `500`, matching the underlying read-only Polymarket client instead of scanning only one active event.
- The effective value is emitted as `effective_profile.polymarket_active_scan_limit`.
- This does not relax any signal, evidence, readiness, or live-order gate. It only prevents false `no_current_weather_signal` diagnoses caused by scanning too shallowly.
- Unit coverage asserts that `--production-profile` passes `active_scan_limit=500` into `build_polymarket_weather_payload`.

Fresh production paper-cycle verification after the scan-depth fix:

```text
cycle_output: /tmp/polyweather_active500_cycle_v2_report.json
generated_at: 2026-06-27T09:43:49Z
effective_profile.polymarket_active_scan_limit: 500
source_diagnostics.raw_active_events: 500
source_diagnostics.weather_events: 19
source_diagnostics.markets_kept: 120
source_diagnostics.rows: 240
orderbook_archive.snapshot_count: 240
orderbook_archive_coverage.hard_conclusion: orderbook_archive_coverage_ready
signal_summary.candidate_count: 1
signal_summary.reject_count: 239
live_readiness_progress.readiness_pct: 25.83
live_readiness_progress.live_gate: false
live_readiness_progress.hard_conclusion: 只能继续 paper
```

The current strict candidate is a `tail_threshold` paper-only probe:

```text
market_slug: highest-temperature-in-moscow-on-june-29-2026-23corhigher
side: no
bucket_type: ge
entry_price: 0.56
p_lcb: 0.646677
q_effective: 0.56
cost: 0.005
ev_safe: 0.081677
station: UUWW
settlement_source: metar
end_time: 2026-06-29T12:00:00Z
latest_markout: -2.0 cents
```

Interpretation: the scan-depth fix removed the false `no_current_weather_signal` state, but it did not make the strategy live-ready. The first forward markout is still negative, resolved PnL is unavailable until settlement, and no-lookahead replay / settlement calibration gates remain missing.

Follow-up markout after the fill crossed the 5-minute horizon:

```text
markout_output: /tmp/polyweather_active500_cycle_v2/paper/markouts.jsonl
recorded_at: 2026-06-27T09:49:21Z
markout_horizon: 5-15m
markout_age_seconds: 332
current_bid: 0.54
current_ask: 0.56
markout_cents: -2.0
strategy_id: tail_threshold
settlement_station_code: UUWW
end_time: 2026-06-29T12:00:00Z
```

Readiness rerun with explicit orderbook coverage input:

```text
readiness_output: /tmp/polyweather_active500_readiness_forward_path.json
readiness_pct: 25.83
live_gate: false
execution_orderbook_diagnostics: passed
latest_only_gate_marked_count: 1
all_horizon_path_selected_markout_count: 3
all_horizon_path_mean_markout_cents: -2.0
by_strategy_and_horizon:
  tail_threshold / 0-5m: count=2, mean=-2.0, win_rate=0.0
  tail_threshold / 5-15m: count=1, mean=-2.0, win_rate=0.0
by_station_and_horizon:
  UUWW / 0-5m: count=2, mean=-2.0, win_rate=0.0
  UUWW / 5-15m: count=1, mean=-2.0, win_rate=0.0
failed_gates:
  forward_paper_markout
  resolved_pnl_audit
  strategy_evidence_ledger
  no_lookahead_replay
  settlement_calibration
  live_authorization
```

Interpretation: independent readiness reporting no longer loses the orderbook coverage evidence. It also now exposes the full forward-markout path by horizon/strategy/station while keeping the hard gate latest-only by fill, so repeated marks from one fill cannot inflate readiness. The remaining blockers are now the real blockers: negative forward markout, missing resolution PnL, missing replay, missing calibration, and live authorization intentionally disabled.

Current archived-orderbook due-refresh check:

```text
due_refresh_output: /tmp/polyweather_active500_due_refresh_now.json
generated_at: 2026-06-27T09:50:52Z
execution.status: dry_run
pending_closed_backfill_due_token_count: 0
pending_closed_backfill_await_market_end_token_count: 120
pending_closed_backfill_missing_end_time_token_count: 0
next_refresh_check_after: 2026-06-27T12:00:00Z
next_await_market_end_token_count: 44
```

### Settlement metadata propagation

- `weather_market_signal.py` now treats supported temperature settlement specs as incomplete if any required strict-gate field is missing: station, source, target date, timezone, metric, unit, bucket type, threshold, rounding, rule hash, or end time.
- Signal rows now copy canonical settlement fields from `settlement_spec` to top-level fields: `target_date`, `end_time`, station, source, timezone, metric, unit, rule hash, and rule text.
- `weather_paper_journal.py` now persists those fields and the full `settlement_spec` into paper fills and markout records.
- `weather_resolved_audit.py` now preserves entry city, bucket, strategy, EV inputs, settlement fields, and full settlement spec in audit records, even before resolution.
- Runtime verification on `/tmp/polyweather_active500_cycle_v2/paper` confirms the Moscow fill, resolved audit, and refreshed markout all retain `end_time=2026-06-29T12:00:00Z`, station `UUWW`, source `metar`, and rule hash `676bd17d8c752b7ca444`.

## Verification

Full test suite:

```text
PYTHONPATH=src .venv/bin/python -m pytest -q
923 passed, 26 warnings
```

Latest fresh production paper-cycle readiness diagnostic:

```text
readiness_pct: 25.83
distance_to_live_pct: 74.17
live_gate: false
live_order_path_available: false
live_order_path_hard_disabled: true
live_authorization_pct: 0
signal_source: strict_current_signal_report
hard_conclusion: 只能继续 paper
```

Readiness fell from `38.33` to `30.50` because the report now correctly excludes 13 legacy exact-temperature fills from live evidence. Those rows remain useful for calibration, but they are no longer counted toward live readiness.

After the active-scan fix, readiness is `25.83` rather than `10.50`: the missing-current-signal blocker is gone and the fresh scan has `candidate_count=1`, so signal availability contributes points. This is still diagnostic-only because hard gates fail.

Current signal scan summary:

```text
total_rows: 240
candidate_count: 1
watch_count: 0
quarantine_count: 8
reject_count: 239
top live blockers: forward markout / resolved PnL / replay / calibration
```

Strict gate summary:

```text
paper_only_row_count: 198
live_eligible_row_count: 42
live_eligible_candidate_count: 0
live_eligible_reject_count: 42
by_strategy:
  tail_threshold: 26 rejected
  near_lock: 16 rejected
top live-eligible blockers:
  edge: 76 hits
  price: 40 hits
  depth: 32 hits
  spread: 14 hits
  liquidity: 13 hits
```

Strict-gate paper queues:

```text
ev_calibration: 14 rows
execution_depth_price: 14 rows
risk_rule_review: 13 rows
```

The latest `/tmp` current signal was also materialized as a strict-gate queue journal for verification:

```text
queue_dir: /tmp/polyweather_strict_gate_queues
record_count: 30
ev_calibration: 10 records
execution_depth_price: 10 records
risk_rule_review: 10 records
counts_for_live_gate: false
```

A fresh read-only verification cycle was then run entirely under `/tmp` using current code. It produced a new strict queue and orderbook archive:

```text
cycle total_rows: 240
strict queue records: 30
orderbook snapshots: 240
replay candidates with token_id: 30
replay fills: 30
no visible orderbook: 0
missed fills: 2
missing resolved outcomes: 30
hard_conclusion: strict_gate_replay_execution_depth_insufficient
mean_entry_minus_q_effective_cents: 0.0
mean_ev_after_depth_cost_cents: -0.358075
resolved_outcome_count: 514
resolved_outcome_source_counts: closed_backfill=514
```

Interpretation: the strict-gate queue can now be replayed against archived books and can see historical closed-market token outcomes. For the active verification batch, the markets are not settled yet, so outcome PnL is still unavailable. Execution is also not clean: 2/30 one-share probes missed fill and the mean EV after depth cost is negative.

A later goal-cycle run using the bundle hint confirmed the cleaner operational case: 20/20 replay candidates filled against archived orderbooks with zero missed fills and zero invisible books, but all 20 remained unresolved. This moved the immediate blocker from `strict_gate_queue_missing` / `orderbook_archive_missing` to `resolved_outcome_missing_for_replay_fills` plus zero official-truth calibration samples.

That bundle now also runs inline official-value supplementation. On the current local closed backfill it found:

```text
input_record_count: 257
ready_count: 0
gap_count: 257
missing_observation_points: 219
unsupported_non_temperature_market: 38
fetch_external: false
official_observation_backfill_plan.request_count: 21
official_observation_backfill_plan.records_covered_count: 219
```

Interpretation: local token payouts exist, but official station/date observations have not been populated for the closed temperature sample. The next evidence step is no longer "wire official supplements into the bundle"; it is to populate or fetch the 21 unique official observations, then rerun the same bundle.

With `--fetch-external-official-values`, the same bundle now reaches:

```text
ready_count: 71
gap_count: 186
official_truth_sample_count: 71
official_truth_coverage: 0.276265
probability_score_sample_count: 0
resolved_pnl_sample_count: 0
remaining_backfill_plan.request_count: 14
remaining_backfill_plan.records_covered_count: 148
closed_historical_replay.hard_conclusion: closed_historical_replay_no_preresolution_snapshots
closed_historical_replay.candidate_count: 0
closed_historical_replay.gaps_by_reason: post_resolution_snapshot=256, missing_market_end_time=1
```

Interpretation: official-value coverage is no longer zero, but live readiness still does not improve materially because profitability evidence requires historical no-lookahead probabilities and executable entry prices, not just final settlement truth. The closed corpus currently cannot provide that evidence because its available snapshots are post-resolution.

These queues overlap by design: the current live-eligible rows are not just missing one small condition; most simultaneously need probability/EV calibration, executable-price validation, and risk-rule review.

Current hard blockers:

- `no_current_weather_signal`
- `insufficient_paper_fills_6_of_30`
- `insufficient_markouts_3_of_30`
- `insufficient_resolved_audits_0_of_10`
- `resolved_win_rate_missing`
- `resolved_total_pnl_missing`
- `strict_gate_queue_missing` on default repository paths, but not on the latest `/tmp` production-profile cycle
- `orderbook_archive_missing` on default repository paths, but not on the latest `/tmp` production-profile cycle
- `resolved_outcome_missing_for_replay_fills`
- `official_value_backfill_gaps`
- `closed_historical_replay_evidence_missing`
- `preresolution_orderbook_replay_evidence_missing`
- diagnostic: closed/archive token overlap is still `0/257` closed tokens and `0/120` archived tokens; the fresh archive schema now preserves end-time metadata, so the current new-schema archived tokens are correctly classified as `await_market_end` before closed-backfill refresh can produce resolved replay evidence
- `official_truth_samples_insufficient`
- `probability_score_samples_insufficient`
- `resolved_pnl_samples_insufficient`
- `strict_gate_replay_report_missing` or non-ready strict replay when no replay JSON is supplied to readiness
- `settlement_calibration_report_missing` or non-ready settlement calibration when no calibration JSON is supplied to readiness
- `resolved_audit_waiting_for_settlement`
- `live_permission_false`
- `live_order_path_disabled`

Evidence ledger:

```text
group_count: 5
tiny-live-eligible: 0
needs-evidence: 2
paper-only: 3
```

## Remaining Work

- Implement full station-calibrated probability distributions instead of the current conservative haircut-based `p_lcb`.
- Expand replay to use historical forecasts, official observations, orderbook archives, and closed markets together.
- Replace the current score summary with stricter hard-gate reporting once enough strategy-level evidence exists.
- Use closed-market backfill / resolved audit to attach actual outcomes to strict-gate replay batches, then evaluate queue-level resolved PnL, Brier score, and calibration.
- Continue expanding official observation coverage for closed temperature markets, especially older METAR dates and AEROWEB/NOAA sources not covered by the current external adapters.
- Collect or reconstruct pre-resolution closed-market snapshots/orderbooks; the current closed snapshot cache is post-resolution, so it cannot produce no-lookahead entry evidence.
- Feed valid closed historical evidence into settlement calibration until Brier score, log loss, and resolved PnL are populated by real pre-resolution probabilities and executable entry prices.
- Keep maker inferred fills diagnostic-only until quote lifecycle evidence exists.
- Build a unified `EvidenceStratifier` to merge quarantine, targeted shadow, quality surface, and maker focus diagnostics.

## Hard Conclusion

The system is safer and more evidence-shaped than before, but it is still not live-ready. The next highest priority is collecting and replaying real orderbook + resolved weather evidence until taker/maker EV is positive after costs.
