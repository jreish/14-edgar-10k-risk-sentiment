#!/usr/bin/env python3
"""How risk-factor language has shifted over ~20 years.

Two independent, transparent signals rather than importing an external
sentiment lexicon (the project's data sources are scoped to SEC/GitHub;
a finance-specific sentiment wordlist like Loughran-McDonald would be a
new, undocumented external dependency, so this uses a small curated word
list defined directly here instead):

  1. Section length over time (avg word count per filing per year) -- a
     well-documented general trend in 10-K disclosure is that risk
     sections have grown substantially longer/more detailed since 2006.
  2. Frequency (per 1,000 words, to normalize for #1's length growth) of
     a curated set of theme categories that plausibly shifted in
     prominence over the study window: general negative/uncertainty tone,
     cybersecurity, climate, pandemic, and inflation/supply-chain -- each
     its own column so a reader isn't stuck trusting one blended "tone"
     score.

This is a deliberately modest, inspectable methodology (not a claim of a
peer-reviewed sentiment instrument) -- the word lists are visible right
here in the script, not a black-box import.
"""
import pathlib
import re
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect, record_meta

ROOT = pathlib.Path(__file__).resolve().parents[2]

THEME_WORDS = {
    "negative_uncertainty": [
        "adverse", "adversely", "decline", "declined", "risk", "risks", "uncertain",
        "uncertainty", "volatil", "disrupt", "loss", "losses", "litigation",
        "material weakness", "materially adverse",
    ],
    "cybersecurity": ["cybersecurity", "cyber attack", "cyberattack", "data breach", "ransomware"],
    "climate": ["climate change", "greenhouse gas", "carbon emission", "extreme weather"],
    "pandemic": ["pandemic", "covid-19", "covid", "epidemic", "public health emergency"],
    "inflation_supply_chain": ["inflation", "supply chain", "supply-chain", "labor shortage"],
}
THEME_PATTERNS = {
    theme: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
    for theme, words in THEME_WORDS.items()
}

WORD_RE = re.compile(r"[A-Za-z']+")


def main():
    con = connect()
    filings = con.execute("""
        SELECT year, file_path FROM risk_factors_index WHERE has_item_1a = true
    """).fetchall()
    print(f"Analyzing language trends across {len(filings)} filings...")

    by_year_wordcount = defaultdict(list)
    by_year_theme_counts = defaultdict(lambda: defaultdict(int))
    by_year_theme_words = defaultdict(int)

    for year, file_path in filings:
        text = (ROOT / file_path).read_text(errors="ignore")
        words = WORD_RE.findall(text)
        n_words = len(words)
        by_year_wordcount[year].append(n_words)
        by_year_theme_words[year] += n_words
        for theme, pattern in THEME_PATTERNS.items():
            by_year_theme_counts[year][theme] += len(pattern.findall(text))

    results = []
    for year in sorted(by_year_wordcount):
        counts = by_year_wordcount[year]
        avg_words = sum(counts) / len(counts)
        total_words = by_year_theme_words[year]
        row = {"year": year, "n_filings": len(counts), "avg_word_count": round(avg_words, 1)}
        for theme in THEME_WORDS:
            per_1000 = by_year_theme_counts[year][theme] / total_words * 1000 if total_words else 0.0
            row[f"{theme}_per_1000_words"] = round(per_1000, 3)
        results.append(row)

    con.execute("DROP TABLE IF EXISTS language_trends_by_year")
    cols = ["year INTEGER", "n_filings INTEGER", "avg_word_count DOUBLE"] + [
        f"{theme}_per_1000_words DOUBLE" for theme in THEME_WORDS
    ]
    con.execute(f"CREATE TABLE language_trends_by_year ({', '.join(cols)})")
    col_names = ["year", "n_filings", "avg_word_count"] + [f"{t}_per_1000_words" for t in THEME_WORDS]
    con.executemany(
        f"INSERT INTO language_trends_by_year VALUES ({', '.join('?' for _ in col_names)})",
        [tuple(r[c] for c in col_names) for r in results],
    )
    record_meta(
        con, "language_trends_by_year", script="07_language_trends.py",
        source_urls=["derived from risk_factors_index text"],
        column_descriptions={
            "year": "Study year", "n_filings": "Number of filings analyzed that year",
            "avg_word_count": "Average Item 1A word count across filings that year",
            **{f"{t}_per_1000_words": f"Occurrences of the {t} word list per 1,000 words of Item 1A text that year"
               for t in THEME_WORDS},
        },
        row_count=len(results),
    )
    con.close()

    print("\nAvg word count by year (sample):")
    for r in results[::5]:
        print(f"  {r['year']}: {r['avg_word_count']:.0f} words avg, {r['n_filings']} filings")


if __name__ == "__main__":
    main()
