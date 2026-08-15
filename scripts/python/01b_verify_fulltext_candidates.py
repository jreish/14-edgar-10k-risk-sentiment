#!/usr/bin/env python3
"""Promote full text search candidates to resolved -- but only on positive
cover-page evidence, never on search ranking.

Checkpoint 2 deliberately refused to auto-accept full text search results:
measured at ~70% accuracy in a 27-item manual audit, and a wrong company
attribution presented as resolved is the exact failure this rebuild exists
to prevent. Those candidate CIKs were kept in cik_resolution.resolution_detail
for later review rather than discarded. This script is that review, automated.

The insight is that *ranking* ("which filing best matches this ticker
string?") and *verification* ("does this filing claim this ticker?") are
different problems with very different accuracy. See lib/cover_page.py for
the Teradyne/Trump Entertainment case that ranking cannot solve and
verification solves trivially.

Two passes:

  Pass 1 -- direct verification. Fetch the candidate's own 10-K for that
  year and read the symbols it declares. Accept only "verified".

  Pass 2 -- propagation within a stint. Pre-2019 cover pages had no trading
  symbol column (SEC only required it from 2019), so a great many older
  filings never print their own ticker at all -- Teradyne's 2010 10-K
  doesn't contain the string "TER" even once. Those years can never be
  verified directly. But if a ticker verifies to exactly one CIK somewhere
  in its membership stint, and that same CIK filed a 10-K in the target year
  too, the identity carries: it is the same company filing continuously
  under the same symbol. Propagation is refused when the stint has two
  different verified CIKs (a real mid-stint handover, which is precisely the
  case that must not be guessed at) or when the year has its own
  contradicting evidence.

Rows this script cannot verify are left unresolved with the verdict recorded,
so the gap stays visible and auditable instead of being papered over.
"""
import collections
import datetime
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.cover_page import verify_ticker
from lib.db import connect, record_meta
from lib.edgar import all_10k_filings, filing_doc_url, get

CANDIDATE_RE = re.compile(r"found candidate CIK (\d+)")
MATCH_WINDOW = datetime.timedelta(days=400)


def pick_10k_near(filings: list[dict], snapshot: datetime.date) -> dict | None:
    """The 10-K closest to this year's snapshot, within the match window."""
    best, best_gap = None, None
    for f in filings:
        gap = abs((datetime.date.fromisoformat(f["filing_date"]) - snapshot).days)
        if gap <= MATCH_WINDOW.days and (best_gap is None or gap < best_gap):
            best, best_gap = f, gap
    return best


def main():
    con = connect()
    rows = con.execute("""
        SELECT year, ticker, snapshot_date, stint_start, resolution_detail
        FROM cik_resolution
        WHERE resolution_status = 'unresolved'
        ORDER BY ticker, year
    """).fetchall()
    print(f"{len(rows)} unresolved rows to attempt.")

    filings_cache: dict[int, list[dict]] = {}
    verdict_cache: dict[tuple, tuple] = {}  # (cik, accession, ticker) -> (verdict, declared)
    updates: dict[tuple, tuple] = {}  # (year, ticker) -> (cik, method, detail)
    verdicts: dict[tuple, str] = {}  # (year, ticker) -> verdict
    # (ticker, stint_start) -> {cik verified directly}
    verified_by_stint: dict[tuple, set] = collections.defaultdict(set)

    n_with_candidate = sum(1 for r in rows if CANDIDATE_RE.search(r[4] or ""))
    print(f"  {n_with_candidate} of them carry a stashed candidate CIK.\n")

    # ---- Pass 1: direct cover-page verification -------------------------
    counts = collections.Counter()
    for i, (year, ticker, snapshot, stint_start, detail) in enumerate(rows):
        if i % 200 == 0:
            print(f"  pass1 {i}/{len(rows)} ({dict(counts)})")
        match = CANDIDATE_RE.search(detail or "")
        if not match:
            counts["no_candidate"] += 1
            continue
        cand = int(match.group(1))

        if cand not in filings_cache:
            filings_cache[cand] = all_10k_filings(cand)
        filing = pick_10k_near(filings_cache[cand], snapshot)
        if filing is None:
            counts["candidate_has_no_10k_that_year"] += 1
            verdicts[(year, ticker)] = "candidate_has_no_10k_that_year"
            continue

        key = (cand, filing["accession_number"], ticker)
        if key not in verdict_cache:
            resp = get(filing_doc_url(cand, filing["accession_number"], filing["primary_document"]))
            if resp.status_code != 200:
                verdict_cache[key] = ("fetch_failed", set())
            else:
                verdict_cache[key] = verify_ticker(resp.content, ticker)
        verdict, declared = verdict_cache[key]
        counts[verdict] += 1
        verdicts[(year, ticker)] = verdict

        if verdict == "verified":
            verified_by_stint[(ticker, stint_start)].add(cand)
            updates[(year, ticker)] = (cand, "fulltext_coverpage_verified", (
                f"full text search candidate CIK {cand} CONFIRMED: its {filing['filing_date']} 10-K "
                f"declares trading symbol {ticker} on the cover page / in its own text"
            ))
        elif verdict == "contradicted":
            verdicts[(year, ticker)] = "contradicted"

    print(f"\nPass 1 verdicts: {dict(counts)}")
    print(f"Pass 1 resolved {len(updates)} rows.")

    # ---- Pass 2: propagate a verified identity across its stint ----------
    usable = {k: v for k, v in verified_by_stint.items() if len(v) == 1}
    ambiguous = {k: v for k, v in verified_by_stint.items() if len(v) > 1}
    print(f"\n{len(usable)} (ticker, stint) pairs verified to exactly one CIK; "
          f"{len(ambiguous)} ambiguous and refused: {sorted(ambiguous)[:5]}")

    n_prop = 0
    for year, ticker, snapshot, stint_start, detail in rows:
        if (year, ticker) in updates:
            continue
        if verdicts.get((year, ticker)) == "contradicted":
            continue  # this year has its own evidence pointing elsewhere
        anchor = usable.get((ticker, stint_start))
        if not anchor:
            continue
        cik = next(iter(anchor))
        filing = pick_10k_near(filings_cache.get(cik, []), snapshot)
        if filing is None:
            continue  # no 10-K that year -- nothing to attribute
        updates[(year, ticker)] = (cik, "coverpage_verified_propagated", (
            f"CIK {cik} is the single cover-page-verified holder of {ticker} within this "
            f"membership stint (from {stint_start}); it filed a 10-K on {filing['filing_date']}, "
            f"within {MATCH_WINDOW.days}d of this year's snapshot, so the identity carries"
        ))
        n_prop += 1

    print(f"Pass 2 propagated {n_prop} further rows.")

    # ---- Write back -----------------------------------------------------
    for (year, ticker), (cik, method, detail) in updates.items():
        con.execute("""
            UPDATE cik_resolution
            SET cik = ?, resolution_method = ?, resolution_status = 'resolved', resolution_detail = ?
            WHERE year = ? AND ticker = ?
        """, [cik, method, detail, year, ticker])

    # Record the negative verdicts too -- a "contradicted" is a real finding
    # (the stashed candidate is the wrong company) and should not silently
    # look identical to "we never looked".
    for (year, ticker), verdict in verdicts.items():
        if (year, ticker) in updates or verdict == "verified":
            continue
        con.execute("""
            UPDATE cik_resolution
            SET resolution_detail = resolution_detail || ?
            WHERE year = ? AND ticker = ?
        """, [f"; cover-page verification verdict: {verdict}", year, ticker])

    total = con.execute("SELECT count(*) FROM cik_resolution").fetchone()[0]
    resolved = con.execute(
        "SELECT count(*) FROM cik_resolution WHERE resolution_status = 'resolved'").fetchone()[0]

    # record_meta is DELETE-then-INSERT, so writing a bare entry here would
    # throw away 01's provenance for this same table. This script amends
    # cik_resolution rather than producing it, so merge into what 01 recorded.
    prior = con.execute(
        "SELECT script, source_urls, column_descriptions FROM _meta WHERE table_name = 'cik_resolution'"
    ).fetchone()
    prior_script, prior_urls, prior_cols = prior if prior else ("", "[]", "{}")
    merged_cols = json.loads(prior_cols)
    merged_cols["resolution_method"] = (
        merged_cols.get("resolution_method", "")
        + " | added by 01b: fulltext_coverpage_verified (the candidate's own 10-K declares this ticker "
          "on its cover page) / coverpage_verified_propagated (single cover-page-verified CIK carried "
          "across the rest of its membership stint)"
    )
    record_meta(
        con, "cik_resolution",
        script=f"{prior_script} -> 01b_verify_fulltext_candidates.py",
        source_urls=json.loads(prior_urls) + ["https://www.sec.gov/Archives/edgar/data/"],
        column_descriptions=merged_cols,
        row_count=total,
    )
    con.close()
    print(f"\ncik_resolution now {resolved}/{total} resolved ({resolved/total:.1%}).")


if __name__ == "__main__":
    main()
