"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json.
"""
import json
import os

from greenhouse.boards import list_jobs
from greenhouse.jobs import get_job
from greenhouse.resolve import resolve_company
from greenhouse.refs import resolve_company as parse_company

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # every open job at SpaceX, filtered and paged (20 per page)
    save("spacex_engineer_jobs.json", list_jobs("spacex", query="engineer", location="Hawthorne"))

    # one job in full, pay ranges included (an id, a Greenhouse link or a careers link with gh_jid)
    save("job_4017331008.json", get_job({"id": 4017331008, "board": None}))

    # a company name or domain -> its Greenhouse board
    save("resolve_airbnb.json", resolve_company(parse_company("airbnb.com")))
