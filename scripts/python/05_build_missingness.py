#!/usr/bin/env python3
"""Build the missingness dataset: every (ticker, year) in the TRUE
historical S&P 500 universe (sp500_universe_raw, checkpoint 1 -- 10,524
rows, nothing dropped) ends up in exactly one of:
  - has_item_1a = true (the success case, risk_factors_index)
  - missing, with exactly one specific reason.

This is the deliverable pitfall #4 exists to protect: project 13's
missingness report undercounted true missingness by ~7x because its
working table only kept rows that had already resolved to a CIK. Starting
from sp500_universe_raw (not cik_resolution, not filing_universe) and
LEFT JOINing everything else is what keeps every unresolved row visible.

Reason taxonomy:
  - cik_never_resolved: this ticker has NO resolved CIK in ANY study year
    (a wholesale resolution failure, not a year-specific gap).
  - year_specific_no_match: this ticker DOES resolve to a trustworthy CIK
    in at least one other year, just not this one (a stint-boundary or
    snapshot-date edge case).
  - cik_candidate_never_filed: CIK resolved for this year, but that CIK
    has ZERO matched 10-Ks across every study year (suggests it's not
    actually a 10-K filer under this identity -- e.g. a foreign private
    issuer filing 20-F instead).
  - no_filing_found: CIK resolved and does file 10-Ks generally, but none
    was found for this specific year. Sub-classified into not_yet_due /
    past_due_not_filed / will_not_file / still_unknown (see below) --
    this sub-classification is what the tariffs-missing chart's hatch box
    draws on, but every sub-reason is independently stored and queryable.
  - no_item_1a_extracted: a 10-K was found and fetched, but Item 1A
    extraction failed on it.

The no_filing_found sub-reason `filed_late_not_ingested` deserves its own
note, because it is the one that was previously saying something untrue.
"the 10-K for year Y" means the 10-K FILED during calendar Y (see
02_build_filing_universe), and pick_filing_for_year takes the LATEST original
in that year. A company that misses its deadline files in a later calendar
year that already contains an on-time filing, loses that comparison, and its
late 10-K is dropped from filing_universe entirely -- so the original year
reports "past_due_not_filed", which reads as "this company did not file".

Six of the seven 2006 cases are the stock-option backdating cluster (ACS,
APOL, FDO, JBL, KLAC, SANM), which delayed filings for months during the
investigations; Maxim Integrated is the extreme case, filing FY2006, FY2007
and FY2008 on the same day in September 2008. It is not a historical
artifact -- Super Micro 2024, Xerox 2019, Jefferies 2019 and Mallinckrodt
2017 are the same shape.

The filing exists and is identified here (accession + date) but is NOT
ingested: doing so would need either two filings in one ticker-year or a
switch from filing-date to period-of-report keying, both of which change the
project's central convention rather than patch a bug. Recording it makes the
gap honest and the recovery a decision rather than a discovery.
"""
import datetime
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect, record_meta
from lib.edgar import fetch_submissions, get_json


def fetch_submissions_file(name: str) -> dict | None:
    """One of the older paginated submission blocks. `recent` holds only the
    latest ~1000 filings, so a large filer's 2006-2008 10-Ks are not in it --
    checking only `recent` reports "no filings" for exactly the long-lived
    companies this check is about."""
    try:
        return get_json(f"https://data.sec.gov/submissions/{name}")
    except Exception:
        return None

TODAY = datetime.date.today()


def median_month_day(dates: list[datetime.date]) -> tuple[int, int]:
    day_of_year = sorted(d.timetuple().tm_yday for d in dates)
    mid = day_of_year[len(day_of_year) // 2]
    proxy = datetime.date(2001, 1, 1) + datetime.timedelta(days=mid - 1)
    return proxy.month, proxy.day


def project_expected_date(year: int, month: int, day: int) -> datetime.date:
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return datetime.date(year, month, 28)  # Feb 29 in a non-leap projection year


def main():
    con = connect()

    universe = con.execute("SELECT year, ticker, snapshot_date FROM sp500_universe_raw").fetchall()
    resolution = {
        (r[0], r[1]): r[2] for r in con.execute(
            "SELECT year, ticker, cik FROM cik_resolution WHERE resolution_status = 'resolved'"
        ).fetchall()
    }
    ticker_ever_resolved = {t for (_, t) in resolution.keys()}

    filings = {(r[0], r[1]): r[2] for r in con.execute(
        "SELECT year, ticker, filing_date FROM filing_universe"
    ).fetchall()}
    cik_filing_dates = defaultdict(list)  # cik -> [filing_date, ...] across all matched years
    for (year, ticker), cik in resolution.items():
        if (year, ticker) in filings:
            cik_filing_dates[cik].append(filings[(year, ticker)])

    extracted = {(r[0], r[1]): r[2] for r in con.execute(
        "SELECT year, ticker, has_item_1a FROM risk_factors_index"
    ).fetchall()}

    # 03c splits extraction failures by whether the company ANSWERED Item 1A.
    # "Pointed at a document we could not bound" and "no Item 1A heading at
    # all" are both gaps, but only the first is a fact about the filer, and
    # the difference is invisible unless it is carried through to here.
    extraction_state = {(r[0], r[1]): (r[2] or "").split(":")[0] for r in con.execute(
        "SELECT year, ticker, fetch_error FROM risk_factors_index WHERE has_item_1a = false"
    ).fetchall()}
    KNOWN_STATES = {"no_item_1a_incorporated", "no_item_1a_found", "grade-1 ambiguous"}

    print(f"Base universe: {len(universe)} rows.")

    # Pass 1: classify every row into a coarse bucket.
    rows_no_filing = []  # (year, ticker, cik) needing date-projection sub-classification
    results = []  # final rows: year, ticker, cik, status, reason, sub_reason

    for year, ticker, snapshot_date in universe:
        cik = resolution.get((year, ticker))
        if cik is None:
            reason = "year_specific_no_match" if ticker in ticker_ever_resolved else "cik_never_resolved"
            results.append((year, ticker, None, "missing", reason, None))
            continue

        has_extracted = extracted.get((year, ticker))
        if has_extracted:
            results.append((year, ticker, cik, "has_item_1a", None, None))
            continue
        if (year, ticker) in filings:
            # Filing found, but extraction failed on it.
            state = extraction_state.get((year, ticker))
            sub_reason = state if state in KNOWN_STATES else None
            results.append((year, ticker, cik, "missing", "no_item_1a_extracted", sub_reason))
            continue

        if cik not in cik_filing_dates:
            results.append((year, ticker, cik, "missing", "cik_candidate_never_filed", None))
            continue

        rows_no_filing.append((year, ticker, cik))

    print(f"  {len(rows_no_filing)} rows need filing-date-projection sub-classification...")

    # Pass 2: for CIKs with a no_filing_found row, fetch deregistration status once per CIK.
    used_accessions = {
        a for (a,) in con.execute("SELECT accession_number FROM filing_universe").fetchall()
    }
    ciks_needing_dereg_check = sorted({cik for _, _, cik in rows_no_filing})
    # cik -> [(filingDate, accession)] for ORIGINAL 10-Ks never ingested.
    # Amendments are excluded deliberately: a 10-K/A restates a filing we
    # already hold and is not a missing observation.
    unused_originals = {}
    deregistered_date = {}  # cik -> date of most recent Form 15-* filing, if any
    for i, cik in enumerate(ciks_needing_dereg_check):
        subs = fetch_submissions(cik)
        if subs is None:
            continue
        recent = subs.get("filings", {}).get("recent", {})
        forms_dates = list(zip(recent.get("form", []), recent.get("filingDate", [])))
        dereg_dates = [datetime.date.fromisoformat(d) for f, d in forms_dates if f.startswith("15-")]
        if dereg_dates:
            latest_dereg = max(dereg_dates)
            # A Form 15 only means "stopped filing entirely" if nothing was
            # filed after it -- large companies routinely file a 15-12G/15-12B
            # to deregister ONE specific security class (an old debt issue, a
            # secondary share class) while continuing to file 10-Ks every year.
            # Confirmed bug case: Applied Materials filed a 15-12G on
            # 2018-12-12, one day before its 2018-12-13 10-K, then kept filing
            # 10-Ks every year through 2025 -- a naive "any Form 15 exists"
            # check wrongly marked AMAT, MU, PG, STX, TEL, TPR, WDC as
            # "will never file again."
            latest_any = max(
                (datetime.date.fromisoformat(d) for _, d in forms_dates),
                default=latest_dereg,
            )
            if latest_any <= latest_dereg:
                deregistered_date[cik] = latest_dereg
        originals = []
        blocks = [recent] + [
            fetch_submissions_file(f["name"]) for f in subs.get("filings", {}).get("files", [])
        ]
        for blk in blocks:
            if not blk:
                continue
            originals += [
                (d, a)
                for f, d, a in zip(blk.get("form", []), blk.get("filingDate", []),
                                   blk.get("accessionNumber", []))
                if f in ("10-K", "10-K405", "10-KSB", "10-KSB405") and a not in used_accessions
            ]
        unused_originals[cik] = sorted(originals)

        if i % 100 == 0:
            print(f"  ...dereg check {i}/{len(ciks_needing_dereg_check)}")

    for year, ticker, cik in rows_no_filing:
        other_dates = cik_filing_dates[cik]
        month, day = median_month_day(other_dates)
        expected_date = project_expected_date(year, month, day)

        # A 10-K that exists but landed in a later calendar year, where an
        # on-time filing outranked it. 36 months rather than 12: Maxim's
        # FY2006 report was not filed until September 2008.
        late = [
            (d, a) for d, a in unused_originals.get(cik, [])
            if f"{year}-01-01" <= d <= f"{year + 3}-01-01"
        ]

        if late:
            sub_reason = "filed_late_not_ingested"
        elif cik in deregistered_date and deregistered_date[cik] < expected_date:
            sub_reason = "will_not_file"
        elif expected_date > TODAY:
            sub_reason = "not_yet_due"
        elif expected_date <= TODAY:
            sub_reason = "past_due_not_filed"
        else:
            sub_reason = "still_unknown"

        results.append((year, ticker, cik, "missing", "no_filing_found", sub_reason))

    con.execute("DROP TABLE IF EXISTS missing_records")
    con.execute("""
        CREATE TABLE missing_records (
            year INTEGER,
            ticker VARCHAR,
            cik BIGINT,
            status VARCHAR,
            reason VARCHAR,
            sub_reason VARCHAR
        )
    """)
    con.executemany("INSERT INTO missing_records VALUES (?, ?, ?, ?, ?, ?)", results)

    record_meta(
        con, "missing_records", script="05_build_missingness.py",
        source_urls=["derived from sp500_universe_raw, cik_resolution, filing_universe, risk_factors_index"],
        column_descriptions={
            "year": "Study year", "ticker": "Ticker", "cik": "Resolved CIK, or NULL if never resolved",
            "status": "has_item_1a (success) or missing",
            "reason": "cik_never_resolved / year_specific_no_match / cik_candidate_never_filed / no_filing_found / no_item_1a_extracted / NULL (if status=has_item_1a)",
            "sub_reason": "For reason=no_filing_found only: not_yet_due / past_due_not_filed / will_not_file / still_unknown -- see decisions.md for the projection method",
        },
        row_count=len(results),
    )
    con.close()

    print(f"\nTotal rows: {len(results)} (should equal base universe {len(universe)})")
    from collections import Counter
    print("\nBy status/reason:")
    for (status, reason), count in Counter((r[3], r[4]) for r in results).most_common():
        print(f"  {status} / {reason}: {count}")
    print("\nsub_reason breakdown (no_filing_found only):")
    for sub_reason, count in Counter(r[5] for r in results if r[5]).most_common():
        print(f"  {sub_reason}: {count}")


if __name__ == "__main__":
    main()
