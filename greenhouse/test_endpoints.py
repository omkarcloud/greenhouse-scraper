"""Live endpoint smoke tests: one call per /greenhouse/* route against a
running service, with example values proven to return data (2026-10-14).
The listing tooling reads each route's FIRST call from this file's AST as
its working example, so the values stay literals.

Skipped unless GREENHOUSE_BASE points at a running service:

    SCRAPERS_PORT=6071 ONLY_SCRAPER=greenhouse python run.py
    GREENHOUSE_BASE=http://127.0.0.1:6071 python -m pytest greenhouse/test_endpoints.py -q
"""
import os

import pytest

BASE = os.environ.get("GREENHOUSE_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set GREENHOUSE_BASE to run live endpoint tests")


def call(path, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=300)
    assert resp.status_code == 200, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


def status(path, **params):
    from curl_cffi import requests
    return requests.get(BASE + path, params=params, timeout=300).status_code


def test_board_details():
    body = call("/greenhouse/boards/details", board="stripe")
    assert body["name"] == "Stripe" and body["jobs_count"] > 100 and body["departments_count"] > 10
    assert body["top_departments"][0]["jobs_count"] and body["latest_posted_at"]
    body = call("/greenhouse/boards/details", board="https://job-boards.greenhouse.io/anthropic")
    assert body["board"] == "anthropic" and body["workplace_types"]["on_site"] > 0


def test_board_jobs():
    body = call("/greenhouse/boards/jobs", board="stripe", query="engineer", location="remote", per_page=20)
    assert body["count"] > 5 and body["jobs"] and body["per_page"] == 20 and body["jobs"][0]["board_link"]
    job = body["jobs"][0]
    assert "engineer" in job["title"].lower() and job["posted_at"] and "description" not in job
    body = call("/greenhouse/boards/jobs", board="cloudflare", department="Engineering", workplace_type="hybrid",
                seniority="senior", posted_since="90d", has_salary="true", min_salary=150000, sort="updated",
                include_description="true")
    assert body["jobs"] and all(j["salary"]["max"] >= 150000 and j["workplace_type"] == "hybrid" for j in body["jobs"])
    assert body["jobs"][0]["description"] and body["jobs"][0]["description_html"].startswith("<")
    page2 = call("/greenhouse/boards/jobs", board="stripe", per_page=5, page=2)
    assert page2["current_page"] == 2 and page2["previous"] and len(page2["jobs"]) == 5


def test_job_details():
    body = call("/greenhouse/jobs/details", job="5813967004")
    assert body["board"] == "figma" and body["salary"]["source"] == "pay_range" and body["pay_ranges"][0]["min"] > 0
    assert body["description"] and body["description_html"] and body["departments"][0]["name"]
    body = call("/greenhouse/jobs/details", job="https://job-boards.greenhouse.io/figma/jobs/5813967004")
    assert body["id"] == 5813967004
    body = call("/greenhouse/jobs/details", job="https://stripe.com/jobs/search?gh_jid=8172510")
    assert body["board"] == "stripe" and body["title"]


def test_application_form():
    body = call("/greenhouse/jobs/application-form", job="https://stripe.com/jobs/search?gh_jid=8172510")
    labels = [q["label"] for q in body["questions"]]
    assert "First Name" in labels and "Email" in labels and body["location_questions"] and body["job"]["board"] == "stripe"
    assert body["compliance"][0]["type"] == "eeoc" and body["compliance"][0]["questions"][0]["fields"][0]["options"]


def test_departments_and_offices():
    body = call("/greenhouse/boards/departments", board="figma", render_as="tree")
    assert body["count"] > 5 and "children" in body["departments"][0] and any(d["jobs_count"] for d in body["departments"])
    body = call("/greenhouse/boards/offices", board="figma", render_as="tree", include_jobs="true")
    office = body["offices"][0]
    assert office["children"] and office["name"]
    flat = call("/greenhouse/boards/offices", board="figma")
    assert flat["count"] > 10 and "child_ids" in flat["offices"][0]


def test_sections_and_education():
    body = call("/greenhouse/boards/sections", board="figma")
    assert body["count"] == len(body["sections"])
    body = call("/greenhouse/boards/education", board="figma", type="schools", query="stanford")
    assert body["items"][0]["name"] == "Stanford University"


def test_changes():
    body = call("/greenhouse/boards/changes", board="vercel", since="90d")
    assert body["opened_count"] >= 1 and body["opened"][0]["posted_at"] >= body["since"] and "closed" in body
    assert body["snapshot"]["is_available"] in (True, False)


def test_search():
    body = call("/greenhouse/jobs/search", boards="stripe,figma,vercel,anthropic", query="product manager",
                per_page=25)
    assert body["count"] > 3 and len(body["boards"]) == 4 and all(b["error"] is None for b in body["boards"])
    assert {j["board"] for j in body["jobs"]} <= {"stripe", "figma", "vercel", "anthropic"}
    body = call("/greenhouse/jobs/search", boards="vercel,no-such-board-xyz", per_page=5)
    assert body["boards"][1]["error"] and body["jobs"]


def test_resolve():
    body = call("/greenhouse/boards/resolve", company="Datadog")
    assert body["is_resolved"] and body["boards"][0]["board"] == "datadog" and body["boards"][0]["jobs_count"] > 50
    body = call("/greenhouse/boards/resolve", company="stripe.com")
    assert body["boards"][0]["board"] == "stripe"


def test_errors():
    assert status("/greenhouse/boards/jobs", board="no-such-board-xyz") == 404
    assert status("/greenhouse/jobs/details", job="1") == 404
    assert status("/greenhouse/boards/jobs", board="stripe", bogus=1) == 400
    assert status("/greenhouse/boards/details", board="https://example.com/x") == 400
    assert status("/greenhouse/jobs/search", boards=",".join(["stripe"] * 1 + [f"b{i}" for i in range(11)])) == 400
    assert status("/greenhouse/boards/changes", board="stripe") == 400
