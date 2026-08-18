#!/usr/bin/env python3
"""Test proposed (ticker -> CIK) override hypotheses against SEC's own data.

Overrides are the one place in this pipeline where a human proposes an
identity directly, which makes them the one place a confident-sounding wrong
answer could walk straight into the dataset. So proposing and accepting are
split: a hypothesis may come from anywhere (recollection, the worklist's
candidate column, a news article), but nothing reaches
manual_cik_overrides.csv until SEC's filings confirm it.

A hypothesis passes only if the CIK's OWN filings declare the ticker --
lib/cover_page.declared_symbols over every 10-K it filed inside the
ticker's unresolved year range. That is the same evidence standard 01b
applies to search candidates; the only difference is where the candidate
came from.

Reported outcomes:
  CONFIRMED   - a filing in range declares this exact ticker. Safe to add,
                and the script prints the CSV row with its evidence.
  CONTRADICTED- filings in range declare only OTHER symbols. The hypothesis
                is wrong; do not add it.
  NO_EVIDENCE - the CIK filed in range but never printed a symbol (routine
                pre-2019, where SEC did not require one on the cover page).
                Not confirmation. Left for a human with a real source.
  NO_FILINGS  - this CIK filed no 10-K in range at all.

Usage:  01d_check_override_candidates.py TICKER=CIK [TICKER=CIK ...]
"""
import collections
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib.cover_page import declared_symbols
from lib.db import connect
from lib.edgar import all_10k_filings, fetch_submissions, filing_doc_url, get, get_json

ROOT = pathlib.Path(__file__).resolve().parents[2]
SYMBOL_CACHE_PATH = ROOT / "data" / "raw" / "coverpage_symbols.json"


def norm(s: str) -> str:
    return s.replace(".", "").replace("-", "").upper()


def symbol_matches(ticker: str, declared: set[str]) -> bool:
    """Does any declared symbol correspond to this ticker?

    Exact match after normalising separators, plus one specific allowance:
    when a company enters Chapter 11 its listing moves to OTC and the ticker
    gains a suffix ending in Q -- Peabody BTU -> BTUUQ, Eastman Kodak EK ->
    EKDKQ, RadioShack RSH -> RSHCQ, Lehman LEH -> LEHMQ, GM MTL -> MTLQQ. The
    index membership source records the bankruptcy ticker; the company's own
    filings, written before or during the transition, still declare the base
    symbol. Requiring the declared symbol to be a genuine PREFIX of a
    Q-terminated ticker keeps this tight -- it cannot match an unrelated
    company, only the pre-bankruptcy form of this same one.
    """
    target = norm(ticker)
    declared_norm = {norm(s) for s in declared}
    if target in declared_norm:
        return True
    if target.endswith("Q") and len(target) >= 4:
        return any(len(d) >= 2 and target.startswith(d) for d in declared_norm)
    return False


def declared_via_any_filing(cik: int, ticker: str, symbol_cache: dict, max_hits: int = 4):
    """Second evidence path: search THIS CIK's entire filing history.

    SEC only required a trading-symbol field on the 10-K cover page from
    2019, and a great many pre-2019 10-Ks never print their own ticker --
    Safeway, Avon, Sigma-Aldrich, Legg Mason, US Steel and Plum Creek all
    file a decade of 10-Ks without the string appearing once. But the same
    companies say it readily in proxies and 8-Ks ("trades on the NYSE under
    the symbol"). Restricting full text search to the candidate's own CIK
    turns that into evidence: a document FILED BY this company that declares
    this symbol is the company claiming the ticker, whatever form it is on.

    Returns (matching_accession, declared_symbols) or (None, set()).
    """
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?q=%22{ticker}%22&ciks={str(cik).zfill(10)}"
    )
    try:
        data = get_json(url)
    except Exception:
        return None, set()

    seen = set()
    for hit in (data.get("hits", {}).get("hits", []) or [])[:max_hits]:
        source = hit.get("_source", {})
        hit_id = hit.get("_id", "")
        # _id is "accession:document"; the accession has no dashes here.
        accn_raw, _, doc = hit_id.partition(":")
        if not doc:
            continue
        accn = accn_raw.replace("-", "")
        if len(accn) != 18:
            continue
        formatted = f"{accn[:10]}-{accn[10:12]}-{accn[12:]}"
        if formatted in symbol_cache:
            symbols = set(symbol_cache[formatted])
        else:
            resp = get(filing_doc_url(cik, formatted, doc))
            if resp.status_code != 200:
                continue
            symbols = declared_symbols(resp.content)
            symbol_cache[formatted] = sorted(symbols)
        seen |= symbols
        if symbol_matches(ticker, symbols):
            return formatted, symbols
    return None, seen


# Phrases a company uses to state its OWN symbol. Searching these inside a
# candidate's filings is far more precise than searching the ticker itself:
# short tickers (X, LM, GR, PD, AT) match text everywhere, so a ticker-first
# query buries the one document that actually declares the symbol under
# hundreds that merely contain the letters. Asking "which of your filings
# declares a symbol?" and then reading the answer inverts that -- it converted
# 12 of 13 tickers that ticker-first search had left unverifiable, including
# Legg Mason, Avon, US Steel, BellSouth and Anheuser-Busch.
_DECLARATION_PHRASES = ("under the symbol", "trading symbol", "ticker symbol")


def declared_via_phrase_search(cik: int, ticker: str, symbol_cache: dict, max_hits: int = 6):
    """Third evidence path: ask the CIK's filings which symbol they declare.

    Same evidence standard as the other two -- a document filed by this
    company, declaring this symbol, read by lib.cover_page.declared_symbols.
    Only the query differs.
    """
    seen = set()
    for phrase in _DECLARATION_PHRASES:
        url = (
            "https://efts.sec.gov/LATEST/search-index"
            f"?q=%22{phrase.replace(' ', '+')}%22&ciks={str(cik).zfill(10)}"
        )
        try:
            data = get_json(url)
        except Exception:
            continue
        for hit in (data.get("hits", {}).get("hits", []) or [])[:max_hits]:
            accn_raw, _, doc = hit.get("_id", "").partition(":")
            accn = accn_raw.replace("-", "")
            if len(accn) != 18 or not doc:
                continue
            formatted = f"{accn[:10]}-{accn[10:12]}-{accn[12:]}"
            if formatted in symbol_cache:
                symbols = set(symbol_cache[formatted])
            else:
                resp = get(filing_doc_url(cik, formatted, doc))
                if resp.status_code != 200:
                    continue
                symbols = declared_symbols(resp.content)
                symbol_cache[formatted] = sorted(symbols)
            seen |= symbols
            if symbol_matches(ticker, symbols):
                return formatted, symbols
        if seen:
            break
    return None, seen


def main():
    hypotheses = []
    for arg in sys.argv[1:]:
        ticker, _, cik = arg.partition("=")
        hypotheses.append((ticker.strip(), int(cik)))
    if not hypotheses:
        print(__doc__)
        return

    con = connect()
    symbol_cache = json.loads(SYMBOL_CACHE_PATH.read_text()) if SYMBOL_CACHE_PATH.exists() else {}

    print(f"{'ticker':8} {'cik':>9}  {'verdict':12} {'company':34} evidence")
    print("-" * 120)
    accepted = []

    for ticker, cik in hypotheses:
        years = [r[0] for r in con.execute(
            "SELECT year FROM cik_resolution WHERE ticker = ? AND resolution_status = 'unresolved' ORDER BY year",
            [ticker]).fetchall()]
        if not years:
            print(f"{ticker:8} {cik:>9}  {'SKIP':12} (nothing unresolved for this ticker)")
            continue
        lo, hi = min(years), max(years)

        subs = fetch_submissions(cik)
        if subs is None:
            print(f"{ticker:8} {cik:>9}  {'BAD_CIK':12} (no SEC submissions record)")
            continue
        # SEC reports a CIK's CURRENT name, which for exactly the successor
        # chains this file exists to handle is not the name that filed the
        # 10-Ks. Reviewing "ATGE -> Covista Inc." looks like an obvious error
        # until you see the former names include DEVRY EDUCATION GROUP; the
        # same for WAMUQ -> "Maverick Merger Sub 2, LLC" (formerly WMI
        # HOLDINGS, Washington Mutual's successor) and IAC -> "Match Group"
        # (formerly IAC/INTERACTIVECORP). Carrying former names into the
        # evidence note is what makes a human review of this file meaningful
        # rather than misleading.
        former = [f.get("name") for f in subs.get("formerNames", []) if f.get("name")]
        name = subs.get("name", "?")
        if former:
            name_with_history = f"{name} (formerly {'; '.join(former[:3])})"
        else:
            name_with_history = name

        in_range = [f for f in all_10k_filings(cik)
                    if lo - 1 <= int(f["filing_date"][:4]) <= hi + 1]
        if not in_range:
            print(f"{ticker:8} {cik:>9}  {'NO_FILINGS':12} {name[:34]:34} no 10-K filed {lo}-{hi}")
            continue

        declared_all, evidence = set(), None
        for filing in in_range:
            accn = filing["accession_number"]
            if accn in symbol_cache:
                symbols = set(symbol_cache[accn])
            else:
                resp = get(filing_doc_url(cik, accn, filing["primary_document"]))
                if resp.status_code != 200:
                    continue
                symbols = declared_symbols(resp.content)
                symbol_cache[accn] = sorted(symbols)
            declared_all |= symbols
            if symbol_matches(ticker, symbols) and evidence is None:
                evidence = filing

        if evidence is not None:
            verdict = "CONFIRMED"
            # Bound the range by the years this CIK actually filed 10-Ks, not
            # by the full span of the ticker's missing years. Confirming the
            # IDENTITY does not confirm the DURATION: Baker Hughes Co declares
            # "BHGE" on its cover page, but first filed in 2018, so writing the
            # range as 2006-2018 would hand twelve pre-existence years to it
            # under cover of a real verification note.
            filed_years = sorted({int(f["filing_date"][:4]) for f in in_range})
            year_start, year_end = max(lo, min(filed_years)), min(hi, max(filed_years))
            span_note = ""
            if (year_start, year_end) != (lo, hi):
                span_note = (f" [range narrowed from the {lo}-{hi} gap to {year_start}-{year_end}: "
                             f"this CIK only filed 10-Ks in {min(filed_years)}-{max(filed_years)}]")
            note = (f"{name_with_history} filed {len(in_range)} 10-Ks {lo}-{hi}; its {evidence['filing_date']} "
                    f"10-K (accession {evidence['accession_number']}) declares trading symbol "
                    f"{ticker} (verified via lib/cover_page.declared_symbols){span_note}")
            accepted.append((ticker, year_start, year_end, cik, name, note))
        else:
            # The 10-Ks did not settle it. Widen to every form this CIK ever
            # filed before concluding anything -- a pre-2019 10-K routinely
            # omits the symbol that the same company's proxy states plainly.
            accn, wider = declared_via_any_filing(cik, ticker, symbol_cache)
            if accn is None:
                # Ticker-first search found nothing usable. Ask the inverse
                # question -- which symbol does this company declare? -- which
                # does not depend on the ticker being a distinctive string.
                accn, phrase_seen = declared_via_phrase_search(cik, ticker, symbol_cache)
                wider |= phrase_seen
            if accn is not None:
                verdict = "CONFIRMED"
                filed_years = sorted({int(f["filing_date"][:4]) for f in in_range})
                year_start, year_end = max(lo, min(filed_years)), min(hi, max(filed_years))
                note = (f"{name_with_history} filed {len(in_range)} 10-Ks {lo}-{hi}; none prints a symbol "
                        f"(pre-2019 cover pages did not require one), but its own filing "
                        f"{accn} declares {ticker} (CIK-restricted full text search + "
                        f"lib/cover_page.declared_symbols)")
                accepted.append((ticker, year_start, year_end, cik, name, note))
                print(f"{ticker:8} {cik:>9}  {verdict:12} {name[:34]:34} {note[:130]}")
                continue
            declared_all |= wider
            if declared_all:
                verdict = "CONTRADICTED"
                note = f"declares only {sorted(declared_all)[:6]} across all filings searched"
            else:
                verdict = "NO_EVIDENCE"
                note = f"{len(in_range)} 10-Ks in range; no filing of any type declares a symbol"

        print(f"{ticker:8} {cik:>9}  {verdict:12} {name[:34]:34} {note[:140]}")

    SYMBOL_CACHE_PATH.write_text(json.dumps(symbol_cache))
    con.close()

    if accepted:
        print(f"\n\n{len(accepted)} CONFIRMED -- append these to data/manual_cik_overrides.csv:\n")
        import csv as _csv
        writer = _csv.writer(sys.stdout, quoting=_csv.QUOTE_MINIMAL)
        for ticker, lo, hi, cik, name, note in accepted:
            writer.writerow([ticker, lo, hi, cik,
                             f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json", note])


if __name__ == "__main__":
    main()
