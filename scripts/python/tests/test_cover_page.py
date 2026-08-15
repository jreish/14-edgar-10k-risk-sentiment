#!/usr/bin/env python3
"""Known-truth checks for lib/cover_page.py, run against live SEC filings.

These are the cases that motivated the module, pinned so a future regex
"improvement" can't quietly break them. Hits the network, like everything
else in this project; run it directly:

    ./.venv/bin/python scripts/python/tests/test_cover_page.py

The two cases that matter most are the TER pair. Checkpoint 2's manual audit
found full text search returning Trump Entertainment Resorts when searching
for Teradyne's ticker, and no amount of search tuning fixes that: Trump
Entertainment's 10-K abbreviates the registrant itself as "TER" throughout
("TER Common Stock traded on the Nasdaq Global Market..."), so the document
genuinely is dense with the string. Verification separates them instantly --
Trump Entertainment declares TRMP/TRMPQ as its symbols and never claims TER.

The Teradyne 2010 case pins the opposite guarantee: the RIGHT company, in a
pre-2019 filing, gets "no_evidence" rather than a guess. Teradyne's own 2010
10-K does not contain the string "TER" even once, because the cover-page
trading-symbol column was not required until SEC's 2019 rule change. Silence
must stay silence.
"""
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib.cover_page import verify_ticker
from lib.edgar import all_10k_filings, fetch_submissions, filing_doc_url, get

# (ticker, year, cik, expected_verdict, why)
CASES = [
    ("TER", 2010, 943320, "contradicted",
     "Trump Entertainment Resorts: WRONG company, says 'TER' constantly, declares TRMP/TRMPQ"),
    ("TER", 2010, 97210, "no_evidence",
     "Teradyne: RIGHT company, pre-2019 filing never prints its own ticker"),
    ("TER", 2020, 97210, "verified",
     "Teradyne: post-2019 cover-page Trading Symbol column"),
    ("PLL", 2014, 75829, "verified",
     "Pall Corp: pre-2019 but states its symbol in prose"),
    ("ABC", 2019, 892222, "contradicted",
     "Craft Brew Alliance (declares BREW): a real stashed full-text candidate for ABC, and wrong"),
    ("LB", 2016, 701985, "no_evidence",
     "Bath & Body Works (ex-L Brands): right company, filing states no symbol"),
    ("XOM", 2025, 34088, "verified",
     "Exxon Mobil Corp: the true filer behind XOM, vs. the 2026 holdco company_tickers.json points to"),
]


def main() -> int:
    failures = 0
    for ticker, year, cik, expected, why in CASES:
        subs = fetch_submissions(cik)
        name = (subs or {}).get("name", "?")
        filings = [f for f in all_10k_filings(cik) if f["filing_date"].startswith(str(year))]
        if not filings:
            print(f"SKIP {ticker}/{year} {name}: no 10-K filed that year")
            continue
        filing = filings[0]
        resp = get(filing_doc_url(cik, filing["accession_number"], filing["primary_document"]))
        if resp.status_code != 200:
            print(f"SKIP {ticker}/{year} {name}: HTTP {resp.status_code}")
            continue

        verdict, declared = verify_ticker(resp.content, ticker)
        ok = verdict == expected
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'} {ticker}/{year} {name[:30]:30} -> {verdict:13} "
              f"(expected {expected:13}) declared={sorted(declared)[:6]}")
        if not ok:
            print(f"       {why}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
