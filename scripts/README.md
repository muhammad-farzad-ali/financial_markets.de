# Scripts and data reference

This file documents every script, the shell wrapper, the Makefile targets and
the generated data schema.

## Pipeline overview

```
config/*.tsv
    |
    v
scripts/collect.py  -->  data/german_markets.tsv
    |                    data/dax_constituents.tsv
    |                    data/collection_manifest.json
    v
scripts/analyze.py  -->  data/metrics.tsv
    |                    data/correlations.tsv
    |                    data/monthly_returns.tsv
    |                    data/forward_base_rates.tsv
    |                    data/breadth.tsv
    |                    data/sector_breadth.tsv
    |                    data/analysis_manifest.json
    |                    reports/charts/*.svg
    v
scripts/report.py   -->  reports/*.md

scripts/fundamentals.py collect  -->  data/fundamentals.tsv
    |                                data/fundamentals_quarterly.tsv
    |                                data/forecasts.tsv
    |                                data/fundamentals_manifest.json
    v
scripts/fundamentals.py analyze  -->  data/fundamentals_metrics.tsv
    |                                data/fundamentals_scores.tsv
    |                                data/fundamentals_scenarios.tsv
    |                                data/fundamentals_analysis_manifest.json
    v
scripts/fundamentals.py report   -->  reports/fundamentals.md
                                     reports/charts/fundamentals_*.svg

scripts/web.py                  -->  web/index.html
```

`scripts/run_all.py` runs the price pipeline and then the fundamentals
pipeline. `scripts/run.sh` is a thin, documented shell wrapper around it.

## `scripts/lib.py`

Shared standard-library helpers. It is imported by the other scripts and has no
command-line interface.

- **HTTP:** `http_get()` throttles to one request per second
  (`MIN_REQUEST_INTERVAL`), retries HTTP 429/500/502/503/504 with exponential
  backoff (`MAX_RETRIES = 5`, `BACKOFF_BASE = 2.0`) and honours `Retry-After`.
- **Cache:** `fetch_json()` caches responses in `data/raw/` for
  `CACHE_TTL_HOURS = 6.0`. Pass `refresh=True` to bypass the cache.
- **Files:** `read_tsv()`, `write_tsv()`, `read_json()`, `write_json()`.
- **Statistics:** `mean()`, `stdev()`, `percentile()`, `skewness()`,
  `excess_kurtosis()`.
- **Formatting:** `fmt_pct()`, `fmt_num()`, `fmt_int()`, `md_table()`.
- **Paths:** `ROOT`, `CONFIG_DIR`, `DATA_DIR`, `RAW_DIR`, `REPORTS_DIR`,
  `CHARTS_DIR`.

## `scripts/collect.py`

Collects the configured series and writes the main TSV plus the composition
and collection manifest.

```bash
python3 scripts/collect.py                 # use the cache when fresh
python3 scripts/collect.py --refresh       # ignore the cache
python3 scripts/collect.py --only DAX,SAP.DE
```

Options:

| Option | Meaning |
|---|---|
| `--refresh` | Ignore the on-disk cache and download fresh responses. |
| `--only IDS` | Comma-separated `series_id` values (for example `DAX,SAP.DE`). |

What it does:

1. Loads the DAX 40 composition from Wikipedia (cached), falling back to the
   last successful composition file if the page is unavailable.
2. Loads `config/series.tsv` (indices, FX, rates) and
   `config/nace_map.tsv` (company classification).
3. Resolves CNBC symbols. Some German tickers work with a dot (`SAP.DE`),
   others only with a hyphen (`BMW-DE`). The collector tries the variants,
   prefers the one with the longest history and treats a short dot history
   (under 200 bars) as a hint to try the hyphen variant.
4. Parses daily bars, removes duplicate dates, clamps OHLC inconsistencies and
   counts violations only for equities and indices (FX/rate high/low
   conventions differ).
5. Appends the current quote when it is newer than the last daily bar.
6. Writes `data/german_markets.tsv`, `data/dax_constituents.tsv` and
   `data/collection_manifest.json`.

Cache files are named
`<series>_<symbol>_<sha1-prefix>_<chart|quote>.json`; the hash prevents
collisions between dot and hyphen variants.

## `scripts/analyze.py`

Reads the collected TSV and writes metrics, correlations, monthly returns,
forward base rates, breadth and SVG charts. It does not use the network.

```bash
python3 scripts/analyze.py
```

Key definitions:

- **Analysis window:** the last 252 trading sessions in the dataset.
- **Returns:** simple price returns over 1 day, 1 week, 1/3/6 months, YTD and
  1 year (252 sessions).
- **Volatility:** standard deviation of daily returns times √252.
- **Sharpe:** `(mean daily return × 252 − 2%) / annualised volatility`.
- **Maximum drawdown:** largest close-to-running-peak decline in the window.
- **VaR 95 / CVaR 95:** 5th percentile of daily returns and the mean of returns
  at or below it.
- **Trend:** `Uptrend` when the price is above the 200-day SMA and the 50-day
  SMA is above the 200-day SMA; `Downtrend` for the mirror case; otherwise
  `Mixed`.
- **Volatility regime:** current 20-day annualised volatility versus its own
  rolling median (`High vol` > 1.1×, `Low vol` < 0.9×).
- **Group indices:** equal-weighted synthetic indices built from constituent
  daily returns, for broad sectors, industries, NACE sections and NACE
  divisions. Groups with fewer than 30 observations are skipped.
- **Beta:** covariance of daily returns with the DAX divided by DAX variance.
- **Forward base rates:** for horizons 21, 63 and 126 trading days, every
  historical forward return is computed. The distribution is summarised
  unconditionally and conditional on the current trend, drawdown bucket and
  volatility regime. Conditional rows need at least 15 observations.
- **Breadth:** share of DAX 40 constituents above their 50/200-day SMA on each
  date in the window.

Charts written to `reports/charts/`:

| File | Content |
|---|---|
| `performance_indices.svg` | Five indices, base 100 |
| `performance_sectors.svg` | Equal-weighted sector indices, base 100 |
| `drawdowns.svg` | Index drawdowns from the running peak |
| `rolling_vol.svg` | 20-day annualised volatility for the DAX and selected sectors |
| `correlation_heatmap.svg` | Sector daily-return correlation matrix |
| `monthly_heatmap.svg` | DAX monthly returns by year and month |
| `breadth.svg` | DAX 40 breadth over time |
| `company_ranking.svg` | DAX 40 one-year returns, sorted |
| `risk_return.svg` | Company volatility versus one-year return |

## `scripts/svg.py`

Dependency-free SVG primitives: `SVG` (text, line, rect, polyline, circle),
axis frames, nice ticks, legends and colour scales. It contains no business
logic and is imported by `charts.py`.

## `scripts/charts.py`

Chart definitions. Each `chart_*` function reads the already-computed series,
metrics or tables and writes one SVG file to `reports/charts/`. It uses
`svg.py` for drawing and `lib.py` for formatting and statistics.

## `scripts/report.py`

Reads the generated TSVs/JSON and writes seven price-market Markdown files:
`market_overview.md`, `sectors.md`, `companies.md`, `outlook.md`, `risk.md`,
`data_quality.md` and the report index. The fundamentals report is generated
separately by `fundamentals.py report`.

| File | Content |
|---|---|
| `reports/market_overview.md` | Key facts, index/sector dashboards, breadth, rates and FX |
| `reports/sectors.md` | Sector, industry, NACE section and division rankings; correlations |
| `reports/companies.md` | Full DAX 40 ranking, leaders/laggards, relative performance |
| `reports/outlook.md` | Current regime, unconditional and conditional forward base rates, scenarios |
| `reports/risk.md` | Volatility, drawdowns, tail risk, correlations, concentration |
| `reports/data_quality.md` | Coverage, errors, stale series, duplicates, OHLC flags |
| `reports/README.md` | Report index, data dictionary, methodology and disclaimer |

```bash
python3 scripts/report.py
```

## `scripts/fundamentals.py`

Collects company statements and analyst forecasts for 159 DAX 40, MDAX and
SDAX companies, computes distress/growth/valuation scores and five-year
scenarios, and renders `reports/fundamentals.md` plus three SVG charts.

```bash
python3 scripts/fundamentals.py collect            # network; cached and throttled
python3 scripts/fundamentals.py collect --refresh  # ignore the cache
python3 scripts/fundamentals.py collect --skip-quarterly
python3 scripts/fundamentals.py collect --only SAP.DE,BMW.DE
python3 scripts/fundamentals.py analyze            # metrics, scores, scenarios
python3 scripts/fundamentals.py report             # Markdown + charts
python3 scripts/fundamentals.py all                # all three stages
```

Options:

| Option | Meaning |
|---|---|
| `--refresh` | Ignore the on-disk fundamentals cache. |
| `--only TICKERS` | Comma-separated DAX tickers for the collector. |
| `--skip-forecast` | Do not fetch analyst forecast pages. |
| `--skip-quarterly` | Do not fetch quarterly statement pages. |

### Data sources and parsing

- Annual/TTM statement pages: `https://stockanalysis.com/quote/{exchange}/{symbol}/financials/{income-statement|balance-sheet|cash-flow-statement|ratios}/`
- Quarterly statement pages: the same URL with `?p=quarterly`; the collector stores up to 20 quarters in `data/fundamentals_quarterly.tsv`, scaled to millions. Quarterly data are not used by the scorecard.
- Forecast page: `https://stockanalysis.com/quote/{exchange}/{symbol}/forecast/`
- Airbus uses `epa/AIR`; all other DAX tickers use `etr/<symbol>`.
- Qiagen uses `https://stockanalysis.com/stocks/qgen/...` and reports in USD.
- Pages embed statement data as JavaScript arrays. `parse_js_arrays()` extracts
  flat `key:[...]` blocks before the `,map:` suffix.
- Monetary values are stored in millions; EPS/DPS/per-share values remain
  per-share.
- Porsche SE has no revenue line because it is an investment holding.
- The source map is `config/fundamentals_sources.tsv` with columns `ticker`,
  `company`, `industry`, `sector`, `nace_code`, `nace_label`, `exchange`,
  `symbol`, `url_style`, `model_type`, `notes`.

### Model types

| `model_type` | Used for | Main survival inputs |
|---|---|---|
| `non_financial` | Industrials, consumer, healthcare, materials, tech | Altman Z″, Ohlson O, coverage, debt/EBITDA, current ratio, cash/ST debt, FCF margin, synthetic credit score |
| `auto` | BMW, Mercedes-Benz, Volkswagen, Daimler Truck | Industrial scorecard; industrial debt/EBITDA replaces total debt/EBITDA where the captive-finance split is identifiable |
| `bank` | Deutsche Bank, Commerzbank | Equity/assets, ROA, earnings stability, cost/income, loan-loss rate, revenue growth |
| `insurer` | Allianz, Munich Re, Hannover Re | Equity/assets, ROA, earnings stability, policy-benefit ratio, expense ratio, revenue growth |
| `real_estate` | Vonovia | Equity/assets, interest coverage, debt/EBITDA, FFO margin, growth, earnings stability |
| `holding` | Porsche SE | Equity/assets, debt/EBITDA, FCF/assets, ROA, earnings stability, EPS growth |
| `utility` | E.ON, RWE | Coverage, debt/EBITDA, FCF margin, equity/assets, growth, earnings stability, current ratio |
| `telecom` | Deutsche Telekom | Same as utility |
| `asset_light` | Scout24 | Debt/EBITDA, coverage, FCF margin, ROIC−WACC, growth, earnings stability |
| `financial_infrastructure` | Deutsche Börse | Coverage, debt/EBITDA, ROIC−WACC, FCF margin, growth, earnings stability |

### Score construction

- **Survival (0–100):** industrial weights are Altman Z″ 25%, Ohlson 20%,
  interest coverage 15%, debt/EBITDA 15%, current ratio 8%, cash/ST debt 5%,
  FCF margin 7%, synthetic credit score 5%. Sector models use the ratios in the
  table above. Missing components are renormalised.
- **Growth (0–100):** industrial weights are revenue CAGR 20%, ROIC−WACC 25%,
  gross-margin trend 15%, FCF CAGR 10%, EPS CAGR 10%, R&D/revenue 10%,
  order-backlog growth 5%, analyst revenue growth 5%. Financials use
  ROE−cost of equity, book-value growth and dividend growth instead.
- **Valuation (0–100):** percentile rank of current PE, EV/EBITDA, P/FCF and
  FCF yield versus the company's own FY2021–FY2025 history. Banks, insurers,
  holdings and real estate use PE, price/book and dividend yield. A low score
  means expensive relative to its own history, not necessarily expensive in
  absolute terms.
- **Investment (0–100):** 40% growth + 30% survival + 30% valuation.
- **Distress band:** Low ≥75, Moderate 60–75, Elevated 45–60, High <45
  survival; Altman distress and Ohlson >50% can downgrade the band.
- **Altman Z″:** `6.56·WC/TA + 3.26·RE/TA + 6.72·EBIT/TA + 1.05·MVE/TL`.
  Safe ≥2.6, grey 1.1–2.6, distress <1.1. Original Z is kept as a cross-check.
- **Ohlson O-score:** the 1980 nine-factor model with operating cash flow as
  the FFO proxy and a constant GNP index. The probability is a relative
  indicator, not a calibrated default probability.
- **WACC:** CAPM with the 10-year Bund yield (3.27% fallback), a 5.5% equity
  risk premium, DAX beta where available (otherwise 1.0), clamped to 0.4–1.8,
  and after-tax cost of debt.
- **Scenarios:** five-year bear/base/bull/weighted cases using historical and
  analyst growth, an exit multiple from the company's own history bounded
  around the current multiple, and the current dividend yield. Weights are
  25/50/25. When spot PE exceeds 1.8× the historical median, average positive
  annual EPS is used to normalise a cyclical trough.
- **Data gaps:** debt-maturity schedules, order backlog, market-share trends,
  organic growth and a complete set of external credit ratings are unavailable
  for all companies; missing inputs are renormalised. MDAX/SDAX scenario prices
  use market cap / diluted shares and beta defaults to 1.0 without a DAX beta.

### Outputs

| File | Content |
|---|---|
| `data/fundamentals.tsv` | Up to 954 rows: 159 companies × TTM + up to five fiscal years |
| `data/fundamentals_quarterly.tsv` | Quarterly income, balance-sheet and cash-flow rows for the full universe (up to 20 quarters per company) |
| `data/forecasts.tsv` | Analyst forward revenue and EPS estimates |
| `data/fundamentals_manifest.json` | Collection coverage and missing-field flags |
| `data/fundamentals_metrics.tsv` | 159 rows of computed metrics |
| `data/fundamentals_scores.tsv` | 159 rows of scores and JSON component breakdowns |
| `data/fundamentals_scenarios.tsv` | Up to 636 rows: 159 companies × 4 scenario rows |
| `data/fundamentals_analysis_manifest.json` | Weights, assumptions and score coverage |
| `reports/fundamentals.md` | Full methodology, rankings and 159 company sections |
| `reports/charts/fundamentals_survival.svg` | Survival ranking |
| `reports/charts/fundamentals_scores.svg` | Growth vs survival scatter |
| `reports/charts/fundamentals_growth_valuation.svg` | Growth vs valuation scatter |

## `scripts/web.py`

Generates the static web dashboard `web/index.html` from the same fundamentals
TSVs for the DAX 40, MDAX and SDAX. It uses only the Python standard library and
no JavaScript framework, server, database or API. Search, model/band filters, sorting and expandable
company details are implemented in a small inline script.

```bash
python3 scripts/web.py
python3 scripts/web.py --open
```

Open `web/index.html` directly, or serve the repository root:

```bash
python3 -m http.server 8000
# http://localhost:8000/web/
```

The dashboard is deliberately single-page and read-only (KISS/YAGNI): no
authentication, editing, database or separate API. It reuses `lib.py` and the
data-loading/formatting helpers in `fundamentals.py` to stay DRY. Scores are
shown with plain-language names and short explanations (financial safety,
growth potential, price attractiveness, overall appeal) so the page is usable
without knowing the underlying formulas. See
`web/README.md` for usage.

## `scripts/run_all.py`

Runs the price pipeline, fundamentals pipeline and static web dashboard in
order using subprocesses, so each stage keeps its own options and exit status.

```bash
python3 scripts/run_all.py
python3 scripts/run_all.py --refresh
python3 scripts/run_all.py --skip-collect
python3 scripts/run_all.py --only DAX,SAP.DE
python3 scripts/run_all.py --skip-fundamentals
python3 scripts/run_all.py --fundamentals-only SAP.DE
```

| Option | Meaning |
|---|---|
| `--refresh` | Pass `--refresh` to the price and fundamentals collectors. |
| `--skip-collect` | Reuse the existing price data and cached fundamentals; recompute all analysis and reports without network access. |
| `--only IDS` | Collect only the listed price series ids (partial dataset). |
| `--skip-fundamentals` | Skip the fundamentals collect/analyse/report stage. |
| `--fundamentals-only TICKERS` | Collect only the listed DAX tickers for fundamentals. |
| `--skip-quarterly` | Skip quarterly fundamentals collection. |

## `scripts/run.sh`

Documented bash wrapper. It changes to the repository root and executes
`python3 scripts/run_all.py "$@"`.

```bash
./scripts/run.sh
./scripts/run.sh --refresh
./scripts/run.sh --skip-collect
./scripts/run.sh --only DAX,SAP.DE
./scripts/run.sh --skip-fundamentals
./scripts/run.sh --fundamentals-only SAP.DE
```

The script uses `set -euo pipefail` and `exec`, so failures propagate and the
Python exit code becomes the shell exit code. It is safe to call from any
working directory.

## `Makefile` targets

| Target | Command | Purpose |
|---|---|---|
| `make all` | `python3 scripts/run_all.py` | Full sequential pipeline |
| `make collect` | `python3 scripts/collect.py` | Collect/reuse cached price data |
| `make refresh` | `python3 scripts/collect.py --refresh` | Force fresh price downloads |
| `make analyze` | `python3 scripts/analyze.py` | Recompute price metrics and charts |
| `make report` | `python3 scripts/report.py` | Regenerate Markdown reports |
| `make fundamentals` | `python3 scripts/fundamentals.py all` | Collect, analyse and report fundamentals |
| `make fundamentals-refresh` | `python3 scripts/fundamentals.py all --refresh` | Force fresh fundamentals downloads |
| `make fundamentals-analyze` | `python3 scripts/fundamentals.py analyze` | Recompute scores and scenarios |
| `make fundamentals-report` | `python3 scripts/fundamentals.py report` | Regenerate `fundamentals.md` and charts |
| `make web` | `python3 scripts/web.py` | Regenerate `web/index.html` |
| `make clean` | removes `data/raw/` and `reports/charts/` | Clear cache and generated charts |

`ARGS` is passed to the price collector, for example
`make collect ARGS="--only DAX,SAP.DE"`. `FUNDAMENTALS_ARGS` is passed to the
fundamentals target, for example
`make fundamentals FUNDAMENTALS_ARGS="--only SAP.DE"`.

## Generated data schema

### `data/german_markets.tsv`

Long format, one row per series and date:

| Column | Meaning |
|---|---|
| `date` | ISO date (`YYYY-MM-DD`) |
| `series_id` | Ticker or series id (`DAX`, `SAP.DE`, `EURUSD`, `DE10Y`, …) |
| `name` | Human-readable name |
| `asset_class` | `index`, `equity`, `fx` or `rate` |
| `group` | `Indices`, `DAX 40`, `FX` or `Rates` |
| `industry` | Prime Standard industry (equities only) |
| `sector` | Broad sector (equities only) |
| `nace_code` | NACE Rev. 2.1 class code (equities only) |
| `nace_label` | NACE class label |
| `nace_section` | NACE section letter |
| `nace_section_label` | NACE section name |
| `nace_division` | NACE 2-digit division |
| `nace_division_label` | NACE division name |
| `unit` | `EUR`, `index`, `rate`, … |
| `currency` | Currency code where applicable |
| `open`, `high`, `low`, `close` | Daily OHLC |
| `volume` | Daily volume (0 when unavailable) |
| `source` | `CNBC` |
| `source_symbol` | Resolved CNBC symbol |
| `fetched_at` | UTC collection timestamp |

### `data/dax_constituents.tsv`

One row per company: `ticker`, `company`, `industry`, `sector`, `nace_code`,
`nace_label`, `nace_section`, `nace_section_label`, `nace_division`,
`nace_division_label`, `weight_pct`, `series_id`, `source`.

### `data/metrics.tsv`

One row per individual series or synthetic group. Important columns:
`level`, `series_id`, `name`, `last`, `ret_1d/1w/1m/3m/6m/ytd/1y`,
`ann_vol`, `sharpe`, `max_dd`, `dd_peak_date`, `dd_trough_date`,
`pct_from_high`, `var95`, `cvar95`, `sma50`, `sma200`, `trend`,
`current_vol`, `median_vol`, `vol_regime`, `n_constituents`,
`pct_above_sma50`, `pct_above_sma200`, `beta_dax`, and for rates
`change_bps_1d/1m/1y/ytd` and `ann_vol_bps`.

### `data/forward_base_rates.tsv`

One row per series, horizon and condition: `series_id`, `level`,
`horizon_days` (21/63/126), `condition_type` (`All`, `Trend`, `Drawdown`,
`Volatility`), `condition_value`, `n`, `mean`, `median`, `p10`, `p25`, `p75`,
`p90`, `prob_positive`.

### `data/correlations.tsv`

`series_a`, `series_b`, `corr`, `n`.

### `data/monthly_returns.tsv`

`series_id`, `month` (`YYYY-MM`), `return`.

### `data/breadth.tsv`

`date`, `n`, `above_sma50_pct`, `above_sma200_pct`.

### `data/sector_breadth.tsv`

`group_id`, `name`, `level`, `n_constituents`, `above_sma50_pct`,
`above_sma200_pct`, `positive_ytd_pct`, `median_ret_1y`.

### `data/fundamentals.tsv`

Long format, one row per company and period (`TTM`, `FY2021`–`FY2025`).
Important columns: `ticker`, `company`, `model_type`, `period`,
`period_end_date`, `period_end`, `is_ttm`, `currency`, `revenue`,
`gross_profit`, `ebit`, `ebitda`, `net_income`, `interest_expense`,
`rnd_expense`, `eps_diluted`, `dps`, `total_assets`, `total_liabilities`,
`total_equity`, `current_assets`, `current_liabilities`,
`cash_st_investments`, `receivables`, `inventory`, `retained_earnings`,
`total_debt`, `short_term_debt`, `financial_services_debt`, `free_cash_flow`,
`operating_cash_flow`, `capex`, `market_cap`, `enterprise_value`, `pe`,
`forward_pe`, `ev_ebitda`, `p_fcf`, `fcf_yield`, `dividend_yield`, `pb`,
`ps`, `roic`, `roe`, `roa`, `gross_margin`, `operating_margin`,
`cost_income`, `loan_loss_ratio`, `policy_benefit_ratio`, `expense_ratio`,
`ffo_margin`, `book_value_per_share`, `order_backlog`, `shares_diluted`.

### `data/fundamentals_quarterly.tsv`

One row per company and quarter: `ticker`, `company`, `period`
(`YYYY-Qn`), `period_end`, `fiscal_year`, `fiscal_quarter`, `currency`,
`units`, `source`, `source_date`, `revenue`, `gross_profit`, `ebit`, `ebitda`,
`net_income`, `interest_expense`, `rnd_expense`, `eps_diluted`,
`total_assets`, `total_equity`, `total_liabilities`, `total_debt`,
`cash_st_investments`, `operating_cash_flow`, `capex`, `free_cash_flow`,
`notes`.

### `data/forecasts.tsv`

One row per company: `ticker`, `company`, `next_revenue_growth`,
`next_eps_growth`, `source`, `fetched_at`.

### `data/fundamentals_metrics.tsv`

One row per company. Includes `index_name`, `model_type`, `altman_z`, `altman_z2`, `ohlson_probability`,
`wacc`, `roic`, `roic_wacc_spread`, `interest_coverage`, `debt_ebitda`,
`net_debt_ebitda`, `industrial_debt_ebitda`, `leverage_for_score`,
`current_ratio`, `cash_st_debt`, `fcf_margin`, `equity_assets`, `roa`,
`earnings_volatility`, `valuation_score`, `survival_score`, `growth_score`,
`investment_score`, `distress_band`, `distress_prob_1y`, `distress_prob_3y`,
`credit_score`, `valuation_notes` and `data_notes`.

### `data/fundamentals_scores.tsv`

One row per company: `index_name`, the four scores, the distress band, Altman/Ohlson fields,
and JSON component/weight breakdowns (`survival_components`,
`survival_weights`, `growth_components`, `growth_weights`,
`valuation_components`, `valuation_weights`, `investment_weights`).

### `data/fundamentals_scenarios.tsv`

Four rows per company: `bear`, `base`, `bull`, `weighted`. Columns:
`ticker`, `company`, `scenario`, `horizon_years`, `basis`, `current_price`,
`current_multiple`, `growth_rate`, `exit_multiple`, `expected_price`,
`price_return_cagr`, `dividend_yield`, `total_return_cagr`,
`probability_weight`, `notes`.

## Configuration schema

### `config/series.tsv`

`series_id`, `name`, `asset_class`, `group`, `unit`, `currency`, `symbol`.
Add a row here to collect another index, FX pair or rate series.

### `config/sector_map.tsv`

`industry`, `sector`. Maps DAX/MDAX/SDAX industry labels to a broad sector.

### `config/nace_map.tsv`

`ticker`, `nace_code`, `nace_label`, `nace_section`, `nace_section_label`,
`nace_division`, `nace_division_label`. One row per DAX 40, MDAX and SDAX company.

### `config/fundamentals_sources.tsv`

`ticker`, `company`, `industry`, `sector`, `nace_code`, `nace_label`,
`source_exchange`, `source_symbol`, `source_route`, `model_type`, `notes`,
`index_name`. One row per DAX 40, MDAX and SDAX company. `source_route` is
`quote` for `etr/<symbol>` and `stocks` for Qiagen; `model_type` selects the
sector scorecard.

## Troubleshooting

- **`No data found`:** run `python3 scripts/collect.py` first.
- **A series fails:** re-run with `--refresh`; the collector retries symbol
  variants and keeps going. Check `data/collection_manifest.json`.
- **Stale cache:** delete `data/raw/` or run `--refresh`.
- **Charts missing:** run `python3 scripts/analyze.py`; it writes
  `reports/charts/`.
- **Report tables empty:** run `python3 scripts/analyze.py` before
  `python3 scripts/report.py`.
- **Fundamentals report missing:** run `python3 scripts/fundamentals.py all`.
- **Fundamentals tables empty:** run `python3 scripts/fundamentals.py analyze`
  before `python3 scripts/fundamentals.py report`.
- **A fundamentals fetch fails:** re-run with `--refresh`; the collector keeps
  cached pages and continues with the remaining companies. Check
  `data/fundamentals_manifest.json`.
- **Quarterly panel missing:** run `python3 scripts/fundamentals.py collect`
  without `--skip-quarterly`.
- **Web dashboard missing:** run `python3 scripts/web.py` after the fundamentals
  analysis. It needs `data/fundamentals_scores.tsv`.
- **Rate limiting:** both collectors already throttle and back off; do not run
  multiple collectors in parallel.
