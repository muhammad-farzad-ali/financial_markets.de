"""Shared utilities for the financial_markets.de pipeline.

The whole project deliberately uses only the Python standard library so it
can run on a bare Python 3.11+ installation (no pandas, numpy or yfinance).

Rate limiting
-------------
The CNBC endpoints used by the collector are unofficial.  They answer with
HTTP 429 when called too quickly, therefore every request is throttled to at
most one per second (``MIN_REQUEST_INTERVAL``), retried with exponential
backoff, and cached on disk for ``CACHE_TTL_HOURS`` hours.  A full refresh
therefore performs at most ~100 requests and takes roughly two minutes.
"""

from __future__ import annotations

import csv
import json
import math
import random
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REPORTS_DIR = ROOT / "reports"
CHARTS_DIR = REPORTS_DIR / "charts"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Rate limiting / HTTP
# ---------------------------------------------------------------------------

MIN_REQUEST_INTERVAL = 1.0  # seconds between any two outbound requests
MAX_RETRIES = 5
BACKOFF_BASE = 2.0
CACHE_TTL_HOURS = 6.0

_last_request_ts = 0.0


class FetchError(RuntimeError):
    """Raised when a remote endpoint cannot be read after all retries."""


def ensure_dirs() -> None:
    for directory in (CONFIG_DIR, DATA_DIR, RAW_DIR, REPORTS_DIR, CHARTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _throttle() -> None:
    """Keep at least MIN_REQUEST_INTERVAL seconds between requests."""
    global _last_request_ts
    elapsed = time.monotonic() - _last_request_ts
    wait = MIN_REQUEST_INTERVAL - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def http_get(url: str, timeout: int = 30) -> bytes:
    """HTTP GET with throttling, retry/backoff and Retry-After support."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        _throttle()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/csv,text/html,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if exc.code in (429, 500, 502, 503, 504):
                delay = BACKOFF_BASE ** attempt + random.uniform(0.0, 1.0)
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                time.sleep(delay)
                continue
            raise FetchError(f"HTTP {exc.code} for {url}") from exc
        except Exception as exc:  # noqa: BLE001 - network errors are retried
            last_error = exc
            time.sleep(BACKOFF_BASE ** attempt + random.uniform(0.0, 1.0))
    raise FetchError(f"Failed after {MAX_RETRIES} attempts: {url}: {last_error}")


def _cache_is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age_hours = (time.time() - path.stat().st_mtime) / 3600.0
    return age_hours < CACHE_TTL_HOURS


def fetch_text(url: str, cache_path: Path | None = None, refresh: bool = False) -> str:
    """Fetch text, optionally reusing a fresh on-disk cache entry."""
    if cache_path and not refresh and _cache_is_fresh(cache_path):
        return cache_path.read_text(encoding="utf-8", errors="replace")
    payload = http_get(url)
    text = payload.decode("utf-8", errors="replace")
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
    return text


def fetch_json(url: str, cache_path: Path | None = None, refresh: bool = False) -> dict:
    """Fetch JSON, optionally reusing a fresh on-disk cache entry."""
    if cache_path and not refresh and _cache_is_fresh(cache_path):
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    text = fetch_text(url, cache_path, refresh=refresh)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Parsing / formatting
# ---------------------------------------------------------------------------

def parse_number(value) -> float | None:
    """Parse numbers such as ``1,234.56``, ``3.25%`` or ``-`` into floats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace("\u00a0", " ").replace(" ", "")
    if text in ("", "-", "--", "N/A", "n/a", "None", "null", "nan"):
        return None
    text = text.replace("%", "").replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def parse_date(value: str) -> date | None:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolation percentile (q in [0, 1])."""
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


def mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return statistics.fmean(clean) if clean else None


def stdev(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return statistics.stdev(clean) if len(clean) > 1 else None


def skewness(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    n = len(clean)
    if n < 3:
        return None
    avg = statistics.fmean(clean)
    sd = statistics.pstdev(clean)
    if sd == 0:
        return None
    return sum(((v - avg) / sd) ** 3 for v in clean) / n


def excess_kurtosis(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    n = len(clean)
    if n < 4:
        return None
    avg = statistics.fmean(clean)
    sd = statistics.pstdev(clean)
    if sd == 0:
        return None
    return sum(((v - avg) / sd) ** 4 for v in clean) / n - 3.0


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def fmt_pct(value: float | None, digits: int = 1, sign: bool = True) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    percent = value * 100.0
    if sign:
        return f"{percent:+.{digits}f}%"
    return f"{percent:.{digits}f}%"


def fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:,.{digits}f}"


def fmt_int(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{int(round(value)):,}"


def md_table(headers: list[str], rows: list[list], aligns: list[str] | None = None) -> str:
    """Render a GitHub-flavoured Markdown table."""
    aligns = aligns or ["left"] * len(headers)

    def cell(value) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").strip()

    lines = ["| " + " | ".join(cell(h) for h in headers) + " |"]
    separators = []
    for align in aligns:
        if align == "right":
            separators.append("---:")
        elif align == "center":
            separators.append(":---:")
        else:
            separators.append(":---")
    lines.append("| " + " | ".join(separators) + " |")
    for row in rows:
        lines.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(lines)


def write_tsv(path: Path, headers: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def business_days(start: date, end: date) -> int:
    """Number of Monday-Friday days between two dates, inclusive."""
    if end < start:
        return 0
    days = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days += 1
        cursor += timedelta(days=1)
    return days


def xml_escape(text) -> str:
    return escape(str(text), {'"': "&quot;", "'": "&apos;"})
