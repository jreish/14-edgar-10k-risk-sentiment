#!/usr/bin/env python3
"""Match each resolved (cik, ticker, year) row to an actual 10-K filing.

Convention: "the 10-K for year Y" means the 10-K FILED during calendar
year Y, not the one covering fiscal year Y. For a company with a normal
December fiscal year end, the 10-K filed in year Y covers FY Y-1 -- but
using filing date (not period-of-report) matches this project's framing
of "how did companies characterize risk during year Y," which wants the
company's most recent public risk disclosure as of that year, not a
report that came out a year later covering an already-elapsed period.
This is also what the prior build used (reviewed and endorsed, not
blindly copied) -- see decisions.md.

Only processes rows with a trustworthy resolved CIK (resolution_status =
'resolved' in cik_resolution from checkpoint 2). Rows with no resolved
CIK are NOT re-derived here; checkpoint 6's missingness dataset joins
back against cik_resolution to account for them by absence, per that
checkpoint's documented reason taxonomy ("cik_never_resolved").
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect, record_meta
from lib.edgar import get_json

TEN_K_FORMS = {"10-K", "10-K405", "10-KSB", "10-KSB405"}
TEN_K_AMENDED_FORMS = {"10-K/A", "10-K405/A", "10-KSB/A", "10-KSB405/A"}


def collect_10k_filings(bucket: dict) -> list[dict]:
    forms = bucket.get("form", [])
    dates = bucket.get("filingDate", [])
    accns = bucket.get("accessionNumber", [])
    docs = bucket.get("primaryDocument", [])
    return [
        {"form": form, "filingDate": date, "accessionNumber": accn, "primaryDocument": doc}
        for form, date, accn, doc in zip(forms, dates, accns, docs)
        if form in TEN_K_FORMS or form in TEN_K_AMENDED_FORMS
    ]


def get_all_10k_filings(cik: int) -> list[dict]:
    padded = str(cik).zfill(10)
    data = get_json(f"https://data.sec.gov/submissions/CIK{padded}.json")
    filings = collect_10k_filings(data.get("filings", {}).get("recent", {}))
    for file_meta in data.get("filings", {}).get("files", []):
        try:
            more = get_json(f"https://data.sec.gov/submissions/{file_meta['name']}")
        except Exception:
            continue
        filings.extend(collect_10k_filings(more))
    return filings


def pick_filing_for_year(filings: list[dict], year: int) -> dict | None:
    same_year = [f for f in filings if f["filingDate"].startswith(str(year))]
    originals = [f for f in same_year if f["form"] in TEN_K_FORMS]
    if originals:
        return max(originals, key=lambda f: f["filingDate"])
    amended = [f for f in same_year if f["form"] in TEN_K_AMENDED_FORMS]
    if amended:
        return max(amended, key=lambda f: f["filingDate"])
    return None


def main():
    con = connect()
    resolved_rows = con.execute("""
        SELECT year, ticker, cik FROM cik_resolution
        WHERE resolution_status = 'resolved'
        ORDER BY cik, year
    """).fetchall()
    print(f"Building filing universe for {len(resolved_rows)} resolved rows...")

    unique_ciks = sorted({r[2] for r in resolved_rows})
    filings_by_cik = {}
    for i, cik in enumerate(unique_ciks, 1):
        filings_by_cik[cik] = get_all_10k_filings(cik)
        if i % 100 == 0:
            print(f"  ...fetched filing history for {i}/{len(unique_ciks)} CIKs")

    results = []
    n_matched = 0
    n_no_filing = 0
    for year, ticker, cik in resolved_rows:
        filing = pick_filing_for_year(filings_by_cik[cik], year)
        if filing is None:
            n_no_filing += 1
            continue
        results.append((
            year, ticker, cik, filing["form"], filing["filingDate"],
            filing["accessionNumber"], filing["primaryDocument"],
        ))
        n_matched += 1

    con.execute("DROP TABLE IF EXISTS filing_universe")
    con.execute("""
        CREATE TABLE filing_universe (
            year INTEGER,
            ticker VARCHAR,
            cik BIGINT,
            form VARCHAR,
            filing_date DATE,
            accession_number VARCHAR,
            primary_document VARCHAR
        )
    """)
    con.executemany("INSERT INTO filing_universe VALUES (?, ?, ?, ?, ?, ?, ?)", results)

    record_meta(
        con, "filing_universe", script="02_build_filing_universe.py",
        source_urls=["https://data.sec.gov/submissions/"],
        column_descriptions={
            "year": "Study year (matches cik_resolution.year, resolved rows only)",
            "ticker": "Ticker",
            "cik": "Resolved CIK",
            "form": "10-K form type actually filed (10-K, 10-K405, 10-KSB, or an /A amendment if no original exists for this year)",
            "filing_date": "Date this specific filing was submitted to EDGAR",
            "accession_number": "SEC accession number, used to build the filing's document URLs",
            "primary_document": "Primary document filename within the filing, per EDGAR's index",
        },
        row_count=len(results),
    )
    con.close()

    print(f"\nMatched {n_matched}/{len(resolved_rows)} resolved rows to a 10-K "
          f"({n_no_filing} had a trustworthy CIK but no 10-K filed that calendar year).")


if __name__ == "__main__":
    main()
