"""Single-candidate resume download with retry and checkpointing."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path

from playwright.async_api import BrowserContext

from attachment import (
    collect_attachments,
    find_resume_attachment_id,
    pick_resume_id_from_captures,
    preview_from_resume_button,
    unwrap_preview_source,
)
from models import CandidateRef, CrawlResult, slugify

log = logging.getLogger(__name__)


async def fetch_one_candidate(
    context: BrowserContext,
    candidate: CandidateRef,
    raw_dir: Path,
    sem: asyncio.Semaphore,
) -> CrawlResult:
    """Download the resume for a single candidate. Resumable via filesystem."""
    async with sem:
        for existing in raw_dir.glob(f"{candidate.person_id}_*.pdf"):
            if not existing.name.endswith("_pending.pdf"):
                log.info("[%s] already downloaded — skipping", candidate.person_id)
                return CrawlResult(
                    candidate_id=candidate.person_id,
                    name=candidate.name or existing.stem.split("_", 1)[1].replace("-", " "),
                    status="ok",
                    detail_url=candidate.detail_url,
                    pdf_path=str(existing),
                )

        page = await context.new_page()
        captured_attachments: list[dict] = []

        async def on_response(response):
            try:
                url = response.url
                ct = response.headers.get("content-type", "")
                if "json" not in ct.lower():
                    return
                if "app.greenhouse.io" not in url:
                    return
                if not any(seg in url for seg in ["/people/", "/applications/", "/attachments/"]):
                    return
                body = await response.body()
                if not body:
                    return
                try:
                    data = json.loads(body)
                except Exception:
                    return
                collect_attachments(data, captured_attachments, url)
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        try:
            for attempt in range(3):
                navigation = await page.goto(
                    candidate.detail_url,
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                status = navigation.status if navigation else 0
                body_text = (await page.locator("body").inner_text()).lstrip()
                blocked = status in (403, 429) or body_text.startswith("403 ERROR")
                if not blocked:
                    break
                delay = (5, 15, 30)[attempt] + random.uniform(0, 3)
                log.warning(
                    "[%s] detail page blocked (HTTP %s); retrying in %.1fs",
                    candidate.person_id, status or "unknown", delay,
                )
                await asyncio.sleep(delay)
            else:
                return CrawlResult(
                    candidate_id=candidate.person_id,
                    name=candidate.name,
                    status="error",
                    detail_url=candidate.detail_url,
                    error="candidate detail page blocked by CloudFront after 3 retries",
                )

            await asyncio.sleep(random.uniform(2.5, 4.0))

            try:
                await page.locator('h1').first.wait_for(state="visible", timeout=10_000)
            except Exception:
                pass

            try:
                candidate.name = (await page.locator(
                    'h1, [data-test="candidate-name"]'
                ).first.inner_text()).strip()
            except Exception:
                pass

            attachment_id, pdf_url = await preview_from_resume_button(page)
            log.debug(
                "[%s] resume button attachment_id=%s source=%s",
                candidate.person_id, attachment_id, bool(pdf_url),
            )

            if not attachment_id:
                attachment_id = await find_resume_attachment_id(page)
                log.debug("[%s] DOM attachment_id: %s", candidate.person_id, attachment_id)

            if attachment_id is None and captured_attachments:
                attachment_id = pick_resume_id_from_captures(captured_attachments)
                log.debug("[%s] captured attachment_id: %s", candidate.person_id, attachment_id)

            if attachment_id is None:
                return CrawlResult(
                    candidate_id=candidate.person_id,
                    name=candidate.name,
                    status="no_resume",
                    detail_url=candidate.detail_url,
                    error="no resume found via DOM or API capture",
                )

            if not pdf_url:
                preview_api = f"https://app.greenhouse.io/attachment_previews/{attachment_id}?width=800"
                resp = await page.request.get(preview_api)
                if not resp.ok:
                    return CrawlResult(
                        candidate_id=candidate.person_id,
                        name=candidate.name,
                        status="download_failed",
                        detail_url=candidate.detail_url,
                        error=f"attachment_previews returned {resp.status}",
                    )
                preview = await resp.json()
                pdf_url = unwrap_preview_source(preview.get("source", ""))
            if not pdf_url:
                return CrawlResult(
                    candidate_id=candidate.person_id,
                    name=candidate.name,
                    status="download_failed",
                    detail_url=candidate.detail_url,
                    error="attachment_previews returned no source URL",
                )

            pdf_resp = await page.request.get(pdf_url)
            if not pdf_resp.ok:
                return CrawlResult(
                    candidate_id=candidate.person_id,
                    name=candidate.name,
                    status="download_failed",
                    detail_url=candidate.detail_url,
                    error=f"S3 download returned {pdf_resp.status}",
                )
            pdf_bytes = await pdf_resp.body()
            if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
                return CrawlResult(
                    candidate_id=candidate.person_id,
                    name=candidate.name,
                    status="download_failed",
                    detail_url=candidate.detail_url,
                    error=f"downloaded payload is not a PDF ({len(pdf_bytes)} bytes)",
                )

            final_name = f"{candidate.person_id}_{slugify(candidate.name or f'cid-{candidate.person_id}')}.pdf"
            final_path = raw_dir / final_name
            out_path = raw_dir / f"{candidate.person_id}_pending.pdf"
            if out_path.exists():
                out_path.unlink()
            final_path.write_bytes(pdf_bytes)

            return CrawlResult(
                candidate_id=candidate.person_id,
                name=candidate.name,
                status="ok",
                detail_url=candidate.detail_url,
                pdf_path=str(final_path),
                source_url=pdf_url,
                bytes=len(pdf_bytes),
            )
        except Exception as e:
            log.exception("[%s] unhandled error", candidate.person_id)
            return CrawlResult(
                candidate_id=candidate.person_id,
                name=candidate.name,
                status="error",
                detail_url=candidate.detail_url,
                error=str(e),
            )
        finally:
            await asyncio.sleep(random.uniform(1.0, 2.0))
            await page.close()
