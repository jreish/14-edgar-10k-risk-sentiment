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


def extract_item_1a(text: str) -> str | None:
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


def extract_item_1a_from_html(raw_html: str) -> str | None:
    return extract_item_1a(html_to_text(raw_html))
