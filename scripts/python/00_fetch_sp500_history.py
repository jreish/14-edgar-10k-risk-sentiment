#!/usr/bin/env python3
"""Fetch and validate historical S&P 500 constituents, year by year.

Source: fja05680/sp500 on GitHub.
  - "S&P 500 Historical Components & Changes (Updated).csv" — one row per
    date the index changed, with the full constituent ticker list as of
    that date. Verified via the GitHub API (2026-08-02) to be the actively
    maintained file (last commit 2026-07-13); a similarly-named file
    without "(Updated)" is frozen since 2019-11-21 and must NOT be used
    (see decisions.md).
  - "sp500_ticker_start_end.csv" — one row per contiguous membership stint
    per ticker (a ticker can have multiple non-contiguous stints, e.g. a
    company that left and later rejoined). Stored alongside the snapshot
    data for use in checkpoint 2 (CIK resolution).

For each year from START_YEAR through the current year, this pulls the
snapshot closest to (on or before) July 1 of that year — a mid-year anchor
that represents "who was a member during this fiscal year" without
favoring the January or December edge of the year.

Every run re-fetches both source files (they're small) rather than
trusting a cache-if-exists check — the prior build's version of this
script cached forever and silently kept serving a 2019 snapshot for six
years. Raw bytes are still saved to data/raw/ for offline debugging, but
they're overwritten every run, not read-if-present.
"""
import csv
import datetime
import hashlib
import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect, record_meta

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"

COMPONENTS_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
STINTS_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "sp500_ticker_start_end.csv"
)

START_YEAR = 2006
END_YEAR = datetime.date.today().year


def fetch(url: str, cache_name: str) -> str:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    (RAW_DIR / cache_name).write_text(resp.text)
    return resp.text


def parse_snapshots(csv_text: str):
    reader = csv.DictReader(csv_text.splitlines())
    snapshots = []
    for row in reader:
        d = datetime.date.fromisoformat(row["date"])
        tickers = [t.strip() for t in row["tickers"].split(",") if t.strip()]
        snapshots.append((d, tickers))
    snapshots.sort(key=lambda x: x[0])
    return snapshots


def snapshot_for_year(snapshots, year: int):
    target = datetime.date(year, 7, 1)
    candidates = [s for s in snapshots if s[0] <= target]
    if not candidates:
        return snapshots[0]
    return candidates[-1]


def validate(rows: list[dict]) -> None:
    """Pitfall #1 guard: fail loudly if this looks like a frozen snapshot."""
    by_year = {}
    for r in rows:
        by_year.setdefault(r["year"], []).append(r["ticker"])

    years = sorted(by_year)
    hashes = {}
    for y in years:
        h = hashlib.sha256(",".join(sorted(by_year[y])).encode()).hexdigest()
        hashes[y] = h

    frozen_pairs = []
    for a, b in zip(years, years[1:]):
        if hashes[a] == hashes[b]:
            frozen_pairs.append((a, b))

    counts = {y: len(by_year[y]) for y in years}
    print("Year-by-year constituent counts:")
    for y in years:
        print(f"  {y}: {counts[y]} tickers (snapshot hash {hashes[y][:10]}...)")

    if frozen_pairs:
        raise SystemExit(
            f"VALIDATION FAILED: identical ticker sets across adjacent years: "
            f"{frozen_pairs}. This is the exact frozen-cache failure mode from "
            f"the prior build (pitfall #1) — investigate before proceeding."
        )

    count_values = list(counts.values())
    if max(count_values) - min(count_values) > 60:
        print(
            f"WARNING: constituent count swings from {min(count_values)} to "
            f"{max(count_values)} across the study window — worth a manual look, "
            f"though the S&P 500 has genuinely resized over time (400 pre-1957 "
            f"is out of scope here, but methodology changes could explain jumps)."
        )

    print("Validation passed: no adjacent years share an identical snapshot.")


def main():
    components_text = fetch(COMPONENTS_URL, "sp500_historical_components.csv")
    stints_text = fetch(STINTS_URL, "sp500_ticker_start_end.csv")

    snapshots = parse_snapshots(components_text)

    rows = []
    for year in range(START_YEAR, END_YEAR + 1):
        snap_date, tickers = snapshot_for_year(snapshots, year)
        seen = set()
        for raw_ticker in tickers:
            if raw_ticker in seen:
                continue
            seen.add(raw_ticker)
            rows.append({"year": year, "snapshot_date": snap_date, "ticker": raw_ticker})

    validate(rows)

    con = connect()
    con.execute("DROP TABLE IF EXISTS sp500_universe_raw")
    con.execute("""
        CREATE TABLE sp500_universe_raw (
            year INTEGER,
            snapshot_date DATE,
            ticker VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO sp500_universe_raw VALUES (?, ?, ?)",
        [(r["year"], r["snapshot_date"], r["ticker"]) for r in rows],
    )
    record_meta(
        con,
        "sp500_universe_raw",
        script="00_fetch_sp500_history.py",
        source_urls=[COMPONENTS_URL],
        column_descriptions={
            "year": "Study year (2006-present); one row per (year, ticker) that was an S&P 500 constituent",
            "snapshot_date": "Actual source snapshot date used for this year (closest on/before July 1 of `year`)",
            "ticker": "Raw ticker symbol as it appears in the source constituent list for that snapshot date",
        },
        row_count=len(rows),
    )

    # Ticker membership stints — used in checkpoint 2 to disambiguate reused
    # tickers by cross-referencing against SEC CIK formerNames date ranges.
    stint_reader = csv.DictReader(stints_text.splitlines())
    stint_rows = []
    for row in stint_reader:
        start = datetime.date.fromisoformat(row["start_date"]) if row["start_date"] else None
        end = datetime.date.fromisoformat(row["end_date"]) if row["end_date"] else None
        stint_rows.append((row["ticker"], start, end))

    con.execute("DROP TABLE IF EXISTS sp500_ticker_stints")
    con.execute("""
        CREATE TABLE sp500_ticker_stints (
            ticker VARCHAR,
            start_date DATE,
            end_date DATE
        )
    """)
    con.executemany("INSERT INTO sp500_ticker_stints VALUES (?, ?, ?)", stint_rows)
    record_meta(
        con,
        "sp500_ticker_stints",
        script="00_fetch_sp500_history.py",
        source_urls=[STINTS_URL],
        column_descriptions={
            "ticker": "Ticker symbol",
            "start_date": "Date this ticker's S&P 500 membership stint began",
            "end_date": "Date this stint ended (NULL = still a current member)",
        },
        row_count=len(stint_rows),
    )
    con.close()

    print(f"\nWrote {len(rows)} (year, ticker) rows to sp500_universe_raw.")
    print(f"Wrote {len(stint_rows)} ticker membership stints to sp500_ticker_stints.")


if __name__ == "__main__":
    main()
