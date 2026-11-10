#!/usr/bin/env python3
"""Run the complete pipeline: collect -> analyse -> report -> fundamentals -> web.

Each stage is executed as a separate process so that its own command-line
options and exit status are preserved.  The final web stage renders the static
dashboards from the fundamentals TSVs; it does not touch the network.  The collector is the only stage that
touches the network; the analysis and report stages are deterministic given
the collected TSV.

Examples
--------
    python3 scripts/run_all.py
    python3 scripts/run_all.py --refresh
    python3 scripts/run_all.py --skip-collect
    python3 scripts/run_all.py --only DAX,SAP.DE
    python3 scripts/run_all.py --skip-fundamentals
    python3 scripts/run_all.py --fundamentals-only SAP.DE
    python3 scripts/run_all.py --skip-quarterly
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
FUNDAMENTALS_SCORES = SCRIPTS_DIR.parent / "data" / "fundamentals_scores.tsv"


def run_stage(name: str, arguments: list[str]) -> None:
    command = [sys.executable, str(SCRIPTS_DIR / name), *arguments]
    print(f"\n=== {name} ===", flush=True)
    result = subprocess.run(command)
    if result.returncode != 0:
        raise SystemExit(f"{name} failed with exit code {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the full German markets, fundamentals and web pipeline.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the on-disk cache and download fresh data",
    )
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="reuse existing price and fundamentals data; no network access",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated series ids for the price collector (implies a partial dataset)",
    )
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="skip the fundamentals collection/analysis/report stage",
    )
    parser.add_argument(
        "--fundamentals-only",
        default="",
        help="comma-separated DAX tickers for the fundamentals collector",
    )
    parser.add_argument(
        "--skip-quarterly",
        action="store_true",
        help="do not fetch quarterly fundamentals",
    )
    args = parser.parse_args()
    if args.skip_collect and args.refresh:
        parser.error("--skip-collect and --refresh are mutually exclusive")
    if args.skip_collect and args.fundamentals_only:
        parser.error("--skip-collect and --fundamentals-only are mutually exclusive")

    if not args.skip_collect:
        collect_args: list[str] = []
        if args.refresh:
            collect_args.append("--refresh")
        if args.only:
            collect_args.extend(["--only", args.only])
        run_stage("collect.py", collect_args)

    run_stage("analyze.py", [])
    run_stage("report.py", [])

    if not args.skip_fundamentals:
        if args.skip_collect:
            # --skip-collect is the no-network path: reuse both the price TSV
            # and the cached fundamentals, then recompute scores and reports.
            run_stage("fundamentals.py", ["analyze"])
            run_stage("fundamentals.py", ["report"])
        else:
            fundamentals_args = ["all"]
            if args.refresh:
                fundamentals_args.append("--refresh")
            if args.fundamentals_only:
                fundamentals_args.extend(["--only", args.fundamentals_only])
            if args.skip_quarterly:
                fundamentals_args.append("--skip-quarterly")
            run_stage("fundamentals.py", fundamentals_args)

    if FUNDAMENTALS_SCORES.exists():
        run_stage("web.py", [])
    else:
        print("Skipping web.py: run the fundamentals stage first.", flush=True)

    print("\nPipeline finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
