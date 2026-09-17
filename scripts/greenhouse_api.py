"""Greenhouse internal API contract.

Single source of truth for URLs, regexes, and field conventions used by
both the Python crawler and the Chrome extension. When Greenhouse changes
its DOM or endpoints, update this file.

The Chrome extension (page-operations.js) duplicates these constants in
JavaScript — keep them in sync. The canonical deduplication key for
candidates is person_id (not application_id).
"""

from __future__ import annotations

import re

LOGIN_URL = "https://app.greenhouse.io/sdash"
ALL_JOBS_URL = "https://app.greenhouse.io/alljobs"
BULK_EXPORT_URL = "https://app.greenhouse.io/people/bulk/print_resumes"

CANDIDATE_URL_RE = re.compile(
    r"/people/(?P<person>\d+)/applications/(?P<app>\d+)(?:/redesign)?"
)

ATTACHMENT_PREVIEW_RE = re.compile(r"/attachment_previews/(\d+)")

JOB_DASHBOARD_RE = re.compile(r"/sdash/(\d+)")

BULK_EXPORT_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

DEDUP_KEY = "person_id"

BATCH_SIZE_MAX = 30


def candidate_list_url(job_id: str) -> str:
    return (
        f"https://app.greenhouse.io/plans/{job_id}/candidates"
        f"?hiring_plan_id={job_id}&job_status=open&sort=last_activity+desc"
        "&stage_status_id=2&type=all"
    )
