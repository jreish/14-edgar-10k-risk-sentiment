#!/usr/bin/env python3
"""Pin the extraction behaviours that were expensive to work out.

Each case is a real filing whose structure defeated an earlier version of the
parser. Run after any change to lib/risk_factor_parser.py, alongside
test_parser_regression.py (which proves nothing that already worked broke).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib.edgar import get
from lib.risk_factor_parser import (
    MIN_PLAUSIBLE_SECTION_CHARS,
    _extract_by_markers,
    extract_item_1a,
    html_to_text,
    is_implausible_section,
)

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def check(label, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {label}{('  -- ' + detail) if detail else ''}")
    return bool(condition)


def main():
    ok = []

    # --- Running-header genre -------------------------------------------
    # BNY Mellon's Annual Report exhibit carries no SEC Item numbering, so it
    # has 28 start markers and zero end markers. Its section is delimited by a
    # repeated "Risk Factors (continued)" page header instead.
    text = html_to_text(get(f"{ARCHIVES}/1390777/000139077716000204/bk4q201510-kex131.htm").text)
    ok.append(check("BK 2016 ex13: primary marker strategy finds nothing",
                    _extract_by_markers(text) is None))
    section = extract_item_1a(text)
    ok.append(check("BK 2016 ex13: running-header strategy extracts the section",
                    section is not None and len(section) > 100_000,
                    f"{len(section) if section else 0} chars"))
    ok.append(check("BK 2016 ex13: starts on the real opening sentence",
                    section is not None and section.startswith("Risk Factors")
                    and "Making or continuing an investment" in section[:200]))
    ok.append(check("BK 2016 ex13: does not bleed into the next section",
                    section is not None
                    and "Recent Accounting Developments" not in section[-400:]
                    and section.rstrip().endswith(".")))

    # --- Incorporation-by-reference stubs --------------------------------
    # These sit between valid start and end markers, so the primary strategy
    # extracts them happily. They are pointers to Item 1A, not Item 1A, and
    # storing one is a false positive -- worse than a gap, because it enters a
    # language study as though it were real disclosure.
    stubs = [
        "Information in response to this Item 1A can be found in the Company's 2014 Annual "
        "Report on pages 155 to 165 under the heading “Risk Factors.” That information "
        "is incorporated into this report by reference.",
        "Information regarding our risk factors is included in the Financial Review section "
        "of our Annual Report to Stockholders and is incorporated herein by reference.",
        "We incorporate our disclosure related to risk factors into this section by reference.",
        "For identification and discussion of the most significant risks applicable to the "
        "Company, see the Risk Factors section of the Annual Report.",
        "The Company is a smaller reporting company as defined by Rule 12b-2 and is not "
        "required to provide the information under this item.",
    ]
    for stub in stubs:
        ok.append(check(f"stub rejected: {stub[:52]}...", is_implausible_section(stub)))

    # A real section must survive. Length is the discriminator, so this guards
    # the floor from being raised into genuine content.
    real = "Our operations involve various risks. " * 60  # ~2,200 chars
    ok.append(check("a genuine short section is kept",
                    not is_implausible_section(real), f"{len(real)} chars"))
    ok.append(check(f"floor is {MIN_PLAUSIBLE_SECTION_CHARS} chars",
                    MIN_PLAUSIBLE_SECTION_CHARS == 1500))

    print(f"\n{sum(ok)}/{len(ok)} passed")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
