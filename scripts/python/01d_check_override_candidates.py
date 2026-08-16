#!/usr/bin/env python3
"""Test proposed (ticker -> CIK) override hypotheses against SEC's own data.

Overrides are the one place in this pipeline where a human proposes an
identity directly, which makes them the one place a confident-sounding wrong
answer could walk straight into the dataset. So proposing and accepting are
split: a hypothesis may come from anywhere (recollection, the worklist's
candidate column, a news article), but nothing reaches
manual_cik_overrides.csv until SEC's filings confirm it.

A hypothesis passes only if the CIK's OWN filings declare the ticker --
lib/cover_page.declared_symbols over every 10-K it filed inside the
ticker's unresolved year range. That is the same evidence standard 01b
applies to search candidates; the only difference is where the candidate
came from.

Reported outcomes:
  CONFIRMED   - a filing in range declares this exact ticker. Safe to add,
                and the script prints the CSV row with its evidence.
  CONTRADICTED- filings in range declare only OTHER symbols. The hypothesis
                is wrong; do not add it.
  NO_EVIDENCE - the CIK filed in range but never printed a symbol (routine
                pre-2019, where SEC did not require one on the cover page).
                Not confirmation. Left for a human with a real source.
  NO_FILINGS  - this CIK filed no 10-K in range at all.

Usage:  01d_check_override_candidates.py TICKER=CIK [TICKER=CIK ...]
"""
import collections
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.cover_page import declared_symbols
from lib.db import connect
from lib.edgar import all_10k_filings, fetch_submissions, filing_doc_url, get

ROOT = pathlib.Path(__file__).resolve().parents[2]
SYMBOL_CACHE_PATH = ROOT / "data" / "raw" / "coverpage_symbols.json"


def norm(s: str) -> str:
    return s.replace(".", "").replace("-", "").upper()


def main():
    hypotheses = []
    for arg in sys.argv[1:]:
        ticker, _, cik = arg.partition("=")
        hypotheses.append((ticker.strip(), int(cik)))
    if not hypotheses:
        print(__doc__)
        return

    con = connect()
    symbol_cache = json.loads(SYMBOL_CACHE_PATH.read_text()) if SYMBOL_CACHE_PATH.exists() else {}

    print(f"{'ticker':8} {'cik':>9}  {'verdict':12} {'company':34} evidence")
    print("-" * 120)
    accepted = []

    for ticker, cik in hypotheses:
        years = [r[0] for r in con.execute(
            "SELECT year FROM cik_resolution WHERE ticker = ? AND resolution_status = 'unresolved' ORDER BY year",
            [ticker]).fetchall()]
        if not years:
            print(f"{ticker:8} {cik:>9}  {'SKIP':12} (nothing unresolved for this ticker)")
            continue
        lo, hi = min(years), max(years)

        subs = fetch_submissions(cik)
        if subs is None:
            print(f"{ticker:8} {cik:>9}  {'BAD_CIK':12} (no SEC submissions record)")
            continue
        name = subs.get("name", "?")

        in_range = [f for f in all_10k_filings(cik)
                    if lo - 1 <= int(f["filing_date"][:4]) <= hi + 1]
        if not in_range:
            print(f"{ticker:8} {cik:>9}  {'NO_FILINGS':12} {name[:34]:34} no 10-K filed {lo}-{hi}")
            continue

        declared_all, evidence = set(), None
        for filing in in_range:
            accn = filing["accession_number"]
            if accn in symbol_cache:
                symbols = set(symbol_cache[accn])
            else:
                resp = get(filing_doc_url(cik, accn, filing["primary_document"]))
                if resp.status_code != 200:
                    continue
                symbols = declared_symbols(resp.content)
                symbol_cache[accn] = sorted(symbols)
            declared_all |= symbols
            if norm(ticker) in {norm(s) for s in symbols} and evidence is None:
                evidence = filing

        if evidence is not None:
            verdict = "CONFIRMED"
            # Bound the range by the years this CIK actually filed 10-Ks, not
            # by the full span of the ticker's missing years. Confirming the
            # IDENTITY does not confirm the DURATION: Baker Hughes Co declares
            # "BHGE" on its cover page, but first filed in 2018, so writing the
            # range as 2006-2018 would hand twelve pre-existence years to it
            # under cover of a real verification note.
            filed_years = sorted({int(f["filing_date"][:4]) for f in in_range})
            year_start, year_end = max(lo, min(filed_years)), min(hi, max(filed_years))
            span_note = ""
            if (year_start, year_end) != (lo, hi):
                span_note = (f" [range narrowed from the {lo}-{hi} gap to {year_start}-{year_end}: "
                             f"this CIK only filed 10-Ks in {min(filed_years)}-{max(filed_years)}]")
            note = (f"{name} filed {len(in_range)} 10-Ks {lo}-{hi}; its {evidence['filing_date']} "
                    f"10-K (accession {evidence['accession_number']}) declares trading symbol "
                    f"{ticker} (verified via lib/cover_page.declared_symbols){span_note}")
            accepted.append((ticker, year_start, year_end, cik, name, note))
        elif declared_all:
            verdict = "CONTRADICTED"
            note = f"declares only {sorted(declared_all)[:6]} across {len(in_range)} filings in range"
        else:
            verdict = "NO_EVIDENCE"
            note = f"{len(in_range)} 10-Ks in range, none prints any trading symbol (normal pre-2019)"

        print(f"{ticker:8} {cik:>9}  {verdict:12} {name[:34]:34} {note[:140]}")

    SYMBOL_CACHE_PATH.write_text(json.dumps(symbol_cache))
    con.close()

    if accepted:
        print(f"\n\n{len(accepted)} CONFIRMED -- append these to data/manual_cik_overrides.csv:\n")
        import csv as _csv
        writer = _csv.writer(sys.stdout, quoting=_csv.QUOTE_MINIMAL)
        for ticker, lo, hi, cik, name, note in accepted:
            writer.writerow([ticker, lo, hi, cik,
                             f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json", note])


if __name__ == "__main__":
    main()
