# Forward-looking base rates

> **This is not a forecast and not investment advice.** The tables below show what happened historically when a series was in a similar state. They are empirical base rates from a short sample, not predictions.

**As of:** 2026-11-09  
**Window:** 2025-11-11 to 2026-11-09

## Method

For each series and horizon (3 and 6 months), the analysis computes every historical forward return in the available sample and summarises its distribution. It then repeats the calculation conditional on the current state:

- **Trend:** above or below the 200-day moving average.
- **Drawdown:** current distance from the 252-day high, bucketed.
- **Volatility:** current 20-day annualised volatility versus its own median.

Overlapping forward windows are not independent, so the effective sample is smaller than the raw `n`. Rows with fewer than 15 observations are omitted from the conditional tables. A one-year window is a small sample; treat the numbers as context, not precision.

## Current regime

| Series | Trend | Drawdown | Vol regime | 3M momentum | 6M momentum | 1Y return |
| :--- | :--- | ---: | :--- | ---: | ---: | ---: |
| DAX | Uptrend | -1.1% | High vol | -0.2% | +9.7% | +9.6% |
| MDAX | Downtrend | -6.8% | Normal vol | -3.9% | -0.9% | +6.6% |
| SDAX | Mixed | -6.3% | Normal vol | -2.8% | -2.1% | +12.5% |
| Euro Stoxx 50 | Uptrend | -4.0% | High vol | -3.7% | +7.9% | +11.1% |
| TecDAX | Uptrend | -4.4% | Low vol | -0.5% | +5.0% | +16.8% |
| Communication Services | Downtrend | -23.3% | Normal vol | -7.1% | -5.5% | -2.0% |
| Consumer Discretionary | Mixed | -11.7% | High vol | +2.1% | +1.7% | -6.1% |
| Consumer Staples | Uptrend | -11.8% | Normal vol | +7.9% | +25.5% | +3.1% |
| Financials | Uptrend | -2.3% | High vol | +7.5% | +21.3% | +20.0% |
| Health Care | Uptrend | -1.1% | Low vol | +11.8% | +36.0% | +27.9% |
| Industrials | Uptrend | -6.7% | High vol | -6.0% | +7.4% | +6.8% |
| Information Technology | Uptrend | -12.0% | Normal vol | -2.2% | +9.3% | +27.7% |
| Materials | Uptrend | -1.6% | High vol | +7.7% | +13.0% | +17.1% |
| Real Estate | Downtrend | -39.9% | Low vol | -17.8% | -20.4% | -31.4% |
| Utilities | Mixed | -2.4% | Normal vol | +3.1% | +7.1% | +28.2% |

## Unconditional 3M and 6M base rates

The columns show the median, 10th percentile, 90th percentile and the share of positive outcomes across all historical windows in the sample.

| Series | 3M median | 3M p10 | 3M p90 | 3M P(+) | 6M median | 6M p10 | 6M p90 | 6M P(+) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DAX | +2.4% | -2.3% | +11.3% | 75% | +4.3% | -0.9% | +17.6% | 83% |
| MDAX | +2.4% | -5.3% | +10.9% | 62% | +3.5% | -3.1% | +17.3% | 77% |
| SDAX | +3.0% | -3.3% | +13.4% | 69% | +5.6% | -2.1% | +24.2% | 82% |
| Euro Stoxx 50 | +3.4% | -2.7% | +10.8% | 67% | +6.9% | +0.8% | +12.5% | 92% |
| TecDAX | +1.0% | -5.7% | +11.4% | 57% | +3.6% | -6.5% | +14.0% | 59% |
| Communication Services | -3.3% | -12.6% | +16.6% | 36% | -6.6% | -15.4% | +8.0% | 35% |
| Consumer Discretionary | -2.1% | -8.7% | +5.4% | 37% | -4.0% | -10.1% | +4.0% | 25% |
| Consumer Staples | -4.5% | -14.9% | +11.0% | 38% | -10.3% | -17.3% | +8.0% | 17% |
| Financials | +4.3% | -2.6% | +18.2% | 84% | +7.0% | -1.3% | +28.7% | 83% |
| Health Care | +2.9% | -8.2% | +12.7% | 69% | +2.6% | -4.7% | +12.9% | 69% |
| Industrials | +4.8% | -2.5% | +19.0% | 82% | +8.2% | +1.7% | +35.3% | 96% |
| Information Technology | +1.4% | -10.9% | +27.2% | 54% | +6.0% | -9.7% | +29.8% | 58% |
| Materials | +1.2% | -4.9% | +9.7% | 58% | +2.3% | -5.0% | +13.2% | 65% |
| Real Estate | -6.7% | -19.1% | +2.7% | 17% | -12.0% | -26.0% | +1.4% | 14% |
| Utilities | +7.3% | -2.5% | +22.3% | 80% | +18.8% | +0.7% | +35.2% | 90% |

## Conditional on the current trend

These rows use only historical observations with the same trend state (above/below the 200-day SMA) as today.

| Series | Current trend | 3M n | 3M median | 3M p10 | 3M p90 | 3M P(+) | 6M n | 6M median | 6M p10 | 6M p90 | 6M P(+) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DAX | Above 200d SMA | 202 | +1.5% | -3.6% | +5.0% | 64% | 139 | +2.2% | -2.3% | +5.6% | 67% |
| MDAX | Below 200d SMA | 56 | +8.9% | +0.1% | +13.9% | 89% | 56 | +9.4% | +1.1% | +13.3% | 96% |
| SDAX | Below 200d SMA | 40 | +10.2% | +5.6% | +13.6% | 100% | 40 | +11.2% | +9.0% | +18.2% | 100% |
| Euro Stoxx 50 | Above 200d SMA | 235 | +4.3% | -2.8% | +8.8% | 67% | 172 | +6.1% | +2.9% | +9.8% | 99% |
| TecDAX | Above 200d SMA | 114 | +2.2% | -4.6% | +7.8% | 65% | 52 | +3.3% | -3.8% | +9.7% | 75% |
| Communication Services | Below 200d SMA | 191 | +0.3% | -9.2% | +19.5% | 52% | 132 | +0.1% | -8.9% | +9.4% | 52% |
| Consumer Discretionary | Above 200d SMA | 34 | -10.4% | -14.5% | -8.2% | 3% | 33 | -10.7% | -13.1% | -7.6% | 0% |
| Consumer Staples | Above 200d SMA | 37 | -23.4% | -27.1% | +4.9% | 35% | 24 | -15.1% | -17.6% | -8.5% | 0% |
| Financials | Above 200d SMA | 196 | +2.6% | -3.0% | +8.5% | 80% | 146 | +4.3% | -1.7% | +11.1% | 75% |
| Health Care | Above 200d SMA | 154 | +2.1% | -15.6% | +7.0% | 58% | 119 | -2.2% | -9.9% | +2.2% | 29% |
| Industrials | Above 200d SMA | 227 | +2.1% | -4.2% | +9.5% | 67% | 170 | +4.0% | +0.1% | +7.8% | 91% |
| Information Technology | Above 200d SMA | 98 | +2.4% | -10.3% | +12.2% | 57% | 35 | +11.7% | +5.4% | +21.6% | 94% |
| Materials | Above 200d SMA | 145 | -0.1% | -3.3% | +3.7% | 47% | 83 | +0.3% | -4.5% | +3.7% | 54% |
| Real Estate | Below 200d SMA | 233 | -8.4% | -21.2% | +0.8% | 12% | 170 | -16.5% | -27.6% | -10.2% | 2% |
| Utilities | Above 200d SMA | 242 | +4.1% | -3.4% | +21.4% | 71% | 179 | +16.5% | -3.5% | +36.6% | 79% |

## Scenario ranges

Bear, base and bull cases are the 10th percentile, median and 90th percentile of the historical forward-return distribution. They are not targets.

| Series | 3M bear | 3M base | 3M bull | 6M bear | 6M base | 6M bull |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| DAX | -2.3% | +2.4% | +11.3% | -0.9% | +4.3% | +17.6% |
| Utilities | -2.5% | +7.3% | +22.3% | +0.7% | +18.8% | +35.2% |
| Real Estate | -19.1% | -6.7% | +2.7% | -26.0% | -12.0% | +1.4% |

## What the facts suggest

- **Trend:** 6 of 10 sectors were in an uptrend at the end of the window; the DAX itself was in an **uptrend**.
- **Breadth:** 75% of DAX 40 constituents were above their 50-day SMA and 75% above their 200-day SMA.
- **Momentum:** the strongest 3-month sector momentum was **Health Care** (+11.8%); the weakest was **Real Estate** (-17.8%).
- **Conditional base rates:** 11 of 15 index/sector series had a positive median 3-month return when conditioned on their current trend state.
- **Rates:** the 10-year Bund yield was **3.27%**, **+61 bps** over 12 months. Higher yields raise the discount rate for long-duration equities and can compress valuation multiples.

## What would change the view

These are factual monitoring triggers, not predictions:

- The DAX closing below its 200-day SMA (24,932.29).
- DAX 40 breadth above the 50-day SMA falling below 40% (latest: 75%).
- The 10-year Bund yield rising more than 50 bps above its current level (3.77%).
- The strongest sector's 3-month momentum turning negative.
- A sharp rise in the average pairwise sector correlation, which would indicate that diversification is breaking down.

## Limitations

- The sample covers roughly one to two years of daily data; regime-specific samples are small.
- Overlapping forward windows are autocorrelated, so `n` overstates independent information.
- Price returns exclude dividends, buybacks and corporate actions beyond the adjusted series.
- NACE and sector labels are static; index composition and classifications can change.
- The source is an unofficial CNBC endpoint with delayed quotes.
