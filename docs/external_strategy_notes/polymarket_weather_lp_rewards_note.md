# Polymarket Weather LP Reward External Strategy Note

Source type: user_supplied_external_note

This note records a user-supplied external strategy idea for Polymarket weather LP rewards. It is not accepted as proven alpha. It is converted into paper-only, testable hypotheses.

## Original Strategy Points

- Each city is unique. Do not trade Beijing, Hong Kong, London, Moscow, Ankara, Istanbul, or other cities with one shared template. Stable cities may not need wide range coverage; volatile cities may require wider ranges.
- Avoid expensive baskets. If the total basket entry price is close to full payout, for example 98%, skip it because the risk/reward is poor and may lose over time.
- Check what smart traders or large holders are buying. Top holder lists may reveal weather-market participants, but this is only a supporting signal.
- Weather LP reward playbook:
  - Screen for markets with the highest LP rewards.
  - Screen for low-price outcomes.
  - Quote high-temperature buckets at night and low-temperature buckets during the day to reduce time-of-day weather risk.
  - When rewards are high, consider dual-leg quoting.
  - When prices are low, consider single-leg quoting with strict risk control.
  - LP reward windows may appear around minute 40-51 of the hour; cancel near the hour boundary.
  - Build city familiarity and peak-time judgment to reduce risk exposure.
  - Beijing time 05:00-08:00 may be a better reward window.
  - Small accounts may focus on cheap single-leg strategies.
  - The edge target is LP reward plus controlled price risk, not blind weather prediction.

## Paper-Only Hypotheses

- LP reward metadata can be observed and timestamped without assuming the reward exists.
- City-specific volatility and intraday peak profiles can reduce bad basket construction.
- Expensive baskets near full payout should be rejected even if a reward signal exists.
- Smart holder data is supporting context only; it cannot override risk filters.
- Estimated rewards must be tracked separately from price markout and never treated as guaranteed PnL.

All experiments from this note must remain `paper_only=true`, `counts_for_live_gate=false`, and `live_order_path=false`.
