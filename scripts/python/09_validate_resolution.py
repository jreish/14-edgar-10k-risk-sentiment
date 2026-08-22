#!/usr/bin/env python3
"""Post-pipeline invariants that would otherwise fail silently.

Coverage counts cannot detect a wrong attribution -- a row pointing at the
wrong company's 10-K looks exactly like a correct one, and inflates the
totals rather than depressing them. These checks exist because every bug
found by auditing this dataset has been of that shape.

Each check prints FAIL and sets a non-zero exit code; none of them are
advisory. Run after 05_build_missingness.py.
"""
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect
from lib.risk_factor_parser import MIN_PLAUSIBLE_SECTION_CHARS

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVERRIDES = ROOT / "data" / "manual_cik_overrides.csv"


# One CIK legitimately carries two dissimilar tickers when the COMPANY was
# renamed and the index backfilled the new ticker over earlier years. Both
# tickers then point at the same, correct filings -- the row is duplicated,
# not misattributed. Listed explicitly so the check stays sharp: anything new
# that collides still fails.
SAME_COMPANY_RENAMES = {
    frozenset({"CPRI", "KORS"}),  # Michael Kors Holdings renamed Capri Holdings, 2018
}


def common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def main():
    con = connect()
    failures = []

    # 1. Two DIFFERENT companies' tickers resolving to one CIK+year.
    #
    # Dual-class listings legitimately share a CIK (GOOG/GOOGL, FOX/FOXA,
    # DISCA/DISCK, NWS/NWSA, UA/UAA) and always share a ticker prefix. Two
    # tickers with no common prefix on one filing means one of them is
    # attributed to the wrong company's document. That is how Merck's
    # 2006-2009 rows were found sitting on Schering-Plough's filings (CIK
    # 310158 was renamed Merck after the 2009 reverse merger), and how SRCL
    # 2015 ended up on Cintas's 10-K.
    rows = con.execute("""
        SELECT cik, year, string_agg(DISTINCT ticker, ',') AS tickers
        FROM filing_universe GROUP BY 1, 2 HAVING count(DISTINCT ticker) > 1
    """).fetchall()
    collisions = []
    for cik, year, tickers in rows:
        parts = sorted(t.strip() for t in tickers.split(","))
        if frozenset(parts) in SAME_COMPANY_RENAMES:
            continue
        worst = min(common_prefix(a, b) for a in parts for b in parts if a != b)
        if worst < 2:
            collisions.append((cik, year, tickers))
    print(f"[1] shared CIK+year across unrelated tickers: {len(collisions)} "
          f"(of {len(rows)} shared-CIK pairs; the rest are dual-class)")
    for cik, year, tickers in collisions[:20]:
        print(f"      cik={cik} {year} tickers={tickers}")
    if collisions:
        failures.append(f"{len(collisions)} unrelated-ticker CIK collisions")

    # 2. Override ranges must not overlap: find_override returns the FIRST
    #    match, so an overlap makes resolution silently order-dependent.
    if OVERRIDES.exists():
        import csv
        by_ticker = collections.defaultdict(list)
        with OVERRIDES.open(newline="") as fh:
            for r in csv.DictReader(fh):
                by_ticker[r["ticker"]].append((int(r["year_start"]), int(r["year_end"]), r["cik"]))
        overlaps = []
        for ticker, spans in by_ticker.items():
            spans.sort()
            for (a1, b1, c1), (a2, b2, c2) in zip(spans, spans[1:]):
                if a2 <= b1:
                    overlaps.append((ticker, (a1, b1, c1), (a2, b2, c2)))
        print(f"[2] overlapping override ranges: {len(overlaps)}")
        for o in overlaps[:10]:
            print(f"      {o}")
        if overlaps:
            failures.append(f"{len(overlaps)} overlapping override ranges")

    # 3. No stored section may sit below the plausibility floor -- those are
    #    incorporation-by-reference stubs or truncations, not risk factors.
    short = con.execute(
        "SELECT count(*) FROM risk_factors_index WHERE has_item_1a AND char_count < ?",
        [MIN_PLAUSIBLE_SECTION_CHARS]).fetchone()[0]
    print(f"[3] stored sections below the {MIN_PLAUSIBLE_SECTION_CHARS}-char floor: {short}")
    if short:
        failures.append(f"{short} sections below the plausibility floor")

    # 4. Every universe row must land in exactly one bucket (pitfall #4).
    universe = con.execute("SELECT count(*) FROM sp500_universe_raw").fetchone()[0]
    accounted = con.execute("SELECT count(*) FROM missing_records").fetchone()[0]
    print(f"[4] universe rows {universe} vs missing_records rows {accounted}")
    if universe != accounted:
        failures.append(f"missingness does not account for every universe row "
                        f"({universe} vs {accounted})")

    # 5. Two tickers must never share an extracted text file.
    dupe_files = con.execute("""
        SELECT count(*) FROM (
            SELECT file_path FROM risk_factors_index
            WHERE file_path IS NOT NULL GROUP BY file_path HAVING count(*) > 1)
    """).fetchone()[0]
    print(f"[5] extracted files claimed by more than one row: {dupe_files}")
    if dupe_files:
        failures.append(f"{dupe_files} shared extraction files")

    con.close()
    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS - all resolution invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
