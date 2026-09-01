#!/usr/bin/env python3
"""Recover risk-factor sections printed under a heading with no Item numbering.

The residual extraction failures are not 50 separate filer quirks. They are
one habit: the company prints a real "Risk Factors" section somewhere the
Item-numbered markers cannot bound -- in the annual-report portion of the
10-K after Item numbering stops (FedEx), or in the Annual Report exhibit the
10-K points at (U.S. Bancorp, Wells Fargo, Citigroup). See
lib/risk_factor_parser.bare_section_candidates for how the section end is
found and why it is enumerated rather than inferred.

Three rules, decided 2026-08-23 and each load-bearing:

  Gaps only. Runs against has_item_1a = false rows and nothing else, so
  every one of the 10,088 sections already extracted is untouched. Any
  movement in the tariff series is "we found more filings", never "we
  changed how filings are read".

  Grade 1 only. A section headed "Risk Factors" counts. A safe-harbor
  cautionary statement standing in for Item 1A does not (Johnson & Johnson's
  Exhibit 99: legally the Item 1A response, substantively a different
  document, and it mentions tariffs zero times). Risk discussion folded into
  MD&A under a mixed heading does not either (Eastman Chemical's
  "Forward-Looking Statements and Risk Factors" -- real content, but its
  boundaries are a guess). Both are recorded as their own status rather than
  left indistinguishable from a filing we could not find.

  Exactly one survivor. Candidates are pooled across every document in the
  accession and the row is only filled when precisely one clears both guards
  in bare_section_candidates. Two survivors is ambiguity, and ambiguity is
  resolved by a human reading them, not by "take the longest" -- the rule
  that works until it doesn't and then fails silently by storing the wrong
  text.

Uniqueness alone was not enough. A first run without the length and
internal-Item-heading guards recovered 75 rows, of which reading the output
showed ~30 were wrong text rather than short text -- see the guard notes in
lib/risk_factor_parser.py. Those two guards are the difference between this
script and that one.

Rows left missing are separated into two states, because "the company
answered Item 1A by pointing somewhere else" and "we could not find a
section" are different facts about the world and only the first is a
statement about the filer:

  no_item_1a_incorporated  -- the 10-K carries a real Item 1A heading whose
    body is a pointer, so the company did respond; the response is either in
    a document this strategy could not bound, or is a safe-harbour cautionary
    statement standing in for risk factors (Johnson & Johnson).
  no_item_1a_found         -- no Item 1A heading and no bounded section.

Both remain gaps for coverage purposes. Neither appears in the tariff bars.

Writes recovered sections to data/clean/risk_factors/ and prints an
ambiguity list for review.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.db import connect
from lib.edgar import get
from lib.risk_factor_parser import (
    MIN_PLAUSIBLE_SECTION_CHARS,
    _extract_by_markers,
    bare_section_candidates_from_html,
    html_to_text,
)

# other_documents() already ranks the accession's documents the way this
# strategy needs (Exhibit 13 first, then largest), and its reasoning about
# excluding the concatenated full-submission text file applies unchanged.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
_retry = __import__("03b_retry_missing_item_1a")
other_documents = _retry.other_documents

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "clean" / "risk_factors"

# Rows where the four guards pass but reading the section shows it does not
# stop where the risk factors stop. All of these OPEN correctly -- the first
# paragraph is the genuine one -- and over-run at the tail into material that
# follows: Citigroup into "Accounting Changes", BNY Mellon into the Code of
# Conduct discussion, Constellation into an MD&A note reference. Each would
# need a terminator specific to how that filer lays out its annual report,
# which is the per-filer parser this project decided in checkpoint 4 not to
# write -- the maintenance cost compounds and the failure mode is silent.
#
# Worth stating what this costs, because it is not small: Citigroup was one
# of the three banks that motivated this whole strategy, and it ends up
# entirely absent, including 2025 and 2026. The contamination is about 1% of
# a ~386,000-character section and is accounting boilerplate that contains no
# tariff mentions, so admitting these rows would barely move the chart. That
# is an argument for keeping them, and it is exactly the argument this project
# has refused every previous time: a row that is 99% right is still a row
# whose contents are not what the column says they are, and nothing
# downstream can tell the difference. Reversing this decision means deleting
# from this list, not loosening a guard.
REJECTED_TAIL_OVERRUN = {
    ("C", 2023), ("C", 2024), ("C", 2025), ("C", 2026),
    ("BK", 2006), ("BK", 2007),
    ("CEG", 2024),
}

# Enough documents to reach the Annual Report exhibit without walking all 172
# of a large accession. other_documents puts the likely ones first.
MAX_DOCUMENTS = 12


def is_pointer_stub(raw_html: str) -> bool:
    """True if this document answers Item 1A with a cross-reference.

    _extract_by_markers pairs two corroborating Item headings, so what it
    returns here IS the filer's whole Item 1A response; the floor is what
    normally discards it. Below the floor and it is a pointer, not a section
    -- which is a fact about the company's filing habit, worth keeping.
    """
    try:
        section = _extract_by_markers(html_to_text(raw_html))
    except Exception:
        return False
    return section is not None and len(section) < MIN_PLAUSIBLE_SECTION_CHARS


def candidates_for(cik: int, accession: str, primary_document: str):
    """(sections, saw_pointer_stub) for the whole accession."""
    accn = accession.replace("-", "")
    found = []
    saw_stub = False
    documents = [primary_document] + other_documents(cik, accession, primary_document)[:MAX_DOCUMENTS]
    for name in documents:
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{name}"
        try:
            response = get(url)
            if response.status_code != 200:
                continue
            if name == primary_document and is_pointer_stub(response.text):
                saw_stub = True
            sections = bare_section_candidates_from_html(response.text)
        except Exception:
            continue
        found.extend((section, name) for section in sections)
    return found, saw_stub


def main():
    con = connect()
    misses = con.execute("""
        SELECT year, ticker, cik, accession_number, primary_document
        FROM risk_factors_index WHERE has_item_1a = false
        ORDER BY ticker, year
    """).fetchall()
    print(f"Grade-1 recovery against {len(misses)} extraction failures...")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n_recovered = 0
    ambiguous = []
    still_missing = []

    n_incorporated = 0
    for year, ticker, cik, accession, primary_document in misses:
        found, saw_stub = candidates_for(cik, accession, primary_document)

        if (ticker, year) in REJECTED_TAIL_OVERRUN:
            still_missing.append((ticker, year))
            con.execute("""
                UPDATE risk_factors_index SET fetch_error = ?
                WHERE year = ? AND ticker = ?
            """, ["no_item_1a_tail_overrun: section found but does not stop where "
                  "the risk factors stop; see REJECTED_TAIL_OVERRUN", year, ticker])
            continue

        if len(found) == 1:
            section, document = found[0]
            out_path = OUT_DIR / f"{ticker}_{year}.txt"
            out_path.write_text(section)
            con.execute("""
                UPDATE risk_factors_index
                SET has_item_1a = true, file_path = ?, char_count = ?,
                    fetch_error = NULL,
                    source_location = 'bare_heading', source_document = ?
                WHERE year = ? AND ticker = ?
            """, [str(out_path.relative_to(ROOT)), len(section), document, year, ticker])
            n_recovered += 1
            print(f"  recovered {ticker} {year} from {document} ({len(section):,} chars)")
        elif len(found) > 1:
            ambiguous.append((ticker, year, [(len(s), d) for s, d in found]))
            con.execute("""
                UPDATE risk_factors_index
                SET fetch_error = ?
                WHERE year = ? AND ticker = ?
            """, [f"grade-1 ambiguous: {len(found)} plausible sections in this accession",
                  year, ticker])
        else:
            still_missing.append((ticker, year))
            n_incorporated += int(saw_stub)
            con.execute("""
                UPDATE risk_factors_index SET fetch_error = ?
                WHERE year = ? AND ticker = ?
            """, ["no_item_1a_incorporated: company answered Item 1A by reference; "
                  "target not boundable as a risk-factor section"
                  if saw_stub else
                  "no_item_1a_found: no Item 1A heading and no bounded section",
                  year, ticker])

    con.close()

    print(f"\nRecovered {n_recovered}/{len(misses)}.")
    if ambiguous:
        print(f"\n{len(ambiguous)} ambiguous -- two or more plausible sections, left as gaps for review:")
        for ticker, year, sizes in ambiguous:
            print(f"  {ticker} {year}: " + ", ".join(f"{n:,} chars in {d}" for n, d in sizes))
    print(f"\n{len(still_missing)} still missing: {n_incorporated} answered Item 1A by "
          f"reference, {len(still_missing) - n_incorporated} with no Item 1A heading at all.")


if __name__ == "__main__":
    main()
