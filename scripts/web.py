#!/usr/bin/env python3
"""Generate a dependency-free static dashboard for the DAX 40, MDAX and SDAX analysis.

The dashboard is intentionally simple: one generated HTML file, no framework,
no database, no API server.  All data is rendered once from the existing TSVs;
JavaScript is only used for search, filtering, sorting and expanding rows.

Usage
-----
    python3 scripts/web.py
    python3 scripts/web.py --open
    python3 -m http.server 8000   # then open http://localhost:8000/web/
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
import webbrowser
from datetime import date
from pathlib import Path

from fundamentals import (
    COMPONENT_LABELS,
    MODEL_DESCRIPTIONS,
    by_ticker,
    load_numeric_tsv,
    scenario_lookup,
)
from lib import DATA_DIR, ROOT, fmt_num, fmt_pct

WEB_DIR = ROOT / "web"
INDEX_PATH = WEB_DIR / "index.html"
METRICS_PATH = DATA_DIR / "fundamentals_metrics.tsv"
SCORES_PATH = DATA_DIR / "fundamentals_scores.tsv"
SCENARIOS_PATH = DATA_DIR / "fundamentals_scenarios.tsv"
ANALYSIS_MANIFEST_PATH = DATA_DIR / "fundamentals_analysis_manifest.json"
COLLECTION_MANIFEST_PATH = DATA_DIR / "fundamentals_manifest.json"


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fnum(value, digits: int = 1) -> str:
    return "n/a" if value is None else fmt_num(value, digits)


def fpct(value, digits: int = 1, sign: bool = True) -> str:
    return "n/a" if value is None else fmt_pct(value, digits, sign)


def fpp(value, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:+.{digits}f} percentage points"


def fx(value, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}×"


def fyears(value, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f} years"


def data_num(value, fallback: float = -999.0) -> float:
    return fallback if value is None else value


def score_class(value) -> str:
    if value is None:
        return "muted"
    if value >= 70:
        return "good"
    if value >= 55:
        return "ok"
    if value >= 40:
        return "warn"
    return "bad"


def band_class(band: str | None) -> str:
    return {
        "Low": "low",
        "Moderate": "moderate",
        "Elevated": "elevated",
        "High": "high",
    }.get(band or "", "muted")


SCORE_LABELS = {
    "survival_score": "Financial safety",
    "growth_score": "Growth potential",
    "valuation_score": "Price attractiveness",
    "investment_score": "Overall appeal",
}

SCORE_HELP = {
    "survival_score": "Can the company carry its debt and stay healthy?",
    "growth_score": "Can the business grow over the next 3–10 years?",
    "valuation_score": "Are the shares cheap or expensive versus their own history?",
    "investment_score": "Combines growth, safety and price.",
}

BAND_LABELS = {
    "Low": "Low risk",
    "Moderate": "Some risk",
    "Elevated": "Elevated risk",
    "High": "High risk",
}

BAND_HELP = {
    "Low": "The model sees strong financial safety relative to the other companies in this universe.",
    "Moderate": "Mostly healthy, but not among the strongest.",
    "Elevated": "Several warning signs; worth watching closely.",
    "High": "The weakest relative financial safety in this universe.",
}

MODEL_LABELS = {
    "non_financial": "Industrial / consumer",
    "auto": "Car maker",
    "bank": "Bank",
    "insurer": "Insurer",
    "real_estate": "Property company",
    "holding": "Holding company",
    "utility": "Utility",
    "telecom": "Telecom",
    "asset_light": "Asset-light business",
    "financial_infrastructure": "Market infrastructure",
}

MODEL_HELP = {
    "non_financial": "Assessed like a normal operating company: debt, interest cover, cash flow and profit.",
    "auto": "Assessed like a car maker; finance-arm debt is separated where the data allows.",
    "bank": "Banks are assessed differently: capital strength, profitability, cost efficiency and loan losses.",
    "insurer": "Insurers are assessed on capital strength, profitability, claims and expenses.",
    "real_estate": "Property companies are assessed on debt, interest cover, cash flow and asset strength.",
    "holding": "Holding companies are assessed on asset strength, cash flow and debt.",
    "utility": "Utilities are assessed on debt, interest cover, cash flow and stability.",
    "telecom": "Telecoms are assessed on debt, interest cover, cash flow and stability.",
    "asset_light": "Asset-light businesses are assessed on debt, cash flow and returns on capital.",
    "financial_infrastructure": "Market infrastructure is assessed on debt, interest cover, cash flow and returns.",
}


def score_word(key: str, value) -> str:
    if value is None:
        return "No data"
    if key == "survival_score":
        return "Very safe" if value >= 75 else "Generally safe" if value >= 60 else "Watch list" if value >= 45 else "High risk"
    if key == "growth_score":
        return "High" if value >= 70 else "Good" if value >= 55 else "Moderate" if value >= 40 else "Weak"
    if key == "valuation_score":
        return "Cheap vs history" if value >= 70 else "Slightly cheap" if value >= 55 else "Fair" if value >= 40 else "Expensive vs history"
    return "Attractive" if value >= 70 else "Above average" if value >= 55 else "Neutral" if value >= 40 else "Unattractive"


def band_label(band: str | None) -> str:
    return BAND_LABELS.get(band or "", band or "No data")


def model_label(model: str | None) -> str:
    return MODEL_LABELS.get(model or "", (model or "Unknown").replace("_", " ").title())


def period_label(period: str | None) -> str:
    return "Latest 12 months" if period == "TTM" else (period or "—")


def score_badge(key: str, value) -> str:
    return (
        f'<div class="score-card">'
        f'<span class="score-label">{esc(SCORE_LABELS[key])}</span>'
        f'<span class="score {score_class(value)}">{fnum(value, 1)}</span>'
        f'<span class="score-word">{esc(score_word(key, value))}</span>'
        f'<span class="score-note">{esc(SCORE_HELP[key])}</span>'
        f"</div>"
    )


def metric_table(rows: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f'<tr><td>{esc(name)}</td><td class="num">{esc(value)}</td><td>{esc(help_text)}</td></tr>'
        for name, value, help_text in rows
    )
    return (
        '<div class="table-wrap mini-wrap"><table class="metric-table">'
        "<thead><tr><th>What it measures</th><th>Value</th><th>How to read it</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


CHIP_LABELS = {
    "altman_z2": "Financial-health check",
    "ohlson": "Distress check",
    "interest_coverage": "Interest cover",
    "debt_ebitda": "Debt load",
    "current_ratio": "Short-term liquidity",
    "cash_st_debt": "Cash buffer",
    "fcf_margin": "Cash generation",
    "credit_score": "Credit strength",
    "equity_assets": "Capital buffer",
    "roa": "Profitability",
    "earnings_stability": "Earnings stability",
    "cost_income": "Cost efficiency",
    "loan_losses": "Loan losses",
    "policy_benefits": "Claims ratio",
    "expense_ratio": "Expense ratio",
    "revenue_growth": "Sales growth",
    "roic_wacc_spread": "Value creation",
    "gross_margin_change": "Margin trend",
    "eps_cagr": "Profit growth",
    "fcf_cagr": "Cash-flow growth",
    "rnd_intensity": "R&D intensity",
    "order_backlog_cagr": "Order backlog",
    "analyst_revenue_growth": "Analyst growth",
    "roe_ke_spread": "Return vs cost of equity",
    "bvps_cagr": "Book value growth",
    "dps_cagr": "Dividend growth",
    "expense_trend": "Expense trend",
    "revenue_cagr": "Sales growth",
    "real_estate_margin": "Property cash margin",
    "fcf_assets": "Cash return on assets",
}


def driver_chips(components: dict, weights: dict) -> tuple[str, str]:
    items = []
    for key, value in components.items():
        if value is None:
            continue
        weight = weights.get(key, 0.0)
        items.append((key, value, (value - 50.0) * weight))
    strengths = sorted(items, key=lambda item: item[2], reverse=True)[:3]
    weaknesses = sorted(items, key=lambda item: item[2])[:3]

    def chips(rows):
        if not rows:
            return '<span class="muted">—</span>'
        return "".join(
            f'<span class="chip">{esc(CHIP_LABELS.get(key, COMPONENT_LABELS.get(key, key)))} '
            f'<strong>{value:.0f}</strong></span>'
            for key, value, _ in rows
        )

    return chips(strengths), chips(weaknesses)


def scenario_table(scenarios: dict[str, dict]) -> str:
    if not scenarios:
        return '<p class="muted">Scenario analysis is not available for this company.</p>'
    case_labels = {
        "bear": "Weak case",
        "base": "Central case",
        "bull": "Strong case",
        "weighted": "Weighted average",
    }
    rows = []
    for key in ("bear", "base", "bull", "weighted"):
        row = scenarios.get(key)
        if not row:
            continue
        rows.append(
            "<tr>"
            f"<td>{esc(case_labels[key])}</td>"
            f"<td class=\"num\">{fpct(row.get('growth_rate'))}</td>"
            f"<td class=\"num\">{fx(row.get('exit_multiple'))}</td>"
            f"<td class=\"num\">{fnum(row.get('expected_price'), 2)}</td>"
            f"<td class=\"num\">{fpct(row.get('total_return_cagr'))}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap mini-wrap">'
        '<table class="mini">'
        "<thead><tr><th>Scenario</th><th>Yearly growth</th><th>Exit price multiple</th>"
        "<th>Share price in 5y</th><th>Yearly return</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )


def business_metric_rows(model_type: str, metric: dict) -> list[tuple[str, str, str]]:
    if model_type in ("bank", "insurer"):
        return [
            ("Sales growth", fpct(metric.get("revenue_cagr")),
             "How fast revenue grew per year. Higher is better."),
            ("Profit per share growth", fpct(metric.get("eps_cagr")),
             "How fast profit per share grew per year. Higher is better."),
            ("Profitability", fpct(metric.get("roa"), 2, False),
             "Profit as a share of assets. Higher is better."),
            ("Return vs cost of equity", fpp(metric.get("roe_ke_spread")),
             "Return on equity minus the cost of equity. Above zero is good."),
        ]
    return [
        ("Sales growth", fpct(metric.get("revenue_cagr")),
         "How fast sales grew per year. Higher is better."),
        ("Profit per share growth", fpct(metric.get("eps_cagr")),
         "How fast profit per share grew per year. Higher is better."),
        ("Cash generation", fpct(metric.get("fcf_margin")),
         "Free cash flow as a share of sales. Higher is better."),
        ("Value creation", fpp(metric.get("roic_wacc_spread")),
         "Return on capital minus the cost of capital. Above zero is good."),
    ]


def financial_metric_rows(model_type: str, metric: dict) -> list[tuple[str, str, str]]:
    if model_type == "bank":
        return [
            ("Capital buffer", fpct(metric.get("equity_assets"), 1, False),
             "Equity as a share of assets. Higher is safer."),
            ("Profitability", fpct(metric.get("roa"), 2, False),
             "Profit as a share of assets. Higher is better."),
            ("Cost efficiency", fpct(metric.get("cost_income"), 1, False),
             "Costs as a share of income. Lower is better."),
            ("Loan losses", fpct(metric.get("loan_loss_ratio"), 2, False),
             "Loan losses as a share of assets. Lower is safer."),
        ]
    if model_type == "insurer":
        return [
            ("Capital buffer", fpct(metric.get("equity_assets"), 1, False),
             "Equity as a share of assets. Higher is safer."),
            ("Profitability", fpct(metric.get("roa"), 2, False),
             "Profit as a share of assets. Higher is better."),
            ("Claims ratio", fpct(metric.get("policy_benefit_ratio"), 1, False),
             "Claims and benefits as a share of revenue. Lower is generally better."),
            ("Expense ratio", fpct(metric.get("expense_ratio"), 1, False),
             "Operating costs as a share of revenue. Lower is better."),
        ]
    if model_type == "real_estate":
        return [
            ("Debt load", fyears(metric.get("debt_ebitda")),
             "Years of operating profit needed to repay debt. Lower is safer."),
            ("Interest cover", fx(metric.get("interest_coverage")),
             "How often operating profit covers interest payments. Higher is safer."),
            ("Property cash margin", fpct(metric.get("real_estate_margin")),
             "Cash earnings as a share of revenue. Higher is better."),
            ("Capital buffer", fpct(metric.get("equity_assets"), 1, False),
             "Equity as a share of assets. Higher is safer."),
        ]
    if model_type == "auto":
        return [
            ("Debt load", fyears(metric.get("industrial_debt_ebitda") or metric.get("debt_ebitda")),
             "Debt divided by operating profit, excluding the finance arm where available. Lower is safer."),
            ("Interest cover", fx(metric.get("interest_coverage")),
             "How often operating profit covers interest payments. Higher is safer."),
            ("Short-term liquidity", fnum(metric.get("current_ratio"), 2),
             "Ability to pay bills due within a year. Around 1.5–2 is comfortable."),
            ("Cash buffer", fx(metric.get("cash_st_debt")),
             "Cash compared with short-term debt. Higher is safer."),
        ]
    if model_type == "holding":
        return [
            ("Capital buffer", fpct(metric.get("equity_assets"), 1, False),
             "Equity as a share of assets. Higher is safer."),
            ("Cash return on assets", fpct(metric.get("fcf_assets"), 2, False),
             "Free cash flow as a share of assets. Higher is better."),
            ("Profitability", fpct(metric.get("roa"), 2, False),
             "Profit as a share of assets. Higher is better."),
            ("Debt load", fyears(metric.get("debt_ebitda")),
             "Years of operating profit needed to repay debt. Lower is safer."),
        ]
    return [
        ("Debt load", fyears(metric.get("debt_ebitda")),
         "Years of operating profit needed to repay debt. Lower is safer."),
        ("Interest cover", fx(metric.get("interest_coverage")),
         "How often operating profit covers interest payments. Higher is safer."),
        ("Short-term liquidity", fnum(metric.get("current_ratio"), 2),
         "Ability to pay bills due within a year. Around 1.5–2 is comfortable."),
        ("Cash buffer", fx(metric.get("cash_st_debt")),
         "Cash compared with short-term debt. Higher is safer."),
    ]


def detail_html(ticker: str, metric: dict, score: dict, scenarios: dict[str, dict]) -> str:
    company = metric.get("company", ticker)
    model_type = metric.get("model_type", "")
    components = json.loads(score.get("survival_components") or "{}")
    weights = json.loads(score.get("survival_weights") or "{}")
    strengths, weaknesses = driver_chips(components, weights)
    altman = metric.get("altman_z2")
    ohlson = metric.get("ohlson_probability")
    band = score.get("distress_band") or "—"
    data_notes = score.get("data_notes") or ""
    latest = f"{period_label(metric.get('latest_period'))} ({metric.get('latest_period_end', '—')})"

    business_rows = business_metric_rows(model_type, metric)
    financial_rows = financial_metric_rows(model_type, metric)
    share_rows = [
        ("Price / earnings", fx(metric.get("pe")),
         "Share price divided by yearly profit. Lower can be cheaper, but compare with the company's own history."),
        ("Price / free cash flow", fx(metric.get("p_fcf")),
         "Share price divided by free cash flow. Lower can be cheaper."),
        ("Price / operating profit", fx(metric.get("ev_ebitda")),
         "Enterprise value divided by operating profit. Lower can be cheaper."),
        ("Price / book value", fx(metric.get("pb")),
         "Share price compared with accounting book value. Useful for banks, insurers and property."),
        ("Dividend yield", fpct(metric.get("dividend_yield"), 2, False),
         "Yearly dividend as a percentage of the share price."),
    ]

    weighted = scenarios.get("weighted") or {}
    current_line = ""
    if weighted:
        current_line = (
            f'<p class="current">Current share price <strong>{fnum(weighted.get("current_price"), 2)} EUR</strong> '
            f'· starting valuation multiple <strong>{fx(weighted.get("current_multiple"))}</strong> '
            f'· dividend yield <strong>{fpct(weighted.get("dividend_yield"), 2, False)}</strong></p>'
        )

    if altman is not None:
        technical = (
            f'Technical checks: Altman Z″ <strong>{fnum(altman, 2)}</strong> (higher is safer) '
            f'· Ohlson 1-year score <strong>{fpct(ohlson, 1, False)}</strong> (lower is safer).'
        )
    else:
        technical = "Altman and Ohlson are not meaningful for this business type; the sector-specific scorecard is used instead."

    notes_html = ""
    if data_notes:
        notes_html = f'<p class="notes"><strong>Data limitations:</strong> {esc(data_notes)}</p>'

    return f"""
<div class="detail">
  <div class="detail-head">
    <div>
      <h2>{esc(company)} <span class="ticker">{esc(ticker)}</span></h2>
      <p>{esc(metric.get('index_name', '—'))} · {esc(model_label(model_type))} · {esc(metric.get('sector', '—'))} · {esc(latest)}</p>
    </div>
    <a class="report-link" href="../reports/fundamentals.md">Full report ↗</a>
  </div>

  <div class="score-grid detail-scores">
    {score_badge("survival_score", score.get("survival_score"))}
    {score_badge("growth_score", score.get("growth_score"))}
    {score_badge("valuation_score", score.get("valuation_score"))}
    {score_badge("investment_score", score.get("investment_score"))}
  </div>

  <p class="plain-summary"><strong>In plain English:</strong>
    Financial safety: <strong>{esc(score_word('survival_score', score.get('survival_score')))}</strong>.
    Growth potential: <strong>{esc(score_word('growth_score', score.get('growth_score')))}</strong>.
    Price attractiveness: <strong>{esc(score_word('valuation_score', score.get('valuation_score')))}</strong>.
    Overall appeal: <strong>{esc(score_word('investment_score', score.get('investment_score')))}</strong>.
  </p>

  <div class="risk-line">
    <span class="band {band_class(band)}">{esc(band_label(band))}</span>
    <span>{esc(BAND_HELP.get(band, ''))}</span>
  </div>
  <p class="muted small">{technical}</p>

  <div class="detail-grid">
    <section class="detail-card">
      <h3>Business performance</h3>
      {metric_table(business_rows)}
    </section>
    <section class="detail-card">
      <h3>Financial health</h3>
      {metric_table(financial_rows)}
    </section>
    <section class="detail-card wide">
      <h3>Share price</h3>
      {metric_table(share_rows)}
    </section>
    <section class="detail-card wide">
      <h3>Five-year what-if</h3>
      {current_line}
      {scenario_table(scenarios)}
      <p class="muted small">The weak, central and strong cases are weighted 25/50/25. Yearly return includes the current dividend yield. This is a calculation, not a prediction or target price.</p>
    </section>
  </div>

  <div class="drivers">
    <div><h4>What boosts the safety score</h4>{strengths}</div>
    <div><h4>Relatively weaker safety inputs</h4>{weaknesses}</div>
  </div>
  {notes_html}
  <p class="muted small"><strong>How this company is assessed:</strong> {esc(MODEL_HELP.get(model_type, MODEL_DESCRIPTIONS.get(model_type, '')))}</p>
</div>
"""

def company_row(rank: int, ticker: str, metric: dict, score: dict,
                scenarios: dict[str, dict]) -> str:
    company = metric.get("company", ticker)
    band = score.get("distress_band") or "—"
    weighted = scenarios.get("weighted") or {}
    total_return = weighted.get("total_return_cagr")
    detail_id = f"detail-{ticker.replace('.', '-')}"
    search_blob = " ".join(
        str(value).lower()
        for value in (company, ticker, metric.get("index_name", ""),
                      model_label(metric.get("model_type")), metric.get("sector", ""))
    )

    def score_cell(key: str) -> str:
        value = score.get(key)
        return (
            f'<span class="score {score_class(value)}">{fnum(value, 1)}</span>'
            f'<span class="score-word">{esc(score_word(key, value))}</span>'
        )

    return f"""
<tr class="company-row" tabindex="0" role="button" aria-expanded="false"
    data-detail="{esc(detail_id)}"
    data-search="{esc(search_blob)}"
    data-model="{esc(metric.get('model_type', ''))}"
    data-index="{esc(metric.get('index_name', ''))}"
    data-band="{esc(band)}"
    data-company="{esc(company.lower())}"
    data-investment="{data_num(score.get('investment_score'))}"
    data-survival="{data_num(score.get('survival_score'))}"
    data-growth="{data_num(score.get('growth_score'))}"
    data-valuation="{data_num(score.get('valuation_score'))}"
    data-return="{data_num(total_return)}">
  <td class="rank">{rank}</td>
  <td>
    <strong>{esc(company)}</strong>
    <span class="ticker">{esc(ticker)}</span>
  </td>
  <td class="num">{score_cell('survival_score')}</td>
  <td class="num">{score_cell('growth_score')}</td>
  <td class="num">{score_cell('valuation_score')}</td>
  <td class="num">{score_cell('investment_score')}</td>
  <td><span class="band {band_class(band)}">{esc(band_label(band))}</span></td>
  <td class="num">{fpct(total_return)}<span class="score-word">per year</span></td>
</tr>
<tr class="detail-row" id="{esc(detail_id)}" hidden>
  <td colspan="8">{detail_html(ticker, metric, score, scenarios)}</td>
</tr>
"""

def summary_cards(scores: list[dict], metrics: dict[str, dict]) -> str:
    survival = [row["survival_score"] for row in scores if row.get("survival_score") is not None]
    growth = [row["growth_score"] for row in scores if row.get("growth_score") is not None]
    valuation = [row["valuation_score"] for row in scores if row.get("valuation_score") is not None]
    investment = [row["investment_score"] for row in scores if row.get("investment_score") is not None]
    high = sum(1 for row in scores if row.get("distress_band") in ("High", "Elevated"))
    top = max(scores, key=lambda row: row.get("investment_score") or -1)
    top_company = metrics.get(top["ticker"], {}).get("company", top["ticker"])
    avg_survival = statistics.fmean(survival)
    avg_growth = statistics.fmean(growth)
    avg_valuation = statistics.fmean(valuation)
    avg_investment = statistics.fmean(investment)
    cards = [
        ("Average financial safety", f"{avg_survival:.1f}", score_word("survival_score", avg_survival),
         score_class(avg_survival)),
        ("Average growth potential", f"{avg_growth:.1f}", score_word("growth_score", avg_growth),
         score_class(avg_growth)),
        ("Average price attractiveness", f"{avg_valuation:.1f}", score_word("valuation_score", avg_valuation),
         score_class(avg_valuation)),
        ("Average overall appeal", f"{avg_investment:.1f}", score_word("investment_score", avg_investment),
         score_class(avg_investment)),
        ("Some or high risk", str(high), "companies to watch", "warn" if high else "good"),
        ("Top overall appeal", top_company, f"{top['investment_score']:.1f}/100", "good"),
    ]
    return "".join(
        f'<div class="summary-card {css_class}"><span>{esc(label)}</span>'
        f'<strong>{esc(value)}</strong><small>{esc(note)}</small></div>'
        for label, value, note, css_class in cards
    )

CSS = """
:root {
  --bg: #f4f6f8;
  --card: #ffffff;
  --text: #17202a;
  --muted: #667085;
  --border: #d9dee5;
  --accent: #0b5cad;
  --good-bg: #e6f4ea; --good: #1a7f37;
  --ok-bg: #e7f0fb; --ok: #1d5fa8;
  --warn-bg: #fff1e0; --warn: #a75b00;
  --bad-bg: #fde8e8; --bad: #b42318;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.45;
}
main { max-width: 1440px; margin: 0 auto; padding: 28px 22px 48px; }
header { margin-bottom: 18px; }
h1 { font-size: clamp(1.5rem, 3vw, 2.1rem); margin: 0 0 6px; letter-spacing: -0.02em; }
.subtitle { color: var(--muted); margin: 0; max-width: 900px; }
.summary { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 10px; margin: 20px 0; }
.summary-card { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.summary-card.good { border-left-color: #2ca02c; }
.summary-card.ok { border-left-color: #1d5fa8; }
.summary-card.warn { border-left-color: #d97706; }
.summary-card.bad { border-left-color: #b42318; }
.summary-card span { display: block; color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }
.summary-card strong { display: block; font-size: 1.15rem; line-height: 1.2; margin-top: 2px; }
.summary-card small { color: var(--muted); font-size: 0.78rem; }
.controls {
  display: grid;
  grid-template-columns: minmax(210px, 2fr) repeat(4, minmax(125px, 1fr));
  gap: 10px;
  align-items: end;
  margin: 18px 0 12px;
}
.controls label { display: grid; gap: 4px; color: var(--muted); font-size: 0.8rem; }
input, select {
  width: 100%;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--text);
  border-radius: 8px;
  padding: 9px 10px;
  font: inherit;
}
input:focus, select:focus, .company-row:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.result-count { color: var(--muted); font-size: 0.9rem; margin: 0 0 8px; }
.table-wrap { background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: auto; }
table { width: 100%; border-collapse: collapse; }
thead th {
  position: sticky; top: 0; z-index: 1;
  background: #eef2f6; color: #344054;
  font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em;
  text-align: left; padding: 10px 11px; border-bottom: 1px solid var(--border);
}
tbody td { padding: 10px 11px; border-bottom: 1px solid #edf0f3; vertical-align: middle; }
tbody tr.company-row { cursor: pointer; }
tbody tr.company-row:hover { background: #f8fafc; }
tbody tr.company-row[aria-expanded="true"] { background: #f1f6fc; }
.ticker { display: block; color: var(--muted); font-size: 0.76rem; font-variant-numeric: tabular-nums; }
.model { color: var(--muted); font-size: 0.78rem; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.rank { color: var(--muted); width: 42px; text-align: right; font-variant-numeric: tabular-nums; }
.score, .band {
  display: inline-block; min-width: 48px; text-align: center;
  border-radius: 999px; padding: 3px 8px; font-size: 0.78rem; font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.score.good, .band.low { background: var(--good-bg); color: var(--good); }
.score.ok, .band.moderate { background: var(--ok-bg); color: var(--ok); }
.score.warn, .band.elevated { background: var(--warn-bg); color: var(--warn); }
.score.bad, .band.high { background: var(--bad-bg); color: var(--bad); }
.score.muted, .band.muted { background: #eef0f2; color: var(--muted); }
.score-word { display: block; color: var(--muted); font-size: 0.68rem; font-weight: 500; margin-top: 2px; line-height: 1.1; }
.guide { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin: 16px 0; }
.guide summary { cursor: pointer; font-weight: 700; }
.guide-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 12px; }
.guide-grid div { background: #f8fafc; border-radius: 8px; padding: 10px 12px; }
.guide-grid strong { display: block; font-size: 0.86rem; }
.guide-grid span { color: var(--muted); font-size: 0.78rem; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; color: var(--muted); font-size: 0.78rem; margin: 0 0 10px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }
.dot.good { background: #2ca02c; } .dot.ok { background: #1d5fa8; } .dot.warn { background: #d97706; } .dot.bad { background: #b42318; }
.detail-row > td { padding: 0; background: #fbfcfe; }
.detail-scores { margin-top: 14px; }
.plain-summary { background: #f8fafc; border-left: 3px solid var(--accent); padding: 9px 12px; margin: 10px 0 0; font-size: 0.9rem; }
.risk-line { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; margin-top: 10px; font-size: 0.86rem; }
.metric-table thead th { position: static; background: #f4f6f8; }
.metric-table td { padding: 7px 9px; font-size: 0.84rem; vertical-align: top; }
.metric-table td:first-child { width: 26%; font-weight: 600; }
.metric-table td:last-child { color: var(--muted); }
.detail { padding: 18px 20px 22px; border-bottom: 1px solid var(--border); }
.detail-head { display: flex; justify-content: space-between; gap: 14px; align-items: start; }
.detail-head h2 { margin: 0 0 2px; font-size: 1.25rem; }
.detail-head p { margin: 0; color: var(--muted); font-size: 0.88rem; }
.report-link { color: var(--accent); text-decoration: none; font-weight: 600; white-space: nowrap; }
.report-link:hover { text-decoration: underline; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 14px; }
.detail-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.detail-card.wide { grid-column: 1 / -1; }
.detail-card h3 { margin: 0 0 10px; font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
.score-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.score-card { background: #f8fafc; border-radius: 8px; padding: 8px; }
.score-label { display: block; color: var(--muted); font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.04em; }
.score-note { display: block; color: var(--muted); font-size: 0.72rem; margin-top: 3px; }
.band-line { display: flex; align-items: center; gap: 9px; margin-top: 10px; flex-wrap: wrap; }
.kv-grid { display: grid; grid-template-columns: repeat(2, minmax(120px, 1fr)); gap: 5px 14px; }
.kv { display: flex; justify-content: space-between; gap: 10px; border-bottom: 1px dotted #e2e6ea; padding: 4px 0; font-size: 0.86rem; }
.kv span { color: var(--muted); }
.current { color: var(--muted); margin: 0 0 8px; font-size: 0.88rem; }
.mini-wrap { border: 0; border-radius: 0; overflow: visible; }
.mini thead th { position: static; background: #f4f6f8; }
.mini td, .mini th { padding: 7px 9px; font-size: 0.84rem; }
.drivers { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.drivers h4 { margin: 0 0 6px; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
.chip { display: inline-block; background: #eef2f6; border-radius: 999px; padding: 4px 9px; margin: 0 5px 5px 0; font-size: 0.78rem; }
.chip strong { color: var(--accent); }
.notes { color: var(--muted); font-size: 0.82rem; margin: 10px 0 0; }
.muted { color: var(--muted); }
.small { font-size: 0.78rem; }
.methodology { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin-top: 16px; }
.methodology summary { cursor: pointer; font-weight: 700; }
.methodology p, .methodology li { color: var(--muted); }
footer { margin-top: 18px; color: var(--muted); font-size: 0.82rem; }
@media (max-width: 1000px) {
  .summary { grid-template-columns: repeat(3, 1fr); }
  .controls { grid-template-columns: 1fr 1fr; }
  .guide-grid { grid-template-columns: 1fr 1fr; }
  .detail-grid, .drivers { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  main { padding: 18px 12px 40px; }
  .summary { grid-template-columns: 1fr 1fr; }
  .controls { grid-template-columns: 1fr; }
  .score-grid { grid-template-columns: 1fr 1fr; }
  .kv-grid { grid-template-columns: 1fr; }
  .detail-head { display: block; }
  .report-link { display: inline-block; margin-top: 6px; }
}
"""

JS = """
(() => {
  const tbody = document.querySelector("#companies tbody");
  const search = document.querySelector("#search");
  const indexFilter = document.querySelector("#index-filter");
  const modelFilter = document.querySelector("#model-filter");
  const bandFilter = document.querySelector("#band-filter");
  const sortSelect = document.querySelector("#sort-by");
  const resultCount = document.querySelector("#result-count");

  function companyRows() {
    return Array.from(tbody.querySelectorAll(".company-row"));
  }

  function detailFor(row) {
    return document.getElementById(row.dataset.detail);
  }

  function toggle(row) {
    const detail = detailFor(row);
    if (!detail) return;
    const isOpen = row.getAttribute("aria-expanded") === "true";
    row.setAttribute("aria-expanded", String(!isOpen));
    detail.hidden = isOpen;
  }

  function refreshCount() {
    const visible = companyRows().filter((row) => !row.hidden).length;
    resultCount.textContent = `Showing ${visible} of ${companyRows().length} companies`;
    let rank = 0;
    companyRows().forEach((row) => {
      if (!row.hidden) {
        rank += 1;
        row.querySelector(".rank").textContent = rank;
      }
    });
  }

  function applyFilters() {
    const term = search.value.trim().toLowerCase();
    const selectedIndex = indexFilter.value;
    const selectedModel = modelFilter.value;
    const selectedBand = bandFilter.value;
    companyRows().forEach((row) => {
      const matches =
        (!term || row.dataset.search.includes(term)) &&
        (!selectedIndex || row.dataset.index === selectedIndex) &&
        (!selectedModel || row.dataset.model === selectedModel) &&
        (!selectedBand || row.dataset.band === selectedBand);
      row.hidden = !matches;
      const detail = detailFor(row);
      if (detail && !matches) {
        detail.hidden = true;
        row.setAttribute("aria-expanded", "false");
      }
    });
    refreshCount();
  }

  function sortRows() {
    const key = sortSelect.value;
    const rows = companyRows().sort((a, b) => {
      if (key === "company") {
        return a.dataset.company.localeCompare(b.dataset.company);
      }
      return (Number(b.dataset[key]) || -999) - (Number(a.dataset[key]) || -999);
    });
    rows.forEach((row) => {
      tbody.appendChild(row);
      const detail = detailFor(row);
      if (detail) tbody.appendChild(detail);
    });
  }

  companyRows().forEach((row) => {
    row.addEventListener("click", () => toggle(row));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle(row);
      }
    });
  });

  search.addEventListener("input", applyFilters);
  indexFilter.addEventListener("change", applyFilters);
  modelFilter.addEventListener("change", applyFilters);
  bandFilter.addEventListener("change", applyFilters);
  sortSelect.addEventListener("change", () => {
    sortRows();
    applyFilters();
  });

  sortRows();
  applyFilters();
})();
"""


def generate() -> Path:
    scores_rows = load_numeric_tsv(SCORES_PATH)
    metrics_rows = load_numeric_tsv(METRICS_PATH)
    scenario_rows = load_numeric_tsv(SCENARIOS_PATH)
    metrics = by_ticker(metrics_rows)
    scores = by_ticker(scores_rows)
    scenarios = scenario_lookup(scenario_rows)
    analysis_manifest = json.loads(ANALYSIS_MANIFEST_PATH.read_text()) if ANALYSIS_MANIFEST_PATH.exists() else {}
    collection_manifest = json.loads(COLLECTION_MANIFEST_PATH.read_text()) if COLLECTION_MANIFEST_PATH.exists() else {}

    if not scores_rows:
        raise SystemExit("Missing score data; run 'python3 scripts/fundamentals.py analyze' first")

    ranked = sorted(
        scores_rows,
        key=lambda row: (row.get("investment_score") is None,
                         -(row.get("investment_score") or 0)),
    )
    models = sorted({row.get("model_type", "") for row in scores_rows})
    indices = sorted({
        metrics.get(row["ticker"], {}).get("index_name", "")
        for row in scores_rows
        if metrics.get(row["ticker"], {}).get("index_name")
    })
    bands = ["Low", "Moderate", "Elevated", "High"]
    rows_html = "".join(
        company_row(index, row["ticker"], metrics.get(row["ticker"], {}), row,
                    scenarios.get(row["ticker"], {}))
        for index, row in enumerate(ranked, start=1)
    )
    model_options = "".join(
        f'<option value="{esc(model)}">{esc(model_label(model))}</option>' for model in models
    )
    index_options = "".join(
        f'<option value="{esc(index)}">{esc(index)}</option>' for index in indices
    )
    band_options = "".join(
        f'<option value="{esc(band)}">{esc(band_label(band))}</option>' for band in bands
    )
    generated = date.today().isoformat()
    model_version = analysis_manifest.get("model_version", "1.0")
    quarterly_rows = collection_manifest.get("n_quarterly_rows", 0)
    companies = len(scores_rows)

    page = []
    page.append("<!doctype html>")
    page.append('<html lang="en">')
    page.append("<head>")
    page.append('<meta charset="utf-8">')
    page.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    page.append("<title>DAX · MDAX · SDAX — Company Health &amp; Investment Dashboard</title>")
    page.append("<style>")
    page.append(CSS)
    page.append("</style>")
    page.append("</head>")
    page.append("<body>")
    page.append("<main>")
    page.append("<header>")
    page.append("<h1>DAX · MDAX · SDAX — Company Health &amp; Investment Dashboard</h1>")
    page.append(
        f'<p class="subtitle">A simple view of {companies} German listed companies. Scores run from 0 to 100; '
        "higher is generally better. Updated " + esc(generated) + ". Not investment advice.</p>"
    )
    page.append("</header>")
    page.append('<details class="guide" open>')
    page.append("<summary>How to read this dashboard</summary>")
    page.append('<div class="guide-grid">')
    page.append('<div><strong>Financial safety</strong><span>Can the company carry its debt and stay healthy? Higher is safer.</span></div>')
    page.append('<div><strong>Growth potential</strong><span>Can the business grow over the next 3–10 years? Higher is better.</span></div>')
    page.append('<div><strong>Price attractiveness</strong><span>Are the shares cheap or expensive versus their own history? Higher means cheaper.</span></div>')
    page.append('<div><strong>Overall appeal</strong><span>40% growth + 30% safety + 30% price. A high score is not a buy recommendation.</span></div>')
    page.append("</div>")
    page.append('<p class="muted small">Covers the DAX 40, MDAX and SDAX. Click any company for plain-English explanations of the numbers. '
                '“n/a” means the figure was not reported or is not meaningful for that business type. '
                'The 5-year return is a what-if scenario, not a forecast.</p>')
    page.append("</details>")
    page.append(f'<section class="summary">{summary_cards(scores_rows, metrics)}</section>')
    page.append('<section class="controls">')
    page.append('<label>Search<input id="search" type="search" placeholder="Search a company or ticker"></label>')
    page.append(f'<label>Index<select id="index-filter"><option value="">All indices</option>{index_options}</select></label>')
    page.append(f'<label>Business type<select id="model-filter"><option value="">All business types</option>{model_options}</select></label>')
    page.append(f'<label>Risk level<select id="band-filter"><option value="">All risk levels</option>{band_options}</select></label>')
    page.append(
        '<label>Sort by<select id="sort-by">'
        '<option value="investment">Overall appeal (best first)</option>'
        '<option value="survival">Financial safety (safest first)</option>'
        '<option value="growth">Growth potential (highest first)</option>'
        '<option value="valuation">Price attractiveness (cheapest first)</option>'
        '<option value="return">5-year return (highest first)</option>'
        '<option value="company">Company name (A–Z)</option>'
        "</select></label>"
    )
    page.append("</section>")
    page.append('<p id="result-count" class="result-count" aria-live="polite"></p>')
    page.append('<div class="legend">'
                '<span><strong>Colour guide:</strong></span>'
                '<span><i class="dot good"></i>70–100 strong / safe / cheap</span>'
                '<span><i class="dot ok"></i>55–70 good</span>'
                '<span><i class="dot warn"></i>40–55 watch</span>'
                '<span><i class="dot bad"></i>below 40 weak / expensive</span>'
                '<span>Colours are relative, not guarantees.</span></div>')
    page.append('<noscript><p class="muted">JavaScript is disabled; the full table is visible, '
                'but search, filters and company details are unavailable.</p></noscript>')
    page.append('<div class="table-wrap">')
    page.append('<table id="companies">')
    page.append("<thead><tr>")
    page.append("<th>#</th><th>Company</th><th>Financial safety</th><th>Growth potential</th>")
    page.append("<th>Price attractiveness</th><th>Overall appeal</th><th>Risk level</th>")
    page.append("<th>Illustrative yearly return (5-year)</th>")
    page.append("</tr></thead>")
    page.append(f"<tbody>{rows_html}</tbody>")
    page.append("</table>")
    page.append("</div>")
    page.append('<details class="methodology">')
    page.append("<summary>Methodology, caveats and data notes</summary>")
    page.append(
        "<p>The dashboard reads the same data as <code>reports/fundamentals.md</code>. "
        "Scores are 0–100 relative comparisons across the DAX 40, MDAX and SDAX, not probabilities. "
        "Overall appeal = 40% growth + 30% safety + 30% price. Banks, insurers, "
        "utilities, telecoms, holdings, property companies, asset-light companies and "
        "market infrastructure use sector-specific scorecards.</p>"
    )
    page.append(
        "<ul><li>Scores are not calibrated default probabilities and are not validated "
        "against a historical default/return label panel.</li>"
        "<li>Five-year scenarios are mechanical what-if calculations, not forecasts or target prices.</li>"
        "<li>External credit ratings, debt-maturity schedules, order backlog, market share and "
        "organic growth are incomplete for all companies; missing inputs are renormalised.</li>"
        "<li>MDAX/SDAX scenario prices are derived from market cap / diluted shares and beta "
        "defaults to 1.0 where no DAX beta is available. A few recent listings lack enough "
        "market data for a valuation score or scenario.</li>"
        "<li>Quarterly statements are collected for transparency but are not used in the scorecard.</li>"
        "<li>This is a research tool, not investment advice.</li></ul>"
    )
    page.append("</details>")
    page.append("<footer>")
    page.append(
        f'Generated by <code>scripts/web.py</code> from <code>data/fundamentals_*.tsv</code>. '
        'Fundamentals: stockanalysis.com / S&P Global Market Intelligence; prices: CNBC. '
        f'<a href="../reports/fundamentals.md">Read the full report</a> · '
        f'<a href="../reports/README.md">Report index</a>.'
    )
    page.append("</footer>")
    page.append("</main>")
    page.append("<script>")
    page.append(JS)
    page.append("</script>")
    page.append("</body>")
    page.append("</html>")

    WEB_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text("\n".join(page), encoding="utf-8")
    return INDEX_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the static DAX/MDAX/SDAX dashboard.")
    parser.add_argument("--open", action="store_true", help="open the generated page in a browser")
    args = parser.parse_args()
    path = generate()
    print(f"Wrote {path}")
    if args.open:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
