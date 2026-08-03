#!/usr/bin/env python3
"""Download each filing's primary document and extract Item 1A text.

Writes extracted text to data/clean/risk_factors/{ticker}_{year}.txt (flat
files on disk, per the project's storage convention -- DuckDB holds the
manifest with has_item_1a and file_path, not the blob itself). Logs every
filing_universe row into risk_factors_index, whether or not extraction
succeeded, so has_item_1a=false rows stay visible rather than silently
missing.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect, record_meta
from lib.edgar import get
from lib.risk_factor_parser import extract_item_1a_from_html

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "clean" / "risk_factors"


def filing_doc_url(cik: int, accession_number: str, primary_document: str) -> str:
    accn_nodash = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{primary_document}"


def main():
    con = connect()
    rows = con.execute("""
        SELECT year, ticker, cik, form, filing_date, accession_number, primary_document
        FROM filing_universe ORDER BY ticker, year
    """).fetchall()
    print(f"Pulling {len(rows)} filings...")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    n_extracted = 0
    n_fetch_failed = 0
    n_no_item_1a = 0

    for i, (year, ticker, cik, form, filing_date, accn, doc) in enumerate(rows):
        if i % 250 == 0:
            print(f"  ...{i}/{len(rows)} (extracted {n_extracted}, no_item_1a {n_no_item_1a}, fetch_failed {n_fetch_failed})")

        url = filing_doc_url(cik, accn, doc)
        file_path = None
        has_item_1a = False
        char_count = None
        fetch_error = None

        try:
            resp = get(url)
            if resp.status_code != 200:
                fetch_error = f"HTTP {resp.status_code}"
                n_fetch_failed += 1
            else:
                text = extract_item_1a_from_html(resp.text)
                if text:
                    out_path = OUT_DIR / f"{ticker}_{year}.txt"
                    out_path.write_text(text)
                    file_path = str(out_path.relative_to(ROOT))
                    has_item_1a = True
                    char_count = len(text)
                    n_extracted += 1
                else:
                    n_no_item_1a += 1
        except Exception as exc:
            fetch_error = str(exc)[:300]
            n_fetch_failed += 1

        results.append((
            year, ticker, cik, form, filing_date, accn, doc,
            has_item_1a, file_path, char_count, fetch_error,
        ))

    con.execute("DROP TABLE IF EXISTS risk_factors_index")
    con.execute("""
        CREATE TABLE risk_factors_index (
            year INTEGER,
            ticker VARCHAR,
            cik BIGINT,
            form VARCHAR,
            filing_date DATE,
            accession_number VARCHAR,
            primary_document VARCHAR,
            has_item_1a BOOLEAN,
            file_path VARCHAR,
            char_count INTEGER,
            fetch_error VARCHAR
        )
    """)
    con.executemany("INSERT INTO risk_factors_index VALUES (?,?,?,?,?,?,?,?,?,?,?)", results)

    record_meta(
        con, "risk_factors_index", script="03_pull_filings.py",
        source_urls=["https://www.sec.gov/Archives/edgar/data/"],
        column_descriptions={
            "year": "Study year", "ticker": "Ticker", "cik": "CIK",
            "form": "10-K form type", "filing_date": "Filing date",
            "accession_number": "SEC accession number", "primary_document": "Primary document filename",
            "has_item_1a": "TRUE if Item 1A text was successfully extracted",
            "file_path": "Path (relative to project root) to the extracted .txt file, or NULL",
            "char_count": "Character count of extracted text, or NULL",
            "fetch_error": "HTTP/network error if the filing document itself could not be fetched, else NULL",
        },
        row_count=len(results),
    )
    con.close()

    n_total = len(results)
    print(f"\nDone: {n_extracted}/{n_total} extracted ({n_extracted/n_total:.1%}), "
          f"{n_no_item_1a} fetched but no Item 1A found, {n_fetch_failed} fetch failures.")


if __name__ == "__main__":
    main()
