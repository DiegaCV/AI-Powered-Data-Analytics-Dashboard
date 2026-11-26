# Methodology

## Calculation chain

`snapshots → hourly → brand_hour → brand_status → cluster_kpi → action_points`

1. **Snapshots:** every checkpoint row from both channels in one shape. A row is used only if it has a brand, a capture time and a numeric GMV, and its date matches a peak-event date in the targets table.
2. **Hourly:** per event, brand, channel and hour, the latest checkpoint of every session so far that day, added together.
3. **Brand-hour:** per brand and hour for the selected event and channel: totals, pace, forecasts, variance, per-hour changes and rates.
4. **Brand status:** the brand-hour row at the selected hour, plus conversion on the most recent earlier event.
5. **Portfolio KPIs:** all brands added together at the selected hour.
6. **Interventions:** the rules below, evaluated for every hour.

## Rules for the time axis

- Hour = the checkpoint slot (`01.00` is 1 … `23.59` is 24).
- **Midnight rule:** a `00.00` checkpoint of a session that started the previous day counts as that day's closing value (hour 24, `23.59`). Other `00.00` checkpoints get no hour but still update the session's latest value.
- **Missing hour:** no checkpoint but later ones exist means "missing" (last value carried forward). A session that ended, or no later data, means "not live".

## Metrics

| Metric | Definition |
|---|---|
| Target | Sum of the brand's target GMV across the selected channels |
| Plan-curve share | Expected cumulative share of the day's GMV at hour h |
| Pace | GMV ÷ (target × plan-curve share) |
| Forecast at close, curve | GMV ÷ plan-curve share |
| Forecast at close, linear | GMV ÷ h × 24 |
| Variance to target | max(0, target − GMV) |
| Required run-rate / hr | Variance ÷ (24 − h) |
| Current run-rate / hr | (GMV at h − GMV at h−3) ÷ 3; GMV ÷ h for the first three hours |
| Conversion | Orders ÷ views, over sessions that report both |
| Average order value | GMV ÷ orders, same sessions |
| Comments per 1,000 views | Comments ÷ views × 1,000 |

## Status

| Status | Condition |
|---|---|
| On track | Pace ≥ 100% |
| At risk | 90% ≤ pace < 100% |
| Behind | Pace < 90% |
| No data | Pace not computable |

## Escalation rules (every hour; LOW = 0.8, minimum 50 views)

| Group | Rule |
|---|---|
| Behind plan or at risk | Status behind or at risk, ranked by variance |
| Funnel flag | Hourly conversion < 80% of the brand's day-so-far conversion (h ≥ 2) |
| Funnel flag | Day conversion < 80% of the all-brand average |
| Funnel flag | Hourly views < 80% of the brand's average views per hour so far (h ≥ 3, live) |
| Funnel flag | Hourly AOV < 80% of day AOV (h ≥ 2, at least 5 orders in the hour) |
| Funnel flag | Comments per 1,000 views < 80% of the all-brand average |
| Paid media | Paid boost switched off on Channel B |
| Paid media | Channel B paid-media ROI < 80% of its value three hours earlier |
| Data | Missing checkpoint for a live channel |
| On track | Status on track, ranked by pace |

Design choice: a metric is compared with the brand's own day average and with the all-brand average, not with other brands on the same channel, because baselines differ.

## Data contract (files in `data/`)

| Table | Columns (key ones) |
|---|---|
| `Channel_A.csv` | LivestreamID, Brand, Captured At, Slot, GMV, Orders, Livestream Start, Buyer, Quantity Sold, Views, Likes, Comment, Shares, Flags |
| `Channel_B.csv` | LivestreamID, Brand, Captured At, Slot, GMV, Attributed Orders, Attributed Items Sold, Livestream Start, Views, Comments, Ads Cost, Paid Boost ROI, GMV Type, Flags |
| `Master_Targets.csv` | Campaign, Campaign Date, Brand, Platform, Target GMV (EUR), Source |
| `Pacing_Curve.csv` | Slot, Expected Cumulative Share, Based On |
| `Brand_Benchmarks.csv` | Campaign, Campaign Date, Brand, Platform, GMV, Orders, Views, Conversion (Orders / Views), … |

A brand appears for an event only if it has a target row. Missing values stay blank and appear under data quality.

## Limitations

See the README. The plan curve is a single reference-day average, and extraction accuracy sets the ceiling on data quality.

## Currency

Monetary columns (GMV, targets, average order value, ad cost) are in euros, converted once from the source currency at a fixed rate of 1 EUR = 20,140 IDR. Counts, percentages and ratios are unaffected.
