#!/usr/bin/env python3
"""Analyse the collected German market data and write metrics/charts.

Outputs
-------
* data/metrics.tsv                 – one row per series/group with risk metrics
* data/correlations.tsv            – pairwise daily-return correlations
* data/monthly_returns.tsv         – month-end returns
* data/forward_base_rates.tsv      – empirical forward-return distributions
* data/breadth.tsv                 – DAX 40 breadth over time
* data/sector_breadth.tsv          – current breadth by sector/industry/NACE
* data/analysis_manifest.json      – window and coverage information
* reports/charts/*.svg             – self-contained SVG charts

The forward-return tables are historical base rates, not forecasts.  They
answer: "when a series was in a similar state in the past, what happened over
the next 1/3/6 months?"
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

from charts import (
    chart_breadth,
    chart_company_ranking,
    chart_correlation_heatmap,
    chart_drawdowns,
    chart_monthly_heatmap,
    chart_performance,
    chart_risk_return,
    chart_rolling_vol,
)
from lib import (
    CHARTS_DIR,
    DATA_DIR,
    REPORTS_DIR,
    ensure_dirs,
    excess_kurtosis,
    fmt_num,
    fmt_pct,
    mean,
    percentile,
    read_json,
    read_tsv,
    skewness,
    stdev,
    write_json,
    write_tsv,
)

RF_ANNUAL = 0.02  # risk-free rate used for the Sharpe ratio
TRADING_DAYS = 252
WINDOW = 252


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Series:
    series_id: str
    name: str
    asset_class: str
    group: str = ""
    industry: str = ""
    sector: str = ""
    nace_code: str = ""
    nace_label: str = ""
    nace_section: str = ""
    nace_section_label: str = ""
    nace_division: str = ""
    nace_division_label: str = ""
    unit: str = ""
    currency: str = ""
    dates: list[str] = field(default_factory=list)
    open: list[float] = field(default_factory=list)
    high: list[float] = field(default_factory=list)
    low: list[float] = field(default_factory=list)
    close: list[float] = field(default_factory=list)
    volume: list[float] = field(default_factory=list)

    @property
    def last(self) -> float:
        return self.close[-1]

    @property
    def n(self) -> int:
        return len(self.close)


def to_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_series() -> dict[str, Series]:
    rows = read_tsv(DATA_DIR / "german_markets.tsv")
    grouped: dict[str, list[dict]] = defaultdict(list)
    meta: dict[str, dict] = {}
    for row in rows:
        series_id = row["series_id"]
        grouped[series_id].append(row)
        meta[series_id] = {
            "name": row["name"],
            "asset_class": row["asset_class"],
            "group": row["group"],
            "industry": row.get("industry", ""),
            "sector": row.get("sector", ""),
            "nace_code": row.get("nace_code", ""),
            "nace_label": row.get("nace_label", ""),
            "nace_section": row.get("nace_section", ""),
            "nace_section_label": row.get("nace_section_label", ""),
            "nace_division": row.get("nace_division", ""),
            "nace_division_label": row.get("nace_division_label", ""),
            "unit": row["unit"],
            "currency": row["currency"],
        }

    series: dict[str, Series] = {}
    for series_id, items in grouped.items():
        items.sort(key=lambda item: item["date"])
        deduped: dict[str, dict] = {}
        for item in items:
            deduped[item["date"]] = item
        items = [deduped[key] for key in sorted(deduped)]
        series[series_id] = Series(
            series_id=series_id,
            dates=[item["date"] for item in items],
            open=[to_float(item["open"]) for item in items],
            high=[to_float(item["high"]) for item in items],
            low=[to_float(item["low"]) for item in items],
            close=[to_float(item["close"]) for item in items],
            volume=[to_float(item["volume"]) for item in items],
            **meta[series_id],
        )
    return series


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def returns_of(closes: list[float], sessions: int | None = None) -> list[float]:
    values = closes if sessions is None else closes[-sessions - 1:]
    return [
        values[i] / values[i - 1] - 1.0
        for i in range(1, len(values))
        if values[i - 1] > 0
    ]


def lookback_return(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions:
        return None
    return closes[-1] / closes[-1 - sessions] - 1.0


def sma(closes: list[float], sessions: int) -> float | None:
    if len(closes) < sessions:
        return None
    return mean(closes[-sessions:])


def rolling_vol(closes: list[float], window: int = 20) -> list[float | None]:
    output: list[float | None] = []
    for end in range(window, len(closes) + 1):
        values = returns_of(closes[:end], window)
        output.append(stdev(values) * math.sqrt(TRADING_DAYS) if len(values) > 1 else None)
    return output


def drawdown(closes: list[float], dates: list[str]) -> tuple[float | None, str | None, str | None]:
    if not closes:
        return None, None, None
    peak = closes[0]
    peak_date = dates[0]
    max_dd = 0.0
    dd_peak = dates[0]
    dd_trough = dates[0]
    for day, close in zip(dates, closes):
        if close > peak:
            peak = close
            peak_date = day
        current = close / peak - 1.0 if peak > 0 else 0.0
        if current < max_dd:
            max_dd = current
            dd_peak = peak_date
            dd_trough = day
    return max_dd, dd_peak, dd_trough


def ytd_return(dates: list[str], closes: list[float], as_of: str) -> float | None:
    year = int(as_of[:4])
    previous = [close for day, close in zip(dates, closes) if int(day[:4]) < year]
    if not previous:
        return None
    return closes[-1] / previous[-1] - 1.0


def compute_metrics(series: Series, as_of: str) -> dict:
    closes = series.close
    dates = series.dates
    n = len(closes)
    last = closes[-1]
    window_n = min(WINDOW, n)
    window_closes = closes[-window_n:]
    window_dates = dates[-window_n:]
    window_returns = returns_of(window_closes)

    ann_vol = stdev(window_returns) * math.sqrt(TRADING_DAYS) if len(window_returns) > 1 else None
    avg_daily = mean(window_returns)
    sharpe = None
    if ann_vol and ann_vol > 0 and avg_daily is not None:
        sharpe = (avg_daily * TRADING_DAYS - RF_ANNUAL) / ann_vol

    max_dd, dd_peak, dd_trough = drawdown(window_closes, window_dates)
    high_52w = max(window_closes)
    low_52w = min(window_closes)

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    vol_series = rolling_vol(closes, 20)
    recent_vols = [v for v in vol_series[-WINDOW:] if v is not None]
    current_vol = vol_series[-1] if vol_series else None
    median_vol = statistics.median(recent_vols) if recent_vols else None
    if current_vol is None or median_vol in (None, 0):
        vol_regime = "n/a"
    elif current_vol > median_vol * 1.1:
        vol_regime = "High vol"
    elif current_vol < median_vol * 0.9:
        vol_regime = "Low vol"
    else:
        vol_regime = "Normal vol"

    above_sma50 = last > sma50 if sma50 is not None else None
    above_sma200 = last > sma200 if sma200 is not None else None
    if above_sma200 is None:
        trend = "n/a"
    elif above_sma200 and (sma50 is None or sma50 > sma200):
        trend = "Uptrend"
    elif not above_sma200 and (sma50 is None or sma50 < sma200):
        trend = "Downtrend"
    else:
        trend = "Mixed"

    var95 = percentile(window_returns, 0.05)
    cvar95 = mean([r for r in window_returns if var95 is not None and r <= var95])

    metrics = {
        "series_id": series.series_id,
        "name": series.name,
        "level": series.asset_class,
        "group": series.group,
        "industry": series.industry,
        "sector": series.sector,
        "nace_code": series.nace_code,
        "nace_label": series.nace_label,
        "nace_section": series.nace_section,
        "nace_section_label": series.nace_section_label,
        "nace_division": series.nace_division,
        "nace_division_label": series.nace_division_label,
        "unit": series.unit,
        "currency": series.currency,
        "last": last,
        "last_date": dates[-1],
        "n_bars": n,
        "start": dates[0],
        "end": dates[-1],
        "ret_1d": lookback_return(closes, 1),
        "ret_1w": lookback_return(closes, 5),
        "ret_1m": lookback_return(closes, 21),
        "ret_3m": lookback_return(closes, 63),
        "ret_6m": lookback_return(closes, 126),
        "ret_ytd": ytd_return(dates, closes, as_of),
        "ret_1y": lookback_return(closes, 252),
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "dd_peak_date": dd_peak,
        "dd_trough_date": dd_trough,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pct_from_high": last / high_52w - 1.0 if high_52w > 0 else None,
        "current_dd": last / max(window_closes) - 1.0 if window_closes else None,
        "best_day": max(window_returns) if window_returns else None,
        "worst_day": min(window_returns) if window_returns else None,
        "pct_positive": (
            sum(1 for r in window_returns if r > 0) / len(window_returns)
            if window_returns
            else None
        ),
        "var95": var95,
        "cvar95": cvar95,
        "skew": skewness(window_returns),
        "kurtosis": excess_kurtosis(window_returns),
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "above_sma50": above_sma50,
        "above_sma200": above_sma200,
        "trend": trend,
        "momentum_3m": lookback_return(closes, 63),
        "momentum_6m": lookback_return(closes, 126),
        "current_vol": current_vol,
        "median_vol": median_vol,
        "vol_regime": vol_regime,
        "n_constituents": 1,
        "pct_above_sma50": 1.0 if above_sma50 else 0.0,
        "pct_above_sma200": 1.0 if above_sma200 else 0.0,
        "pct_positive_ytd": 1.0 if (ytd_return(dates, closes, as_of) or 0) > 0 else 0.0,
        "median_ret_1y": lookback_return(closes, 252),
        "beta_dax": None,
    }

    # Rates: also report absolute changes in basis points.
    if series.asset_class == "rate":
        metrics["change_bps_1d"] = (last - closes[-2]) * 100 if n >= 2 else None
        metrics["change_bps_1m"] = (last - closes[-22]) * 100 if n >= 22 else None
        metrics["change_bps_1y"] = (last - closes[-253]) * 100 if n >= 253 else None
        metrics["change_bps_ytd"] = None
        previous_year = [c for d, c in zip(dates, closes) if int(d[:4]) < int(as_of[:4])]
        if previous_year:
            metrics["change_bps_ytd"] = (last - previous_year[-1]) * 100
        daily_bps = [
            (closes[i] - closes[i - 1]) * 100.0
            for i in range(1, n)
        ]
        window_bps = daily_bps[-WINDOW:]
        metrics["ann_vol_bps"] = (
            stdev(window_bps) * math.sqrt(TRADING_DAYS) if len(window_bps) > 1 else None
        )
    else:
        metrics["change_bps_1d"] = None
        metrics["change_bps_1m"] = None
        metrics["change_bps_1y"] = None
        metrics["change_bps_ytd"] = None
        metrics["ann_vol_bps"] = None

    return metrics


# ---------------------------------------------------------------------------
# Group indices (sector / industry / NACE)
# ---------------------------------------------------------------------------

def group_members(series: dict[str, Series]) -> dict[str, dict]:
    equities = [s for s in series.values() if s.asset_class == "equity"]
    groups: dict[str, dict] = {}

    def add(prefix: str, key: str, name: str, members: list[Series], level: str, **extra):
        if not members:
            return
        group_id = f"{prefix}:{key}"
        groups[group_id] = {
            "series_id": group_id,
            "name": name,
            "level": level,
            "members": members,
            **extra,
        }

    by_sector: dict[str, list[Series]] = defaultdict(list)
    by_industry: dict[str, list[Series]] = defaultdict(list)
    by_section: dict[str, list[Series]] = defaultdict(list)
    by_division: dict[str, list[Series]] = defaultdict(list)
    for equity in equities:
        if equity.sector:
            by_sector[equity.sector].append(equity)
        if equity.industry:
            by_industry[equity.industry].append(equity)
        if equity.nace_section:
            by_section[equity.nace_section].append(equity)
        if equity.nace_division:
            by_division[equity.nace_division].append(equity)

    for sector, members in sorted(by_sector.items()):
        add("SECTOR", sector, sector, members, "sector", sector=sector)
    for industry, members in sorted(by_industry.items()):
        sector = members[0].sector
        add("IND", industry, industry, members, "industry", industry=industry, sector=sector)
    for section, members in sorted(by_section.items()):
        label = members[0].nace_section_label
        add(
            "NACE",
            section,
            f"NACE {section} — {label}",
            members,
            "nace_section",
            nace_section=section,
            nace_section_label=label,
        )
    for division, members in sorted(by_division.items()):
        label = members[0].nace_division_label
        add(
            "NACE",
            division,
            f"NACE {division} — {label}",
            members,
            "nace_division",
            nace_division=division,
            nace_division_label=label,
        )
    return groups


def build_group_series(group: dict, series: dict[str, Series], dates_universe: list[str]) -> Series:
    member_returns: dict[str, dict[str, float]] = {}
    for member in group["members"]:
        mapping: dict[str, float] = {}
        for i in range(1, member.n):
            if member.close[i - 1] > 0:
                mapping[member.dates[i]] = member.close[i] / member.close[i - 1] - 1.0
        member_returns[member.series_id] = mapping

    dates: list[str] = []
    closes: list[float] = []
    level = 100.0
    for day in dates_universe:
        values = [
            member_returns[member.series_id].get(day)
            for member in group["members"]
            if day in member_returns[member.series_id]
        ]
        values = [v for v in values if v is not None]
        if not values:
            continue
        if not dates:
            dates.append(day)
            closes.append(level)
        else:
            level *= 1.0 + (mean(values) or 0.0)
            dates.append(day)
            closes.append(level)

    return Series(
        series_id=group["series_id"],
        name=group["name"],
        asset_class=group["level"],
        group=group.get("group", ""),
        industry=group.get("industry", ""),
        sector=group.get("sector", ""),
        nace_section=group.get("nace_section", ""),
        nace_section_label=group.get("nace_section_label", ""),
        nace_division=group.get("nace_division", ""),
        nace_division_label=group.get("nace_division_label", ""),
        unit="index",
        currency="EUR",
        dates=dates,
        open=list(closes),
        high=list(closes),
        low=list(closes),
        close=list(closes),
        volume=[0.0] * len(closes),
    )


# ---------------------------------------------------------------------------
# Correlations and beta
# ---------------------------------------------------------------------------

def daily_return_map(series: Series, window_dates: list[str]) -> dict[str, float]:
    allowed = set(window_dates)
    mapping: dict[str, float] = {}
    for i in range(1, series.n):
        if series.dates[i] in allowed and series.close[i - 1] > 0:
            mapping[series.dates[i]] = series.close[i] / series.close[i - 1] - 1.0
    return mapping


def pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 30:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx = mean(xs)
    my = mean(ys)
    if mx is None or my is None:
        return None
    cov = sum((x - mx) * (y - my) for x, y in pairs) / len(pairs)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / len(xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / len(ys))
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def beta_vs(reference: dict[str, float], other: dict[str, float]) -> float | None:
    common = sorted(set(reference) & set(other))
    if len(common) < 60:
        return None
    pairs = [(other[day], reference[day]) for day in common]
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx = mean(xs)
    my = mean(ys)
    if mx is None or my is None:
        return None
    var = sum((y - my) ** 2 for y in ys) / len(ys)
    if var == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in pairs) / len(pairs)
    return cov / var


# ---------------------------------------------------------------------------
# Forward base rates
# ---------------------------------------------------------------------------

def dd_bucket(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 0:
        return ">= 0%"
    if value >= -0.05:
        return "-5% to 0%"
    if value >= -0.10:
        return "-10% to -5%"
    return "< -10%"


def vol_bucket(current: float | None, median: float | None) -> str:
    if current is None or median in (None, 0):
        return "n/a"
    if current > median * 1.1:
        return "High vol"
    if current < median * 0.9:
        return "Low vol"
    return "Normal vol"


def dist_stats(values: list[float]) -> dict:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return {
        "n": len(clean),
        "mean": mean(clean),
        "median": percentile(clean, 0.5),
        "p10": percentile(clean, 0.10),
        "p25": percentile(clean, 0.25),
        "p75": percentile(clean, 0.75),
        "p90": percentile(clean, 0.90),
        "prob_positive": (sum(1 for v in clean if v > 0) / len(clean)) if clean else None,
    }


def forward_base_rates(series: Series) -> list[dict]:
    closes = series.close
    n = len(closes)
    if n < 120:
        return []

    sma200_at: list[float | None] = [None] * n
    for i in range(199, n):
        sma200_at[i] = mean(closes[i - 199:i + 1])

    dd_at: list[float | None] = [None] * n
    for i in range(n):
        peak = max(closes[max(0, i - 251):i + 1])
        dd_at[i] = closes[i] / peak - 1.0 if peak > 0 else None

    vol_at: list[float | None] = [None] * n
    for i in range(20, n):
        values = returns_of(closes[:i + 1], 20)
        vol_at[i] = stdev(values) * math.sqrt(TRADING_DAYS) if len(values) > 1 else None

    median_vol_at: list[float | None] = [None] * n
    for i in range(20, n):
        recent = [v for v in vol_at[max(0, i - 251):i + 1] if v is not None]
        median_vol_at[i] = statistics.median(recent) if recent else None

    last = n - 1
    current_trend = (
        "Above 200d SMA" if sma200_at[last] is not None and closes[last] > sma200_at[last]
        else "Below 200d SMA"
    )
    current_dd = dd_bucket(dd_at[last])
    current_vol = vol_bucket(vol_at[last], median_vol_at[last])

    trend_at = [
        None if sma200_at[i] is None
        else ("Above 200d SMA" if closes[i] > sma200_at[i] else "Below 200d SMA")
        for i in range(n)
    ]
    vol_at_bucket = [vol_bucket(vol_at[i], median_vol_at[i]) for i in range(n)]
    dd_at_bucket = [dd_bucket(dd_at[i]) for i in range(n)]

    output: list[dict] = []
    for horizon in (21, 63, 126):
        forward = [None] * n
        for i in range(n - horizon):
            if closes[i] > 0:
                forward[i] = closes[i + horizon] / closes[i] - 1.0
        valid = [i for i in range(n - horizon) if forward[i] is not None]
        unconditional = dist_stats([forward[i] for i in valid])
        rows = [
            {
                "condition_type": "All",
                "condition_value": "All history",
                "current_state": "—",
                **unconditional,
            }
        ]
        for condition_type, condition_series, current_value in (
            ("Trend", trend_at, current_trend),
            ("Drawdown", dd_at_bucket, current_dd),
            ("Volatility", vol_at_bucket, current_vol),
        ):
            selected = [
                forward[i]
                for i in valid
                if condition_series[i] == current_value
            ]
            stats = dist_stats(selected)
            if stats["n"] >= 15:
                rows.append(
                    {
                        "condition_type": condition_type,
                        "condition_value": current_value,
                        "current_state": current_value,
                        **stats,
                    }
                )
        for row in rows:
            output.append(
                {
                    "series_id": series.series_id,
                    "name": series.name,
                    "level": series.asset_class,
                    "sector": series.sector,
                    "nace_section": series.nace_section,
                    "horizon_days": horizon,
                    **row,
                }
            )
    return output


# ---------------------------------------------------------------------------
# Breadth
# ---------------------------------------------------------------------------

def breadth_series(equities: list[Series], window_dates: list[str]) -> list[dict]:
    # date -> close for each company
    closes_by_date = [{day: close for day, close in zip(s.dates, s.close)} for s in equities]
    output: list[dict] = []
    for day in window_dates:
        above50 = 0
        above200 = 0
        total = 0
        for series, mapping in zip(equities, closes_by_date):
            if day not in mapping:
                continue
            index = series.dates.index(day) if day in series.dates else -1
            if index < 0:
                continue
            closes = series.close[:index + 1]
            total += 1
            if len(closes) >= 50 and closes[-1] > mean(closes[-50:]):
                above50 += 1
            if len(closes) >= 200 and closes[-1] > mean(closes[-200:]):
                above200 += 1
        if total:
            output.append(
                {
                    "date": day,
                    "n": total,
                    "above_sma50_pct": above50 / total,
                    "above_sma200_pct": above200 / total,
                }
            )
    return output


def filter_to_calendar(series: dict[str, Series], calendar: list[str]) -> dict[str, Series]:
    """Keep only bars that fall on the reference trading calendar.

    The DAX calendar is used for the whole analysis so that correlations,
    breadth and group indices compare the same sessions.  FX and rate series
    can contain weekend dates; those are excluded here.
    """
    allowed = set(calendar)
    filtered: dict[str, Series] = {}
    for series_id, item in series.items():
        indices = [i for i, day in enumerate(item.dates) if day in allowed]
        if not indices:
            continue
        filtered[series_id] = replace(
            item,
            dates=[item.dates[i] for i in indices],
            open=[item.open[i] for i in indices],
            high=[item.high[i] for i in indices],
            low=[item.low[i] for i in indices],
            close=[item.close[i] for i in indices],
            volume=[item.volume[i] for i in indices],
        )
    return filtered


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ensure_dirs()
    raw_series = load_series()
    if not raw_series:
        raise SystemExit("No data found. Run scripts/collect.py first.")

    # Use the DAX trading calendar as the reference for every series.
    if "DAX" in raw_series:
        calendar = raw_series["DAX"].dates
    else:
        calendar = sorted({day for item in raw_series.values() for day in item.dates})
    series = filter_to_calendar(raw_series, calendar)
    if not series:
        raise SystemExit("No data left after applying the trading calendar.")

    as_of = calendar[-1]
    all_dates = calendar
    window_dates = all_dates[-WINDOW:]

    print(f"Loaded {len(series)} series, as of {as_of}, window {window_dates[0]}..{window_dates[-1]}")

    metrics: list[dict] = []
    metrics_by_id: dict[str, dict] = {}
    for series_id, item in sorted(series.items()):
        item_metrics = compute_metrics(item, as_of)
        metrics.append(item_metrics)
        metrics_by_id[series_id] = item_metrics

    # Group indices and metrics
    groups = group_members(series)
    group_series: dict[str, Series] = {}
    for group_id, group in groups.items():
        synthetic = build_group_series(group, series, all_dates)
        if synthetic.n < 30:
            continue
        group_series[group_id] = synthetic
        group_metrics = compute_metrics(synthetic, as_of)
        members = group["members"]
        member_metrics = [metrics_by_id[m.series_id] for m in members]
        group_metrics["n_constituents"] = len(members)
        group_metrics["pct_above_sma50"] = mean(
            [1.0 if m["above_sma50"] else 0.0 for m in member_metrics]
        )
        group_metrics["pct_above_sma200"] = mean(
            [1.0 if m["above_sma200"] else 0.0 for m in member_metrics]
        )
        group_metrics["pct_positive_ytd"] = mean(
            [1.0 if (m["ret_ytd"] or 0) > 0 else 0.0 for m in member_metrics]
        )
        group_metrics["median_ret_1y"] = statistics.median(
            [m["ret_1y"] for m in member_metrics if m["ret_1y"] is not None]
        ) if any(m["ret_1y"] is not None for m in member_metrics) else None
        group_metrics["sector"] = group.get("sector", "")
        group_metrics["industry"] = group.get("industry", "")
        group_metrics["nace_section"] = group.get("nace_section", "")
        group_metrics["nace_section_label"] = group.get("nace_section_label", "")
        group_metrics["nace_division"] = group.get("nace_division", "")
        group_metrics["nace_division_label"] = group.get("nace_division_label", "")
        metrics.append(group_metrics)
        metrics_by_id[group_id] = group_metrics

    # Correlations and beta
    return_maps = {
        series_id: daily_return_map(item, window_dates)
        for series_id, item in {**series, **group_series}.items()
    }
    dax_returns = return_maps.get("DAX", {})
    for series_id, item_metrics in metrics_by_id.items():
        if series_id == "DAX":
            continue
        if item_metrics["level"] in ("index", "equity", "sector", "industry", "nace_section", "nace_division"):
            item_metrics["beta_dax"] = beta_vs(dax_returns, return_maps.get(series_id, {}))

    correlation_rows: list[list] = []
    correlation_ids = [
        series_id
        for series_id in return_maps
        if metrics_by_id.get(series_id, {}).get("level")
        in ("index", "sector", "industry", "nace_section", "nace_division")
    ]
    for i, left in enumerate(correlation_ids):
        for right in correlation_ids[i + 1:]:
            common = sorted(set(return_maps[left]) & set(return_maps[right]))
            if len(common) < 60:
                continue
            corr = pearson([(return_maps[left][d], return_maps[right][d]) for d in common])
            if corr is not None:
                correlation_rows.append([left, right, f"{corr:.6f}", len(common)])
    write_tsv(
        DATA_DIR / "correlations.tsv",
        ["series_a", "series_b", "corr", "n"],
        correlation_rows,
    )

    # Monthly returns
    monthly_rows: list[list] = []
    for series_id, item in {**series, **group_series}.items():
        month_end: dict[str, float] = {}
        for day, close in zip(item.dates, item.close):
            month_end[day[:7]] = close
        months = sorted(month_end)
        for previous, current in zip(months, months[1:]):
            if month_end[previous] > 0:
                monthly_rows.append(
                    [
                        series_id,
                        current,
                        f"{month_end[current] / month_end[previous] - 1.0:.6f}",
                    ]
                )
    write_tsv(DATA_DIR / "monthly_returns.tsv", ["series_id", "month", "return"], monthly_rows)

    # Forward base rates
    forward_rows: list[dict] = []
    for series_id, item in {**series, **group_series}.items():
        if metrics_by_id.get(series_id, {}).get("level") in (
            "index", "equity", "sector", "industry", "nace_section", "nace_division"
        ):
            forward_rows.extend(forward_base_rates(item))
    forward_headers = [
        "series_id", "name", "level", "sector", "nace_section",
        "horizon_days", "condition_type", "condition_value", "current_state",
        "n", "mean", "median", "p10", "p25", "p75", "p90", "prob_positive",
    ]
    write_tsv(
        DATA_DIR / "forward_base_rates.tsv",
        forward_headers,
        [[row.get(header) for header in forward_headers] for row in forward_rows],
    )

    # Breadth
    equities = [s for s in series.values() if s.asset_class == "equity"]
    breadth_rows = breadth_series(equities, window_dates)
    write_tsv(
        DATA_DIR / "breadth.tsv",
        ["date", "n", "above_sma50_pct", "above_sma200_pct"],
        [
            [row["date"], row["n"], f"{row['above_sma50_pct']:.6f}", f"{row['above_sma200_pct']:.6f}"]
            for row in breadth_rows
        ],
    )

    # Current breadth by sector / industry / NACE
    breadth_group_rows: list[list] = []
    for group_id, group in groups.items():
        member_metrics = [metrics_by_id[m.series_id] for m in group["members"]]
        breadth_group_rows.append(
            [
                group_id,
                group["name"],
                group["level"],
                len(group["members"]),
                f"{mean([1.0 if m['above_sma50'] else 0.0 for m in member_metrics]) or 0:.6f}",
                f"{mean([1.0 if m['above_sma200'] else 0.0 for m in member_metrics]) or 0:.6f}",
                f"{mean([1.0 if (m['ret_ytd'] or 0) > 0 else 0.0 for m in member_metrics]) or 0:.6f}",
                f"{statistics.median([m['ret_1y'] for m in member_metrics if m['ret_1y'] is not None]):.6f}"
                if any(m["ret_1y"] is not None for m in member_metrics)
                else "",
            ]
        )
    write_tsv(
        DATA_DIR / "sector_breadth.tsv",
        ["group_id", "name", "level", "n_constituents", "above_sma50_pct", "above_sma200_pct", "positive_ytd_pct", "median_ret_1y"],
        breadth_group_rows,
    )

    # Metrics TSV
    metric_headers = [
        "series_id", "name", "level", "group", "industry", "sector",
        "nace_code", "nace_label", "nace_section", "nace_section_label",
        "nace_division", "nace_division_label", "unit", "currency",
        "last", "last_date", "n_bars", "start", "end",
        "ret_1d", "ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_ytd", "ret_1y",
        "ann_vol", "sharpe", "max_dd", "dd_peak_date", "dd_trough_date",
        "high_52w", "low_52w", "pct_from_high", "current_dd",
        "best_day", "worst_day", "pct_positive", "var95", "cvar95",
        "skew", "kurtosis", "sma20", "sma50", "sma200",
        "above_sma50", "above_sma200", "trend", "momentum_3m", "momentum_6m",
        "current_vol", "median_vol", "vol_regime",
        "n_constituents", "pct_above_sma50", "pct_above_sma200",
        "pct_positive_ytd", "median_ret_1y", "beta_dax",
        "change_bps_1d", "change_bps_1m", "change_bps_1y", "change_bps_ytd",
        "ann_vol_bps",
    ]

    def clean(value):
        if isinstance(value, float):
            return f"{value:.6f}"
        return "" if value is None else value

    write_tsv(
        DATA_DIR / "metrics.tsv",
        metric_headers,
        [[clean(row.get(header)) for header in metric_headers] for row in metrics],
    )

    manifest = {
        "as_of": as_of,
        "calendar": "DAX trading dates",
        "window_start": window_dates[0],
        "window_end": window_dates[-1],
        "window_days": len(window_dates),
        "series_count": len(series),
        "equity_count": len(equities),
        "group_count": len(group_series),
        "metric_rows": len(metrics),
        "correlation_rows": len(correlation_rows),
        "forward_rows": len(forward_rows),
        "risk_free_rate": RF_ANNUAL,
    }
    write_json(DATA_DIR / "analysis_manifest.json", manifest)

    # Charts
    print("Rendering charts ...")
    chart_performance(series, group_series, window_dates)
    chart_drawdowns(series, window_dates)
    chart_rolling_vol(series, window_dates)
    chart_correlation_heatmap(group_series, correlation_rows)
    chart_monthly_heatmap(series.get("DAX"), monthly_rows)
    chart_breadth(breadth_rows)
    chart_company_ranking(metrics, metrics_by_id)
    chart_risk_return(metrics)

    print(
        f"Wrote {len(metrics)} metric rows, {len(correlation_rows)} correlations, "
        f"{len(forward_rows)} forward-base-rate rows and 8 charts."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
