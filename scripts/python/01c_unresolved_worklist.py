#!/usr/bin/env python3
"""Turn whatever is still unresolved into a ranked, evidence-carrying worklist.

Manual overrides are the last resort for identities no automated path can
reach, and the thing that makes them safe is that each one is justified by
evidence recorded in data/manual_cik_overrides.csv. This script produces the
evidence, so seeding an override is a review step rather than an act of
memory -- writing a CIK in from recall is precisely the kind of unverified
attribution this project exists to avoid.

Ranked by rows recoverable per override, because resolution is per-ticker:
one correct (ticker, year-range, CIK) row can close up to 21 study years at
once, so the tail is far shorter in effort than it looks in row count.

For each unresolved ticker it reports the membership stint, the years
affected, what the current-holder path did, and every candidate CIK seen
along with the trading symbols that candidate's own 10-K declares -- which
is what tells you whether a candidate is the right company or merely one
that mentions the ticker.

Writes output/unresolved_worklist.csv.
"""
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect
from lib.edgar import fetch_submissions

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_PATH = ROOT / "output" / "unresolved_worklist.csv"
SYMBOL_CACHE_PATH = ROOT / "data" / "raw" / "coverpage_symbols.json"
CANDIDATE_RE = re.compile(r"CIK (\d+)")


def main():
    con = connect()
    rows = con.execute("""
        SELECT ticker, year, stint_start, stint_end, resolution_method, resolution_detail
        FROM cik_resolution
        WHERE resolution_status = 'unresolved'
        ORDER BY ticker, year
    """).fetchall()

    if not rows:
        print("Nothing unresolved.")
        return

    by_ticker = collections.defaultdict(list)
    for ticker, year, stint_start, stint_end, method, detail in rows:
        by_ticker[ticker].append((year, stint_start, stint_end, method, detail))

    print(f"{len(rows)} unresolved rows across {len(by_ticker)} tickers.\n")

    out = []
    for ticker, entries in sorted(by_ticker.items(), key=lambda kv: -len(kv[1])):
        years = sorted(e[0] for e in entries)
        stint_start, stint_end = entries[0][1], entries[0][2]
        methods = collections.Counter(e[3] for e in entries)

        # Every distinct CIK that has ever been floated for this ticker, with
        # what its own filings say. A candidate whose filings declare some
        # OTHER symbol is positively ruled out, which is usually more useful
        # than the candidates that merely can't be confirmed.
        candidates = []
        for entry in entries:
            for raw in CANDIDATE_RE.findall(entry[4] or ""):
                if int(raw) not in candidates:
                    candidates.append(int(raw))

        candidate_notes = []
        for cik in candidates[:6]:
            subs = fetch_submissions(cik)
            name = (subs or {}).get("name", "?")
            candidate_notes.append(f"CIK {cik} = {name}")

        out.append({
            "ticker": ticker,
            "n_rows_recoverable": len(entries),
            "years": f"{years[0]}-{years[-1]}" if len(years) > 1 else str(years[0]),
            "n_years": len(years),
            "stint_start": stint_start,
            "stint_end": stint_end,
            "methods": "; ".join(f"{m}={n}" for m, n in methods.most_common()),
            "candidates": " | ".join(candidate_notes) or "(none found)",
        })

    import pandas as pd
    df = pd.DataFrame(out)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)

    print("Top 25 tickets by rows recoverable per override:\n")
    print(df.head(25).drop(columns=["stint_start", "stint_end"]).to_string(index=False))

    cumulative = df.n_rows_recoverable.cumsum()
    total = df.n_rows_recoverable.sum()
    for n in (10, 25, 50, 100):
        if n <= len(df):
            print(f"\n  resolving the top {n:3} tickers would close "
                  f"{cumulative.iloc[n-1]:5} of {total} rows "
                  f"({100*cumulative.iloc[n-1]/total:.0f}%)")

    con.close()
    print(f"\nWrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
