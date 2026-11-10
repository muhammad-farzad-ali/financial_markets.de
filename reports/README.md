# German financial markets — report index

**Market data as of:** 2026-11-09 · generated 2026-11-10  
**Analysis window:** 2025-11-11 to 2026-11-09 (252 trading sessions)

## Reports

| Report | Contents |
|---|---|
| [Market overview](market_overview.md) | Index and sector dashboard, breadth, rates and FX |
| [Sectors, industries and NACE](sectors.md) | Sector/industry/NACE rankings, correlations |
| [DAX 40 companies](companies.md) | Full company ranking, leaders, laggards, relative performance |
| [Outlook](outlook.md) | Empirical forward-return base rates and scenario ranges |
| [Risk](risk.md) | Volatility, drawdowns, tail risk, correlations, concentration |
| [Data quality](data_quality.md) | Coverage, gaps, duplicates, stale series |
| [Fundamentals, distress and investment](fundamentals.md) | DAX/MDAX/SDAX survival, growth, valuation and investment scores; five-year scenarios |

## Web dashboard

A generated, dependency-free dashboard for the DAX 40, MDAX and SDAX is available at [`web/index.html`](../web/index.html). It includes a plain-language reading guide, friendly score names and short metric explanations. Open it directly or run `python3 -m http.server 8000` and visit `http://localhost:8000/web/`.

## Reproducing

See [scripts/README.md](../scripts/README.md) for documentation of every script, the `scripts/run.sh` shell wrapper, the Makefile targets and the generated data schema. The short version is `make all` or `python3 scripts/run_all.py`. Fundamentals can be regenerated separately with `python3 scripts/fundamentals.py all`.

## Data

| File | Description |
|---|---|
| `data/german_markets.tsv` | Main long-format dataset: daily OHLCV plus classification columns |
| `data/dax_constituents.tsv` | DAX 40 composition with industry, broad sector and NACE codes |
| `data/metrics.tsv` | One row per series/group with return, risk and trend metrics |
| `data/forward_base_rates.tsv` | Empirical forward-return distributions by horizon and condition |
| `data/correlations.tsv` | Pairwise daily-return correlations |
| `data/monthly_returns.tsv` | Month-end returns |
| `data/breadth.tsv` | DAX 40 breadth over time |
| `data/sector_breadth.tsv` | Current breadth by sector/industry/NACE |
| `data/fundamentals.tsv` | Statements for the DAX 40, MDAX and SDAX universe |
| `data/fundamentals_quarterly.tsv` | Quarterly income, balance-sheet and cash-flow statements |
| `data/forecasts.tsv` | Analyst forward revenue and EPS estimates |
| `data/fundamentals_metrics.tsv` | Distress, growth, valuation and WACC metrics |
| `data/fundamentals_scores.tsv` | Survival, growth, valuation and investment scores |
| `data/fundamentals_scenarios.tsv` | Bear/base/bull/weighted five-year scenarios |
| `data/collection_manifest.json` | Fetch log, row counts and validation flags |
| `data/analysis_manifest.json` | Analysis window and row counts |
| `data/fundamentals_manifest.json` | Fundamentals collection coverage |
| `data/fundamentals_analysis_manifest.json` | Score weights and assumptions |

## Method

1. **Collect** daily OHLCV from CNBC for five German indices, 40 DAX constituents, EUR/USD and the German 10-year Bund yield. The collector is rate-limited to one request per second and caches responses for six hours.
2. **Align** every series to the DAX trading calendar so that correlations, breadth and group indices compare the same sessions. The analysis window is the last 252 DAX sessions.
3. **Classify** each company by broad sector, Prime Standard industry and NACE Rev. 2.1 (section, division and class). NACE is the official EU activity classification; it describes what a company does, not how it trades.
4. **Aggregate** equal-weighted sector, industry and NACE group indices from constituent daily returns.
5. **Analyse** returns, volatility, Sharpe ratio, drawdowns, VaR/CVaR, moving averages, breadth, correlations and beta versus the DAX.
6. **Project historically** by computing empirical forward-return distributions and conditioning them on the current trend, drawdown and volatility state.
7. **Assess fundamentals separately** with a sector-aware scorecard: survival (Altman/Ohlson and sector ratios), business growth (revenue/EPS/FCF growth, ROIC−WACC, margins, R&D, backlog), valuation versus each company's own history, and a 40/30/30 investment score. Five-year bear/base/bull scenarios are illustrations, not forecasts. Quarterly statements are collected separately for transparency and are not used in the scorecard.

## Metrics

- Returns are simple price returns; the analysis window is the last 252 DAX trading sessions.
- Annualised volatility is the standard deviation of daily returns times √252.
- Sharpe uses a 2% annual risk-free rate.
- Maximum drawdown is measured on daily closes from the running peak.
- VaR 95 is the 5th percentile of daily returns; CVaR 95 is the mean of returns at or below it.
- Beta is the covariance of daily returns with the DAX divided by DAX variance.
- Breadth is the share of DAX 40 constituents above their 50/200-day moving average.

## NACE mapping

NACE codes are assigned at class level where the primary activity is clear, and at division level otherwise. The mapping lives in `config/nace_map.tsv` and is documented with the code, label, section and division. Examples:

| Company | NACE | Activity |
|---|---|---|
| SAP | 58.29 | Other software publishing |
| Siemens | 28.29 | Other general-purpose machinery |
| Allianz | 65.12 | Other non-life insurance |
| Deutsche Bank | 64.19 | Other monetary intermediation |
| Volkswagen | 29.10 | Manufacture of motor vehicles |

Conglomerates are assigned by their largest/primary activity; this is an analytical choice, not an official company register entry.

## Caveats

- CNBC is an unofficial endpoint; quotes are delayed and the API can change.
- Price indices and price returns exclude dividends.
- One year of daily data is a small sample for regime analysis; forward base rates are context, not forecasts.
- Wikipedia composition data can lag index changes.
- The NACE mapping is static and should be reviewed when index composition changes.
- Fundamentals scores are relative scorecards, not calibrated probabilities. They are not historically validated against default or return labels because a consistent DAX 40 panel of such labels is unavailable.
- Ohlson probabilities are relative indicators, not calibrated default probabilities. Sector scorecards for banks, insurers, utilities, telecoms, holdings, real estate, asset-light companies and financial infrastructure are transparent but judgment-based.
- R&D, order backlog, debt-maturity schedules, market-share trends, organic growth and a complete set of external credit ratings are unavailable for all 40 companies; missing inputs are renormalised.
- Auto leverage includes captive financial-services debt; where the split is identifiable, the score uses industrial debt/EBITDA and the report shows total leverage separately.
- Quarterly statements are collected separately for transparency; the scorecard uses the annual/TTM dataset.
- Five-year scenario returns are highly sensitive to the starting and exit multiples and are not target prices.

## Disclaimer

This project is for research and education. It is not investment advice, an offer, or a solicitation to buy or sell any security. Past performance does not guarantee future results. Forward-looking tables are historical base rates, not predictions. Verify all data before making decisions.
