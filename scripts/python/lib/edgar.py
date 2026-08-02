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


def get(url: str, **kwargs) -> requests.Response:
    _throttle()
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)
    resp = requests.get(url, headers=headers, timeout=30, **kwargs)
    return resp


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
