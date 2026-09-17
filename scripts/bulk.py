"""Greenhouse native bulk resume export via internal API."""

from __future__ import annotations

import asyncio
import logging
import random
from urllib.parse import urlencode

from playwright.async_api import Page

from greenhouse_api import BATCH_SIZE_MAX, BULK_EXPORT_HEADERS, BULK_EXPORT_URL
from models import CandidateRef, CrawlResult

log = logging.getLogger(__name__)


async def request_bulk_resume_exports(
    page: Page,
    candidates: list[CandidateRef],
    batch_size: int = 30,
) -> list[CrawlResult]:
    """Ask Greenhouse to email its native merged-resume exports.

    Greenhouse's UI limits each request to 30 applications. Each
    successful batch becomes a separate email attachment.
    """
    if not 1 <= batch_size <= BATCH_SIZE_MAX:
        raise ValueError(f"bulk batch size must be between 1 and {BATCH_SIZE_MAX}")

    try:
        csrf_token = await page.locator('meta[name="csrf-token"]').get_attribute("content")
    except Exception:
        csrf_token = None
    if not csrf_token:
        raise RuntimeError("Greenhouse CSRF token was not found on the candidate list page")

    results: list[CrawlResult] = []
    batches = [candidates[i:i + batch_size] for i in range(0, len(candidates), batch_size)]
    for batch_number, batch in enumerate(batches, start=1):
        body = urlencode(
            [("sort", "")]
            + [("application_ids[]", candidate.application_id) for candidate in batch]
        )
        response = await page.request.post(
            BULK_EXPORT_URL,
            data=body,
            headers={
                **BULK_EXPORT_HEADERS,
                "X-CSRF-Token": csrf_token,
                "Referer": page.url,
            },
        )
        response_text = await response.text()
        success = response.ok
        if success:
            try:
                success = (await response.json()).get("status") == "success"
            except Exception:
                success = False

        log.info(
            "Bulk resume export batch %d/%d: %s (%d applications)",
            batch_number, len(batches), "submitted" if success else "failed", len(batch),
        )
        for candidate in batch:
            results.append(CrawlResult(
                candidate_id=candidate.person_id,
                name=candidate.name,
                status="bulk_requested" if success else "bulk_request_failed",
                detail_url=candidate.detail_url,
                error="" if success else f"HTTP {response.status}: {response_text[:300]}",
            ))
        if not success:
            break
        await asyncio.sleep(random.uniform(1.0, 2.0))

    return results
