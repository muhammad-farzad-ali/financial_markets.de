"""SVG chart generation for the German markets reports."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from lib import CHARTS_DIR, fmt_num, fmt_pct, mean, stdev
from svg import (
    PALETTE,
    SVG,
    correlation_color,
    diverging_color,
    fmt_axis_num,
    fmt_axis_pct,
    frame,
    legend,
    nice_ticks,
)

TRADING_DAYS = 252


def save(svg: SVG, name: str) -> None:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    (CHARTS_DIR / name).write_text(svg.render(), encoding="utf-8")


def month_ticks(dates: list[str], count: int = 6) -> tuple[list[int], list[str]]:
    if not dates:
        return [], []
    if len(dates) <= count:
        indices = list(range(len(dates)))
    else:
        step = (len(dates) - 1) / (count - 1)
        indices = sorted({int(round(i * step)) for i in range(count)})
    return indices, [dates[i][:7] for i in indices]


def series_points(item, window_dates: list[str]) -> list[tuple[int, float]]:
    mapping = dict(zip(item.dates, item.close))
    return [(index, mapping[day]) for index, day in enumerate(window_dates) if day in mapping]


def normalized_points(item, window_dates: list[str]) -> list[tuple[int, float]]:
    points = series_points(item, window_dates)
    if not points:
        return []
    base = points[0][1]
    if base <= 0:
        return []
    return [(index, value / base * 100.0) for index, value in points]


def rolling_vol_values(item, window_dates: list[str], window: int = 20) -> list[tuple[int, float]]:
    index_by_date = {day: i for i, day in enumerate(item.dates)}
    closes = item.close
    output: list[tuple[int, float]] = []
    for position, day in enumerate(window_dates):
        idx = index_by_date.get(day)
        if idx is None or idx < window:
            continue
        values = [
            closes[j] / closes[j - 1] - 1.0
            for j in range(idx - window + 1, idx + 1)
            if closes[j - 1] > 0
        ]
        if len(values) > 1:
            output.append((position, stdev(values) * math.sqrt(TRADING_DAYS)))
    return output


def drawdown_values(item, window_dates: list[str]) -> list[tuple[int, float]]:
    mapping = dict(zip(item.dates, item.close))
    output: list[tuple[int, float]] = []
    peak = None
    for index, day in enumerate(window_dates):
        value = mapping.get(day)
        if value is None:
            continue
        peak = value if peak is None else max(peak, value)
        output.append((index, value / peak - 1.0 if peak > 0 else 0.0))
    return output


# ---------------------------------------------------------------------------
# Performance and risk charts
# ---------------------------------------------------------------------------

def chart_performance(series, group_series, window_dates: list[str]) -> None:
    index_ids = ["DAX", "MDAX", "SDAX", "TECDAX", "SX5E"]
    _performance_chart(
        series,
        window_dates,
        index_ids,
        "German equity indices — cumulative performance",
        "Base 100 at the start of the window; price indices, EUR",
        "performance_indices.svg",
        3,
    )

    sector_ids = sorted(key for key in group_series if key.startswith("SECTOR:"))
    _performance_chart(
        group_series,
        window_dates,
        sector_ids,
        "DAX 40 sectors — cumulative performance",
        "Equal-weighted sector indices; base 100 at the start of the window",
        "performance_sectors.svg",
        3,
    )


def _performance_chart(container, window_dates, ids, title, subtitle, filename, legend_columns) -> None:
    series_list = [(series_id, container[series_id]) for series_id in ids if series_id in container]
    all_points = []
    for series_id, item in series_list:
        points = normalized_points(item, window_dates)
        if points:
            all_points.extend(value for _, value in points)
    if not all_points:
        return

    ymin = min(all_points)
    ymax = max(all_points)
    padding = (ymax - ymin) * 0.08 or 1.0
    ymin -= padding
    ymax += padding

    width, height = 1120, 590
    svg = SVG(width, height, title, f"{subtitle}  ·  {window_dates[0]} to {window_dates[-1]}")
    x0, y0, plot_w, plot_h = 80, 90, width - 130, height - 190
    x_ticks, x_labels = month_ticks(window_dates, 7)
    y_ticks = nice_ticks(ymin, ymax, 6)
    sx, sy = frame(
        svg, x0, y0, plot_w, plot_h,
        xmin=0, xmax=max(1, len(window_dates) - 1), ymin=ymin, ymax=ymax,
        x_ticks=x_ticks, y_ticks=y_ticks,
        x_fmt=lambda value: x_labels[x_ticks.index(int(round(value)))] if int(round(value)) in x_ticks else "",
        y_fmt=fmt_axis_num, y_label="Index (base 100)",
    )
    legend_items = []
    for position, (series_id, item) in enumerate(series_list):
        points = normalized_points(item, window_dates)
        if not points:
            continue
        color = PALETTE[position % len(PALETTE)]
        svg.polyline([(sx(x), sy(y)) for x, y in points], stroke=color, width=1.8)
        legend_items.append((item.name, color))
    legend(svg, legend_items, x0, height - 65, columns=legend_columns)
    save(svg, filename)


def chart_drawdowns(series, window_dates: list[str]) -> None:
    ids = ["DAX", "MDAX", "SDAX", "TECDAX", "SX5E"]
    series_list = [(series_id, series[series_id]) for series_id in ids if series_id in series]
    all_values = []
    for _, item in series_list:
        all_values.extend(value for _, value in drawdown_values(item, window_dates))
    if not all_values:
        return
    ymin = min(all_values) * 1.05
    width, height = 1120, 540
    svg = SVG(
        width, height,
        "Drawdowns from the running peak",
        f"{window_dates[0]} to {window_dates[-1]}",
    )
    x0, y0, plot_w, plot_h = 80, 90, width - 130, height - 180
    x_ticks, x_labels = month_ticks(window_dates, 7)
    y_ticks = nice_ticks(ymin, 0.0, 6)
    sx, sy = frame(
        svg, x0, y0, plot_w, plot_h,
        xmin=0, xmax=max(1, len(window_dates) - 1), ymin=ymin, ymax=0.0,
        x_ticks=x_ticks, y_ticks=y_ticks,
        x_fmt=lambda value: x_labels[x_ticks.index(int(round(value)))] if int(round(value)) in x_ticks else "",
        y_fmt=fmt_axis_pct, y_label="Drawdown",
    )
    items = []
    for position, (series_id, item) in enumerate(series_list):
        points = drawdown_values(item, window_dates)
        color = PALETTE[position % len(PALETTE)]
        svg.polyline([(sx(x), sy(y)) for x, y in points], stroke=color, width=1.7)
        items.append((item.name, color))
    legend(svg, items, x0, height - 55, columns=3)
    save(svg, "drawdowns.svg")


def chart_rolling_vol(series, window_dates: list[str]) -> None:
    ids = ["DAX"]
    for sector in ("Financials", "Industrials", "Information Technology", "Health Care", "Consumer Discretionary"):
        ids.append(f"SECTOR:{sector}")
    series_list = [(series_id, series[series_id]) for series_id in ids if series_id in series]
    all_values = []
    for _, item in series_list:
        all_values.extend(value for _, value in rolling_vol_values(item, window_dates))
    if not all_values:
        return
    ymin = 0.0
    ymax = max(all_values) * 1.1
    width, height = 1120, 540
    svg = SVG(
        width, height,
        "Rolling 20-day annualised volatility",
        f"{window_dates[0]} to {window_dates[-1]}",
    )
    x0, y0, plot_w, plot_h = 80, 90, width - 130, height - 180
    x_ticks, x_labels = month_ticks(window_dates, 7)
    y_ticks = nice_ticks(ymin, ymax, 6)
    sx, sy = frame(
        svg, x0, y0, plot_w, plot_h,
        xmin=0, xmax=max(1, len(window_dates) - 1), ymin=ymin, ymax=ymax,
        x_ticks=x_ticks, y_ticks=y_ticks,
        x_fmt=lambda value: x_labels[x_ticks.index(int(round(value)))] if int(round(value)) in x_ticks else "",
        y_fmt=fmt_axis_pct, y_label="Annualised volatility",
    )
    items = []
    for position, (series_id, item) in enumerate(series_list):
        points = rolling_vol_values(item, window_dates)
        color = PALETTE[position % len(PALETTE)]
        svg.polyline([(sx(x), sy(y)) for x, y in points], stroke=color, width=1.7)
        items.append((item.name, color))
    legend(svg, items, x0, height - 50, columns=3)
    save(svg, "rolling_vol.svg")


# ---------------------------------------------------------------------------
# Heatmaps
# ---------------------------------------------------------------------------

def chart_correlation_heatmap(group_series, correlation_rows) -> None:
    sector_ids = sorted(key for key in group_series if key.startswith("SECTOR:"))
    if len(sector_ids) < 2:
        return
    matrix = {left: {right: 1.0 if left == right else None for right in sector_ids} for left in sector_ids}
    for row in correlation_rows:
        left, right, corr = row[0], row[1], float(row[2])
        if left in matrix and right in matrix[left]:
            matrix[left][right] = corr
            matrix[right][left] = corr

    cell = 58
    left_margin = 210
    top_margin = 170
    width = left_margin + cell * len(sector_ids) + 40
    height = top_margin + cell * len(sector_ids) + 60
    svg = SVG(
        width, height,
        "Sector correlation matrix",
        "Pearson correlation of daily returns over the analysis window",
    )
    for row_index, left in enumerate(sector_ids):
        label = group_series[left].name
        svg.text(left_margin - 8, top_margin + row_index * cell + cell / 2 + 3, label, size=10, anchor="end")
        for col_index, right in enumerate(sector_ids):
            value = matrix[left][right]
            x = left_margin + col_index * cell
            y = top_margin + row_index * cell
            svg.rect(x, y, cell, cell, fill=correlation_color(value), stroke="#ffffff", stroke_width=1)
            if value is not None:
                svg.text(
                    x + cell / 2, y + cell / 2 + 3, f"{value:.2f}",
                    size=8, anchor="middle",
                    fill="#ffffff" if abs(value) > 0.65 else "#222222",
                )
    for col_index, right in enumerate(sector_ids):
        label = group_series[right].name
        svg.text(
            left_margin + col_index * cell + cell / 2,
            top_margin - 8,
            label, size=9, anchor="start", rotate=-55,
        )
    svg.text(left_margin, height - 18, "Red = positive correlation, blue = negative correlation", size=10, fill="#555555")
    save(svg, "correlation_heatmap.svg")


def chart_monthly_heatmap(dax_series, monthly_rows) -> None:
    if dax_series is None:
        return
    data: dict[int, dict[int, float]] = defaultdict(dict)
    for row in monthly_rows:
        if row[0] != "DAX":
            continue
        year, month = row[1].split("-")
        data[int(year)][int(month)] = float(row[2])
    if not data:
        return
    years = sorted(data)
    months = list(range(1, 13))
    cell_w, cell_h = 72, 40
    left_margin, top_margin = 70, 90
    width = left_margin + cell_w * 12 + 30
    height = top_margin + cell_h * len(years) + 80
    svg = SVG(
        width, height,
        "DAX monthly returns",
        "Month-end close versus previous month-end",
    )
    for col, month in enumerate(months):
        svg.text(left_margin + col * cell_w + cell_w / 2, top_margin - 10, f"{month:02d}", size=10, anchor="middle")
    values = [value for year in years for value in data[year].values()]
    vmax = max((abs(v) for v in values), default=0.01)
    for row, year in enumerate(years):
        svg.text(left_margin - 10, top_margin + row * cell_h + cell_h / 2 + 4, str(year), size=11, anchor="end")
        for col, month in enumerate(months):
            value = data[year].get(month)
            x = left_margin + col * cell_w
            y = top_margin + row * cell_h
            svg.rect(x, y, cell_w, cell_h, fill=diverging_color(value or 0.0, -vmax, vmax), stroke="#ffffff", stroke_width=1)
            if value is not None:
                svg.text(x + cell_w / 2, y + cell_h / 2 + 4, fmt_pct(value, 1), size=9, anchor="middle")
    svg.text(left_margin, height - 20, "Green = positive month, red = negative month", size=10, fill="#555555")
    save(svg, "monthly_heatmap.svg")


def chart_breadth(breadth_rows) -> None:
    if not breadth_rows:
        return
    dates = [row["date"] for row in breadth_rows]
    width, height = 1120, 500
    svg = SVG(
        width, height,
        "DAX 40 breadth",
        "Share of constituents trading above their 50-day and 200-day moving averages",
    )
    x0, y0, plot_w, plot_h = 80, 90, width - 130, height - 170
    x_ticks, x_labels = month_ticks(dates, 7)
    y_ticks = nice_ticks(0.0, 1.0, 6)
    sx, sy = frame(
        svg, x0, y0, plot_w, plot_h,
        xmin=0, xmax=max(1, len(dates) - 1), ymin=0.0, ymax=1.0,
        x_ticks=x_ticks, y_ticks=y_ticks,
        x_fmt=lambda value: x_labels[x_ticks.index(int(round(value)))] if int(round(value)) in x_ticks else "",
        y_fmt=fmt_axis_pct, y_label="Share of constituents",
    )
    series = [
        ("Above 50-day SMA", "above_sma50_pct", PALETTE[0]),
        ("Above 200-day SMA", "above_sma200_pct", PALETTE[3]),
    ]
    for label, key, color in series:
        points = [(sx(i), sy(row[key])) for i, row in enumerate(breadth_rows)]
        svg.polyline(points, stroke=color, width=2.0)
    legend(svg, [(label, color) for label, _, color in series], x0, height - 45, columns=2)
    save(svg, "breadth.svg")


# ---------------------------------------------------------------------------
# Cross-section charts
# ---------------------------------------------------------------------------

def chart_company_ranking(metrics, metrics_by_id) -> None:
    equities = [
        row for row in metrics
        if row["level"] == "equity" and row["ret_1y"] is not None
    ]
    equities.sort(key=lambda row: row["ret_1y"])
    if not equities:
        return
    sectors = sorted({row["sector"] for row in equities})
    colors = {sector: PALETTE[i % len(PALETTE)] for i, sector in enumerate(sectors)}

    bar_height = 19
    width = 1120
    height = 110 + bar_height * len(equities) + 40
    svg = SVG(
        width, height,
        "DAX 40 — one-year total price return",
        "Sorted from weakest to strongest; colour = broad sector",
    )
    left, right = 240, 100
    plot_w = width - left - right
    values = [row["ret_1y"] for row in equities]
    vmin, vmax = min(values + [0.0]), max(values + [0.0])
    padding = (vmax - vmin) * 0.08 or 0.01
    vmin -= padding
    vmax += padding

    def sx(value: float) -> float:
        return left + (value - vmin) / (vmax - vmin) * plot_w

    zero_x = sx(0.0)
    svg.line(zero_x, 80, zero_x, 80 + bar_height * len(equities), stroke="#888888", width=1)
    for index, row in enumerate(equities):
        y = 80 + index * bar_height
        x1, x2 = sx(row["ret_1y"]), zero_x
        svg.rect(min(x1, x2), y + 2, abs(x2 - x1), bar_height - 5, fill=colors[row["sector"]], opacity=0.9)
        svg.text(left - 8, y + bar_height / 2 + 4, row["name"], size=9, anchor="end")
        label_x = x1 + 5 if x1 >= x2 else x1 - 5
        svg.text(label_x, y + bar_height / 2 + 4, fmt_pct(row["ret_1y"], 1), size=8,
                 anchor="start" if x1 >= x2 else "end")
    tick_values = nice_ticks(vmin, vmax, 6)
    for tick in tick_values:
        x = sx(tick)
        svg.line(x, 80, x, 80 + bar_height * len(equities), stroke="#eeeeee", width=1)
        svg.text(x, 72, fmt_axis_pct(tick), size=9, anchor="middle", fill="#555555")
    legend_items = [(sector, colors[sector]) for sector in sectors]
    legend(svg, legend_items, left, height - 40, columns=5)
    save(svg, "company_ranking.svg")


def chart_risk_return(metrics) -> None:
    equities = [
        row for row in metrics
        if row["level"] == "equity"
        and row["ann_vol"] is not None
        and row["ret_1y"] is not None
    ]
    if len(equities) < 3:
        return
    sectors = sorted({row["sector"] for row in equities})
    colors = {sector: PALETTE[i % len(PALETTE)] for i, sector in enumerate(sectors)}
    width, height = 1100, 620
    svg = SVG(
        width, height,
        "DAX 40 — risk versus one-year return",
        "Annualised volatility (x) and one-year price return (y); colour = broad sector",
    )
    x0, y0, plot_w, plot_h = 85, 95, width - 150, height - 200
    xmax = max(row["ann_vol"] for row in equities) * 1.12
    ymin = min(row["ret_1y"] for row in equities) * 1.15
    ymax = max(row["ret_1y"] for row in equities) * 1.15
    x_ticks = nice_ticks(0.0, xmax, 6)
    y_ticks = nice_ticks(ymin, ymax, 6)
    sx, sy = frame(
        svg, x0, y0, plot_w, plot_h,
        xmin=0.0, xmax=xmax, ymin=ymin, ymax=ymax,
        x_ticks=x_ticks, y_ticks=y_ticks,
        x_fmt=fmt_axis_pct, y_fmt=fmt_axis_pct,
        x_label="Annualised volatility", y_label="One-year return",
    )
    svg.line(x0, sy(0.0), x0 + plot_w, sy(0.0), stroke="#999999", width=1, dash="4 3")
    for row in equities:
        svg.circle(sx(row["ann_vol"]), sy(row["ret_1y"]), 4.5, fill=colors[row["sector"]], opacity=0.85)
    labelled = sorted(equities, key=lambda row: row["ret_1y"])
    labelled = labelled[:5] + labelled[-5:]
    for row in labelled:
        svg.text(sx(row["ann_vol"]) + 7, sy(row["ret_1y"]) + 3, row["name"], size=8, fill="#333333")
    legend_items = [(sector, colors[sector]) for sector in sectors]
    legend(svg, legend_items, x0, height - 55, columns=5)
    save(svg, "risk_return.svg")
