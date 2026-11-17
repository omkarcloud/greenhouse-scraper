"""Small value and transport helpers the greenhouse package needs, kept in the package
so it needs nothing from another site (the open-source Greenhouse Scraper ships this
folder verbatim). Pure functions plus a tiny in-process memo; every helper tolerates
None and junk."""
import html as html_lib
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures
from scraper_utils.values import as_dict, as_list, g, num, to_int_round as to_int
from scraper_utils.text import clean as text
from scraper_utils import fanout, memo


# ---- numbers / text --------------------------------------------------------------------------


_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>|</p>\s*<p[^>]*>|</li>\s*<li[^>]*>|</h\d>|</div>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def html_to_text(value):
    """HTML fragment -> readable plain text (block tags become newlines)."""
    if not value:
        return None
    out_text = _BR_RE.sub("\n", str(value))
    out_text = _TAG_RE.sub("", out_text)
    out_text = html_lib.unescape(out_text)
    lines = [_WS_RE.sub(" ", line).strip() for line in out_text.split("\n")]
    out, blank = [], False
    for line in lines:
        if not line:
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    result = "\n".join(out).strip()
    return result or None


# ---- transport helpers ------------------------------------------------------------------------

CF_MARKERS = ("just a moment", "security check", "attention required", "cf-chl", "challenge-platform")


def is_challenged(status, headers, body):
    """A Cloudflare challenge page (company careers pages only)."""
    if (headers or {}).get("cf-mitigated") == "challenge":
        return True
    head = (body or "")[:4000].lower()
    return status in (403, 429, 503) and any(m in head for m in CF_MARKERS)


_memo = OrderedDict()
_memo_lock = threading.Lock()
MEMO_TTL = 600
MEMO_MAX = 256


def memoized(key, fn, ttl=MEMO_TTL):
    """fn() memoised in-process for `ttl` seconds under `key`."""
    return memo.memoized(_memo, _memo_lock, MEMO_MAX, key, fn, ttl)


# Long-lived pools (2026-10-23): their threads keep their thread-local curl
# sessions — and the sticky exits behind them — between requests. The
# per-call executor they replace opened fresh sessions/exits for every
# fan-out and threw them away when the call returned. Two levels: a fan-out
# running inside a fan-out goes to the second pool, so it neither waits on
# the workers it occupies (deadlock) nor loses its parallelism; a third
# level runs inline.
_pools = (ThreadPoolExecutor(max_workers=24, thread_name_prefix="greenhouse-fanout-0"),
          ThreadPoolExecutor(max_workers=24, thread_name_prefix="greenhouse-fanout-1"))


def _windowed(fns, workers):
    """Run `fns` on the pool for this nesting level, at most `workers` at a
    time; results align with `fns`. The first failure (in `fns` order)
    propagates once the callables already started have finished."""
    return fanout.windowed(_pools, "greenhouse-fanout", fns, workers)


def run_parallel(fns, workers):
    """Run zero-arg callables in parallel; results align with `fns`. Each
    result is ("ok", value) or ("error", exception)."""
    def guard(fn):
        try:
            return ("ok", fn())
        except Exception as e:
            return ("error", e)
    if not fns:
        return []
    return _windowed([lambda fn=fn: guard(fn) for fn in fns], max(1, workers))
