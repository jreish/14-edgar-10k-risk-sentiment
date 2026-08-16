#!/usr/bin/env python3
"""Retry Item 1A extraction misses by checking other documents in the same
filing accession, not just the primary document.

Investigated a sample of misses (concentrated in a handful of tickers --
CLX, CINF, HAL, USB, C, KDP alone were 65/107): several companies file a
10-K whose primary document is a short "Form 10-K Cross-Reference Index"
(page-number table only, confirmed case: JNJ 2006 -- a 173KB document
containing nothing but a table of contents pointing to page 4 of a
separately-filed exhibit) with the actual Item 1A text living in a
separate exhibit document within the same accession (commonly named like
"..exv13.htm" / "ex13...htm", SEC's conventional numbering for an Annual
Report exhibit incorporated by reference). This is the same underlying
pattern as the McDonald's case the parser already handles, except
McDonald's embeds the annual report text directly in the primary document
while these filers split it into a separate exhibit file.

For each has_item_1a=false row, fetches the filing's index.json (lists
every document in the accession) and tries each document in turn until
one yields a valid extraction.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect, record_meta
from lib.edgar import get, get_json
from lib.risk_factor_parser import extract_item_1a_from_html

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "clean" / "risk_factors"


def other_documents(cik: int, accession_number: str, primary_document: str) -> list[str]:
    """Candidate sibling documents, most-likely first.

    Ordering matters for both cost and correctness. BNY Mellon's 2016
    accession contains 172 documents; walking them in EDGAR's listing order
    means ~170 wasted fetches before reaching the Annual Report, and it takes
    whichever document parses FIRST rather than the one most likely to be
    right -- an early spurious match wins over the real exhibit.

    Two signals, in order:
      1. Exhibit 13 is SEC's conventional number for the Annual Report to
         Shareholders, which is what Item 1A gets incorporated by reference
         INTO. Naming is inconsistent across filing agents (ex13, exv13,
         ex-13, ex_13, and suffixed forms like kex131), so match loosely.
      2. Otherwise largest first. The Annual Report is invariably the biggest
         document in the accession; certifications and consents are tiny.

    The concatenated full-submission text file is excluded outright: it
    contains every document in the accession glued together, so a section
    extracted from it can silently span document boundaries.
    """
    accn_nodash = accession_number.replace("-", "")
    idx_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/index.json"
    try:
        data = get_json(idx_url)
    except Exception:
        return []

    skip_ext = (".jpg", ".gif", ".png", ".xsd", ".xml", ".jpeg")
    full_submission = f"{accession_number}.txt"

    candidates = []
    for item in data.get("directory", {}).get("item", []):
        name = item.get("name", "")
        lowered = name.lower()
        if name in (primary_document, full_submission):
            continue
        if lowered.endswith(skip_ext) or "index" in lowered:
            continue
        if not lowered.endswith((".htm", ".html", ".txt")):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        is_ex13 = bool(re.search(r"ex[\-_v]?13", lowered))
        candidates.append((0 if is_ex13 else 1, -size, name))

    return [name for _, _, name in sorted(candidates)]


def main():
    con = connect()
    misses = con.execute("""
        SELECT year, ticker, cik, accession_number, primary_document
        FROM risk_factors_index WHERE has_item_1a = false
        ORDER BY ticker, year
    """).fetchall()
    print(f"Retrying {len(misses)} misses against other documents in the same filing...")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n_recovered = 0

    for i, (year, ticker, cik, accn, primary_doc) in enumerate(misses):
        candidates = other_documents(cik, accn, primary_doc)
        recovered_text = None
        recovered_doc = None
        for doc_name in candidates:
            accn_nodash = accn.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{doc_name}"
            try:
                resp = get(url)
                if resp.status_code != 200:
                    continue
                text = extract_item_1a_from_html(resp.text)
            except Exception:
                continue
            if text:
                recovered_text = text
                recovered_doc = doc_name
                break

        if recovered_text:
            out_path = OUT_DIR / f"{ticker}_{year}.txt"
            out_path.write_text(recovered_text)
            file_path = str(out_path.relative_to(ROOT))
            con.execute("""
                UPDATE risk_factors_index
                SET has_item_1a = true, file_path = ?, char_count = ?,
                    fetch_error = 'recovered from secondary document: ' || ?
                WHERE year = ? AND ticker = ?
            """, [file_path, len(recovered_text), recovered_doc, year, ticker])
            n_recovered += 1
            print(f"  recovered {ticker} {year} from {recovered_doc} ({len(recovered_text)} chars)")
        else:
            # Record WHY it is still missing rather than leaving the row
            # indistinguishable from one never attempted. "0 siblings" means
            # the accession genuinely has nothing else to try; a non-zero
            # count means the text is not recoverable by any current
            # strategy, which is a parser question, not a fetching one.
            con.execute("""
                UPDATE risk_factors_index SET fetch_error = ?
                WHERE year = ? AND ticker = ?
            """, [f"no Item 1A in primary document or any of {len(candidates)} sibling documents",
                  year, ticker])

    con.close()
    print(f"\nRecovered {n_recovered}/{len(misses)} via secondary-document fallback.")


if __name__ == "__main__":
    main()
