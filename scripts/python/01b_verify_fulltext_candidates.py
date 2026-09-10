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
from lib.cover_page import declared_symbols
from lib.db import amend_meta, connect
from lib.edgar import SearchUnavailable, all_10k_filings, filing_doc_url, full_text_search, get

CANDIDATE_RE = re.compile(r"found candidate CIK (\d+)")
MATCH_WINDOW = datetime.timedelta(days=400)

# Same narrow window 01 uses for its fallback search: wide windows surface
# unrelated documents as top hits purely because there is more noise to
# outscore the real filing.
SEARCH_BEFORE = datetime.timedelta(days=200)
SEARCH_AFTER = datetime.timedelta(days=400)
# How many ranked hits per query to treat as candidates. 01 only ever looked
# at the single top hit, which is what capped it at ~70%: the right company
# is frequently present but ranked second or third behind a filing that
# merely mentions the ticker more often. Verification does not care about
# rank, so widening the candidate set costs nothing in accuracy -- an
# unverifiable extra candidate is simply rejected -- and buys real recall.
TOP_N = 3

ROOT = pathlib.Path(__file__).resolve().parents[2]
# Declared symbols keyed by accession number, persisted so re-runs (and the
# seed-an-override / re-run loop) don't re-download filings. Keyed by
# accession alone, NOT by ticker, so one fetch serves every ticker and year
# that happens to land on the same filing.
SYMBOL_CACHE_PATH = ROOT / "data" / "raw" / "coverpage_symbols.json"


def load_symbol_cache() -> dict[str, list[str]]:
    if SYMBOL_CACHE_PATH.exists():
        try:
            return json.loads(SYMBOL_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_symbol_cache(cache: dict) -> None:
    SYMBOL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SYMBOL_CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(SYMBOL_CACHE_PATH)


def pick_10k_near(filings: list[dict], snapshot: datetime.date) -> dict | None:
    """The 10-K closest to this year's snapshot, within the match window."""
    best, best_gap = None, None
    for f in filings:
        gap = abs((datetime.date.fromisoformat(f["filing_date"]) - snapshot).days)
        if gap <= MATCH_WINDOW.days and (best_gap is None or gap < best_gap):
            best, best_gap = f, gap
    return best


def candidate_ciks(ticker: str, snapshot: datetime.date, today: datetime.date,
                   stashed: int | None) -> list[int]:
    """Every plausible CIK for this (ticker, year), best-ranked first."""
    found: list[int] = []
    if stashed is not None:
        found.append(stashed)
    window_start = snapshot - SEARCH_BEFORE
    window_end = min(snapshot + SEARCH_AFTER, today)
    for query in (f'"{ticker}"', f'symbol "{ticker}"'):
        try:
            hits = full_text_search(query, "10-K", window_start.isoformat(), window_end.isoformat())
        except SearchUnavailable:
            continue
        for hit in hits[:TOP_N]:
            for raw in hit.get("_source", {}).get("ciks", []):
                cik = int(raw)
                if cik not in found:
                    found.append(cik)
    return found


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
    today = datetime.date.today()
    symbol_cache = load_symbol_cache()
    print(f"  symbol cache warm with {len(symbol_cache)} filings.\n")
    counts = collections.Counter()

    def symbols_for(cik: int, filing: dict) -> set[str] | None:
        """Declared symbols for one filing, memoized on disk by accession.
        None means the document could not be fetched."""
        accn = filing["accession_number"]
        if accn in symbol_cache:
            return set(symbol_cache[accn])
        resp = get(filing_doc_url(cik, accn, filing["primary_document"]))
        if resp.status_code != 200:
            return None
        symbols = declared_symbols(resp.content)
        symbol_cache[accn] = sorted(symbols)
        return symbols

    norm = lambda s: s.replace(".", "").replace("-", "").upper()

    for i, (year, ticker, snapshot, stint_start, detail) in enumerate(rows):
        if i % 100 == 0:
            print(f"  pass1 {i}/{len(rows)} ({dict(counts)})")
            save_symbol_cache(symbol_cache)

        match = CANDIDATE_RE.search(detail or "")
        stashed = int(match.group(1)) if match else None
        candidates = candidate_ciks(ticker, snapshot, today, stashed)
        if not candidates:
            counts["no_candidate"] += 1
            continue

        # Verify EVERY candidate, not just the best-ranked one. Ranking is the
        # unreliable step; this asks each candidate the question it can answer
        # definitively -- "do you claim this ticker?" -- and lets the evidence
        # pick the winner instead of the search engine's score.
        confirmed, contradicted_any = [], False
        for cand in candidates:
            if cand not in filings_cache:
                filings_cache[cand] = all_10k_filings(cand)
            filing = pick_10k_near(filings_cache[cand], snapshot)
            if filing is None:
                continue
            symbols = symbols_for(cand, filing)
            if symbols is None:
                continue
            if not symbols:
                continue  # filing declares no symbol at all -- no evidence either way
            if norm(ticker) in {norm(s) for s in symbols}:
                confirmed.append((cand, filing))
            else:
                contradicted_any = True

        if len(confirmed) == 1:
            cand, filing = confirmed[0]
            counts["verified"] += 1
            verdicts[(year, ticker)] = "verified"
            verified_by_stint[(ticker, stint_start)].add(cand)
            updates[(year, ticker)] = (cand, "fulltext_coverpage_verified", (
                f"CIK {cand} CONFIRMED out of {len(candidates)} search candidates: its "
                f"{filing['filing_date']} 10-K declares trading symbol {ticker}"
            ))
        elif len(confirmed) > 1:
            # Two filings both claiming the ticker in the same year. Real, and
            # exactly the case that must not be guessed at (a mid-year
            # succession, or a genuine dual listing).
            counts["ambiguous_multiple_verified"] += 1
            verdicts[(year, ticker)] = (
                f"ambiguous_multiple_verified ({','.join(str(c) for c, _ in confirmed)})")
        elif contradicted_any:
            counts["contradicted"] += 1
            verdicts[(year, ticker)] = "contradicted"
        else:
            counts["no_evidence"] += 1
            verdicts[(year, ticker)] = "no_evidence"

    save_symbol_cache(symbol_cache)
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

    # This script amends cik_resolution rather than producing it, so it must
    # not call record_meta directly -- that is DELETE-then-INSERT and would
    # throw away 01's provenance for the same table. amend_meta merges.
    amend_meta(
        con, "cik_resolution", "01b_verify_fulltext_candidates.py",
        source_urls=["https://www.sec.gov/Archives/edgar/data/"],
        column_updates={
            "resolution_method":
                "| added by 01b: fulltext_coverpage_verified (the candidate's own 10-K declares this "
                "ticker on its cover page) / coverpage_verified_propagated (single cover-page-verified "
                "CIK carried across the rest of its membership stint)",
        },
    )
    con.close()
    print(f"\ncik_resolution now {resolved}/{total} resolved ({resolved/total:.1%}).")


if __name__ == "__main__":
    main()
