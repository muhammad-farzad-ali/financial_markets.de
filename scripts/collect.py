#!/usr/bin/env python3
"""Collect German market data into a single long-format TSV file.

Sources
-------
* CNBC quote + chart endpoints for daily OHLCV of German indices, DAX 40
  constituents, EUR/USD and the German 10-year Bund yield.
* Wikipedia's DAX page for the current index composition and sector labels.

The collector is deliberately polite: one request per second, exponential
backoff on HTTP 429/5xx, and a six-hour on-disk cache.  Use ``--refresh`` to
force a network refresh.

Usage
-----
    python3 scripts/collect.py
    python3 scripts/collect.py --refresh
    python3 scripts/collect.py --only DAX,SAP.DE
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_module
import re
import sys
import urllib.parse
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path

from lib import (
    CONFIG_DIR,
    DATA_DIR,
    RAW_DIR,
    FetchError,
    business_days,
    ensure_dirs,
    fetch_json,
    fetch_text,
    iso_now,
    parse_date,
    parse_number,
    read_tsv,
    utc_now,
    write_json,
    write_tsv,
)

CHART_URL = (
    "https://ts-api.cnbc.com/harmony/app/charts/1Y.json"
    "?symbol={symbol}&interval=1D"
)
QUOTE_URL = (
    "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
    "?symbols={symbol}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json"
)
WIKI_URL = "https://en.wikipedia.org/wiki/DAX"

TSV_HEADERS = [
    "date",
    "series_id",
    "name",
    "asset_class",
    "group",
    "industry",
    "sector",
    "nace_code",
    "nace_label",
    "nace_section",
    "nace_section_label",
    "nace_division",
    "nace_division_label",
    "unit",
    "currency",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source",
    "source_symbol",
    "fetched_at",
]

NACE_FIELDS = [
    "nace_code",
    "nace_label",
    "nace_section",
    "nace_section_label",
    "nace_division",
    "nace_division_label",
]

# Fallback composition if Wikipedia cannot be parsed.  Kept in sync with the
# table on https://en.wikipedia.org/wiki/DAX (checked 2026-11-09).
FALLBACK_CONSTITUENTS = [
    ("ADS.DE", "Adidas", "Apparel"),
    ("AIR.PA", "Airbus", "Aerospace & Defence"),
    ("ALV.DE", "Allianz", "Financial Services"),
    ("BAS.DE", "BASF", "Chemicals"),
    ("BAYN.DE", "Bayer", "Pharmaceuticals"),
    ("BEI.DE", "Beiersdorf", "Consumer goods"),
    ("BMW.DE", "BMW", "Automotive"),
    ("BNR.DE", "Brenntag", "Distribution"),
    ("CBK.DE", "Commerzbank", "Financial Services"),
    ("CON.DE", "Continental", "Automotive"),
    ("DB1.DE", "Deutsche Börse", "Financial Services"),
    ("DBK.DE", "Deutsche Bank", "Financial Services"),
    ("DHL.DE", "DHL Group", "Logistics"),
    ("DTE.DE", "Deutsche Telekom", "Telecommunication"),
    ("DTG.DE", "Daimler Truck", "Automotive"),
    ("ENR.DE", "Siemens Energy", "Energy technology"),
    ("EOAN.DE", "E.ON", "Utilities"),
    ("FME.DE", "Fresenius Medical Care", "Healthcare"),
    ("FRE.DE", "Fresenius", "Healthcare"),
    ("G1A.DE", "GEA Group", "Mechanical Engineering"),
    ("G24.DE", "Scout24", "E-Commerce"),
    ("HEI.DE", "Heidelberg Materials", "Construction Materials"),
    ("HEN3.DE", "Henkel", "Consumer Goods"),
    ("HNR1.DE", "Hannover Rück", "Insurance"),
    ("IFX.DE", "Infineon", "Technology"),
    ("MBG.DE", "Mercedes-Benz Group", "Automotive"),
    ("MRK.DE", "Merck KGaA", "Pharmaceuticals"),
    ("MTX.DE", "MTU Aero Engines", "Aerospace & Defence"),
    ("MUV2.DE", "Munich Re", "Financial Services"),
    ("PAH3.DE", "Porsche SE", "Automotive"),
    ("QIA.DE", "Qiagen", "Biotech"),
    ("RHM.DE", "Rheinmetall", "Aerospace & Defence"),
    ("RWE.DE", "RWE", "Utilities"),
    ("SAP.DE", "SAP", "Technology"),
    ("SHL.DE", "Siemens Healthineers", "Medical Equipment"),
    ("SIE.DE", "Siemens", "Industrials"),
    ("SY1.DE", "Symrise", "Chemicals"),
    ("VNA.DE", "Vonovia", "Real Estate"),
    ("VOW3.DE", "Volkswagen", "Automotive"),
    ("ZAL.DE", "Zalando", "E-Commerce"),
]

FALLBACK_SECTOR_MAP = {
    "Aerospace & Defence": "Industrials",
    "Apparel": "Consumer Discretionary",
    "Automotive": "Consumer Discretionary",
    "Biotech": "Health Care",
    "Chemicals": "Materials",
    "Construction Materials": "Materials",
    "Consumer Goods": "Consumer Staples",
    "Consumer goods": "Consumer Staples",
    "Distribution": "Industrials",
    "E-Commerce": "Consumer Discretionary",
    "Energy technology": "Industrials",
    "Financial Services": "Financials",
    "Healthcare": "Health Care",
    "Industrials": "Industrials",
    "Insurance": "Financials",
    "Logistics": "Industrials",
    "Mechanical Engineering": "Industrials",
    "Medical Equipment": "Health Care",
    "Pharmaceuticals": "Health Care",
    "Real Estate": "Real Estate",
    "Technology": "Information Technology",
    "Telecommunication": "Communication Services",
    "Utilities": "Utilities",
}


# ---------------------------------------------------------------------------
# Configuration and composition
# ---------------------------------------------------------------------------

def load_base_series() -> list[dict]:
    rows = read_tsv(CONFIG_DIR / "series.tsv")
    series = []
    for row in rows:
        item = {
            "series_id": row["series_id"],
            "name": row["name"],
            "asset_class": row["asset_class"],
            "group": row["group"],
            "unit": row["unit"],
            "currency": row["currency"],
            "symbol": row["symbol"],
            "industry": "",
            "sector": "",
        }
        item.update({field: "" for field in NACE_FIELDS})
        series.append(item)
    return series


def load_nace_map() -> dict[str, dict]:
    path = CONFIG_DIR / "nace_map.tsv"
    if not path.exists():
        return {}
    return {row["ticker"]: row for row in read_tsv(path)}


def load_sector_map() -> dict[str, str]:
    path = CONFIG_DIR / "sector_map.tsv"
    if not path.exists():
        return dict(FALLBACK_SECTOR_MAP)
    mapping = {row["industry"]: row["sector"] for row in read_tsv(path)}
    return mapping or dict(FALLBACK_SECTOR_MAP)


def clean_cell(value: str) -> str:
    text = html_module.unescape(re.sub(r"<[^>]+>", "", value or ""))
    text = re.sub(r"\[[^\]]*\]", "", text)  # footnote markers
    return re.sub(r"\s+", " ", text).strip()


def parse_dax_wikipedia(text: str) -> list[dict]:
    """Parse the DAX composition table from Wikipedia."""
    tables = re.findall(r"<table[^>]*wikitable.*?</table>", text, re.S)
    table = next((t for t in tables if "Ticker" in t), None)
    if not table:
        return []

    rows = re.findall(r"<tr.*?</tr>", table, re.S)
    if not rows:
        return []
    header = [
        clean_cell(cell)
        for cell in re.findall(r"<t[dh].*?</t[dh]>", rows[0], re.S)
    ]
    index = {name: i for i, name in enumerate(header)}
    ticker_i = index.get("Ticker")
    company_i = index.get("Company")
    sector_i = next(
        (i for name, i in index.items() if name.startswith("Prime Standard Sector")),
        index.get("Sector"),
    )
    weight_i = next(
        (i for name, i in index.items() if name.startswith("Index weighting")),
        None,
    )
    if ticker_i is None or company_i is None or sector_i is None:
        return []

    constituents: list[dict] = []
    for row in rows[1:]:
        cells = [
            clean_cell(cell)
            for cell in re.findall(r"<t[dh].*?</t[dh]>", row, re.S)
        ]
        if len(cells) <= max(ticker_i, company_i, sector_i):
            continue
        ticker = cells[ticker_i]
        company = cells[company_i]
        industry = cells[sector_i]
        if not ticker or not company or not industry:
            continue
        weight = parse_number(cells[weight_i]) if weight_i is not None and weight_i < len(cells) else None
        constituents.append(
            {
                "ticker": ticker,
                "company": company,
                "industry": industry,
                "weight_pct": weight,
            }
        )
    return constituents


def load_constituents(refresh: bool) -> list[dict]:
    """Load the DAX composition from Wikipedia with a static fallback."""
    wiki_cache = RAW_DIR / "dax_wikipedia.html"
    try:
        text = fetch_text(WIKI_URL, wiki_cache, refresh=refresh)
        constituents = parse_dax_wikipedia(text)
        if len(constituents) < 30:
            raise ValueError(f"only {len(constituents)} rows parsed")
        source = "Wikipedia"
    except Exception as exc:  # noqa: BLE001 - fall back to the static list
        print(f"  ! Wikipedia composition unavailable ({exc}); using fallback list")
        constituents = [
            {"ticker": ticker, "company": company, "industry": industry, "weight_pct": None}
            for ticker, company, industry in FALLBACK_CONSTITUENTS
        ]
        source = "fallback"

    sector_map = load_sector_map()
    nace_map = load_nace_map()
    missing_nace = []
    for item in constituents:
        item["sector"] = sector_map.get(item["industry"], "Other")
        item["series_id"] = item["ticker"]
        item["source"] = source
        nace = nace_map.get(item["ticker"], {})
        for field in NACE_FIELDS:
            item[field] = nace.get(field, "")
        if not item["nace_code"]:
            missing_nace.append(item["ticker"])
    if missing_nace:
        print(f"  ! no NACE mapping for: {', '.join(missing_nace)}")
    return constituents


def write_constituents(constituents: list[dict]) -> None:
    headers = [
        "ticker",
        "company",
        "industry",
        "sector",
        "nace_code",
        "nace_label",
        "nace_section",
        "nace_section_label",
        "nace_division",
        "nace_division_label",
        "weight_pct",
        "series_id",
        "source",
    ]
    rows = []
    for item in constituents:
        rows.append(
            [
                item["ticker"],
                item["company"],
                item["industry"],
                item["sector"],
                item["nace_code"],
                item["nace_label"],
                item["nace_section"],
                item["nace_section_label"],
                item["nace_division"],
                item["nace_division_label"],
                "" if item["weight_pct"] is None else f"{item['weight_pct']:.4f}",
                item["series_id"],
                item["source"],
            ]
        )
    write_tsv(DATA_DIR / "dax_constituents.tsv", headers, rows)


# ---------------------------------------------------------------------------
# CNBC access
# ---------------------------------------------------------------------------

def safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_") or "series"


def cache_name(series_id: str, symbol: str, kind: str) -> str:
    """Unique cache file name; a hash avoids '.' and '-' collisions."""
    digest = hashlib.sha1(symbol.encode("utf-8")).hexdigest()[:8]
    return f"{safe_token(series_id)}_{safe_token(symbol)}_{digest}_{kind}.json"


def symbol_candidates(ticker: str) -> list[str]:
    if ticker.startswith("."):
        return [ticker]
    candidates = [ticker]
    if "." in ticker:
        candidates.append(ticker.replace(".", "-"))
    return candidates


def fetch_chart(symbol: str, series_id: str, refresh: bool) -> list[dict]:
    url = CHART_URL.format(symbol=urllib.parse.quote(symbol, safe=""))
    cache = RAW_DIR / cache_name(series_id, symbol, "chart")
    payload = fetch_json(url, cache, refresh=refresh)
    bars = payload.get("barData", {}).get("priceBars") or []
    if not bars and cache.exists():
        # Do not keep an error response in the cache: the next run retries.
        cache.unlink(missing_ok=True)
    return bars


def fetch_quote(symbol: str, series_id: str, refresh: bool) -> dict | None:
    url = QUOTE_URL.format(symbol=urllib.parse.quote(symbol, safe=""))
    cache = RAW_DIR / cache_name(series_id, symbol, "quote")
    payload = fetch_json(url, cache, refresh=refresh)
    result = payload.get("FormattedQuoteResult", {}).get("FormattedQuote") or []
    if not result or result[0].get("code") != 0:
        if cache.exists():
            cache.unlink(missing_ok=True)
        return None
    return result[0]


def resolve_series(series: dict, refresh: bool) -> tuple[str, list[dict], dict | None]:
    """Find the CNBC symbol variant with the longest usable history.

    Some German tickers work with a dot (``SAP.DE``) while others only work
    with a hyphen (``BMW-DE``).  A few dot symbols resolve to a different,
    recently listed security, so a short history is also treated as a hint to
    try the hyphen variant.
    """
    errors: list[str] = []
    best: tuple[str, list[dict]] | None = None
    for symbol in symbol_candidates(series["symbol"]):
        try:
            bars = fetch_chart(symbol, series["series_id"], refresh)
        except FetchError as exc:
            errors.append(str(exc))
            continue
        if bars and (best is None or len(bars) > len(best[1])):
            best = (symbol, bars)
        if bars and len(bars) >= 200:
            break
        if not bars:
            errors.append(f"empty chart for {symbol}")

    if best is None:
        raise FetchError("; ".join(errors[-3:]) or "no symbol variant worked")

    symbol, bars = best
    quote = None
    try:
        quote = fetch_quote(symbol, series["series_id"], refresh)
    except FetchError as exc:
        errors.append(str(exc))
    return symbol, bars, quote


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def parse_bars(raw_bars: list[dict], asset_class: str = "equity") -> tuple[list[dict], int, int]:
    """Convert CNBC price bars to clean daily OHLCV rows.

    OHLC consistency is only counted for equities and indices. FX and rate
    series use different high/low conventions and their intraday fields are
    not used by the analysis.
    """
    by_date: OrderedDict[str, dict] = OrderedDict()
    duplicates = 0
    ohlc_violations = 0

    for raw in raw_bars:
        stamp = str(raw.get("tradeTime") or "")
        if len(stamp) < 8:
            continue
        try:
            day = datetime.strptime(stamp[:8], "%Y%m%d").date().isoformat()
        except ValueError:
            continue
        close = parse_number(raw.get("close"))
        if close is None or close <= 0:
            continue
        open_ = parse_number(raw.get("open"))
        high = parse_number(raw.get("high"))
        low = parse_number(raw.get("low"))
        volume = parse_number(raw.get("volume")) or 0.0

        open_ = open_ if open_ is not None and open_ > 0 else close
        high = high if high is not None and high > 0 else max(open_, close)
        low = low if low is not None and low > 0 else min(open_, close)
        if high < max(open_, close) or low > min(open_, close) or low > high:
            if asset_class in ("equity", "index"):
                ohlc_violations += 1
            high = max(high, open_, close)
            low = min(low, open_, close)

        if day in by_date:
            duplicates += 1
            by_date.pop(day)
        by_date[day] = {
            "date": day,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return sorted(by_date.values(), key=lambda row: row["date"]), duplicates, ohlc_violations


def quote_bar(quote: dict | None) -> dict | None:
    if not quote:
        return None
    last = parse_number(quote.get("last"))
    stamp = str(quote.get("last_time") or "")
    if last is None or last <= 0:
        return None
    match = re.match(r"(\d{4}-\d{2}-\d{2})", stamp)
    if not match:
        return None
    open_ = parse_number(quote.get("open")) or last
    high = parse_number(quote.get("high")) or max(open_, last)
    low = parse_number(quote.get("low")) or min(open_, last)
    volume = parse_number(quote.get("volume")) or 0.0
    return {
        "date": match.group(1),
        "open": open_,
        "high": max(high, open_, last),
        "low": min(low, open_, last),
        "close": last,
        "volume": volume,
    }


def merge_quote(bars: list[dict], quote: dict | None) -> tuple[list[dict], bool]:
    """Append the current session if the daily chart is one day behind."""
    extra = quote_bar(quote)
    if not extra or not bars:
        return bars, False
    last_date = bars[-1]["date"]
    if extra["date"] > last_date:
        return bars + [extra], True
    if extra["date"] == last_date:
        # The chart bar is the official close; only fill missing volume.
        if not bars[-1].get("volume") and extra.get("volume"):
            bars[-1]["volume"] = extra["volume"]
    return bars, False


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Collect German market data into a TSV.")
    parser.add_argument("--refresh", action="store_true", help="ignore the on-disk cache")
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated series ids to collect (default: everything)",
    )
    args = parser.parse_args()

    ensure_dirs()
    fetched_at = iso_now()
    only = {item.strip() for item in args.only.split(",") if item.strip()}

    print("Loading DAX composition ...")
    constituents = load_constituents(args.refresh)
    write_constituents(constituents)
    print(f"  {len(constituents)} constituents ({constituents[0]['source']})")

    base_series = load_base_series()
    series_list = list(base_series)
    for item in constituents:
        series_list.append(
            {
                "series_id": item["series_id"],
                "name": item["company"],
                "asset_class": "equity",
                "group": "DAX 40",
                "unit": "EUR",
                "currency": "EUR",
                "symbol": item["ticker"],
                "industry": item["industry"],
                "sector": item["sector"],
                **{field: item[field] for field in NACE_FIELDS},
            }
        )

    if only:
        series_list = [s for s in series_list if s["series_id"] in only]
        if not series_list:
            print(f"No matching series for --only {sorted(only)}", file=sys.stderr)
            return 2

    print(f"Collecting {len(series_list)} series (throttled to 1 request/second) ...")
    all_rows: list[list] = []
    manifest_series: list[dict] = []
    errors: list[dict] = []
    as_of: str = ""

    for position, series in enumerate(series_list, start=1):
        label = f"[{position:>2}/{len(series_list)}] {series['series_id']:<10} {series['name']}"
        try:
            symbol, raw_bars, quote = resolve_series(series, args.refresh)
            bars, duplicates, violations = parse_bars(raw_bars, series["asset_class"])
            bars, appended = merge_quote(bars, quote)
            if not bars:
                raise FetchError("no valid daily bars")

            for bar in bars:
                all_rows.append(
                    [
                        bar["date"],
                        series["series_id"],
                        series["name"],
                        series["asset_class"],
                        series["group"],
                        series.get("industry", ""),
                        series.get("sector", ""),
                        series.get("nace_code", ""),
                        series.get("nace_label", ""),
                        series.get("nace_section", ""),
                        series.get("nace_section_label", ""),
                        series.get("nace_division", ""),
                        series.get("nace_division_label", ""),
                        series["unit"],
                        series["currency"],
                        f"{bar['open']:.6f}",
                        f"{bar['high']:.6f}",
                        f"{bar['low']:.6f}",
                        f"{bar['close']:.6f}",
                        f"{bar['volume']:.0f}",
                        "CNBC",
                        symbol,
                        fetched_at,
                    ]
                )

            end = bars[-1]["date"]
            as_of = max(as_of, end)
            expected = business_days(parse_date(bars[0]["date"]), parse_date(end))
            manifest_series.append(
                {
                    "series_id": series["series_id"],
                    "name": series["name"],
                    "asset_class": series["asset_class"],
                    "group": series["group"],
                    "symbol": symbol,
                    "rows": len(bars),
                    "start": bars[0]["date"],
                    "end": end,
                    "duplicates_removed": duplicates,
                    "ohlc_violations": violations,
                    "quote_appended": appended,
                    "expected_business_days": expected,
                    "missing_business_days": max(0, expected - len(bars)),
                    "error": None,
                }
            )
            flag = " +quote" if appended else ""
            print(f"{label}  {len(bars):>4} bars  {bars[0]['date']}..{end}{flag}")
        except Exception as exc:  # noqa: BLE001 - keep going for other series
            message = str(exc)
            errors.append({"series_id": series["series_id"], "error": message})
            manifest_series.append(
                {
                    "series_id": series["series_id"],
                    "name": series["name"],
                    "asset_class": series["asset_class"],
                    "group": series["group"],
                    "symbol": series.get("symbol"),
                    "rows": 0,
                    "start": None,
                    "end": None,
                    "duplicates_removed": 0,
                    "ohlc_violations": 0,
                    "quote_appended": False,
                    "expected_business_days": 0,
                    "missing_business_days": 0,
                    "error": message,
                }
            )
            print(f"{label}  FAILED: {message}")

    if not all_rows:
        print("No data collected.", file=sys.stderr)
        return 1

    all_rows.sort(key=lambda row: (row[1], row[0]))
    write_tsv(DATA_DIR / "german_markets.tsv", TSV_HEADERS, all_rows)

    manifest = {
        "generated_at": fetched_at,
        "as_of": as_of,
        "series_count": len(manifest_series),
        "successful_series": len(manifest_series) - len(errors),
        "row_count": len(all_rows),
        "errors": errors,
        "series": manifest_series,
    }
    write_json(DATA_DIR / "collection_manifest.json", manifest)

    print(
        f"\nWrote {len(all_rows):,} rows for "
        f"{manifest['successful_series']}/{len(manifest_series)} series "
        f"to {DATA_DIR / 'german_markets.tsv'}"
    )
    if errors:
        print(f"{len(errors)} series failed; see data/collection_manifest.json", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
