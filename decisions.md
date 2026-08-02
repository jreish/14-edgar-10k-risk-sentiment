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
