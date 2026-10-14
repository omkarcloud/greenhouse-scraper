"""Raw Job Board API payloads -> clean API objects. Pure functions, no
network; every parser tolerates missing / null / wrong-typed fields and
never raises on a partial payload.

Intentionally dropped: `child_ids` on the departments/offices listed INSIDE
a job (the full tree is /greenhouse/boards/departments and /offices),
`internal_job_id` is kept as `internal_id` (it is the requisition's opening
id, useful for deduping reposts), `data_compliance` is kept (GDPR consent
flags are real data for application builders), `include_ai_disclaimer` /
`ai_disclaimer` / `ai_opt_out_request_url` are grouped as `ai_disclaimer`.
Nothing else in a Greenhouse job is tracking or presentation noise, so
everything else is returned under readable names.
"""
import html as html_lib
import re
from datetime import datetime, timezone

from greenhouse import refs
from greenhouse.shared import as_dict, as_list, g, html_to_text, num, text, to_int


# ---- values -------------------------------------------------------------------------------------

def parse_datetime(value):
    """'2026-10-07T19:02:27-04:00' / '2026-10-07' -> aware UTC datetime, else None."""
    value = text(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value):
    """-> '2026-10-07T23:02:27Z' (UTC) or None."""
    parsed = parse_datetime(value)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ") if parsed else None


def unescape_html(content):
    """Greenhouse serves job HTML entity-escaped (&lt;p&gt;…) -> real HTML."""
    if content is None:
        return None
    out = html_lib.unescape(str(content)).strip()
    return out or None


def to_text(html):
    return html_to_text(html) if html else None


def clean_list(values):
    return [v for v in values if v is not None]


# ---- location / workplace / employment / seniority ------------------------------------------------

_LOCATION_SPLIT_RE = re.compile(r"\s*(?:•|\||;|·)\s*")
_REMOTE_RE = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b|\bdistributed\b|\banywhere\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_ONSITE_RE = re.compile(r"\bon[- ]?site\b|\bin[- ]office\b|\bin[- ]person\b|\boffice[- ]based\b", re.I)
_WORKPLACE_META_RE = re.compile(r"location type|workplace|work ?type|work arrangement|work model|remote|on-?site|"
                                r"hybrid|work setting|office type", re.I)
_EMPLOYMENT_META_RE = re.compile(r"time type|employment type|job type|employment|position type|worker type|"
                                 r"contract type|work schedule|schedule type|hours type", re.I)
_EMPLOYMENT_VALUES = (
    ("internship", re.compile(r"\bintern", re.I)),
    ("part_time", re.compile(r"\bpart[- ]?time\b", re.I)),
    ("full_time", re.compile(r"\bfull[- ]?time\b|\bregular\b|\bpermanent\b", re.I)),
    ("contract", re.compile(r"\bcontract|\bfixed[- ]term\b|\bfreelance\b|\bconsultant\b", re.I)),
    ("temporary", re.compile(r"\btemp(orary)?\b|\bseasonal\b|\bfellowship\b", re.I)),
)
_EMPLOYMENT_TITLE = (
    ("internship", re.compile(r"\bintern(ship)?\b", re.I)),
    ("contract", re.compile(r"\bcontract(or)?\b|\bfreelance\b", re.I)),
    ("part_time", re.compile(r"\bpart[- ]time\b", re.I)),
    ("temporary", re.compile(r"\btemporary\b|\bseasonal\b|\bfixed[- ]term\b", re.I)),
)
_SENIORITY = (
    ("intern", re.compile(r"\bintern(ship)?\b", re.I)),
    ("c_level", re.compile(r"\bchief\b|\b(ceo|cto|cfo|coo|cmo|cpo|ciso|cro|cio)\b", re.I)),
    ("vp", re.compile(r"\b(vp|svp|evp|avp)\b|\bvice president\b", re.I)),
    ("director", re.compile(r"\bdirector\b|\bhead of\b", re.I)),
    ("manager", re.compile(r"\bmanager\b|\bmgr\b", re.I)),
    ("staff_principal", re.compile(r"\b(staff|principal|distinguished|fellow)\b", re.I)),
    ("lead", re.compile(r"\blead\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr)\b\.?", re.I)),
    ("entry", re.compile(r"\b(junior|jr|associate|entry[- ]level|graduate|new grad|apprentice|trainee)\b\.?",
                         re.I)),
    ("mid", re.compile(r"\b(ii|iii|mid[- ]level|intermediate)\b", re.I)),
)


def split_locations(name):
    """'San Francisco, CA • New York, NY' / 'A | B' / 'A; B' -> ['…', '…']."""
    name = text(name)
    if not name:
        return []
    parts = [p.strip(" ,") for p in _LOCATION_SPLIT_RE.split(name)]
    return [p for p in parts if p] or [name]


def _workplace_of(value):
    value = text(value)
    if not value:
        return None
    if _REMOTE_RE.search(value):
        return "remote"
    if _HYBRID_RE.search(value):
        return "hybrid"
    if _ONSITE_RE.search(value):
        return "on_site"
    return None


def workplace_type(location_name, metadata):
    """remote / hybrid / on_site from the board's own custom field when it has
    one (Location Type, Workplace Type, …), else from the location text."""
    for item in metadata or []:
        if item.get("name") and _WORKPLACE_META_RE.search(item["name"]):
            values = item.get("value") if isinstance(item.get("value"), list) else [item.get("value")]
            for value in values:
                found = _workplace_of(value if isinstance(value, str) else None)
                if found:
                    return found
    found = _workplace_of(location_name)
    if found:
        return found
    for item in metadata or []:
        values = item.get("value") if isinstance(item.get("value"), list) else [item.get("value")]
        for value in values:
            if isinstance(value, str) and _HYBRID_RE.search(value):
                return "hybrid"
    return None


def employment_type(metadata, title):
    for item in metadata or []:
        if item.get("name") and _EMPLOYMENT_META_RE.search(item["name"]):
            values = item.get("value") if isinstance(item.get("value"), list) else [item.get("value")]
            for value in values:
                if not isinstance(value, str):
                    continue
                for label, pattern in _EMPLOYMENT_VALUES:
                    if pattern.search(value):
                        return label
    for label, pattern in _EMPLOYMENT_TITLE:
        if title and pattern.search(title):
            return label
    return None


def seniority(title):
    title = text(title)
    if not title:
        return None
    for label, pattern in _SENIORITY:
        if pattern.search(title):
            return label
    return None


# ---- salary ---------------------------------------------------------------------------------------

_CURRENCIES = {"$": "USD", "us$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
               "ca$": "CAD", "cad": "CAD", "c$": "CAD", "a$": "AUD", "aud": "AUD", "au$": "AUD", "₹": "INR",
               "inr": "INR", "rs.": "INR", "rs": "INR", "s$": "SGD", "sgd": "SGD", "chf": "CHF", "nz$": "NZD",
               "nzd": "NZD", "¥": "JPY", "jpy": "JPY", "sek": "SEK", "dkk": "DKK", "nok": "NOK", "pln": "PLN",
               "brl": "BRL", "r$": "BRL", "mxn": "MXN", "zar": "ZAR", "hkd": "HKD", "hk$": "HKD", "ils": "ILS",
               "₪": "ILS", "aed": "AED", "krw": "KRW", "₩": "KRW"}
_CUR = r"(?:US\$|CA\$|C\$|A\$|AU\$|NZ\$|S\$|HK\$|R\$|\$|€|£|₹|¥|₪|₩|USD|EUR|GBP|CAD|AUD|INR|SGD|CHF|NZD|JPY|SEK|DKK|NOK|" \
       r"PLN|BRL|MXN|ZAR|HKD|ILS|AED|KRW|Rs\.?)"
_NUM = r"\d{1,2}(?:,\d{2})+,\d{3}|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?:\.\d{3})+|\d+(?:\.\d+)?"
_SALARY_RE = re.compile(
    rf"(?P<cur>{_CUR})?\s?(?P<min>{_NUM})\s?(?P<kmin>[kK]\b)?\s*(?:-|–|—|to|and)\s*(?P<cur2>{_CUR})?\s?"
    rf"(?P<max>{_NUM})\s?(?P<kmax>[kK]\b)?(?P<tail>[^\n]{{0,60}})")
_HOURLY_RE = re.compile(r"per hour|/\s?hour|/\s?hr\b|hourly|an hour", re.I)
_YEARLY_RE = re.compile(r"per year|/\s?year|/\s?yr\b|annual|yearly|per annum|p\.a\.|a year", re.I)
_MONTHLY_RE = re.compile(r"per month|/\s?month|monthly|a month", re.I)
_SALARY_CONTEXT_RE = re.compile(r"salary|compensation|pay|base|range|ote|wage|rate|remuneration|per annum", re.I)


def _money(raw, k):
    """'165,000' / '12,00,000' (lakh) / '80.000' (European thousands) /
    '28.26' -> float; k = a 'k' suffix."""
    raw = (raw or "").strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    value = num(raw)
    if value is None:
        return None
    return value * 1000 if k else value


def _period(tail, lo, hi):
    tail = tail or ""
    if _HOURLY_RE.search(tail):
        return "hourly"
    if _MONTHLY_RE.search(tail):
        return "monthly"
    if _YEARLY_RE.search(tail):
        return "yearly"
    if hi is not None and hi >= 10000:
        return "yearly"
    if hi is not None and hi <= 500:
        return "hourly"
    return None


def _amount(value):
    if value is None:
        return None
    return int(value) if float(value).is_integer() else round(value, 2)


def salary_from_text(description):
    """The first plausible pay range in a description: '$140,000 - $296,000
    USD per year' / '€60k–€80k' / '$45 - $60 per hour' -> a salary object,
    else None. Only ranges with a currency marker (or an explicit salary /
    compensation context) count, so years, dates and job ids are skipped."""
    if not description:
        return None
    for match in _SALARY_RE.finditer(description):
        currency = _CURRENCIES.get((match.group("cur") or match.group("cur2") or "").lower().strip())
        if currency is None:
            trailing = re.match(rf"\s?({_CUR})(?![A-Za-z])", match.group("tail") or "")
            currency = _CURRENCIES.get(trailing.group(1).lower()) if trailing else None
        if not currency:
            # a bare range only counts next to salary wording ("salary range 120,000 to 150,000")
            start = max(0, match.start() - 80)
            if not _SALARY_CONTEXT_RE.search(description[start:match.start()]):
                continue
        lo = _money(match.group("min"), match.group("kmin"))
        hi = _money(match.group("max"), match.group("kmax"))
        if lo is None or hi is None or lo <= 0 or hi < lo or hi > 5_000_000:
            continue
        if hi < 10 or (hi < 1000 and not _HOURLY_RE.search(match.group("tail") or "") and currency is None):
            continue
        period = _period(match.group("tail"), lo, hi)
        if currency is None and period is None:
            continue
        return {"min": _amount(lo), "max": _amount(hi), "currency": currency, "period": period, "source": "description"}
    return None


def salary_from_metadata(metadata):
    """A board's custom 'Pay Transparency Range' field (currency_range) -> salary."""
    for item in metadata or []:
        value = item.get("value")
        if item.get("type") == "currency_range" and isinstance(value, dict):
            lo, hi = value.get("min"), value.get("max")
            if lo is None and hi is None:
                continue
            return {"min": _amount(lo), "max": _amount(hi), "currency": value.get("currency"),
                    "period": "yearly" if (hi or lo or 0) >= 10000 else None, "source": "metadata"}
    return None


def pay_range(raw):
    raw = as_dict(raw)
    lo, hi = num(raw.get("min_cents")), num(raw.get("max_cents"))
    if lo is None and hi is None:
        return None
    title = text(raw.get("title"))
    lo_major = lo / 100 if lo is not None else None
    hi_major = hi / 100 if hi is not None else None
    # the title names the period when it can ("Annual Salary", "Hourly
    # Range"); a bare "Pay Range" falls back to the amount (>= 10,000 yearly)
    period = _period(title or "", None, None)
    if period is None and title:
        period = "hourly" if _HOURLY_RE.search(title) else ("yearly" if re.search(r"annual|year|salary", title, re.I) else None)
    if period is None:
        period = _period("", lo_major, hi_major if hi_major is not None else lo_major)
    return {"title": title.rstrip(":").strip() if title else None,
            "min": _amount(lo_major),
            "max": _amount(hi_major),
            "currency": text(raw.get("currency_type")), "period": period, "note": to_text(raw.get("blurb"))}


def pay_ranges(raw_list):
    return clean_list([pay_range(r) for r in as_list(raw_list)])


def salary_from_pay_ranges(ranges):
    for item in ranges or []:
        if item.get("min") is not None or item.get("max") is not None:
            return {"min": item["min"], "max": item["max"], "currency": item["currency"], "period": item["period"],
                    "source": "pay_range"}
    return None


# ---- metadata / compliance -----------------------------------------------------------------------

def _metadata_value(value_type, value):
    if value is None:
        return None
    if value_type == "yes_no":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("yes", "true", "1")
    if value_type == "number":
        return num(value)
    if value_type == "currency_range" and isinstance(value, dict):
        return {"min": _amount(num(value.get("min_value"))), "max": _amount(num(value.get("max_value"))),
                "currency": text(value.get("unit"))}
    if value_type == "currency" and isinstance(value, dict):
        return {"amount": _amount(num(value.get("amount"))), "currency": text(value.get("unit"))}
    if isinstance(value, list):
        return clean_list([text(v) if not isinstance(v, (dict, list)) else v for v in value])
    if isinstance(value, dict):
        return value
    return text(value)


def metadata_items(raw):
    """A board's custom fields: [{id, name, type, value}] with typed values."""
    out = []
    for item in as_list(raw):
        if not isinstance(item, dict):
            continue
        name = text(item.get("name"))
        if not name:
            continue
        value_type = text(item.get("value_type"))
        out.append({"id": to_int(item.get("id")), "name": name, "type": value_type,
                    "value": _metadata_value(value_type, item.get("value"))})
    return out


def data_compliance(raw):
    out = []
    for item in as_list(raw):
        if not isinstance(item, dict):
            continue
        out.append({"type": text(item.get("type")),
                    "is_consent_required": bool(item.get("requires_consent")),
                    "is_processing_consent_required": bool(item.get("requires_processing_consent")),
                    "is_retention_consent_required": bool(item.get("requires_retention_consent")),
                    "retention_period": text(item.get("retention_period")),
                    "does_demographic_consent_apply": bool(item.get("demographic_data_consent_applies"))})
    return out


def ai_disclaimer(raw):
    return {"is_shown": bool(raw.get("include_ai_disclaimer")), "text": to_text(raw.get("ai_disclaimer")),
            "opt_out_link": text(raw.get("ai_opt_out_request_url"))}


def education_requirement(value):
    value = text(value)
    if not value:
        return None
    return {"education_required": "required", "education_optional": "optional"}.get(value, value)


# ---- departments / offices ----------------------------------------------------------------------

def departments_brief(raw):
    out = []
    for item in as_list(raw):
        if isinstance(item, dict) and item.get("id") is not None:
            out.append({"id": to_int(item.get("id")), "name": text(item.get("name")),
                        "parent_id": to_int(item.get("parent_id"))})
    return out


def offices_brief(raw):
    out = []
    for item in as_list(raw):
        if isinstance(item, dict) and item.get("id") is not None:
            out.append({"id": to_int(item.get("id")), "name": text(item.get("name")),
                        "location": text(item.get("location")), "parent_id": to_int(item.get("parent_id"))})
    return out


# ---- jobs -----------------------------------------------------------------------------------------

def job_summary(raw, board):
    """One job of a board listing (/jobs?content=true) -> the API object,
    description included (listings drop it unless asked, see
    strip_description)."""
    raw = as_dict(raw)
    job_id = to_int(raw.get("id"))
    title = text(raw.get("title"))
    html = unescape_html(raw.get("content"))
    description = to_text(html)
    metadata = metadata_items(raw.get("metadata"))
    location_name = text(g(raw, "location", "name"))
    board = (board or "").lower() or None
    return {
        "id": job_id,
        "title": title,
        "board": board,
        "company_name": text(raw.get("company_name")),
        "link": text(raw.get("absolute_url")),
        "board_link": refs.job_link(board, job_id) if board and job_id else None,
        "location": location_name,
        "locations": split_locations(location_name),
        "workplace_type": workplace_type(location_name, metadata),
        "employment_type": employment_type(metadata, title),
        "seniority": seniority(title),
        "salary": salary_from_metadata(metadata) or salary_from_text(description),
        "departments": departments_brief(raw.get("departments")),
        "offices": offices_brief(raw.get("offices")),
        "description": description,
        "description_html": html,
        "posted_at": iso_utc(raw.get("first_published")),
        "updated_at": iso_utc(raw.get("updated_at")),
        "application_deadline": iso_utc(raw.get("application_deadline")),
        "language": text(raw.get("language")),
        "requisition_id": text(raw.get("requisition_id")),
        "internal_id": to_int(raw.get("internal_job_id")),
        "education_requirement": education_requirement(raw.get("education")),
        "is_employment_history_required": (text(raw.get("employment")) == "employment_required") if raw.get("employment") is not None else None,
        "metadata": metadata,
        "data_compliance": data_compliance(raw.get("data_compliance")),
        "ai_disclaimer": ai_disclaimer(raw),
    }


def strip_description(job):
    """A listing row: the summary without the (large) description fields."""
    return {k: v for k, v in job.items() if k not in ("description", "description_html")}


def job_details(raw, board):
    """/jobs/<id>?pay_transparency=true -> the full job with pay ranges."""
    out = job_summary(raw, board)
    ranges = pay_ranges(as_dict(raw).get("pay_input_ranges"))
    out["salary"] = salary_from_pay_ranges(ranges) or out["salary"]
    # keep salary and pay_ranges together
    ordered = {}
    for key, value in out.items():
        ordered[key] = value
        if key == "salary":
            ordered["pay_ranges"] = ranges
    return ordered


# ---- application form -----------------------------------------------------------------------------

def _field(raw):
    raw = as_dict(raw)
    options = []
    for value in as_list(raw.get("values")):
        if isinstance(value, dict):
            options.append({"label": text(value.get("label")), "value": value.get("value")})
    return {"name": text(raw.get("name")), "type": text(raw.get("type")), "options": options}


def question(raw):
    raw = as_dict(raw)
    return {"label": text(raw.get("label")), "description": to_text(raw.get("description")),
            "is_required": bool(raw.get("required")),
            "fields": [_field(f) for f in as_list(raw.get("fields")) if isinstance(f, dict)]}


def questions(raw):
    return [question(q) for q in as_list(raw) if isinstance(q, dict)]


def compliance_sections(raw):
    out = []
    for item in as_list(raw):
        if not isinstance(item, dict):
            continue
        out.append({"type": text(item.get("type")), "description": to_text(item.get("description")),
                    "questions": questions(item.get("questions"))})
    return out


def demographic_questions(raw):
    """Greenhouse's demographic section: {header, description, questions:
    [{id, label, required, type, answer_options: [{id, label, free_form}]}]}."""
    if isinstance(raw, list):
        return {"header": None, "description": None, "questions": questions(raw)}
    raw = as_dict(raw)
    if not raw:
        return None
    out = []
    for item in as_list(raw.get("questions")):
        if not isinstance(item, dict):
            continue
        options = []
        for option in as_list(item.get("answer_options")):
            if isinstance(option, dict):
                options.append({"id": to_int(option.get("id")), "label": text(option.get("label")),
                                "is_free_form": bool(option.get("free_form"))})
        out.append({"id": to_int(item.get("id")), "label": text(item.get("label")),
                    "is_required": bool(item.get("required")), "type": text(item.get("type")), "options": options})
    return {"header": text(raw.get("header")), "description": to_text(raw.get("description")), "questions": out}


def application_form(raw, board):
    """/jobs/<id>?questions=true -> every question an applicant answers."""
    raw = as_dict(raw)
    job_id = to_int(raw.get("id"))
    return {
        "job": {"id": job_id, "title": text(raw.get("title")), "board": board, "link": text(raw.get("absolute_url")),
                "board_link": refs.job_link(board, job_id) if board and job_id else None},
        "questions": questions(raw.get("questions")),
        "location_questions": questions(raw.get("location_questions")),
        "compliance": compliance_sections(raw.get("compliance")),
        "demographic_questions": demographic_questions(raw.get("demographic_questions")),
        "data_compliance": data_compliance(raw.get("data_compliance")),
    }


# ---- trees -----------------------------------------------------------------------------------------

def _jobs_of(raw, board, include_jobs):
    jobs = [j for j in as_list(raw.get("jobs")) if isinstance(j, dict)]
    out = {"jobs_count": len(jobs)}
    if include_jobs:
        out["jobs"] = [strip_description(job_summary(j, board)) for j in jobs]
    return out


def department_node(raw, board, include_jobs=False, tree=False):
    raw = as_dict(raw)
    node = {"id": to_int(raw.get("id")), "name": text(raw.get("name")), "parent_id": to_int(raw.get("parent_id"))}
    node.update(_jobs_of(raw, board, include_jobs))
    if tree:
        node["children"] = [department_node(c, board, include_jobs, True) for c in as_list(raw.get("children"))
                            if isinstance(c, dict)]
    else:
        node["child_ids"] = [to_int(c) for c in as_list(raw.get("child_ids")) if c is not None]
    return node


def office_node(raw, board, include_jobs=False, tree=False):
    raw = as_dict(raw)
    departments = [department_node(d, board, include_jobs, False) for d in as_list(raw.get("departments"))
                   if isinstance(d, dict)]
    # an office lists EVERY department of the board, nearly all with no jobs
    # at that office: only the departments hiring there are kept
    node = {"id": to_int(raw.get("id")), "name": text(raw.get("name")), "location": text(raw.get("location")),
            "parent_id": to_int(raw.get("parent_id")),
            "jobs_count": sum(d.get("jobs_count") or 0 for d in departments),
            "departments": [d for d in departments if d.get("jobs_count")]}
    if tree:
        node["children"] = [office_node(c, board, include_jobs, True) for c in as_list(raw.get("children"))
                            if isinstance(c, dict)]
    else:
        node["child_ids"] = [to_int(c) for c in as_list(raw.get("child_ids")) if c is not None]
    return node


def section(raw, board):
    raw = as_dict(raw)
    node = {"id": to_int(raw.get("id")), "name": text(raw.get("name"))}
    node.update(_jobs_of(raw, board, True))
    return node


def education_item(raw):
    raw = as_dict(raw)
    return {"id": to_int(raw.get("id")), "name": text(raw.get("text"))}
