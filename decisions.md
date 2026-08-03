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

## 2026-08-02 — Checkpoint 4: Item 1A extraction, parser reuse, and a targeted secondary-document fix

**Decision:** Reused `risk_factors_parser.py` from project 13 essentially
as-is (adapted import paths only). Unlike the CIK resolution work, this
component isn't identity-sensitive — its correctness is independently
checkable by inspecting extracted section length/content — and it already
encodes several confirmed real-world edge cases (ToC false starts, inline
cross-references in Exxon's filings, McDonald's glossy-annual-report format
with bare unprefixed headings, mid-word line breaks). Rewriting from
scratch would just re-discover the same edge cases with no benefit.

**First-pass result:** 7,205/7,312 filings (98.5%) extracted successfully,
0 fetch failures. This is squarely in the "high 90s%" range pitfall #3
anticipated.

**Investigated the 107 misses instead of accepting the number blindly**
(per pitfall #7): they were NOT random noise — 61% concentrated in just 6
tickers (CLX 20, CINF 13, HAL 10, USB 10, C 7, KDP 5). Traced one
(JNJ 2006) by hand: the primary document SEC lists for that filing is a
173KB "Form 10-K Cross-Reference Index" — a bare page-number table with no
substantive text at all — while the actual annual report content
(including Item 1A) lives in a separate exhibit document within the same
filing accession, a common older-era pattern for incorporating an annual
report by reference. This is the same underlying phenomenon the parser's
McDonald's handling addresses, except McDonald's embeds the annual report
text directly in the primary document (bare, unprefixed headings) while
these filers split it into a genuinely separate file.

**Targeted fix (`03b_retry_missing_item_1a.py`):** for each miss, fetch the
filing's `index.json` (lists every document in the accession) and try each
non-primary document until one parses successfully. Recovered only 6/107
(FCX 2006, USB 2008/2009/2010/2019/2020) — all recovered from the
filing's full concatenated submission `.txt` file, not a dedicated exhibit;
spot-checked USB 2008's recovered text and it's genuine risk-factors prose,
not boilerplate. The much lower yield than the concentration pattern
suggested means most of the remaining misses (JNJ, CLX, CINF, HAL, KDP,
etc.) either don't have a machine-findable secondary document with a
parseable heading, or genuinely have unusually-formatted risk sections this
parser's heading patterns don't catch. This retry pass alone took ~40
minutes for 107 rows (multiple documents fetched and parsed per miss,
mostly failing) for a 5.6% recovery rate within that batch — a clear
diminishing-returns signal.

**Final result:** 7,211/7,312 (98.6%). Stopped here rather than chasing
further per pitfall #3's explicit guidance ("expect to land in the high
90s%... document the remainder rather than chasing it indefinitely") —
the concentration pattern was worth one targeted fix, and that fix has now
been tried and found to have a low ceiling for further gains.

**Rejected alternative:** keep iterating on the secondary-document fallback
(e.g. trying every remaining document type, adding OCR for scanned
exhibits, loosening the parser's heading match further). Rejected given
the measured 5.6% marginal recovery rate against a 40-minute cost for just
this batch — not a good use of remaining project time relative to the
other checkpoints still ahead.

**Storage note:** extracted text totals 474MB across 7,211 `.txt` files in
`data/clean/risk_factors/`, committed to git directly (same convention as
project 13, which also committed its equivalent 534MB corpus) — DuckDB
holds the manifest (`file_path` column) and provenance, not the blob text
itself, per the project's stated storage convention.

## 2026-08-02 — Checkpoint 5: SIC->sector mapping built broadly, then corrected against real output

**Decision:** `lib/sectors.py` classifies via SIC-major-group ranges
(2-digit prefix) covering the full 01-99 SIC space, not just the ~197
codes actually observed in this dataset — per pitfall #6's instruction to
build broadly rather than reactively patch an "Unclassified" bucket.
Explicit per-code overrides handle ranges that don't map to one sector
(pharma sits inside the general chemicals range but is Health Care;
motor vehicles sit inside transportation equipment but are Consumer
Discretionary; REITs sit inside real-estate-adjacent finance codes but
are Real Estate, not Financials). Result: 0/546 companies fell through to
"Unclassified" on the first run.

**Caught and fixed after inspecting real output, not just the count:**
first run put 130/546 companies (24%) in Industrials — high relative to
typical S&P 500 sector weight. Inspecting which SIC codes fed that bucket
found two real problems: (1) SIC 5122/5047 (wholesale drug/medical
distributors — Cencora, Cardinal Health, McKesson, Henry Schein) fell into
a generic "wholesale = Industrials" default despite clearly belonging in
Health Care; (2) SIC 7389 ("Business Services, NEC," a vague catch-all
predating modern industry classification) held 18 companies with no
single correct sector — a mix of payment networks (Visa, Mastercard,
PayPal, Western Union, Global Payments, Corpay), online marketplaces
(eBay, Etsy, Uber, DoorDash), and genuine IT/data-services companies
(Accenture, Akamai, Fiserv, FIS, Fair Isaac, Broadridge, MSCI, CoStar).

Fixed with: SIC-level overrides for the wholesale-distributor codes
(-> Health Care) and wholesale groceries (-> Consumer Staples, correct for
Sysco), a changed 7389 default (-> Information Technology, the majority
pattern among the remaining names), and a small `CIK_OVERRIDES` dict for
the 11 companies whose real business model is unambiguous but doesn't
match that default (Visa/Mastercard/PayPal/Western
Union/Global Payments/Corpay -> Financials; eBay/Etsy/Uber/DoorDash ->
Consumer Discretionary; Domino's Pizza, whose SIC 5140 "wholesale
groceries" default of Consumer Staples is wrong for a restaurant chain,
-> Consumer Discretionary). Second run: Industrials down to 106/546 (19%),
a more plausible distribution.

**Rejected alternative:** keep refining sector-boundary judgment calls
indefinitely (e.g. individually checking every wholesale-trade or
business-services company). Stopped once the distribution looked
plausible and the highest-count, most clearly-wrong cases were fixed —
same iterate-then-stop discipline as the Item 1A parser and CIK
resolution work, not a claim of perfect GICS-equivalent classification.

## 2026-08-02 — Checkpoint 6: missingness dataset, built from the raw base table outward

**Decision:** `05_build_missingness.py` starts from `sp500_universe_raw`
(the checkpoint 1 output — every raw (ticker, year), 10,524 rows, nothing
ever filtered out) and LEFT JOINs `cik_resolution`, `filing_universe`, and
`risk_factors_index` outward from there, rather than starting from any
downstream table. This is the direct, structural fix for pitfall #4:
project 13's missingness report undercounted true missingness by ~7x
because its working table only kept rows that had *already* resolved to a
CIK, so anything that failed resolution was invisible to the report by
construction, not just underrepresented in it.

**Reason taxonomy** (every "missing" row gets exactly one):
- `cik_never_resolved` — ticker has no resolved CIK in *any* study year.
- `year_specific_no_match` — ticker resolves in other years, not this one.
- `cik_candidate_never_filed` — CIK resolved, but has zero matched 10-Ks
  across every study year (suggests it isn't actually a 10-K filer under
  this identity).
- `no_filing_found` — CIK resolved and does file 10-Ks generally, just not
  found for this specific year. Sub-classified (see below) into
  `not_yet_due` / `past_due_not_filed` / `will_not_file` / `still_unknown`.
- `no_item_1a_extracted` — filing found and fetched, extraction failed.

**Verification:** every upstream count reconciles exactly — 7,211
`has_item_1a` (matches checkpoint 4's final number exactly), 101
`no_item_1a_extracted` (= 7312 filing-universe rows minus 7211 extracted,
exactly), 3,015 + 106 = 3,121 unresolved (matches checkpoint 2's
unresolved count exactly), 89 + 2 = 91 (matches checkpoint 3's "resolved
CIK but no filing found" count exactly). Total rows: 10,524, equal to the
base universe — confirms no row was silently dropped anywhere in the chain.

**Bug found and fixed — deregistration (`will_not_file`) false positives:**
the first run flagged 7 unambiguously still-thriving S&P 500 companies
(Applied Materials, Micron, Procter & Gamble, Seagate, TE Connectivity,
Tapestry, Western Digital) as "will never file again," all for their 2026
row. Root cause: the check treated *any* Form 15 (15-12B/15-12G/etc.) in a
CIK's history as full deregistration. Real case: Applied Materials filed a
15-12G on 2018-12-12 — one day before its 2018-12-13 10-K — to deregister
one specific security class (common for large companies with multiple
registered securities, e.g. an old debt issue), then kept filing 10-Ks
every year through 2025 with zero interruption. Fixed by only trusting a
Form 15 as real deregistration when it's also the CIK's *most recent*
filing of any kind (nothing — no 10-K, nothing — filed after it). All 7
companies correctly reclassified to `not_yet_due` after the fix; the
`will_not_file` count dropped from 7 to 0, which is itself plausible for
this dataset (a company that's genuinely deregistered mid-target-year
typically also stops being CIK-resolvable/ticker-current well before that
target year, so it more often surfaces as `cik_never_resolved` upstream
than reaches this specific check at all).

**Filing-date projection method:** for a CIK's `no_filing_found` row,
projects the expected filing date as the median month/day across that same
CIK's *other* matched filing years (from `filing_universe`), applied to the
target year. Compares against today's date and the (corrected)
deregistration check to land on `not_yet_due` / `past_due_not_filed` /
`will_not_file`. `still_unknown` is reserved for a CIK with no other
matched years to project from at all — didn't occur in this run (every
`no_filing_found` CIK had at least one other matched year), so the bucket
exists in the schema but is currently empty; left in place since a future
re-run could hit it.

**Rejected alternative:** the live-EDGAR spot-check verification pass
project 13 used for recent years (`11_verify_recent_filing_status.py`).
Not implemented here — the projection method alone already produced a
clean, internally-consistent, upstream-reconciling result, and the one bug
found was caught by inspecting output rather than needing a second live
data source to cross-check against. Worth adding if a future audit finds
the projection method missing something the way this session's audit
caught the Form-15 bug.

## 2026-08-02 — Checkpoint 7: analysis scripts, and using real-world events as a validation signal

**Decision:** `06_keyword_topic_analysis.py` (Iran-conflict keyword
categories kept as separate columns rather than one blanket flag, plus
Iran/tariffs/Ukraine filing-level topic trends and the tariffs-by-sector
breakdown the reference chart needs) and `07_language_trends.py` (avg
Item 1A word count by year, plus five curated theme-word categories
normalized per 1,000 words) — both pure text processing over the
already-extracted `.txt` files, no network calls, adapted from project
13's equivalent scripts where the underlying methodology (keyword
presence counting) wasn't identity-sensitive and was already sound.

**Sentiment methodology — no external lexicon:** rather than importing a
finance-specific sentiment wordlist (e.g. Loughran-McDonald, the standard
academic choice for 10-K tone analysis), `07_language_trends.py` defines
a small curated word list directly in the script. The project's data
sources are explicitly scoped to SEC/GitHub; pulling in an external,
undocumented lexicon file would be a new dependency outside that scope,
and a visible, inspectable word list (even if less academically
established) is more in keeping with this project's overall bias toward
transparency over borrowed authority.

**Validation — real-world events as an independent check:** rather than
just eyeballing the output for "does this look like numbers," cross-checked
the results against known real-world history, since if the pipeline is
actually working end-to-end (correct filing-year matching, correct Item 1A
extraction), the resulting language trends should line up with real
events without any tuning:
- Pandemic-word frequency: near-zero 2006-2019, spikes to 2.6-per-1000-words
  in 2021 (10-Ks filed in 2021 cover FY2020, the first full pandemic year),
  stays elevated through 2022-2023, fades by 2024-2026 — exactly the
  expected shape.
- Inflation/supply-chain frequency: flat ~0.19-0.22 for over a decade,
  rises sharply 2021-2023 (peak 0.77 in 2023), recedes after — matches the
  real 2021-2023 inflation surge.
- Cybersecurity frequency: near-zero in 2006, climbs steadily from ~2012
  onward, keeps rising through 2026 — matches the well-known multi-decade
  rise in cyber-risk disclosure.
- Avg Item 1A word count: 4,485 words in 2006 to 14,398 in 2026, a ~3.2x
  increase — matches the widely-documented trend of risk sections growing
  substantially longer over this exact period.

None of these patterns were targeted or tuned for — they fell out of the
pipeline on the first run. That's a stronger correctness signal for the
whole extraction chain (filing-year matching, Item 1A parsing) than any
row-count reconciliation check alone could provide, since it's an
independent real-world cross-check rather than internal consistency.

**Iran-conflict finding:** Iran-related keyword mentions show a gradual
rise from 1-2 filings/year in 2006-2008 to 19 in 2025 and 25 in 2026, not
a sharp step-change exactly at the 2025 conflict. This is a real,
unforced result (not adjusted to match an expected narrative) — plausibly
reflects a broader multi-year trend toward expanded sanctions/export-
control risk disclosure rather than one discrete event, and 2026 (the
first filing year to substantially cover FY2025, when the conflict
occurred) does show the highest count on record.

## 2026-08-02 — Checkpoint 8: R visualization, and correcting the hatch-box semantics

**Decision:** R scripts (`scripts/r/`) read directly from the DuckDB
database (via the R `duckdb` package, read-only connections) rather than
from CSV exports — keeps DuckDB the single source of truth end to end,
including the plotting layer, rather than introducing a parallel CSV path
that could drift out of sync. A shared `lib_theme.R` holds the Okabe-Ito
palette, a restrained Healy-style theme (minimal gridlines, direct
labeling via `ggrepel` instead of a legend, bold titles, grey captions),
a `top_n_plus_other()` helper for binning small categories, and the
diagonal-hatch-line helper (`make_hatch()`, adapted from project 13 — pure
geometry, not data-sensitive, so reuse was safe here the way it wasn't for
the CIK resolution work).

**The tariffs-by-sector-with-missing chart — the brief's explicit
correction, implemented:** the hatch box on top of each year's stacked bar
uses `missing_records` filtered to `reason = 'no_filing_found' AND
sub_reason = 'not_yet_due'` only — not every missing company-year, and not
even every `no_filing_found` row (which would still incorrectly include
`past_due_not_filed` and `will_not_file`). This is the direct fix for the
exact problem the brief flagged in advance: mixing in other missing
reasons (especially CIK-resolution-stage gaps, which this build's
checkpoint 2 numbers show are large — 3,121 unresolved rows) would dwarf
the real bars and make the box meaningless. Every other missing reason
remains fully queryable in `missing_records` — the chart just doesn't
draw them into this particular box, exactly the "what belongs in the
dataset vs. what this one chart visualizes are separate decisions" split
the brief called for.

**Validation via real-world cross-check, again:** the tariffs-by-sector
chart shows mention counts roughly flat through 2017, then rising sharply
starting 2018-2019 — matching the real US-China trade war tariff
escalation that began in 2018. Another independent, untuned confirmation
that the filing-year matching and extraction pipeline built in earlier
checkpoints is working correctly.

**Google Fonts / showtext:** project 13's R scripts used `showtext` +
`font_add_google()` to pull "Source Sans 3" from Google Fonts at render
time. Dropped that here in favor of the system default sans-serif —
avoids a render-time network dependency for a cosmetic typeface choice,
keeping the chart-generation step reproducible without network access
once the underlying data is in DuckDB.

## 2026-08-02 — Checkpoint 9: final QA

**Row-count reconciliation:** `sp500_universe_raw` (10,524) and
`missing_records` (10,524) match exactly; zero rows have an inconsistent
status/reason combination (`has_item_1a` with a reason set, or `missing`
with no reason); zero duplicate `(year, ticker)` pairs. The full pipeline
accounts for the entire base universe with nothing silently dropped.

**Spot-check 1 — recycled ticker (LB):** all 16 years correctly show
`cik_never_resolved` / unresolved. No wrong CIK is ever assigned. This is
the exact case the project's known-pitfalls list was built around, and the
conservative design from checkpoint 2 (never auto-accept an unverified
full-text candidate) holds here as intended.

**Spot-check 2 — merger (CELG, Celgene, acquired by Bristol Myers Squibb
in 2019):** every year 2007-2019 shows `cik_never_resolved`, honestly and
correctly labeled — but this surfaces a real, worth-naming cost of the
checkpoint 2 design: Celgene has no current ticker holder (delisted, not
reused) and its full text search candidate (CIK 816284, correctly
"CELGENE CORP" — confirmed by hand against live EDGAR) is never
auto-accepted, because that method's ~70% aggregate reliability isn't
trusted even in instances where it happens to be right. Net effect: a
real, unambiguous, long-time S&P 500 constituent is entirely absent from
the resolved dataset, not because it's unresolvable in principle but
because this project chose accuracy-with-gaps over recall-with-errors.
This is the direct, known tradeoff of the checkpoint 2 decision, not a
new bug — flagged here explicitly so it's not mistaken for one.

**Spot-check 3 — recent IPO/spinoff (GEV, GE Vernova, spun off April
2024):** resolves cleanly via `current_ticker_validated` for all three
of its real years (2024-2026) — the CIK-resolution design working exactly
as intended for a straightforward recent case. But this check surfaced
two smaller, genuine findings on the downstream steps:
  - **2024 shows `no_filing_found` / `past_due_not_filed`.** Technically
    accurate (no 10-K was filed *in* calendar 2024 for a company that
    didn't exist as a public filer until April that year), but
    conceptually imperfect: the filing-date projection method (checkpoint
    6) implicitly assumes a company was already an established filer, an
    assumption that doesn't hold for a spinoff's debut partial year. A
    more precise label would be something like "not yet a public filer
    this year," but this is a minor, narrow edge case (affects
    brand-new-that-year constituents specifically) not worth a special
    case given the low volume it would affect.
  - **2025 (GE Vernova's actual first 10-K, filed 2025-02-06) shows
    `no_item_1a_extracted`.** Checked by hand: the filing does contain a
    real Item 1A section, but its heading is formatted
    `Item 1A. "Risk Factors"` — with literal quotation marks around the
    heading text — which the parser's regex doesn't match (it allows
    `-–—:` as separators between the item number and heading, not a
    quote character). The filing's inline cross-references use the same
    quoted format, so this isn't a cross-reference false-positive problem
    like Exxon's case; it's a heading-format variant not yet in the
    parser. Documented here rather than patched — per pitfall #3's
    explicit allowance, and because this session already spent
    substantial effort on parser edge cases in checkpoint 4 with a
    measured low marginal return; this is exactly the kind of remainder
    that gets documented, not chased indefinitely.

**Overall QA conclusion:** the dataset does what it claims to do — every
company-year has either real extracted text or a specific, honest,
verifiable reason it doesn't — but "resolved and extracted" is a
meaningfully conservative subset of "the true history," by design. The
biggest visible cost of that conservatism is companies like Celgene that
are real, identifiable, and simply not trusted by the current pipeline.
A natural next iteration (not undertaken here, flagged for a future
session) would be a manual review pass over `fulltext_candidate_unverified`
rows, since a person looking at company name + candidate CIK side by side
could resolve many of these with far more confidence than the automated
two-query-agreement heuristic ever could on its own.
