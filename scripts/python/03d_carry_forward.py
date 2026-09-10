#!/usr/bin/env python3
"""Fill a year that has no 10-K with the most recent 10-K filed before it ended.

02_build_filing_universe keys on FILING DATE, deliberately: "the 10-K for
year Y" is the one filed during Y, because the question is how companies
characterized risk DURING Y, and that wants the most recent disclosure that
was actually public at the time.

Leaving a year blank when the company filed nothing that year is a deviation
from that framing rather than an expression of it. Throughout 2006, Affiliated
Computer Services HAD a live, public risk disclosure -- the 10-K it filed in
September 2005. It just did not file a new one, having delayed into 2007
during the stock-option backdating investigations. The framing says that
September 2005 document is the 2006 observation; the implementation said
"missing".

So: for a missing year, carry forward the most recent original 10-K filed on
or before 31 December of that year. Every such row is stamped
source_location = 'carried_forward' so any analysis can drop them with a
WHERE clause -- necessary, because the same text then serves two years and a
word-count or sentiment series that treats them as independent observations
would be double-counting.

Bounded to 24 months. Without a bound the rule degenerates: Compuware's 2008
row reached back to a 10-K filed in 1996, which is not "the company's live
disclosure" in any useful sense -- it means the CIK stopped filing, or (as
here) was recycled to a different company entirely. A carry longer than two
years is evidence the row's problem is identification, not timing.

Deliberately NOT applied to not_yet_due. Those are pending filings in the
current year, and filling them with last year's document would quietly
complete an incomplete year -- the exact thing the tariffs chart's hatched
cap exists to stop a reader doing by eye.

What this does NOT recover: the late filing itself. ACS's FY2006 report,
filed 2007-01-23, still enters no year -- 2007 already holds its on-time
FY2007 filing, which is the more recent disclosure as of 2007. Those filings
are recorded in late_filings_not_ingested so the fact survives; reaching
their text would require re-keying the project on period-of-report.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import amend_meta, connect, record_meta
from lib.edgar import get, get_json, filing_doc_url
from lib.risk_factor_parser import extract_item_1a_from_html

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "clean" / "risk_factors"
ORIGINAL_FORMS = {"10-K", "10-K405", "10-KSB", "10-KSB405"}
ELIGIBLE = ("past_due_not_filed", "filed_late_not_ingested", "will_not_file")
MAX_CARRY_MONTHS = 24


def all_originals(cik: int) -> list[dict]:
    data = get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    blocks = [data.get("filings", {}).get("recent", {})]
    for meta in data.get("filings", {}).get("files", []):
        try:
            blocks.append(get_json(f"https://data.sec.gov/submissions/{meta['name']}"))
        except Exception:
            continue
    out = []
    for blk in blocks:
        out += [
            {"form": f, "filingDate": d, "accessionNumber": a, "primaryDocument": p}
            for f, d, a, p in zip(blk.get("form", []), blk.get("filingDate", []),
                                  blk.get("accessionNumber", []), blk.get("primaryDocument", []))
            if f in ORIGINAL_FORMS
        ]
    return sorted(out, key=lambda f: f["filingDate"])


def record_provenance(con, late_count: int) -> None:
    """Stamp _meta for the tables this script writes.

    This script appends to two tables it does not own, so their _meta
    row_count is whatever 02 and 03 stamped before these rows existed -- a
    provenance table that undercounts is worse than no provenance table,
    because it reads as authoritative. amend_meta recounts from the tables
    and keeps 02's and 03's descriptions rather than overwriting them.
    """
    amend_meta(
        con, "filing_universe", "03d_carry_forward.py",
        source_urls=["https://data.sec.gov/submissions/"],
        column_updates={
            "filing_date": "NOT necessarily within the study year: rows added by 03d carry "
                           "forward the most recent 10-K filed on or before 31 Dec of that year "
                           "(bounded to 24 months), so filing_date may fall in a prior year. "
                           "Join risk_factors_index on (year, ticker) and check "
                           "source_location = 'carried_forward' to identify them.",
        },
    )
    amend_meta(
        con, "risk_factors_index", "03d_carry_forward.py",
        source_urls=["https://www.sec.gov/Archives/edgar/data/"],
        column_updates={
            "source_location": "carried_forward = the year had no 10-K of its own and this is the "
                               "most recent one filed before it ended (03d); the same text also "
                               "serves the year it was filed in, so any series treating "
                               "company-years as independent observations must exclude these.",
        },
    )
    record_meta(
        con, "late_filings_not_ingested",
        script="03d_carry_forward.py",
        source_urls=["https://data.sec.gov/submissions/"],
        column_descriptions={
            "year": "Study year the filing belongs to but does not enter",
            "ticker": "Ticker",
            "cik": "Resolved CIK",
            "filing_date": "Date the late 10-K was actually submitted to EDGAR",
            "accession_number": "SEC accession number, so the filing can be retrieved",
        },
        row_count=late_count,
    )


def main():
    con = connect()
    rows = con.execute(f"""
        SELECT year, ticker, cik, sub_reason FROM missing_records
        WHERE reason = 'no_filing_found' AND sub_reason IN {ELIGIBLE}
        ORDER BY year, ticker
    """).fetchall()
    print(f"Carry-forward candidates: {len(rows)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    filled, no_prior, no_text, late_records = 0, [], [], []

    for year, ticker, cik, sub_reason in rows:
        try:
            originals = all_originals(cik)
        except Exception:
            no_prior.append((ticker, year, "submissions fetch failed"))
            continue

        if sub_reason == "filed_late_not_ingested":
            for f in originals:
                if f"{year}-01-01" <= f["filingDate"] <= f"{year + 3}-01-01":
                    late_records.append((year, ticker, cik, f["filingDate"], f["accessionNumber"]))
                    break

        earliest = f"{year - MAX_CARRY_MONTHS // 12}-01-01"
        prior = [f for f in originals if earliest <= f["filingDate"] <= f"{year}-12-31"]
        if not prior:
            no_prior.append((ticker, year, "no 10-K within 24 months before this year"))
            continue
        filing = prior[-1]

        url = filing_doc_url(cik, filing["accessionNumber"], filing["primaryDocument"])
        try:
            resp = get(url)
            text = extract_item_1a_from_html(resp.text) if resp.status_code == 200 else None
        except Exception:
            text = None
        if not text:
            no_text.append((ticker, year, filing["filingDate"]))
            continue

        out_path = OUT_DIR / f"{ticker}_{year}.txt"
        out_path.write_text(text)
        con.execute(
            "INSERT INTO filing_universe VALUES (?,?,?,?,?,?,?)",
            [year, ticker, cik, filing["form"], filing["filingDate"],
             filing["accessionNumber"], filing["primaryDocument"]],
        )
        con.execute("""
            INSERT INTO risk_factors_index
            (year, ticker, cik, form, filing_date, accession_number, primary_document,
             has_item_1a, file_path, char_count, fetch_error, source_location, source_document)
            VALUES (?,?,?,?,?,?,?, true, ?, ?, NULL, 'carried_forward', ?)
        """, [year, ticker, cik, filing["form"], filing["filingDate"],
              filing["accessionNumber"], filing["primaryDocument"],
              str(out_path.relative_to(ROOT)), len(text), filing["primaryDocument"]])
        filled += 1
        print(f"  {ticker} {year} <- 10-K filed {filing['filingDate']} ({len(text):,} chars)")

    con.execute("DROP TABLE IF EXISTS late_filings_not_ingested")
    con.execute("""CREATE TABLE late_filings_not_ingested
                   (year INTEGER, ticker VARCHAR, cik BIGINT,
                    filing_date VARCHAR, accession_number VARCHAR)""")
    if late_records:
        con.executemany("INSERT INTO late_filings_not_ingested VALUES (?,?,?,?,?)", late_records)

    record_provenance(con, len(late_records))
    con.close()

    print(f"\nFilled {filled}. No prior filing: {len(no_prior)}. Prior filing but no Item 1A: {len(no_text)}.")
    print(f"Late filings recorded but not ingested: {len(late_records)}")
    for x in no_text:
        print("   no Item 1A in carried filing:", x)


if __name__ == "__main__":
    main()
