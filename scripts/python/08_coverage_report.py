#!/usr/bin/env python3
"""By-year coverage: universe -> 10-K found -> Item 1A extracted.

Answers the question the missingness work exists to support: for each year,
how many companies were in the index, how many of them we located a 10-K
for, and how many of those yielded an Item 1A section.

Two denominators are reported on purpose. Item 1A extraction is highly
reliable (~99-100% of located filings), so a percentage against filings
found says almost nothing about coverage. The number that actually moves is
the share of the INDEX reached at all, which is dominated by CIK resolution
difficulty and rises steeply over time as delisted-company identities get
easier to pin down. Reporting only the first would make a resolution
artifact look like a change in corporate filing behavior.

Writes output/coverage_by_year.csv and prints the table.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_PATH = ROOT / "output" / "coverage_by_year.csv"

QUERY = """
WITH universe AS (
    SELECT year, ticker FROM sp500_universe_raw GROUP BY 1, 2
), filings AS (
    SELECT year, ticker FROM filing_universe GROUP BY 1, 2
), extracted AS (
    SELECT year, ticker, max(CAST(has_item_1a AS INT)) AS has_1a
    FROM risk_factors_index GROUP BY 1, 2
)
SELECT
    u.year,
    count(*)                                                   AS n_companies,
    sum(CASE WHEN f.ticker IS NOT NULL THEN 1 ELSE 0 END)      AS n_10k_found,
    sum(coalesce(e.has_1a, 0))                                 AS n_item_1a,
    round(100.0 * sum(coalesce(e.has_1a, 0))
          / nullif(sum(CASE WHEN f.ticker IS NOT NULL THEN 1 ELSE 0 END), 0), 1)
                                                               AS pct_of_10ks,
    round(100.0 * sum(coalesce(e.has_1a, 0)) / count(*), 1)    AS pct_of_universe
FROM universe u
LEFT JOIN filings f USING (year, ticker)
LEFT JOIN extracted e USING (year, ticker)
GROUP BY u.year
ORDER BY u.year
"""


def main():
    con = connect()
    df = con.execute(QUERY).df()

    totals = {
        "year": "TOTAL",
        "n_companies": df.n_companies.sum(),
        "n_10k_found": df.n_10k_found.sum(),
        "n_item_1a": df.n_item_1a.sum(),
        "pct_of_10ks": round(100.0 * df.n_item_1a.sum() / df.n_10k_found.sum(), 1),
        "pct_of_universe": round(100.0 * df.n_item_1a.sum() / df.n_companies.sum(), 1),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)

    print(df.to_string(index=False))
    print(f"\nTOTAL  companies={totals['n_companies']}  10-Ks={totals['n_10k_found']}  "
          f"Item 1A={totals['n_item_1a']}  "
          f"({totals['pct_of_10ks']}% of 10-Ks, {totals['pct_of_universe']}% of universe)")

    print("\nUnresolved-CIK rows by resolution method (the binding constraint on column 2):")
    print(con.execute("""
        SELECT resolution_method, count(*) AS n_rows, count(DISTINCT ticker) AS n_tickers
        FROM cik_resolution GROUP BY 1 ORDER BY 2 DESC
    """).df().to_string(index=False))

    con.close()
    print(f"\nWrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
