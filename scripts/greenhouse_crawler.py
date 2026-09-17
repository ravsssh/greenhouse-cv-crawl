"""Orchestrator for the Greenhouse CV crawl pipeline.

Composes the focused modules (auth, discovery, attachment, download,
bulk) behind a single run() entry point that crawl.py calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from auth import handle_greenhouse_otp, is_logged_in, login_with_google
from bulk import request_bulk_resume_exports
from discovery import collect_accessible_jobs, collect_candidate_list
from download import fetch_one_candidate
from greenhouse_api import LOGIN_URL
from models import CandidateRef, CrawlResult, JobRef

log = logging.getLogger(__name__)


async def run(
    *,
    job_id: str,
    list_url: str,
    out_root: Path,
    headless: bool,
    user_data_dir: Path,
    email: str,
    password: str,
    concurrency: int = 4,
    limit: Optional[int] = None,
    bulk_export: bool = False,
    bulk_batch_size: int = 30,
    list_jobs_only: bool = False,
) -> list[CrawlResult] | list[JobRef]:
    if not list_jobs_only:
        out_root.mkdir(parents=True, exist_ok=True)
    raw_dir = out_root / "raw"
    if not list_jobs_only:
        raw_dir.mkdir(parents=True, exist_ok=True)

    checkpoint_path = out_root / ".checkpoint.json"
    failures_log = out_root / ".failures.log"
    checkpoint = {"last_list_page": 0, "completed_ids": []}
    if checkpoint_path.exists():
        try:
            checkpoint = json.loads(checkpoint_path.read_text())
        except Exception:
            log.warning("Could not read checkpoint — starting fresh")

    async with async_playwright() as pw:
        launch_kwargs = dict(
            user_data_dir=str(user_data_dir),
            headless=headless,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            slow_mo=50 if not headless else 0,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--lang=en-US",
            ],
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        context = await pw.chromium.launch_persistent_context(**launch_kwargs)
        log.info("Launched isolated Playwright Chromium (user_data_dir=%s)", user_data_dir)

        try:
            stealth = Stealth(navigator_platform_override="MacIntel")
            await stealth.apply_stealth_async(context)
            log.info("Applied playwright-stealth to context")
        except Exception as e:
            log.warning("Could not apply stealth: %s", e)

        try:
            await login_with_google(context, email, password)
        except Exception as e:
            log.warning("login_with_google raised %s — will try the list anyway", e)

        list_page = await context.new_page()
        try:
            await list_page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            if "/otp_auth" in list_page.url:
                log.info("Greenhouse OTP screen detected — waiting for code")
                if not await handle_greenhouse_otp(list_page):
                    log.error("OTP never completed — aborting")
                    await context.close()
                    return []
                await list_page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            if not is_logged_in(list_page):
                log.error("Session is not authenticated — landed on %s", list_page.url)
                log.error("Please run interactively and complete the Google login.")
                await context.close()
                return []
            log.info("Session verified — on %s", list_page.url)
        except Exception as e:
            log.error("Could not verify session: %s", e)
            await context.close()
            return []

        if list_jobs_only:
            jobs = await collect_accessible_jobs(list_page)
            log.info("Found %d accessible jobs", len(jobs))
            await context.close()
            return jobs

        candidates = await collect_candidate_list(
            list_page, list_url, max_candidates=limit
        )
        log.info("Found %d candidates on the list", len(candidates))

        if limit:
            candidates = candidates[:limit]

        if bulk_export:
            results = await request_bulk_resume_exports(
                list_page, candidates, batch_size=bulk_batch_size,
            )
            await context.close()
            return results

        completed = set(checkpoint.get("completed_ids", []))
        todo = [c for c in candidates if c.person_id not in completed]
        log.info("%d to download (skipping %d already done)", len(todo), len(completed))

        sem = asyncio.Semaphore(concurrency)
        results: list[CrawlResult] = []
        for done in asyncio.as_completed(
            [fetch_one_candidate(context, c, raw_dir, sem) for c in todo]
        ):
            res = await done
            results.append(res)
            log.info("[%d/%d] %-15s %s", len(results), len(todo),
                     res.status, res.name or res.candidate_id)
            if res.status == "ok":
                completed.add(res.candidate_id)
                checkpoint["completed_ids"] = sorted(completed)
                checkpoint_path.write_text(json.dumps(checkpoint, indent=2))
            else:
                with failures_log.open("a", encoding="utf-8") as f:
                    f.write(
                        f"{datetime.now(timezone.utc).isoformat()} "
                        f"{res.candidate_id} {res.status}: {res.error}\n"
                    )
            await asyncio.sleep(random.uniform(0.3, 0.8))

        await context.close()

    return results


def write_metadata(results: list[CrawlResult], out_path: Path, job_id: str) -> None:
    """Write the per-candidate metadata.json (authoritative index)."""
    payload = {
        "job_id": job_id,
        "merged_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            status: sum(1 for r in results if r.status == status)
            for status in sorted({r.status for r in results})
        },
        "candidates": [
            {
                **r.to_json(),
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            }
            for r in sorted(results, key=lambda r: int(r.candidate_id))
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
