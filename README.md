# EDGAR 10-K Risk Factors Dataset — S&P 500, 2006–present

Item 1A "Risk Factors" text extracted from 10-K filings for every point-in-time
S&P 500 constituent, 2006 (first mandatory year for Item 1A) through the present.

This is a from-scratch rebuild of project `13_EDGAR_10K_Risk_Sentiment`, done in a
fresh project folder to avoid carrying over stale caches and architectural issues
found during that build (see `decisions.md` for what changed and why, and
`../13_EDGAR_10K_Risk_Sentiment/REBUILD_PROMPT.md` for the original handoff brief).

## Supports two studies
1. How companies characterized risk around the 2025 Iran-Israel-US conflict.
2. How risk-factor sentiment/language has shifted over ~20 years.

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
