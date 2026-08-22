"""Extract the ticker symbols a filing declares as ITS OWN.

The point of this module is to turn an unreliable *ranking* problem into a
reliable *verification* problem.

Checkpoint 2's full text search fallback asks "which company's 10-K best
matches this ticker string?" and was measured at only ~70% accuracy, because
a bare ticker string appears in plenty of filings that don't own it. The
confirmed failure case: searching "TER" for Teradyne returned Trump
Entertainment Resorts, whose 10-K abbreviates the registrant itself as "TER"
("TER Common Stock traded on the Nasdaq Global Market...") dozens of times.
Ranking cannot separate those two documents -- both are saturated with "TER".

But the documents are trivially separable on a different question: which
symbol does each one claim? Trump Entertainment's filing says its symbol is
"TRMP" (later "TRMPQ") and never claims "TER", so it can be positively
*contradicted* rather than merely out-ranked.

Three outcomes, deliberately not two:
  - verified:     the filing declares this exact ticker as its own symbol.
  - contradicted: the filing declares symbols, and this ticker isn't among
                  them. Strong evidence the candidate is the WRONG company.
  - no_evidence:  the filing declares no symbol anywhere. Common pre-2019 --
                  Teradyne's own 2010 10-K never prints "TER" a single time,
                  because the trading-symbol column on the cover page was not
                  required until SEC's 2019 rule change. This is an honest
                  gap, NOT a reason to fall back to guessing.

That third outcome is the whole reason this is worth doing: it keeps the
project's rule that a wrong company attribution silently presented as
resolved is worse than a documented gap.
"""

import re

import lxml.html

# A plausible US exchange ticker: 1-6 chars, optional class suffix (BF.B,
# BRK-B). Anchored uppercase so it doesn't match ordinary prose words.
_TICKER = r"[A-Z][A-Z0-9]{0,5}(?:[.\-][A-Z])?"
_Q = r"[\"'“”‘’]"  # straight and smart quotes

# Self-declarative phrasings. Each must capture the symbol the REGISTRANT
# claims, not any symbol mentioned in passing.
_PHRASE_PATTERNS = [
    # "under the symbol 'XYZ'" / "under the ticker symbol XYZ"
    re.compile(rf"under\s+the\s+(?:ticker\s+|trading\s+)?symbols?\s*:?\s*{_Q}?({_TICKER}){_Q}?"),
    # "ticker symbol: XYZ" / "trading symbol 'XYZ'"
    re.compile(rf"(?:ticker|trading)\s+symbols?\s*:?\s*{_Q}?({_TICKER}){_Q}?"),
    # "symbol 'XYZ'" -- quotes required here, since bare "symbol" + word is noisy
    re.compile(rf"symbols?\s*:?\s*{_Q}({_TICKER}){_Q}"),
]

# "(NYSE: XYZ)" is applied ONLY to the cover-page region, unlike the patterns
# above which run over the whole document.
#
# Confirmed false positive: Cintas's 2015 10-K contains "...agreement to sell
# its investment in the Shred-it Partnership to Stericycle, Inc. (Nasdaq:
# SRCL)...". Read over the full document, that parenthetical made Cintas
# "declare" SRCL, and SRCL 2015 was then attributed to Cintas's filing --
# through the cover-page verification that exists to PREVENT exactly this.
# Mondelez's spin-off 10-K names KRFT the same way, taking Kraft Foods Group's
# 2013 row with it.
#
# The asymmetry is the whole point: a company states its OWN symbol as "under
# the symbol X" or in the Section 12(b) table, whereas the parenthetical
# "(Nasdaq: X)" form is how it names SOMEBODY ELSE -- an acquirer, a target, a
# spun-off sibling. Confining it to the cover page keeps the handful of
# registrants who use it about themselves, where no third party appears.
_COVER_REGION_CHARS = 15000
_EXCHANGE_COLON = re.compile(
    rf"(?:NYSE|NASDAQ|Nasdaq|NYSE\s+American|NYSE\s+MKT|AMEX|OTC)\s*[:\-–]\s*({_TICKER})\b"
)


def _symbols_from_cover_table(doc) -> set[str]:
    """Read the Section 12(b) cover-page table's "Trading Symbol(s)" column.

    Mandatory since SEC's 2019 cover-page rule, and by far the strongest
    evidence available: it is the registrant formally registering that symbol,
    not prose. Parsed structurally (locate the header cell, take its column
    index, read that column's data cells) rather than by regex over flattened
    text -- text_content() emits every header cell before any data cell, so a
    regex anchored on "Trading Symbol" reads the NEXT HEADER ("Name of each
    exchange...") instead of the value.
    """
    found = set()
    for table in doc.xpath("//table"):
        rows = table.xpath(".//tr")
        header_idx = None
        for row in rows:
            cells = row.xpath("./td|./th")
            for i, cell in enumerate(cells):
                text = " ".join(cell.text_content().split()).lower()
                if "trading symbol" in text:
                    header_idx = i
                    break
            if header_idx is not None:
                break
        if header_idx is None:
            continue
        for row in rows:
            cells = row.xpath("./td|./th")
            if len(cells) <= header_idx:
                continue
            value = " ".join(cells[header_idx].text_content().split())
            if re.fullmatch(_TICKER, value):
                found.add(value)
    return found


def declared_symbols(html_bytes: bytes) -> set[str]:
    """Every ticker this filing claims as its own."""
    # Bytes, not str: SEC filings carry an XML encoding declaration, and lxml
    # refuses a str that declares its own encoding.
    doc = lxml.html.fromstring(html_bytes)
    symbols = _symbols_from_cover_table(doc)

    text = doc.text_content()
    for pattern in _PHRASE_PATTERNS:
        for match in pattern.finditer(text):
            symbols.add(match.group(1))
    for match in _EXCHANGE_COLON.finditer(text[:_COVER_REGION_CHARS]):
        symbols.add(match.group(1))
    return symbols


def verify_ticker(html_bytes: bytes, ticker: str) -> tuple[str, set[str]]:
    """Returns (verdict, declared_symbols) where verdict is one of
    verified / contradicted / no_evidence."""
    declared = declared_symbols(html_bytes)
    if not declared:
        return "no_evidence", declared
    # Compare on a normalized form so BF.B / BF-B / BFB don't spuriously differ
    # across the ticker conventions used by the index source vs. the filing.
    norm = lambda s: s.replace(".", "").replace("-", "").upper()
    if norm(ticker) in {norm(s) for s in declared}:
        return "verified", declared
    return "contradicted", declared
