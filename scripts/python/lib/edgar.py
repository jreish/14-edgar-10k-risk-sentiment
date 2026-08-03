"""Shared SEC EDGAR access: rate limiting, User-Agent, common endpoints.

SEC enforces 10 req/sec; we throttle to ~9/sec to stay safely under it.
"""
import os
import time

import requests

USER_AGENT = os.environ.get("EDGAR_CONTACT", "Your Name your.email@example.com")
_MIN_INTERVAL = 1.0 / 9.0
_last_request_time = [0.0]


def _throttle():
    elapsed = time.monotonic() - _last_request_time[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time[0] = time.monotonic()


_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})


def get(url: str, max_retries: int = 3, **kwargs) -> requests.Response:
    """Retries transient failures with backoff -- a 429/403 gets a long
    back-off (SEC may be flagging this IP over the rate limit), a 5xx gets
    a short exponential one. Needed once real filing-download volume (7000+
    requests in checkpoint 4) makes a handful of transient failures a
    near-certainty rather than an edge case.
    """
    last_exc = None
    resp = None
    for attempt in range(max_retries):
        _throttle()
        try:
            resp = _session.get(url, timeout=30, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
            continue
        if resp.status_code in (429, 403):
            time.sleep(10 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        return resp
    if resp is not None:
        return resp
    raise last_exc


def get_json(url: str, **kwargs):
    resp = get(url, **kwargs)
    resp.raise_for_status()
    return resp.json()


def fetch_company_tickers() -> dict:
    """Ticker -> current CIK (int), per SEC's live company_tickers.json.

    Current-holder-only by construction; callers must validate against a
    target historical window rather than trusting this mapping blindly.
    """
    data = get_json("https://www.sec.gov/files/company_tickers.json")
    return {v["ticker"]: v["cik_str"] for v in data.values()}


def fetch_submissions(cik: int) -> dict | None:
    padded = str(cik).zfill(10)
    resp = get(f"https://data.sec.gov/submissions/CIK{padded}.json")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def full_text_search(query: str, forms: str, start: str, end: str) -> list[dict]:
    """Returns hit list from EDGAR full text search (2001-present coverage)."""
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?q={requests.utils.quote(query)}&forms={forms}"
        f"&dateRange=custom&startdt={start}&enddt={end}"
    )
    resp = get(url)
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data.get("hits", {}).get("hits", [])
