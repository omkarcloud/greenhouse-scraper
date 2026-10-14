"""/greenhouse/boards/resolve — find a company's Greenhouse board token.

Greenhouse publishes no directory of boards, so discovery is: (1) a board
link or token the caller already has, (2) token guesses from the company
name / website domain validated against the Job Board API (a board root
answers {name} or 404), (3) the company's own careers pages scanned for the
boards.greenhouse.io / job-boards.greenhouse.io / boards-api markers every
Greenhouse embed carries. Every candidate that validates is returned with
its name and open-job count; the caller picks."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from greenhouse.shared import as_dict, as_list
from scraper_errors import NotFound
from greenhouse import fetch
from greenhouse import parsers as P
from greenhouse import refs

_SUFFIXES = ("inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "labs", "lab",
             "technologies", "technology", "tech", "software", "hq", "ai", "io", "app", "group", "holdings",
             "the")
_DOMAIN_PREFIXES = ("get", "try", "go", "hello", "use", "join", "with", "my", "the", "meet", "www")
_DOMAIN_SUFFIXES = ("hq", "app", "io", "ai", "labs", "inc", "co", "tech", "careers", "jobs")
_MARKER_RE = re.compile(
    r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:v1/boards/|embed/job_(?:board|app)(?:/js)?/?\?(?:[^\"'\s]*&(?:amp;)?)?for=)?"
    r"([A-Za-z0-9_-]{1,100})", re.I)
_PATHS = ("/careers", "/jobs", "/careers/", "/careers/jobs", "/company/careers", "/about/careers", "/join-us",
          "/join", "/")
MAX_CANDIDATES = 12


def _dedupe(items):
    out = []
    for item in items:
        item = (item or "").strip().lower()
        if item and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", item) and item not in out:
            out.append(item)
    return out


def candidates_from_name(name):
    low = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    words = low.split()
    if not words:
        return []
    out = ["".join(words), "-".join(words), "_".join(words)]
    core = [w for w in words if w not in _SUFFIXES] or words
    out += ["".join(core), "-".join(core), core[0]]
    return _dedupe(out)


def candidates_from_domain(domain):
    label = (domain or "").lower().split(".")[0]
    if not label:
        return []
    out = [label, label.replace("-", ""), label.replace("-", "_")]
    for prefix in _DOMAIN_PREFIXES:
        if label.startswith(prefix) and len(label) > len(prefix) + 2:
            out.append(label[len(prefix):].lstrip("-"))
    for suffix in _DOMAIN_SUFFIXES:
        if label.endswith(suffix) and len(label) > len(suffix) + 2:
            out.append(label[:-len(suffix)].rstrip("-"))
    return _dedupe(out)


def validate_board(token):
    """-> {board, name, jobs_count} when the token is a live public board."""
    try:
        root = fetch.api(token, "", label=f"resolve {token}", not_found="no board")
    except NotFound:
        return None
    try:
        jobs = fetch.api(token, "/jobs", label=f"resolve {token} jobs", not_found="no board")
    except NotFound:
        jobs = {}
    jobs_count = P.to_int(as_dict(as_dict(jobs).get("meta")).get("total"))
    if jobs_count is None:
        jobs_count = len(as_list(as_dict(jobs).get("jobs")))
    return {"board": token, "name": P.text(as_dict(root).get("name")), "link": refs.board_link(token),
            "jobs_count": jobs_count}


def _validate_many(tokens):
    results = fetch.run_parallel([lambda t=t: validate_board(t) for t in tokens],
                                 max(1, int(config.GREENHOUSE_FANOUT_WORKERS)))
    found = {}
    for token, (status, value) in zip(tokens, results):
        if status == "ok" and value:
            found[token] = value
        elif status == "error":
            print(f"greenhouse: resolve candidate {token} failed: {value}")
    return found


def careers_page_tokens(domain):
    """Scan the company's careers pages for Greenhouse embed markers ->
    (page_url, [tokens]). Stops at the first page that carries one."""
    for path in _PATHS:
        url = f"https://{domain}{path}"
        try:
            final, html = fetch.page(url, label=url)
        except Exception as e:
            print(f"greenhouse: careers scan {url}: {type(e).__name__}: {e}")
            continue
        tokens = _dedupe(t for t in _MARKER_RE.findall(html) if t.lower() not in ("embed", "v1", "boards"))
        if tokens:
            return final or url, tokens
    return None, []


def resolve_company(company):
    """`company` is refs.resolve_company's {"name", "domain", "board"}."""
    query = company.get("board") or company.get("domain") or company.get("name")
    tried, boards, scanned_page = [], [], None

    def add(found, confidence, source):
        for token, info in found.items():
            if all(b["board"] != token for b in boards):
                boards.append(dict(info, confidence=confidence, source=source))

    if company.get("board"):
        tried.append(company["board"])
        add(_validate_many([company["board"]]), "provided", "board link")
    name_candidates = candidates_from_name(company.get("name"))
    domain_candidates = candidates_from_domain(company.get("domain"))
    guesses = _dedupe(name_candidates + domain_candidates)[:MAX_CANDIDATES]
    guesses = [t for t in guesses if t not in tried]
    if guesses:
        tried += guesses
        found = _validate_many(guesses)
        exact = {guesses[0]} if guesses else set()
        for token, info in found.items():
            add({token: info}, "exact" if token in exact else "variant", "name" if token in name_candidates else "domain")
    domain = company.get("domain")
    if not domain and company.get("name") and not boards:
        domain = (name_candidates[0] + ".com") if name_candidates else None
    if domain and not boards:
        scanned_page, tokens = careers_page_tokens(domain)
        tokens = [t for t in tokens if t not in tried]
        if tokens:
            tried += tokens
            add(_validate_many(tokens), "careers_page", scanned_page)
    boards.sort(key=lambda b: -(b.get("jobs_count") or 0))
    return {"query": query, "is_resolved": bool(boards), "boards": boards, "tried": tried,
            "careers_page": scanned_page}
