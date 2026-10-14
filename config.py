"""Configuration for the Greenhouse Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT              port the API listens on (default 8000)
    GREENHOUSE_PROXY  proxy URL, e.g. http://user:pass@host:port (default:
                      none — direct). Greenhouse's job board API has no bot
                      protection and answered 30 parallel calls from one IP,
                      so you very likely don't need this. When set, it is
                      used for the retry after a failed call and for the
                      company careers pages that Find Company Board scans.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

GREENHOUSE_PROXY = os.environ.get("GREENHOUSE_PROXY") or None

# Greenhouse job board API: attempts per call (a retry goes through GREENHOUSE_PROXY when set).
GREENHOUSE_API_MAX_ATTEMPTS = MAX_RETRIES
# Parallel board fetches in Search Jobs Across Companies and Find Company Board.
GREENHOUSE_FANOUT_WORKERS = int(os.environ.get("GREENHOUSE_FANOUT_WORKERS", "6"))
# The hosted version can open a real browser when a company's careers page is
# behind Cloudflare; this package stays plain HTTP (no browser to install), so
# the fallback is off.
GREENHOUSE_PAGE_BROWSER_FALLBACK = False
GREENHOUSE_PAGE_BROWSER_TIMEOUT = 45


def greenhouse_api_proxy(fallback=False):
    """The job board API runs direct; a retry after a failure uses GREENHOUSE_PROXY."""
    return GREENHOUSE_PROXY if fallback else None


def greenhouse_page_proxy():
    """The proxy for company careers pages (None = direct)."""
    return GREENHOUSE_PROXY
