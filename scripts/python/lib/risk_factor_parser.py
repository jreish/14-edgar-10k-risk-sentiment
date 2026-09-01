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


# --- grade-1 recovery: a real section under a heading with no Item numbering -
# Decided 2026-08-23. Three filers were inspected before writing this:
#
#   FedEx  -- 15 failing years. Its risk factors are in the primary document
#     the pipeline already downloads, under a bare "RISK FACTORS" heading, in
#     the annual-report portion appended after the Item-numbered part. The
#     marker strategy finds the heading (offset 234,999 in FY2014) but there
#     is no "Item 1B"/"Item 2" anywhere after it, so the start is discarded
#     for want of an end and the row is recorded as a miss.
#   U.S. Bancorp -- same shape, one document further out: the real section is
#     in the Annual Report exhibit, and the 10-K itself carries a 196-char
#     pointer under its own "Risk Factors" heading.
#   Johnson & Johnson -- deliberately NOT recovered here. Its Exhibit 99 is a
#     safe-harbor cautionary statement, not a risk-factor section: different
#     genre, and (checked) zero tariff mentions, so admitting it would put a
#     document in the corpus that structurally cannot say what the corpus is
#     being measured for. It stays a gap.
#
# The end is the problem. extract_running_header's "next standalone heading"
# rule truncates these badly -- FedEx to 2,641 chars of a 21,786-char section,
# stopping at a line-wrapped risk-factor title; U.S. Bancorp to 312, stopping
# at its first subsection heading ("Economic and Market Conditions Risk").
# Both false stops look exactly like a real end.
#
# So the terminator is enumerated instead of inferred. The headings that can
# follow a risk-factor section are a small, closed set in this corpus, and an
# unlisted one leaves the row a gap rather than guessing -- which is the same
# trade this project makes everywhere else. Confirmed ends: FedEx stops at
# "FORWARD-LOOKING STATEMENTS", U.S. Bancorp at "Managing Committee".
#
# This is reachable ONLY from bare_section_candidates, which nothing on the
# extract_item_1a path calls. Existing extractions are byte-identical by
# construction, not by measurement.
_SECTION_SUCCESSORS = [
    "forward looking statements", "forward-looking statements",
    "cautionary statement", "cautionary statements",
    "management's discussion and analysis", "managements discussion and analysis",
    "quantitative and qualitative disclosures",
    "report of management", "management's report", "managements report",
    "controls and procedures", "control over financial reporting",
    "selected financial data", "five year selected financial data",
    "financial statements and supplementary data",
    "consolidated financial statements",
    "supplemental financial information", "supplemental information",
    "recent accounting developments", "critical accounting",
    "managing committee", "executive officers", "directors and executive officers",
    "legal proceedings", "mine safety disclosures",
    "unresolved staff comments", "properties",
    # Added 2026-08-23 after reading the output: without these, 14 of 62
    # recoveries ran past the end of the risk factors and into the audited
    # financials. Citigroup 2024 captured 390,451 chars ending mid-way through
    # KPMG's opinion on internal control; PG&E 2007 ended inside Deloitte's.
    # Both START correctly -- the opening paragraph is the real one -- so
    # nothing about the beginning of the capture reveals the problem, and at
    # these lengths no plausibility floor would either. An annual-report
    # exhibit carries no Item numbering, so GUARD 1 cannot see it either.
    # These are the headings that actually terminate a risk-factor section in
    # that genre. Multi-word and distinctive on purpose: a bare "internal
    # control over financial reporting" would also match the phrase in running
    # prose if a line break happened to fall in front of it, and truncating
    # early is as wrong as running long.
    "report of independent registered public accounting firm",
    "reports of independent registered public accounting firm",
    "management's report on internal control", "managements report on internal control",
    "management's annual report on internal control",
    "managements annual report on internal control",
    "report of management on internal control",
    "consolidated balance sheet", "consolidated balance sheets",
    "consolidated statement of income", "consolidated statements of income",
    "consolidated statement of operations", "consolidated statements of operations",
    "glossary of terms",
    # Second reading pass, same day. "controls and procedures" was already
    # listed but never fires, because the heading in practice reads
    # "DISCLOSURE CONTROLS AND PROCEDURES" and the match is line-anchored --
    # so Citigroup 2023-2026 each carried ~6,000 extra characters ending on
    # "...Citigroup's disclosure controls and procedures were effective".
    # Small against a 390,000-char section, and still text that is not risk
    # factors.
    "disclosure controls and procedures",
    "corporate governance", "code of conduct", "available information",
]


def _successor_pattern(phrase: str) -> str:
    """Per-letter whitespace tolerance, plus either apostrophe.

    Filing agents render the possessive with a typographic apostrophe far
    more often than an ASCII one -- Citigroup's terminator is
    "MANAGEMENT'S ANNUAL REPORT ON INTERNAL CONTROL..." with U+2019 -- and a
    straight-quote pattern silently misses it. Silently, because a missed
    terminator does not fail: the capture just runs on to the next one it
    can match, which is how Citigroup 2024 came back 390,451 chars long with
    a correct opening paragraph and KPMG's audit opinion on the end.
    """
    def characters(word: str) -> str:
        return r"\s*".join(
            "['’]" if c == "'" else re.escape(c) for c in word
        )

    return r"\s*".join(characters(word) for word in phrase.split())


_END_SUCCESSOR = re.compile(
    _LINE_START + r"(?:" + "|".join(_successor_pattern(p) for p in _SECTION_SUCCESSORS) + r")\b",
    re.IGNORECASE | re.MULTILINE,
)

# "Table of Contents" is reprinted at every page break in the paginated
# filings this strategy targets, so the last one before the terminator is
# left dangling on the tail alongside the page number.
_TRAILING_TOC = re.compile(r"\n[ \t\xa0]*table\s*of\s*contents[ \t\xa0]*$", re.IGNORECASE)

# --- two guards this strategy needs and the marker strategy does not --------
# Learned by running it without them (2026-08-23) and reading the output. The
# uniqueness rule catches ambiguity between documents; it says nothing about
# whether the single survivor stops in the right place, and 30 of 75 did not:
#
#   JNJ 2006  -- 3,092 chars of Item 1B, Item 2 Properties, Item 3 Legal
#     Proceedings and Item 4. The start marker matched a heading whose body is
#     "Not applicable."; the nearest enumerated terminator was four items
#     downstream, so the capture ran straight through them.
#   CVG 2008  -- a pointer stub followed by the Properties section.
#   UPS 2006  -- the forward-looking-statements bullet list, ending "Item 7A."
#   WFC 2008  -- 1,604 chars: the cautionary preamble only, stopping before
#     the risk factors it introduces.
#
# All four are the failure this project treats as worse than a gap: text that
# is not what it is labelled, at a length no coverage number would question.
#
# GUARD 1 -- an Item-numbered heading INSIDE the capture proves it ran past
# the section end, because Item 1A is followed by Item 1B and never contains
# one. Line-anchored, so a cross-reference in prose ("see Item 7") is not
# mistaken for a heading; that is the same distinction the start markers draw.
#
# GUARD 2 -- a much higher length floor than MIN_PLAUSIBLE_SECTION_CHARS.
# 1,500 was calibrated against the marker strategy, whose bounds are two
# corroborating Item headings. This strategy's end is a single enumerated
# heading with nothing to corroborate it, so it has to clear a stricter bar
# than the method it is standing in for, not the same one: the 5th percentile
# of the 10,088 sections the trustworthy method produced (11,595 chars,
# against a median of 49,983). The cost is real and one-sided -- FedEx 2006 at
# 12,160 clears it by a hair, and genuinely short sections in the 5-11k range
# will be refused. That is the trade this project makes everywhere else.
#
# The lettered suffix is not optional decoration: "[2-9]\b" does not match
# "ITEM 7A." at all, because there is no word boundary between "7" and "A".
# That hole let UHS 2020 through with the MD&A appended and "ITEM 7A." sitting
# on the last line -- the exact thing this guard exists to catch, in the
# guard's own blind spot.
_INTERNAL_ITEM_HEADING = re.compile(
    rf"{_LINE_START}{_ITEM}\s*(?:1\s*[.\(]?\s*b|[2-9]\s*[.\(]?\s*[ab]?)\b",
    re.IGNORECASE | re.MULTILINE,
)

MIN_GRADE1_SECTION_CHARS = 11_595


# A short last line carrying no sentence punctuation is a page furniture line,
# not prose -- Wells Fargo's exhibit signs each page "Wells Fargo & Company",
# which _TRAILING_PAGE_FOOTER leaves behind because it carries no digit.
_TRAILING_RUNNING_FOOTER = re.compile(r"\n[ \t\xa0]*[^\n.!?;:]{1,40}[ \t\xa0]*$")


def _trim_page_artifacts(section: str) -> str:
    previous = None
    while previous != section:
        previous = section
        section = _TRAILING_TOC.sub("", section).strip()
        section = _TRAILING_PAGE_FOOTER.sub("", section).strip()
        section = _TRAILING_RUNNING_FOOTER.sub("", section).strip()
    return section


# GUARD 3 -- a real section ends where a sentence ends. A capture that stops
# mid-clause was not stopped by a heading; it was stopped by a terminator
# phrase that happened to fall at the start of a line inside running prose.
# Citigroup 2021 ended "...see Notes 1 and 15 to the", 109,180 chars in,
# because "Consolidated Financial Statements" followed as a styled
# cross-reference and html_to_text puts every element on its own line. The
# capture is not merely long there, it is arbitrary -- it stops in a place no
# document structure corresponds to, so nothing about it can be trusted.
_ENDS_A_SENTENCE = re.compile(r"[.!?][\"'’)\]]*$")


def ends_mid_sentence(section: str) -> bool:
    return not _ENDS_A_SENTENCE.search(section.strip())


# GUARD 4 -- the mirror of guard 3, and needed for the same reason. In a
# document that reprints "Risk Factors (continued)" at every page break, a
# start marker lands on each repeat, and if the outermost one is rejected by
# another guard the collapse hands back a LATER repeat -- a section that is
# correct at the end and 80,000 characters short at the front. Wells Fargo
# 2021 came back that way: 20,133 chars against ~100,000 for every
# neighbouring year, opening "(continued) example, if market interest rates
# increase...". Its true opening, shared with every other Wells Fargo year,
# is "An investment in the Company involves risk...".
#
# A section starts at the start of a sentence. A capture beginning lowercase,
# or on a "(continued)" tag, began mid-flow.
_STARTS_A_SENTENCE = re.compile(r"^[\"'“(\[]*[A-Z0-9]")
_CONTINUED_TAG = re.compile(r"^[ \t\xa0]*\(?\s*continued\s*\)?", re.IGNORECASE)


def starts_mid_sentence(section: str) -> bool:
    section = section.strip()
    if _CONTINUED_TAG.match(section):
        return True
    return not _STARTS_A_SENTENCE.match(section)


def bare_section_candidates(text: str) -> list[str]:
    """Every plausible risk-factor section in this document, by pairing a
    start marker with the nearest following enumerated successor heading.

    Returns all survivors rather than picking one: the caller pools
    candidates across every document in the accession and requires exactly
    one, because a filing that offers two is ambiguous (U.S. Bancorp's 10-K
    and its Annual Report exhibit each carry a "Risk Factors" heading) and
    ambiguity here is resolved by a human, not by a tiebreak rule.
    """
    starts = _start_offsets(text)
    ends = sorted({m.start() for m in _END_SUCCESSOR.finditer(text)})
    if not starts or not ends:
        return []

    # Candidates that share an end are NOT competing readings of the document
    # -- they are one section entered at successive points. Wells Fargo's
    # Annual Report exhibit reprints "Risk Factors" as a running page header,
    # so a 2018 filing yields eight starts at ~15,000-character intervals, all
    # running to the same terminator: 117,385 / 116,447 / 100,728 / ... /
    # 24,206 chars, each a suffix of the one before. Counting those as eight
    # rival candidates made every Wells Fargo year from 2012 on "ambiguous",
    # which is the wrong answer to a question that isn't ambiguous at all.
    #
    # Collapsing to the earliest start per end is not the "take the longest"
    # tiebreak rejected earlier: that would pick between genuinely different
    # spans of text. This picks the whole of a section over a suffix of
    # itself. Rival candidates -- different ends -- are still ambiguity and
    # still refused. (This is the same phenomenon extract_running_header was
    # written for; that strategy cannot serve here because it needs the run to
    # end at an inferrable heading, which is precisely what fails in these
    # documents.)
    outermost_start_for_end: dict[int, int] = {}
    for start in starts:
        later = [e for e in ends if e > start]
        if not later:
            continue
        end = min(later)
        if end not in outermost_start_for_end:
            outermost_start_for_end[end] = start

    sections = []
    for end, start in sorted(outermost_start_for_end.items()):
        section = text[start:end].strip()
        section = _START_STRICT.sub("", section, count=1).strip()
        section = _START_BARE.sub("", section, count=1).strip()
        section = _trim_page_artifacts(section)
        if len(section) < MIN_GRADE1_SECTION_CHARS:
            continue
        if _INTERNAL_ITEM_HEADING.search(section):
            continue
        if ends_mid_sentence(section) or starts_mid_sentence(section):
            continue
        sections.append(section)
    return sections


def bare_section_candidates_from_html(raw_html: str) -> list[str]:
    return bare_section_candidates(html_to_text(raw_html))
