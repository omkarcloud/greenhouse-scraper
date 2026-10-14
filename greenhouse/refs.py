"""Greenhouse input parsing: ONE param per input that auto-detects its forms
(tripadvisor QueryOrLinkField convention, never a sibling `url`/`id` pair).
Every resolver returns plain JSON values so validated params stay usable as
a response-cache key, and raises ValueError with a user-facing message.

  board    stripe | Stripe | https://job-boards.greenhouse.io/stripe
           | https://boards.greenhouse.io/stripe/jobs/8172510
           | https://boards.greenhouse.io/embed/job_board?for=stripe
           | https://boards-api.greenhouse.io/v1/boards/stripe/jobs          -> "stripe"
  job      8172510 | https://job-boards.greenhouse.io/stripe/jobs/8172510
           | https://boards.greenhouse.io/embed/job_app?for=stripe&token=8172510
           | https://stripe.com/jobs/search?gh_jid=8172510 (any careers site)
           | stripe/jobs/8172510                     -> {"id": 8172510, "board": "stripe" | None}
  boards   "stripe, figma, https://job-boards.greenhouse.io/vercel"   -> ["stripe", "figma", "vercel"]
  company  Stripe | stripe.com | https://stripe.com/jobs | https://job-boards.greenhouse.io/stripe
                                                        -> {"name", "domain", "board"}
"""
import re
from urllib.parse import parse_qs, unquote, urlparse

MAX_BOARDS = 10
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_JOB_PATH_RE = re.compile(r"^/?([A-Za-z0-9_-]+)/jobs/(\d+)/?$")
BOARD_HINT = ("Must be a Greenhouse board token (the name in job-boards.greenhouse.io/<token>) or a "
              "boards.greenhouse.io / job-boards.greenhouse.io link.")
JOB_HINT = ("Must be a Greenhouse job id, a job-boards.greenhouse.io/<board>/jobs/<id> link, or a careers-page "
            "link carrying gh_jid=<id>.")


def _is_url(value):
    low = value.lower()
    return low.startswith(("http://", "https://", "//")) or ".greenhouse.io/" in low


def _parse_url(value):
    """-> (host, path, query dict) for a URL-looking value, else None."""
    raw = value.strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    elif not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        return None
    return host, unquote(parsed.path), parse_qs(parsed.query)


def is_greenhouse_host(host):
    return host == "greenhouse.io" or host.endswith(".greenhouse.io")


def _token(value, hint):
    value = (value or "").strip().lower()
    if not _TOKEN_RE.match(value):
        raise ValueError(hint)
    return value


def board_from_url(host, path, query):
    """The board token in a greenhouse.io link, or None when the link
    carries none (an embed without ?for=)."""
    segments = [s for s in path.split("/") if s]
    if segments and segments[0] == "embed":
        token = (query.get("for") or [None])[0]
        return token.strip().lower() if token else None
    if len(segments) >= 3 and segments[0] == "v1" and segments[1] == "boards":
        return segments[2].lower()
    if segments:
        return segments[0].lower()
    return None


# ---- board ---------------------------------------------------------------------------------------

def resolve_board(value):
    raw = (value or "").strip()
    if not raw:
        raise ValueError(BOARD_HINT)
    if _is_url(raw):
        url = _parse_url(raw)
        if not url or not is_greenhouse_host(url[0]):
            raise ValueError(BOARD_HINT)
        token = board_from_url(*url)
        if not token:
            raise ValueError(BOARD_HINT)
        return _token(token, BOARD_HINT)
    return _token(raw, BOARD_HINT)


def resolve_boards(value):
    """Comma-separated boards (tokens or links) -> deduped tokens, 1..MAX_BOARDS."""
    out = []
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        token = resolve_board(item)
        if token not in out:
            out.append(token)
    if not out:
        raise ValueError("Must list at least one board token or link (comma-separated).")
    if len(out) > MAX_BOARDS:
        raise ValueError(f"At most {MAX_BOARDS} boards per call.")
    return out


# ---- job -----------------------------------------------------------------------------------------

def resolve_job(value):
    raw = (value or "").strip()
    if not raw:
        raise ValueError(JOB_HINT)
    if raw.isdigit():
        return {"id": int(raw), "board": None}
    if _is_url(raw):
        url = _parse_url(raw)
        if not url:
            raise ValueError(JOB_HINT)
        host, path, query = url
        job_id = (query.get("gh_jid") or query.get("token") or [None])[0]
        board = board_from_url(host, path, query) if is_greenhouse_host(host) else None
        match = _JOB_PATH_RE.match(path) if is_greenhouse_host(host) else None
        if match:
            board, job_id = match.group(1).lower(), match.group(2)
        if not job_id or not str(job_id).strip().isdigit():
            raise ValueError(JOB_HINT)
        return {"id": int(str(job_id).strip()), "board": _token(board, BOARD_HINT) if board else None}
    match = _JOB_PATH_RE.match(raw)
    if match:
        return {"id": int(match.group(2)), "board": match.group(1).lower()}
    raise ValueError(JOB_HINT)


# ---- company (board discovery) ---------------------------------------------------------------------

def resolve_company(value):
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Must be a company name, its website domain, or a Greenhouse board link.")
    if _is_url(raw):
        url = _parse_url(raw)
        if not url:
            raise ValueError("Must be a company name, its website domain, or a Greenhouse board link.")
        host, path, query = url
        if is_greenhouse_host(host):
            token = board_from_url(host, path, query)
            return {"name": None, "domain": None, "board": _token(token, BOARD_HINT) if token else None}
        return {"name": None, "domain": host[4:] if host.startswith("www.") else host, "board": None}
    if " " not in raw and "." in raw and re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw):
        host = raw.lower()
        return {"name": None, "domain": host[4:] if host.startswith("www.") else host, "board": None}
    return {"name": " ".join(raw.split()), "domain": None, "board": None}


# ---- links ------------------------------------------------------------------------------------------

def board_link(board):
    return f"https://job-boards.greenhouse.io/{board}"


def job_link(board, job_id):
    return f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
