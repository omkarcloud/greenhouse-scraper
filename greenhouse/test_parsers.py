"""Offline tests: refs resolvers, marshmallow schemas, parsers and the
endpoint functions with the transport monkeypatched to serve fixtures
(greenhouse/fixtures/*.json.gz, raw payloads captured 2026-10-14) — no
network.

    python -m pytest greenhouse/test_parsers.py -q
"""
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from greenhouse import boards, fetch, jobs, parsers as P, refs, resolve, schemas  # noqa: E402
from schema_fields import load_query  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fx(name):
    with gzip.open(os.path.join(FX, name + ".json.gz"), "rt", encoding="utf-8") as f:
        return json.load(f)


# ---- refs ------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("stripe", "stripe"), ("Stripe", "stripe"), (" figma ", "figma"),
    ("https://job-boards.greenhouse.io/stripe", "stripe"),
    ("https://boards.greenhouse.io/stripe/jobs/8172510?gh_jid=8172510", "stripe"),
    ("https://boards.greenhouse.io/embed/job_board?for=figma", "figma"),
    ("https://boards.greenhouse.io/embed/job_app?for=figma&token=5813967004", "figma"),
    ("https://boards-api.greenhouse.io/v1/boards/vercel/jobs", "vercel"),
    ("job-boards.greenhouse.io/Anthropic/", "anthropic"),
])
def test_resolve_board(value, expected):
    assert refs.resolve_board(value) == expected


@pytest.mark.parametrize("value", ["", "https://example.com/stripe", "stri pe", "a/b", "https://boards.greenhouse.io/embed/job_board"])
def test_resolve_board_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_board(value)


@pytest.mark.parametrize("value,expected", [
    ("8172510", {"id": 8172510, "board": None}),
    ("https://job-boards.greenhouse.io/stripe/jobs/8172510", {"id": 8172510, "board": "stripe"}),
    ("https://boards.greenhouse.io/figma/jobs/5813967004?gh_jid=5813967004", {"id": 5813967004, "board": "figma"}),
    ("https://boards.greenhouse.io/embed/job_app?for=figma&token=5813967004", {"id": 5813967004, "board": "figma"}),
    ("https://boards.greenhouse.io/embed/job_app?token=4017331008", {"id": 4017331008, "board": None}),
    ("https://stripe.com/jobs/search?gh_jid=8172510", {"id": 8172510, "board": None}),
    ("https://www.figma.com/careers/?gh_jid=5813967004&gh_src=x", {"id": 5813967004, "board": None}),
    ("figma/jobs/5813967004", {"id": 5813967004, "board": "figma"}),
])
def test_resolve_job(value, expected):
    assert refs.resolve_job(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "https://stripe.com/jobs", "https://job-boards.greenhouse.io/stripe"])
def test_resolve_job_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_job(value)


def test_resolve_boards():
    assert refs.resolve_boards("stripe, Figma,https://job-boards.greenhouse.io/vercel,stripe") == ["stripe", "figma", "vercel"]
    with pytest.raises(ValueError):
        refs.resolve_boards(",".join(f"b{i}" for i in range(11)))
    with pytest.raises(ValueError):
        refs.resolve_boards(" , ")


@pytest.mark.parametrize("value,expected", [
    ("Stripe", {"name": "Stripe", "domain": None, "board": None}),
    ("stripe.com", {"name": None, "domain": "stripe.com", "board": None}),
    ("https://www.figma.com/careers/", {"name": None, "domain": "figma.com", "board": None}),
    ("https://job-boards.greenhouse.io/vercel", {"name": None, "domain": None, "board": "vercel"}),
    ("Datadog  Inc", {"name": "Datadog Inc", "domain": None, "board": None}),
])
def test_resolve_company(value, expected):
    assert refs.resolve_company(value) == expected


# ---- schemas ----------------------------------------------------------------------------------

def test_schemas():
    data, err = load_query(schemas.BoardJobsSchema, {"board": "https://job-boards.greenhouse.io/Stripe", "posted_since": "7d",
                                                   "has_salary": "yes", "sort": "Updated", "per_page": "50"})
    assert err is None and data["board"] == "stripe" and data["has_salary"] is True and data["sort"] == "updated"
    assert data["posted_since"].endswith(":00:00Z") and data["per_page"] == 50 and data["page"] == 1
    data, err = load_query(schemas.ChangesSchema, {"board": "stripe", "since": "2026-10-01"})
    assert data["since"] == "2026-10-01T00:00:00Z"
    _, err = load_query(schemas.ChangesSchema, {"board": "stripe"})
    assert err and "since" in err["errors"]
    _, err = load_query(schemas.BoardJobsSchema, {"board": "stripe", "posted_since": "yesterday"})
    assert err and "posted_since" in err["errors"]
    _, err = load_query(schemas.BoardJobsSchema, {"board": "stripe", "per_page": "500"})
    assert err and "per_page" in err["errors"]
    _, err = load_query(schemas.BoardSchema, {"board": "stripe", "bogus": "1"})
    assert err and "bogus" in err["errors"]
    data, err = load_query(schemas.EducationSchema, {"board": "figma", "type": "Schools"})
    assert data["type"] == "schools"
    _, err = load_query(schemas.EducationSchema, {"board": "figma"})
    assert err and "type" in err["errors"]
    data, _ = load_query(schemas.JobSchema, {"job": "https://stripe.com/jobs/search?gh_jid=8172510"})
    assert data["job"] == {"id": 8172510, "board": None}


# ---- value helpers ------------------------------------------------------------------------------

def test_values():
    assert P.iso_utc("2026-10-07T19:02:27-04:00") == "2026-10-07T23:02:27Z"
    assert P.iso_utc("2026-10-07") == "2026-10-07T00:00:00Z" and P.iso_utc("junk") is None and P.iso_utc(None) is None
    assert P.unescape_html("&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;") == "<p>Hi &amp; bye</p>"
    assert P.to_text("<p>Hi &amp; bye</p><p>Two</p>") == "Hi & bye\nTwo"
    assert P.split_locations("San Francisco, CA • New York, NY | Remote; London") == ["San Francisco, CA", "New York, NY", "Remote", "London"]
    assert P.split_locations("Remote - USA") == ["Remote - USA"] and P.split_locations(None) == []
    assert P.workplace_type("Remote - USA", []) == "remote" and P.workplace_type("Hybrid - London", []) == "hybrid"
    assert P.workplace_type("Seattle", [{"name": "Location Type", "value": "On-Site"}]) == "on_site"
    assert P.workplace_type("Seattle", [{"name": "Location Type", "value": "Hybrid (Travel-Required)"}]) == "hybrid"
    assert P.workplace_type("Seattle", []) is None
    assert P.employment_type([{"name": "Time Type", "value": "Full time"}], "Engineer") == "full_time"
    assert P.employment_type([], "Software Engineering Intern") == "internship"
    assert P.employment_type([], "Engineer (Contractor)") == "contract" and P.employment_type([], "Engineer") is None
    assert P.seniority("Senior Software Engineer") == "senior" and P.seniority("Staff Engineer") == "staff_principal"
    assert P.seniority("Head of Sales") == "director" and P.seniority("VP, Marketing") == "vp"
    assert P.seniority("Chief of Staff") == "c_level" and P.seniority("Engineering Manager") == "manager"
    assert P.seniority("PhD Intern, Graphics") == "intern" and P.seniority("Software Engineer") is None
    assert P.seniority("Junior Analyst") == "entry" and P.seniority("Account Executive II") == "mid"


@pytest.mark.parametrize("text,expected", [
    ("Annual Base Salary Range:\n$165,000—$190,000 USD\n", (165000, 190000, "USD", "yearly")),
    ("salary range for this role is $269,000 - $370,000,\xa0 depending", (269000, 370000, "USD", "yearly")),
    ("€60k–€80k per year", (60000, 80000, "EUR", "yearly")),
    ("$45 - $60 per hour", (45, 60, "USD", "hourly")),
    ("₹12,00,000 - ₹18,00,000 per year", (1200000, 1800000, "INR", "yearly")),
    ("Compensation: 80.000 - 95.000 EUR", (80000, 95000, "EUR", "yearly")),
    ("salary range 120,000 to 150,000 per annum", (120000, 150000, None, "yearly")),
])
def test_salary_from_text(text, expected):
    out = P.salary_from_text(text)
    assert (out["min"], out["max"], out["currency"], out["period"]) == expected and out["source"] == "description"


@pytest.mark.parametrize("text", ["processing $20M–$50M in annual payment volume", "founded in 2010 - 2015 we",
                                  "deal size of $100k+ and 5-10 years", "between 2.000 and 3.000 users", "", None])
def test_salary_from_text_rejects(text):
    assert P.salary_from_text(text) is None


def test_metadata():
    items = P.metadata_items([
        {"id": 1, "name": "Pay Transparency Range", "value_type": "currency_range",
         "value": {"unit": "USD", "min_value": "59000.0", "max_value": "79000.0"}},
        {"id": 2, "name": "Is this job part of ACC?", "value_type": "yes_no", "value": False},
        {"id": 3, "name": "mule_sourcers_counter_custom", "value_type": "number", "value": "2.0"},
        {"id": 4, "name": "Job Posting Location", "value_type": "multi_select", "value": ["Washington DC, US", None]},
        {"id": 5, "name": "Empty", "value_type": "single_select", "value": None},
        "junk", {"value_type": "short_text", "value": "no name"},
    ])
    assert items[0]["value"] == {"min": 59000, "max": 79000, "currency": "USD"} and items[0]["type"] == "currency_range"
    assert items[1]["value"] is False and items[2]["value"] == 2.0 and items[3]["value"] == ["Washington DC, US"]
    assert items[4]["value"] is None and len(items) == 5
    assert P.salary_from_metadata(items) == {"min": 59000, "max": 79000, "currency": "USD", "period": "yearly", "source": "metadata"}


# ---- parsers on fixtures ------------------------------------------------------------------------------

def test_job_summary_figma():
    raw = fx("board_jobs_figma")
    job = P.job_summary(raw["jobs"][0], "figma")
    assert job["id"] and job["title"] and job["board"] == "figma" and job["company_name"] == "Figma"
    assert job["link"].startswith("https://") and job["board_link"] == f"https://job-boards.greenhouse.io/figma/jobs/{job['id']}"
    assert job["locations"] and job["posted_at"].endswith("Z") and job["updated_at"].endswith("Z")
    assert job["description"] and "<" not in job["description"] and job["description_html"].startswith("<")
    assert job["departments"][0]["name"] and "child_ids" not in job["departments"][0]
    assert job["data_compliance"][0]["type"] == "gdpr" and job["ai_disclaimer"]["opt_out_link"]
    assert job["education_requirement"] in ("required", "optional", None)
    listed = P.strip_description(job)
    assert "description" not in listed and "description_html" not in listed and listed["title"] == job["title"]
    all_jobs = [P.job_summary(j, "figma") for j in raw["jobs"]]
    assert any(j["salary"] and j["salary"]["source"] == "description" for j in all_jobs)
    assert all(j["id"] for j in all_jobs)


def test_job_summary_datadog_metadata():
    raw = fx("board_jobs_datadog")
    jobs_ = [P.job_summary(j, "datadog") for j in raw["jobs"]]
    with_meta = [j for j in jobs_ if j["salary"] and j["salary"]["source"] == "metadata"]
    assert with_meta and with_meta[0]["salary"]["currency"] == "USD"
    assert all(j["employment_type"] == "full_time" for j in jobs_)
    names = {m["name"] for j in jobs_ for m in j["metadata"]}
    assert "Pay Transparency Range" in names


def test_job_summary_anthropic_workplace():
    raw = fx("board_jobs_anthropic")
    jobs_ = [P.job_summary(j, "anthropic") for j in raw["jobs"]]
    assert all(j["workplace_type"] in ("remote", "hybrid", "on_site", None) for j in jobs_)
    assert any(j["workplace_type"] for j in jobs_)


def test_job_details_and_form():
    details = P.job_details(fx("job_figma_5813967004"), "figma")
    assert details["salary"] == {"min": 140000, "max": 296000, "currency": "USD", "period": "yearly", "source": "pay_range"}
    assert details["pay_ranges"][0]["title"] == "Annual Base Salary Range" and details["pay_ranges"][0]["note"]
    assert list(details).index("pay_ranges") == list(details).index("salary") + 1
    form = P.application_form(fx("job_stripe_8172510_questions"), "stripe")
    assert form["job"]["id"] == 8172510 and form["job"]["board_link"].endswith("/stripe/jobs/8172510")
    labels = [q["label"] for q in form["questions"]]
    assert "First Name" in labels and form["questions"][4]["fields"][0]["type"] == "input_file"
    assert form["location_questions"][2]["fields"][0]["name"] == "location"
    assert form["compliance"][0]["type"] == "eeoc" and form["compliance"][0]["questions"][0]["fields"][0]["options"][0]["label"]
    assert form["demographic_questions"] is None


def test_partial_payloads_never_raise():
    assert P.job_summary({}, "x")["id"] is None and P.job_summary(None, None)["board"] is None
    assert P.job_details({"pay_input_ranges": "junk"}, "x")["pay_ranges"] == []
    assert P.application_form({"questions": [None, 1]}, "x")["questions"] == []
    assert P.department_node({"children": "junk"}, "x", True, True)["children"] == []
    assert P.office_node(None, "x")["departments"] == []
    assert P.metadata_items("junk") == [] and P.data_compliance(None) == []
    assert P.demographic_questions({"questions": [{"answer_options": [{"id": "1", "label": "A", "free_form": 1}]}]})["questions"][0]["options"][0]["is_free_form"] is True


def test_trees():
    tree = [P.department_node(d, "figma", False, True) for d in fx("departments_figma_tree")["departments"]]
    assert tree and "children" in tree[0] and "child_ids" not in tree[0] and any(d["jobs_count"] for d in tree)
    flat = [P.department_node(d, "figma", True, False) for d in fx("departments_figma_list")["departments"]]
    assert "child_ids" in flat[0] and any(d["jobs"] for d in flat)
    offices = [P.office_node(o, "figma", False, True) for o in fx("offices_figma_tree")["offices"]]
    assert offices[0]["children"] and all(d["jobs_count"] for o in offices for d in o["departments"])
    assert sum(o["jobs_count"] + sum(c["jobs_count"] for c in o["children"]) for o in offices) >= 0
    assert P.education_item(fx("education_schools_figma")["items"][0]) == {"id": 8471133004, "name": "Stanford University"}


# ---- endpoint functions with the transport monkeypatched -----------------------------------------------

@pytest.fixture
def offline(monkeypatch):
    figma = fx("board_jobs_figma")
    vercel = {"jobs": fx("board_jobs_anthropic")["jobs"], "meta": {"total": 6}}   # a second "board"

    def api(board, path="", params=None, label=None, not_found=None):
        if board == "figma":
            if path == "/jobs":
                return figma
            if path == "":
                return fx("board_root_figma")
            if path == "/jobs/5813967004":
                return fx("job_figma_5813967004_questions") if (params or {}).get("questions") else fx("job_figma_5813967004")
            if path == "/departments":
                return fx("departments_figma_tree") if (params or {}).get("render_as") == "tree" else fx("departments_figma_list")
            if path == "/offices":
                return fx("offices_figma_tree") if (params or {}).get("render_as") == "tree" else fx("offices_figma_list")
            if path == "/sections":
                return fx("sections_figma")
            if path.startswith("/education/"):
                return fx("education_schools_figma")
        if board == "anthropic" and path == "/jobs":
            return vercel
        raise fetch.GreenhouseNotFound(not_found or "no board")

    monkeypatch.setattr(fetch, "api", api)
    monkeypatch.setattr(fetch, "board_of_job", lambda job_id: "figma" if int(job_id) == 5813967004 else (_ for _ in ()).throw(fetch.GreenhouseNotFound("no job")))
    monkeypatch.setattr(fetch, "shared_raw", lambda name, params, build, ttl: build())
    monkeypatch.setattr(fetch, "memoized", lambda key, fn, ttl=0: fn())
    monkeypatch.setattr(boards, "_snapshot_store", lambda: None)
    import cache
    monkeypatch.setattr(cache, "cached_call", lambda ns, data, fn, ttl, **kw: fn(**data))
    return api


def test_endpoints_offline(offline):
    board = boards.get_board("figma")
    assert board["name"] == "Figma" and board["jobs_count"] == 12 and board["departments_count"] == 16
    assert board["top_departments"] and board["latest_posted_at"]
    listing = boards.list_jobs("figma", query="account executive", per_page=2, page=1)
    assert listing["pagination"]["total_count"] >= 1 and len(listing["jobs"]) <= 2 and "description" not in listing["jobs"][0]
    assert boards.list_jobs("figma", include_description=True)["jobs"][0]["description"]
    assert boards.list_jobs("figma", location="London")["pagination"]["total_count"] == 2
    assert boards.list_jobs("figma", posted_since="2099-01-01T00:00:00Z")["pagination"] == {"page": 1, "items_per_page": 20, "total_pages": 0, "total_count": 0}
    assert boards.list_jobs("figma", sort="title")["jobs"][0]["title"] <= boards.list_jobs("figma", sort="title")["jobs"][1]["title"]
    changes = boards.job_changes("figma", "2000-01-01T00:00:00Z")
    assert changes["opened_count"] == 12 and changes["closed"] is None and changes["snapshot"]["is_available"] is False
    assert jobs.get_job({"id": 5813967004, "board": None})["salary"]["source"] == "pay_range"
    assert jobs.get_application_form({"id": 5813967004, "board": "figma"})["questions"]
    with pytest.raises(fetch.GreenhouseNotFound):
        jobs.get_job({"id": 1, "board": None})
    search = jobs.search_jobs(["figma", "anthropic", "missing"], per_page=50)
    assert [b["error"] is None for b in search["boards"]] == [True, True, False] and search["pagination"]["total_count"] == 18
    with pytest.raises(fetch.GreenhouseNotFound):
        jobs.search_jobs(["missing", "missing2"])
    assert boards.list_departments("figma", "tree")["count"] == 16
    assert boards.list_offices("figma", "list", include_jobs=True)["offices"]
    assert boards.list_sections("figma")["count"] == 0
    assert boards.education_options("figma", "schools", query="stanford")["items"][0]["name"] == "Stanford University"


def test_resolve_offline(offline, monkeypatch):
    monkeypatch.setattr(fetch, "run_parallel", lambda fns, workers: [("ok", f()) for f in fns])
    monkeypatch.setattr(fetch, "page", lambda url, label=None: (url, '<a href="https://boards.greenhouse.io/embed/job_board?for=figma">jobs</a>'))
    out = resolve.resolve_company(refs.resolve_company("Figma Inc"))
    assert out["is_resolved"] and out["boards"][0]["board"] == "figma" and out["boards"][0]["jobs_count"] == 144
    assert out["boards"][0]["confidence"] in ("exact", "variant")
    out = resolve.resolve_company(refs.resolve_company("https://www.example-no-board.com/careers"))
    assert out["is_resolved"] and out["boards"][0]["confidence"] == "careers_page" and out["careers_page"]
    assert resolve.candidates_from_domain("getcruise.com")[:2] == ["getcruise", "cruise"]
    assert "datadog" in resolve.candidates_from_name("Datadog, Inc.") and "datadoginc" in resolve.candidates_from_name("Datadog Inc")
