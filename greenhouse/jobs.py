"""/greenhouse/jobs/* — one job (with pay ranges), its application form and
a search across several boards."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import NotFound
from greenhouse import boards as B
from greenhouse import cache_config as ttl
from greenhouse import fetch
from greenhouse import parsers as P
from greenhouse import refs


def board_for(job):
    """The board token of a `job` ref ({"id", "board"}); a bare id is
    resolved through the embed redirect (cached: it never changes)."""
    if job.get("board"):
        return job["board"]
    from cache import cached_call
    job_id = int(job["id"])
    return fetch.memoized(("greenhouse", "job_board", job_id),
                          lambda: cached_call("greenhouse.job_board", {"id": job_id},
                                              lambda **_: fetch.board_of_job(job_id), ttl.JOB_BOARD_CACHE),
                          ttl=600)


def _job_raw(board, job_id, questions=False):
    params = {"pay_transparency": "true", "questions": "true" if questions else None}
    return fetch.api(board, f"/jobs/{int(job_id)}", params, label=f"{board} job {job_id}",
                     not_found=f"Greenhouse board '{board}' has no job {job_id}.")


def get_job(job):
    board = board_for(job)
    return P.job_details(_job_raw(board, job["id"]), board)


def get_application_form(job):
    board = board_for(job)
    return P.application_form(_job_raw(board, job["id"], questions=True), board)


def search_jobs(boards, page=1, per_page=20, sort="newest", include_description=False, **filters):
    """The matching jobs of up to refs.MAX_BOARDS boards, merged and paged.
    A board that fails (unknown token, upstream error) is reported in its
    row and never fails the batch; only every board failing raises."""
    results = fetch.run_parallel([lambda b=b: B.parsed_jobs(b) for b in boards],
                                 max(1, int(config.GREENHOUSE_FANOUT_WORKERS)))
    merged, board_rows, errors = [], [], []
    for board, (status, value) in zip(boards, results):
        if status == "error":
            errors.append(value)
            message = str(value) if isinstance(value, NotFound) else f"{type(value).__name__}: {value}"
            board_rows.append({"board": board, "company_name": None, "link": refs.board_link(board),
                               "open_jobs_count": None, "matched_count": None, "error": message})
            continue
        matched = B.filter_jobs(value, **filters)
        merged.extend(matched)
        board_rows.append({"board": board, "company_name": value[0].get("company_name") if value else None,
                           "link": refs.board_link(board), "open_jobs_count": len(value),
                           "matched_count": len(matched), "error": None})
    if errors and len(errors) == len(boards):
        raise errors[0]
    items, pagination = B.page_of(B.sort_jobs(merged, sort), page, per_page)
    return {"boards": board_rows, "jobs": B.rows(items, include_description), "pagination": pagination}
