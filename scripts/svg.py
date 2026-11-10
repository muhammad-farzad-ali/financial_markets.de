"""Minimal dependency-free SVG helpers used by the chart module."""

from __future__ import annotations

import math
from xml.sax.saxutils import escape

PALETTE = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#393b79", "#637939", "#8c6d31", "#843c39", "#7b4173",
    "#3182bd", "#e6550d", "#31a354", "#756bb1", "#636363",
]


def esc(text) -> str:
    return escape(str(text), {'"': "&quot;", "'": "&apos;"})


class SVG:
    def __init__(self, width: int, height: int, title: str = "", subtitle: str = ""):
        self.width = width
        self.height = height
        self.parts: list[str] = [
            f'<rect width="{width}" height="{height}" fill="#ffffff"/>'
        ]
        if title:
            self.text(width / 2, 26, title, size=18, anchor="middle", weight="bold")
        if subtitle:
            self.text(width / 2, 46, subtitle, size=11, anchor="middle", fill="#666666")

    def text(
        self,
        x: float,
        y: float,
        value,
        size: float = 11,
        anchor: str = "start",
        fill: str = "#222222",
        weight: str = "normal",
        rotate: float | None = None,
    ) -> None:
        transform = f' transform="rotate({rotate} {x:.2f} {y:.2f})"' if rotate is not None else ""
        self.parts.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" '
            f'font-family="Helvetica, Arial, sans-serif" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{transform}>{esc(value)}</text>'
        )

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        stroke: str = "#dddddd",
        width: float = 1.0,
        dash: str | None = None,
        opacity: float = 1.0,
    ) -> None:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{stroke}" stroke-width="{width}" opacity="{opacity}"{dash_attr}/>'
        )

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        fill: str = "#ffffff",
        stroke: str = "none",
        stroke_width: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        self.parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" opacity="{opacity}"/>'
        )

    def polyline(
        self,
        points: list[tuple[float, float]],
        stroke: str,
        width: float = 1.6,
        opacity: float = 1.0,
        fill: str = "none",
    ) -> None:
        if not points:
            return
        path = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        self.parts.append(
            f'<path d="{path}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{width}" opacity="{opacity}" stroke-linejoin="round" '
            f'stroke-linecap="round"/>'
        )

    def circle(
        self,
        x: float,
        y: float,
        radius: float,
        fill: str,
        stroke: str = "none",
        stroke_width: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        self.parts.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{stroke_width}" opacity="{opacity}"/>'
        )

    def render(self) -> str:
        body = "\n".join(self.parts)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" '
            f'height="{self.height}" viewBox="0 0 {self.width} {self.height}">\n'
            f"{body}\n</svg>\n"
        )


def nice_ticks(minimum: float, maximum: float, count: int = 5) -> list[float]:
    if not math.isfinite(minimum) or not math.isfinite(maximum) or minimum == maximum:
        return [minimum]
    span = maximum - minimum
    raw_step = span / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / magnitude
    if residual <= 1:
        step = 1 * magnitude
    elif residual <= 2:
        step = 2 * magnitude
    elif residual <= 5:
        step = 5 * magnitude
    else:
        step = 10 * magnitude
    start = math.ceil(minimum / step) * step
    ticks = []
    value = start
    while value <= maximum + step * 0.001:
        ticks.append(value)
        value += step
    return ticks


def frame(
    svg: SVG,
    x0: float,
    y0: float,
    width: float,
    height: float,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    x_ticks: list[float],
    y_ticks: list[float],
    x_fmt=str,
    y_fmt=str,
    x_label: str = "",
    y_label: str = "",
    grid: bool = True,
):
    """Draw axes/grid and return scale functions."""
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1

    def sx(value: float) -> float:
        return x0 + (value - xmin) / (xmax - xmin) * width

    def sy(value: float) -> float:
        return y0 + height - (value - ymin) / (ymax - ymin) * height

    svg.rect(x0, y0, width, height, fill="#fbfbfb", stroke="#dddddd", stroke_width=1)
    if grid:
        for tick in y_ticks:
            y = sy(tick)
            svg.line(x0, y, x0 + width, y, stroke="#e8e8e8", width=1)
        for tick in x_ticks:
            x = sx(tick)
            svg.line(x, y0, x, y0 + height, stroke="#f0f0f0", width=1)

    for tick in y_ticks:
        svg.text(x0 - 8, sy(tick) + 4, y_fmt(tick), size=10, anchor="end", fill="#555555")
    for tick in x_ticks:
        svg.text(sx(tick), y0 + height + 18, x_fmt(tick), size=10, anchor="middle", fill="#555555")

    svg.line(x0, y0 + height, x0 + width, y0 + height, stroke="#999999", width=1)
    svg.line(x0, y0, x0, y0 + height, stroke="#999999", width=1)
    if x_label:
        svg.text(x0 + width / 2, y0 + height + 40, x_label, size=11, anchor="middle", fill="#444444")
    if y_label:
        svg.text(x0 - 55, y0 + height / 2, y_label, size=11, anchor="middle", fill="#444444", rotate=-90)
    return sx, sy


def legend(svg: SVG, items: list[tuple[str, str]], x: float, y: float, columns: int = 1) -> None:
    for index, (label, color) in enumerate(items):
        column = index % columns
        row = index // columns
        cx = x + column * 210
        cy = y + row * 18
        svg.line(cx, cy - 4, cx + 18, cy - 4, stroke=color, width=2.5)
        svg.text(cx + 24, cy, label, size=10, fill="#333333")


def diverging_color(value: float, vmin: float, vmax: float) -> str:
    """Red for negative, white for zero, green for positive."""
    if value is None or not math.isfinite(value):
        return "#f2f2f2"
    if value >= 0:
        strength = 0.0 if vmax <= 0 else min(1.0, value / vmax)
        red = int(255 - strength * (255 - 0))
        green = int(255 - strength * (255 - 128))
        blue = int(255 - strength * (255 - 0))
    else:
        strength = 0.0 if vmin >= 0 else min(1.0, -value / -vmin)
        red = int(255 - strength * (255 - 200))
        green = int(255 - strength * (255 - 0))
        blue = int(255 - strength * (255 - 0))
    return f"rgb({red},{green},{blue})"


def correlation_color(value: float) -> str:
    """Blue for negative, white for zero, red for positive."""
    if value is None or not math.isfinite(value):
        return "#f2f2f2"
    strength = min(1.0, abs(value))
    if value >= 0:
        red = int(255 - strength * (255 - 180))
        green = int(255 - strength * (255 - 30))
        blue = int(255 - strength * (255 - 30))
    else:
        red = int(255 - strength * (255 - 30))
        green = int(255 - strength * (255 - 60))
        blue = int(255 - strength * (255 - 180))
    return f"rgb({red},{green},{blue})"


def fmt_axis_pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def fmt_axis_num(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}"
