"""Candidate list pagination and job discovery for Greenhouse."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.async_api import Page

from greenhouse_api import ALL_JOBS_URL, CANDIDATE_URL_RE, JOB_DASHBOARD_RE
from models import CandidateRef, JobRef

log = logging.getLogger(__name__)


async def collect_candidate_list(
    page: Page,
    list_url: str,
    max_pages: int = 100,
    max_candidates: Optional[int] = None,
) -> list[CandidateRef]:
    """Walk the paginated candidate list via direct URL navigation (?page=N)."""
    log.info("Opening candidate list: %s", list_url)

    seen: dict[str, CandidateRef] = {}
    parsed = urlparse(list_url)
    base_qs = parse_qs(parsed.query)

    for page_num in range(1, max_pages + 1):
        base_qs["page"] = [str(page_num)]
        new_qs = urlencode({k: v[0] if isinstance(v, list) else v for k, v in base_qs.items()})
        page_url = urlunparse(parsed._replace(query=new_qs))

        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            log.error("Could not open page %d: %s", page_num, e)
            break

        await asyncio.sleep(random.uniform(0.6, 1.0))

        html = await page.content()
        page_matches = list(CANDIDATE_URL_RE.finditer(html))
        new_count = 0
        for m in page_matches:
            pid, aid = m.group("person"), m.group("app")
            detail_url = (
                f"https://app.greenhouse.io/people/{pid}/applications/{aid}/redesign"
                f"?src=search"
            )
            if pid not in seen:
                seen[pid] = CandidateRef(
                    person_id=pid,
                    application_id=aid,
                    detail_url=detail_url,
                )
                new_count += 1
                if max_candidates is not None and len(seen) >= max_candidates:
                    log.info("Reached candidate limit %d on page %d", max_candidates, page_num)
                    return list(seen.values())

        log.info(
            "Page %d: %d matches, %d new, %d unique total",
            page_num, len(page_matches), new_count, len(seen),
        )

        if len(page_matches) == 0:
            log.info("Empty page — end of pagination at page %d", page_num)
            break

    return list(seen.values())


async def collect_accessible_jobs(page: Page) -> list[JobRef]:
    """Return jobs visible to the currently authenticated Greenhouse user."""
    log.info("Opening all jobs: %s", ALL_JOBS_URL)
    response = await page.goto(ALL_JOBS_URL, wait_until="domcontentloaded", timeout=60_000)
    if response and response.status in (401, 403):
        raise RuntimeError(f"Greenhouse all-jobs page returned HTTP {response.status}")
    await asyncio.sleep(2)

    links = await page.locator('a[href*="/sdash/"]').evaluate_all(
        """anchors => anchors.map(anchor => ({
            href: anchor.href,
            text: (anchor.innerText || anchor.textContent || '').trim()
        }))"""
    )
    jobs: dict[str, JobRef] = {}
    for link in links:
        match = JOB_DASHBOARD_RE.search(link.get("href", ""))
        if not match:
            continue
        job_id = match.group(1)
        name = re.sub(r"\s+", " ", link.get("text", "")).strip()
        existing = jobs.get(job_id)
        if existing is None or len(name) > len(existing.name):
            jobs[job_id] = JobRef(
                job_id=job_id,
                name=name or f"Job {job_id}",
                dashboard_url=f"https://app.greenhouse.io/sdash/{job_id}",
            )
    return sorted(jobs.values(), key=lambda job: (job.name.lower(), int(job.job_id)))
