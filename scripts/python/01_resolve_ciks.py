#!/usr/bin/env python3
"""Resolve every (year, ticker) row to the historical CIK that actually
held that ticker during that year -- not just whoever holds it today.

Why this matters (pitfall #2): SEC's company_tickers.json and browse-edgar
both map a ticker to its CURRENT holder only. Over a 20-year window a
ticker can mean two unrelated things (e.g. "LB" = L Brands 1996-2021, vs.
an unrelated company that IPO'd under "LB" in 2024) or a legitimate
successor entity under a new CIK (e.g. "DELL" = Dell Inc CIK 826083 until
its 2013 going-private, vs. Dell Technologies Inc CIK 1571996 from the LBO
holding company onward). Naive current-ticker lookup silently mislabels
both cases as the current holder.

Method, per (ticker, year):
  1. Find the real membership stint (start_date, end_date) containing this
     year's snapshot_date, from sp500_ticker_stints (checkpoint 1).
  2. Look up the ticker's CURRENT holder CIK via company_tickers.json.
  3. Validate PER YEAR: did that CIK file a 10-K within 400 days of THIS
     year's snapshot date? If yes -> resolved, "current_ticker_validated".
     (This deliberately does not anchor to the stint start -- see
     validate_year for the 503-row false-negative bug that caused.)
  4. If the current holder fails that check (didn't exist yet / wasn't
     filing that year) -- or the ticker has no current holder at all
     (fully delisted, e.g. via acquisition) -- fall back to EDGAR full
     text search: search 10-K filings for the ticker string within a tight
     window around the target year. Cross-check two independent queries
     (bare ticker; "symbol "TICKER"" phrase) for agreement before accepting.
  5. If nothing resolves, the row is logged unresolved with a specific
     reason -- never silently dropped (pitfall #4). This is the first
     slice of the missingness dataset.

Tested and rejected as not adding coverage beyond company_tickers.json:
browse-edgar's ticker-based CIK lookup (`action=getcompany&CIK=<ticker>`)
resolves through the exact same current-ticker table, confirmed against
several fully-delisted tickers (WCOM, ENE, LEH) that return nothing via
either path. Full text search on filing content is the fallback that
actually adds coverage, since older 10-Ks commonly state "trades under
the symbol 'XYZ'" in Item 5 even though there was no dedicated structured
cover-page ticker field before SEC's 2019 rule change.
"""
import csv
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect, record_meta
from lib.edgar import fetch_company_tickers, fetch_submissions, full_text_search, get_json

# How far from a year's snapshot date a 10-K may sit and still count as
# "this ticker's filing for this year". 400 days (not 365) because filing
# dates drift: a company that files in March one year and the following
# February is 11 months apart, and a fiscal-year change can stretch the gap
# past a calendar year without any break in coverage.
YEAR_MATCH_WINDOW = datetime.timedelta(days=400)
TENK_FORMS = {"10-K", "10-K405", "10-KSB", "10-KSB405", "10-K/A", "10-KSB/A"}


def earliest_filing_date(submissions: dict) -> datetime.date | None:
    dates = []
    recent = submissions.get("filings", {}).get("recent", {})
    dates.extend(recent.get("filingDate", []))
    for f in submissions.get("filings", {}).get("files", []):
        if f.get("filingFrom"):
            dates.append(f["filingFrom"])
    if not dates:
        return None
    return datetime.date.fromisoformat(min(dates))


_tenk_dates_cache: dict[int, list[datetime.date]] = {}


def tenk_filing_dates(cik: int, submissions: dict) -> list[datetime.date]:
    """All 10-K filing dates for this CIK, spanning its ENTIRE filing
    history -- not just submissions.json's `recent` block.

    Confirmed bug (see decisions.md): `recent` is truncated by SEC to
    roughly the last ~1000 filings, which for a long-lived active filer
    (AMD, DuPont, Constellation Energy, etc.) doesn't reach back to a
    2006-era stint at all. That made `has_10k_in_window` always return
    False for old stints even though the company filed 10-Ks every single
    year -- verified against 116 tickers whose "failed continuity check"
    candidate CIK, per SEC's own submissions data, still trades under that
    exact ticker today. The paginated older-filings files SEC lists under
    filings.files[] (already fetched by 02_build_filing_universe.py the
    same way) are what's missing here.
    """
    if cik in _tenk_dates_cache:
        return _tenk_dates_cache[cik]

    recent = submissions.get("filings", {}).get("recent", {})
    forms = list(recent.get("form", []))
    filing_dates = list(recent.get("filingDate", []))
    for file_meta in submissions.get("filings", {}).get("files", []):
        try:
            more = get_json(f"https://data.sec.gov/submissions/{file_meta['name']}")
        except Exception:
            continue
        forms.extend(more.get("form", []))
        filing_dates.extend(more.get("filingDate", []))

    dates = [
        datetime.date.fromisoformat(d)
        for f, d in zip(forms, filing_dates)
        if f in TENK_FORMS
    ]
    _tenk_dates_cache[cik] = dates
    return dates


def has_10k_in_window(tenk_dates: list[datetime.date], start: datetime.date, end: datetime.date) -> bool:
    return any(start <= d <= end for d in tenk_dates)


def validate_year(cik: int, submissions: dict, snapshot_date: datetime.date) -> tuple[bool, str | None, str]:
    """Did this CIK actually file a 10-K around THIS year, not "was it alive
    at the start of the S&P membership stint"?

    Confirmed bug (this replaces validate_continuity): the old check anchored
    to stint_start, which for most tickers is 1996-01-02 -- a decade before
    the study window even opens. Any company whose current CIK is newer than
    its 1996 index entry therefore failed EVERY year, including years where
    that CIK is unambiguously the filer. Measured against SEC's own data,
    that wrongly rejected 503 of 883 rows: ORCL (CIK 1341439, 21 10-Ks on
    file, first 2006-07-21) failed all 21 study years because Oracle's
    current holding company was registered in 2005 and the check demanded
    1996. Same for CMCSA, COP, DUK, ED, EXC, FE, MCO, NEM, NOC, RF, CNP --
    all 21-for-21 false negatives. FDX missed the CONTINUITY_BUFFER_DAYS
    grace window by ten months and lost two decades of data.

    Anchoring to the snapshot year is also STRICTLY better at catching the
    ticker-recycling case the old check existed to catch: a company that
    IPO'd under a recycled ticker in 2024 has no 10-K anywhere near a 2010
    snapshot, so 2010 still will not resolve to it. The old check only
    verified the stint's start; this verifies the actual year in question.

    Returns (ok, method, detail).
    """
    tenks = tenk_filing_dates(cik, submissions)
    lo = snapshot_date - YEAR_MATCH_WINDOW
    hi = snapshot_date + YEAR_MATCH_WINDOW

    if tenks:
        if has_10k_in_window(tenks, lo, hi):
            return True, "current_ticker_validated", (
                f"current holder CIK {cik} filed a 10-K within "
                f"{YEAR_MATCH_WINDOW.days}d of this year's snapshot ({lo}..{hi})"
            )
        return False, None, (
            f"current holder CIK {cik} has {len(tenks)} 10-Ks on file but none within "
            f"{YEAR_MATCH_WINDOW.days}d of this year's snapshot ({lo}..{hi})"
        )

    # No 10-K anywhere in this CIK's entire filing history. That is not a
    # resolution failure -- it is very often a real identification of a real
    # company that simply does not file 10-Ks under this identity (a foreign
    # private issuer filing 20-F, say). Resolving it keeps 05's
    # cik_candidate_never_filed bucket working instead of dumping the row
    # into the far less informative cik_never_resolved bucket. The guard is
    # that the entity must demonstrably have existed and been filing SOMETHING
    # by this year, which still rejects a recycled ticker's newer holder.
    earliest = earliest_filing_date(submissions)
    if earliest is not None and earliest <= snapshot_date:
        return True, "current_ticker_non_10k_filer", (
            f"current holder CIK {cik} filed no 10-K ever, but was filing by {earliest} "
            f"(<= snapshot {snapshot_date}); resolved so downstream can classify it as a non-10-K filer"
        )
    return False, None, (
        f"current holder CIK {cik} has no 10-K on file and no filing history at or before "
        f"snapshot {snapshot_date} (earliest={earliest})"
    )


def fulltext_candidate(ticker: str, window_start: datetime.date, window_end: datetime.date):
    """Only accept a full text search candidate when independent queries
    agree AND the candidate is a real operating company, not a shell.

    A manual audit (see decisions.md, checkpoint 2) found that accepting a
    single query's top hit alone ("low confidence" mode) was essentially
    noise -- 0/2 correct in a spot check (garbage hits included a mortgage
    trust, a shell antimony miner, an unrelated holding company). Requiring
    two independently-phrased queries to agree on the same CIK raised
    accuracy to about 75% in a 12-item audit, which is still not something
    to accept silently -- so this method's rows are tagged distinctly in
    cik_resolution and documented as carrying materially higher error risk
    than current_ticker_validated, rather than presented as equally solid.
    """
    bare_hits = full_text_search(f'"{ticker}"', "10-K", window_start.isoformat(), window_end.isoformat())
    # "symbol" + exact-phrase ticker (not a nested-quote phrase, which EDGAR's
    # search syntax doesn't support and silently mis-parses into noise --
    # confirmed via a broken LB query that returned an unrelated mortgage
    # trust document as the top hit before this fix).
    phrase_hits = full_text_search(f'symbol "{ticker}"', "10-K", window_start.isoformat(), window_end.isoformat())

    def top_cik(hits):
        for h in hits:
            ciks = h.get("_source", {}).get("ciks", [])
            if ciks:
                return int(ciks[0])
        return None

    bare_cik = top_cik(bare_hits)
    phrase_cik = top_cik(phrase_hits)

    if not (bare_cik and phrase_cik and bare_cik == phrase_cik):
        return None, None

    subs = fetch_submissions(bare_cik)
    if subs is None or subs.get("entityType") not in (None, "operating"):
        # Excludes trusts/shells caught this way; doesn't catch a wrong-but-
        # real operating company (that's the residual ~25% error rate).
        return None, None

    return bare_cik, "fulltext_fallback"


OVERRIDES_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "manual_cik_overrides.csv"


def load_overrides() -> list[dict]:
    """Hand-verified (ticker, year range) -> CIK mappings, checked into the
    repo as data, not code.

    This is the escape hatch for identities no automated path can reach: most
    importantly the successor-entity chain, where a ticker's CURRENT holder is
    a newer corporate entity than the one that actually filed the 10-Ks.
    Confirmed case: company_tickers.json maps XOM to ExxonMobil Holdings Corp
    (CIK 2115436), a 2026 reorg entity with zero 10-Ks ever filed, so every
    automated route -- current-holder validation and full text search alike --
    either fails or points at the wrong entity for all 21 study years.

    Every row carries a source_url and a note recording the evidence, so an
    override is auditable and diffable rather than an unexplained magic
    number. Overrides take priority over all automated paths; that is the
    point of them, and it is safe precisely because each one is justified in
    the file itself.
    """
    if not OVERRIDES_PATH.exists():
        return []
    with OVERRIDES_PATH.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [
        {
            "ticker": r["ticker"].strip(),
            "year_start": int(r["year_start"]),
            "year_end": int(r["year_end"]),
            "cik": int(r["cik"]),
            "source_url": r["source_url"].strip(),
            "note": r["note"].strip(),
        }
        for r in rows
        if r.get("ticker") and not r["ticker"].startswith("#")
    ]


def find_override(overrides: list[dict], ticker: str, year: int) -> dict | None:
    for rule in overrides:
        if rule["ticker"] == ticker and rule["year_start"] <= year <= rule["year_end"]:
            return rule
    return None


def find_stint(stints: list[tuple], ticker: str, snapshot_date: datetime.date):
    matches = [
        s for s in stints
        if s[0] == ticker and s[1] <= snapshot_date and (s[2] is None or snapshot_date <= s[2])
    ]
    if not matches:
        return None
    return matches[0]


def main():
    con = connect()
    universe_rows = con.execute(
        "SELECT year, snapshot_date, ticker FROM sp500_universe_raw ORDER BY ticker, year"
    ).fetchall()
    stints = con.execute(
        "SELECT ticker, start_date, end_date FROM sp500_ticker_stints"
    ).fetchall()

    print(f"Resolving {len(universe_rows)} (year, ticker) rows...")

    current_holder = fetch_company_tickers()
    print(f"Loaded {len(current_holder)} current ticker->CIK mappings from company_tickers.json")

    overrides = load_overrides()
    print(f"Loaded {len(overrides)} manual override rules from {OVERRIDES_PATH.name}")

    submissions_cache: dict[int, dict | None] = {}
    year_resolution_cache: dict[tuple, tuple] = {}  # (ticker, snapshot_date) -> (cik, method, detail); cik None means "needs fallback"
    fallback_cache: dict[tuple, tuple] = {}  # (ticker, snapshot_date) -> (cik, method, detail)
    today = datetime.date.today()
    # Fallback full text search window: narrow, centered on this specific
    # year's snapshot date, NOT the whole membership stint. Searching the
    # full stint (e.g. DELL's 1996-2013, 17 years) surfaced unrelated
    # documents as top hits (ABM Industries for "DELL", Discovery Oil & Gas
    # for "LB") purely because a wider window has more chances for noise to
    # outscore the real hit -- confirmed by diffing against a narrow
    # single-year window, which found the correct CIK in every spot check
    # (see decisions.md, checkpoint 2 fallback-window-width entry).
    FALLBACK_WINDOW_BEFORE = datetime.timedelta(days=200)
    FALLBACK_WINDOW_AFTER = datetime.timedelta(days=400)

    results = []
    for i, (year, snapshot_date, ticker) in enumerate(universe_rows):
        if i % 500 == 0:
            print(f"  ...{i}/{len(universe_rows)}")

        stint = find_stint(stints, ticker, snapshot_date)
        if stint is None:
            results.append((year, ticker, snapshot_date, None, None, None,
                             "no_stint_match", "unresolved",
                             "Ticker/snapshot_date not covered by any sp500_ticker_stints row"))
            continue

        _, stint_start, stint_end = stint

        # Highest priority: a hand-verified override, which exists precisely
        # because every automated path below is known to be wrong here.
        override = find_override(overrides, ticker, year)
        if override is not None:
            results.append((
                year, ticker, snapshot_date, stint_start, stint_end, override["cik"],
                "manual_override", "resolved",
                f"manual override -> CIK {override['cik']} ({override['source_url']}): {override['note']}",
            ))
            continue

        # Keyed by (ticker, snapshot_date), NOT (ticker, stint_start). Caching
        # one verdict per stint was half of the bug validate_year documents:
        # a single 1996-anchored rejection was reused for all 21 study years.
        # Network cost is unchanged -- submissions_cache and the 10-K date
        # cache are both keyed by CIK, so per-year validation is pure
        # computation over already-fetched data.
        year_key = (ticker, snapshot_date)

        if year_key not in year_resolution_cache:
            cik, method, detail = None, None, None
            candidate_cik = current_holder.get(ticker)

            if candidate_cik is not None:
                if candidate_cik not in submissions_cache:
                    submissions_cache[candidate_cik] = fetch_submissions(candidate_cik)
                subs = submissions_cache[candidate_cik]
                if subs is None:
                    detail = f"current holder CIK {candidate_cik} has no submissions data at SEC"
                else:
                    ok, ok_method, detail = validate_year(candidate_cik, subs, snapshot_date)
                    if ok:
                        cik, method = candidate_cik, ok_method
            else:
                detail = "ticker has no current holder in company_tickers.json"

            # None cik here means "fall back below", not unresolved yet.
            year_resolution_cache[year_key] = (cik, method, detail)

        cik, method, detail = year_resolution_cache[year_key]

        if cik is None:
            fb_key = (ticker, snapshot_date)
            if fb_key not in fallback_cache:
                window_start = snapshot_date - FALLBACK_WINDOW_BEFORE
                window_end = min(snapshot_date + FALLBACK_WINDOW_AFTER, today)
                fb_cik, fb_method = fulltext_candidate(ticker, window_start, window_end)
                if fb_cik is not None:
                    # NOT auto-accepted as resolved. A 27-item manual audit
                    # against known company identities (see decisions.md)
                    # found this method -- even after requiring two
                    # independent queries to agree and excluding shells --
                    # was only about 70% accurate (wrong hits included
                    # Trump Entertainment Resorts for "TER" instead of
                    # Teradyne, Meridian Biosciences for "PLL" instead of
                    # Pall Corp). Per this project's core lesson from the
                    # prior build, a wrong company attribution silently
                    # presented as resolved is worse than an honestly
                    # documented gap, so the candidate is kept in the detail
                    # text for future manual review but the row stays
                    # unresolved.
                    fallback_cache[fb_key] = (
                        None, "fulltext_candidate_unverified",
                        detail + f"; narrow-window full text search ({window_start}..{window_end}) "
                                 f"found candidate CIK {fb_cik} but it is NOT auto-accepted "
                                 f"(empirical accuracy ~70% in manual audit, judged too risky)",
                    )
                else:
                    fallback_cache[fb_key] = (
                        None, "unresolved",
                        detail + f"; narrow-window full text search ({window_start}..{window_end}) found no consistent candidate",
                    )
            cik, method, detail = fallback_cache[fb_key]

        results.append((year, ticker, snapshot_date, stint_start, stint_end, cik, method,
                         "resolved" if cik else "unresolved", detail))

    con.execute("DROP TABLE IF EXISTS cik_resolution")
    con.execute("""
        CREATE TABLE cik_resolution (
            year INTEGER,
            ticker VARCHAR,
            snapshot_date DATE,
            stint_start DATE,
            stint_end DATE,
            cik BIGINT,
            resolution_method VARCHAR,
            resolution_status VARCHAR,
            resolution_detail VARCHAR
        )
    """)
    con.executemany("INSERT INTO cik_resolution VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", results)

    record_meta(
        con, "cik_resolution", script="01_resolve_ciks.py",
        source_urls=[
            "https://www.sec.gov/files/company_tickers.json",
            "https://data.sec.gov/submissions/",
            "https://efts.sec.gov/LATEST/search-index",
        ],
        column_descriptions={
            "year": "Study year",
            "ticker": "Raw ticker from sp500_universe_raw",
            "snapshot_date": "Source snapshot date for this year (from checkpoint 1)",
            "stint_start": "Start date of the sp500_ticker_stints membership stint this row falls into",
            "stint_end": "End date of that stint (NULL = still current)",
            "cik": "Resolved historical CIK, or NULL if unresolved",
            "resolution_method": "current_ticker_validated (10-K filed within 400d of this year's snapshot) / current_ticker_non_10k_filer (real entity, files no 10-Ks) / fulltext_candidate_unverified (candidate found but NOT accepted) / unresolved / no_stint_match",
            "resolution_status": "resolved / unresolved",
            "resolution_detail": "Free-text explanation of how/why this row resolved or didn't",
        },
        row_count=len(results),
    )
    con.close()

    n_resolved = sum(1 for r in results if r[5] is not None)
    n_total = len(results)
    print(f"\nResolved {n_resolved}/{n_total} rows ({n_resolved/n_total:.1%})")

    method_counts = {}
    for r in results:
        method_counts[r[6]] = method_counts.get(r[6], 0) + 1
    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count}")


if __name__ == "__main__":
    main()
