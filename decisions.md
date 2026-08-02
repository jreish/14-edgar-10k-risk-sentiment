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

## 2026-08-02 — Checkpoint 2: CIK resolution cascade, and why full text search is NOT trusted as an acceptance method

**Decision:** `01_resolve_ciks.py` resolves each (ticker, year) row through
a two-stage cascade:
1. **`current_ticker_validated`** — look up the ticker's current holder via
   `company_tickers.json`, then validate it against the real membership
   stint (from checkpoint 1's `sp500_ticker_stints`): the candidate CIK's
   earliest-ever filing date must predate the stint's start (within a
   365-day buffer), and it must have at least one 10-K filed within the
   stint window. This is resolved once per (ticker, stint) and reused
   across every year in that stint — cheap, and reliable enough to trust
   outright.
2. **Full text search fallback** — for rows that fail step 1 (current
   holder is a different, later entity — e.g. Dell Technologies Inc. vs.
   the original Dell Inc.) or have no current holder at all (fully
   delisted via M&A — 290 of 935 distinct tickers in this dataset), search
   EDGAR full text search for the ticker string within a narrow window
   around that specific year (not the whole stint — see bug below).

**Why full text search candidates are logged but NOT accepted as
resolved:** this took three iterations to get right, and the honest
answer is that the method never got reliable enough to trust blindly.
Tested three configurations, each audited manually against a sample of
known companies (fetching the candidate CIK's real name/formerNames from
SEC and checking it against the target ticker):
  - **Single bare-ticker or single phrase query, accept the top hit
    ("low confidence" tier):** 0/2 correct in spot checks. Produced
    outright garbage — a mortgage-backed securities trust for "LB", a
    shell antimony mining company for "LB" in another year, an unrelated
    telecom holding company for "DELL". Discarded entirely.
  - **Require two independently-phrased queries (bare ticker; "symbol
    "TICKER"") to agree on the same top CIK, plus require the candidate
    be `entityType == "operating"` (excludes trusts/shells):** two
    separate 12-15 item manual audits came back at 9/12 and 9/13 correct
    (~70-75%). Real failures included: "HRS" (should be Harris Corp)
    resolving to an unrelated micro-cap "Clean Energy Technologies, Inc.";
    "TER" (should be Teradyne) resolving to "Trump Entertainment Resorts,
    Inc."; "PLL" (should be Pall Corp) resolving to "Meridian Biosciences";
    "FRX" (should be Forest Laboratories) resolving to "Daegis Inc."; "MIL"
    (should be Millipore Corp) resolving to "Viasat Inc." These are exactly
    the kind of confident-looking wrong answer pitfall #2 warned about —
    real companies, real 10-Ks, coincidentally containing the target ticker
    string somewhere in their own filing, with nothing in the API response
    itself signaling "this is a false positive."

  A ~70-75% accurate method, silently blended into the same "resolved"
  bucket as the ~99%+ reliable `current_ticker_validated` method, would
  mean roughly 1 in 4 of ~1,150 rows carries a wrong company's risk-factor
  text with no way to tell which ones from the data alone — a worse
  outcome than leaving them honestly blank, per this project's founding
  lesson from project 13's `APC`/`LB` mislabeling incident. So the
  candidate CIK is preserved in `resolution_detail` (for a future manual
  review pass, if ever worth the effort) but `cik` is set to NULL and
  `resolution_method` is `fulltext_candidate_unverified`, not `resolved`.

**Bug found and fixed along the way:** the first full-pipeline run searched
each *entire membership stint* (e.g. DELL's 1996-2013, a 17-year span) in
one full text query, reusing that single result across every year in the
stint. This actively made things worse, not just imprecise — DELL's whole
2006-2013 span collapsed to a single (wrong) top hit, "ABM Industries
Inc.", and every LB year collapsed to "Discovery Oil & Gas Inc." Searching
a narrow window centered on each specific year individually (target
snapshot date minus 200 days to plus 400 days) fixed this — it's why the
DELL 2008-2010/2013 rows correctly landed on CIK 826083 (the real original
Dell Inc.) once evaluated per-year instead of per-stint.

**Final resolution rate:** 7,403 / 10,524 rows (70.3%) resolved via the
trustworthy method; 3,121 rows (29.7%) unresolved, split between "no
candidate found at all" and "a full text candidate exists but wasn't
trusted" (both fully logged with a specific reason and, where applicable,
the candidate CIK for later reference). This number is lower than a
naive full-text-accepting pipeline would report (which hit 98.5%), and
that's the point — the number, this time, means what it says.

**Process note — output buffering:** ran the resolution script through
`| tail -N` for the first two attempts, which fully buffers stdout until
the process exits (not a TTY), so there was zero visibility into progress
for ~50-90 minute runs. Redirecting to a file directly with `python -u`
(unbuffered) on the third attempt restored live progress visibility.
Worth remembering for future long-running scripts in this project.

**Rejected alternative:** spend more engineering time trying to push full
text search precision higher (e.g. requiring 3-way agreement across more
phrase variants, proximity-restricted queries). Stopped after diminishing
returns became clear across three iterations and two manual audits;
70.3% honestly-labeled resolution is a legitimate, defensible checkpoint
result under this project's stated tolerance for documented gaps over
chased-but-uncertain completeness (see pitfall #3's parallel allowance for
the Item 1A parser).

## 2026-08-02 — Checkpoint 3: filing-year convention is "filed in year Y," not "covers fiscal year Y"

**Decision:** `02_build_filing_universe.py` matches each resolved
(cik, ticker, year) row to the 10-K *filed during calendar year Y*, not
the 10-K covering fiscal year Y. For a normal December-fiscal-year-end
company, the 10-K filed in year Y covers FY Y-1.

**Why:** this project studies "how did companies characterize risk during
year Y" (both the 20-year sentiment drift study and the 2025 Iran-conflict
study). The 10-K filed during year Y is the company's most recent public
risk disclosure as of that year — it's the document that was actually
sitting in front of investors and regulators at the time. A report that
came out covering an already-elapsed fiscal year is a worse match for
"real-time risk characterization" than a same-year-filed one. Reviewed
this convention from the prior build's equivalent script and endorsed it
on that reasoning rather than adopting it by default.

**Rejected alternative:** match by fiscal period-of-report instead of
filing date. More "textbook correct" for financial-statement analysis, but
worse for this project's actual research questions, which care about when
risk language was written, not which fiscal year it accounts for.

**Result:** 7,312/7,403 resolved rows (98.8%) matched to an actual 10-K.
Checked the 91 that didn't: 64 are year 2026 (this year — companies with
fiscal year ends later than the pull date simply haven't filed yet, exactly
pitfall #5's caveat), and the remaining 27 are thin one-offs scattered
across 2006-2024 (plausible genuine gaps — mid-year bankruptcies, brief
index membership, etc.) to be characterized properly in checkpoint 6's
missingness dataset rather than hand-waved here.
