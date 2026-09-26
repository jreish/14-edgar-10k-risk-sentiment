# EDGAR 10-K Risk Factors Dataset — S&P 500, 2006–present

Item 1A "Risk Factors" text extracted from 10-K filings for every point-in-time
S&P 500 constituent, 2006 (first mandatory year for Item 1A) through the present.

**Coverage:** risk-factor text for 10,164 of 10,524 constituent company-years
(96.6%), 2006 through the filings due so far in 2026. See
[`output/coverage_by_year.csv`](output/coverage_by_year.csv).

## Supports two studies
1. How companies characterized risk around the 2025 Iran-Israel-US conflict.
2. How risk-factor sentiment/language has shifted over ~20 years.

## Data quality & major fixes
The first complete build covered 71% of company-years (48% in 2006). Nearly all
of the gap came from identification (working out which company held a ticker
in a given year), not from finding or parsing filings. The fixes below raised
coverage to 96.6%. Several of them *lowered* the count, because they removed
rows that looked like data but weren't.

**Identifying the company behind each ticker**
- **Continuity check tested against the wrong date.** It asked whether a
  company was filing when its index membership began, usually 1996, so any
  company that reorganized later (Oracle, FedEx, Comcast, and others) failed
  every year. It now tests the year being studied. (+500 rows)
- **Verification instead of ranking.** Full-text search for a delisted
  company's ticker was right about 70% of the time. For example, Trump
  Entertainment's filings say "TER" more often than Teradyne's. Now each
  candidate must state the ticker in its own filing, and the search checks
  which ticker a filing states rather than searching for the ticker text, so
  short symbols like `X` and `LM` work too. 1,142 wrong matches were ruled
  out. (+~1,700 rows)
- **Manual overrides need evidence.** Hand-proposed ticker→CIK mappings are
  accepted only when SEC filings confirm both the identity and the date range.
  About one in five proposals was rejected.
- **Audit for two tickers mapped to one company in the same year.** This
  caught Merck's 2006–09 rows, which were actually Schering-Plough's, plus
  Cintas/Stericycle and Mondelez/Kraft. It also caught a verifier bug that
  read "(Nasdaq: SRCL)" in Cintas's 10-K as Cintas claiming SRCL. These
  checks now run on every build in `09_validate_resolution.py`.

**Extracting Item 1A**
- **Pointer stubs rejected.** 99 stored "sections" were one-line notes that
  the risk factors were incorporated by reference (Wells Fargo's entire run
  was stored this way). Anything under 1,500 characters now counts as missing
  and is retried against the referenced exhibit.
- **Unnumbered "RISK FACTORS" headings recovered** (FedEx, Wells Fargo,
  U.S. Bancorp), with guards against sections that run on into the auditor's
  report or the rest of the 10-K. Seven sections that were only mostly
  correct, including Citigroup, were left missing rather than kept.

**Timing**
- **Late filings are labelled, not called unfiled.** Seven 2006 filers
  delayed by the stock-option backdating investigations were listed as
  "past due, not filed". They are now labelled as late filings, and a
  company-year with no new 10-K carries forward the one still in force,
  looking back at most two years. These 12 rows are flagged
  `source_location = 'carried_forward'`.

**Analysis**
- The tariff chart had been drawn from a table built before the coverage
  fixes. Rebuilding it removed a "tariff mentions grew every year" pattern,
  since mentions fell in 2008, 2015, 2022 and 2024. Table provenance is now
  stamped in the `_meta` table.

`decisions.md` gives the reasoning, evidence and rejected alternatives for
each fix. `log.md` tells the same history in plain language.

## Structure
- `data/` — DuckDB database (source of truth) + extracted risk-factor `.txt` files.
  `data/raw/` (gitignored) holds cached raw API/filing responses.
- `scripts/python/` — data pipeline (fetch, resolve, extract, classify, analyze).
- `scripts/r/` — plotting only, reads from DuckDB.
- `output/` — rendered charts and CSV export convenience layer.
- `decisions.md` — non-obvious technical choices + rejected alternatives.
- `log.md` — same history, plain language.

## Setup
```
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```
SEC EDGAR requires a descriptive `User-Agent` header and enforces a 10 req/sec
rate limit; scripts run at ~9 req/sec. Set your own contact before running
fetch scripts — they stop with an error if it's missing:
```
export EDGAR_CONTACT="Jane Doe jane@example.com"
```

## License
Code is released under the [MIT License](LICENSE). The risk-factor text in
`data/clean/risk_factors/` is excerpted from companies' public 10-K filings on
SEC EDGAR.
