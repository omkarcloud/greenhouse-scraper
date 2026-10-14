"""Greenhouse transport: plain curl_cffi, validated 2026-10-14.

Surfaces:

  api(board, path, params)   boards-api.greenhouse.io/v1/boards/<token>/…, the
                             public Job Board API every Greenhouse careers
                             page is built on: no key, no cookies, no bot
                             protection. One GET /jobs?content=true returns a
                             WHOLE board (Stripe: 723 jobs with descriptions,
                             ~10 MB); /jobs/<id>?pay_transparency=true adds
                             pay ranges, ?questions=true the application
                             form; /departments and /offices come as a list
                             or a tree. 30 parallel calls from one IP all
                             answered 200. Direct egress; one retry on a
                             residential exit after a failure.
  board_of_job(job_id)       boards.greenhouse.io/embed/job_app?token=<id>
                             answers 301 to job-boards.greenhouse.io/embed/
                             job_app?for=<board>&token=<id>: the Location
                             header names the board a bare job id belongs to
                             (the API itself needs the board in the path).
  page(url)                  a company's own careers page (board discovery):
                             Chrome-TLS curl_cffi; when Cloudflare challenges
                             it, one headful patchright Chrome loads it and
                             hands back the rendered HTML.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  GreenhouseUpstreamError  transport failure / 5xx / unexpected body — retryable
  GreenhouseBlocked        403 / 429 / Cloudflare challenge          — retryable
  GreenhouseBadRequest     Greenhouse rejected the params             — never retried
  GreenhouseNotFound       no such board / job                        — never retried
"""
import json
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from urllib.parse import parse_qs, quote, urlencode, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from greenhouse.shared import click_turnstile, is_challenged, memoized, run_parallel  # noqa: F401  (re-exported)

API_BASE = "https://boards-api.greenhouse.io/v1/boards/"
EMBED_JOB_APP = "https://boards.greenhouse.io/embed/job_app"
BOARD_SITE = "https://job-boards.greenhouse.io"
TIMEOUT = (10, 90)             # (connect, read): a 700-job board with content is ~10 MB
IMPERSONATE = "chrome"
JSON_HEADERS = {"accept": "application/json", "accept-language": "en-US,en;q=0.9"}
PAGE_HEADERS = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "accept-language": "en-US,en;q=0.9"}
_FOR_RE = re.compile(r"[?&]for=([A-Za-z0-9_-]+)")


class GreenhouseUpstreamError(UpstreamError):
    """Transport failure, 5xx or an unexpected body — retryable."""


class GreenhouseBlocked(GreenhouseUpstreamError, Blocked):
    """403 / 429 / Cloudflare challenge that a fresh exit could not get past."""


class GreenhouseBadRequest(BadRequest):
    """Greenhouse rejected the params."""


class GreenhouseNotFound(NotFound):
    """No such board / job — never retried."""


def dump_debug(name, text):
    """Write a raw response to $GREENHOUSE_DEBUG_DIR/<name>.txt."""
    dbg = os.environ.get("GREENHOUSE_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w") as f:
                f.write(text)
        except OSError:
            pass


# ---- curl sessions (per thread, keyed by exit) -------------------------------------------

_local = threading.local()


def _sessions():
    store = getattr(_local, "sessions", None)
    if store is None:
        store = _local.sessions = OrderedDict()
    return store


def _session(key, proxy):
    from curl_cffi import requests as curl_requests
    store = _sessions()
    sess = store.get(key)
    if sess is None:
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        store[key] = sess
        while len(store) > 4:
            _, old = store.popitem(last=False)
            try:
                old.close()
            except Exception:
                pass
    else:
        store.move_to_end(key)
    return sess


def _drop_session(key):
    sess = _sessions().pop(key, None)
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


# ---- Job Board API ----------------------------------------------------------------------------

def api_url(board, path="", params=None):
    query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    return API_BASE + quote(board, safe="") + path + (("?" + urlencode(query)) if query else "")


def _classify_api(status, text, label, not_found):
    """-> the parsed JSON body, or raise the failure the response represents."""
    if status == 404:
        raise GreenhouseNotFound(not_found or f"Greenhouse has nothing at {label}")
    if status in (400, 422):
        raise GreenhouseBadRequest((text or "")[:200] or f"HTTP {status} on {label}")
    if status in (403, 429):
        raise GreenhouseBlocked(f"HTTP {status} from boards-api.greenhouse.io on {label}")
    if status >= 500:
        raise GreenhouseUpstreamError(f"HTTP {status} from boards-api.greenhouse.io on {label}")
    try:
        body = json.loads(text or "")
    except ValueError:
        dump_debug("api_non_json", text)
        raise GreenhouseUpstreamError(f"non-JSON answer (HTTP {status}) on {label}")
    if status != 200:
        raise GreenhouseUpstreamError(f"HTTP {status} on {label}: {str(body)[:200]}")
    if not isinstance(body, dict):
        raise GreenhouseUpstreamError(f"unexpected body on {label}")
    return body


def api(board, path="", params=None, label=None, not_found=None):
    """GET one Job Board API resource of `board` -> its JSON object.
    `not_found` is the 404 wording (the API says "Job not found" for an
    unknown BOARD as well, so callers name what is missing)."""
    url = api_url(board, path, params)
    label = label or f"{board}{path}"
    attempts = max(1, int(config.GREENHOUSE_API_MAX_ATTEMPTS))
    last = None
    for attempt in range(1, attempts + 1):
        proxy = config.greenhouse_api_proxy(fallback=attempt > 1)
        key = f"api:{proxy or 'direct'}"
        sess = _session(key, proxy)
        try:
            resp = sess.get(url, headers=JSON_HEADERS, timeout=TIMEOUT)
        except Exception as e:
            _drop_session(key)
            last = GreenhouseUpstreamError(f"request failed on {label}: {type(e).__name__}: {e}")
        else:
            try:
                return _classify_api(resp.status_code, resp.text, label, not_found)
            except (GreenhouseNotFound, GreenhouseBadRequest):
                raise
            except GreenhouseUpstreamError as e:
                _drop_session(key)
                last = e
        print(f"greenhouse: attempt {attempt}/{attempts} on {label} failed: {last}")
        if attempt < attempts:
            time.sleep(0.4 * attempt)
    raise last


def board_of_job(job_id):
    """The board token a bare job id belongs to, from the embed redirect."""
    url = f"{EMBED_JOB_APP}?token={int(job_id)}"
    label = f"job {job_id} board lookup"
    attempts = max(1, int(config.GREENHOUSE_API_MAX_ATTEMPTS))
    last = None
    for attempt in range(1, attempts + 1):
        proxy = config.greenhouse_api_proxy(fallback=attempt > 1)
        key = f"api:{proxy or 'direct'}"
        sess = _session(key, proxy)
        try:
            resp = sess.get(url, headers=PAGE_HEADERS, timeout=TIMEOUT, allow_redirects=False)
        except Exception as e:
            _drop_session(key)
            last = GreenhouseUpstreamError(f"request failed on {label}: {type(e).__name__}: {e}")
        else:
            if resp.status_code in (404, 410):
                raise GreenhouseNotFound(f"Greenhouse has no job with id {job_id}.")
            location = resp.headers.get("location") or ""
            match = _FOR_RE.search(location)
            if resp.status_code in (301, 302, 303, 307, 308) and match and match.group(1):
                return match.group(1).lower()
            if resp.status_code in (403, 429):
                last = GreenhouseBlocked(f"HTTP {resp.status_code} on {label}")
            else:
                dump_debug("job_app_redirect", f"{resp.status_code} {location}\n{resp.text[:2000]}")
                last = GreenhouseUpstreamError(f"no board in the redirect (HTTP {resp.status_code}) on {label}")
            _drop_session(key)
        print(f"greenhouse: attempt {attempt}/{attempts} on {label} failed: {last}")
        if attempt < attempts:
            time.sleep(0.4 * attempt)
    raise last


# ---- company careers pages (board discovery) ----------------------------------------------------

_browser_slots = threading.BoundedSemaphore(2)


def _browser_page(url, label):
    """One headful patchright Chrome: load the page, click Turnstile when
    shown, return the rendered HTML once the challenge is gone."""
    try:
        from chrome_manager import create_scope
        from patchright_driver import PatchrightDriver
    except ImportError:
        raise GreenhouseBlocked(f"Cloudflare challenge on {label} (no browser available for the fallback)")

    driver = None
    with _browser_slots:
        try:
            with create_scope():
                driver = PatchrightDriver(proxy_url=config.greenhouse_page_proxy(), headless=False,
                                          geoip_timezone=True)
            driver.call(lambda page: page.goto(url, wait_until="domcontentloaded", timeout=60000))
            deadline = time.monotonic() + float(config.GREENHOUSE_PAGE_BROWSER_TIMEOUT)
            last_click = 0.0
            while time.monotonic() < deadline:
                try:
                    html = driver.content() or ""
                except Exception:
                    html = ""
                if html and not is_challenged(403, {}, html):
                    return html
                if time.monotonic() - last_click > 6:
                    try:
                        if driver.call(click_turnstile):
                            last_click = time.monotonic()
                    except Exception:
                        pass
                time.sleep(1.0)
            raise GreenhouseBlocked(f"Cloudflare challenge did not clear in a browser on {label}")
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass


def page(url, label=None):
    """GET a company web page -> (final_url, HTML). 404 -> GreenhouseNotFound;
    a Cloudflare challenge -> the browser fallback, else GreenhouseBlocked."""
    label = label or url
    proxy = config.greenhouse_page_proxy()
    key = f"page:{proxy or 'direct'}"
    sess = _session(key, proxy)
    try:
        resp = sess.get(url, headers=PAGE_HEADERS, timeout=(10, 40), allow_redirects=True)
    except Exception as e:
        _drop_session(key)
        raise GreenhouseUpstreamError(f"request failed on {label}: {type(e).__name__}: {e}")
    status, text, final = resp.status_code, resp.text or "", str(resp.url)
    if not is_challenged(status, dict(resp.headers), text):
        if status in (404, 410):
            raise GreenhouseNotFound(f"no page at {label}")
        if status >= 400:
            raise GreenhouseUpstreamError(f"HTTP {status} on {label}")
        return final, text
    _drop_session(key)
    if not config.GREENHOUSE_PAGE_BROWSER_FALLBACK:
        raise GreenhouseBlocked(f"Cloudflare challenge on {label}")
    print(f"greenhouse: {label} is Cloudflare-challenged; trying a browser")
    return url, _browser_page(url, label)


# ---- shared raw payloads ------------------------------------------------------------------------

def shared_raw(name, params, build, ttl):
    """An upstream payload several routes read (a board's whole job list
    feeds listing pages, search, changes and the board profile): in-process
    memo, then the response cache (pods share it), then build()."""
    from cache import cached_call
    seconds = ttl.total_seconds() if hasattr(ttl, "total_seconds") else ttl
    key = ("greenhouse", name, json.dumps(params, sort_keys=True))
    return memoized(key, lambda: cached_call(f"greenhouse.raw.{name}", params, lambda **_: build(), ttl),
                    ttl=min(seconds, 600))


def parse_qs_first(url, name):
    try:
        return (parse_qs(urlparse(url).query).get(name) or [None])[0]
    except Exception:
        return None


if __name__ == "__main__":
    # Smoke test: python greenhouse/fetch.py
    data = api("figma", "/jobs", {"content": "true"}, label="smoke")
    print(len(data.get("jobs") or []), "figma jobs;", data.get("meta"))
    print("job 4017331008 ->", board_of_job(4017331008))
