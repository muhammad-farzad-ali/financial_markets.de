#!/usr/bin/env python3
"""Corporate distress prediction and long-term investment forecasting.

This module extends the German-markets pipeline with company fundamentals for
the DAX 40, MDAX and SDAX.  It has three stages, mirroring the price pipeline:

    collect   fetch income statements, balance sheets, cash-flow statements,
              valuation ratios and analyst forecasts from stockanalysis.com
              (S&P Global Market Intelligence data), then normalise them into
              ``data/fundamentals.tsv``.  A separate quarterly panel is written
              to ``data/fundamentals_quarterly.tsv``.
    analyze   compute distress, growth and investment scores plus five-year
              return scenarios, writing the metrics/scores/scenarios TSVs.
    report    render ``reports/fundamentals.md`` and the score charts.

The analysis is deliberately rule-based and transparent.  Historical default
and long-run growth labels are not available for this universe, so the scores are
relative scorecards, not calibrated probabilities or forecasts.

Only the Python standard library is used.  Network requests are throttled and
cached by :mod:`lib`; the collector is the only stage that touches the network.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from lib import (
    CHARTS_DIR,
    CONFIG_DIR,
    DATA_DIR,
    RAW_DIR,
    REPORTS_DIR,
    fmt_int,
    fmt_num,
    fmt_pct,
    iso_now,
    md_table,
    parse_number,
    read_json,
    read_tsv,
    safe_div,
    write_json,
    write_tsv,
)
from svg import PALETTE, SVG, frame, legend, nice_ticks

SOURCES_PATH = CONFIG_DIR / "fundamentals_sources.tsv"
FUNDAMENTALS_PATH = DATA_DIR / "fundamentals.tsv"
FORECASTS_PATH = DATA_DIR / "forecasts.tsv"
QUARTERLY_PATH = DATA_DIR / "fundamentals_quarterly.tsv"
METRICS_PATH = DATA_DIR / "fundamentals_metrics.tsv"
SCORES_PATH = DATA_DIR / "fundamentals_scores.tsv"
SCENARIOS_PATH = DATA_DIR / "fundamentals_scenarios.tsv"
MANIFEST_PATH = DATA_DIR / "fundamentals_manifest.json"
ANALYSIS_MANIFEST_PATH = DATA_DIR / "fundamentals_analysis_manifest.json"
REPORT_PATH = REPORTS_DIR / "fundamentals.md"
CHART_SCATTER = CHARTS_DIR / "fundamentals_scores.svg"
CHART_SURVIVAL = CHARTS_DIR / "fundamentals_survival.svg"
CHART_VALUATION = CHARTS_DIR / "fundamentals_growth_valuation.svg"

RAW_FUNDAMENTALS = RAW_DIR / "fundamentals"

STATEMENTS = ("income", "balance", "cashflow", "ratios")
STATEMENT_PATHS = {
    "income": "income-statement",
    "balance": "balance-sheet",
    "cashflow": "cash-flow-statement",
    "ratios": "ratios",
}

# Model version is written into the manifest and the report.
MODEL_VERSION = "1.0"
RISK_FREE_FALLBACK = 0.0327
EQUITY_RISK_PREMIUM = 0.055
DEFAULT_COST_OF_DEBT = 0.045
SCENARIO_YEARS = 5

MONETARY_FIELDS = {
    "revenue", "gross_profit", "ebit", "ebitda", "net_income",
    "interest_expense", "pretax_income", "income_tax", "rnd_expense",
    "total_assets", "total_liabilities", "total_equity", "current_assets",
    "current_liabilities", "cash_st_investments", "receivables", "inventory",
    "retained_earnings", "total_debt", "short_term_debt", "working_capital",
    "goodwill", "net_ppe", "operating_cash_flow", "capex", "free_cash_flow",
    "dividends_paid", "buybacks", "market_cap", "enterprise_value",
    "order_backlog", "financial_services_debt", "financial_services_liabilities",
    "financial_services_loans", "ffo", "affo",
}
PER_SHARE_FIELDS = {"eps_diluted", "dps", "book_value_per_share", "fcf_per_share"}
RATIO_FIELDS = {
    "pe", "forward_pe", "ev_ebitda", "p_fcf", "fcf_yield", "dividend_yield",
    "roic", "roe", "roa", "asset_turnover", "current_ratio", "debt_ebitda",
    "net_debt_ebitda", "pb", "ps", "ev_revenue", "earnings_yield",
    "gross_margin", "operating_margin", "profit_margin", "fcf_margin",
    "tax_rate", "cost_income", "loan_loss_ratio", "policy_benefit_ratio",
    "expense_ratio", "ffo_margin",
}

BASE_COLUMNS = [
    "ticker", "company", "industry", "sector", "nace_code", "nace_label",
    "model_type", "period", "period_end", "is_ttm", "currency", "units",
    "source", "source_date",
]
DATA_COLUMNS = [
    "revenue", "gross_profit", "ebit", "ebitda", "net_income",
    "interest_expense", "pretax_income", "income_tax", "rnd_expense",
    "eps_diluted", "dps", "shares_diluted", "book_value_per_share",
    "total_assets", "total_liabilities", "total_equity", "current_assets",
    "current_liabilities", "cash_st_investments", "receivables", "inventory",
    "retained_earnings", "total_debt", "short_term_debt", "working_capital",
    "goodwill", "net_ppe", "operating_cash_flow", "capex", "free_cash_flow",
    "fcf_per_share", "dividends_paid", "buybacks", "market_cap",
    "enterprise_value", "pe", "forward_pe", "ev_ebitda", "p_fcf",
    "fcf_yield", "dividend_yield", "roic", "roe", "roa", "asset_turnover",
    "current_ratio", "debt_ebitda", "net_debt_ebitda", "pb", "ps",
    "ev_revenue", "earnings_yield", "gross_margin", "operating_margin",
    "profit_margin", "fcf_margin", "tax_rate", "cost_income",
    "loan_loss_ratio", "policy_benefit_ratio", "expense_ratio", "ffo_margin",
    "order_backlog", "financial_services_debt",
    "financial_services_liabilities", "financial_services_loans", "ffo",
    "affo", "notes",
]
FUNDAMENTALS_COLUMNS = BASE_COLUMNS + DATA_COLUMNS

QUARTERLY_COLUMNS = [
    "ticker", "company", "period", "period_end", "fiscal_year",
    "fiscal_quarter", "currency", "units", "source", "source_date",
    "revenue", "gross_profit", "ebit", "ebitda", "net_income",
    "interest_expense", "rnd_expense", "eps_diluted", "total_assets",
    "total_equity", "total_liabilities", "total_debt", "cash_st_investments",
    "operating_cash_flow", "capex", "free_cash_flow", "notes",
]


# ---------------------------------------------------------------------------
# Source configuration
# ---------------------------------------------------------------------------

def load_sources() -> list[dict]:
    if not SOURCES_PATH.exists():
        raise SystemExit(f"Missing source map: {SOURCES_PATH}")
    rows = read_tsv(SOURCES_PATH)
    for row in rows:
        row["model_type"] = (row.get("model_type") or "non_financial").strip()
    return rows


def source_url(source: dict, statement: str, quarterly: bool = False) -> str:
    exchange = source["source_exchange"]
    symbol = source["source_symbol"]
    route = source.get("source_route") or "quote"
    if route == "stocks":
        url = f"https://stockanalysis.com/stocks/{symbol.lower()}/financials/{STATEMENT_PATHS[statement]}/"
    else:
        url = f"https://stockanalysis.com/quote/{exchange}/{symbol}/financials/{STATEMENT_PATHS[statement]}/"
    return url + ("?p=quarterly" if quarterly else "")


def forecast_url(source: dict) -> str:
    exchange = source["source_exchange"]
    symbol = source["source_symbol"]
    route = source.get("source_route") or "quote"
    if route == "stocks":
        return f"https://stockanalysis.com/stocks/{symbol.lower()}/forecast/"
    return f"https://stockanalysis.com/quote/{exchange}/{symbol}/forecast/"


# ---------------------------------------------------------------------------
# Parsing the embedded statement data
# ---------------------------------------------------------------------------

def _parse_js_array(raw: str) -> list:
    raw = raw.replace("void 0", "null").replace("undefined", "null")
    raw = raw.replace('"[PRO]"', "null").replace("[PRO]", "null")
    try:
        return json.loads("[" + raw + "]")
    except json.JSONDecodeError:
        values = []
        for part in raw.split(","):
            part = part.strip()
            if part in ("", "null"):
                values.append(None)
            elif part.startswith('"') and part.endswith('"'):
                values.append(part[1:-1])
            else:
                try:
                    values.append(float(part))
                except ValueError:
                    values.append(part)
        return values


def parse_js_arrays(html: str) -> dict[str, list]:
    """Extract the first flat ``key:[...]`` block from a stockanalysis page.

    The page embeds a JavaScript object (not strict JSON) with one array per
    financial line item.  We only need the flat arrays before the ``map:``
    section; the ``prior``/``ttmPrior`` objects after it repeat keys and would
    otherwise overwrite the main series.
    """
    match = re.search(r"fiscalYear:\[", html)
    if not match:
        return {}
    start = match.start()
    # Quarterly pages place the period-end dates in a ``datekey`` array just
    # before ``fiscalYear``.  Include it so the quarterly collector can attach
    # an ISO period-end date to each row.
    prefix = html[max(0, start - 500):start]
    datekey = re.search(r"datekey:\[[^\]]*\]", prefix)
    if datekey:
        start = max(0, start - 500) + datekey.start()
    end = html.find(",map:", start)
    if end < 0:
        end = html.find(",epsBasis:", start)
    if end < 0:
        end = start + 250_000
    blob = html[start:end]
    output: dict[str, list] = {}
    for key_match in re.finditer(r"([A-Za-z][A-Za-z0-9]*):\[([^\]]*)\]", blob):
        output[key_match.group(1)] = _parse_js_array(key_match.group(2))
    return output


def parse_metadata(html: str) -> dict:
    metadata = {}
    currency = re.search(r"Financials in millions ([A-Z]{3})", html)
    if currency:
        metadata["currency"] = currency.group(1)
    fiscal = re.search(r"Fiscal year is ([A-Za-z]+ - [A-Za-z]+)", html)
    if fiscal:
        metadata["fiscal_year_range"] = fiscal.group(1)
    trailing = re.search(r'lastTrailingDate:"([^"]+)"', html)
    if trailing:
        metadata["last_trailing_date"] = trailing.group(1)
    updated = re.search(r'lastUpdated:"([^"]+)"', html)
    if updated:
        metadata["last_updated"] = updated.group(1)
    return metadata


def parse_forecast(html: str) -> dict:
    arrays = parse_js_arrays(html)
    if not arrays:
        return {}
    years = [str(y) for y in arrays.get("fiscalYear", [])]
    output = {"fiscal_year": years}
    for key in ("revenue", "adjustedEps", "revenueGrowth", "adjustedEpsGrowth",
                "operatingIncome", "grossMargin", "freeCashFlow"):
        values = arrays.get(key, [])
        output[key] = values
    # The forecast table has one row per fiscal year; the last year with a
    # numeric value is the nearest analyst estimate.  Find the first future
    # year (2026 or later) with a revenue estimate.
    for index, year in enumerate(years):
        try:
            year_number = int(year)
        except ValueError:
            continue
        if year_number >= 2026:
            revenue = output.get("revenue", [])
            eps = output.get("adjustedEps", [])
            if index < len(revenue) and revenue[index] is not None:
                output["next_year"] = year
                output["next_revenue"] = parse_number(revenue[index])
                growth = output.get("revenueGrowth", [])
                output["next_revenue_growth"] = (
                    parse_number(growth[index]) if index < len(growth) else None
                )
                output["next_eps"] = parse_number(eps[index]) if index < len(eps) else None
                eps_growth = output.get("adjustedEpsGrowth", [])
                output["next_eps_growth"] = (
                    parse_number(eps_growth[index]) if index < len(eps_growth) else None
                )
                break
    return output


def parse_float(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def pick(arrays: dict, *keys, index: int):
    for key in keys:
        values = arrays.get(key)
        if values and index < len(values):
            value = parse_float(values[index])
            if value is not None:
                return value
    return None


def pick_sum(arrays: dict, *keys, index: int):
    values = [pick(arrays, key, index=index) for key in keys]
    values = [value for value in values if value is not None]
    return sum(values) if values else None


def scaled(value, factor=1_000_000.0):
    return value / factor if value is not None else None


def month_number(name: str) -> int | None:
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
        "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
        "november": 11, "december": 12,
    }
    return months.get(name.strip().lower())


def last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def fiscal_period_end(metadata: dict, year: str, is_ttm: bool) -> str:
    if is_ttm:
        trailing = metadata.get("last_trailing_date")
        if trailing:
            try:
                return datetime.strptime(trailing, "%b %d, %Y").date().isoformat()
            except ValueError:
                pass
        return date.today().isoformat()
    fiscal_range = metadata.get("fiscal_year_range", "January - December")
    end_month_name = fiscal_range.split("-")[-1].strip()
    month = month_number(end_month_name) or 12
    try:
        return last_day_of_month(int(year), month).isoformat()
    except ValueError:
        return f"{year}-12-31"


def detect_currency(html: str) -> str:
    match = re.search(r"Financials in millions ([A-Z]{3})", html)
    return match.group(1) if match else "EUR"


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def collect_company(source: dict, refresh: bool = False) -> tuple[list[dict], dict]:
    """Fetch and normalise one company's statements.

    Returns ``(rows, metadata)`` where rows is the long-format list of six
    periods (TTM plus five fiscal years).  Missing values are left as None.
    """
    from lib import fetch_text  # local import keeps the analysis stage network-free

    ticker = source["ticker"]
    raw_dir = RAW_FUNDAMENTALS
    raw_dir.mkdir(parents=True, exist_ok=True)
    statements = {}
    metadata = {}
    fetch_errors = []
    for statement in STATEMENTS:
        url = source_url(source, statement)
        cache_path = raw_dir / f"{ticker.replace('.', '_')}_{statement}.html"
        try:
            html = fetch_text(url, cache_path, refresh=refresh)
        except Exception as exc:  # noqa: BLE001 - report and continue
            fetch_errors.append(f"{statement}: {exc}")
            statements[statement] = {}
            continue
        statements[statement] = parse_js_arrays(html)
        if statement == "income":
            metadata = parse_metadata(html)
            metadata["currency"] = detect_currency(html)
    income = statements.get("income", {})
    balance = statements.get("balance", {})
    cashflow = statements.get("cashflow", {})
    ratios = statements.get("ratios", {})
    years = [str(y) for y in income.get("fiscalYear", [])]
    if not years:
        years = [str(y) for y in balance.get("fiscalYear", [])]
    rows = []
    for index, year in enumerate(years[:6]):
        is_ttm = index == 0
        period = "TTM" if is_ttm else f"FY{year}"
        period_end = fiscal_period_end(metadata, year, is_ttm)

        revenue = pick(income, "revenue", "revenueIns", "revenueRE", "revenueBank", "operatingRevenue", index=index)
        gross_profit = pick(income, "gp", index=index)
        cost_of_revenue = pick(income, "cor", index=index)
        if gross_profit is None and revenue is not None and cost_of_revenue is not None:
            gross_profit = revenue - cost_of_revenue
        ebit = pick(income, "opinc", "ebit", "opincIns", "operatingIncomeRE", index=index)
        ebitda = pick(income, "ebitda", index=index)
        if ebitda is None:
            depreciation = pick(income, "depAmorEbitda", "depAmorCF", index=index)
            if ebit is not None and depreciation is not None:
                ebitda = ebit + depreciation
        net_income = pick(income, "netinc", "netinccmn", index=index)
        interest_expense = pick(income, "interestExpense", "interestExpenseRE",
                                "interestExpenseBank", index=index)
        pretax = pick(income, "pretax", "ebtBank", "ebtExclRE", index=index)
        tax = pick(income, "taxexp", index=index)
        rnd = pick(income, "rnd", index=index)
        eps = pick(income, "epsdil", "epsBasic", index=index)
        dps = pick(income, "dps", index=index)
        shares = pick(income, "sharesDiluted", "sharesBasic", index=index)

        total_assets = pick(balance, "assets", index=index)
        total_liabilities = pick(balance, "liabilities", index=index)
        total_equity = pick(balance, "equity", index=index)
        if total_liabilities is None and total_assets is not None and total_equity is not None:
            # Banks, insurers and some real-estate reporters only publish
            # total assets and equity; liabilities follow from the balance
            # sheet identity.
            total_liabilities = total_assets - total_equity
        current_assets = pick(balance, "assetsc", index=index)
        current_liabilities = pick(balance, "currentLiabilities", index=index)
        cash = pick(balance, "totalcash", "cashneq", index=index)
        receivables = pick(balance, "receivables", "accountsReceivable", index=index)
        inventory = pick(balance, "inventory", index=index)
        retained_earnings = pick(balance, "retearn", index=index)
        total_debt = pick(balance, "debt", index=index)
        current_debt = pick(balance, "currentPortDebt", index=index)
        current_leases = pick(balance, "currentCapLeases", index=index)
        short_term_debt = None
        if current_debt is not None or current_leases is not None:
            short_term_debt = (current_debt or 0.0) + (current_leases or 0.0)
        working_capital = pick(balance, "workingcapital", index=index)
        if working_capital is None and current_assets is not None and current_liabilities is not None:
            working_capital = current_assets - current_liabilities
        goodwill = pick(balance, "goodwill", index=index)
        net_ppe = pick(balance, "netPPE", index=index)
        book_value_per_share = pick(balance, "bvps", index=index)
        order_backlog = pick(balance, "orderBacklog", index=index)
        fs_debt = pick_sum(balance, "finDivDebtCurrent", "finDivDebtLT", index=index)
        fs_liabilities = pick_sum(balance, "finDivLiabCurrent", "finDivLiabLT", index=index)
        fs_loans = pick_sum(balance, "finDivLoansCurrent", "finDivLoansLT", index=index)
        ffo = pick(income, "ffo", index=index)
        if ffo is None:
            ffo = pick(balance, "ffo", index=index)
        affo = pick(income, "affo", index=index)
        if affo is None:
            affo = pick(balance, "affo", index=index)

        operating_cash_flow = pick(cashflow, "ncfo", index=index)
        capex = pick(cashflow, "capex", index=index)
        free_cash_flow = pick(cashflow, "fcf", index=index)
        if free_cash_flow is None and operating_cash_flow is not None and capex is not None:
            free_cash_flow = operating_cash_flow + capex
        fcf_per_share = pick(cashflow, "fcfps", index=index)
        dividends_paid = pick(cashflow, "commonDividendCF", index=index)
        buybacks = pick(cashflow, "commonRepurchased", index=index)

        market_cap = pick(ratios, "marketcap", index=index)
        enterprise_value = pick(ratios, "ev", index=index)
        pe = pick(ratios, "pe", index=index)
        forward_pe = pick(ratios, "peForward", index=index)
        ev_ebitda = pick(ratios, "evebitda", index=index)
        p_fcf = pick(ratios, "pfcf", index=index)
        fcf_yield = pick(ratios, "fcfyield", index=index)
        dividend_yield = pick(ratios, "dividendyield", index=index)
        roic = pick(ratios, "roic", index=index)
        roe = pick(ratios, "roe", index=index)
        roa = pick(ratios, "roa", index=index)
        asset_turnover = pick(ratios, "assetturnover", index=index)
        current_ratio = pick(ratios, "currentratio", index=index)
        debt_ebitda = pick(ratios, "debtebitda", index=index)
        net_debt_ebitda = pick(ratios, "netdebtebitda", index=index)
        pb = pick(ratios, "pb", index=index)
        ps = pick(ratios, "ps", index=index)
        ev_revenue = pick(ratios, "evrevenue", index=index)
        earnings_yield = pick(ratios, "earningsyield", index=index)
        gross_margin = pick(income, "grossMargin", index=index)
        operating_margin = pick(income, "operatingMargin", index=index)
        profit_margin = pick(income, "profitMargin", index=index)
        fcf_margin = pick(income, "fcfMargin", index=index)
        # Some reporters omit the cash-flow FCF line but publish FCF yield or
        # FCF margin.  Reconstruct the absolute FCF so the scorecard can use
        # the same definition across companies.
        if free_cash_flow is None and fcf_yield is not None and market_cap:
            free_cash_flow = fcf_yield * market_cap
        if fcf_per_share is None and free_cash_flow is not None and shares:
            fcf_per_share = free_cash_flow / shares
        if fcf_margin is None and free_cash_flow is not None and revenue:
            fcf_margin = free_cash_flow / revenue
        tax_rate = pick(income, "taxrate", index=index)
        cost_income = None
        loan_loss_ratio = None
        policy_benefit_ratio = None
        expense_ratio = None
        ffo_margin = None
        if source["model_type"] == "bank":
            non_interest_expense = pick(income, "nonInterestExpenseBank", index=index)
            loan_losses = pick(income, "loanLosses", index=index)
            if revenue and non_interest_expense is not None:
                cost_income = non_interest_expense / revenue
            if total_assets and loan_losses is not None:
                # Provision rate on assets is comparable across banks; loan
                # losses divided by revenue overstates the ratio because
                # revenue is a flow, not the loan book.
                loan_loss_ratio = loan_losses / total_assets
        elif source["model_type"] == "insurer":
            policy_benefits = pick(income, "policyBenefits", index=index)
            total_opex = pick(income, "totalOpexIns", index=index)
            if revenue and policy_benefits is not None:
                policy_benefit_ratio = policy_benefits / revenue
            if revenue and total_opex is not None:
                expense_ratio = total_opex / revenue
        elif source["model_type"] == "real_estate":
            if revenue and ffo is not None:
                ffo_margin = ffo / revenue

        row = {
            "ticker": ticker,
            "company": source["company"],
            "industry": source["industry"],
            "sector": source["sector"],
            "nace_code": source["nace_code"],
            "nace_label": source["nace_label"],
            "model_type": source["model_type"],
            "period": period,
            "period_end": period_end,
            "is_ttm": 1 if is_ttm else 0,
            "currency": metadata.get("currency", "EUR"),
            "units": "millions",
            "source": "stockanalysis.com (S&P Global Market Intelligence)",
            "source_date": metadata.get("last_updated", ""),
        }
        monetary = {
            "revenue": revenue, "gross_profit": gross_profit, "ebit": ebit,
            "ebitda": ebitda, "net_income": net_income,
            "interest_expense": abs(interest_expense) if interest_expense is not None else None,
            "pretax_income": pretax, "income_tax": tax, "rnd_expense": rnd,
            "total_assets": total_assets, "total_liabilities": total_liabilities,
            "total_equity": total_equity, "current_assets": current_assets,
            "current_liabilities": current_liabilities,
            "cash_st_investments": cash, "receivables": receivables,
            "inventory": inventory, "retained_earnings": retained_earnings,
            "total_debt": total_debt, "short_term_debt": short_term_debt,
            "working_capital": working_capital, "goodwill": goodwill,
            "net_ppe": net_ppe, "operating_cash_flow": operating_cash_flow,
            "capex": capex, "free_cash_flow": free_cash_flow,
            "dividends_paid": dividends_paid, "buybacks": buybacks,
            "market_cap": market_cap, "enterprise_value": enterprise_value,
            "order_backlog": order_backlog,
            "financial_services_debt": fs_debt,
            "financial_services_liabilities": fs_liabilities,
            "financial_services_loans": fs_loans,
            "ffo": ffo, "affo": affo,
        }
        for key, value in monetary.items():
            row[key] = scaled(value)
        per_share = {
            "eps_diluted": eps, "dps": dps,
            "book_value_per_share": book_value_per_share,
            "fcf_per_share": fcf_per_share,
            "shares_diluted": scaled(shares),
        }
        row.update(per_share)
        ratio_values = {
            "pe": pe, "forward_pe": forward_pe, "ev_ebitda": ev_ebitda,
            "p_fcf": p_fcf, "fcf_yield": fcf_yield,
            "dividend_yield": dividend_yield, "roic": roic, "roe": roe,
            "roa": roa, "asset_turnover": asset_turnover,
            "current_ratio": current_ratio, "debt_ebitda": debt_ebitda,
            "net_debt_ebitda": net_debt_ebitda, "pb": pb, "ps": ps,
            "ev_revenue": ev_revenue, "earnings_yield": earnings_yield,
            "gross_margin": gross_margin, "operating_margin": operating_margin,
            "profit_margin": profit_margin, "fcf_margin": fcf_margin,
            "tax_rate": tax_rate, "cost_income": cost_income,
            "loan_loss_ratio": loan_loss_ratio,
            "policy_benefit_ratio": policy_benefit_ratio,
            "expense_ratio": expense_ratio, "ffo_margin": ffo_margin,
        }
        row.update(ratio_values)
        row["notes"] = source.get("notes", "")
        rows.append(row)

    metadata["fetch_errors"] = fetch_errors
    metadata["n_periods"] = len(rows)
    metadata["years"] = years[:6]
    return rows, metadata


def collect_company_quarterly(source: dict, refresh: bool = False) -> tuple[list[dict], dict]:
    """Fetch quarterly income, balance-sheet and cash-flow statements.

    Quarterly values are stored in millions, like the annual table.  The
    quarterly panel is collected for transparency and is not used by the
    scorecard, which relies on the annual/TTM dataset.
    """
    from lib import fetch_text  # local import keeps the analysis stage network-free

    ticker = source["ticker"]
    raw_dir = RAW_FUNDAMENTALS
    raw_dir.mkdir(parents=True, exist_ok=True)
    statements = {}
    metadata = {}
    fetch_errors = []
    for statement in ("income", "balance", "cashflow"):
        cache_path = raw_dir / f"{ticker.replace('.', '_')}_{statement}_quarterly.html"
        try:
            html = fetch_text(source_url(source, statement, quarterly=True),
                              cache_path, refresh=refresh)
        except Exception as exc:  # noqa: BLE001 - report and continue
            fetch_errors.append(f"quarterly {statement}: {exc}")
            statements[statement] = {}
            continue
        statements[statement] = parse_js_arrays(html)
        if statement == "income":
            metadata = parse_metadata(html)
            metadata["currency"] = detect_currency(html)
    income = statements.get("income", {})
    balance = statements.get("balance", {})
    cashflow = statements.get("cashflow", {})
    datekeys = income.get("datekey") or balance.get("datekey") or []
    years = income.get("fiscalYear") or balance.get("fiscalYear") or []
    quarters = income.get("fiscalQuarter") or balance.get("fiscalQuarter") or []
    rows = []
    for index, datekey in enumerate(datekeys[:20]):
        year = years[index] if index < len(years) else ""
        quarter = quarters[index] if index < len(quarters) else ""
        period = f"{year}-{quarter}" if year and quarter else str(datekey)
        revenue = pick(income, "revenue", "revenueIns", "revenueRE",
                       "revenueBank", "operatingRevenue", index=index)
        gross_profit = pick(income, "gp", index=index)
        cost_of_revenue = pick(income, "cor", index=index)
        if gross_profit is None and revenue is not None and cost_of_revenue is not None:
            gross_profit = revenue - cost_of_revenue
        ebit = pick(income, "opinc", "ebit", "opincIns", "operatingIncomeRE", index=index)
        ebitda = pick(income, "ebitda", index=index)
        if ebitda is None:
            depreciation = pick(income, "depAmorEbitda", "depAmorCF", index=index)
            if ebit is not None and depreciation is not None:
                ebitda = ebit + depreciation
        total_assets = pick(balance, "assets", index=index)
        total_equity = pick(balance, "equity", index=index)
        total_liabilities = pick(balance, "liabilities", index=index)
        if total_liabilities is None and total_assets is not None and total_equity is not None:
            total_liabilities = total_assets - total_equity
        operating_cash_flow = pick(cashflow, "ncfo", index=index)
        capex = pick(cashflow, "capex", index=index)
        free_cash_flow = pick(cashflow, "fcf", index=index)
        if free_cash_flow is None and operating_cash_flow is not None and capex is not None:
            free_cash_flow = operating_cash_flow + capex
        rows.append({
            "ticker": ticker,
            "company": source["company"],
            "period": period,
            "period_end": datekey,
            "fiscal_year": year,
            "fiscal_quarter": quarter,
            "currency": metadata.get("currency", ""),
            "units": "millions",
            "source": "stockanalysis.com",
            "source_date": metadata.get("last_updated", ""),
            "revenue": scaled(revenue),
            "gross_profit": scaled(gross_profit),
            "ebit": scaled(ebit),
            "ebitda": scaled(ebitda),
            "net_income": scaled(pick(income, "netinc", "netinccmn", index=index)),
            "interest_expense": scaled(pick(income, "interestExpense", "interestExpenseRE",
                                            "interestExpenseBank", index=index)),
            "rnd_expense": scaled(pick(income, "rnd", index=index)),
            "eps_diluted": pick(income, "epsdil", "epsBasic", index=index),
            "total_assets": scaled(total_assets),
            "total_equity": scaled(total_equity),
            "total_liabilities": scaled(total_liabilities),
            "total_debt": scaled(pick(balance, "debt", index=index)),
            "cash_st_investments": scaled(pick(balance, "totalcash", "cashneq", index=index)),
            "operating_cash_flow": scaled(operating_cash_flow),
            "capex": scaled(capex),
            "free_cash_flow": scaled(free_cash_flow),
            "notes": source.get("notes", ""),
        })
    metadata["fetch_errors"] = fetch_errors
    metadata["n_periods"] = len(rows)
    return rows, metadata


def collect_all(refresh: bool = False, only: list[str] | None = None,
                 skip_forecast: bool = False, skip_quarterly: bool = False) -> None:
    sources = load_sources()
    if only:
        wanted = {item.strip().upper() for item in only if item.strip()}
        sources = [source for source in sources if source["ticker"].upper() in wanted]
        if not sources:
            raise SystemExit(f"No matching tickers in {SOURCES_PATH}")
    RAW_FUNDAMENTALS.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    quarterly_rows: list[dict] = []
    coverage = []
    forecasts = []
    for position, source in enumerate(sources, start=1):
        ticker = source["ticker"]
        print(f"[{position:02d}/{len(sources)}] {ticker} {source['company']}", flush=True)
        rows, metadata = collect_company(source, refresh=refresh)
        all_rows.extend(rows)
        coverage.append({
            "ticker": ticker,
            "company": source["company"],
            "index_name": source.get("index_name", ""),
            "model_type": source["model_type"],
            "periods": metadata.get("n_periods", 0),
            "years": metadata.get("years", []),
            "currency": metadata.get("currency", ""),
            "source_date": metadata.get("last_updated", ""),
            "fetch_errors": metadata.get("fetch_errors", []),
        })
        # Quarterly statements are a separate, raw transparency dataset.  A
        # failure does not block the annual/TTM scorecard.
        if not skip_quarterly:
            try:
                qrows, qmeta = collect_company_quarterly(source, refresh=refresh)
                quarterly_rows.extend(qrows)
                coverage[-1]["quarterly_periods"] = qmeta.get("n_periods", 0)
                coverage[-1]["fetch_errors"].extend(qmeta.get("fetch_errors", []))
            except Exception as exc:  # noqa: BLE001
                coverage[-1]["fetch_errors"].append(f"quarterly: {exc}")

        # Forecasts are optional; a failure does not block the fundamentals.
        from lib import fetch_text
        forecast = {}
        if not skip_forecast:
            cache_path = RAW_FUNDAMENTALS / f"{ticker.replace('.', '_')}_forecast.html"
            try:
                forecast_html = fetch_text(forecast_url(source), cache_path, refresh=refresh)
                forecast = parse_forecast(forecast_html)
            except Exception as exc:  # noqa: BLE001
                coverage[-1]["fetch_errors"].append(f"forecast: {exc}")
        if forecast:
            forecasts.append({
                "ticker": ticker,
                "company": source["company"],
                "next_year": forecast.get("next_year", ""),
                "next_revenue": forecast.get("next_revenue"),
                "next_revenue_growth": (
                    forecast.get("next_revenue_growth") / 100.0
                    if forecast.get("next_revenue_growth") is not None else None
                ),
                "next_eps": forecast.get("next_eps"),
                "next_eps_growth": (
                    forecast.get("next_eps_growth") / 100.0
                    if forecast.get("next_eps_growth") is not None else None
                ),
                "source": "stockanalysis.com forecast (S&P Global)",
            })

    # Write the long-format fundamentals table.
    write_tsv(FUNDAMENTALS_PATH, FUNDAMENTALS_COLUMNS,
              [[row.get(column) for column in FUNDAMENTALS_COLUMNS] for row in all_rows])
    if not skip_quarterly:
        write_tsv(QUARTERLY_PATH, QUARTERLY_COLUMNS,
                  [[row.get(column) for column in QUARTERLY_COLUMNS] for row in quarterly_rows])
    forecast_columns = [
        "ticker", "company", "next_year", "next_revenue", "next_revenue_growth",
        "next_eps", "next_eps_growth", "source",
    ]
    write_tsv(FORECASTS_PATH, forecast_columns,
              [[row.get(column) for column in forecast_columns] for row in forecasts])
    n_quarterly_rows = len(quarterly_rows)
    if skip_quarterly and QUARTERLY_PATH.exists():
        n_quarterly_rows = len(read_tsv(QUARTERLY_PATH))
    manifest = {
        "generated_at": iso_now(),
        "model_version": MODEL_VERSION,
        "source": "stockanalysis.com, data by S&P Global Market Intelligence",
        "n_companies": len(sources),
        "n_rows": len(all_rows),
        "n_quarterly_rows": n_quarterly_rows,
        "coverage": coverage,
        "notes": [
            "Financial statement values are normalised to millions of the reported currency.",
            "TTM is the first period; FY2021-FY2025 follow.",
            "Quarterly income, balance-sheet and cash-flow statements are stored separately in data/fundamentals_quarterly.tsv.",
            "Banks, insurers, real estate and holdings use sector-specific scorecards.",
            "Qiagen reports in USD because its primary listing is NYSE.",
        ],
    }
    write_json(MANIFEST_PATH, manifest)
    missing = sum(1 for row in all_rows if row.get("revenue") is None)
    print(f"Wrote {len(all_rows)} rows for {len(sources)} companies to {FUNDAMENTALS_PATH}")
    if not skip_quarterly:
        print(f"Wrote {len(quarterly_rows)} quarterly rows to {QUARTERLY_PATH}")
    print(f"Rows with missing revenue: {missing}")
    print(f"Wrote manifest to {MANIFEST_PATH}")


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def clamp(value: float | None, low: float, high: float) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return max(low, min(high, value))


def linear_score(value: float | None, bad: float, good: float) -> float | None:
    """Map ``bad`` to 0 and ``good`` to 100, clamping outside the range.

    The argument order encodes the direction: for a lower-is-better metric
    such as debt/EBITDA, pass ``bad=6`` and ``good=0``.  Keeping the direction
    in the arguments avoids a separate ``invert`` flag and the sign errors it
    invites.
    """
    if value is None or not math.isfinite(value) or bad == good:
        return None
    score = (value - bad) / (good - bad) * 100.0
    return max(0.0, min(100.0, score))


def cagr(start: float | None, end: float | None, years: float | None) -> float | None:
    if start is None or end is None or years is None or years <= 0:
        return None
    if start <= 0 or end <= 0:
        return None
    try:
        return (end / start) ** (1.0 / years) - 1.0
    except (OverflowError, ZeroDivisionError):
        return None


def percentile_rank(values: list[float], current: float) -> float | None:
    clean = sorted(v for v in values if v is not None and math.isfinite(v))
    if not clean or current is None:
        return None
    below = sum(1 for value in clean if value < current)
    equal = sum(1 for value in clean if value == current)
    return (below + 0.5 * equal) / len(clean)


def quantile(values: list[float], q: float) -> float | None:
    clean = sorted(v for v in values if v is not None and math.isfinite(v))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return clean[lower]
    weight = position - lower
    return clean[lower] * (1.0 - weight) + clean[upper] * weight


def median(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return statistics.median(clean) if clean else None


def weighted_score(components: dict[str, float | None],
                   weights: dict[str, float]) -> tuple[float | None, dict[str, float]]:
    available = {key: value for key, value in components.items()
                 if value is not None and key in weights}
    total_weight = sum(weights[key] for key in available)
    if total_weight <= 0:
        return None, {}
    score = sum(available[key] * weights[key] for key in available) / total_weight
    effective = {key: weights[key] / total_weight for key in available}
    return score, effective


def load_fundamentals() -> dict[str, list[dict]]:
    if not FUNDAMENTALS_PATH.exists():
        raise SystemExit(f"Missing {FUNDAMENTALS_PATH}; run 'fundamentals.py collect' first")
    rows = read_tsv(FUNDAMENTALS_PATH)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        for key in DATA_COLUMNS:
            if key == "notes":
                continue
            row[key] = parse_number(row[key])
        row["is_ttm"] = int(parse_number(row.get("is_ttm")) or 0)
        try:
            row["period_end_date"] = date.fromisoformat(row["period_end"])
        except (KeyError, ValueError):
            row["period_end_date"] = None
        grouped[row["ticker"]].append(row)
    for ticker, items in grouped.items():
        items.sort(key=lambda item: item.get("period_end") or "")
    return grouped


def load_market_metrics() -> dict[str, dict]:
    path = DATA_DIR / "metrics.tsv"
    if not path.exists():
        return {}
    output = {}
    for row in read_tsv(path):
        row["last"] = parse_number(row.get("last"))
        row["beta_dax"] = parse_number(row.get("beta_dax"))
        output[row["series_id"]] = row
    return output


def load_forecasts() -> dict[str, dict]:
    if not FORECASTS_PATH.exists():
        return {}
    output = {}
    for row in read_tsv(FORECASTS_PATH):
        for key in ("next_revenue", "next_revenue_growth", "next_eps", "next_eps_growth"):
            row[key] = parse_number(row.get(key))
        output[row["ticker"]] = row
    return output


def risk_free_rate(market: dict[str, dict]) -> float:
    bund = market.get("DE10Y", {})
    value = bund.get("last")
    if value is not None and 0 < value < 20:
        return value / 100.0
    return RISK_FREE_FALLBACK


def value_or_none(row: dict, key: str):
    value = row.get(key)
    return value if value is not None else None


def gross_margin(row: dict) -> float | None:
    if row.get("gross_margin") is not None:
        return row["gross_margin"]
    return safe_div(row.get("gross_profit"), row.get("revenue"))


def earnings_volatility(history: list[dict]) -> float | None:
    values = [row.get("net_income") for row in history if row.get("net_income") is not None]
    if len(values) < 3:
        return None
    mean_value = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if abs(mean_value) < 1e-9:
        return None
    return min(2.0, sd / abs(mean_value))


def altman_scores(row: dict) -> tuple[float | None, float | None, str]:
    total_assets = row.get("total_assets")
    total_liabilities = row.get("total_liabilities")
    working_capital = row.get("working_capital")
    retained = row.get("retained_earnings")
    ebit = row.get("ebit")
    market_cap = row.get("market_cap")
    revenue = row.get("revenue")
    required = (total_assets, total_liabilities, working_capital, retained,
                ebit, market_cap, revenue)
    if any(value is None for value in required) or total_assets <= 0 or total_liabilities <= 0:
        return None, None, "n/a"
    x1 = working_capital / total_assets
    x2 = retained / total_assets
    x3 = ebit / total_assets
    x4 = market_cap / total_liabilities
    x5 = revenue / total_assets
    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    z2 = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
    if z2 >= 2.6:
        zone = "safe"
    elif z2 >= 1.1:
        zone = "grey"
    else:
        zone = "distress"
    return z, z2, zone


def ohlson_score(row: dict, prior: dict | None) -> tuple[float | None, float | None]:
    """Ohlson (1980) O-score, using operating cash flow as the FFO proxy.

    The original GNP price index is replaced by a constant (100), so the
    resulting probability is a relative model output, not a calibrated
    current default probability.
    """
    total_assets = row.get("total_assets")
    total_liabilities = row.get("total_liabilities")
    working_capital = row.get("working_capital")
    current_liabilities = row.get("current_liabilities")
    current_assets = row.get("current_assets")
    net_income = row.get("net_income")
    operating_cash_flow = row.get("operating_cash_flow")
    required = (total_assets, total_liabilities, working_capital,
                current_liabilities, current_assets, net_income, operating_cash_flow)
    if any(value is None for value in required):
        return None, None
    if total_assets <= 0 or total_liabilities <= 0 or current_assets <= 0 or current_liabilities <= 0:
        return None, None
    prior_income = prior.get("net_income") if prior else None
    negative_now = 1.0 if net_income < 0 else 0.0
    negative_two = 1.0 if (net_income < 0 or (prior_income is not None and prior_income < 0)) else 0.0
    delta_term = 0.0
    if prior_income is not None:
        denominator = abs(net_income) + abs(prior_income)
        if denominator > 0:
            delta_term = (net_income - prior_income) / denominator
    o = (
        -1.32
        - 0.407 * math.log(total_assets / 100.0)
        + 6.03 * (total_liabilities / total_assets)
        - 1.43 * (working_capital / total_assets)
        + 0.0757 * (current_liabilities / current_assets)
        - 1.72 * negative_now
        - 2.37 * (operating_cash_flow / total_liabilities)
        - 1.83 * (net_income / total_assets)
        + 0.285 * negative_two
        - 0.521 * delta_term
    )
    probability = 1.0 / (1.0 + math.exp(-o))
    return o, probability


def synthetic_credit_score(interest_coverage: float | None,
                           debt_ebitda: float | None) -> float | None:
    if interest_coverage is None or debt_ebitda is None:
        return None
    if interest_coverage >= 12 and debt_ebitda <= 1.5:
        return 95.0
    if interest_coverage >= 8 and debt_ebitda <= 2.5:
        return 85.0
    if interest_coverage >= 5 and debt_ebitda <= 3.5:
        return 75.0
    if interest_coverage >= 3 and debt_ebitda <= 4.5:
        return 60.0
    if interest_coverage >= 2 and debt_ebitda <= 5.5:
        return 45.0
    if interest_coverage >= 1:
        return 30.0
    return 10.0


def compute_wacc(row: dict, beta: float | None, rf: float,
                 model_type: str) -> dict:
    beta = clamp(beta if beta is not None else 1.0, 0.4, 1.8)
    cost_of_equity = rf + beta * EQUITY_RISK_PREMIUM
    market_cap = row.get("market_cap")
    total_debt = row.get("total_debt")
    if market_cap is None or market_cap <= 0:
        market_cap = row.get("total_equity")
    if market_cap is None or market_cap <= 0:
        market_cap = 0.0
    if total_debt is None or total_debt < 0:
        total_debt = 0.0
    interest_expense = row.get("interest_expense")
    tax_rate = clamp(row.get("tax_rate") if row.get("tax_rate") is not None else 0.25,
                     0.0, 0.35)
    if interest_expense is not None and total_debt > 0:
        cost_of_debt = clamp(interest_expense / total_debt, 0.01, 0.10)
    else:
        cost_of_debt = DEFAULT_COST_OF_DEBT
    if model_type in ("bank", "insurer"):
        # WACC is not meaningful for deposit-funded banks and insurers; the
        # cost of equity is used as the hurdle rate instead.
        return {
            "beta": beta,
            "risk_free": rf,
            "erp": EQUITY_RISK_PREMIUM,
            "cost_of_equity": cost_of_equity,
            "cost_of_debt": None,
            "tax_rate": tax_rate,
            "equity_weight": None,
            "debt_weight": None,
            "wacc": cost_of_equity,
            "wacc_note": "cost of equity used for financials",
        }
    total_value = market_cap + total_debt
    if total_value <= 0:
        return {
            "beta": beta,
            "risk_free": rf,
            "erp": EQUITY_RISK_PREMIUM,
            "cost_of_equity": cost_of_equity,
            "cost_of_debt": cost_of_debt,
            "tax_rate": tax_rate,
            "equity_weight": None,
            "debt_weight": None,
            "wacc": cost_of_equity,
            "wacc_note": "cost of equity used; no positive capital base",
        }
    equity_weight = market_cap / total_value
    debt_weight = total_debt / total_value
    wacc = equity_weight * cost_of_equity + debt_weight * cost_of_debt * (1.0 - tax_rate)
    return {
        "beta": beta,
        "risk_free": rf,
        "erp": EQUITY_RISK_PREMIUM,
        "cost_of_equity": cost_of_equity,
        "cost_of_debt": cost_of_debt,
        "tax_rate": tax_rate,
        "equity_weight": equity_weight,
        "debt_weight": debt_weight,
        "wacc": wacc,
        "wacc_note": "",
    }


def absolute_valuation_score(field: str, value: float | None) -> float | None:
    if value is None:
        return None
    if field == "pe":
        return linear_score(value, 40.0, 8.0)
    if field == "ev_ebitda":
        return linear_score(value, 20.0, 5.0)
    if field == "p_fcf":
        return linear_score(value, 30.0, 10.0)
    if field == "fcf_yield":
        return linear_score(value, -0.05, 0.10)
    if field == "pb":
        return linear_score(value, 5.0, 0.5)
    if field == "dividend_yield":
        return linear_score(value, 0.0, 0.08)
    return None


def valuation_components(company: dict, latest: dict, history: list[dict]) -> tuple[dict, dict, str]:
    model_type = company["model_type"]
    if model_type in ("bank", "insurer", "holding", "real_estate"):
        specs = [
            ("pe", 40.0, False),
            ("pb", 30.0, False),
            ("dividend_yield", 30.0, True),
        ]
    else:
        specs = [
            ("pe", 30.0, False),
            ("ev_ebitda", 30.0, False),
            ("p_fcf", 20.0, False),
            ("fcf_yield", 20.0, True),
        ]
    components = {}
    weights = {}
    notes = []
    for field, weight, higher_better in specs:
        current = latest.get(field)
        valid_current = current is not None and math.isfinite(current)
        if field == "pe":
            valid_current = valid_current and 0 < current < 100
        elif field == "ev_ebitda":
            valid_current = valid_current and 0 < current < 50
        elif field == "p_fcf":
            valid_current = valid_current and 0 < current < 100
        elif field == "fcf_yield":
            valid_current = valid_current and -1.0 < current < 1.0
        elif field == "pb":
            valid_current = valid_current and 0 < current < 20
        elif field == "dividend_yield":
            valid_current = valid_current and 0 <= current < 0.5
        history_values = []
        for row in history:
            value = row.get(field)
            if value is None or not math.isfinite(value):
                continue
            if field == "pe" and not (0 < value < 100):
                continue
            if field == "ev_ebitda" and not (0 < value < 50):
                continue
            if field == "p_fcf" and not (0 < value < 100):
                continue
            if field == "fcf_yield" and not (-1.0 < value < 1.0):
                continue
            if field == "pb" and not (0 < value < 20):
                continue
            if field == "dividend_yield" and not (0 <= value < 0.5):
                continue
            history_values.append(value)
        score = None
        if valid_current and len(history_values) >= 3:
            rank = percentile_rank(history_values, current)
            if rank is not None:
                score = 100.0 * rank if higher_better else 100.0 * (1.0 - rank)
                field_labels = {
                    "pe": "PE", "ev_ebitda": "EV/EBITDA", "p_fcf": "P/FCF",
                    "fcf_yield": "FCF yield", "pb": "price/book",
                    "dividend_yield": "dividend yield",
                }
                notes.append(
                    f"{field_labels.get(field, field)} is at the "
                    f"{rank * 100:.0f}th percentile of its own history"
                )
        if score is None:
            score = absolute_valuation_score(field, current)
            if score is not None:
                notes.append(f"{field}: absolute fallback")
        if score is not None:
            components[field] = score
            weights[field] = weight
    return components, weights, "; ".join(notes)


def growth_components(company: dict, metrics: dict, latest: dict) -> tuple[dict, dict]:
    model_type = company["model_type"]
    score_cagr = lambda value: linear_score(value, -0.10, 0.15)  # noqa: E731
    score_eps = lambda value: linear_score(value, -0.20, 0.20)  # noqa: E731
    score_spread = lambda value: linear_score(value, -0.10, 0.10)  # noqa: E731
    score_margin = lambda value: linear_score(value, -0.10, 0.10)  # noqa: E731
    score_dividend = lambda value: linear_score(value, -0.10, 0.15)  # noqa: E731
    score_rnd = lambda value: (20.0 + 80.0 * min(1.0, max(0.0, value / 0.10))
                               if value is not None else None)  # noqa: E731
    score_order = lambda value: linear_score(value, -0.10, 0.15)  # noqa: E731
    score_expense = lambda value: linear_score(value, 0.10, -0.10)  # noqa: E731

    if model_type in ("bank", "insurer"):
        components = {
            "revenue_cagr": score_cagr(metrics.get("revenue_cagr")),
            "roe_ke_spread": score_spread(metrics.get("roe_ke_spread")),
            "eps_cagr": score_eps(metrics.get("eps_cagr")),
            "bvps_cagr": score_cagr(metrics.get("bvps_cagr")),
            "dps_cagr": score_dividend(metrics.get("dps_cagr")),
            "expense_trend": score_expense(
                metrics.get("cost_income_change")
                if model_type == "bank" else metrics.get("expense_ratio_change")
            ),
        }
        weights = {
            "revenue_cagr": 20.0, "roe_ke_spread": 25.0, "eps_cagr": 20.0,
            "bvps_cagr": 15.0, "dps_cagr": 10.0, "expense_trend": 10.0,
        }
    elif model_type in ("utility", "telecom"):
        components = {
            "revenue_cagr": score_cagr(metrics.get("revenue_cagr")),
            "roic_wacc_spread": score_spread(metrics.get("roic_wacc_spread")),
            "eps_cagr": score_eps(metrics.get("eps_cagr")),
            "fcf_cagr": score_eps(metrics.get("fcf_cagr")),
            "gross_margin_change": score_margin(metrics.get("gross_margin_change")),
            "dps_cagr": score_dividend(metrics.get("dps_cagr")),
        }
        weights = {
            "revenue_cagr": 20.0, "roic_wacc_spread": 25.0, "eps_cagr": 20.0,
            "fcf_cagr": 15.0, "gross_margin_change": 10.0, "dps_cagr": 10.0,
        }
    elif model_type == "asset_light":
        components = {
            "revenue_cagr": score_cagr(metrics.get("revenue_cagr")),
            "roic_wacc_spread": score_spread(metrics.get("roic_wacc_spread")),
            "fcf_cagr": score_eps(metrics.get("fcf_cagr")),
            "eps_cagr": score_eps(metrics.get("eps_cagr")),
            "gross_margin_change": score_margin(metrics.get("gross_margin_change")),
        }
        weights = {
            "revenue_cagr": 25.0, "roic_wacc_spread": 25.0, "fcf_cagr": 20.0,
            "eps_cagr": 15.0, "gross_margin_change": 15.0,
        }
    elif model_type == "real_estate":
        components = {
            "revenue_cagr": score_cagr(metrics.get("revenue_cagr")),
            "roe_ke_spread": score_spread(metrics.get("roe_ke_spread")),
            "eps_cagr": score_eps(metrics.get("eps_cagr")),
            "bvps_cagr": score_cagr(metrics.get("bvps_cagr")),
            "fcf_cagr": score_eps(metrics.get("fcf_cagr")),
            "dps_cagr": score_dividend(metrics.get("dps_cagr")),
        }
        weights = {
            "revenue_cagr": 20.0, "roe_ke_spread": 25.0, "eps_cagr": 15.0,
            "bvps_cagr": 15.0, "fcf_cagr": 10.0, "dps_cagr": 15.0,
        }
    elif model_type == "holding":
        components = {
            "eps_cagr": score_eps(metrics.get("eps_cagr")),
            "bvps_cagr": score_cagr(metrics.get("bvps_cagr")),
            "roe_ke_spread": score_spread(metrics.get("roe_ke_spread")),
            "dps_cagr": score_dividend(metrics.get("dps_cagr")),
            "fcf_cagr": score_eps(metrics.get("fcf_cagr")),
        }
        weights = {
            "eps_cagr": 25.0, "bvps_cagr": 25.0, "roe_ke_spread": 20.0,
            "dps_cagr": 15.0, "fcf_cagr": 15.0,
        }
    elif model_type == "financial_infrastructure":
        components = {
            "revenue_cagr": score_cagr(metrics.get("revenue_cagr")),
            "roic_wacc_spread": score_spread(metrics.get("roic_wacc_spread")),
            "eps_cagr": score_eps(metrics.get("eps_cagr")),
            "gross_margin_change": score_margin(metrics.get("gross_margin_change")),
            "fcf_cagr": score_eps(metrics.get("fcf_cagr")),
            "dps_cagr": score_dividend(metrics.get("dps_cagr")),
        }
        weights = {
            "revenue_cagr": 20.0, "roic_wacc_spread": 25.0, "eps_cagr": 20.0,
            "gross_margin_change": 15.0, "fcf_cagr": 10.0, "dps_cagr": 10.0,
        }
    else:
        components = {
            "revenue_cagr": score_cagr(metrics.get("revenue_cagr")),
            "roic_wacc_spread": score_spread(metrics.get("roic_wacc_spread")),
            "gross_margin_change": score_margin(metrics.get("gross_margin_change")),
            "fcf_cagr": score_eps(metrics.get("fcf_cagr")),
            "eps_cagr": score_eps(metrics.get("eps_cagr")),
            "rnd_intensity": score_rnd(metrics.get("rnd_intensity")),
            "order_backlog_cagr": score_order(metrics.get("order_backlog_cagr")),
            "analyst_revenue_growth": score_cagr(metrics.get("analyst_revenue_growth")),
        }
        weights = {
            "revenue_cagr": 20.0, "roic_wacc_spread": 25.0,
            "gross_margin_change": 15.0, "fcf_cagr": 10.0,
            "eps_cagr": 10.0, "rnd_intensity": 10.0,
            "order_backlog_cagr": 5.0, "analyst_revenue_growth": 5.0,
        }
    return components, weights


def survival_components(company: dict, metrics: dict, latest: dict) -> tuple[dict, dict]:
    model_type = company["model_type"]
    if model_type == "bank":
        components = {
            "equity_assets": linear_score(metrics.get("equity_assets"), 0.03, 0.07),
            "roa": linear_score(metrics.get("roa"), 0.0, 0.012),
            "earnings_stability": linear_score(metrics.get("earnings_volatility"), 1.0, 0.1),
            "cost_income": linear_score(metrics.get("cost_income"), 0.90, 0.40),
            "loan_losses": linear_score(metrics.get("loan_loss_ratio"), 0.005, 0.0005),
            "revenue_growth": linear_score(metrics.get("revenue_cagr"), -0.10, 0.10),
        }
        weights = {
            "equity_assets": 25.0, "roa": 20.0, "earnings_stability": 15.0,
            "cost_income": 15.0, "loan_losses": 15.0, "revenue_growth": 10.0,
        }
    elif model_type == "insurer":
        components = {
            "equity_assets": linear_score(metrics.get("equity_assets"), 0.02, 0.10),
            "roa": linear_score(metrics.get("roa"), 0.0, 0.015),
            "earnings_stability": linear_score(metrics.get("earnings_volatility"), 1.0, 0.1),
            "policy_benefits": linear_score(metrics.get("policy_benefit_ratio"), 1.0, 0.5),
            "expense_ratio": linear_score(metrics.get("expense_ratio"), 1.05, 0.65),
            "revenue_growth": linear_score(metrics.get("revenue_cagr"), -0.10, 0.10),
        }
        weights = {
            "equity_assets": 20.0, "roa": 20.0, "earnings_stability": 15.0,
            "policy_benefits": 15.0, "expense_ratio": 15.0, "revenue_growth": 15.0,
        }
    elif model_type in ("utility", "telecom"):
        components = {
            "interest_coverage": linear_score(metrics.get("interest_coverage"), 0.0, 10.0),
            "debt_ebitda": linear_score(metrics.get("debt_ebitda"), 8.0, 1.0),
            "fcf_margin": linear_score(metrics.get("fcf_margin"), -0.05, 0.15),
            "equity_assets": linear_score(metrics.get("equity_assets"), 0.05, 0.35),
            "revenue_growth": linear_score(metrics.get("revenue_cagr"), -0.10, 0.10),
            "earnings_stability": linear_score(metrics.get("earnings_volatility"), 1.0, 0.1),
            "current_ratio": linear_score(metrics.get("current_ratio"), 0.5, 1.5),
        }
        weights = {
            "interest_coverage": 20.0, "debt_ebitda": 20.0, "fcf_margin": 15.0,
            "equity_assets": 15.0, "revenue_growth": 10.0,
            "earnings_stability": 10.0, "current_ratio": 10.0,
        }
    elif model_type == "asset_light":
        components = {
            "debt_ebitda": linear_score(metrics.get("debt_ebitda"), 4.0, 0.5),
            "interest_coverage": linear_score(metrics.get("interest_coverage"), 0.0, 12.0),
            "fcf_margin": linear_score(metrics.get("fcf_margin"), 0.0, 0.25),
            "roic_wacc_spread": linear_score(metrics.get("roic_wacc_spread"), -0.05, 0.15),
            "revenue_growth": linear_score(metrics.get("revenue_cagr"), -0.10, 0.15),
            "earnings_stability": linear_score(metrics.get("earnings_volatility"), 1.0, 0.1),
        }
        weights = {
            "debt_ebitda": 25.0, "interest_coverage": 15.0, "fcf_margin": 20.0,
            "roic_wacc_spread": 20.0, "revenue_growth": 10.0,
            "earnings_stability": 10.0,
        }
    elif model_type == "real_estate":
        components = {
            "equity_assets": linear_score(metrics.get("equity_assets"), 0.10, 0.40),
            "interest_coverage": linear_score(metrics.get("interest_coverage"), 0.0, 6.0),
            "debt_ebitda": linear_score(metrics.get("debt_ebitda"), 15.0, 0.0),
            "real_estate_margin": linear_score(metrics.get("real_estate_margin"), 0.0, 0.15),
            "revenue_growth": linear_score(metrics.get("revenue_cagr"), -0.10, 0.10),
            "earnings_stability": linear_score(metrics.get("earnings_volatility"), 1.0, 0.1),
        }
        weights = {
            "equity_assets": 20.0, "interest_coverage": 20.0, "debt_ebitda": 20.0,
            "real_estate_margin": 15.0, "revenue_growth": 10.0, "earnings_stability": 15.0,
        }
    elif model_type == "holding":
        components = {
            "equity_assets": linear_score(metrics.get("equity_assets"), 0.20, 0.80),
            "roa": linear_score(metrics.get("roa"), -0.02, 0.05),
            "earnings_stability": linear_score(metrics.get("earnings_volatility"), 1.0, 0.1),
            "debt_ebitda": linear_score(metrics.get("debt_ebitda"), 10.0, 0.0),
            "fcf_assets": linear_score(metrics.get("fcf_assets"), -0.05, 0.05),
            "eps_growth": linear_score(metrics.get("eps_cagr"), -0.20, 0.20),
        }
        weights = {
            "equity_assets": 40.0, "debt_ebitda": 15.0, "fcf_assets": 15.0,
            "roa": 10.0, "earnings_stability": 10.0, "eps_growth": 10.0,
        }
    elif model_type == "financial_infrastructure":
        components = {
            "interest_coverage": linear_score(metrics.get("interest_coverage"), 0.0, 15.0),
            "debt_ebitda": linear_score(metrics.get("debt_ebitda"), 6.0, 0.0),
            "roic_wacc_spread": linear_score(metrics.get("roic_wacc_spread"), -0.10, 0.10),
            "fcf_margin": linear_score(metrics.get("fcf_margin"), 0.0, 0.30),
            "revenue_growth": linear_score(metrics.get("revenue_cagr"), -0.10, 0.10),
            "earnings_stability": linear_score(metrics.get("earnings_volatility"), 1.0, 0.1),
        }
        weights = {
            "interest_coverage": 25.0, "debt_ebitda": 20.0,
            "roic_wacc_spread": 20.0, "fcf_margin": 15.0,
            "revenue_growth": 10.0, "earnings_stability": 10.0,
        }
    else:
        components = {
            "altman_z2": linear_score(metrics.get("altman_z2"), 0.5, 3.0),
            "ohlson": (100.0 * (1.0 - metrics["ohlson_probability"])
                       if metrics.get("ohlson_probability") is not None else None),
            "interest_coverage": linear_score(metrics.get("interest_coverage"), 0.0, 12.0),
            "debt_ebitda": linear_score(
                metrics.get("leverage_for_score", metrics.get("debt_ebitda")), 6.0, 0.0
            ),
            "current_ratio": linear_score(metrics.get("current_ratio"), 0.5, 2.0),
            "cash_st_debt": linear_score(metrics.get("cash_st_debt"), 0.0, 2.0),
            "fcf_margin": linear_score(metrics.get("fcf_margin"), -0.10, 0.15),
            "credit_score": metrics.get("credit_score"),
        }
        weights = {
            "altman_z2": 25.0, "ohlson": 20.0, "interest_coverage": 15.0,
            "debt_ebitda": 15.0, "current_ratio": 8.0, "cash_st_debt": 5.0,
            "fcf_margin": 7.0, "credit_score": 5.0,
        }
    return components, weights


def distress_band(survival: float | None, altman_zone: str,
                  ohlson_probability: float | None) -> str:
    if survival is None:
        return "n/a"
    if survival >= 75:
        band = "Low"
    elif survival >= 60:
        band = "Moderate"
    elif survival >= 45:
        band = "Elevated"
    else:
        band = "High"
    if altman_zone == "distress" and band in ("Low", "Moderate"):
        band = "Elevated"
    if ohlson_probability is not None and ohlson_probability > 0.5 and band in ("Low", "Moderate"):
        band = "Elevated"
    if altman_zone == "distress" and ohlson_probability is not None and ohlson_probability > 0.5:
        band = "High"
    return band


def scenario_analysis(company: dict, metrics: dict, latest: dict,
                      history: list[dict], forecast: dict | None,
                      market: dict | None) -> list[dict]:
    current_price = market.get("last") if market else None
    if current_price is None or current_price <= 0:
        return []
    dividend_yield = latest.get("dividend_yield") or 0.0
    if dividend_yield is None or not math.isfinite(dividend_yield):
        dividend_yield = 0.0
    dividend_yield = clamp(dividend_yield, 0.0, 0.15) or 0.0

    model_type = company.get("model_type", "non_financial")
    if model_type in ("bank", "insurer", "holding", "utility", "telecom"):
        base_cap = 0.12
    elif model_type == "asset_light":
        base_cap = 0.20
    else:
        base_cap = 0.15
    bull_cap = base_cap + 0.05
    bear_floor = -0.15

    basis = None
    current_multiple = None
    historical_multiples: list[float | None] = []
    historical_growth = None
    analyst_growth = None
    multiple_low, multiple_high = 0.2, 30.0

    # Holdings are valued on book value, not on volatile equity-method earnings.
    if model_type == "holding":
        pb = latest.get("pb")
        bvps = latest.get("book_value_per_share")
        if pb is not None and 0 < pb < 10 and bvps is not None and bvps > 0:
            basis = "pb"
            current_multiple = pb
            historical_multiples = [row.get("pb") for row in history]
            historical_growth = metrics.get("bvps_cagr")
            analyst_growth = None
            multiple_low, multiple_high = 0.2, 3.0

    if basis is None:
        eps = latest.get("eps_diluted")
        pe = latest.get("pe")
        if eps is not None and eps > 0 and pe is not None and 0 < pe < 100:
            pe_history = [row.get("pe") for row in history
                          if row.get("pe") is not None and 3.0 <= row["pe"] <= 30.0]
            median_pe = median(pe_history)
            annual_eps = [row.get("eps_diluted") for row in history
                          if row.get("eps_diluted") is not None and row["eps_diluted"] > 0]
            if (median_pe is not None and pe > 1.8 * median_pe
                    and annual_eps and latest.get("currency") == "EUR"):
                # Cyclical trough: the spot PE overstates the valuation multiple
                # because earnings are temporarily depressed.  Use average
                # positive annual EPS to normalise the starting multiple.
                normalized_eps = statistics.fmean(annual_eps)
                if normalized_eps > 0:
                    basis = "normalized_eps"
                    current_multiple = current_price / normalized_eps
                    historical_multiples = pe_history
                    historical_growth = metrics.get("revenue_cagr")
                    analyst_growth = (forecast or {}).get("next_revenue_growth")
                    multiple_low, multiple_high = 3.0, 30.0
            if basis is None:
                basis = "eps"
                current_multiple = pe
                historical_multiples = [row.get("pe") for row in history]
                historical_growth = metrics.get("eps_cagr")
                analyst_growth = (forecast or {}).get("next_eps_growth")
                multiple_low, multiple_high = 3.0, 30.0

    if basis is None:
        fcf_per_share = latest.get("fcf_per_share")
        p_fcf = latest.get("p_fcf")
        if fcf_per_share is not None and fcf_per_share > 0 and p_fcf is not None and 0 < p_fcf < 100:
            basis = "fcf"
            current_multiple = p_fcf
            historical_multiples = [row.get("p_fcf") for row in history]
            historical_growth = metrics.get("fcf_cagr")
            analyst_growth = (forecast or {}).get("next_revenue_growth")
            multiple_low, multiple_high = 6.0, 30.0

    if basis is None:
        ps = latest.get("ps")
        shares = latest.get("shares_diluted")
        revenue = latest.get("revenue")
        revenue_per_share = safe_div(revenue, shares)
        if ps is not None and 0 < ps < 20 and revenue_per_share and revenue_per_share > 0:
            basis = "revenue"
            current_multiple = ps
            historical_multiples = [row.get("ps") for row in history]
            historical_growth = metrics.get("revenue_cagr")
            analyst_growth = (forecast or {}).get("next_revenue_growth")
            multiple_low, multiple_high = 0.2, 8.0

    if basis is None or current_multiple is None or current_multiple <= 0:
        return []

    clean_multiples = [value for value in historical_multiples
                       if value is not None and math.isfinite(value)
                       and multiple_low <= value <= multiple_high]
    if len(clean_multiples) >= 3:
        base_multiple = median(clean_multiples) or current_multiple
        bear_multiple = quantile(clean_multiples, 0.25) or base_multiple
        bull_multiple = quantile(clean_multiples, 0.75) or base_multiple
    else:
        base_multiple = bear_multiple = bull_multiple = current_multiple
    base_multiple = clamp(base_multiple, multiple_low, multiple_high) or current_multiple
    bear_multiple = clamp(bear_multiple, multiple_low, multiple_high) or base_multiple
    bull_multiple = clamp(bull_multiple, multiple_low, multiple_high) or base_multiple
    # Do not let a single historical median imply implausible re-rating or
    # de-rating over five years.  The caps preserve the direction of the
    # historical signal while keeping the scenario anchored to today's price.
    base_multiple = clamp(base_multiple, 0.75 * current_multiple, 1.25 * current_multiple) or base_multiple
    bear_multiple = clamp(bear_multiple, 0.50 * current_multiple, 1.00 * current_multiple) or bear_multiple
    bull_multiple = clamp(bull_multiple, 1.00 * current_multiple, 1.75 * current_multiple) or bull_multiple

    if historical_growth is not None and analyst_growth is not None:
        base_growth = 0.6 * historical_growth + 0.4 * analyst_growth
    elif historical_growth is not None:
        base_growth = historical_growth
    elif analyst_growth is not None:
        base_growth = analyst_growth
    else:
        base_growth = 0.0
    base_growth = clamp(base_growth, -0.10, base_cap) or 0.0

    growth_candidates = [value for value in (historical_growth, analyst_growth) if value is not None]
    if growth_candidates:
        bear_growth = min(growth_candidates + [0.0]) - 0.02
        bull_growth = max(growth_candidates + [0.0]) + 0.02
    else:
        bear_growth = -0.05
        bull_growth = 0.05
    bear_growth = clamp(bear_growth, bear_floor, 0.0) or 0.0
    bull_growth = clamp(bull_growth, 0.0, bull_cap) or 0.0

    rows = []
    for scenario, growth, exit_multiple, weight, note in (
        ("bear", bear_growth, bear_multiple, 0.25,
         "Weaker growth and a lower exit multiple; no recovery re-rating."),
        ("base", base_growth, base_multiple, 0.50,
         "Blend of historical growth and the next analyst estimate; median exit multiple."),
        ("bull", bull_growth, bull_multiple, 0.25,
         "Stronger growth and a re-rating to the upper quartile of the own history."),
    ):
        price_5y = current_price * ((1.0 + growth) ** SCENARIO_YEARS) * (exit_multiple / current_multiple)
        price_cagr = (price_5y / current_price) ** (1.0 / SCENARIO_YEARS) - 1.0
        total_return = (1.0 + price_cagr) * (1.0 + dividend_yield) - 1.0
        rows.append({
            "ticker": company["ticker"],
            "company": company["company"],
            "scenario": scenario,
            "horizon_years": SCENARIO_YEARS,
            "basis": basis,
            "current_price": current_price,
            "current_multiple": current_multiple,
            "growth_rate": growth,
            "exit_multiple": exit_multiple,
            "expected_price": price_5y,
            "price_return_cagr": price_cagr,
            "dividend_yield": dividend_yield,
            "total_return_cagr": total_return,
            "probability_weight": weight,
            "notes": note,
        })
    weighted_return = sum(row["probability_weight"] * row["total_return_cagr"] for row in rows)
    weighted_price = sum(row["probability_weight"] * row["expected_price"] for row in rows)
    rows.append({
        "ticker": company["ticker"],
        "company": company["company"],
        "scenario": "weighted",
        "horizon_years": SCENARIO_YEARS,
        "basis": basis,
        "current_price": current_price,
        "current_multiple": current_multiple,
        "growth_rate": base_growth,
        "exit_multiple": base_multiple,
        "expected_price": weighted_price,
        "price_return_cagr": (weighted_price / current_price) ** (1.0 / SCENARIO_YEARS) - 1.0,
        "dividend_yield": dividend_yield,
        "total_return_cagr": weighted_return,
        "probability_weight": 1.0,
        "notes": "Scenario-weighted illustration (25% bear / 50% base / 25% bull), not a forecast.",
    })
    return rows

def analyze_company(ticker: str, rows: list[dict], market: dict | None,
                    forecast: dict | None, rf: float) -> tuple[dict, dict, list[dict]]:
    latest = rows[-1]
    # Some TTM pages omit shares, EPS, per-share cash flow or market cap.
    # Fall back to the most recent annual observation for scenario inputs.
    for key in ("shares_diluted", "eps_diluted", "market_cap",
                "book_value_per_share", "fcf_per_share", "pe", "p_fcf", "ps"):
        if latest.get(key) is None:
            for prior_row in reversed(rows[:-1]):
                if prior_row.get(key) is not None:
                    latest[key] = prior_row[key]
                    break
    first = rows[0]
    annual_history = [row for row in rows if not row.get("is_ttm")]
    if not annual_history:
        annual_history = rows[:-1]
    prior = annual_history[-1] if annual_history else None
    model_type = latest.get("model_type") or "non_financial"
    company = {
        "ticker": ticker,
        "company": latest.get("company", ticker),
        "industry": latest.get("industry", ""),
        "sector": latest.get("sector", ""),
        "nace_code": latest.get("nace_code", ""),
        "nace_label": latest.get("nace_label", ""),
        "model_type": model_type,
    }
    years = None
    if latest.get("period_end_date") and first.get("period_end_date"):
        years = (latest["period_end_date"] - first["period_end_date"]).days / 365.25
    annual_years = None
    if annual_history and annual_history[0].get("period_end_date"):
        annual_years = (annual_history[-1]["period_end_date"]
                        - annual_history[0]["period_end_date"]).days / 365.25

    metrics: dict = {}
    metrics.update(company)
    metrics["latest_period"] = latest.get("period")
    metrics["latest_period_end"] = latest.get("period_end")
    metrics["currency"] = latest.get("currency")
    for key in ("revenue", "ebit", "ebitda", "net_income", "free_cash_flow",
                "market_cap", "enterprise_value", "roic", "roe", "roa",
                "pe", "forward_pe", "ev_ebitda", "p_fcf", "fcf_yield",
                "dividend_yield", "pb", "ps"):
        metrics[key] = latest.get(key)

    revenue_cagr = cagr(first.get("revenue"), latest.get("revenue"), years)
    eps_cagr = cagr(first.get("eps_diluted"), latest.get("eps_diluted"), years)
    fcf_cagr = cagr(first.get("free_cash_flow"), latest.get("free_cash_flow"), years)
    dps_cagr = cagr(first.get("dps"), latest.get("dps"), years)
    bvps_cagr = cagr(first.get("book_value_per_share"), latest.get("book_value_per_share"), years)
    order_backlog_cagr = cagr(first.get("order_backlog"), latest.get("order_backlog"), years)
    receivables_cagr = cagr(first.get("receivables"), latest.get("receivables"), years)
    interest_cagr = cagr(first.get("interest_expense"), latest.get("interest_expense"), years)
    ebit_cagr = cagr(first.get("ebit"), latest.get("ebit"), years)

    metrics.update({
        "revenue_cagr": revenue_cagr,
        "eps_cagr": eps_cagr,
        "fcf_cagr": fcf_cagr,
        "dps_cagr": dps_cagr,
        "bvps_cagr": bvps_cagr,
        "order_backlog_cagr": order_backlog_cagr,
        "receivables_cagr": receivables_cagr,
        "interest_expense_cagr": interest_cagr,
        "ebit_cagr": ebit_cagr,
        "ar_growth_vs_revenue": (receivables_cagr - revenue_cagr
                                 if receivables_cagr is not None and revenue_cagr is not None else None),
        "interest_growth_vs_ebit": (interest_cagr - ebit_cagr
                                    if interest_cagr is not None and ebit_cagr is not None else None),
        "cagr_basis": (f"FY2021–TTM ({years:.1f}y)" if latest.get("is_ttm") and years
                       else f"FY2021–FY2025 ({annual_years:.1f}y)" if annual_years else "n/a"),
    })

    latest_gross_margin = gross_margin(latest)
    first_gross_margin = gross_margin(first)
    metrics["gross_margin"] = latest_gross_margin
    metrics["gross_margin_change"] = (
        latest_gross_margin - first_gross_margin
        if latest_gross_margin is not None and first_gross_margin is not None else None
    )
    metrics["operating_margin"] = latest.get("operating_margin")
    metrics["rnd_intensity"] = safe_div(latest.get("rnd_expense"), latest.get("revenue"))

    interest_expense = latest.get("interest_expense")
    ebit = latest.get("ebit")
    metrics["interest_coverage"] = (
        ebit / interest_expense if ebit is not None and interest_expense not in (None, 0) else None
    )
    positive_ebitda = latest.get("ebitda") is not None and latest["ebitda"] > 0
    metrics["debt_ebitda"] = (
        latest.get("total_debt") / latest["ebitda"]
        if latest.get("total_debt") is not None and positive_ebitda else None
    )
    net_debt = None
    if latest.get("total_debt") is not None and latest.get("cash_st_investments") is not None:
        net_debt = latest["total_debt"] - latest["cash_st_investments"]
    metrics["net_debt_ebitda"] = (
        net_debt / latest["ebitda"] if net_debt is not None and positive_ebitda else None
    )
    industrial_debt = None
    if latest.get("total_debt") is not None and latest.get("financial_services_debt") is not None:
        industrial_debt = latest["total_debt"] - latest["financial_services_debt"]
    metrics["industrial_debt_ebitda"] = (
        industrial_debt / latest["ebitda"]
        if industrial_debt is not None and positive_ebitda else None
    )
    metrics["current_ratio"] = safe_div(latest.get("current_assets"), latest.get("current_liabilities"))
    metrics["cash_st_debt"] = safe_div(latest.get("cash_st_investments"), latest.get("short_term_debt"))
    metrics["fcf_margin"] = safe_div(latest.get("free_cash_flow"), latest.get("revenue"))
    metrics["equity_assets"] = safe_div(latest.get("total_equity"), latest.get("total_assets"))
    metrics["roa"] = latest.get("roa")
    if metrics["roa"] is None:
        metrics["roa"] = safe_div(latest.get("net_income"), latest.get("total_assets"))
    metrics["earnings_volatility"] = earnings_volatility(annual_history)
    metrics["cost_income"] = latest.get("cost_income")
    metrics["loan_loss_ratio"] = latest.get("loan_loss_ratio")
    metrics["policy_benefit_ratio"] = latest.get("policy_benefit_ratio")
    metrics["expense_ratio"] = latest.get("expense_ratio")
    metrics["ffo_margin"] = latest.get("ffo_margin")
    metrics["real_estate_margin"] = latest.get("ffo_margin")
    if metrics["real_estate_margin"] is None:
        metrics["real_estate_margin"] = safe_div(latest.get("ebitda"), latest.get("revenue"))
    metrics["fcf_assets"] = safe_div(latest.get("free_cash_flow"), latest.get("total_assets"))
    metrics["cost_income_change"] = (
        latest.get("cost_income") - first.get("cost_income")
        if latest.get("cost_income") is not None and first.get("cost_income") is not None else None
    )
    metrics["expense_ratio_change"] = (
        latest.get("expense_ratio") - first.get("expense_ratio")
        if latest.get("expense_ratio") is not None and first.get("expense_ratio") is not None else None
    )
    metrics["analyst_revenue_growth"] = (forecast or {}).get("next_revenue_growth")
    metrics["analyst_eps_growth"] = (forecast or {}).get("next_eps_growth")

    # Bank and insurer cash-flow/working-capital ratios are not comparable with
    # industrial companies.  Blank them rather than showing misleading numbers
    # such as a negative "FCF margin" for a bank.
    if model_type in ("bank", "insurer"):
        for key in ("interest_coverage", "debt_ebitda", "net_debt_ebitda",
                    "current_ratio", "cash_st_debt", "fcf_margin",
                    "fcf_cagr", "ev_ebitda", "p_fcf", "fcf_yield"):
            metrics[key] = None
    elif model_type == "holding":
        metrics["fcf_margin"] = None

    if model_type in ("non_financial", "auto", "real_estate"):
        z, z2, zone = altman_scores(latest)
        o, o_probability = ohlson_score(latest, prior)
    else:
        z = z2 = None
        zone = "n/a (sector model)"
        o = o_probability = None
    metrics["altman_z"] = z
    metrics["altman_z2"] = z2
    metrics["altman_zone"] = zone
    metrics["ohlson_o"] = o
    metrics["ohlson_probability"] = o_probability

    beta = market.get("beta_dax") if market else None
    wacc_data = compute_wacc(latest, beta, rf, model_type)
    metrics.update(wacc_data)
    roic = latest.get("roic")
    roe = latest.get("roe")
    metrics["roic_wacc_spread"] = (
        roic - wacc_data["wacc"] if roic is not None and wacc_data.get("wacc") is not None else None
    )
    metrics["roe_ke_spread"] = (
        roe - wacc_data["cost_of_equity"]
        if roe is not None and wacc_data.get("cost_of_equity") is not None else None
    )
    # Captive finance debt is funded by financial-services assets.  Where the
    # split is available and materially lower, use industrial leverage for the
    # scorecard but keep total debt/EBITDA in the report for transparency.
    metrics["leverage_for_score"] = metrics.get("debt_ebitda")
    if (model_type == "auto" and metrics.get("industrial_debt_ebitda") is not None
            and metrics["industrial_debt_ebitda"] > 0
            and metrics.get("debt_ebitda") is not None
            and metrics["industrial_debt_ebitda"] < 0.9 * metrics["debt_ebitda"]):
        metrics["leverage_for_score"] = metrics["industrial_debt_ebitda"]
    metrics["credit_score"] = synthetic_credit_score(
        metrics.get("interest_coverage"), metrics.get("leverage_for_score")
    )

    valuation, valuation_weights, valuation_notes = valuation_components(company, latest, annual_history)
    valuation_score, valuation_effective = weighted_score(valuation, valuation_weights)
    metrics["valuation_score"] = valuation_score
    metrics["valuation_notes"] = valuation_notes

    growth, growth_weights = growth_components(company, metrics, latest)
    growth_score, growth_effective = weighted_score(growth, growth_weights)
    metrics["growth_score"] = growth_score

    survival, survival_weights = survival_components(company, metrics, latest)
    survival_score, survival_effective = weighted_score(survival, survival_weights)
    metrics["survival_score"] = survival_score
    metrics["distress_band"] = distress_band(survival_score, zone, o_probability)
    metrics["distress_prob_1y"] = o_probability
    metrics["distress_prob_3y"] = (
        1.0 - (1.0 - o_probability) ** 3 if o_probability is not None else None
    )

    investment_components = {
        "growth": growth_score,
        "survival": survival_score,
        "valuation": valuation_score,
    }
    investment_weights = {"growth": 40.0, "survival": 30.0, "valuation": 30.0}
    investment_score, investment_effective = weighted_score(investment_components, investment_weights)
    metrics["investment_score"] = investment_score

    # Data-quality notes shown next to each company.
    notes = []
    if model_type != "non_financial":
        notes.append(f"sector-specific scorecard ({model_type})")
    if model_type == "auto":
        notes.append("leverage includes captive financial-services debt")
        if (metrics.get("leverage_for_score") is not None
                and metrics.get("debt_ebitda") is not None
                and metrics["leverage_for_score"] < 0.9 * metrics["debt_ebitda"]):
            notes.append("score uses industrial debt/EBITDA; total leverage shown separately")
        else:
            notes.append("industrial/financial-services debt split unavailable; score uses total debt/EBITDA")
    if latest.get("currency") and latest["currency"] != "EUR":
        notes.append(f"financials reported in {latest['currency']}")
    if latest.get("revenue") is None:
        notes.append("no revenue line (investment holding)")
    if latest.get("rnd_expense") is None:
        notes.append("R&D not reported separately")
    if latest.get("order_backlog") is None:
        notes.append("order backlog not available")
    if latest.get("interest_expense") is None:
        notes.append("interest expense not reported; 4.5% cost of debt assumed")
    if latest.get("free_cash_flow") is not None and latest["free_cash_flow"] < 0:
        notes.append("free cash flow negative in the latest period")
    if latest.get("net_income") is not None and latest["net_income"] < 0:
        notes.append("net income negative; PE and EPS CAGR not meaningful")
    if market is None or market.get("beta_dax") is None:
        notes.append("beta unavailable; 1.0 assumed")
    metrics["data_notes"] = "; ".join(notes)

    scores = {
        "ticker": ticker,
        "company": company["company"],
        "model_type": model_type,
        "survival_score": survival_score,
        "growth_score": growth_score,
        "valuation_score": valuation_score,
        "investment_score": investment_score,
        "distress_band": metrics["distress_band"],
        "altman_zone": zone,
        "ohlson_probability": o_probability,
        "distress_prob_1y": o_probability,
        "distress_prob_3y": metrics["distress_prob_3y"],
        "survival_components": json.dumps(survival, separators=(",", ":")),
        "survival_weights": json.dumps(survival_effective, separators=(",", ":")),
        "growth_components": json.dumps(growth, separators=(",", ":")),
        "growth_weights": json.dumps(growth_effective, separators=(",", ":")),
        "valuation_components": json.dumps(valuation, separators=(",", ":")),
        "valuation_weights": json.dumps(valuation_effective, separators=(",", ":")),
        "investment_weights": json.dumps(investment_effective, separators=(",", ":")),
        "data_notes": metrics["data_notes"],
    }
    if not market or not market.get("last"):
        derived_price = safe_div(latest.get("market_cap"), latest.get("shares_diluted"))
        if derived_price:
            market = dict(market or {})
            market["last"] = derived_price
    scenarios = scenario_analysis(company, metrics, latest, annual_history, forecast, market)
    return metrics, scores, scenarios


def analyze() -> None:
    grouped = load_fundamentals()
    market = load_market_metrics()
    forecasts = load_forecasts()
    rf = risk_free_rate(market)
    all_metrics = []
    all_scores = []
    all_scenarios = []
    sources = {row["ticker"]: row for row in load_sources()}
    for ticker in sorted(grouped):
        rows = grouped[ticker]
        if not rows:
            continue
        metrics, scores, scenarios = analyze_company(
            ticker, rows, market.get(ticker), forecasts.get(ticker), rf
        )
        metrics["index_name"] = sources.get(ticker, {}).get("index_name", "")
        scores["index_name"] = metrics["index_name"]
        all_metrics.append(metrics)
        all_scores.append(scores)
        all_scenarios.extend(scenarios)
    metric_columns = [
        "ticker", "company", "index_name", "industry", "sector", "nace_code", "model_type",
        "latest_period", "latest_period_end", "currency", "cagr_basis",
        "revenue", "ebit", "ebitda", "net_income", "free_cash_flow",
        "market_cap", "enterprise_value",
        "revenue_cagr", "eps_cagr", "fcf_cagr", "dps_cagr", "bvps_cagr",
        "order_backlog_cagr", "gross_margin", "gross_margin_change",
        "operating_margin", "rnd_intensity", "interest_coverage",
        "debt_ebitda", "net_debt_ebitda", "industrial_debt_ebitda",
        "leverage_for_score", "current_ratio", "cash_st_debt", "fcf_margin", "equity_assets",
        "roa", "earnings_volatility", "cost_income", "loan_loss_ratio",
        "policy_benefit_ratio", "expense_ratio", "ffo_margin",
        "real_estate_margin", "fcf_assets", "ar_growth_vs_revenue",
        "interest_growth_vs_ebit", "altman_z", "altman_z2", "altman_zone",
        "ohlson_o", "ohlson_probability", "beta", "risk_free", "erp",
        "cost_of_equity", "cost_of_debt", "tax_rate", "equity_weight",
        "debt_weight", "wacc", "roic", "roic_wacc_spread", "roe",
        "roe_ke_spread", "pe", "forward_pe", "ev_ebitda", "p_fcf",
        "fcf_yield", "dividend_yield", "pb", "ps", "analyst_revenue_growth",
        "analyst_eps_growth", "valuation_score", "survival_score",
        "growth_score", "investment_score", "distress_band",
        "distress_prob_1y", "distress_prob_3y", "credit_score",
        "valuation_notes", "data_notes",
    ]
    write_tsv(METRICS_PATH, metric_columns,
              [[row.get(column) for column in metric_columns] for row in all_metrics])
    score_columns = [
        "ticker", "company", "index_name", "model_type", "survival_score", "growth_score",
        "valuation_score", "investment_score", "distress_band", "altman_zone",
        "ohlson_probability", "distress_prob_1y", "distress_prob_3y",
        "survival_components", "survival_weights", "growth_components",
        "growth_weights", "valuation_components", "valuation_weights",
        "investment_weights", "data_notes",
    ]
    write_tsv(SCORES_PATH, score_columns,
              [[row.get(column) for column in score_columns] for row in all_scores])
    scenario_columns = [
        "ticker", "company", "scenario", "horizon_years", "basis",
        "current_price", "current_multiple", "growth_rate", "exit_multiple",
        "expected_price", "price_return_cagr", "dividend_yield",
        "total_return_cagr", "probability_weight", "notes",
    ]
    write_tsv(SCENARIOS_PATH, scenario_columns,
              [[row.get(column) for column in scenario_columns] for row in all_scenarios])
    manifest = {
        "generated_at": iso_now(),
        "model_version": MODEL_VERSION,
        "risk_free_rate": rf,
        "equity_risk_premium": EQUITY_RISK_PREMIUM,
        "scenario_years": SCENARIO_YEARS,
        "n_companies": len(all_metrics),
        "n_scenarios": len(all_scenarios),
        "score_weights": {
            "survival_non_financial": {
                "altman_z2": 25, "ohlson": 20, "interest_coverage": 15,
                "debt_ebitda": 15, "current_ratio": 8, "cash_st_debt": 5,
                "fcf_margin": 7, "credit_score": 5,
            },
            "growth_non_financial": {
                "revenue_cagr": 20, "roic_wacc_spread": 25,
                "gross_margin_change": 15, "fcf_cagr": 10, "eps_cagr": 10,
                "rnd_intensity": 10, "order_backlog_cagr": 5,
                "analyst_revenue_growth": 5,
            },
            "investment": {"growth": 40, "survival": 30, "valuation": 30},
        },
        "assumptions": [
            "Risk-free rate is the German 10-year Bund yield from data/metrics.tsv.",
            "Equity risk premium is 5.5%; beta is the DAX beta from data/metrics.tsv where available (otherwise 1.0), clamped to 0.4-1.8.",
            "Ohlson uses operating cash flow as the FFO proxy and a constant GNP index of 100.",
            "Scores are relative scorecards, not calibrated probabilities or forecasts.",
            "Valuation percentiles compare the current multiple with the company's FY2021-FY2025 history.",
            "Banks, insurers, holdings, real estate, utilities, telecoms, asset-light companies and financial infrastructure use sector scorecards; Altman/Ohlson are used only where meaningful.",
            "External credit ratings are not used as a scoring input; the model uses a synthetic credit score from coverage and leverage. BMW's A stable rating (Morningstar DBRS, October 2026) is a cross-check; Siemens' rating is unverified.",
            "Auto scores use industrial debt/EBITDA where the captive-finance split is identifiable; total leverage is reported separately.",
            "Scenario exit multiples are bounded to 0.75-1.25x the current multiple in the base case, 0.5-1.0x in the bear case and 1.0-1.75x in the bull case.",
            "Cyclical troughs with spot PE above 1.8x the historical median use average positive annual EPS as the starting basis.",
            "Scores are not validated against a historical default/growth label panel because no consistent DAX/MDAX/SDAX panel was available.",
            "Scenario returns are illustrations, not forecasts.",
        ],
        "coverage": [
            {
                "ticker": row["ticker"],
                "index_name": row.get("index_name", ""),
                "model_type": row["model_type"],
                "survival_score": row["survival_score"],
                "growth_score": row["growth_score"],
                "valuation_score": row["valuation_score"],
                "investment_score": row["investment_score"],
            }
            for row in all_scores
        ],
    }
    write_json(ANALYSIS_MANIFEST_PATH, manifest)
    print(f"Wrote {len(all_metrics)} company metric rows to {METRICS_PATH}")
    print(f"Wrote {len(all_scores)} score rows to {SCORES_PATH}")
    print(f"Wrote {len(all_scenarios)} scenario rows to {SCENARIOS_PATH}")
    print(f"Wrote analysis manifest to {ANALYSIS_MANIFEST_PATH}")


COMPONENT_LABELS = {
    "altman_z2": "Altman Z″",
    "ohlson": "Ohlson O-score",
    "interest_coverage": "interest coverage",
    "debt_ebitda": "debt/EBITDA",
    "current_ratio": "current ratio",
    "cash_st_debt": "cash/short-term debt",
    "fcf_margin": "FCF margin",
    "credit_score": "synthetic credit score",
    "equity_assets": "equity/assets",
    "roa": "ROA",
    "earnings_stability": "earnings stability",
    "cost_income": "cost/income",
    "loan_losses": "loan-loss rate",
    "policy_benefits": "policy-benefit ratio",
    "expense_ratio": "expense ratio",
    "revenue_growth": "revenue growth",
    "real_estate_margin": "real-estate margin",
    "fcf_assets": "FCF/assets",
    "eps_growth": "EPS growth",
    "roic_wacc_spread": "ROIC−WACC",
    "gross_margin_change": "gross-margin trend",
    "fcf_cagr": "FCF CAGR",
    "eps_cagr": "EPS CAGR",
    "rnd_intensity": "R&D intensity",
    "order_backlog_cagr": "order-backlog growth",
    "analyst_revenue_growth": "analyst revenue growth",
    "roe_ke_spread": "ROE−cost of equity",
    "bvps_cagr": "book value/share growth",
    "dps_cagr": "dividend growth",
    "expense_trend": "expense trend",
    "revenue_cagr": "revenue CAGR",
}

MODEL_DESCRIPTIONS = {
    "non_financial": "Industrial/corporate scorecard with Altman Z″, Ohlson O-score, coverage, leverage, liquidity and FCF.",
    "auto": "Industrial scorecard with a captive-finance caveat; where available, industrial debt/EBITDA replaces total debt/EBITDA in the score.",
    "bank": "Bank scorecard: equity/assets, ROA, earnings stability, cost/income, loan-loss rate and revenue growth.",
    "insurer": "Insurer scorecard: equity/assets, ROA, earnings stability, policy-benefit ratio, expense ratio and revenue growth.",
    "real_estate": "Real-estate scorecard: equity/assets, interest coverage, debt/EBITDA, FFO/EBITDA margin, growth and earnings stability.",
    "holding": "Holding-company scorecard: equity/assets, ROA, earnings stability, debt/EBITDA, FCF/assets and EPS growth.",
    "utility": "Regulated-utility scorecard: coverage, debt/EBITDA, FCF margin, equity/assets, growth and earnings stability.",
    "telecom": "Telecom scorecard: coverage, debt/EBITDA, FCF margin, equity/assets, growth and earnings stability.",
    "asset_light": "Asset-light scorecard for negative-working-capital businesses: leverage, FCF margin, ROIC−WACC, growth and earnings stability.",
    "financial_infrastructure": "Exchange/clearing scorecard: coverage, debt/EBITDA, ROIC−WACC, FCF margin, growth and earnings stability.",
}


def load_numeric_tsv(path: Path) -> list[dict]:
    rows = []
    for raw in read_tsv(path):
        item = {}
        for key, value in raw.items():
            parsed = parse_number(value)
            item[key] = parsed if parsed is not None else (value if value not in ("",) else None)
        rows.append(item)
    return rows


def by_ticker(rows: list[dict]) -> dict[str, dict]:
    return {row["ticker"]: row for row in rows}


def fnum(value, digits: int = 1) -> str:
    return fmt_num(value, digits)


def fpct(value, digits: int = 1, sign: bool = True) -> str:
    return fmt_pct(value, digits, sign)


def fpp(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100.0:+.{digits}f} pp"


def fmoney(value, currency: str = "EUR", digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f} {currency}m"


def fx(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}x"


def score_text(value) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}"


def band_color(band: str | None) -> str:
    return {
        "Low": "#2ca02c",
        "Moderate": "#1f77b4",
        "Elevated": "#ff7f0e",
        "High": "#d62728",
    }.get(band or "", "#999999")


def score_color(value) -> str:
    if value is None:
        return "#999999"
    if value >= 70:
        return "#2ca02c"
    if value >= 55:
        return "#1f77b4"
    if value >= 40:
        return "#ff7f0e"
    return "#d62728"


def survival_interpretation(value) -> str:
    if value is None:
        return "n/a"
    if value >= 75:
        return "Low distress"
    if value >= 60:
        return "Moderate distress"
    if value >= 45:
        return "Elevated distress"
    return "High distress"


def growth_interpretation(value) -> str:
    if value is None:
        return "n/a"
    if value >= 70:
        return "Strong"
    if value >= 55:
        return "Above average"
    if value >= 40:
        return "Average"
    return "Weak"


def valuation_interpretation(value) -> str:
    if value is None:
        return "n/a"
    if value >= 70:
        return "Cheap vs own history"
    if value >= 55:
        return "Slightly cheap"
    if value >= 40:
        return "Mid-range"
    return "Expensive vs own history"


def investment_interpretation(value) -> str:
    if value is None:
        return "n/a"
    if value >= 70:
        return "Attractive score"
    if value >= 55:
        return "Above average"
    if value >= 40:
        return "Neutral"
    return "Unattractive score"


def component_drivers(components: dict, weights: dict) -> tuple[list[str], list[str]]:
    items = []
    for key, value in components.items():
        if value is None:
            continue
        weight = weights.get(key, 0.0)
        items.append((key, value, weight, (value - 50.0) * weight))
    strengths = sorted(items, key=lambda item: item[3], reverse=True)[:2]
    weaknesses = sorted(items, key=lambda item: item[3])[:2]
    def label(item):
        return f"{COMPONENT_LABELS.get(item[0], item[0])} ({item[1]:.0f}/100)"
    return [label(item) for item in strengths], [label(item) for item in weaknesses]


def scenario_lookup(rows: list[dict]) -> dict[str, dict[str, dict]]:
    output: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        output[row["ticker"]][row["scenario"]] = row
    return output


def write_survival_chart(scores: list[dict]) -> None:
    ordered = sorted(scores, key=lambda row: (row.get("survival_score") is None,
                                              -(row.get("survival_score") or 0)))
    row_height = 21
    top = 82
    height = top + len(ordered) * row_height + 30
    svg = SVG(980, height, "DAX · MDAX · SDAX survival scores",
              "0–100; higher is safer. Sector-specific scorecards; not a default probability.")
    x0, width = 250, 650
    for tick in (0, 25, 50, 75, 100):
        x = x0 + tick / 100.0 * width
        svg.line(x, top - 8, x, top + len(ordered) * row_height, stroke="#e8e8e8", width=1)
        svg.text(x, top - 14, str(tick), size=9, anchor="middle", fill="#666666")
    for index, row in enumerate(ordered):
        score = row.get("survival_score") or 0.0
        y = top + index * row_height
        svg.text(x0 - 8, y + 13, f"{row['ticker']}  {str(row.get('company', ''))[:19]}",
                 size=9, anchor="end", fill="#333333")
        svg.rect(x0, y + 3, score / 100.0 * width, row_height - 7,
                 fill=band_color(row.get("distress_band")))
        svg.text(x0 + score / 100.0 * width + 5, y + 13, f"{score:.1f}",
                 size=9, fill="#333333")
    svg.line(x0, top + len(ordered) * row_height, x0 + width,
             top + len(ordered) * row_height, stroke="#999999", width=1)
    legend_items = [("Low", "#2ca02c"), ("Moderate", "#1f77b4"),
                    ("Elevated", "#ff7f0e"), ("High", "#d62728")]
    legend(svg, legend_items, x0, height - 12, columns=4)
    CHART_SURVIVAL.write_text(svg.render(), encoding="utf-8")


def write_scatter_chart(scores: list[dict], metrics: dict[str, dict],
                        x_key: str, y_key: str, title: str, subtitle: str,
                        path: Path) -> None:
    points = [row for row in scores
              if row.get(x_key) is not None and row.get(y_key) is not None]
    if not points:
        return
    svg = SVG(1120, 760, title, subtitle)
    x0, y0, width, height = 85, 75, 930, 590
    sx, sy = frame(svg, x0, y0, width, height, 0, 100, 0, 100,
                   [0, 20, 40, 60, 80, 100], [0, 20, 40, 60, 80, 100],
                   x_fmt=lambda value: f"{value:.0f}",
                   y_fmt=lambda value: f"{value:.0f}",
                   x_label=x_key.replace("_", " ").title(),
                   y_label=y_key.replace("_", " ").title())
    max_cap = max((metrics.get(row["ticker"], {}).get("market_cap") or 0)
                  for row in points) or 1.0
    label_set = set()
    by_investment = sorted(points, key=lambda row: -(row.get("investment_score") or 0))
    by_cap = sorted(points, key=lambda row: -(metrics.get(row["ticker"], {}).get("market_cap") or 0))
    for row in by_investment[:8] + by_investment[-4:] + by_cap[:10]:
        label_set.add(row["ticker"])
    occupied: list[tuple[float, float, float, float]] = []
    for row in points:
        cap = metrics.get(row["ticker"], {}).get("market_cap") or 0.0
        radius = 4.0 + 9.0 * math.sqrt(max(0.0, cap) / max_cap)
        x, y = sx(row[x_key]), sy(row[y_key])
        svg.circle(x, y, radius, fill=score_color(row.get("investment_score")),
                   stroke="#ffffff", stroke_width=1.0, opacity=0.82)
        if row["ticker"] not in label_set:
            continue
        label = row["ticker"]
        label_width = len(label) * 6.0 + 2.0
        for dy in (0.0, -10.0, 10.0, -20.0, 20.0, -30.0, 30.0):
            left = x + radius + 3.0
            top = y + 3.0 + dy - 8.0
            rect = (left, top, left + label_width, top + 10.0)
            overlaps = any(
                not (rect[2] < other[0] or rect[0] > other[2]
                     or rect[3] < other[1] or rect[1] > other[3])
                for other in occupied
            )
            if not overlaps:
                svg.text(left, y + 3.0 + dy, label, size=8, fill="#333333")
                occupied.append(rect)
                break
    legend(svg, [("Investment ≥70", "#2ca02c"), ("55–70", "#1f77b4"),
                 ("40–55", "#ff7f0e"), ("<40", "#d62728")],
           x0, y0 + height + 45, columns=4)
    path.write_text(svg.render(), encoding="utf-8")


def company_section(metric: dict, score: dict, scenarios: dict[str, dict]) -> str:
    ticker = metric["ticker"]
    company = metric.get("company", ticker)
    model_type = metric.get("model_type", "non_financial")
    currency = metric.get("currency") or "EUR"
    lines = [f"### {company} ({ticker})", ""]
    lines.append(
        f"**Index:** {metric.get('index_name', '—')} · **Model:** `{model_type}` · "
        f"**Sector:** {metric.get('sector', '—')} · "
        f"**NACE:** {metric.get('nace_code', '—')} {metric.get('nace_label', '')} · "
        f"**Latest period:** {metric.get('latest_period', '—')} "
        f"({metric.get('latest_period_end', '—')}) · **Currency:** {currency}"
    )
    lines.append("")

    score_rows = [
        ["Survival", score_text(score.get("survival_score")), survival_interpretation(score.get("survival_score"))],
        ["Growth", score_text(score.get("growth_score")), growth_interpretation(score.get("growth_score"))],
        ["Valuation", score_text(score.get("valuation_score")), valuation_interpretation(score.get("valuation_score"))],
        ["Investment", score_text(score.get("investment_score")), investment_interpretation(score.get("investment_score"))],
        ["Distress band", score.get("distress_band") or "—", "Model-implied relative band"],
        ["Altman Z / Z″", f"{fnum(metric.get('altman_z'), 2)} / {fnum(metric.get('altman_z2'), 2)}",
         metric.get("altman_zone") or "—"],
        ["Ohlson 1-year probability", fpct(metric.get("ohlson_probability"), 1, False),
         "Relative model output; not calibrated" if metric.get("ohlson_probability") is not None else "n/a for this sector model"],
        ["Naive 3-year distress", fpct(metric.get("distress_prob_3y"), 1, False),
         "1 − (1 − p₁ᵧ)³; illustrative" if metric.get("distress_prob_3y") is not None else "n/a for this sector model"],
        ["Synthetic credit score", score_text(metric.get("credit_score")),
         "Derived from coverage and leverage" if metric.get("credit_score") is not None else "n/a for this sector model"],
    ]
    lines.append(md_table(["Score", "Value", "Interpretation"], score_rows,
                          ["left", "right", "left"]))
    lines.append("")

    lines.append("**Growth and profitability**")
    lines.append("")
    growth_rows = [
        ["Revenue", fmoney(metric.get("revenue"), currency), "Latest period"],
        ["Net income", fmoney(metric.get("net_income"), currency), "Latest period"],
        ["Free cash flow", (fmoney(metric.get("free_cash_flow"), currency)
                            if model_type not in ("bank", "insurer")
                            else "—"),
         "Latest period" if model_type not in ("bank", "insurer")
         else "Cash-flow FCF is not comparable for banks/insurers"],
        ["Revenue CAGR", fpct(metric.get("revenue_cagr")), metric.get("cagr_basis") or "—"],
        ["EPS CAGR", fpct(metric.get("eps_cagr")), metric.get("cagr_basis") or "—"],
        ["FCF CAGR", fpct(metric.get("fcf_cagr")), metric.get("cagr_basis") or "—"],
        ["Gross margin", fpct(metric.get("gross_margin"), 1, False), "Latest period"],
        ["Gross-margin change", fpp(metric.get("gross_margin_change")), "Change vs first period"],
        ["R&D intensity", fpct(metric.get("rnd_intensity"), 1, False), "R&D / revenue"],
    ]
    if model_type in ("bank", "insurer"):
        growth_rows.extend([
            ["ROE", fpct(metric.get("roe"), 1, False), "Latest period"],
            ["Cost of equity", fpct(metric.get("cost_of_equity"), 1, False), "Bank/insurer hurdle rate"],
            ["ROE−cost of equity", fpp(metric.get("roe_ke_spread")), "Positive = value creation"],
        ])
    else:
        growth_rows.extend([
            ["ROIC", fpct(metric.get("roic"), 1, False), "Latest period"],
            ["WACC", fpct(metric.get("wacc"), 1, False), "CAPM + after-tax cost of debt"],
            ["ROIC−WACC", fpp(metric.get("roic_wacc_spread")), "Positive = value creation"],
        ])
    growth_rows.extend([
        ["Analyst revenue growth", fpct(metric.get("analyst_revenue_growth")), "Next fiscal year"],
        ["Analyst EPS growth", fpct(metric.get("analyst_eps_growth")), "Next fiscal year"],
    ])
    lines.append(md_table(["Metric", "Value", "Basis"], growth_rows,
                          ["left", "right", "left"]))
    lines.append("")

    lines.append("**Balance sheet, liquidity and valuation**")
    lines.append("")
    balance_rows = []
    if model_type == "bank":
        balance_rows.extend([
            ["Cost/income", fpct(metric.get("cost_income"), 1, False), "Non-interest expense / revenue"],
            ["Loan-loss rate", fpct(metric.get("loan_loss_ratio"), 2, False), "Loan losses / total assets"],
        ])
    elif model_type == "insurer":
        balance_rows.extend([
            ["Policy-benefit ratio", fpct(metric.get("policy_benefit_ratio"), 1, False), "Policy benefits / revenue"],
            ["Expense ratio", fpct(metric.get("expense_ratio"), 1, False), "Operating expense / revenue"],
        ])
    elif model_type == "real_estate":
        balance_rows.append(
            ["FFO/EBITDA margin", fpct(metric.get("real_estate_margin"), 1, False), "FFO or EBITDA / revenue"]
        )
    elif model_type == "holding":
        balance_rows.append(
            ["FCF/assets", fpct(metric.get("fcf_assets"), 2, False), "Free cash flow / total assets"]
        )
    balance_rows.extend([
        ["Interest coverage", fx(metric.get("interest_coverage")), "EBIT / interest expense"],
        ["Debt/EBITDA", fx(metric.get("debt_ebitda")), "Total debt / EBITDA"],
        ["Net debt/EBITDA", fx(metric.get("net_debt_ebitda")), "(Debt − cash) / EBITDA"],
        ["Industrial debt/EBITDA", fx(metric.get("industrial_debt_ebitda")), "Auto captive-finance adjustment"],
        ["Current ratio", fx(metric.get("current_ratio")), "Current assets / current liabilities"],
        ["Cash / short-term debt", fx(metric.get("cash_st_debt")), "Liquidity buffer"],
        ["FCF margin", fpct(metric.get("fcf_margin")), "FCF / revenue"],
        ["Equity/assets", fpct(metric.get("equity_assets"), 1, False), "Capital buffer"],
        ["ROA", fpct(metric.get("roa"), 2, False), "Net income / assets"],
        ["Earnings volatility", fpct(metric.get("earnings_volatility"), 1, False), "5-year standard deviation / mean"],
        ["PE", fx(metric.get("pe")), "Price / diluted EPS"],
        ["EV/EBITDA", fx(metric.get("ev_ebitda")), "Enterprise value / EBITDA"],
        ["P/FCF", fx(metric.get("p_fcf")), "Price / free cash flow"],
        ["FCF yield", fpct(metric.get("fcf_yield"), 2, False), "FCF / market cap"],
        ["Dividend yield", fpct(metric.get("dividend_yield"), 2, False), "Dividend / price"],
        ["Price / book", fx(metric.get("pb")), "Market cap / book equity"],
    ])
    lines.append(md_table(["Metric", "Value", "Basis"], balance_rows,
                          ["left", "right", "left"]))
    lines.append("")

    lines.append("**Five-year scenario illustration**")
    lines.append("")
    if scenarios:
        current = next(iter(scenarios.values()))
        lines.append(
            f"Current price: **{fnum(current.get('current_price'), 2)} EUR** · "
            f"starting multiple: **{fx(current.get('current_multiple'))}** · "
            f"dividend yield: **{fpct(current.get('dividend_yield'), 2, False)}**."
        )
        lines.append("")
        scenario_rows = []
        for key in ("bear", "base", "bull", "weighted"):
            row = scenarios.get(key)
            if not row:
                continue
            scenario_rows.append([
                key.title(),
                row.get("basis") or "—",
                fpct(row.get("growth_rate")),
                fx(row.get("exit_multiple")),
                fnum(row.get("expected_price"), 2),
                fpct(row.get("total_return_cagr")),
                fpct(row.get("probability_weight"), 0, False),
            ])
        lines.append(md_table(
            ["Scenario", "Basis", "Growth", "Exit multiple", "Price in 5y", "Total return p.a.", "Weight"],
            scenario_rows, ["left", "left", "right", "right", "right", "right", "right"]
        ))
        lines.append("")
        lines.append(
            "The scenario is a mechanical illustration: EPS/FCF/revenue/book-value growth plus an exit multiple "
            "from the company's own FY2021–FY2025 range, bounded to 0.75–1.25× the current multiple in the base "
            "case (0.5–1.0× bear, 1.0–1.75× bull) to avoid implausible re-rating or de-rating. When spot PE "
            "exceeds 1.8× the historical median, the model uses average positive annual EPS to normalise a "
            "cyclical trough. It is not a forecast or a target price."
        )
    else:
        lines.append("Scenario analysis is not available because the company lacks a usable positive earnings, "
                     "cash-flow or revenue base, or lacks a current price.")
    lines.append("")

    survival_components = json.loads(score.get("survival_components") or "{}")
    survival_weights = json.loads(score.get("survival_weights") or "{}")
    growth_components = json.loads(score.get("growth_components") or "{}")
    growth_weights = json.loads(score.get("growth_weights") or "{}")
    strengths, weaknesses = component_drivers(survival_components, survival_weights)
    growth_strengths, growth_weaknesses = component_drivers(growth_components, growth_weights)
    readthrough = []
    readthrough.append(
        f"**Survival read-through.** {survival_interpretation(score.get('survival_score'))} "
        f"({score_text(score.get('survival_score'))}/100). "
        f"Strongest inputs: {', '.join(strengths) if strengths else '—'}. "
        f"Weakest inputs: {', '.join(weaknesses) if weaknesses else '—'}."
    )
    if metric.get("altman_z2") is not None:
        readthrough.append(
            f"Altman Z″ is {fnum(metric.get('altman_z2'), 2)} ({metric.get('altman_zone')}); "
            f"Ohlson's 1-year model probability is {fpct(metric.get('ohlson_probability'), 1, False)}."
        )
    else:
        readthrough.append("Altman/Ohlson are not used for this sector; the scorecard above is the relevant model.")
    readthrough.append(
        f"**Growth read-through.** {growth_interpretation(score.get('growth_score'))} "
        f"({score_text(score.get('growth_score'))}/100). "
        f"Revenue CAGR {fpct(metric.get('revenue_cagr'))}, EPS CAGR {fpct(metric.get('eps_cagr'))}, "
        f"ROIC−WACC {fpp(metric.get('roic_wacc_spread'))}. "
        f"Strongest inputs: {', '.join(growth_strengths) if growth_strengths else '—'}; "
        f"weakest: {', '.join(growth_weaknesses) if growth_weaknesses else '—'}."
    )
    valuation_notes = metric.get('valuation_notes') or ''
    if valuation_notes and not valuation_notes.endswith('.'):
        valuation_notes += '.'
    readthrough.append(
        f"**Valuation read-through.** {valuation_interpretation(score.get('valuation_score'))} "
        f"({score_text(score.get('valuation_score'))}/100). "
        f"PE {fx(metric.get('pe'))}, EV/EBITDA {fx(metric.get('ev_ebitda'))}, "
        f"P/FCF {fx(metric.get('p_fcf'))}, FCF yield {fpct(metric.get('fcf_yield'), 2, False)}. "
        f"{valuation_notes}"
    )
    base = scenarios.get("base")
    weighted = scenarios.get("weighted")
    if base and weighted:
        readthrough.append(
            f"**Five-year illustration.** Base case: {fpct(base.get('growth_rate'))} growth, "
            f"{fx(base.get('exit_multiple'))} exit multiple, {fpct(base.get('total_return_cagr'))} total return p.a.; "
            f"scenario-weighted: {fpct(weighted.get('total_return_cagr'))} p.a."
        )
    lines.append(" ".join(readthrough))
    lines.append("")
    if score.get("data_notes"):
        lines.append(f"**Data notes:** {score.get('data_notes')}")
        lines.append("")
    return "\n".join(lines)


def report() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    metrics_rows = load_numeric_tsv(METRICS_PATH)
    scores_rows = load_numeric_tsv(SCORES_PATH)
    scenario_rows = load_numeric_tsv(SCENARIOS_PATH)
    manifest = read_json(ANALYSIS_MANIFEST_PATH)
    collection_manifest = read_json(MANIFEST_PATH) if MANIFEST_PATH.exists() else {}
    metrics = by_ticker(metrics_rows)
    scores = by_ticker(scores_rows)
    scenarios = scenario_lookup(scenario_rows)

    if not metrics or not scores:
        raise SystemExit("Missing analysis outputs; run 'fundamentals.py analyze' first")

    write_survival_chart(scores_rows)
    write_scatter_chart(scores_rows, metrics, "growth_score", "survival_score",
                        "DAX · MDAX · SDAX: growth vs survival",
                        "Bubble size = market cap; colour = investment score.",
                        CHART_SCATTER)
    write_scatter_chart(scores_rows, metrics, "growth_score", "valuation_score",
                        "DAX · MDAX · SDAX: growth vs valuation",
                        "Bubble size = market cap; colour = investment score.",
                        CHART_VALUATION)

    ranked = sorted(scores_rows, key=lambda row: (row.get("investment_score") is None,
                                                  -(row.get("investment_score") or 0)))
    survival_ranked = sorted(scores_rows, key=lambda row: (row.get("survival_score") is None,
                                                           -(row.get("survival_score") or 0)))
    growth_ranked = sorted(scores_rows, key=lambda row: (row.get("growth_score") is None,
                                                         -(row.get("growth_score") or 0)))
    valuation_ranked = sorted(scores_rows, key=lambda row: (row.get("valuation_score") is None,
                                                            -(row.get("valuation_score") or 0)))

    lines = []
    lines.append("# DAX 40, MDAX and SDAX: corporate distress and long-term investment forecasting")
    lines.append("")
    lines.append(
        f"*As of {date.today().isoformat()} · model version {manifest.get('model_version', MODEL_VERSION)} · "
        "statistical scorecard, not investment advice.*"
    )
    lines.append("")
    lines.append("## What this report answers")
    lines.append("")
    lines.append("- **Bankruptcy/distress probability (1–3 years):** a relative survival score, Altman Z/Z″ zones, "
                 "Ohlson O-score and a naive three-year extrapolation where the model is applicable.")
    lines.append("- **Financial survival probability (1–5 years):** a sector-aware 0–100 survival score built from "
                 "leverage, coverage, liquidity, profitability, earnings stability and sector-specific ratios.")
    lines.append("- **Business growth potential (3–10 years):** a 0–100 growth score from revenue/EPS/FCF growth, "
                 "ROIC−WACC, margin trends, R&D, order backlog and analyst estimates.")
    lines.append("- **Expected investment return (1–5 years):** a valuation score plus bear/base/bull five-year "
                 "scenario illustrations; a company can survive while its stock underperforms.")
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    top_survival = survival_ranked[:10]
    bottom_survival = list(reversed(survival_ranked[-10:]))
    top_investment = ranked[:10]
    bottom_investment = list(reversed(ranked[-10:]))
    lines.append("### Highest survival scores")
    lines.append("")
    lines.append(md_table(
        ["Rank", "Company", "Ticker", "Survival", "Growth", "Valuation", "Investment", "Band"],
        [[str(i), row.get("company", ""), row["ticker"], score_text(row.get("survival_score")),
          score_text(row.get("growth_score")), score_text(row.get("valuation_score")),
          score_text(row.get("investment_score")), row.get("distress_band", "—")]
         for i, row in enumerate(top_survival, start=1)],
        ["right", "left", "left", "right", "right", "right", "right", "left"]
    ))
    lines.append("")
    lines.append("### Lowest survival scores")
    lines.append("")
    lines.append(md_table(
        ["Rank", "Company", "Ticker", "Survival", "Band", "Debt/EBITDA", "FCF margin", "Data notes"],
        [[str(i), row.get("company", ""), row["ticker"], score_text(row.get("survival_score")),
          row.get("distress_band", "—"), fx(metrics.get(row["ticker"], {}).get("debt_ebitda")),
          fpct(metrics.get(row["ticker"], {}).get("fcf_margin")),
          (row.get("data_notes") or "")[:60]]
         for i, row in enumerate(bottom_survival, start=1)],
        ["right", "left", "left", "right", "left", "right", "right", "left"]
    ))
    lines.append("")
    lines.append("### Highest investment scores")
    lines.append("")
    lines.append(md_table(
        ["Rank", "Company", "Ticker", "Investment", "Growth", "Survival", "Valuation", "5y weighted return"],
        [[str(i), row.get("company", ""), row["ticker"], score_text(row.get("investment_score")),
          score_text(row.get("growth_score")), score_text(row.get("survival_score")),
          score_text(row.get("valuation_score")),
          fpct((scenarios.get(row["ticker"], {}).get("weighted") or {}).get("total_return_cagr"))]
         for i, row in enumerate(top_investment, start=1)],
        ["right", "left", "left", "right", "right", "right", "right", "right"]
    ))
    lines.append("")
    lines.append("### Lowest investment scores")
    lines.append("")
    lines.append(md_table(
        ["Rank", "Company", "Ticker", "Investment", "Growth", "Survival", "Valuation", "5y weighted return"],
        [[str(i), row.get("company", ""), row["ticker"], score_text(row.get("investment_score")),
          score_text(row.get("growth_score")), score_text(row.get("survival_score")),
          score_text(row.get("valuation_score")),
          fpct((scenarios.get(row["ticker"], {}).get("weighted") or {}).get("total_return_cagr"))]
         for i, row in enumerate(bottom_investment, start=1)],
        ["right", "left", "left", "right", "right", "right", "right", "right"]
    ))
    lines.append("")

    lines.append("## Methodology")
    lines.append("")
    lines.append("The framework separates **survival**, **business growth** and **stock returns**. A company can have a "
                 "high survival score and a low investment score if its shares already discount those strengths. "
                 "The scores are relative scorecards, not calibrated probabilities or price targets.")
    lines.append("")
    lines.append("### Survival score")
    lines.append("")
    lines.append("For industrial companies the survival score uses the following weights; missing inputs are "
                 "renormalised over the available components.")
    lines.append("")
    lines.append(md_table(
        ["Component", "Weight", "Definition"],
        [
            ["Altman Z″", "25%", "6.56·WC/TA + 3.26·RE/TA + 6.72·EBIT/TA + 1.05·market cap/liabilities"],
            ["Ohlson O-score", "20%", "Ohlson (1980) model; operating cash flow as the FFO proxy"],
            ["Interest coverage", "15%", "EBIT / interest expense"],
            ["Debt/EBITDA", "15%", "Total debt / EBITDA; industrial debt for autos where available"],
            ["Current ratio", "8%", "Current assets / current liabilities"],
            ["Cash / short-term debt", "5%", "Cash and short-term investments / current debt"],
            ["FCF margin", "7%", "Free cash flow / revenue"],
            ["Synthetic credit score", "5%", "Coverage and leverage based; external ratings not used"],
        ],
        ["left", "right", "left"]
    ))
    lines.append("")
    lines.append("Altman Z″ zones: **safe ≥ 2.6**, **grey 1.1–2.6**, **distress < 1.1**. Ohlson probabilities are "
                 "relative model outputs, not calibrated default probabilities. The naive three-year distress figure "
                 "is `1 − (1 − p₁ᵧ)³` and assumes independence; it is shown only as a transparency aid.")
    lines.append("")
    lines.append("### Sector-specific scorecards")
    lines.append("")
    sector_rows = []
    for model_type, description in MODEL_DESCRIPTIONS.items():
        members = [row for row in scores_rows if row.get("model_type") == model_type]
        if not members:
            continue
        sector_rows.append([model_type, str(len(members)), description])
    lines.append(md_table(["Model", "Companies", "Description"], sector_rows,
                          ["left", "right", "left"]))
    lines.append("")
    lines.append("### Sector scorecard weights")
    lines.append("")
    lines.append(md_table(
        ["Model", "Survival weights", "Growth weights"],
        [
            ["non_financial / auto", "Altman Z″ 25, Ohlson 20, coverage 15, debt/EBITDA 15, current ratio 8, cash/ST debt 5, FCF margin 7, credit score 5", "Revenue CAGR 20, ROIC−WACC 25, gross margin 15, FCF CAGR 10, EPS CAGR 10, R&D 10, backlog 5, analyst growth 5"],
            ["bank / insurer", "Equity/assets 25, ROA 20, earnings stability 15, cost/income or expense ratio 15, loan losses or policy benefits 15, revenue growth 10", "Revenue CAGR 20, ROE−cost of equity 25, EPS CAGR 20, book value/share 15, dividend growth 10, expense trend 10"],
            ["real_estate", "Equity/assets 20, coverage 20, debt/EBITDA 20, FFO margin 15, revenue growth 10, earnings stability 15", "Revenue CAGR 20, ROE−cost of equity 25, EPS CAGR 15, book value/share 15, FCF CAGR 10, dividend growth 15"],
            ["holding", "Equity/assets 40, debt/EBITDA 15, FCF/assets 15, ROA 10, earnings stability 10, EPS growth 10", "EPS CAGR 25, book value/share 25, ROE−cost of equity 20, dividend growth 15, FCF CAGR 15"],
            ["utility / telecom", "Coverage 20, debt/EBITDA 20, FCF margin 15, equity/assets 15, revenue growth 10, earnings stability 10, current ratio 10", "Revenue CAGR 20, ROIC−WACC 25, EPS CAGR 20, FCF CAGR 15, gross margin 10, dividend growth 10"],
            ["asset_light", "Debt/EBITDA 25, coverage 15, FCF margin 20, ROIC−WACC 20, revenue growth 10, earnings stability 10", "Revenue CAGR 25, ROIC−WACC 25, FCF CAGR 20, EPS CAGR 15, gross margin 15"],
            ["financial_infrastructure", "Coverage 25, debt/EBITDA 20, ROIC−WACC 20, FCF margin 15, revenue growth 10, earnings stability 10", "Revenue CAGR 20, ROIC−WACC 25, EPS CAGR 20, gross margin 15, FCF CAGR 10, dividend growth 10"],
        ],
        ["left", "left", "left"]
    ))
    lines.append("")
    lines.append("### Growth score")
    lines.append("")
    lines.append("Industrial growth weights: revenue CAGR 20%, ROIC−WACC 25%, gross-margin trend 15%, FCF CAGR 10%, "
                 "EPS CAGR 10%, R&D/revenue 10%, order-backlog growth 5%, analyst forward revenue growth 5%. "
                 "Financials replace FCF/R&D/backlog with ROE−cost of equity, book-value growth, dividend growth and "
                 "expense-ratio trends. Missing components are renormalised.")
    lines.append("")
    lines.append("### Valuation and investment score")
    lines.append("")
    lines.append("Valuation compares the current multiple with the company's own FY2021–FY2025 history: PE, EV/EBITDA, "
                 "P/FCF and FCF yield for industrials; PE, price/book and dividend yield for banks, insurers, holdings "
                 "and real estate. A low score means the shares are expensive relative to their own history, not "
                 "necessarily expensive in absolute terms. The investment score is **40% growth + 30% survival + "
                 "30% valuation**.")
    lines.append("")
    lines.append("### WACC and scenarios")
    lines.append("")
    lines.append(f"Risk-free rate: **{fpct(manifest.get('risk_free_rate'), 2, False)}** (German 10-year Bund); "
                 f"equity risk premium: **{fpct(manifest.get('equity_risk_premium'), 1, False)}**; beta from the "
                 "DAX price analysis where available (otherwise 1.0), clamped to 0.4–1.8. Cost of debt is interest expense / total debt, clamped to "
                 "1–10%; tax rate is the effective rate, clamped to 0–35%. Banks and insurers use the cost of equity "
                 "as the hurdle rate. Five-year scenarios use historical and analyst growth, an exit multiple from the "
                 "company's own history, and current dividend yield; bear/base/bull are weighted 25/50/25. "
                 "Exit multiples are bounded around the current multiple to prevent a single historical median "
                 "from producing implausible five-year re-rating or de-rating. Cyclical troughs with spot PE "
                 "above 1.8× the historical median use average positive annual EPS as the starting basis.")
    lines.append("")
    lines.append("### Score interpretation")
    lines.append("")
    lines.append(md_table(
        ["Score", "Survival", "Growth", "Valuation", "Investment"],
        [
            ["≥ 70", "Low distress", "Strong", "Cheap vs own history", "Attractive score"],
            ["55–70", "Moderate", "Above average", "Slightly cheap", "Above average"],
            ["40–55", "Elevated", "Average", "Mid-range", "Neutral"],
            ["< 40", "High distress", "Weak", "Expensive vs own history", "Unattractive score"],
        ],
        ["right", "left", "left", "left", "left"]
    ))
    lines.append("")

    lines.append("## Coverage and data quality")
    lines.append("")
    lines.append(f"The dataset contains **{len(metrics_rows)} companies** and **{manifest.get('n_companies', len(metrics_rows))} "
                 "scored companies**, with TTM plus up to FY2021–FY2025 depending on listing history. A separate quarterly panel "
                 f"(`data/fundamentals_quarterly.tsv`, {collection_manifest.get('n_quarterly_rows', 0)} rows) "
                 "stores up to 20 quarters of income, balance-sheet and cash-flow data for transparency; it is not "
                 "used in the scorecard. Porsche SE has no quarterly revenue line, and a few individual quarters "
                 "are incomplete at the source. Fundamentals come from stockanalysis.com (data by S&P Global Market "
                 "Intelligence); prices and beta come from the existing CNBC pipeline. Qiagen reports in USD; all "
                 "other companies report in EUR.")
    lines.append("")
    coverage_rows = []
    for row in sorted(metrics_rows, key=lambda item: item["ticker"]):
        coverage_rows.append([
            row["ticker"], row.get("company", ""), row.get("index_name", ""),
            row.get("model_type", ""), row.get("latest_period", ""), row.get("currency", ""),
            (scores.get(row["ticker"], {}).get("data_notes") or "")[:90],
        ])
    lines.append(md_table(["Ticker", "Company", "Index", "Model", "Latest", "Currency", "Data notes"],
                          coverage_rows, ["left", "left", "left", "left", "left", "left", "left"]))
    lines.append("")
    lines.append("Known gaps: Porsche SE has no revenue line (investment holding); R&D is not reported separately by "
                 "many companies; order backlog is available only for a minority; external credit ratings are not "
                 "used as a separate input because a complete, dated mapping for all companies was not available. "
                 "BMW's A stable rating (Morningstar DBRS, October 2026) is consistent with the model as a cross-check; "
                 "Siemens' rating remains unverified. Auto leverage includes captive financial-services debt; where "
                 "the split is identifiable, the score uses industrial debt/EBITDA and the report shows total leverage "
                 "separately. A few recent listings lack enough market data for a valuation score or scenario.")
    lines.append("")

    lines.append("## Cross-company ranking")
    lines.append("")
    ranking_rows = []
    for index, row in enumerate(ranked, start=1):
        metric = metrics.get(row["ticker"], {})
        weighted = (scenarios.get(row["ticker"], {}).get("weighted") or {})
        ranking_rows.append([
            str(index), row["ticker"], row.get("company", ""), score_text(row.get("survival_score")),
            score_text(row.get("growth_score")), score_text(row.get("valuation_score")),
            score_text(row.get("investment_score")), row.get("distress_band", "—"),
            fnum(metric.get("altman_z2"), 2), fpct(metric.get("ohlson_probability"), 1, False),
            fpct(weighted.get("total_return_cagr")),
        ])
    lines.append(md_table(
        ["Rank", "Ticker", "Company", "Survival", "Growth", "Valuation", "Investment", "Band",
         "Altman Z″", "Ohlson 1y", "5y weighted return"],
        ranking_rows, ["right", "left", "left", "right", "right", "right", "right", "left",
                       "right", "right", "right"]
    ))
    lines.append("")
    lines.append("![Survival scores](charts/fundamentals_survival.svg)")
    lines.append("")
    lines.append("![Growth vs survival](charts/fundamentals_scores.svg)")
    lines.append("")
    lines.append("![Growth vs valuation](charts/fundamentals_growth_valuation.svg)")
    lines.append("")

    lines.append("## Distress watchlist")
    lines.append("")
    watch = [row for row in reversed(survival_ranked)
             if row.get("distress_band") in ("High", "Elevated")
             and (row.get("survival_score") or 0) < 60][:12]
    lines.append(md_table(
        ["Ticker", "Company", "Survival", "Band", "Altman Z″", "Ohlson 1y", "Debt/EBITDA", "FCF margin", "Main risk"],
        [[row["ticker"], row.get("company", ""), score_text(row.get("survival_score")),
          row.get("distress_band", "—"), fnum(metrics.get(row["ticker"], {}).get("altman_z2"), 2),
          fpct(metrics.get(row["ticker"], {}).get("ohlson_probability"), 1, False),
          fx(metrics.get(row["ticker"], {}).get("debt_ebitda")),
          fpct(metrics.get(row["ticker"], {}).get("fcf_margin")),
          (row.get("data_notes") or "")[:70]]
         for row in watch],
        ["left", "left", "right", "left", "right", "right", "right", "right", "left"]
    ))
    lines.append("")
    lines.append("The watchlist is a relative screen. A high score does not prove safety, and a low score does not "
                 "predict default; sector models and data gaps materially affect the ranking.")
    lines.append("")

    lines.append("## Growth leaders")
    lines.append("")
    lines.append(md_table(
        ["Rank", "Ticker", "Company", "Growth", "Revenue CAGR", "EPS CAGR", "ROIC−WACC", "Investment"],
        [[str(i), row["ticker"], row.get("company", ""), score_text(row.get("growth_score")),
          fpct(metrics.get(row["ticker"], {}).get("revenue_cagr")),
          fpct(metrics.get(row["ticker"], {}).get("eps_cagr")),
          fpp(metrics.get(row["ticker"], {}).get("roic_wacc_spread")),
          score_text(row.get("investment_score"))]
         for i, row in enumerate(growth_ranked[:12], start=1)],
        ["right", "left", "left", "right", "right", "right", "right", "right"]
    ))
    lines.append("")

    lines.append("## Valuation extremes")
    lines.append("")
    lines.append("**Cheapest vs own history**")
    lines.append("")
    lines.append(md_table(
        ["Ticker", "Company", "Valuation", "PE", "EV/EBITDA", "P/FCF", "FCF yield"],
        [[row["ticker"], row.get("company", ""), score_text(row.get("valuation_score")),
          fx(metrics.get(row["ticker"], {}).get("pe")),
          fx(metrics.get(row["ticker"], {}).get("ev_ebitda")),
          fx(metrics.get(row["ticker"], {}).get("p_fcf")),
          fpct(metrics.get(row["ticker"], {}).get("fcf_yield"), 2, False)]
         for row in valuation_ranked[:8]],
        ["left", "left", "right", "right", "right", "right", "right"]
    ))
    lines.append("")
    lines.append("**Most expensive vs own history**")
    lines.append("")
    lines.append(md_table(
        ["Ticker", "Company", "Valuation", "PE", "EV/EBITDA", "P/FCF", "FCF yield"],
        [[row["ticker"], row.get("company", ""), score_text(row.get("valuation_score")),
          fx(metrics.get(row["ticker"], {}).get("pe")),
          fx(metrics.get(row["ticker"], {}).get("ev_ebitda")),
          fx(metrics.get(row["ticker"], {}).get("p_fcf")),
          fpct(metrics.get(row["ticker"], {}).get("fcf_yield"), 2, False)]
         for row in list(reversed(valuation_ranked[-8:]))],
        ["left", "left", "right", "right", "right", "right", "right"]
    ))
    lines.append("")

    lines.append("## Sector and model aggregates")
    lines.append("")
    aggregate_rows = []
    for model_type in MODEL_DESCRIPTIONS:
        members = [row for row in scores_rows if row.get("model_type") == model_type]
        if not members:
            continue
        def mean_score(key):
            values = [row.get(key) for row in members if row.get(key) is not None]
            return statistics.fmean(values) if values else None
        aggregate_rows.append([
            model_type, str(len(members)), score_text(mean_score("survival_score")),
            score_text(mean_score("growth_score")), score_text(mean_score("valuation_score")),
            score_text(mean_score("investment_score")),
        ])
    lines.append(md_table(
        ["Model", "N", "Avg survival", "Avg growth", "Avg valuation", "Avg investment"],
        aggregate_rows, ["left", "right", "right", "right", "right", "right"]
    ))
    lines.append("")

    lines.append("## Company-by-company analysis")
    lines.append("")
    for row in sorted(metrics_rows, key=lambda item: item.get("company", item["ticker"])):
        ticker = row["ticker"]
        lines.append(company_section(row, scores.get(ticker, {}), scenarios.get(ticker, {})))
        lines.append("")

    lines.append("## Limitations and disclaimer")
    lines.append("")
    lines.append("- **No historical validation labels.** The current index snapshot contains no defaults in the collected sample, and a consistent "
                 "panel of historical default/growth labels was not available. The scores are transparent relative "
                 "scorecards, not statistically validated default or return models.")
    lines.append("- **Ohlson probabilities are not calibrated.** The original model uses a GNP price index and a "
                 "different sample period; the implementation uses a constant index and operating cash flow as the "
                 "FFO proxy. Treat the output as a relative distress indicator.")
    lines.append("- **Sector models are judgment-based.** Banks, insurers, utilities, telecoms, holdings, real estate, "
                 "asset-light companies and financial infrastructure use different ratios because Altman/Ohlson are "
                 "not meaningful for their balance sheets. The thresholds are transparent but not estimated from a "
                 "labelled dataset.")
    lines.append("- **Data gaps.** R&D, order backlog, debt-maturity schedules, market share, organic growth and "
                 "external credit ratings are incomplete. Missing inputs are renormalised, which changes the "
                 "effective weights by company.")
    lines.append("- **MDAX/SDAX market data.** The daily price pipeline still covers the DAX 40; for the "
                 "additional companies the scenario price is derived from market cap / diluted shares and beta "
                 "defaults to 1.0 where no DAX beta is available.")
    lines.append("- **Captive finance.** BMW, Mercedes-Benz, Volkswagen and Daimler Truck have financial-services "
                 "operations. Total debt/EBITDA overstates industrial leverage; where the split is available the score "
                 "uses industrial debt/EBITDA, but the adjustment is imperfect.")
    lines.append("- **Scenario sensitivity.** Five-year returns are highly sensitive to the starting multiple and the "
                 "exit multiple. The bear/base/bull cases are illustrations, not forecasts or target prices.")
    lines.append("- **Not investment advice.** This report is a statistical and analytical exercise. It does not "
                 "constitute investment, legal, tax or accounting advice. Verify all data against primary filings "
                 "before making decisions.")
    lines.append("")
    lines.append("## Reproducibility")
    lines.append("")
    lines.append("```bash")
    lines.append("python3 scripts/fundamentals.py collect   # network; rate-limited and cached")
    lines.append("python3 scripts/fundamentals.py analyze   # metrics, scores, scenarios")
    lines.append("python3 scripts/fundamentals.py report    # Markdown report and SVG charts")
    lines.append("```")
    lines.append("")
    lines.append("Data files: `data/fundamentals.tsv`, `data/fundamentals_quarterly.tsv`, "
                 "`data/forecasts.tsv`, `data/fundamentals_metrics.tsv`, "
                 "`data/fundamentals_scores.tsv`, `data/fundamentals_scenarios.tsv`, "
                 "`data/fundamentals_manifest.json`, `data/fundamentals_analysis_manifest.json`.")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote charts to {CHARTS_DIR}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Corporate distress and long-term investment forecasting for DAX 40, MDAX and SDAX."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect", help="fetch annual, quarterly and forecast data")
    collect_parser.add_argument("--refresh", action="store_true", help="ignore the on-disk cache")
    collect_parser.add_argument("--only", default="", help="comma-separated tickers")
    collect_parser.add_argument("--skip-forecast", action="store_true",
                                help="do not fetch analyst forecast pages")
    collect_parser.add_argument("--skip-quarterly", action="store_true",
                                help="do not fetch quarterly statements")
    subparsers.add_parser("analyze", help="compute metrics, scores and scenarios")
    subparsers.add_parser("report", help="render the Markdown report and charts")
    all_parser = subparsers.add_parser("all", help="collect, analyze and report")
    all_parser.add_argument("--refresh", action="store_true", help="ignore the on-disk cache")
    all_parser.add_argument("--only", default="", help="comma-separated tickers")
    all_parser.add_argument("--skip-forecast", action="store_true",
                            help="do not fetch analyst forecast pages")
    all_parser.add_argument("--skip-quarterly", action="store_true",
                            help="do not fetch quarterly statements")
    args = parser.parse_args()

    only = [item for item in getattr(args, "only", "").split(",") if item.strip()]
    if args.command == "collect":
        collect_all(refresh=args.refresh, only=only, skip_forecast=args.skip_forecast,
                    skip_quarterly=args.skip_quarterly)
    elif args.command == "analyze":
        analyze()
    elif args.command == "report":
        report()
    elif args.command == "all":
        collect_all(refresh=args.refresh, only=only, skip_forecast=args.skip_forecast,
                    skip_quarterly=args.skip_quarterly)
        analyze()
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
