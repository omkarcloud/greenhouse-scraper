"""The 10 Greenhouse endpoints. Every path is served with and without the
`/greenhouse` prefix, so code generated against the hosted API (paths like
/boards/jobs) runs unchanged against this server.

Each route validates its query with the marshmallow schema in
greenhouse/schemas.py (one param per input: a board token OR any Greenhouse
board link, a job ID OR any job link OR a careers link carrying gh_jid, ...)
and calls the function that returns the JSON. Paginated routes answer with
the same flat block as the hosted API: count / per_page / current_page /
total_pages / next / previous."""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from greenhouse import boards, jobs, resolve
from greenhouse import schemas as S
from schema_fields import load_query
from scraper_errors import BadRequest, NotFound


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The request query as unicode strings (bottle 0.12's .get() hands back
    latin-1 decoded bytes, so a UTF-8 "Zürich" would arrive as "ZÃ¼rich")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page_link(path, params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "")}
    query["page"] = page
    host = request.headers.get("Host") or "localhost"
    return f"{request.urlparts.scheme}://{host}{path}?{urlencode(query, doseq=True)}"


def paginate(result, path, params):
    """Lift the scraper's `pagination` block into the flat hosted-API shape."""
    pagination = result.pop("pagination", None) or {}
    page = _as_int(pagination.get("page")) or _as_int(params.get("page")) or 1
    total_pages = max(_as_int(pagination.get("total_pages")), 0)
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(path, params, page + 1 if page < total_pages else None),
        "previous": _page_link(path, params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(label, schema, fn, paginated):
    """Validate, run, and map errors: bad params -> 400, no data -> 404,
    blocked / transport failure -> 500."""
    raw = query_dict()
    data, error = load_query(schema, raw)
    if error:
        return json_response(error, 400)
    try:
        result = fn(**data)
    except ValueError as e:                # a param value Greenhouse does not serve
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:                # upstream rejected the params
        return json_response({"error": f"greenhouse rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:                 # retries exhausted / blocked
        return json_response({"error": f"greenhouse {label} failed: {e}"}, 500)
    if paginated:
        result = paginate(result, request.path, raw)
    return json_response(result)


# public path -> (schema, function, paginated), in the documented order
ENDPOINTS = [
    ("/boards/jobs", S.BoardJobsSchema, boards.list_jobs, True),
    ("/jobs/details", S.JobSchema, jobs.get_job, False),
    ("/boards/resolve", S.ResolveSchema, resolve.resolve_company, False),
    ("/jobs/search", S.SearchSchema, jobs.search_jobs, True),
    ("/boards/details", S.BoardSchema, boards.get_board, False),
    ("/boards/changes", S.ChangesSchema, boards.job_changes, False),
    ("/jobs/application-form", S.JobSchema, jobs.get_application_form, False),
    ("/boards/departments", S.TreeSchema, boards.list_departments, False),
    ("/boards/offices", S.TreeSchema, boards.list_offices, False),
    ("/boards/education", S.EducationSchema, boards.education_options, False),
]


def mount(path, schema, fn, paginated):
    """Serve one endpoint at /path and /greenhouse/path."""
    label = path.strip("/")

    def handler():
        return call(label, schema, fn, paginated)
    handler.__name__ = "greenhouse_" + label.replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/greenhouse" + path, method="GET")(handler)


for _path, _schema, _fn, _paginated in ENDPOINTS:
    mount(_path, _schema, _fn, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
