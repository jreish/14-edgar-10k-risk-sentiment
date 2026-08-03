#!/usr/bin/env python3
"""Classify every resolved CIK into a sector via its SEC-reported SIC code.

SIC codes are fetched fresh from submissions.json per CIK (small, ~550
unique CIKs -- cheap to always refetch rather than trust a cache, same
reasoning as checkpoint 1). Classification uses lib/sectors.py's broad
SIC-major-group mapping, built to cover more codes than currently observed
in this dataset (pitfall #6), not just the ones that show up today.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect, record_meta
from lib.edgar import fetch_submissions
from lib.sectors import classify_company

def main():
    con = connect()
    ciks = [r[0] for r in con.execute(
        "SELECT DISTINCT cik FROM cik_resolution WHERE resolution_status = 'resolved'"
    ).fetchall()]
    print(f"Classifying {len(ciks)} unique resolved CIKs...")

    results = []
    unclassified = []
    for i, cik in enumerate(ciks):
        subs = fetch_submissions(cik)
        sic = subs.get("sic") if subs else None
        sic_desc = subs.get("sicDescription") if subs else None
        name = subs.get("name") if subs else None
        sector = classify_company(cik, sic)
        if sector == "Unclassified":
            unclassified.append((cik, sic, sic_desc, name))
        results.append((cik, name, sic, sic_desc, sector))
        if i % 100 == 0:
            print(f"  ...{i}/{len(ciks)}")

    con.execute("DROP TABLE IF EXISTS company_sectors")
    con.execute("""
        CREATE TABLE company_sectors (
            cik BIGINT,
            company_name VARCHAR,
            sic VARCHAR,
            sic_description VARCHAR,
            sector VARCHAR
        )
    """)
    con.executemany("INSERT INTO company_sectors VALUES (?, ?, ?, ?, ?)", results)

    record_meta(
        con, "company_sectors", script="04_fetch_sectors.py",
        source_urls=["https://data.sec.gov/submissions/"],
        column_descriptions={
            "cik": "CIK", "company_name": "Company name per SEC submissions.json",
            "sic": "4-digit SIC code as reported by SEC",
            "sic_description": "SEC's text description of the SIC code",
            "sector": "GICS-style sector classified from the SIC code via lib/sectors.py's broad range mapping",
        },
        row_count=len(results),
    )
    con.execute("DROP TABLE IF EXISTS _cik_sic_raw")  # scratch table from investigation, no longer needed
    con.close()

    print(f"\nClassified {len(results)} companies. Sector breakdown:")
    from collections import Counter
    for sector, count in Counter(r[4] for r in results).most_common():
        print(f"  {sector}: {count}")
    if unclassified:
        print(f"\n{len(unclassified)} unclassified (SIC codes with no mapping rule):")
        for row in unclassified:
            print(f"  {row}")


if __name__ == "__main__":
    main()
