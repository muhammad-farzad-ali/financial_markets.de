#!/usr/bin/env python3
"""Generate the Markdown reports from the analysed data."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

from lib import (
    DATA_DIR,
    REPORTS_DIR,
    fmt_int,
    fmt_num,
    fmt_pct,
    md_table,
    parse_number,
    read_json,
    read_tsv,
)

NUMERIC_FIELDS = {
    "last", "ret_1d", "ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_ytd",
    "ret_1y", "ann_vol", "sharpe", "max_dd", "high_52w", "low_52w",
    "pct_from_high", "current_dd", "best_day", "worst_day", "pct_positive",
    "var95", "cvar95", "skew", "kurtosis", "sma20", "sma50", "sma200",
    "momentum_3m", "momentum_6m", "current_vol", "median_vol",
    "n_constituents", "pct_above_sma50", "pct_above_sma200",
    "pct_positive_ytd", "median_ret_1y", "beta_dax",
    "change_bps_1d", "change_bps_1m", "change_bps_1y", "change_bps_ytd",
    "ann_vol_bps", "weight_pct", "n", "expected_business_days", "above_sma50_pct", "above_sma200_pct",
    "positive_ytd_pct", "horizon_days", "mean", "median", "p10", "p25",
    "p75", "p90", "prob_positive", "corr",
}
BOOL_FIELDS = {"above_sma50", "above_sma200", "quote_appended"}


def load_table(path):
    rows = []
    for raw in read_tsv(path):
        item = {}
        for key, value in raw.items():
            if key in BOOL_FIELDS:
                item[key] = str(value).strip().lower() == "true"
            elif key in NUMERIC_FIELDS:
                item[key] = parse_number(value)
            else:
                item[key] = value
        rows.append(item)
    return rows


def by_level(rows, level):
    return [row for row in rows if row.get("level") == level]


def by_id(rows):
    return {row["series_id"]: row for row in rows}


def sorted_rows(rows, key, reverse=True):
    return sorted(rows, key=lambda row: (row.get(key) is None, -(row.get(key) or 0) if reverse else (row.get(key) or 0)))


def top(rows, key, count=5, reverse=True):
    return sorted_rows(rows, key, reverse)[:count]


def pct(value, digits=1, sign=True):
    return fmt_pct(value, digits, sign)


def bps(value):
    if value is None:
        return "—"
    return f"{value:+.0f} bps"


def num(value, digits=2):
    return fmt_num(value, digits)


def fnum(value, digits=2):
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def table(headers, rows, aligns=None):
    return md_table(headers, rows, aligns)


def metric_table_row(row, columns):
    output = []
    for column in columns:
        value = row.get(column)
        if column in ("ret_1d", "ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_ytd", "ret_1y",
                      "ann_vol", "max_dd", "pct_from_high", "pct_above_sma50",
                      "pct_above_sma200", "pct_positive_ytd", "median_ret_1y",
                      "var95", "cvar95"):
            output.append(pct(value))
        elif column in ("last", "high_52w", "low_52w", "sma50", "sma200"):
            output.append(fnum(value))
        elif column == "beta_dax":
            output.append("—" if value is None else f"{value:.2f}")
        elif column == "n_constituents":
            output.append(fmt_int(value))
        else:
            output.append("—" if value is None else str(value))
    return output


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_all():
    metrics = load_table(DATA_DIR / "metrics.tsv")
    correlations = load_table(DATA_DIR / "correlations.tsv")
    forward = load_table(DATA_DIR / "forward_base_rates.tsv")
    breadth = load_table(DATA_DIR / "breadth.tsv")
    sector_breadth = load_table(DATA_DIR / "sector_breadth.tsv")
    constituents = load_table(DATA_DIR / "dax_constituents.tsv")
    collection = read_json(DATA_DIR / "collection_manifest.json")
    analysis = read_json(DATA_DIR / "analysis_manifest.json")
    return {
        "metrics": metrics,
        "metrics_by_id": by_id(metrics),
        "correlations": correlations,
        "forward": forward,
        "breadth": breadth,
        "sector_breadth": sector_breadth,
        "constituents": constituents,
        "collection": collection,
        "analysis": analysis,
    }


def forward_row(forward, series_id, horizon, condition_type):
    for row in forward:
        if (
            row["series_id"] == series_id
            and int(row["horizon_days"] or 0) == horizon
            and row["condition_type"] == condition_type
        ):
            return row
    return None


def forward_all(forward, series_id, horizon):
    return forward_row(forward, series_id, horizon, "All")


def forward_conditional(forward, series_id, horizon, metrics_row):
    if metrics_row is None:
        return None
    trend = metrics_row.get("trend")
    if trend == "Uptrend":
        target = "Above 200d SMA"
    elif trend == "Downtrend":
        target = "Below 200d SMA"
    else:
        target = "Above 200d SMA" if metrics_row.get("above_sma200") else "Below 200d SMA"
    for row in forward:
        if (
            row["series_id"] == series_id
            and int(row["horizon_days"] or 0) == horizon
            and row["condition_type"] == "Trend"
            and row["condition_value"] == target
        ):
            return row
    return None


def latest_breadth(breadth):
    return breadth[-1] if breadth else None


def sector_weight_table(constituents):
    totals = defaultdict(float)
    counts = defaultdict(int)
    for row in constituents:
        sector = row["sector"]
        counts[sector] += 1
        if row.get("weight_pct") is not None:
            totals[sector] += row["weight_pct"]
    rows = []
    for sector in sorted(counts, key=lambda item: (-totals.get(item, 0.0), item)):
        rows.append([sector, counts[sector], f"{totals.get(sector, 0.0):.1f}%"])
    rows.append(["**Total**", sum(counts.values()), f"**{sum(totals.values()):.1f}%**"])
    return rows


def correlation_pairs(correlations, ids):
    pairs = []
    idset = set(ids)
    for row in correlations:
        if row["series_a"] in idset and row["series_b"] in idset:
            pairs.append((row["series_a"], row["series_b"], row["corr"]))
    return pairs


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def report_market_overview(data):
    metrics = data["metrics"]
    by_id = data["metrics_by_id"]
    analysis = data["analysis"]
    breadth = data["breadth"]
    indices = by_level(metrics, "index")
    sectors = by_level(metrics, "sector")
    equities = by_level(metrics, "equity")
    dax = by_id.get("DAX")
    de10y = by_id.get("DE10Y")
    eurusd = by_id.get("EURUSD")
    latest = latest_breadth(breadth)

    best_index = max(indices, key=lambda row: row["ret_1y"] or -9)
    worst_index = min(indices, key=lambda row: row["ret_1y"] or 9)
    best_sector = max(sectors, key=lambda row: row["ret_1y"] or -9)
    worst_sector = min(sectors, key=lambda row: row["ret_1y"] or 9)
    dispersion = statistics.pstdev([row["ret_1y"] for row in equities if row["ret_1y"] is not None])
    positive_companies = sum(1 for row in equities if (row["ret_ytd"] or 0) > 0)

    lines = [
        "# German financial markets — overview",
        "",
        f"**As of:** {analysis['as_of']}  ",
        f"**Analysis window:** {analysis['window_start']} to {analysis['window_end']} "
        f"({analysis['window_days']} trading sessions)  ",
        f"**Coverage:** {analysis['series_count']} series, "
        f"{analysis['equity_count']} DAX 40 companies, {analysis['group_count']} sector/industry/NACE groups",
        "",
        "## Key facts",
        "",
        f"- The **DAX** returned **{pct(dax['ret_1y'])}** over the last 12 months, "
        f"**{pct(dax['ret_ytd'])}** year-to-date, with annualised volatility of "
        f"**{pct(dax['ann_vol'], 1, False)}** and a maximum drawdown of **{pct(dax['max_dd'])}**.",
        f"- The strongest index was **{best_index['name']}** ({pct(best_index['ret_1y'])}); "
        f"the weakest was **{worst_index['name']}** ({pct(worst_index['ret_1y'])}).",
        f"- At the sector level, **{best_sector['name']}** led ({pct(best_sector['ret_1y'])}) "
        f"and **{worst_sector['name']}** lagged ({pct(worst_sector['ret_1y'])}).",
        f"- **Breadth:** {pct(latest['above_sma50_pct'], 0, False)} of DAX 40 constituents traded above "
        f"their 50-day moving average and {pct(latest['above_sma200_pct'], 0, False)} above their 200-day average.",
        f"- **{positive_companies} of {len(equities)}** companies had a positive year-to-date return; "
        f"the cross-sectional dispersion of one-year returns was **{pct(dispersion, 1, False)}**.",
        f"- The **German 10-year Bund yield** closed at **{fnum(de10y['last'])}%**, "
        f"**{bps(de10y['change_bps_1y'])}** over 12 months and **{bps(de10y['change_bps_ytd'])}** year-to-date.",
        f"- **EUR/USD** closed at **{fnum(eurusd['last'], 4)}**, {pct(eurusd['ret_1y'])} over 12 months.",
        "",
        "## Index dashboard",
        "",
        table(
            ["Index", "Last", "1D", "1M", "3M", "YTD", "1Y", "Vol", "Max DD", "Trend", "From 52w high"],
            [
                [
                    row["name"], fnum(row["last"]), pct(row["ret_1d"]), pct(row["ret_1m"]),
                    pct(row["ret_3m"]), pct(row["ret_ytd"]), pct(row["ret_1y"]),
                    pct(row["ann_vol"], 1, False), pct(row["max_dd"]), row["trend"],
                    pct(row["pct_from_high"]),
                ]
                for row in sorted_rows(indices, "ret_1y")
            ],
            ["left", "right", "right", "right", "right", "right", "right", "right", "right", "left", "right"],
        ),
        "",
        "![German equity indices](charts/performance_indices.svg)",
        "",
        "## Sector dashboard",
        "",
        table(
            ["Sector", "n", "1D", "1M", "3M", "YTD", "1Y", "Vol", "Max DD", "% > 50d", "% > 200d"],
            [
                [
                    row["name"], fmt_int(row["n_constituents"]), pct(row["ret_1d"]),
                    pct(row["ret_1m"]), pct(row["ret_3m"]), pct(row["ret_ytd"]),
                    pct(row["ret_1y"]), pct(row["ann_vol"], 1, False), pct(row["max_dd"]),
                    pct(row["pct_above_sma50"], 0, False), pct(row["pct_above_sma200"], 0, False),
                ]
                for row in sorted_rows(sectors, "ret_1y")
            ],
            ["left", "right", "right", "right", "right", "right", "right", "right", "right", "right", "right"],
        ),
        "",
        "![Sector performance](charts/performance_sectors.svg)",
        "",
        "## Breadth",
        "",
        f"Breadth measures the share of the 40 DAX constituents trading above a moving average. "
        f"It is a simple, factual gauge of whether the index move is broad or narrow. "
        f"The latest reading is {pct(latest['above_sma50_pct'], 0, False)} above the 50-day SMA "
        f"and {pct(latest['above_sma200_pct'], 0, False)} above the 200-day SMA.",
        "",
        "![DAX breadth](charts/breadth.svg)",
        "",
        "## Rates and FX",
        "",
        table(
            ["Series", "Last", "1D change", "1M change", "YTD change", "1Y change", "Trend"],
            [
                [
                    de10y["name"], f"{fnum(de10y['last'])}%", bps(de10y["change_bps_1d"]),
                    bps(de10y["change_bps_1m"]), bps(de10y["change_bps_ytd"]),
                    bps(de10y["change_bps_1y"]), de10y["trend"],
                ],
                [
                    eurusd["name"], fnum(eurusd["last"], 4), pct(eurusd["ret_1d"]),
                    pct(eurusd["ret_1m"]), pct(eurusd["ret_ytd"]), pct(eurusd["ret_1y"]),
                    eurusd["trend"],
                ],
            ],
            ["left", "right", "right", "right", "right", "right", "left"],
        ),
        "",
        "## Read next",
        "",
        "- [Sectors, industries and NACE groups](sectors.md)",
        "- [DAX 40 companies](companies.md)",
        "- [Forward-looking base rates](outlook.md)",
        "- [Risk, drawdowns and correlations](risk.md)",
        "- [Data quality and coverage](data_quality.md)",
        "",
        "_Past performance is not a reliable indicator of future results. See the disclaimer in the report index._",
        "",
    ]
    (REPORTS_DIR / "market_overview.md").write_text("\n".join(lines), encoding="utf-8")


def report_sectors(data):
    metrics = data["metrics"]
    sectors = by_level(metrics, "sector")
    industries = by_level(metrics, "industry")
    sections = by_level(metrics, "nace_section")
    divisions = by_level(metrics, "nace_division")
    correlations = data["correlations"]

    sector_ids = [row["series_id"] for row in sectors]
    pairs = correlation_pairs(correlations, sector_ids)
    highest = sorted(pairs, key=lambda item: -item[2])[:5]
    lowest = sorted(pairs, key=lambda item: item[2])[:5]
    average_corr = statistics.fmean([p[2] for p in pairs]) if pairs else None

    def pair_label(pair):
        left = data["metrics_by_id"].get(pair[0], {}).get("name", pair[0])
        right = data["metrics_by_id"].get(pair[1], {}).get("name", pair[1])
        return [f"{left} / {right}", f"{pair[2]:.2f}"]

    sector_ranked = sorted_rows(sectors, "ret_1y")
    section_ranked = sorted_rows(sections, "ret_1y")
    division_ranked = sorted_rows(divisions, "ret_1y")
    industry_ranked = sorted_rows(industries, "ret_1y")

    lines = [
        "# Sectors, industries and NACE groups",
        "",
        f"**As of:** {data['analysis']['as_of']}  ",
        f"**Window:** {data['analysis']['window_start']} to {data['analysis']['window_end']}",
        "",
        "The analysis uses three complementary classifications:",
        "",
        "1. **Broad sector** — a GICS-like grouping used for the dashboard.",
        "2. **Industry** — the Prime Standard sector published alongside the DAX composition.",
        "3. **NACE** — the official EU statistical classification of economic activities "
        "(NACE Rev. 2.1), at section and division level. NACE describes what a company does, "
        "not how it trades; conglomerates are assigned by their primary activity.",
        "",
        "## Sector ranking",
        "",
        table(
            ["Sector", "n", "1M", "3M", "YTD", "1Y", "Vol", "Max DD", "% > 50d", "Median 1Y"],
            [
                [
                    row["name"], fmt_int(row["n_constituents"]), pct(row["ret_1m"]),
                    pct(row["ret_3m"]), pct(row["ret_ytd"]), pct(row["ret_1y"]),
                    pct(row["ann_vol"], 1, False), pct(row["max_dd"]),
                    pct(row["pct_above_sma50"], 0, False), pct(row["median_ret_1y"]),
                ]
                for row in sorted_rows(sectors, "ret_1y")
            ],
            ["left", "right", "right", "right", "right", "right", "right", "right", "right", "right"],
        ),
        "",
        "![Sector performance](charts/performance_sectors.svg)",
        "",
        "## NACE sections",
        "",
        "NACE section level aggregates the DAX 40 by the first letter of the NACE code. "
        "Some sections contain only one or two companies; the `n` column makes this explicit.",
        "",
        table(
            ["NACE section", "n", "1M", "3M", "YTD", "1Y", "Vol", "Max DD", "% > 50d"],
            [
                [
                    row["name"], fmt_int(row["n_constituents"]), pct(row["ret_1m"]),
                    pct(row["ret_3m"]), pct(row["ret_ytd"]), pct(row["ret_1y"]),
                    pct(row["ann_vol"], 1, False), pct(row["max_dd"]),
                    pct(row["pct_above_sma50"], 0, False),
                ]
                for row in sorted_rows(sections, "ret_1y")
            ],
            ["left", "right", "right", "right", "right", "right", "right", "right", "right"],
        ),
        "",
        "## NACE divisions",
        "",
        "Division level is more granular (2-digit NACE). Some divisions contain only one company, "
        "so the aggregate is effectively that company; the `n` column makes this explicit.",
        "",
        table(
            ["NACE division", "n", "1Y", "Vol", "Max DD", "% > 50d", "Median 1Y"],
            [
                [
                    row["name"], fmt_int(row["n_constituents"]), pct(row["ret_1y"]),
                    pct(row["ann_vol"], 1, False), pct(row["max_dd"]),
                    pct(row["pct_above_sma50"], 0, False), pct(row["median_ret_1y"]),
                ]
                for row in sorted_rows(divisions, "ret_1y")
            ],
            ["left", "right", "right", "right", "right", "right", "right"],
        ),
        "",
        "## Industries",
        "",
        table(
            ["Industry", "n", "1Y", "Vol", "Max DD", "% > 50d", "Median 1Y"],
            [
                [
                    row["name"], fmt_int(row["n_constituents"]), pct(row["ret_1y"]),
                    pct(row["ann_vol"], 1, False), pct(row["max_dd"]),
                    pct(row["pct_above_sma50"], 0, False), pct(row["median_ret_1y"]),
                ]
                for row in sorted_rows(industries, "ret_1y")
            ],
            ["left", "right", "right", "right", "right", "right", "right"],
        ),
        "",
        "## Sector correlations",
        "",
        f"The average pairwise correlation of daily sector returns was **{average_corr:.2f}**. "
        "Higher values mean sector diversification was less effective during the window.",
        "",
        "**Most correlated pairs**",
        "",
        table(["Pair", "Correlation"], [pair_label(pair) for pair in highest], ["left", "right"]),
        "",
        "**Least correlated pairs**",
        "",
        table(["Pair", "Correlation"], [pair_label(pair) for pair in lowest], ["left", "right"]),
        "",
        "![Sector correlation heatmap](charts/correlation_heatmap.svg)",
        "",
        "## What the facts show",
        "",
        f"- Leadership was concentrated in **{sector_ranked[0]['name'] if sector_ranked else '—'}**; "
        f"the weakest sector was **{sector_ranked[-1]['name'] if sector_ranked else '—'}**.",
        f"- At NACE section level, **{section_ranked[0]['name'] if section_ranked else '—'}** led and "
        f"**{section_ranked[-1]['name'] if section_ranked else '—'}** lagged over 12 months.",
        f"- **{sum(1 for row in sectors if (row['ret_ytd'] or 0) > 0)} of {len(sectors)}** sectors "
        f"had a positive year-to-date return.",
        f"- **{sum(1 for row in sectors if (row['pct_above_sma200'] or 0) >= 0.5)} of {len(sectors)}** "
        f"sectors had at least half of their constituents above the 200-day moving average.",
        "",
        "> NACE is a statistical activity classification. It is not an investment sector "
        "classification and does not account for revenue mix, geography or valuation. "
        "The mapping used here is documented in `config/nace_map.tsv`.",
        "",
    ]
    (REPORTS_DIR / "sectors.md").write_text("\n".join(lines), encoding="utf-8")


def report_companies(data):
    metrics = data["metrics"]
    by_id = data["metrics_by_id"]
    equities = by_level(metrics, "equity")
    sectors = by_id
    ranked = sorted(equities, key=lambda row: row["ret_1y"] or -9, reverse=True)
    positive = sum(1 for row in equities if (row["ret_1y"] or 0) > 0)
    values = [row["ret_1y"] for row in equities if row["ret_1y"] is not None]

    relative = []
    for row in equities:
        sector = by_id.get(f"SECTOR:{row['sector']}")
        if sector and row["ret_1y"] is not None and sector["ret_1y"] is not None:
            relative.append((row, row["ret_1y"] - sector["ret_1y"]))

    lines = [
        "# DAX 40 companies",
        "",
        f"**As of:** {data['analysis']['as_of']}  ",
        f"**Window:** {data['analysis']['window_start']} to {data['analysis']['window_end']}",
        "",
        f"**{positive} of {len(equities)}** companies delivered a positive one-year price return. "
        f"The median was **{pct(statistics.median(values))}**, the interquartile range "
        f"**{pct(statistics.quantiles(values, n=4)[0])} to {pct(statistics.quantiles(values, n=4)[2])}**.",
        "",
        "## Full ranking",
        "",
        table(
            ["#", "Company", "Sector", "NACE", "Last", "1D", "1M", "YTD", "1Y", "Vol", "Max DD", "From high", "Trend", "Beta"],
            [
                [
                    index + 1, row["name"], row["sector"], row["nace_code"],
                    fnum(row["last"]), pct(row["ret_1d"]), pct(row["ret_1m"]),
                    pct(row["ret_ytd"]), pct(row["ret_1y"]), pct(row["ann_vol"], 1, False),
                    pct(row["max_dd"]), pct(row["pct_from_high"]), row["trend"],
                    "—" if row["beta_dax"] is None else f"{row['beta_dax']:.2f}",
                ]
                for index, row in enumerate(ranked)
            ],
            ["right", "left", "left", "left", "right", "right", "right", "right", "right", "right", "right", "right", "left", "right"],
        ),
        "",
        "![DAX 40 one-year returns](charts/company_ranking.svg)",
        "",
        "## Leaders and laggards",
        "",
        "**Top 5 by one-year return**",
        "",
        table(
            ["Company", "Sector", "1Y", "YTD", "Vol", "Max DD"],
            [
                [row["name"], row["sector"], pct(row["ret_1y"]), pct(row["ret_ytd"]),
                 pct(row["ann_vol"], 1, False), pct(row["max_dd"])]
                for row in ranked[:5]
            ],
            ["left", "left", "right", "right", "right", "right"],
        ),
        "",
        "**Bottom 5 by one-year return**",
        "",
        table(
            ["Company", "Sector", "1Y", "YTD", "Vol", "Max DD"],
            [
                [row["name"], row["sector"], pct(row["ret_1y"]), pct(row["ret_ytd"]),
                 pct(row["ann_vol"], 1, False), pct(row["max_dd"])]
                for row in ranked[-5:]
            ],
            ["left", "left", "right", "right", "right", "right"],
        ),
        "",
        "## Performance versus own sector",
        "",
        "A positive value means the company outperformed its equal-weighted sector index over the window.",
        "",
        table(
            ["Company", "Sector", "1Y return", "Sector 1Y", "Relative"],
            [
                [row["name"], row["sector"], pct(row["ret_1y"]), pct(by_id[f"SECTOR:{row['sector']}"]["ret_1y"]),
                 pct(difference)]
                for row, difference in sorted(relative, key=lambda item: -item[1])[:10]
            ],
            ["left", "left", "right", "right", "right"],
        ),
        "",
        table(
            ["Company", "Sector", "1Y return", "Sector 1Y", "Relative"],
            [
                [row["name"], row["sector"], pct(row["ret_1y"]), pct(by_id[f"SECTOR:{row['sector']}"]["ret_1y"]),
                 pct(difference)]
                for row, difference in sorted(relative, key=lambda item: item[1])[:10]
            ],
            ["left", "left", "right", "right", "right"],
        ),
        "",
        "## Risk versus return",
        "",
        "![Risk versus return](charts/risk_return.svg)",
        "",
        "## What the facts show",
        "",
        f"- The best one-year performer was **{ranked[0]['name']}** ({pct(ranked[0]['ret_1y'])}); "
        f"the weakest was **{ranked[-1]['name']}** ({pct(ranked[-1]['ret_1y'])}).",
        f"- **{sum(1 for row in equities if (row['pct_from_high'] or 0) > -0.05)} of {len(equities)}** "
        f"companies traded within 5% of their 52-week high.",
        f"- **{sum(1 for row in equities if row['trend'] == 'Uptrend')} of {len(equities)}** "
        f"companies were in an uptrend (above the 200-day SMA with the 50-day above the 200-day).",
        f"- The highest annualised volatility was **{max(equities, key=lambda row: row['ann_vol'] or 0)['name']}** "
        f"({pct(max(equities, key=lambda row: row['ann_vol'] or 0)['ann_vol'], 1, False)}); "
        f"the lowest was **{min(equities, key=lambda row: row['ann_vol'] or 9)['name']}** "
        f"({pct(min(equities, key=lambda row: row['ann_vol'] or 9)['ann_vol'], 1, False)}).",
        "",
        "> Company returns are price returns and exclude dividends. The window is approximately "
        "one year of trading sessions; short windows make company-level statistics noisy.",
        "",
    ]
    (REPORTS_DIR / "companies.md").write_text("\n".join(lines), encoding="utf-8")


def report_outlook(data):
    metrics = data["metrics"]
    by_id = data["metrics_by_id"]
    forward = data["forward"]
    breadth = data["breadth"]
    latest = latest_breadth(breadth)
    indices = by_level(metrics, "index")
    sectors = by_level(metrics, "sector")
    focus = indices + sectors
    dax = by_id.get("DAX")
    de10y = by_id.get("DE10Y")

    def unconditional_cells(row):
        return [
            row["name"],
            pct(row["median"]), pct(row["p10"]), pct(row["p90"]),
            pct(row["prob_positive"], 0, False),
        ]

    unconditional_rows = []
    conditional_rows = []
    for item in focus:
        row_3m = forward_all(forward, item["series_id"], 63)
        row_6m = forward_all(forward, item["series_id"], 126)
        if not row_3m or not row_6m:
            continue
        unconditional_rows.append(
            [
                item["name"],
                pct(row_3m["median"]), pct(row_3m["p10"]), pct(row_3m["p90"]),
                pct(row_3m["prob_positive"], 0, False),
                pct(row_6m["median"]), pct(row_6m["p10"]), pct(row_6m["p90"]),
                pct(row_6m["prob_positive"], 0, False),
            ]
        )
        cond_3m = forward_conditional(forward, item["series_id"], 63, item)
        cond_6m = forward_conditional(forward, item["series_id"], 126, item)
        if cond_3m and cond_6m:
            conditional_rows.append(
                [
                    item["name"], cond_3m["condition_value"],
                    fmt_int(cond_3m["n"]), pct(cond_3m["median"]), pct(cond_3m["p10"]),
                    pct(cond_3m["p90"]), pct(cond_3m["prob_positive"], 0, False),
                    fmt_int(cond_6m["n"]), pct(cond_6m["median"]), pct(cond_6m["p10"]),
                    pct(cond_6m["p90"]), pct(cond_6m["prob_positive"], 0, False),
                ]
            )

    # Scenario table for the DAX and the strongest/weakest sector.
    scenario_ids = ["DAX"]
    if sectors:
        scenario_ids.append(sorted(sectors, key=lambda row: row["ret_1y"] or -9)[-1]["series_id"])
        scenario_ids.append(sorted(sectors, key=lambda row: row["ret_1y"] or 9)[0]["series_id"])
    scenario_rows = []
    for series_id in scenario_ids:
        item = by_id.get(series_id)
        if not item:
            continue
        row_3m = forward_all(forward, series_id, 63)
        row_6m = forward_all(forward, series_id, 126)
        if not row_3m or not row_6m:
            continue
        scenario_rows.append(
            [
                item["name"],
                pct(row_3m["p10"]), pct(row_3m["median"]), pct(row_3m["p90"]),
                pct(row_6m["p10"]), pct(row_6m["median"]), pct(row_6m["p90"]),
            ]
        )

    positive_conditional = sum(
        1 for row in conditional_rows if (row[3] or "").startswith("+")
    )
    above_200 = sum(1 for row in sectors if row["trend"] == "Uptrend")

    lines = [
        "# Forward-looking base rates",
        "",
        "> **This is not a forecast and not investment advice.** The tables below show what "
        "happened historically when a series was in a similar state. They are empirical "
        "base rates from a short sample, not predictions.",
        "",
        f"**As of:** {data['analysis']['as_of']}  ",
        f"**Window:** {data['analysis']['window_start']} to {data['analysis']['window_end']}",
        "",
        "## Method",
        "",
        "For each series and horizon (3 and 6 months), the analysis computes every "
        "historical forward return in the available sample and summarises its distribution. "
        "It then repeats the calculation conditional on the current state:",
        "",
        "- **Trend:** above or below the 200-day moving average.",
        "- **Drawdown:** current distance from the 252-day high, bucketed.",
        "- **Volatility:** current 20-day annualised volatility versus its own median.",
        "",
        "Overlapping forward windows are not independent, so the effective sample is smaller "
        "than the raw `n`. Rows with fewer than 15 observations are omitted from the conditional "
        "tables. A one-year window is a small sample; treat the numbers as context, not precision.",
        "",
        "## Current regime",
        "",
        table(
            ["Series", "Trend", "Drawdown", "Vol regime", "3M momentum", "6M momentum", "1Y return"],
            [
                [
                    row["name"], row["trend"], pct(row["current_dd"]), row["vol_regime"],
                    pct(row["momentum_3m"]), pct(row["momentum_6m"]), pct(row["ret_1y"]),
                ]
                for row in focus
            ],
            ["left", "left", "right", "left", "right", "right", "right"],
        ),
        "",
        "## Unconditional 3M and 6M base rates",
        "",
        "The columns show the median, 10th percentile, 90th percentile and the share of "
        "positive outcomes across all historical windows in the sample.",
        "",
        table(
            ["Series", "3M median", "3M p10", "3M p90", "3M P(+)", "6M median", "6M p10", "6M p90", "6M P(+)"],
            unconditional_rows,
            ["left", "right", "right", "right", "right", "right", "right", "right", "right"],
        ),
        "",
        "## Conditional on the current trend",
        "",
        "These rows use only historical observations with the same trend state (above/below the "
        "200-day SMA) as today.",
        "",
        table(
            ["Series", "Current trend", "3M n", "3M median", "3M p10", "3M p90", "3M P(+)", "6M n", "6M median", "6M p10", "6M p90", "6M P(+)"],
            conditional_rows,
            ["left", "left", "right", "right", "right", "right", "right", "right", "right", "right", "right", "right"],
        ),
        "",
        "## Scenario ranges",
        "",
        "Bear, base and bull cases are the 10th percentile, median and 90th percentile of the "
        "historical forward-return distribution. They are not targets.",
        "",
        table(
            ["Series", "3M bear", "3M base", "3M bull", "6M bear", "6M base", "6M bull"],
            scenario_rows,
            ["left", "right", "right", "right", "right", "right", "right"],
        ),
        "",
        "## What the facts suggest",
        "",
        f"- **Trend:** {above_200} of {len(sectors)} sectors were in an uptrend at the end of the window; "
        f"the DAX itself was in an **{dax['trend'].lower()}**.",
        f"- **Breadth:** {pct(latest['above_sma50_pct'], 0, False)} of DAX 40 constituents were above "
        f"their 50-day SMA and {pct(latest['above_sma200_pct'], 0, False)} above their 200-day SMA.",
        f"- **Momentum:** the strongest 3-month sector momentum was **{max(sectors, key=lambda row: row['momentum_3m'] or -9)['name']}** "
        f"({pct(max(sectors, key=lambda row: row['momentum_3m'] or -9)['momentum_3m'])}); "
        f"the weakest was **{min(sectors, key=lambda row: row['momentum_3m'] or 9)['name']}** "
        f"({pct(min(sectors, key=lambda row: row['momentum_3m'] or 9)['momentum_3m'])}).",
        f"- **Conditional base rates:** {positive_conditional} of {len(conditional_rows)} index/sector "
        f"series had a positive median 3-month return when conditioned on their current trend state.",
        f"- **Rates:** the 10-year Bund yield was **{fnum(de10y['last'])}%**, "
        f"**{bps(de10y['change_bps_1y'])}** over 12 months. Higher yields raise the discount rate "
        f"for long-duration equities and can compress valuation multiples.",
        "",
        "## What would change the view",
        "",
        "These are factual monitoring triggers, not predictions:",
        "",
        f"- The DAX closing below its 200-day SMA ({fnum(dax['sma200'])}).",
        f"- DAX 40 breadth above the 50-day SMA falling below 40% (latest: "
        f"{pct(latest['above_sma50_pct'], 0, False)}).",
        f"- The 10-year Bund yield rising more than 50 bps above its current level "
        f"({fnum(de10y['last'] + 0.5)}%).",
        f"- The strongest sector's 3-month momentum turning negative.",
        f"- A sharp rise in the average pairwise sector correlation, which would indicate "
        f"that diversification is breaking down.",
        "",
        "## Limitations",
        "",
        "- The sample covers roughly one to two years of daily data; regime-specific samples are small.",
        "- Overlapping forward windows are autocorrelated, so `n` overstates independent information.",
        "- Price returns exclude dividends, buybacks and corporate actions beyond the adjusted series.",
        "- NACE and sector labels are static; index composition and classifications can change.",
        "- The source is an unofficial CNBC endpoint with delayed quotes.",
        "",
    ]
    (REPORTS_DIR / "outlook.md").write_text("\n".join(lines), encoding="utf-8")


def report_risk(data):
    metrics = data["metrics"]
    by_id = data["metrics_by_id"]
    indices = by_level(metrics, "index")
    sectors = by_level(metrics, "sector")
    equities = by_level(metrics, "equity")
    correlations = data["correlations"]
    constituents = data["constituents"]

    sector_ids = [row["series_id"] for row in sectors]
    pairs = correlation_pairs(correlations, sector_ids)
    average_corr = statistics.fmean([p[2] for p in pairs]) if pairs else None

    def pair_label(pair):
        left = by_id.get(pair[0], {}).get("name", pair[0])
        right = by_id.get(pair[1], {}).get("name", pair[1])
        return [f"{left} / {right}", f"{pair[2]:.2f}"]

    highest = sorted(pairs, key=lambda item: -item[2])[:5]
    lowest = sorted(pairs, key=lambda item: item[2])[:5]

    lines = [
        "# Risk, drawdowns and correlations",
        "",
        f"**As of:** {data['analysis']['as_of']}  ",
        f"**Window:** {data['analysis']['window_start']} to {data['analysis']['window_end']}",
        "",
        "## Volatility and drawdowns",
        "",
        table(
            ["Series", "Level", "Ann. vol", "Max drawdown", "Drawdown peak", "Drawdown trough", "VaR 95", "CVaR 95"],
            [
                [
                    row["name"], row["level"], pct(row["ann_vol"], 1, False), pct(row["max_dd"]),
                    row["dd_peak_date"] or "—", row["dd_trough_date"] or "—",
                    pct(row["var95"]), pct(row["cvar95"]),
                ]
                for row in sorted(indices + sectors, key=lambda row: -(row["ann_vol"] or 0))
            ],
            ["left", "left", "right", "right", "left", "left", "right", "right"],
        ),
        "",
        "![Drawdowns](charts/drawdowns.svg)",
        "",
        "## Rolling volatility",
        "",
        "![Rolling volatility](charts/rolling_vol.svg)",
        "",
        "## Company risk ranking",
        "",
        "**Highest annualised volatility**",
        "",
        table(
            ["Company", "Sector", "Vol", "Max DD", "1Y return"],
            [
                [row["name"], row["sector"], pct(row["ann_vol"], 1, False), pct(row["max_dd"]), pct(row["ret_1y"])]
                for row in sorted(equities, key=lambda row: -(row["ann_vol"] or 0))[:10]
            ],
            ["left", "left", "right", "right", "right"],
        ),
        "",
        "**Deepest maximum drawdowns**",
        "",
        table(
            ["Company", "Sector", "Max DD", "Peak", "Trough", "1Y return"],
            [
                [row["name"], row["sector"], pct(row["max_dd"]), row["dd_peak_date"] or "—",
                 row["dd_trough_date"] or "—", pct(row["ret_1y"])]
                for row in sorted(equities, key=lambda row: row["max_dd"] or 0)[:10]
            ],
            ["left", "left", "right", "left", "left", "right"],
        ),
        "",
        "## Correlation and diversification",
        "",
        f"The average pairwise correlation of daily sector returns was **{average_corr:.2f}**. "
        "A higher average means sectors moved together and diversification was weaker.",
        "",
        "**Most correlated sector pairs**",
        "",
        table(["Pair", "Correlation"], [pair_label(pair) for pair in highest], ["left", "right"]),
        "",
        "**Least correlated sector pairs**",
        "",
        table(["Pair", "Correlation"], [pair_label(pair) for pair in lowest], ["left", "right"]),
        "",
        "![Correlation heatmap](charts/correlation_heatmap.svg)",
        "",
        "## Sector concentration",
        "",
        "Index weights are the published DAX weights from the composition source. They show "
        "which sectors dominate the index and therefore the index-level risk. The source does "
        "not publish weights for GEA and Scout24; the table therefore sums to 99.5%.",
        "",
        table(
            ["Sector", "Constituents", "Index weight"],
            sector_weight_table(constituents),
            ["left", "right", "right"],
        ),
        "",
        "## Rates risk",
        "",
        f"The 10-year Bund yield had an annualised daily-change volatility of "
        f"**{fnum(by_id['DE10Y']['ann_vol_bps'], 0)} bps** and moved "
        f"**{bps(by_id['DE10Y']['change_bps_1y'])}** over 12 months. "
        "Bond yields are a key discount-rate input for equity valuations.",
        "",
        "## Notes",
        "",
        "- VaR 95 is the 5th percentile of daily returns; CVaR 95 is the average of returns at or below it.",
        "- Maximum drawdown is measured on daily closes within the analysis window.",
        "- Company statistics use roughly one year of sessions and are therefore noisy.",
        "",
    ]
    (REPORTS_DIR / "risk.md").write_text("\n".join(lines), encoding="utf-8")


def report_data_quality(data):
    collection = data["collection"]
    analysis = data["analysis"]
    series = collection["series"]
    errors = [row for row in series if row.get("error")]
    stale = [
        row for row in series
        if row.get("end") and row["end"] < analysis["as_of"]
    ]
    duplicates = [row for row in series if (row.get("duplicates_removed") or 0) > 0]
    violations = [row for row in series if (row.get("ohlc_violations") or 0) > 0]

    def missing_threshold(row):
        expected = row.get("expected_business_days") or 0
        return max(10.0, 0.05 * expected)

    missing = [
        row for row in series
        if (row.get("missing_business_days") or 0) > missing_threshold(row)
    ]

    lines = [
        "# Data quality and coverage",
        "",
        f"**Collected at:** {collection['generated_at']}  ",
        f"**Market as of:** {collection['as_of']}  ",
        f"**Series:** {collection['successful_series']}/{collection['series_count']} successful  ",
        f"**Rows:** {collection['row_count']:,}",
        "",
        "## Summary",
        "",
        table(
            ["Check", "Count"],
            [
                ["Series with errors", len(errors)],
                ["Series whose last bar is before the market date", len(stale)],
                ["Series with duplicate dates removed", len(duplicates)],
                ["Series with OHLC violations corrected", len(violations)],
                ["Series missing more than 5% of business days (min 10)", len(missing)],
            ],
            ["left", "right"],
        ),
        "",
        "## Issues",
        "",
    ]
    if errors or stale or duplicates or violations or missing:
        issue_rows = []
        for row in errors:
            issue_rows.append([row["series_id"], "error", row.get("error") or "—"])
        for row in stale:
            issue_rows.append([row["series_id"], "stale", f"last bar {row['end']} vs {analysis['as_of']}"])
        for row in duplicates:
            issue_rows.append([row["series_id"], "duplicates", str(row["duplicates_removed"])])
        for row in violations:
            issue_rows.append([row["series_id"], "OHLC", str(row["ohlc_violations"])])
        for row in missing:
            issue_rows.append([row["series_id"], "missing days", str(row["missing_business_days"])])
        lines.append(table(["Series", "Issue", "Detail"], issue_rows, ["left", "left", "left"]))
    else:
        lines.append("No issues detected.")
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            table(
                ["Series", "Rows", "Start", "End", "Missing business days", "Duplicates", "OHLC fixes", "Quote appended"],
                [
                    [
                        row["series_id"], fmt_int(row["rows"]), row.get("start") or "—",
                        row.get("end") or "—", fmt_int(row.get("missing_business_days")),
                        fmt_int(row.get("duplicates_removed")), fmt_int(row.get("ohlc_violations")),
                        "yes" if row.get("quote_appended") else "no",
                    ]
                    for row in series
                ],
                ["left", "right", "left", "left", "right", "right", "right", "left"],
            ),
            "",
            "## Notes",
            "",
            "- The collector is throttled to one request per second and retries HTTP 429/5xx with "
            "exponential backoff. Responses are cached for six hours; `--refresh` forces a new download.",
            "- `missing_business_days` compares calendar weekdays with observed bars. Exchange holidays, "
            "half-days and source gaps explain most differences; it is not an error count. The issue "
            "threshold is 5% of expected business days (minimum 10 days).",
            "- OHLC consistency is only counted for equities and indices. FX and rate series use "
            "different high/low conventions; their closes are unaffected.",
            "- The analysis uses the DAX trading calendar as the reference. FX/rate weekend dates "
            "remain in the raw TSV but are excluded from correlations, breadth and group indices.",
            "- The DAX chart endpoint can lag the latest session by one day. The collector appends the "
            "current quote when its date is newer than the last daily bar.",
            "- CNBC quotes are delayed and the endpoints are unofficial; the pipeline is designed to fail "
            "softly and keep the last successful data.",
            "- Wikipedia is used only for the DAX composition and sector labels; the NACE mapping is a "
            "curated static file in `config/nace_map.tsv`.",
            "",
        ]
    )
    (REPORTS_DIR / "data_quality.md").write_text("\n".join(lines), encoding="utf-8")


def report_index(data):
    analysis = data["analysis"]
    lines = [
        "# German financial markets — report index",
        "",
        f"**Market data as of:** {analysis['as_of']} · generated {date.today().isoformat()}  ",
        f"**Analysis window:** {analysis['window_start']} to {analysis['window_end']} "
        f"({analysis['window_days']} trading sessions)",
        "",
        "## Reports",
        "",
        "| Report | Contents |",
        "|---|---|",
        "| [Market overview](market_overview.md) | Index and sector dashboard, breadth, rates and FX |",
        "| [Sectors, industries and NACE](sectors.md) | Sector/industry/NACE rankings, correlations |",
        "| [DAX 40 companies](companies.md) | Full company ranking, leaders, laggards, relative performance |",
        "| [Outlook](outlook.md) | Empirical forward-return base rates and scenario ranges |",
        "| [Risk](risk.md) | Volatility, drawdowns, tail risk, correlations, concentration |",
        "| [Data quality](data_quality.md) | Coverage, gaps, duplicates, stale series |",
        "| [Fundamentals, distress and investment](fundamentals.md) | DAX/MDAX/SDAX survival, growth, valuation and investment scores; five-year scenarios |",
        "",
        "## Web dashboard",
        "",
        "A generated, dependency-free dashboard for the DAX 40, MDAX and SDAX is "
        "available at [`web/index.html`](../web/index.html). It includes a plain-language "
        "reading guide, friendly score names and short metric explanations. Open it "
        "directly or run `python3 -m http.server 8000` and visit "
        "`http://localhost:8000/web/`.",
        "",
        "## Reproducing",
        "",
        "See [scripts/README.md](../scripts/README.md) for documentation of every script, the "
        "`scripts/run.sh` shell wrapper, the Makefile targets and the generated data schema. "
        "The short version is `make all` or `python3 scripts/run_all.py`. "
        "Fundamentals can be regenerated separately with `python3 scripts/fundamentals.py all`.",
        "",
        "## Data",
        "",
        "| File | Description |",
        "|---|---|",
        "| `data/german_markets.tsv` | Main long-format dataset: daily OHLCV plus classification columns |",
        "| `data/dax_constituents.tsv` | DAX 40 composition with industry, broad sector and NACE codes |",
        "| `data/metrics.tsv` | One row per series/group with return, risk and trend metrics |",
        "| `data/forward_base_rates.tsv` | Empirical forward-return distributions by horizon and condition |",
        "| `data/correlations.tsv` | Pairwise daily-return correlations |",
        "| `data/monthly_returns.tsv` | Month-end returns |",
        "| `data/breadth.tsv` | DAX 40 breadth over time |",
        "| `data/sector_breadth.tsv` | Current breadth by sector/industry/NACE |",
        "| `data/fundamentals.tsv` | Statements for the DAX 40, MDAX and SDAX universe |",
        "| `data/fundamentals_quarterly.tsv` | Quarterly income, balance-sheet and cash-flow statements |",
        "| `data/forecasts.tsv` | Analyst forward revenue and EPS estimates |",
        "| `data/fundamentals_metrics.tsv` | Distress, growth, valuation and WACC metrics |",
        "| `data/fundamentals_scores.tsv` | Survival, growth, valuation and investment scores |",
        "| `data/fundamentals_scenarios.tsv` | Bear/base/bull/weighted five-year scenarios |",
        "| `data/collection_manifest.json` | Fetch log, row counts and validation flags |",
        "| `data/analysis_manifest.json` | Analysis window and row counts |",
        "| `data/fundamentals_manifest.json` | Fundamentals collection coverage |",
        "| `data/fundamentals_analysis_manifest.json` | Score weights and assumptions |",
        "",
        "## Method",
        "",
        "1. **Collect** daily OHLCV from CNBC for five German indices, 40 DAX constituents, "
        "EUR/USD and the German 10-year Bund yield. The collector is rate-limited to one request "
        "per second and caches responses for six hours.",
        "2. **Align** every series to the DAX trading calendar so that correlations, breadth and "
        "group indices compare the same sessions. The analysis window is the last 252 DAX sessions.",
        "3. **Classify** each company by broad sector, Prime Standard industry and NACE Rev. 2.1 "
        "(section, division and class). NACE is the official EU activity classification; it "
        "describes what a company does, not how it trades.",
        "4. **Aggregate** equal-weighted sector, industry and NACE group indices from constituent "
        "daily returns.",
        "5. **Analyse** returns, volatility, Sharpe ratio, drawdowns, VaR/CVaR, moving averages, "
        "breadth, correlations and beta versus the DAX.",
        "6. **Project historically** by computing empirical forward-return distributions and "
        "conditioning them on the current trend, drawdown and volatility state.",
        "7. **Assess fundamentals separately** with a sector-aware scorecard: survival "
        "(Altman/Ohlson and sector ratios), business growth (revenue/EPS/FCF growth, ROIC−WACC, "
        "margins, R&D, backlog), valuation versus each company's own history, and a 40/30/30 "
        "investment score. Five-year bear/base/bull scenarios are illustrations, not forecasts. "
        "Quarterly statements are collected separately for transparency and are not used in the scorecard.",
        "",
        "## Metrics",
        "",
        "- Returns are simple price returns; the analysis window is the last 252 DAX trading sessions.",
        "- Annualised volatility is the standard deviation of daily returns times √252.",
        "- Sharpe uses a 2% annual risk-free rate.",
        "- Maximum drawdown is measured on daily closes from the running peak.",
        "- VaR 95 is the 5th percentile of daily returns; CVaR 95 is the mean of returns at or below it.",
        "- Beta is the covariance of daily returns with the DAX divided by DAX variance.",
        "- Breadth is the share of DAX 40 constituents above their 50/200-day moving average.",
        "",
        "## NACE mapping",
        "",
        "NACE codes are assigned at class level where the primary activity is clear, and at "
        "division level otherwise. The mapping lives in `config/nace_map.tsv` and is documented "
        "with the code, label, section and division. Examples:",
        "",
        "| Company | NACE | Activity |",
        "|---|---|---|",
        "| SAP | 58.29 | Other software publishing |",
        "| Siemens | 28.29 | Other general-purpose machinery |",
        "| Allianz | 65.12 | Other non-life insurance |",
        "| Deutsche Bank | 64.19 | Other monetary intermediation |",
        "| Volkswagen | 29.10 | Manufacture of motor vehicles |",
        "",
        "Conglomerates are assigned by their largest/primary activity; this is an analytical "
        "choice, not an official company register entry.",
        "",
        "## Caveats",
        "",
        "- CNBC is an unofficial endpoint; quotes are delayed and the API can change.",
        "- Price indices and price returns exclude dividends.",
        "- One year of daily data is a small sample for regime analysis; forward base rates are "
        "context, not forecasts.",
        "- Wikipedia composition data can lag index changes.",
        "- The NACE mapping is static and should be reviewed when index composition changes.",
        "- Fundamentals scores are relative scorecards, not calibrated probabilities. They are "
        "not historically validated against default or return labels because a consistent DAX 40 "
        "panel of such labels is unavailable.",
        "- Ohlson probabilities are relative indicators, not calibrated default probabilities. "
        "Sector scorecards for banks, insurers, utilities, telecoms, holdings, real estate, "
        "asset-light companies and financial infrastructure are transparent but judgment-based.",
        "- R&D, order backlog, debt-maturity schedules, market-share trends, organic growth and a "
        "complete set of external credit ratings are unavailable for all 40 companies; missing "
        "inputs are renormalised.",
        "- Auto leverage includes captive financial-services debt; where the split is identifiable, "
        "the score uses industrial debt/EBITDA and the report shows total leverage separately.",
        "- Quarterly statements are collected separately for transparency; the scorecard uses the "
        "annual/TTM dataset.",
        "- Five-year scenario returns are highly sensitive to the starting and exit multiples and "
        "are not target prices.",
        "",
        "## Disclaimer",
        "",
        "This project is for research and education. It is not investment advice, an offer, or a "
        "solicitation to buy or sell any security. Past performance does not guarantee future "
        "results. Forward-looking tables are historical base rates, not predictions. Verify all "
        "data before making decisions.",
        "",
    ]
    (REPORTS_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    data = load_all()
    report_market_overview(data)
    report_sectors(data)
    report_companies(data)
    report_outlook(data)
    report_risk(data)
    report_data_quality(data)
    report_index(data)
    print(f"Wrote 7 Markdown reports to {REPORTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
