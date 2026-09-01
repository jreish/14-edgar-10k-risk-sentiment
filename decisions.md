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

## 2026-08-03 — Checkpoint 10: fixing the two most tractable missingness gaps

**Decision:** Fix two specific, verified bugs feeding the largest
missingness reason codes, then do a full pipeline rebuild rather than a
partial/incremental patch, so every downstream table (`filing_universe`,
`risk_factors_index`, `company_sectors`, `missing_records`,
`keyword_hits`, `topic_trends_by_year`, `tariffs_by_year_sector`,
`language_trends_by_year`) stays internally consistent with the corrected
`cik_resolution`.

**Bug 1 — `tenk_filing_dates()` only read the `recent` filings block.**
`validate_continuity()` (checkpoint 2) requires a candidate current-ticker
holder to have a 10-K filed within the target historical stint, checked
via `submissions.json`'s `filings.recent` block. SEC truncates `recent`
to roughly the last ~1000 filings, which for a long-lived active filer
doesn't reach back to an old stint at all — so the check spuriously
failed for companies that were correct the whole time, shunting them to
the (deliberately conservative, ~70-75% accurate) full-text-search
fallback instead of accepting the exact match already in hand. Verified
before fixing: pulled all 116 distinct tickers hitting this exact
"current holder failed continuity check" path and confirmed via
`data.sec.gov/submissions` that **100% of them still trade under that
exact ticker today** — i.e. every one was a false rejection, not a real
ticker-reuse ambiguity. Fix: `tenk_filing_dates()` (renamed to take
`cik` + `submissions`) now also fetches the paginated older-filings files
already listed in `submissions["filings"]["files"]` (the same files
`02_build_filing_universe.py` already knew to fetch), cached per CIK.

**Bug 2 — `company_tickers.json` is missing at least one live ticker.**
AEP (American Electric Power), a currently-active NYSE company that
files 10-Ks every year, is absent from `company_tickers.json` entirely —
confirmed by direct comparison against `data.sec.gov/submissions`, which
shows CIK 4904 filing under ticker AEP to this day. SEC's sibling file
`company_tickers_exchange.json` does list it. Fix: `fetch_company_tickers()`
now fills gaps from `company_tickers_exchange.json` (only for tickers
absent from `company_tickers.json`, never overriding it), at effectively
zero cost since it's one extra static JSON fetch.

**Parser fix — three heading-format variants added to `risk_factor_parser.py`.**
The residual 101 `no_item_1a_extracted` rows were concentrated in a
handful of tickers (CLX, CINF, HAL, C, KDP, USB = ~60/101). Manual
inspection of the actual filing HTML found three unhandled variants in
how "Item 1A" is rendered, all fixed by generalizing the existing
per-letter spacing tolerance (already used for "risk"/"factors") to the
word "item" and the item-number-plus-letter itself:
  - CLX: `"ITEM 1.A. RISK FACTORS"` — a period between digit and letter.
  - HAL: `"Item 1(a). Risk Factors"` — letter in parentheses.
  - CINF: the *real* heading (not just the ToC entry) renders each
    letter of "ITEM" in its own inline element, which `html_to_text`'s
    per-element newline insertion splits into `"I\nTEM"` — a literal
    `item` match failed even though the existing regex already tolerated
    this exact pattern for "risk"/"factors".
Regression-checked against 5 previously-passing filings (STX, OMC, LMT,
CB, GIS) — identical extracted length in every case, confirming this
only adds coverage rather than changing existing matches.

**Measured impact of the full rebuild** (`missing_records`, before -> after):
  - `cik_never_resolved`: 3015 -> 2884 (-131)
  - `year_specific_no_match`: 106 -> 62 (-44)
  - `no_item_1a_extracted`: 101 -> 36 (-65)
  - `not_yet_due` (2026 hatch box): 62 -> 62 (unchanged, as expected —
    unrelated to either fix)
  - Total missing: 3313 -> 3079 (-234, a 7.1% reduction)

This is well short of the ~1037-row upper bound estimated before the
fix (which assumed every one of the 116 continuity-check-bug tickers
would resolve across *all* of its affected years). Spot-checking why:
AMD (18/18 years now resolved) and AEP (21/21) are the clean wins the
bug predicted — a single continuously-operating company, wrongly
rejected across its whole stint. But tickers like DD and CEG stayed
mostly unresolved even after the fix, correctly — "DD" spans E.I. du
Pont de Nemours (pre-2017) and the unrelated post-2019 DuPont de Nemours
Inc. spinoff entity, and "CEG" spans the original Constellation Energy
Group (merged into Exelon, 2012) and the 2022 Exelon spinoff of the same
name — genuine ticker reuse, where the current holder correctly fails
continuity for the older, unrelated company's years. The bug only ever
inflated the *count* of tickers routed to the fallback path; it never
guaranteed all of them were false rejections, so the smaller realized
gain is the fix working as intended, not a sign it underdelivered.

---

## 2026-08-16 — CIK resolution: three fixes, 7445 -> 9296 Item 1A sections

Coverage of the study universe went from 70.7% to 88.3% (`sp500_universe_raw`
= 10,524 ticker-years throughout). Resolution went 7578 -> 9500 rows.

### 1. The continuity check was anchored to the wrong date (+500 rows)

`validate_continuity` asked whether a candidate CIK was filing at
`stint_start` — the start of the S&P *membership* stint, which for most
tickers is 1996-01-02, a decade before the study window opens. The verdict
was then cached once per `(ticker, stint_start)` and reused for all 21 study
years. Any company whose current CIK is newer than its 1996 index entry
therefore failed **every** year, including years where that CIK is
unambiguously the filer.

Measured against SEC's own data before writing any code: 503 of 883
"failed continuity" rows were false negatives. ORCL (CIK 1341439, 21 10-Ks
on file) failed all 21 years because Oracle's current holding company was
registered in 2005 and the check demanded 1996. CMCSA, COP, DUK, ED, EXC,
FE, MCO, NEM, NOC, RF, CNP: all 21-for-21. FDX missed the 365-day grace
window by ten months and lost two decades.

Replaced by `validate_year`: did this CIK file a 10-K within 400 days of
*this year's* snapshot? This is strictly better at catching the ticker-reuse
case the old check existed for — a company that IPO'd under a recycled
ticker in 2024 has no 10-K near a 2010 snapshot, so 2010 still won't resolve
to it. Realized gain +500, within 3 of the pre-registered estimate.

**Rejected:** loosening `CONTINUITY_BUFFER_DAYS` instead. It would have
fixed FDX's ten-month miss and nothing else; the anchor date was the bug,
not the tolerance.

### 2. Ranking vs. verification for full text search (+1096 rows)

Checkpoint 2 measured the full-text fallback at ~70% accuracy and refused to
auto-accept it, keeping candidates in `resolution_detail` for review. That
was the right call, but the framing was wrong: *ranking* ("which filing best
matches this ticker string?") is unreliable, while *verification* ("does this
filing declare this ticker?") is nearly deterministic.

The archetype is TER. Trump Entertainment Resorts outranks Teradyne because
its 10-K abbreviates the registrant itself as "TER" throughout — no ranking
can separate them. But Trump Entertainment declares its symbol as TRMP
(later TRMPQ) and never claims TER, so it is positively *contradicted*.

`lib/cover_page.py` returns three outcomes, deliberately not two:
verified / contradicted / **no_evidence**. The third exists because SEC only
required a trading-symbol column on the cover page from 2019: Teradyne's own
2010 10-K does not contain the string "TER" even once. Those years can never
be verified directly and are left as honest gaps.

`01b` then verifies the top 3 hits per query rather than only the top-ranked
one — verification does not care about rank, so a wider candidate set costs
nothing (an unverifiable candidate is simply rejected) and buys recall.
Results over 2446 unresolved rows: 963 verified, **1142 contradicted**,
161 ambiguous (2+ candidates both claiming the ticker — refused, not broken
by rank), 40 no_evidence, 140 no candidate.

That 1142 is the load-bearing number. Under the original top-hit-accept
design a large share of those would have been attributed to the wrong
company. The ~30% error rate found by hand-auditing 27 rows is now caught
mechanically at full scale.

A second pass propagates a verified identity across its stint when exactly
one CIK verifies and that CIK filed a 10-K in the target year (+133 rows).
Refused when two CIKs verify in the same stint — that is a real mid-stint
handover, precisely the case that must not be guessed at.

### 3. Overrides: proposed by hand, accepted only on evidence (+326 rows)

`data/manual_cik_overrides.csv` is the escape hatch for identities no
automated path reaches — mainly successor-entity chains, where a ticker's
current holder is a newer corporate entity than the one that filed the
10-Ks. XOM is the clearest: `company_tickers.json` maps it to ExxonMobil
Holdings Corp (CIK 2115436), a 2026 reorg entity with zero 10-Ks ever, so
every automated route fails for all 21 years.

Because this is the one place a human asserts an identity directly, proposing
and accepting are split. `01d` takes `ticker=CIK` hypotheses from any source
and confirms each against that CIK's own filings before it may be written.
Of 22 proposed, 21 confirmed; **XRX was CONTRADICTED** (its filings in range
declare only CNDT) and was not added. The guard rejecting its author's own
hypothesis is the reason it exists.

**Confirming an identity is not confirming a duration.** Baker Hughes Co
genuinely declares BHGE on its cover page, so the identity verified — but it
first filed a 10-K in 2018 while the proposed range began in 2006. Twelve
years would have been attributed to a company that did not yet exist,
carrying a legitimate-looking verification note. Two fixes: `01d` bounds
ranges by actual filing years (BHGE became 2018-2018), and `01` independently
re-validates every override year against a real 10-K, recording
`manual_override_year_rejected` rather than trusting the range. A wrong range
is now a visible gap instead of silent misattribution.

### 4. Dual-class ticker spelling (part of the +326)

The membership source writes `BF.B` / `BRK.B`; SEC writes `BF-B` / `BRK-B`,
and neither SEC file contains the dotted form. Both failed lookup outright
and fell through to a full text search that cannot confirm a ticker whose own
filings spell it differently — 38 rows lost across two continuously-filing
members with unambiguous CIKs. Aliased in `fetch_company_tickers` rather than
hand-overridden, since it is a class of ticker, not a one-off.

Also retired a stale claim in that function: `company_tickers_exchange.json`
no longer contains a single ticker absent from the main file, so the
documented AEP fallback adds nothing. AEP is missing from both despite CIK
4904 filing 10-Ks to this day, and is now an override.

### Infrastructure

- **API response cache** (`data/raw/api_cache`, gitignored) for
  `data.sec.gov/submissions` and `efts.sec.gov` only. A full resolution run
  is ~2 hours uncached, which makes the seed-an-override / re-run loop
  impractical. **Deliberately not cached:** the membership source files and
  `company_tickers.json` — checkpoint 1 established that source-of-truth
  inputs are always re-fetched, since a silently reused stale snapshot is the
  failure that sank the previous build. `EDGAR_NO_CACHE=1` bypasses.
- **Connection-failure resilience.** A momentary DNS failure resolving
  efts.sec.gov burned all six retries in 61 seconds and killed a run 2000 rows
  in. Connection errors now retry on their own 10-minute budget, separate from
  the HTTP retry budget.
- **Unsearched != searched-and-empty.** `full_text_search` previously swallowed
  every failure into an empty list, so a network outage was recorded
  identically to a real negative — rows that look investigated but never were.
  Now raises `SearchUnavailable`, and 01 records `fulltext_search_failed`.

### What remains

1024 rows across 223 tickers, down from 2946. `missing_records` reasons:
`year_specific_no_match` 521, `cik_never_resolved` 503, `no_filing_found` 103,
`no_item_1a_extracted` 86, `cik_candidate_never_filed` 15.

`01c_unresolved_worklist.py` ranks the tail by rows recoverable per override:
top 50 tickers close 46% of what's left, top 100 close 74%. The head is CMA
(17 rows), XRX (13), VAR (12), AABA/BCR/BHGE/DD/HCP (11 each) — mostly
genuine ticker reuse across unrelated companies (DD spans E.I. du Pont and
the post-2019 DuPont de Nemours spinoff), which is exactly the class that
must be resolved by evidence rather than convenience.

---

## 2026-08-16 (later) — Extraction: a false-positive class, and a smaller win than forecast

Went after the 86 `no_item_1a_extracted` rows. Recovered 28, found and fixed
99 rows that were wrong in a worse way, and ended with **fewer** Item 1A
sections than before: 9296 -> 9219. That is the number getting more honest,
not the dataset getting worse.

### The forecast was wrong, and by how much

The plan estimated "up to 76 of 86 addressable" on the strength of one
diagnosed pattern. Realized recovery was **14** from that pattern, 28 in
total. The error was generalising from a single case: BNY Mellon's structure
turned out to be BNY Mellon's, not a genre shared by the rest.

The 72 that remain are several distinct shapes, not one:
  - FDX: real section sits in the primary document, but the heading that
    follows it ("Forward-Looking Statements") is in no end-marker set.
  - AET: real text is in an Exhibit 13 carrying exactly ONE start marker and
    no end marker -- below any run threshold.
  - C: ends at a company-specific heading ("SUSTAINABILITY AND OTHER ESG
    MATTERS").

**Rejected:** deriving the end marker from each document's own table of
contents (the ToC names what follows Risk Factors). Prototyped; it read the
successor name correctly for FDX and C and still failed, because the name
does not recur as a standalone heading in the body. Also rejected: taking
"the next heading of any kind" after a start, which truncates immediately on
the bolded sub-headings that fill a risk-factors section.

Stopped there rather than write per-filer parsers. Each one adds regression
surface for a handful of rows, and the failure mode is silently capturing the
wrong text -- which this project treats as worse than a gap.

### What worked: the running-header strategy

Annual Report exhibits have no Item numbering, so no end marker exists. What
they do have is a running page header -- BNY Mellon 2016 reprints "Risk
Factors (continued)" 27 times at ~4,900-char intervals across offsets
375,604..504,224. The repetition that makes the document unparseable by
marker-matching is itself the signal: a dense run of repeated headings IS the
section, ending where the run stops.

Invoked ONLY when the primary strategy returns None. That ordering is what
makes the ~9,300 existing extractions byte-identical by construction rather
than by testing; the test exists anyway.

Two refinements the spot-check forced, and it is worth noting neither was
visible in the row counts -- only in reading the output:
  - Parentheses allowed in the heading pattern, since the true terminator is
    often "Supplemental Information (unaudited)". DIGITS deliberately still
    excluded: these documents print "BNY Mellon 109" at every page break
    INSIDE the section, so treating a digit-bearing line as a heading would
    truncate at the first page boundary.
  - Trailing page-footer trim, for the last such footer before the true end.

### The real finding: 99 stored successes were not risk factors

Checking recovered output led to the short end of the distribution. Against a
corpus median of **51,471 characters**, 99 stored sections were under 1,500 --
84 of them under 600. They are incorporation-by-reference pointers:

> "Information in response to this Item 1A can be found in the Company's 2014
> Annual Report on pages 155 to 165 under the heading 'Risk Factors.' That
> information is incorporated into this report by reference."

Wells Fargo's entire 20-year series was stored this way at 229-249 chars.
These sit between a valid start marker and a valid end marker, so the parser
extracted them and recorded a success.

This is a **false positive, which is worse than a gap**: a gap is visible in
the coverage numbers, whereas this silently contributes 200 characters of
boilerplate to a language study as though it were a company's risk
disclosure. It had been inflating every coverage figure reported this session.

**Rejected:** phrase matching. Tried it first; it caught USB and JNJ and
missed HAL ("is described in Management's Discussion and Analysis"), MCK ("is
included in the Financial Review section"), GENZ ("We incorporate our
disclosure related to risk factors into this section"), EMN ("For
identification and discussion of the most significant risks applicable"), and
AMD. The phrasing space is not enumerable.

Length generalises. `MIN_PLAUSIBLE_SECTION_CHARS = 1500`, and the tradeoff is
stated in the code: a genuinely complete but very short Item 1A would be
rejected. Nothing in this corpus looks like that -- the shortest section that
reads as complete is ~2,000 chars -- and being wrong costs a visible gap,
while the status quo costs silent contamination.

Rejection also routes the row to `03b`, which searches the accession for the
document the stub POINTS AT. That is where the payoff is: it does not just
delete bad rows, it often recovers the real ones.

### Effect

| ticker | before | after |
|---|---|---|
| UHS | 6 stubs @ 617-674 chars | 6 real @ 47,596-193,661 |
| MCK | 4 stubs @ 253-254 | 17 real @ 50,724-79,247 |
| USB | 9 stubs @ 201-211 | 9 real (max 46,154) |
| WFC | 20 stubs @ 229-249 | 6 real @ 39,445-605,728, 15 honest gaps |
| GENZ | 5 stubs @ 272-396 | 0, all 5 now honest gaps |

Minimum stored section is now exactly 1,500 chars, against 200 before.

Totals: Item 1A 9296 -> **9219** (99 rejected, 28 recovered, net -77).
Universe coverage 88.3% -> **87.6%**; share of located 10-Ks 99.1% -> 98.3%.
Both previous figures were overstated by the stubs.

`no_item_1a_extracted` rises 86 -> 163, which is the honest accounting of
what extraction cannot currently reach.

### Verification

- `tests/test_parser_regression.py` (new): re-extracts a seeded 300-filing
  sample and asserts byte-identical output. 297 identical, 0 changed, 2
  floor-rejected as intended. This is the gate a parser change must pass
  before the pipeline re-runs over it -- a change that shifts section
  boundaries would corrupt the sentiment series in a way no coverage count
  would reveal.
- `tests/test_risk_factor_parser.py` (new): 11/11, pinning the BNY Mellon
  running-header case and five stub phrasings that defeated phrase matching.

---

## 2026-08-16 (later still) — Attacking cik_never_resolved without a crosswalk

`cik_never_resolved` fell **503 -> 175 rows** (111 -> 49 tickers). Item 1A
9219 -> **9525**; universe coverage 87.6% -> **90.5%**.

No WRDS access, so the CRSP/Compustat crosswalk was unavailable. Instead ran
the existing propose-and-verify loop over all 111 delisted tickers in four
batches: hypotheses proposed from recollection, every one adjudicated by
`01d` against SEC's filings before it could reach the overrides file. 85
rules now, covering 646 ticker-years.

**Roughly a fifth of my own proposals were rejected**, which is the only
reason this method is acceptable. TIN (proposed Temple-Inland) resolves to a
CIK declaring NUE; TRB (Tribune) declares AXE/CAT; MEL declares SYBT; CBE
declares MA; MEDI declares NVAX. Three tickers were handed the same CIK
(Avery Dennison, 8818) through simple carelessness and all three came back
NO_EVIDENCE rather than landing in the data.

### Two extensions were needed, both from studying the batch-1 failures

**CIK-restricted full text search as a second evidence path.** SEC only
required a trading symbol on the 10-K cover page from 2019, and Safeway,
Forest Labs, Avon, Sigma-Aldrich, Legg Mason and US Steel each file a decade
of 10-Ks without the string appearing once -- so the 10-K-only standard could
never confirm them however correct the hypothesis. The same companies state
the symbol readily in proxies and 8-Ks. Restricting EFTS to the candidate's
own CIK (`&ciks=`, verified to actually filter: 2191 -> 949 hits) makes that
admissible on the same evidence standard: a document FILED BY this company
declaring this symbol. Converted Safeway, Forest Labs, AABA and WYND.

**Bankruptcy ticker suffixes.** Chapter 11 moves a listing to OTC and appends
a suffix ending in Q -- Peabody BTU -> BTUUQ, Kodak EK -> EKDKQ, RadioShack
RSH -> RSHCQ, Lehman LEH -> LEHMQ, GM MTL -> MTLQQ. The membership source
records the bankruptcy ticker while the filings declare the base symbol.
Accepting a declared symbol that is a genuine PREFIX of a Q-terminated ticker
is tight enough that it cannot match an unrelated company, only the
pre-bankruptcy form of the same one.

### A near-miss worth recording: SEC reports CURRENT names

Three confirmations looked obviously wrong on the summary line and were held
back before being appended:

| ticker | SEC current name | former names |
|---|---|---|
| ATGE | Covista Inc. | DEVRY EDUCATION GROUP INC. |
| WAMUQ | Maverick Merger Sub 2, LLC | WMI HOLDINGS CORP. (WaMu's successor) |
| IAC | Match Group, Inc. | IAC/INTERACTIVECORP |

All three were correct. SEC's submissions API reports a CIK's *current* name,
which for precisely the successor chains this file exists to handle is not
the name that filed the 10-Ks. The evidence was sound and the display was
misleading -- which would have made a human review of
`manual_cik_overrides.csv` actively counterproductive. `01d` now carries
`formerNames` into every evidence note.

### Where it stops, and why

19 tickers ended NO_EVIDENCE: LM, SIAL, AVP, X, FII, GR, AYE, AKS, BDK, BUD,
LEHMQ, BSC, CFC, PD, BLS, DDR, ACV, ASO, AT. SEC's entity name matches the
proposal exactly in most cases -- LEGG MASON, INC.; AVON PRODUCTS INC; BEAR
STEARNS COMPANIES INC; BELLSOUTH CORP -- but no filing of any type declares
the symbol, and a name that looks right is not evidence. Left unresolved.

This is the honest boundary of what can be done without an external
ticker-history crosswalk. CRSP (PERMNO-ticker history joined to Compustat's
CIK) would settle all 19 in minutes and remains the recommendation if WRDS
access appears.

---

## 2026-08-18 — Phrase-first search: 96.0% coverage, and why the manual crosswalk was the wrong instrument

Item 1A **9525 -> 10102**; universe coverage 90.5% -> **96.0%**. 2006, the
weakest year all along, went 73.8% -> **84.9%** (and 47.9% at the start of
this work). Override rules 85 -> **200**, covering 1,362 ticker-years.

The question that prompted this was whether to hand-build the historical
ticker->CIK crosswalk that CRSP would otherwise provide. The answer turned
out to be no, because the automated path was not exhausted -- the query was
simply wrong.

### Ticker-first search was asking an unanswerable question

Every verification path so far searched for the TICKER inside a candidate's
filings. For distinctive strings that works. For US Steel's "X", Legg Mason's
"LM", Goodrich's "GR", Phelps Dodge's "PD" it is hopeless: the letters appear
in ordinary prose on every page, so the one document that actually declares
the symbol is buried under hundreds that merely contain the characters.

Inverting the query fixes it. Search the DECLARATION PHRASE ("under the
symbol", "trading symbol", "ticker symbol") restricted to the candidate's own
CIK, then read whichever symbol the returned document declares. Same evidence
standard throughout -- a document filed by that company, declaring that
symbol, parsed by the unchanged `lib/cover_page.declared_symbols`. Only the
question changes, from "does this string appear in your filings?" to "which
symbol do your filings declare?"

Measured on the 13 tickers that ticker-first search had left unverifiable:
**12 confirmed immediately**. Across the full re-run of stragglers, 19 of 24
-- and four of those (STJ, DNR, PCL, SUNEQ) had previously come back
CONTRADICTED, meaning ticker-first was not merely failing to find evidence,
it was surfacing actively misleading evidence from documents that mention
other companies' symbols.

On the `year_specific_no_match` bucket (predecessor entities in M&A chains)
it was better still: **49 of 50**, then 48 of 62. Xerox, C.R. Bard, Varian,
DuPont, Cigna, Time Warner, Starwood, Celgene, EMC, Medtronic, Qwest, Whole
Foods, National Semiconductor, Linear Technology -- all resolved against
their own filings.

### The manual crosswalk, assessed properly

It was feasible: ~160 tickers, 3-5 hours, and a hand entry can cite a source
URL like any other. It was still the wrong instrument. Slower, not
reproducible, and it leans on the weakest link in this entire workflow --
**across seven batches roughly one in five of my own proposals was wrong**,
and every one was caught by machine verification rather than by my
confidence. Specimen errors: TIN's proposed CIK declares NUE; TRB declares
AXE/CAT; MEL declares SYBT; CBE declares MA; HSP resolved to Bimini Capital
(BMM); MWW to Lamar Advertising (LAMR); OMX to MKS (MKSI). Three tickers were
handed the same CIK (Avery Dennison) through plain carelessness and all three
were rejected.

That rejection rate is the entire argument. Proposing from recollection is
acceptable ONLY because nothing reaches the dataset on the strength of it.

### formerNames is what makes the file reviewable

Nearly every confirmation in the M&A bucket reads wrong at a glance --
DD -> "EIDP, Inc."; PX -> "LINDE INC"; UTX -> "RTX Corp"; TWX -> "WARNER
MEDIA, LLC"; HRS -> "L3HARRIS TECHNOLOGIES"; COG -> "Coterra Energy"; GGP ->
"Brookfield Property REIT". All correct: SEC reports a CIK's CURRENT name,
and these are exactly the successor chains the overrides exist to encode.
Without former names travelling in the evidence note, a human review of
`manual_cik_overrides.csv` would be worse than no review at all.

### Multi-range tickers

BHGE now carries two non-overlapping ranges (2006-2016 Baker Hughes Holdings
CIK 808362; 2018 Baker Hughes Co CIK 1701605) -- a genuine succession within
one ticker. Checked explicitly that no ticker has overlapping ranges, since
`find_override` returns the first match and an overlap would make the
resolution silently order-dependent.

### Where it now stands

| reason | rows | tickers |
|---|---|---|
| no_item_1a_extracted | 188 | 49 |
| no_filing_found | 105 | 103 |
| cik_never_resolved | 65 | 30 |
| year_specific_no_match | 51 | 29 |
| cik_candidate_never_filed | 13 | 11 |

Identification is now essentially solved: 84 rows unresolved, down from 2,946.
The largest remaining bucket is **extraction** (188), which is the one this
project deliberately stopped optimising -- each remaining filer needs bespoke
handling and the failure mode is silently capturing the wrong text. Of
`no_filing_found`, 51 are 2026 filings not yet due.

CRSP would still settle the last handful faster, but it is no longer worth
buying for this dataset.

---

## 2026-08-18 (audit) — Grilling the result, and finding a bug I introduced

Coverage 96.0% -> **95.9%** (Item 1A 10102 -> 10088). The count went down
because three attributions were wrong and one of my own verification patterns
was producing false positives. A coverage number cannot detect either: a row
pointing at the wrong company's 10-K looks exactly like a correct one and
*inflates* the total. That asymmetry is why this audit was worth running.

### Finding 1: Merck's 2006-2009 risk factors were Schering-Plough's

The detector was cheap and general: **does one (cik, year) serve more than one
ticker?** 56 pairs did. Most are legitimate dual-class listings (GOOG/GOOGL,
FOX/FOXA, DISCA/DISCK, NWS/NWSA, UA/UAA), and those always share a ticker
prefix. Three pairs did not:

  - **MRK + SGP on CIK 310158, 2006-2009.** 310158 is Merck today but was
    SCHERING PLOUGH CORP until the November 2009 reverse merger, in which
    Schering-Plough was the legal acquirer, renamed itself Merck & Co and
    kept its own CIK. Real Merck was CIK 64978 (now Merck Sharp & Dohme).
    So MRK spent four years on Schering-Plough's filings.
  - **CTAS + SRCL on CIK 723254, 2015** (see finding 2).
  - **MDLZ + KRFT on CIK 1103982, 2013.** Mondelez retained the original
    Kraft Foods Inc CIK through the 2012 split; Kraft Foods Group is the new
    registrant, CIK 1545158.

MRK is pitfall #2 in a form the per-year check cannot catch: `validate_year`
confirms the CIK **filed a 10-K near that year**, which was true, but not that
it **held the ticker** then. This is NOT a regression from the per-year fix --
the old stint-anchored check would have accepted it too -- but it is a real
limit of the method, and worth stating plainly rather than leaving implied.

### Finding 2: a false positive in the cover-page verifier (mine, this session)

`SRCL 2015` resolved to **Cintas**, via `fulltext_coverpage_verified` -- the
verification layer built specifically to prevent wrong-company attribution
approved a wrong-company attribution.

Cause: Cintas's 2015 10-K contains "...agreement to sell its investment in the
Shred-it Partnership to Stericycle, Inc. (Nasdaq: SRCL)...", and
`lib/cover_page`'s `(NYSE|Nasdaq): XYZ` pattern ran over the whole document,
so Cintas "declared" SRCL. Mondelez's spin-off 10-K names KRFT the same way.

The asymmetry I had missed: a company states its OWN symbol as "under the
symbol X" or in the Section 12(b) table. The parenthetical "(Nasdaq: X)" form
is how it names SOMEBODY ELSE -- an acquirer, a target, a spun-off sibling.
That pattern is now confined to the cover-page region (first 15,000 chars),
where no third party appears; the other patterns still run over the whole
document. The 7-case cover_page suite still passes unchanged.

**Consequence:** `data/raw/coverpage_symbols.json` (7,652 filings) had been
computed with the buggy pattern, so every entry was suspect and was discarded
rather than reused. Rebuilding it is most of why the audit rerun was slow.
Net effect on resolution: `fulltext_coverpage_verified` 950 -> 893.

### The guard: 09_validate_resolution.py

A one-off fix does not stop recurrence, so the detector is now a pipeline
stage with five hard invariants (non-zero exit, not advisory):

1. **Unrelated tickers sharing a CIK+year.** Dual-class pairs share a prefix;
   MRK/SGP and CTAS/SRCL do not. This check found all three bugs above.
   `CPRI/KORS` is explicitly allowlisted -- Michael Kors renamed to Capri, so
   both tickers point at the same correct filings; that is duplication, not
   misattribution.
2. Overlapping override ranges. `find_override` returns the first match, so an
   overlap would make resolution silently order-dependent.
3. No stored section below the plausibility floor.
4. Every universe row accounted for in `missing_records` (pitfall #4).
5. No extraction file claimed by two rows.

All five now pass.

### The override year-check earned its keep again

Two override years were rejected at resolution time and recorded as
`manual_override_year_rejected`: MXIM 2007 and NAV 2006, where the proposed
CIK filed no 10-K within 400 days of that year's snapshot. Those are ranges I
wrote too wide, caught automatically and surfaced as visible gaps rather than
silent misattribution -- the same guard that caught BHGE earlier.

### What this says about the method

Every bug found in this dataset has been of one shape: **something that looks
like data but isn't**. Stub extractions counted as risk factors, a wrong
company's 10-K counted as the right one, a network failure counted as a
negative result. None are visible in a coverage number; all inflate it. The
counts that go DOWN after an audit are the ones to trust.

---

## 2026-08-23 — Grade-1 recovery, and a stale table under the chart

Two separate things, and the second is the bigger one.

### The tariff table was 20 days stale

`tariffs_by_year_sector` was generated 2026-08-03 21:56. `risk_factors_index`
was rebuilt 2026-08-21 22:54. Every chart drawn from that table between those
dates was reading the corpus as it stood **before** the CIK-resolution work
that took coverage from 71% to 96%.

That work recovered early years hardest, so the staleness was not a uniform
scaling — it bit 2006 far harder than 2025. Re-running `06` against the
current corpus:

| Year | Stale | Rebuilt | Stale rate | Rebuilt rate |
|---|---|---|---|---|
| 2006 | 59 | 114 | 14.0% | 27.1% |
| 2025 | 389 | 431 | 78.0% | 86.0% |

**The old subtitle — "not a single year seeing a decrease" — was an artifact
of this.** Against the rebuilt series the count falls in 2008, 2015, 2022,
2024 and (incompletely) 2026; the share falls in six years. The monotone rise
was manufactured by understating the early years by roughly half. The growth
is real and still large — 27% to 86% — but it is ~3.2x, not the ~5.9x the
stale series implied.

Nothing about the stale table was visible in the chart. It had no timestamp on
it, the shape looked plausible, and the claim it supported was the most
quotable sentence on the page. The lesson is the standing one in this file,
one level up: a derived table can be wrong in the same silent, count-inflating
way an extraction can, and `_meta.generated_at` is the check that catches it.

### Grade-1 recovery: +54 rows, after four wrong versions

`03c_recover_bare_sections.py` recovers sections printed under a "Risk
Factors" heading that the Item-numbered markers cannot bound — FedEx (in the
primary document all along), U.S. Bancorp, Wells Fargo. Decisions taken before
building it: gaps only, so the 10,088 existing sections are untouched by
construction; grade 1 only, so a safe-harbour cautionary statement standing in
for Item 1A is recorded as a different disclosure rather than counted as one;
and exactly one surviving candidate per accession, with ambiguity referred to
a human instead of resolved by a tiebreak.

**Those three rules were not enough, and the way they failed is the point.**
The first run recovered 75 rows. Reading them showed ~30 were not short
sections but wrong text: JNJ 2006 was 3,092 chars of Item 1B, Properties and
Legal Proceedings; CVG 2008 a pointer stub plus Properties; UPS 2006 a
forward-looking-statements bullet list. Uniqueness catches ambiguity between
documents. It says nothing about whether the one survivor stops in the right
place.

Four guards, each found by reading output that had already passed every
previous guard:

1. **No Item-numbered heading inside the capture.** An internal `Item 2` /
   `Item 7A` proves over-run. Note the pattern needs `[2-9]\s*[.\(]?\s*[ab]?`,
   not `[2-9]\b` — there is no word boundary between "7" and "A", so the
   first version could not see "ITEM 7A." at all, and UHS 2020 passed with
   the MD&A appended and that heading on its last line.
2. **Floor at 11,595 chars** — the 5th percentile of sections produced by the
   trustworthy method (median 49,983). The existing 1,500 floor was calibrated
   for marker extraction, which is bounded by two corroborating Item headings.
   This strategy has one enumerated end marker and nothing corroborating it,
   so it must clear a stricter bar than the method it stands in for.
3. **Must end at a sentence.** Citigroup 2021 stopped at "...see Notes 1 and
   15 to the" because "Consolidated Financial Statements" followed as a styled
   cross-reference and `html_to_text` puts every element on its own line.
4. **Must start at a sentence.** Where the nested-candidate collapse falls
   back to a later running-header repeat, the result is correct at the end and
   80,000 chars short at the front: WFC 2021 came back at 20,133 against
   ~100,000 for every neighbouring year, opening "(continued) example, if
   market interest rates increase...". It passed guards 1-3.

**Nested candidates are one section, not rivals.** Wells Fargo's exhibit
reprints "Risk Factors" as a running page header, so 2018 yields eight starts
at ~15,000-char intervals all running to the same terminator — each a suffix
of the one before. Collapsing to the earliest start per end is not the "take
the longest" tiebreak rejected earlier; that would choose between different
spans of text, this chooses a section over a suffix of itself. Candidates with
*different* ends are still ambiguity and still refused.

**Terminators are enumerated, not inferred**, and the list needed two rounds.
"controls and procedures" never fired because the heading reads "DISCLOSURE
CONTROLS AND PROCEDURES" and matching is line-anchored. `management's report
on internal control` never fired because filings render the possessive as
U+2019 and the pattern had an ASCII apostrophe — a miss that does not fail,
it just runs the capture on to the next terminator it can match, which is how
Citigroup 2024 came back 390,451 chars with a correct opening and KPMG's audit
opinion on the end.

**Seven rows were rejected by hand** (`REJECTED_TAIL_OVERRUN`): Citigroup
2023-2026, BNY Mellon 2006-2007, Constellation 2024. All open correctly and
over-run at the tail into material each filer lays out differently. Fixing
them needs per-filer terminators, which is the parser checkpoint 4 declined to
write. **Citigroup therefore ends up entirely absent, 2025 and 2026
included** — the contamination is ~1% of a 386,000-char section and contains
no tariff mentions, so admitting them would barely move the chart. That is an
argument for keeping them and it is the argument this project has refused
every previous time. Reversing it means deleting from that list, not loosening
a guard.

Counts across the five runs: 75, 62, 78, 65, 63, then 61 after the hand
rejections. **The highest number came from the version storing Properties
sections as risk factors.** Extraction 10,088 -> **10,142**; coverage 95.9% ->
**96.4%**. `09_validate_resolution.py` passes all five invariants;
`test_parser_regression.py` reports 149 identical, 0 changed.

### Missingness now distinguishes "no answer" from "answered elsewhere"

`no_item_1a_extracted` carries a sub_reason: `no_item_1a_incorporated` (73
rows — the filer answered Item 1A by reference, a fact about the filer),
`no_item_1a_found` (55), `no_item_1a_tail_overrun` (7). All remain gaps for
coverage; none appear in the tariff bars.

### Chart

2026 is drawn solid at what is counted with a hatched cap for what is pending,
sized at 49 pending filers x the observed 93% rate rather than the prior
build's one-mention-per-filer assumption. The subtitle is a magnitude built
from the data, not a streak. The Trump 1.0/2.0 anchors were hardcoded at
y = 145 and y = 330 against the stale series and both sat inside their bars
once it was rebuilt; they are now read off `bar_totals`.
