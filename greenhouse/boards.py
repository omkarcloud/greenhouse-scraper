"""/greenhouse/boards/* — a board's profile, its jobs (filtered, sorted,
paged), job changes since a moment, department / office / section trees and
the education lookups.

The whole board (one /jobs?content=true call) is the shared raw payload:
fetched once, cached (fetch.shared_raw), parsed once per process
(parsed_jobs) and then sliced by every listing, search and changes call."""
import math
from datetime import datetime, timezone

from greenhouse.shared import as_dict, as_list
from greenhouse import cache_config as ttl
from greenhouse import fetch
from greenhouse import parsers as P
from greenhouse import refs

TOP_N = 10


def no_board(board):
    return f"Greenhouse has no public job board '{board}'."


# ---- raw payloads --------------------------------------------------------------------------------

def jobs_raw(board):
    return fetch.shared_raw("board_jobs", {"board": board},
                            lambda: fetch.api(board, "/jobs", {"content": "true"}, label=f"{board} jobs",
                                              not_found=no_board(board)),
                            ttl.BOARD_JOBS_RAW_CACHE)


def root_raw(board):
    return fetch.shared_raw("board_root", {"board": board},
                            lambda: fetch.api(board, "", label=f"{board} board", not_found=no_board(board)),
                            ttl.BOARD_ROOT_RAW_CACHE)


def parsed_jobs(board):
    """Every open job of the board as API objects (description included)."""
    return fetch.memoized(("greenhouse", "parsed_jobs", board),
                          lambda: [P.job_summary(j, board) for j in as_list(jobs_raw(board).get("jobs"))
                                   if isinstance(j, dict)],
                          ttl=min(600, int(ttl.BOARD_JOBS_RAW_CACHE.total_seconds())))


# ---- filtering / sorting / paging ------------------------------------------------------------------

def _contains(haystack, needle):
    return bool(haystack) and needle.lower() in haystack.lower()


def _words_in(words, *texts):
    blob = " ".join(t for t in texts if t).lower()
    return all(w in blob for w in words)


def filter_jobs(jobs, query=None, in_description=False, department=None, office=None, location=None,
                workplace_type=None, employment_type=None, seniority=None, posted_since=None, updated_since=None,
                has_salary=None, min_salary=None, **_):
    words = [w for w in (query or "").lower().split() if w]
    out = []
    for job in jobs:
        if words and not _words_in(words, job.get("title"), *[d.get("name") for d in job.get("departments") or []],
                                   job.get("description") if in_description else None):
            continue
        if department and not any(_contains(d.get("name"), department) for d in job.get("departments") or []):
            continue
        if office and not any(_contains(o.get("name"), office) or _contains(o.get("location"), office)
                              for o in job.get("offices") or []):
            continue
        if location and not (_contains(job.get("location"), location)
                             or any(_contains(o.get("name"), location) or _contains(o.get("location"), location)
                                    for o in job.get("offices") or [])):
            continue
        if workplace_type and job.get("workplace_type") != workplace_type:
            continue
        if employment_type and job.get("employment_type") != employment_type:
            continue
        if seniority and job.get("seniority") != seniority:
            continue
        if posted_since and not ((job.get("posted_at") or "") >= posted_since):
            continue
        if updated_since and not ((job.get("updated_at") or "") >= updated_since):
            continue
        salary = job.get("salary")
        if has_salary is True and not salary:
            continue
        if has_salary is False and salary:
            continue
        if min_salary is not None:
            top = (salary or {}).get("max") if salary else None
            if top is None:
                top = (salary or {}).get("min") if salary else None
            if top is None or top < min_salary:
                continue
        out.append(job)
    return out


def sort_jobs(jobs, sort="newest"):
    if sort == "oldest":
        return sorted(jobs, key=lambda j: j.get("posted_at") or "9999")
    if sort == "updated":
        return sorted(jobs, key=lambda j: j.get("updated_at") or "", reverse=True)
    if sort == "title":
        return sorted(jobs, key=lambda j: (j.get("title") or "").lower())
    return sorted(jobs, key=lambda j: j.get("posted_at") or "", reverse=True)


def page_of(items, page, per_page):
    total = len(items)
    total_pages = max(1, math.ceil(total / per_page)) if total else 0
    start = (page - 1) * per_page
    return items[start:start + per_page], {"page": page, "items_per_page": per_page, "total_pages": total_pages,
                                          "total_count": total}


def rows(jobs, include_description):
    return jobs if include_description else [P.strip_description(j) for j in jobs]


# ---- endpoints -------------------------------------------------------------------------------------

def _top(counter, limit=TOP_N):
    return [{"name": name, "jobs_count": count}
            for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def get_board(board):
    """The board's profile: name, intro, counts and what it is hiring for."""
    results = fetch.run_parallel([
        lambda: root_raw(board),
        lambda: parsed_jobs(board),
        lambda: fetch.api(board, "/departments", label=f"{board} departments", not_found=no_board(board)),
        lambda: fetch.api(board, "/offices", label=f"{board} offices", not_found=no_board(board)),
    ], 4)
    for status, value in results:
        if status == "error":
            raise value
    root, jobs, departments, offices = (value for _, value in results)
    workplace = {"remote": 0, "hybrid": 0, "on_site": 0, "unknown": 0}
    by_department, by_location, by_seniority, languages = {}, {}, {}, set()
    with_salary = 0
    for job in jobs:
        workplace[job.get("workplace_type") or "unknown"] += 1
        if job.get("salary"):
            with_salary += 1
        if job.get("language"):
            languages.add(job["language"])
        for dep in job.get("departments") or []:
            if dep.get("name"):
                by_department[dep["name"]] = by_department.get(dep["name"], 0) + 1
        for loc in job.get("locations") or []:
            by_location[loc] = by_location.get(loc, 0) + 1
        if job.get("seniority"):
            by_seniority[job["seniority"]] = by_seniority.get(job["seniority"], 0) + 1
    posted = [j["posted_at"] for j in jobs if j.get("posted_at")]
    updated = [j["updated_at"] for j in jobs if j.get("updated_at")]
    department_nodes = [d for d in as_list(as_dict(departments).get("departments")) if isinstance(d, dict)]
    office_nodes = [o for o in as_list(as_dict(offices).get("offices")) if isinstance(o, dict)]
    return {
        "board": board,
        "name": P.text(as_dict(root).get("name")) or (jobs[0].get("company_name") if jobs else None),
        "link": refs.board_link(board),
        "description": P.to_text(as_dict(root).get("content")),
        "jobs_count": len(jobs),
        "jobs_with_salary_count": with_salary,
        "workplace_types": workplace,
        "departments_count": len(department_nodes),
        "offices_count": len(office_nodes),
        "languages": sorted(languages),
        "latest_posted_at": max(posted) if posted else None,
        "latest_updated_at": max(updated) if updated else None,
        "top_departments": _top(by_department),
        "top_locations": _top(by_location),
        "seniority_counts": dict(sorted(by_seniority.items(), key=lambda kv: -kv[1])),
    }


def list_jobs(board, page=1, per_page=20, sort="newest", include_description=False, **filters):
    jobs = sort_jobs(filter_jobs(parsed_jobs(board), **filters), sort)
    items, pagination = page_of(jobs, page, per_page)
    all_jobs = parsed_jobs(board)
    return {
        "board": board,
        "company_name": all_jobs[0].get("company_name") if all_jobs else None,
        "link": refs.board_link(board),
        "open_jobs_count": len(all_jobs),
        "jobs": rows(items, include_description),
        "pagination": pagination,
    }


def _snapshot_store():
    try:
        from cache import storage
        return storage()
    except Exception:
        return None


def job_changes(board, since):
    """Jobs opened / updated since `since` from the live board, plus the jobs
    that disappeared since our previous look at this board (the response
    cache keeps a per-board snapshot of job ids for SNAPSHOT_TTL)."""
    jobs = parsed_jobs(board)
    opened = [j for j in jobs if (j.get("posted_at") or "") >= since]
    opened_ids = {j["id"] for j in opened}
    updated = [j for j in jobs if j["id"] not in opened_ids and (j.get("updated_at") or "") >= since]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    current = {str(j["id"]): {"title": j.get("title"), "link": j.get("link"), "posted_at": j.get("posted_at")}
               for j in jobs if j.get("id") is not None}
    store = _snapshot_store()
    closed, previous_at = None, None
    if store is not None:
        key = {"board": board}
        try:
            hit = store.get("greenhouse.snapshot", key)
        except Exception as e:
            print(f"greenhouse: snapshot read failed for {board}: {type(e).__name__}: {e}")
            hit = None
        previous = as_dict((hit or {}).get("data"))
        if previous:
            previous_at = previous.get("at")
            closed = [{"id": int(job_id), "title": info.get("title"), "link": info.get("link"),
                       "posted_at": info.get("posted_at"), "last_seen_at": previous_at}
                      for job_id, info in as_dict(previous.get("jobs")).items() if job_id not in current]
        try:
            store.put("greenhouse.snapshot", key, {"at": now, "jobs": current}, ttl.SNAPSHOT_TTL)
        except Exception as e:
            print(f"greenhouse: snapshot write failed for {board}: {type(e).__name__}: {e}")
    return {
        "board": board,
        "link": refs.board_link(board),
        "since": since,
        "checked_at": now,
        "open_jobs_count": len(jobs),
        "opened_count": len(opened),
        "updated_count": len(updated),
        "closed_count": len(closed) if closed is not None else None,
        "opened": rows(sort_jobs(opened), False),
        "updated": rows(sort_jobs(updated, "updated"), False),
        "closed": closed,
        "snapshot": {"is_available": store is not None, "previous_at": previous_at,
                     "note": None if store is not None else
                     "closed jobs need the response cache (a previous snapshot of this board); none is configured here"},
    }


def list_departments(board, render_as="list", include_jobs=False):
    raw = fetch.api(board, "/departments", {"render_as": render_as}, label=f"{board} departments",
                    not_found=no_board(board))
    nodes = [P.department_node(d, board, include_jobs, render_as == "tree")
             for d in as_list(as_dict(raw).get("departments")) if isinstance(d, dict)]
    return {"board": board, "link": refs.board_link(board), "render_as": render_as, "count": len(nodes),
            "departments": nodes}


def list_offices(board, render_as="list", include_jobs=False):
    raw = fetch.api(board, "/offices", {"render_as": render_as}, label=f"{board} offices",
                    not_found=no_board(board))
    nodes = [P.office_node(o, board, include_jobs, render_as == "tree")
             for o in as_list(as_dict(raw).get("offices")) if isinstance(o, dict)]
    return {"board": board, "link": refs.board_link(board), "render_as": render_as, "count": len(nodes),
            "offices": nodes}


def list_sections(board):
    raw = fetch.api(board, "/sections", label=f"{board} sections", not_found=no_board(board))
    nodes = [P.section(s, board) for s in as_list(as_dict(raw).get("sections")) if isinstance(s, dict)]
    return {"board": board, "link": refs.board_link(board), "count": len(nodes), "sections": nodes}


def education_options(board, type, query=None, page=None):
    raw = fetch.api(board, f"/education/{type}", {"term": query, "page": page}, label=f"{board} {type}",
                    not_found=no_board(board))
    items = [P.education_item(i) for i in as_list(as_dict(raw).get("items")) if isinstance(i, dict)]
    meta = as_dict(as_dict(raw).get("meta"))
    return {"board": board, "type": type, "query": query, "page": page or 1, "count": len(items),
            "total_count": P.to_int(meta.get("total_count")), "per_page": P.to_int(meta.get("per_page")),
            "items": items}
