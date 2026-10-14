"""Cache TTL per /greenhouse/* endpoint (cache.py, keyed on the validated
params — marshmallow fills the defaults, so an omitted param and its default
share a row).

A board's job list is the one upstream payload most routes read (listing
pages, search, changes, the board profile): it is cached raw for a short
while so paging and filtering never refetch 10 MB. Boards open and close
jobs a few times a day; a single job changes rarely once posted; the
application form, department and office trees and education lookups move
weekly at most. A bare job id's board never changes."""
from datetime import timedelta

# --- raw upstream payloads shared by several routes (fetch.shared_raw) ----------
BOARD_JOBS_RAW_CACHE = timedelta(minutes=20)   # /jobs?content=true (the whole board)
BOARD_ROOT_RAW_CACHE = timedelta(days=1)       # board name + intro content
JOB_BOARD_CACHE = timedelta(days=30)           # bare job id -> board token (embed redirect)

# --- routes -----------------------------------------------------------------------
BOARD_CACHE = timedelta(hours=1)
JOBS_CACHE = timedelta(minutes=20)
SEARCH_CACHE = timedelta(minutes=20)
JOB_CACHE = timedelta(hours=6)
APPLICATION_FORM_CACHE = timedelta(days=1)
DEPARTMENTS_CACHE = timedelta(hours=6)
OFFICES_CACHE = timedelta(hours=6)
SECTIONS_CACHE = timedelta(hours=6)
EDUCATION_CACHE = timedelta(days=7)
RESOLVE_CACHE = timedelta(days=3)
SNAPSHOT_TTL = timedelta(days=60)              # the per-board job-id snapshot behind /boards/changes
