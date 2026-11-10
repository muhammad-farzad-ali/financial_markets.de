# German financial markets — data pipeline and reports

A small, dependency-free Python pipeline that collects German financial-market
data, classifies the DAX 40, MDAX and SDAX by economic activity (including
**NACE Rev. 2.1**), analyses past performance, and generates Markdown reports
with empirical forward-looking base rates. It also builds a sector-aware
corporate distress and long-term investment forecasting scorecard for every
company in the DAX 40, MDAX and SDAX.

The project deliberately uses only the Python standard library (Python 3.11+).
No pandas, numpy, matplotlib or yfinance is required.

## Quick start

```bash
# Full pipeline (prices, fundamentals, reports, web dashboard)
make all

# Or directly
python3 scripts/run_all.py

# Force fresh downloads instead of the 6-hour cache
python3 scripts/run_all.py --refresh

# Re-run both analysis pipelines and reports without network access
python3 scripts/run_all.py --skip-collect

# Fundamentals only (collect, analyse, report)
python3 scripts/fundamentals.py all

# Recompute the fundamentals scores and report from cached data
python3 scripts/fundamentals.py analyze
python3 scripts/fundamentals.py report

# Generate the static web dashboard
python3 scripts/web.py
```

The shell wrapper does the same and can be called from any directory:

```bash
./scripts/run.sh
./scripts/run.sh --refresh
./scripts/run.sh --skip-collect
./scripts/run.sh --only DAX,SAP.DE
./scripts/run.sh --skip-fundamentals
./scripts/run.sh --fundamentals-only SAP.DE
```

Open `reports/README.md` (the report index) or the individual reports in
`reports/` afterwards.

## What is collected

| Group | Series |
|---|---|
| Indices | DAX, MDAX, SDAX, TecDAX, Euro Stoxx 50 |
| DAX 40 | 40 constituents, daily OHLCV (price pipeline) |
| MDAX / SDAX | Fundamentals and valuation data for 119 additional companies |
| FX | EUR/USD |
| Rates | German 10-year Bund yield |
| Classification | Broad sector, Prime Standard industry, NACE Rev. 2.1 |

Daily bars are stored in `data/german_markets.tsv`. The analysis window is the
last 252 trading sessions, while the collector keeps roughly two years of bars
so that one-year metrics, correlations and forward-return samples are available.

The fundamentals stage adds TTM plus up to FY2021–FY2025 income statements,
balance sheets, cash-flow statements, valuation ratios and analyst forecasts
for 159 companies across the DAX 40, MDAX and SDAX. The daily price pipeline
still covers the DAX 40; for MDAX/SDAX companies the scenario price is derived
from market cap / diluted shares and beta falls back to 1.0. A separate
quarterly panel stores up to 20 quarters of income, balance-sheet and cash-flow
data; it is kept for transparency and is
not used in the scorecard. The primary source is
[stockanalysis.com](https://stockanalysis.com/) (data by S&P Global Market
Intelligence); the company-to-source map is `config/fundamentals_sources.tsv`.

## Outputs

| Path | Contents |
|---|---|
| `data/german_markets.tsv` | Main long-format dataset (date, series, OHLCV, classification) |
| `data/dax_constituents.tsv` | DAX 40 composition with sector, industry and NACE codes |
| `data/metrics.tsv` | Return, risk, trend and breadth metrics for every series/group |
| `data/forward_base_rates.tsv` | Empirical forward-return distributions by horizon and condition |
| `data/correlations.tsv` | Pairwise daily-return correlations |
| `data/monthly_returns.tsv` | Month-end returns |
| `data/breadth.tsv` | DAX 40 breadth over time |
| `data/sector_breadth.tsv` | Current breadth by sector/industry/NACE |
| `data/fundamentals.tsv` | Long-format statements for 159 DAX/MDAX/SDAX companies |
| `data/fundamentals_quarterly.tsv` | Quarterly income, balance-sheet and cash-flow statements |
| `data/forecasts.tsv` | Analyst forward revenue/EPS estimates |
| `data/fundamentals_metrics.tsv` | Distress, growth, valuation and WACC metrics |
| `data/fundamentals_scores.tsv` | 0–100 survival, growth, valuation and investment scores |
| `data/fundamentals_scenarios.tsv` | Bear/base/bull/weighted five-year return scenarios |
| `data/collection_manifest.json` | Fetch log, coverage and validation flags |
| `data/analysis_manifest.json` | Analysis window and row counts |
| `data/fundamentals_manifest.json` | Fundamentals collection coverage |
| `data/fundamentals_analysis_manifest.json` | Score weights, assumptions and coverage |
| `reports/*.md` | Market, sector, company, outlook, risk, data-quality and fundamentals reports |
| `reports/charts/*.svg` | Self-contained SVG charts |
| `web/index.html` | Static DAX/MDAX/SDAX dashboard (search, filters, sortable table, detail panels) |

## Repository layout

```
config/
  series.tsv          # index, FX and rate series definitions
  sector_map.tsv      # industry -> broad sector (DAX/MDAX/SDAX)
  nace_map.tsv        # DAX/MDAX/SDAX -> NACE Rev. 2.1 code, section and division
  fundamentals_sources.tsv  # DAX/MDAX/SDAX -> source and model type
data/
  raw/                # cached HTTP responses (safe to delete)
  *.tsv, *.json       # generated datasets and manifests
reports/
  *.md                # generated Markdown reports
  charts/*.svg        # generated charts
web/
  index.html          # generated static dashboard
  README.md           # dashboard usage
scripts/
  lib.py              # throttled HTTP, cache, TSV/JSON and formatting helpers
  collect.py          # data collection
  analyze.py          # metrics, group indices, forward base rates, charts
  svg.py              # dependency-free SVG primitives
  charts.py           # chart definitions
  report.py           # Markdown report generation
  fundamentals.py     # fundamentals collector, scorecards, scenarios and report
  web.py              # static web dashboard generator
  run_all.py          # pipeline orchestrator
  run.sh              # documented shell wrapper
Makefile              # convenience targets
```

## Data source and rate limiting

The primary source is the unofficial CNBC quote/chart endpoint. It is used
politely:

- at most **one request per second**;
- exponential backoff with `Retry-After` support for HTTP 429/5xx;
- a **6-hour disk cache** in `data/raw/`;
- `--refresh` to bypass the cache.

A full refresh performs roughly 100 requests and takes about two minutes. The
collector handles dot/hyphen symbol variants (for example `BMW.DE` vs
`BMW-DE`), prefers the variant with the longest usable history, appends the
current quote when the daily chart lags by one session, and keeps the last
successful data if a series fails.

The fundamentals collector uses the same throttled HTTP/cache layer. It fetches
annual/TTM statement pages, quarterly statement pages and one forecast page per
company, caches them under `data/raw/fundamentals/`, and reuses cached pages
unless `--refresh` is passed. A full refresh for 159 companies is roughly
1,300 requests and takes substantially longer. Do not run multiple collectors
in parallel.

## NACE classification

Each DAX 40, MDAX and SDAX company is mapped to **NACE Rev. 2.1** (the official
EU statistical classification of economic activities) in `config/nace_map.tsv`:

- `nace_code` — class level where the primary activity is clear (for example
  `58.29` for SAP, `29.10` for Volkswagen);
- `nace_label` — official activity label;
- `nace_section` / `nace_section_label` — the first letter and section name;
- `nace_division` / `nace_division_label` — the 2-digit division.

NACE describes **what a company does**, not how it trades. It is not an
investment sector classification and does not capture revenue mix, geography or
valuation. Conglomerates are assigned by their primary activity; this is an
analytical choice, not an official company-register entry.

## Corporate distress and investment forecasting

The fundamentals stage produces four separate outputs for every DAX 40, MDAX
and SDAX company. They are deliberately not collapsed into a single "buy/sell"
signal:

1. **Bankruptcy/distress probability (1–3 years):** a relative survival score,
   Altman Z/Z″ zones, Ohlson O-score and a naive three-year extrapolation.
2. **Financial survival probability (1–5 years):** a sector-aware 0–100 score
   built from leverage, coverage, liquidity, profitability and earnings
   stability.
3. **Business growth potential (3–10 years):** a 0–100 score from revenue,
   EPS and FCF growth, ROIC−WACC, margin trends, R&D, order backlog and
   analyst estimates.
4. **Expected investment return (1–5 years):** a valuation score plus
   bear/base/bull five-year scenario illustrations and a scenario-weighted
   total return.

Because a bank, an insurer, a utility, a holding company and an industrial
conglomerate have fundamentally different balance sheets, the scorecard uses
`model_type` values (`non_financial`, `auto`, `bank`, `insurer`,
`real_estate`, `holding`, `utility`, `telecom`, `asset_light`,
`financial_infrastructure`). Altman and Ohlson are used only where they are
meaningful. External credit ratings are not used as a scoring input because a
complete, dated mapping for all companies was not available; a synthetic
credit score based on coverage and leverage is used instead.

The scores are **relative scorecards, not calibrated probabilities**. The
current index snapshot contains no defaults in the collected sample, and a
consistent historical panel of default/growth labels was not available, so the
scores are not statistically validated against outcomes. Five-year scenarios are
mechanical illustrations, not forecasts or target prices. See
`reports/fundamentals.md` and
`scripts/README.md` for the full methodology and weights.

## Analysis method

- **Returns:** simple price returns; price indices and price returns exclude
  dividends.
- **Risk:** annualised volatility, Sharpe ratio (2% risk-free rate), maximum
  drawdown, VaR 95 and CVaR 95.
- **Trend:** price versus 50/200-day moving averages.
- **Breadth:** share of DAX 40 constituents above their 50/200-day moving
  averages.
- **Groups:** equal-weighted synthetic sector, industry and NACE section/
  division indices built from constituent daily returns.
- **Beta:** covariance of daily returns with the DAX divided by DAX variance.
- **Forward base rates:** for 3- and 6-month horizons, the distribution of all
  historical forward returns, unconditional and conditional on the current
  trend, drawdown bucket and volatility regime. These are **historical base
  rates, not forecasts**.
- **Fundamentals scores:** survival, growth, valuation and investment scores
  are 0–100 relative scorecards. Investment = 40% growth + 30% survival +
  30% valuation. WACC uses CAPM with a 3.27% Bund yield, a 5.5% equity risk
  premium and a DAX beta where available (otherwise 1.0), clamped to 0.4–1.8.
  Scenarios use historical and
  analyst growth, a bounded exit multiple and the current dividend yield.
- **Fundamentals data:** TTM plus FY2021–FY2025 from stockanalysis.com;
  values are stored in millions except per-share figures. Qiagen reports in
  USD; all other companies report in EUR.

See `reports/README.md` for the report index and `scripts/README.md` for
detailed script and data-schema documentation.

## Web dashboard

`python3 scripts/web.py` (or `make web`) generates a single static page at
`web/index.html` for the DAX 40, MDAX and SDAX universe. It is intentionally
minimal: no framework, server, database or API. Search, model/distress-band filters, sorting and expandable company
details run entirely in the browser from data rendered into the page.

```bash
python3 scripts/web.py
# open web/index.html directly, or:
python3 -m http.server 8000
# http://localhost:8000/web/
```

The dashboard is read-only and reuses the same TSVs as
`reports/fundamentals.md`; it is a view, not a second analysis implementation.
Scores are presented in plain language — financial safety, growth potential,
price attractiveness and overall appeal — with short explanations for every
metric and a reading guide for non-technical users. Index, business-type and
risk filters keep the larger MDAX/SDAX list manageable.

## Caveats and disclaimer

- CNBC is an unofficial endpoint; quotes are delayed and the API can change.
- One to two years of daily data is a small sample for regime analysis.
- Overlapping forward windows are autocorrelated, so the effective sample is
  smaller than the raw observation count.
- Wikipedia is used only for the DAX composition and sector labels; the NACE
  mapping is a curated static file.
- Fundamentals data are sourced from stockanalysis.com/S&P Global Market
  Intelligence and can differ from primary filings because of TTM alignment,
  restatements and reported-versus-adjusted definitions. Quarterly statements
  are collected separately and are not used in the scorecard.
- The distress, growth and investment scores are **not historically
  validated** against default or return labels. Ohlson probabilities are
  relative model outputs, not calibrated default probabilities. Sector
  scorecards are judgment-based and transparent.
- R&D, order backlog, debt-maturity schedules, market-share trends, organic
  growth and a complete set of external credit ratings are unavailable for all
  companies. Missing inputs are renormalised, which changes effective
  weights by company.
- Captive financial-services debt distorts leverage for BMW, Mercedes-Benz,
  Volkswagen and Daimler Truck. Where the split is identifiable, the score
  uses industrial debt/EBITDA; total leverage is shown separately.
- Five-year scenario returns are highly sensitive to the starting and exit
  multiples. They are illustrations, not forecasts or target prices.
- The pipeline is for research and education. It is **not investment advice**,
  an offer, or a solicitation to buy or sell any security. Past performance
  does not guarantee future results. Verify all data before making decisions.
