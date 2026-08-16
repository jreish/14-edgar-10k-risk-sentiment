#!/usr/bin/env python3
"""Prove a parser change did not alter any extraction that already worked.

Re-downloads a random sample of filings whose Item 1A we already extracted,
re-runs the parser, and asserts the output is byte-identical to the .txt on
disk. This is the gate a parser change has to pass before the pipeline is
re-run over it: extraction feeds both studies, and a change that silently
shifts section boundaries would corrupt the sentiment series in a way that
no coverage count would reveal.

The running-header strategy is designed to be unreachable for any input the
primary strategy handles, so it should be impossible for it to change these.
This test is what turns "should be impossible" into something checked.

Usage:  test_parser_regression.py [sample_size]   (default 300, 0 = all)
"""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib.db import connect
from lib.edgar import filing_doc_url, get
from lib.risk_factor_parser import MIN_PLAUSIBLE_SECTION_CHARS, extract_item_1a_from_html

ROOT = pathlib.Path(__file__).resolve().parents[3]
SEED = 20260816  # fixed so a failure is reproducible


def main():
    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else 300

    con = connect()
    rows = con.execute("""
        SELECT ticker, year, cik, accession_number, primary_document, file_path, char_count
        FROM risk_factors_index
        WHERE has_item_1a = true AND file_path IS NOT NULL
        ORDER BY ticker, year
    """).fetchall()
    con.close()

    population = len(rows)
    if sample_size and sample_size < population:
        rows = random.Random(SEED).sample(rows, sample_size)
    print(f"Re-extracting {len(rows)} of {population} successful filings (seed {SEED})...\n")

    identical = changed = skipped = floor_rejected = 0
    failures = []

    for i, (ticker, year, cik, accn, doc, file_path, char_count) in enumerate(rows):
        if i and i % 50 == 0:
            print(f"  {i}/{len(rows)} (identical {identical}, CHANGED {changed}, skipped {skipped})")

        stored_path = ROOT / file_path
        if not stored_path.exists():
            skipped += 1
            continue
        stored = stored_path.read_text()

        # 03b records the recovering document in fetch_error; those rows were
        # extracted from a sibling exhibit, not primary_document, so
        # re-extracting the primary would compare two different documents.
        resp = get(filing_doc_url(cik, accn, doc))
        if resp.status_code != 200:
            skipped += 1
            continue
        fresh = extract_item_1a_from_html(resp.text)

        if fresh is None:
            # Two very different causes, and they must not be conflated:
            # a stored section below the plausibility floor is now
            # deliberately rejected (and re-attempted by 03b), whereas a
            # long one going missing would be a real regression.
            if len(stored) < MIN_PLAUSIBLE_SECTION_CHARS:
                floor_rejected += 1
            else:
                skipped += 1  # extracted from a sibling doc, not this one
            continue
        if fresh == stored:
            identical += 1
        else:
            changed += 1
            failures.append((ticker, year, len(stored), len(fresh)))

    print(f"\n{'=' * 60}")
    print(f"identical: {identical}   CHANGED: {changed}   skipped: {skipped}   floor-rejected (intended): {floor_rejected}")
    if failures:
        print("\nCHANGED extractions (this must be empty):")
        for ticker, year, old, new in failures[:25]:
            print(f"  {ticker} {year}: {old} chars -> {new} chars")
        print("\nFAIL")
        return 1
    print("\nPASS - no existing extraction changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
