"""Extract the Item 1A "Risk Factors" section from a 10-K filing document.

Adapted from project 13_EDGAR_10K_Risk_Sentiment's parser (reviewed, not
blindly copied -- this component isn't identity-sensitive like the CIK
resolution work, and its correctness is independently checkable by
inspecting extracted section length/content, so reusing edge cases already
found and fixed there is a legitimate efficiency gain, not corner-cutting).

10-Ks always contain a table of contents that mentions "Item 1A" and "Item 1B"
right next to each other, long before the real sections appear, and often a
running page header repeating "Item 1A" on every page throughout the section
(an artifact of the original paginated document). A naive "first occurrence
to next Item 1B" grabs the ToC; a naive "biggest gap between any Item 1A and
the next Item 1B/2" gets fooled by a stray early cross-reference sentence
(e.g. "...as discussed in Item 1A of this Form 10-K...") that isn't followed
by "Risk Factors" at all, but happens to sit far from the true end.

Some filings (Exxon's is a confirmed case) also quote "Item 1A. Risk Factors"
repeatedly as an inline cross-reference in unrelated prose ("As discussed in
'Item 1A. Risk Factors' in this report, ..."), and the same happens with
"Item 2. Properties" as a cross-reference within the risk factors text
itself while discussing reserves. Those matches satisfy a bare "heading
immediately follows item number" check too, so gap-maximization alone can
pick a cross-reference instead of the real heading.

Real headings always start a fresh line (block-level HTML elements produce a
newline via get_text); inline cross-references sit mid-sentence. So both the
start and end markers require being at the start of a line (allowing leading
whitespace), on top of requiring "Risk Factors" / "Unresolved Staff Comments"
/ "Properties" to immediately follow the item number - with per-letter
whitespace tolerance, because some renderers insert a stray line break
mid-word (observed: "RIS\nK FACTORS").

Some large, long-established filers (confirmed case: McDonald's) wrap their
glossy shareholder Annual Report as the 10-K body, with a separate "Form
10-K Cross-Reference Index" table mapping Item numbers to page numbers - the
actual section headings in the body are bare ("RISK FACTORS", "PROPERTIES"),
never prefixed with "Item 1A"/"Item 2" at all. So bare versions of the same
headings are included as fallback start/end candidates (still anchored to
line start, so they don't match mid-sentence prose like "...our properties
that are leased").

A further variant defeats every marker above, and needs its own strategy
(extract_running_header). Filers who incorporate Item 1A by reference put a
one-line cross-reference stub in the 10-K itself and the real text in a
separately-filed Annual Report exhibit -- 76 of 86 extraction failures were
this shape. 03b_retry_missing_item_1a.py already finds the right exhibit,
but the exhibit still fails to parse, because an Annual Report is a
different genre of document: it carries no SEC "Item" numbering at all, so
neither "Item 1B. Unresolved Staff Comments" nor "Item 2. Properties"
appears anywhere in it, and there is no end marker to find. Confirmed case:
Bank of New York Mellon's 2016 Exhibit 13.1 contains 28 start markers and
zero end markers.

What those documents do have is a RUNNING PAGE HEADER: "Risk Factors
(continued)" reprinted at the top of every page of the section -- 27 times
at ~5,000-character intervals in the BNY Mellon case, spanning offsets
375,604 to 504,224, with the section genuinely ending at the next unrelated
heading ("Recent Accounting Developments"). So the repetition that makes
these documents unparseable by marker-matching is itself the signal: a dense
run of repeated headings IS the section, and the section ends where the run
stops.

That strategy runs ONLY when the primary one returns None. Keeping it on the
None path (rather than merging its markers into the shared sets) is what
guarantees the ~9,300 already-extracted filings are byte-identical
afterwards, since no input that currently succeeds can reach the new code.
"""

import re

import lxml.html


def _spaced(word: str) -> str:
    return r"\s*".join(list(word))


def _item_number(digit: str, letter: str = "") -> str:
    """Matches a heading item number tolerating the separator variants
    seen between the digit and its letter suffix: 'Item 1A.' (baseline),
    'Item 1.A.' (confirmed case: CLX -- a period inserted between digit
    and letter), 'Item 1(a).' (confirmed case: HAL -- letter in
    parentheses). The trailing '.'/')' are optional so callers can still
    append their own closing punctuation tolerance.
    """
    if not letter:
        return digit
    return rf"{digit}\s*[.\(]?\s*{letter}\s*\)?"


# "item" itself is given the same per-letter whitespace tolerance as
# "risk"/"factors" below -- confirmed case: CINF renders its real Item 1A
# heading (not just the table-of-contents entry) with each letter of
# "ITEM" in its own inline element, so html_to_text's per-element newline
# insertion splits it into "I\nTEM" and a literal "item" match fails.
_ITEM = _spaced("item")
_LINE_START = r"^[ \t\xa0]*"
_START_STRICT = re.compile(
    rf"{_LINE_START}{_ITEM}\s*{_item_number('1', 'a')}\.?\s*[\-–—:]*\s*{_spaced('risk')}\s*{_spaced('factors')}",
    re.IGNORECASE | re.MULTILINE,
)
_END_1B = re.compile(
    rf"{_LINE_START}{_ITEM}\s*{_item_number('1', 'b')}\.?\s*[\-–—:]*\s*{_spaced('unresolved')}\s*{_spaced('staff')}\s*{_spaced('comments')}",
    re.IGNORECASE | re.MULTILINE,
)
_END_2 = re.compile(
    rf"{_LINE_START}{_ITEM}\s*{_item_number('2')}\.?\s*[\-–—:]*\s*{_spaced('properties')}",
    re.IGNORECASE | re.MULTILINE,
)
_START_BARE = re.compile(
    rf"{_LINE_START}{_spaced('risk')}\s*{_spaced('factors')}\b",
    re.IGNORECASE | re.MULTILINE,
)
_END_BARE_UNRESOLVED = re.compile(
    rf"{_LINE_START}{_spaced('unresolved')}\s*{_spaced('staff')}\s*{_spaced('comments')}\b",
    re.IGNORECASE | re.MULTILINE,
)
_END_BARE_PROPERTIES = re.compile(
    rf"{_LINE_START}{_spaced('properties')}\b",
    re.IGNORECASE | re.MULTILINE,
)

MIN_SECTION_CHARS = 200

# --- incorporation-by-reference stubs ---------------------------------------
# A filer who incorporates Item 1A by reference leaves a one-or-two-sentence
# pointer under the heading ("Information in response to this Item 1A can be
# found in the Company's 2014 Annual Report on pages 155 to 165"). That text
# sits between a real start marker and a real end marker, so the primary
# strategy extracts it happily and it gets stored as a SUCCESS -- a false
# positive, which is worse than a gap: a gap is visible in the coverage
# numbers, whereas this silently contributes 200 characters of boilerplate to
# a language study as though it were a company's risk disclosure. Wells
# Fargo's entire 20-year series was stored this way, at 229-249 chars against
# a corpus median of 51,471.
#
# Rejecting them here (rather than in the callers) also routes them to
# 03b_retry_missing_item_1a.py, which only reconsiders rows marked false --
# and the document the stub POINTS AT is usually a sibling in the same
# accession, so the real text becomes recoverable.
#
# Length bound is deliberately generous: the longest observed stub is 509
# chars and the shortest confirmed real section is 2,043, so 1,500 separates
# them without needing the phrase list to be exhaustive.
# Phrasing varies far too much to enumerate -- "incorporated herein by
# reference" (USB), "is described in Management's Discussion and Analysis"
# (HAL), "is included in the Financial Review section" (MCK), "We incorporate
# our disclosure related to risk factors into this section" (GENZ), "For
# identification and discussion of the most significant risks applicable"
# (EMN). Every attempt at a phrase list left some of those behind.
#
# Length is the signal that actually generalises. The corpus median section
# is 51,471 characters; a complete Item 1A for an S&P 500 filer is never
# 300. Everything below the floor is either a stub or a truncated extraction,
# and both are defects -- so both should be re-attempted rather than stored.
#
# The tradeoff is deliberate and worth stating: a genuinely complete but very
# short Item 1A would be rejected here. Nothing in this corpus looks like
# that (the shortest section that reads as complete is ~2,000 chars), and the
# cost of being wrong is a visible gap, whereas the cost of the status quo is
# 200 characters of boilerplate silently entering a language study as though
# it were a company's risk disclosure.
MIN_PLAUSIBLE_SECTION_CHARS = 1500


def is_implausible_section(section: str) -> bool:
    """True if this is too short to be a complete Item 1A for this corpus."""
    return len(section) < MIN_PLAUSIBLE_SECTION_CHARS

# --- running-header strategy (see module docstring) -------------------------
# Two consecutive repeats of the header more than a page apart are not a
# running header, they are two unrelated mentions. 9000 chars is a generous
# page: the confirmed BNY Mellon case repeats every ~4,900.
_MAX_PAGE_GAP = 9000
# Three repeats is the threshold that separates a running header from noise.
# A table-of-contents entry is a single isolated hit; a cross-reference in
# prose ("as discussed under Risk Factors") is one or two. Requiring three
# also means the run always spans at least two page boundaries.
_MIN_RUN_LENGTH = 3
# A standalone line of title-case or capitalised words: what a section
# heading looks like once html_to_text has flattened the document. Bounded
# length so a full sentence on its own line cannot masquerade as a heading.
_STANDALONE_HEADING = re.compile(
    r"^[ \t\xa0]*([A-Z][A-Za-z][A-Za-z ,&\-'()]{4,60})[ \t\xa0]*$", re.MULTILINE
)
_IS_RISK_FACTORS_HEADING = re.compile(r"(?i)^risk\s*factors")
# Parentheses are allowed above but DIGITS deliberately are not. The section's
# true terminator is often parenthesised ("Supplemental Information
# (unaudited)" ends BNY Mellon's), but these documents also print a page
# footer ("BNY Mellon 109") at every page break INSIDE the section -- treating
# a digit-bearing line as a heading would truncate at the first page boundary
# rather than the real end.
#
# That leaves the last footer before the true end dangling on the tail, so
# strip it explicitly: a short line carrying a digit and no sentence-ending
# punctuation is a page artifact, not prose.
_TRAILING_PAGE_FOOTER = re.compile(r"\n[ \t\xa0]*(?=[^\n]*\d)[^\n.!?\"')]{0,24}[ \t\xa0]*$")


def html_to_text(raw: str) -> str:
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    doc = lxml.html.fromstring(raw.encode("utf-8", errors="ignore"))
    for el in doc.iter():
        try:
            el.tail = (el.tail + "\n") if el.tail else "\n"
        except ValueError:
            pass
    text = doc.text_content()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _start_offsets(text: str) -> list[int]:
    return sorted(
        {m.start() for m in _START_STRICT.finditer(text)}
        | {m.start() for m in _START_BARE.finditer(text)}
    )


def extract_running_header(text: str) -> str | None:
    """Extract Item 1A from an Annual Report exhibit that has no Item
    numbering, using its repeated running page header as the section bounds.

    See the module docstring for why marker-matching cannot work on these
    documents. Only called when extract_item_1a's primary strategy fails.
    """
    starts = _start_offsets(text)
    if not starts:
        return None

    runs, current = [], [starts[0]]
    for previous, offset in zip(starts, starts[1:]):
        if offset - previous <= _MAX_PAGE_GAP:
            current.append(offset)
        else:
            runs.append(current)
            current = [offset]
    runs.append(current)

    runs = [r for r in runs if len(r) >= _MIN_RUN_LENGTH]
    if not runs:
        return None
    # Longest-spanning rather than most-repeats: page density varies, and the
    # real section is the one that covers the most of the document.
    run = max(runs, key=lambda r: r[-1] - r[0])

    # The section ends at the first heading after the last repeat that isn't
    # itself another "Risk Factors (continued)".
    end = None
    for match in _STANDALONE_HEADING.finditer(text, run[-1]):
        if _IS_RISK_FACTORS_HEADING.match(match.group(1).strip()):
            continue
        end = match.start()
        break
    if end is None:
        return None

    section = text[run[0]:end].strip()
    # Drop any page footers left dangling on the tail (repeat: a single
    # section can end on several stacked artifact lines).
    previous = None
    while previous != section:
        previous = section
        section = _TRAILING_PAGE_FOOTER.sub("", section).strip()
    return section if len(section) >= MIN_SECTION_CHARS else None


def _extract_by_markers(text: str) -> str | None:
    """The primary strategy: a start marker paired with the nearest following
    end marker, maximising the gap. Unchanged -- every filing that currently
    extracts successfully goes through here and only here."""
    starts = sorted({m.start() for m in _START_STRICT.finditer(text)} | {m.start() for m in _START_BARE.finditer(text)})
    ends = sorted(
        {m.start() for m in _END_1B.finditer(text)}
        | {m.start() for m in _END_2.finditer(text)}
        | {m.start() for m in _END_BARE_UNRESOLVED.finditer(text)}
        | {m.start() for m in _END_BARE_PROPERTIES.finditer(text)}
    )
    if not starts or not ends:
        return None

    best = None
    for s in starts:
        later_ends = [e for e in ends if e > s]
        if not later_ends:
            continue
        e = min(later_ends)
        gap = e - s
        if best is None or gap > best[2]:
            best = (s, e, gap)

    if best is None or best[2] < MIN_SECTION_CHARS:
        return None

    s, e, _ = best
    section = text[s:e].strip()
    section = _START_STRICT.sub("", section, count=1).strip()
    section = _START_BARE.sub("", section, count=1).strip()
    return section if len(section) >= MIN_SECTION_CHARS else None


def extract_item_1a(text: str) -> str | None:
    """Marker-pairing first; the running-header strategy only if that fails.

    The ordering is load-bearing, not stylistic. Because the fallback is
    unreachable for any input the primary strategy can handle, adding it
    cannot alter a single existing extraction -- the guarantee is structural
    rather than empirical (and is checked anyway by
    tests/test_parser_regression.py).
    """
    section = _extract_by_markers(text) or extract_running_header(text)
    # An implausibly short result is a pointer to the real Item 1A, or a
    # truncation -- not a short Item 1A. Returning None sends the row to 03b,
    # which searches the rest of the accession for the document being pointed
    # at, and that is usually where the real text is.
    if section is not None and is_implausible_section(section):
        return None
    return section


def extract_item_1a_from_html(raw_html: str) -> str | None:
    return extract_item_1a(html_to_text(raw_html))
