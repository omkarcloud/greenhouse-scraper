"""Marshmallow request schemas for every /greenhouse/* route.

Generic fields live in the shared top-level schema_fields.py; this module
adds the Greenhouse resolvers (refs.py). Every schema's load() output is
the kwargs dict its endpoint function takes.

ONE param per input (tripadvisor QueryOrLinkField convention, never a
sibling `url`/`id` pair): `board` takes a board token OR any
boards.greenhouse.io / job-boards.greenhouse.io link; `job` a job id, a
Greenhouse job link or a careers-page link carrying gh_jid; `boards` a
comma list of either; `company` a name, a website domain or a board link.
"""
import re
from datetime import datetime, timedelta, timezone

from marshmallow import ValidationError, missing, validate

from greenhouse import refs
from schema_fields import (BaseSchema, ChoiceField, Flag, NonNegativeNumber, PageField, PageSizeField,
                           PositiveInt, RefField, StrippedString)

WORKPLACE_TYPES = ["remote", "hybrid", "on_site"]
EMPLOYMENT_TYPES = ["full_time", "part_time", "contract", "internship", "temporary"]
SENIORITIES = ["intern", "entry", "mid", "senior", "lead", "staff_principal", "manager", "director", "vp", "c_level"]
SORTS = ["newest", "oldest", "updated", "title"]
RENDERINGS = ["list", "tree"]
EDUCATION_TYPES = ["degrees", "disciplines", "schools"]
MAX_PAGE = 500
_RELATIVE_RE = re.compile(r"^(\d{1,4})\s*(h|hours?|d|days?|w|weeks?|m|months?)$", re.I)


class BoardField(RefField):
    resolver = staticmethod(refs.resolve_board)


class JobField(RefField):
    resolver = staticmethod(refs.resolve_job)


class BoardsField(RefField):
    resolver = staticmethod(refs.resolve_boards)


class CompanyField(RefField):
    resolver = staticmethod(refs.resolve_company)


class SinceField(StrippedString):
    """A moment: relative ('24h', '7d', '2w', '3m') or absolute (YYYY-MM-DD or
    an ISO datetime) -> an ISO UTC timestamp. Relative values are rounded
    down to the hour so the same '7d' shares a cache row for an hour."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        match = _RELATIVE_RE.match(value)
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if match:
            amount, unit = int(match.group(1)), match.group(2).lower()[0]
            delta = {"h": timedelta(hours=amount), "d": timedelta(days=amount), "w": timedelta(weeks=amount),
                     "m": timedelta(days=30 * amount)}[unit]
            if delta > timedelta(days=3660):
                raise ValidationError("At most 10 years back.")
            return (now - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError("Must be relative (24h, 7d, 2w, 3m) or a date (YYYY-MM-DD or ISO datetime).")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed > datetime.now(timezone.utc) + timedelta(days=1):
            raise ValidationError("Must not be in the future.")
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OptionalText(StrippedString):
    def __init__(self, max_length=200, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        kwargs.setdefault("validate", validate.Length(min=1, max=max_length))
        super().__init__(**kwargs)


class JobFiltersSchema(BaseSchema):
    """The filters, sort and paging every job listing takes."""
    query = OptionalText()
    in_description = Flag(load_default=False)
    department = OptionalText()
    office = OptionalText()
    location = OptionalText()
    workplace_type = ChoiceField(WORKPLACE_TYPES)
    employment_type = ChoiceField(EMPLOYMENT_TYPES)
    seniority = ChoiceField(SENIORITIES)
    posted_since = SinceField()
    updated_since = SinceField()
    has_salary = Flag()
    min_salary = NonNegativeNumber()
    sort = ChoiceField(SORTS, load_default="newest")
    page = PageField(max_page=MAX_PAGE)
    per_page = PageSizeField(default=20, max_size=100)
    include_description = Flag(load_default=False)


class BoardSchema(BaseSchema):
    board = BoardField()


class BoardJobsSchema(JobFiltersSchema):
    board = BoardField()


class SearchSchema(JobFiltersSchema):
    boards = BoardsField()


class JobSchema(BaseSchema):
    job = JobField()


class TreeSchema(BaseSchema):
    board = BoardField()
    render_as = ChoiceField(RENDERINGS, load_default="list")
    include_jobs = Flag(load_default=False)


class ChangesSchema(BaseSchema):
    board = BoardField()
    since = SinceField(required=True, load_default=missing)


class ResolveSchema(BaseSchema):
    company = CompanyField()


class EducationSchema(BaseSchema):
    board = BoardField()
    type = ChoiceField(EDUCATION_TYPES, required=True, load_default=missing)
    query = OptionalText(max_length=100)
    page = PositiveInt(max_value=1000)
