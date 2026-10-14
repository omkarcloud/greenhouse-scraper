"""Small value and transport helpers the greenhouse package needs, kept in
the package so it stands alone (the open-source Greenhouse Scraper ships
this folder verbatim). Pure functions plus a tiny in-process memo; every
helper tolerates None and junk."""
import html as html_lib
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor


# ---- safe access ---------------------------------------------------------------------------

def g(obj, *path, default=None):
    """Safe nested get: g(d, "a", "b", 0) -> d["a"]["b"][0] or default."""
    for step in path:
        if isinstance(step, int):
            if not isinstance(obj, list) or not -len(obj) <= step < len(obj):
                return default
            obj = obj[step]
        else:
            if not isinstance(obj, dict):
                return default
            obj = obj.get(step)
        if obj is None:
            return default
    return obj


def as_list(value):
    return value if isinstance(value, list) else []


def as_dict(value):
    return value if isinstance(value, dict) else {}


# ---- numbers / text --------------------------------------------------------------------------

def num(value):
    """int / float / numeric string ('1,234.5') -> float; bool, junk, NaN -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    try:
        out = float(str(value).strip().replace(",", ""))
    except ValueError:
        return None
    return out if out == out else None


def to_int(value):
    out = num(value)
    return int(round(out)) if out is not None else None


def text(value):
    """Whitespace-collapsed, HTML-unescaped string; '' -> None. Accepts
    {"text": ...} wrappers."""
    if isinstance(value, dict):
        value = value.get("text")
    if value is None:
        return None
    out = " ".join(html_lib.unescape(str(value)).replace("\xa0", " ").split())
    return out or None


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


def click_turnstile(page):
    """Real mouse click on the Cloudflare Turnstile checkbox, when shown."""
    import random
    for frame in page.frames:
        if not frame.url.startswith("https://challenges.cloudflare.com"):
            continue
        try:
            box = frame.frame_element().bounding_box()
        except Exception:
            box = None
        if not box or box.get("width", 0) < 50:
            continue
        x = box["x"] + box["width"] / 9 + random.uniform(-4, 4)
        y = box["y"] + box["height"] / 2 + random.uniform(-4, 4)
        page.mouse.move(x, y, steps=random.randint(10, 25))
        page.wait_for_timeout(random.randint(100, 300))
        page.mouse.down()
        page.wait_for_timeout(random.randint(50, 150))
        page.mouse.up()
        return True
    return False


_memo = OrderedDict()
_memo_lock = threading.Lock()
MEMO_TTL = 600
MEMO_MAX = 256


def memoized(key, fn, ttl=MEMO_TTL):
    """fn() memoised in-process for `ttl` seconds under `key`."""
    now = time.monotonic()
    with _memo_lock:
        hit = _memo.get(key)
        if hit and now - hit[0] < ttl:
            _memo.move_to_end(key)
            return hit[1]
    value = fn()
    with _memo_lock:
        _memo[key] = (now, value)
        _memo.move_to_end(key)
        while len(_memo) > MEMO_MAX:
            _memo.popitem(last=False)
    return value


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
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(fns)))) as ex:
        return list(ex.map(guard, fns))
