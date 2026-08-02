# Decisions

Non-obvious technical choices, and rejected alternatives, in build order.
Written for someone auditing the reasoning later.

## 2026-08-02 — Fresh project folder (14) instead of rebuilding inside 13

**Decision:** Start `14_EDGAR_10K_Risk_Sentiment` as a new numbered project
rather than rebuilding in place inside `13_EDGAR_10K_Risk_Sentiment`.

**Why:** Project 13's rebuild history (see its `decisions.md`, 2026-08-02 entry)
shows a fetch function that cached the S&P 500 membership source file forever
and silently reused a Nov 2019 snapshot for six years of "history." A clean
folder means no risk of a stale `data/raw/` cache, an old script, or an old
DuckDB file quietly surviving into this build and reproducing the same class
of bug. Asked the user directly since the brief flagged this exact ambiguity;
they chose the new folder.

**Rejected alternative:** Rebuild inside 13, wiping `data/` first. Riskier —
requires trusting that every stale artifact actually gets deleted, and the
old `scripts/` (which used current-ticker-holder-only CIK resolution, and
bolted DuckDB on as an afterthought in step 12 rather than storing
everything in DuckDB from the start) would need to be entirely rewritten
anyway, so there was little to gain from reusing the folder.

## 2026-08-02 — S&P 500 source file: `(Updated).csv`, verified via GitHub API

**Decision:** Use `fja05680/sp500`'s
`S&P 500 Historical Components & Changes (Updated).csv`, not the
similarly-named file without "(Updated)".

**Why:** Independently checked both files' commit history via the GitHub API
(`/repos/fja05680/sp500/commits?path=...`) rather than trusting project 13's
prior finding on faith:
- `...(Updated).csv` — last commit `2026-07-13`, actively maintained.
- `...Changes.csv` (no "Updated") — last commit `2019-11-21`, frozen.

This matches project 13's root-cause finding exactly (their frozen dataset's
last real update was also traced to a Nov 2019 snapshot), so the two
independent checks agree. Picking the frozen file by filename-guessing is
precisely how project 13's six-year-flat-snapshot bug happened in the first
place.

**Rejected alternative:** Trust project 13's prior write-up outright without
re-verifying. Rejected per the brief's explicit instruction to verify via the
GitHub API's commit history "before picking one; don't assume."

## 2026-08-02 — DuckDB-first architecture from checkpoint 1 onward

**Decision:** Every pipeline stage (`sp500_universe_raw`, `cik_resolution`,
`filing_universe`, `risk_factors_index`, `missing_records`,
`company_sectors`) writes directly to a single DuckDB database
(`data/sp500_10k.duckdb`) as its primary output, with a queryable `_meta`
table tracking provenance (script, timestamp, source URLs, column
descriptions, row count) per table. CSVs, where produced, are a skimming
convenience layer exported from DuckDB, not the source of truth.

**Why:** Project 13 built each stage as a CSV first and only added DuckDB at
step 12, as a report bolted onto the end. That ordering is exactly why its
missingness report undercounted by ~7x — the working "intended universe"
table it diffed against had already silently dropped unresolved rows upstream,
and there was no single, queryable, running record of every raw
(ticker, year) row from step 1 through step 5 to catch that. Storing
everything in DuckDB from checkpoint 1, with every table traceable via
`_meta`, makes "did this row survive to the next stage, and if not why" a
query instead of an archaeology project.

**Rejected alternative:** Keep CSVs as primary and add a DuckDB layer at the
end (project 13's approach). Rejected as the direct cause of a known prior
bug.

## 2026-08-02 — Checkpoint 1: mid-year (July 1) snapshot anchor, kept from prior build

**Decision:** For each study year Y, use the source snapshot closest to (on
or before) July 1 of year Y as "the" S&P 500 membership for that year.

**Why:** Most S&P 500 constituents have a December fiscal year-end, so a
mid-year anchor is a reasonable representative point for "who was a member
during fiscal year Y" without arbitrarily favoring the January or December
edge of the year. This matches project 13's convention — reviewed it and
found the reasoning sound, so kept it rather than changing it just for the
sake of a fresh build. Not blindly copied, though: the prior script's
docstring claimed the source data used a "-YYYYMM" ticker suffix to
disambiguate delisted tickers; that suffix format does not exist anywhere
in the current source file (checked programmatically across all 1206
distinct raw tickers in the file). That comment was stale — likely an
earlier version of the upstream CSV used it. Dropped that (nonexistent)
disambiguation logic rather than porting dead code forward.

**Rejected alternative:** Calendar-year-end (Dec 31) anchor. Would bias
toward companies' *year-end* index status specifically, which is a defensible
alternate convention, but July 1 more literally satisfies the brief's request
for a snapshot representative of the studied fiscal year.

## 2026-08-02 — Checkpoint 1: always re-fetch source files, never "cache if exists"

**Decision:** `00_fetch_sp500_history.py` downloads both source CSVs fresh
on every run and overwrites `data/raw/`, rather than checking "does a cached
copy exist" and returning it if so.

**Why:** This is the literal fix for pitfall #1 — the prior build's
`if CACHE_PATH.exists(): return CACHE_PATH.read_text()` pattern is exactly
what let a 2019 snapshot silently serve as "today's data" for six years.
Both source files are small (a few thousand rows), so re-fetching every run
costs nothing. Raw bytes are still written to `data/raw/` for offline
debugging, just never read back as an existence check.

**Validation added:** the script hashes each year's constituent ticker set
and hard-fails if any two adjacent years are byte-identical (this is
precisely what a frozen/stale source would look like), then prints
constituent counts per year for a human skim. First real run: counts move
smoothly 497→506 across 2006-2026 with no repeats — no year is flat,
confirming the source is live and the extraction logic isn't accidentally
collapsing years together.

**Bonus find:** the source repo's `sp500_ticker_start_end.csv` (fetched
alongside the main snapshot file, stored as `sp500_ticker_stints`) already
gives clean per-ticker membership date ranges — including multiple
non-contiguous stints for the same ticker. Spot-checked against the
pitfall #2 example cases: `AAL` shows two separate stints (1996-01-02 to
1997-01-15, and 2015-03-23 to 2024-09-23 — the pre-bankruptcy AMR-era AAL
and the post-US-Airways-merger American Airlines Group AAL are correctly
split), and `LB` shows a single 1996-01-02 to 2021-08-03 stint (the real
L Brands tenure, ending when it spun off into Bath & Body Works/Victoria's
Secret — correctly excluding the unrelated company that took the LB ticker
via a 2024 IPO). This table will be the backbone of checkpoint 2's improved
CIK resolution.
