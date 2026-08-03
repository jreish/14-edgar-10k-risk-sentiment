#!/usr/bin/env python3
"""Keyword/topic analysis over extracted Item 1A text -- pure text
processing, no network calls (everything's already on disk from
checkpoint 4). Writes three DuckDB tables:

  - keyword_hits: per-filing hit counts across Iran-conflict-related
    categories (kept as separate columns, not one blanket "Iran" flag,
    since a filing can trip "energy_crisis" or "middle_east" for reasons
    unrelated to Iran specifically -- e.g. general commodity commentary).
  - topic_trends_by_year: year x topic (iran / tariffs / ukraine) count of
    filings whose Item 1A mentions the term at least once (filing-level
    presence, not raw token count, so a single very-repetitive filing
    can't dominate a year's count).
  - tariffs_by_year_sector: year x sector tariff-mention filing counts --
    feeds the tariffs-by-sector reference chart in checkpoint 8.

Adapted from project 13's equivalent scripts (reviewed, not blindly
copied -- reused because the keyword methodology itself isn't
identity-sensitive and was already sound), consolidated into one script
and writing to DuckDB instead of CSV per this project's storage convention.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect, record_meta

ROOT = pathlib.Path(__file__).resolve().parents[2]

IRAN_KEYWORDS = {
    "iran": re.compile(r"\biran(?:ian)?\b", re.IGNORECASE),
    "hormuz": re.compile(r"\bhormuz\b", re.IGNORECASE),
    "israel": re.compile(r"\bisrael(?:i)?\b", re.IGNORECASE),
    "middle_east": re.compile(r"\bmiddle\s+east\b", re.IGNORECASE),
    "energy_crisis": re.compile(r"\benergy\s+crisis\b", re.IGNORECASE),
}
SNIPPET_PRIORITY = ["iran", "hormuz", "israel", "middle_east", "energy_crisis"]
SNIPPET_RADIUS = 160

TOPICS = {
    "iran": re.compile(r"\biran(?:ian)?\b", re.IGNORECASE),
    "tariffs": re.compile(r"\btariff(?:s)?\b", re.IGNORECASE),
    "ukraine": re.compile(r"\bukrain(?:e|ian)?\b", re.IGNORECASE),
}


def make_snippet(text: str, match: re.Match) -> str:
    s = max(0, match.start() - SNIPPET_RADIUS)
    e = min(len(text), match.end() + SNIPPET_RADIUS)
    snippet = re.sub(r"\s+", " ", text[s:e]).strip()
    return snippet


def main():
    con = connect()
    filings = con.execute("""
        SELECT r.year, r.ticker, r.cik, r.file_path, s.sector
        FROM risk_factors_index r
        LEFT JOIN company_sectors s ON r.cik = s.cik
        WHERE r.has_item_1a = true
    """).fetchall()
    print(f"Analyzing {len(filings)} extracted filings...")

    keyword_rows = []
    topic_counts = {}  # (year, topic) -> filing count
    tariff_sector_counts = {}  # (year, sector) -> filing count

    for year, ticker, cik, file_path, sector in filings:
        text = (ROOT / file_path).read_text(errors="ignore")

        hits = {name: list(p.finditer(text)) for name, p in IRAN_KEYWORDS.items()}
        if any(hits.values()):
            snippet = ""
            for name in SNIPPET_PRIORITY:
                if hits[name]:
                    snippet = make_snippet(text, hits[name][0])
                    break
            keyword_rows.append((
                year, ticker, cik, sector,
                *[len(hits[name]) for name in IRAN_KEYWORDS],
                snippet,
            ))

        for topic, pattern in TOPICS.items():
            if pattern.search(text):
                key = (year, topic)
                topic_counts[key] = topic_counts.get(key, 0) + 1
                if topic == "tariffs":
                    skey = (year, sector or "Unclassified")
                    tariff_sector_counts[skey] = tariff_sector_counts.get(skey, 0) + 1

    con.execute("DROP TABLE IF EXISTS keyword_hits")
    con.execute(f"""
        CREATE TABLE keyword_hits (
            year INTEGER, ticker VARCHAR, cik BIGINT, sector VARCHAR,
            {", ".join(f"{k} INTEGER" for k in IRAN_KEYWORDS)},
            snippet VARCHAR
        )
    """)
    con.executemany(
        f"INSERT INTO keyword_hits VALUES (?, ?, ?, ?, {', '.join('?' for _ in IRAN_KEYWORDS)}, ?)",
        keyword_rows,
    )
    record_meta(
        con, "keyword_hits", script="06_keyword_topic_analysis.py",
        source_urls=["derived from risk_factors_index text"],
        column_descriptions={
            "year": "Study year", "ticker": "Ticker", "cik": "CIK", "sector": "Sector",
            **{k: f'Count of "{k}" pattern matches in this filing\'s Item 1A text' for k in IRAN_KEYWORDS},
            "snippet": "First matching keyword's surrounding text (priority: iran, hormuz, israel, middle_east, energy_crisis)",
        },
        row_count=len(keyword_rows),
    )

    con.execute("DROP TABLE IF EXISTS topic_trends_by_year")
    con.execute("CREATE TABLE topic_trends_by_year (year INTEGER, topic VARCHAR, n_filings INTEGER)")
    con.executemany(
        "INSERT INTO topic_trends_by_year VALUES (?, ?, ?)",
        [(y, t, n) for (y, t), n in topic_counts.items()],
    )
    record_meta(
        con, "topic_trends_by_year", script="06_keyword_topic_analysis.py",
        source_urls=["derived from risk_factors_index text"],
        column_descriptions={
            "year": "Study year", "topic": "iran / tariffs / ukraine",
            "n_filings": "Number of filings that year whose Item 1A mentions the topic at least once",
        },
        row_count=len(topic_counts),
    )

    con.execute("DROP TABLE IF EXISTS tariffs_by_year_sector")
    con.execute("CREATE TABLE tariffs_by_year_sector (year INTEGER, sector VARCHAR, n_filings INTEGER)")
    con.executemany(
        "INSERT INTO tariffs_by_year_sector VALUES (?, ?, ?)",
        [(y, s, n) for (y, s), n in tariff_sector_counts.items()],
    )
    record_meta(
        con, "tariffs_by_year_sector", script="06_keyword_topic_analysis.py",
        source_urls=["derived from risk_factors_index text and company_sectors"],
        column_descriptions={
            "year": "Study year", "sector": "GICS-style sector",
            "n_filings": "Number of filings that year, in this sector, whose Item 1A mentions tariffs at least once",
        },
        row_count=len(tariff_sector_counts),
    )
    con.close()

    print(f"\n{len(keyword_rows)} filings had at least one Iran-related keyword hit.")
    print("\nTopic trends (total filings mentioning, all years):")
    from collections import defaultdict
    totals = defaultdict(int)
    for (y, t), n in topic_counts.items():
        totals[t] += n
    for topic, total in sorted(totals.items(), key=lambda x: -x[1]):
        print(f"  {topic}: {total}")


if __name__ == "__main__":
    main()
