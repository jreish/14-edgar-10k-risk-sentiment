"""Shared SEC EDGAR access: rate limiting, User-Agent, common endpoints.

SEC enforces 10 req/sec; we throttle to ~9/sec to stay safely under it.

Derived SEC API responses are cached to data/raw/ (gitignored, and the
location README already designates for cached raw API responses). Only the
two endpoint families that get hammered repeatedly are cached:
data.sec.gov/submissions and efts.sec.gov full text search. A full
resolution run issues thousands of both, largely repeats across years, and
takes ~2 hours uncached -- which makes the iterate-on-overrides loop that
manual_cik_overrides.csv depends on impractical.

Deliberately NOT cached: the point-in-time membership source files and
company_tickers.json. Checkpoint 1 established that source-of-truth inputs
are always re-fetched, never served stale from disk, because a silently
reused stale snapshot is the exact failure that sank the previous build.
Caching is for derived lookups whose answers are historical facts; it is
not for the inputs that define the study universe. Set EDGAR_NO_CACHE=1 to
bypass entirely.
"""
import hashlib
import json
import os
import pathlib
import time

import requests

_MIN_INTERVAL = 1.0 / 9.0
_last_request_time = [0.0]

_CACHE_DIR = pathlib.Path(__file__).resolve().parents[3] / "data" / "raw" / "api_cache"
_CACHEABLE_PREFIXES = (
    "https://data.sec.gov/submissions/",
    "https://efts.sec.gov/",
)
_CACHE_DISABLED = os.environ.get("EDGAR_NO_CACHE") == "1"

# How long to keep waiting out connection-level failures before giving up.
CONNECT_RETRY_BUDGET_S = 600.0


class SearchUnavailable(Exception):
    """EDGAR full text search could not be reached at all.

    Distinct from "the search ran and returned nothing", because the two must
    not be recorded the same way: a network outage silently logged as "no
    candidate found" would be an unresolved row that looks investigated when
    it never was.
    """


def _cache_path(url: str) -> pathlib.Path | None:
    if _CACHE_DISABLED or not url.startswith(_CACHEABLE_PREFIXES):
        return None
    return _CACHE_DIR / f"{hashlib.sha256(url.encode()).hexdigest()}.json"


def _throttle():
    elapsed = time.monotonic() - _last_request_time[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time[0] = time.monotonic()


_session = requests.Session()
_session.headers.update({"Accept-Encoding": "gzip, deflate"})


def _user_agent() -> str:
    """SEC requires a User-Agent naming whoever is running the scripts, so it
    comes from EDGAR_CONTACT with no default: a fallback would send every
    user's traffic under one person's name.

    Checked per request rather than at import so tests and cached runs don't
    need it. SystemExit, not an Exception subclass, because callers that
    catch Exception around a fetch would otherwise record a missing setting
    as a per-filing failure instead of stopping the run.
    """
    contact = os.environ.get("EDGAR_CONTACT", "").strip()
    if not contact:
        raise SystemExit(
            "EDGAR_CONTACT is not set. SEC requires a User-Agent identifying "
            "you, e.g.\n  export EDGAR_CONTACT=\"Jane Doe jane@example.com\""
        )
    return contact


def get(url: str, max_retries: int = 6, **kwargs) -> requests.Response:
    """Retries transient failures with backoff -- a 429/403 gets a long
    back-off (SEC may be flagging this IP over the rate limit), a 5xx gets
    a short exponential one. Needed once real filing-download volume (7000+
    requests in checkpoint 4) makes a handful of transient failures a
    near-certainty rather than an edge case.

    Connection-level failures (DNS, dropped socket) get a much longer ceiling
    than the original 3-try / 1-2-4s ladder: a momentary DNS failure to
    resolve efts.sec.gov killed a ten-minute resolution run outright, having
    exhausted all three retries inside seven seconds. These scripts are long
    and have no checkpointing, so a transient network blip costing the whole
    run is the expensive failure mode, and waiting a minute is cheap.
    """
    _session.headers["User-Agent"] = _user_agent()
    last_exc = None
    resp = None
    attempt = 0
    connect_waited = 0.0
    while attempt < max_retries:
        _throttle()
        try:
            resp = _session.get(url, timeout=30, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            # A connection-level failure is almost always the local network
            # going away briefly, not SEC rejecting us -- so it must NOT spend
            # the HTTP retry budget. Confirmed: a DNS failure to resolve
            # efts.sec.gov burned all six retries in 61 seconds and killed a
            # run 2000 rows in. Wait it out on its own budget instead; ten
            # minutes of sleeping is far cheaper than losing two hours.
            wait = min(30.0, 2.0 ** min(attempt, 5))
            if connect_waited + wait > CONNECT_RETRY_BUDGET_S:
                break
            connect_waited += wait
            time.sleep(wait)
            continue
        if resp.status_code in (429, 403):
            time.sleep(10 * (attempt + 1))
            attempt += 1
            continue
        if resp.status_code >= 500:
            time.sleep(2 ** attempt)
            attempt += 1
            continue
        return resp
    if resp is not None:
        return resp
    raise last_exc


def get_json(url: str, allow_missing: bool = False, **kwargs):
    """GET returning parsed JSON, served from data/raw/api_cache when the URL
    is on the cacheable allowlist. Returns None on 404 if allow_missing.
    """
    path = _cache_path(url)
    if path is not None and path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass  # corrupt/truncated entry -- fall through and re-fetch

    resp = get(url, **kwargs)
    if allow_missing and resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()

    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: these runs get killed midway often enough that a
        # half-written cache entry is a real risk, and a truncated JSON file
        # that looks present is worse than no cache at all.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    return data


def fetch_company_tickers() -> dict:
    """Ticker -> current CIK (int), per SEC's live company_tickers.json,
    filled in with company_tickers_exchange.json for any ticker missing
    from the first file.

    Confirmed gap: AEP (American Electric Power, a currently-active,
    continuously-filing NYSE company) is simply absent from
    company_tickers.json entirely, despite data.sec.gov/submissions
    confirming CIK 4904 files 10-Ks under ticker AEP to this day. SEC's own
    sibling file, company_tickers_exchange.json, does list it. Since the
    two files are otherwise redundant (same ticker->CIK mapping), using the
    exchange file only to fill gaps -- never to override -- costs nothing
    and recovers cases like AEP without changing behavior for tickers
    already covered.

    Current-holder-only by construction; callers must validate against a
    target historical window rather than trusting this mapping blindly.
    """
    data = get_json("https://www.sec.gov/files/company_tickers.json")
    mapping = {v["ticker"]: v["cik_str"] for v in data.values()}

    exchange_data = get_json("https://www.sec.gov/files/company_tickers_exchange.json")
    fields = exchange_data.get("fields", [])
    cik_idx = fields.index("cik")
    ticker_idx = fields.index("ticker")
    for row in exchange_data.get("data", []):
        ticker = row[ticker_idx]
        if ticker not in mapping:
            mapping[ticker] = row[cik_idx]

    # Share-class separator: the index membership source writes BF.B / BRK.B,
    # SEC writes BF-B / BRK-B. Neither file contains the dotted form at all,
    # so every dual-class ticker failed lookup outright and fell through to
    # full text search, which cannot confirm a ticker whose own filings spell
    # it differently -- 38 rows across BF.B and BRK.B resolved to nothing
    # despite both companies being continuously-filing S&P 500 members with
    # unambiguous CIKs. Registering the dotted spelling as an alias, never
    # overriding a real entry, fixes the class systematically instead of
    # needing one manual override per dual-class company.
    for ticker, cik in list(mapping.items()):
        if "-" in ticker:
            dotted = ticker.replace("-", ".")
            if dotted not in mapping:
                mapping[dotted] = cik

    return mapping


def fetch_submissions(cik: int) -> dict | None:
    padded = str(cik).zfill(10)
    return get_json(f"https://data.sec.gov/submissions/CIK{padded}.json", allow_missing=True)


TEN_K_FORMS = {"10-K", "10-K405", "10-KSB", "10-KSB405"}


def all_10k_filings(cik: int) -> list[dict]:
    """Every original 10-K this CIK ever filed, across its FULL history.

    Walks filings.files[] as well as filings.recent, because SEC truncates
    `recent` to roughly the last ~1000 filings -- for a long-lived active
    filer that doesn't reach back to 2006 at all (the confirmed bug behind
    the stint-continuity false negatives; see 01_resolve_ciks.validate_year).
    """
    subs = fetch_submissions(cik)
    if subs is None:
        return []

    def collect(bucket: dict) -> list[dict]:
        return [
            {"form": form, "filing_date": date, "accession_number": accn, "primary_document": doc}
            for form, date, accn, doc in zip(
                bucket.get("form", []), bucket.get("filingDate", []),
                bucket.get("accessionNumber", []), bucket.get("primaryDocument", []),
            )
            if form in TEN_K_FORMS
        ]

    filings = collect(subs.get("filings", {}).get("recent", {}))
    for file_meta in subs.get("filings", {}).get("files", []):
        try:
            filings.extend(collect(get_json(f"https://data.sec.gov/submissions/{file_meta['name']}")))
        except Exception:
            continue
    return sorted(filings, key=lambda f: f["filing_date"])


def filing_doc_url(cik: int, accession_number: str, primary_document: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession_number.replace('-', '')}/{primary_document}"
    )


def full_text_search(query: str, forms: str, start: str, end: str) -> list[dict]:
    """Returns hit list from EDGAR full text search (2001-present coverage)."""
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?q={requests.utils.quote(query)}&forms={forms}"
        f"&dateRange=custom&startdt={start}&enddt={end}"
    )
    try:
        data = get_json(url)
    except requests.HTTPError:
        return []  # search reached, refused this query -- a real empty result
    except (requests.RequestException, ValueError) as exc:
        raise SearchUnavailable(f"{type(exc).__name__}: {exc}") from exc
    return (data or {}).get("hits", {}).get("hits", [])
