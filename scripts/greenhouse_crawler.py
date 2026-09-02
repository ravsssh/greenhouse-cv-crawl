"""Async Playwright crawler for Greenhouse candidate resumes.

Designed for the 600-candidate job pipeline at hiring_plan_id=3209839,
but works for any Greenhouse job given a --job-id and the candidate
list URL.

Flow:
  1. Launch a persistent Chromium context using the user's real Chrome
     (`channel="chrome"`) so passkeys, cookies and saved passwords all
     carry over. Apply playwright-stealth so Google's bot detection
     doesn't kick in.
  2. Log in via Google SSO. Pause via Playwright's `page.pause()` if
     2FA / passkey / "is this you" appears so the user can complete it
     in the browser, then press the Resume button.
  3. Paginate the candidate list page-by-page, collecting every
     /people/{pid}/applications/{aid} link.
  4. For each candidate, open their detail page, find the resume
     attachment, call /attachment_previews/{id} to get the signed S3
     URL, then save the PDF to output/<job_id>/raw/<cid>_<slug>.pdf.
  5. Each candidate's progress is checkpointed to .checkpoint.json so a
     rerun picks up where it left off — critical at 600 candidates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sys
from urllib.parse import urlencode
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
    expect,
)
from playwright_stealth import Stealth

log = logging.getLogger(__name__)

# Greenhouse URLs we care about.
LOGIN_URL = "https://app.greenhouse.io/sdash"
ALL_JOBS_URL = "https://app.greenhouse.io/alljobs"

# Matches /people/<person_id>/applications/<app_id>(/redesign)?(...)
CANDIDATE_URL_RE = re.compile(
    r"/people/(?P<person>\d+)/applications/(?P<app>\d+)(?:/redesign)?"
)

# Matches /attachment_previews/<id>
ATTACHMENT_PREVIEW_RE = re.compile(r"/attachment_previews/(\d+)")


@dataclass
class CandidateRef:
    person_id: str
    application_id: str
    detail_url: str
    name: str = ""  # filled in on detail page


@dataclass
class JobRef:
    job_id: str
    name: str
    dashboard_url: str


@dataclass
class CrawlResult:
    candidate_id: str  # person_id (the stable Greenhouse identifier)
    name: str
    status: str  # "ok" | "no_resume" | "download_failed" | "error"
    detail_url: str = ""
    pdf_path: Optional[str] = None
    source_url: str = ""
    bytes: int = 0
    error: str = ""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "unnamed"


def _unwrap_preview_source(source: str) -> str:
    """Extract the signed document URL from Greenhouse's PDF viewer URL."""
    if not source:
        return ""
    parsed = urlparse(source)
    document_urls = parse_qs(parsed.query).get("document_url", [])
    return document_urls[0] if document_urls else source


async def _preview_from_resume_button(page: Page) -> tuple[str, str]:
    """Click View Resume and capture the exact preview request it creates."""
    resume_button = page.locator(
        '[data-provides="header-view-resume"], '
        'button:has-text("View Resume"), button:has-text("Resume")'
    ).first
    try:
        await resume_button.wait_for(state="visible", timeout=8_000)
        async with page.expect_response(
            lambda response: "/attachment_previews/" in response.url,
            timeout=15_000,
        ) as response_info:
            await resume_button.click()
        response = await response_info.value
        match = ATTACHMENT_PREVIEW_RE.search(response.url)
        if not response.ok:
            log.debug("Resume button preview returned %s", response.status)
            return (match.group(1) if match else "", "")
        payload = await response.json()
        return (
            match.group(1) if match else "",
            _unwrap_preview_source(payload.get("source", "")),
        )
    except Exception as exc:
        log.debug("Could not capture resume-button preview: %s", exc)
        return "", ""


async def _find_resume_attachment_id(page: Page) -> Optional[str]:
    """Find the resume attachment's ID on a candidate detail page.

    Greenhouse's DOM has changed across versions. We try multiple
    strategies, in order of specificity:

      1. <a> with text "Resume" whose href contains /attachments/
      2. <a> with .pdf filename and /attachments/ in href
      3. Any <a> with /attachments/ in href (fallback)
      4. Any element with data-attachment-id attribute
    """
    # Strategy 1: anchor with explicit "Resume" text + /attachments/ href
    try:
        result = await page.evaluate(r"""() => {
            const anchors = Array.from(document.querySelectorAll('a[href*="/attachments/"]'));
            if (anchors.length === 0) return null;

            // Prefer the anchor whose visible text or nearest label says "Resume"
            const scoreResume = (a) => {
                const t = (a.innerText || '').toLowerCase();
                if (t.includes('resume')) return 10;
                if (t.includes('cv ')) return 9;
                // Look at the parent / grandparent for the word "Resume"
                let p = a.parentElement;
                for (let i = 0; i < 4 && p; i++) {
                    const pt = (p.innerText || '').toLowerCase();
                    if (pt.includes('resume') && pt.length < 200) return 8 - i;
                    p = p.parentElement;
                }
                return 0;
            };

            let best = null;
            let bestScore = -1;
            for (const a of anchors) {
                const s = scoreResume(a);
                if (s > bestScore) {
                    bestScore = s;
                    best = a;
                }
            }
            if (!best) return null;

            // Extract the attachment ID from href like /attachments/123 or
            // /people/{pid}/attachments/{aid}
            const href = best.getAttribute('href') || '';
            const m = href.match(/\/attachments\/(\d+)/);
            if (m) return m[1];

            // Fallback: data-attachment-id attribute
            return best.getAttribute('data-attachment-id');
        }""")
        if result:
            return str(result)
    except Exception as e:
        log.debug("Strategy 1 failed: %s", e)

    # Strategy 4: look for any data-attachment-id on the page
    try:
        result = await page.evaluate(r"""() => {
            const els = document.querySelectorAll('[data-attachment-id]');
            if (els.length === 0) return null;
            // Pick the first one whose nearby text mentions resume
            for (const el of els) {
                const t = (el.innerText || '').toLowerCase();
                const pt = el.parentElement ? (el.parentElement.innerText || '').toLowerCase() : '';
                if (t.includes('resume') || pt.includes('resume')) {
                    return el.getAttribute('data-attachment-id');
                }
            }
            return els[0].getAttribute('data-attachment-id');
        }""")
        if result:
            return str(result)
    except Exception as e:
        log.debug("Strategy 4 failed: %s", e)

    return None


async def _capture_body(response, captured_attachments: list) -> None:
    """Read a response body and capture any attachment metadata."""
    try:
        body = await response.body()
        if not body:
            return
        try:
            import json as _json
            data = _json.loads(body)
        except Exception:
            return
        # Recursively search the JSON for attachment-shaped dicts.
        _collect_attachments(data, captured_attachments, response.url)
    except Exception:
        pass


def _collect_attachments(node, out: list, source_url: str) -> None:
    """Walk a JSON tree and collect any objects that look like attachments."""
    if isinstance(node, dict):
        # Heuristic: an attachment dict has an 'id' or 'attachment_id',
        # plus a 'type' or 'filename' or 'url'.
        keys = set(node.keys())
        looks_like_attachment = (
            ("id" in keys or "attachment_id" in keys or "attachmentId" in keys)
            and ("type" in keys or "filename" in keys or "url" in keys or "name" in keys)
        )
        if looks_like_attachment:
            out.append({
                "id": node.get("id") or node.get("attachment_id") or node.get("attachmentId"),
                "type": node.get("type"),
                "filename": node.get("filename") or node.get("name"),
                "url": node.get("url"),
                "source_url": source_url,
            })
        for v in node.values():
            _collect_attachments(v, out, source_url)
    elif isinstance(node, list):
        for v in node:
            _collect_attachments(v, out, source_url)


def _pick_resume_id_from_captures(captured: list[dict]) -> Optional[str]:
    """Pick the attachment ID most likely to be the resume.

    Strategy:
      1. Prefer one whose 'type' is 'resume'.
      2. Then filename ending in .pdf.
      3. Then filename ending in .docx/.doc.
      4. Last resort: any attachment.
    """
    if not captured:
        return None
    # Score each candidate
    def score(att):
        t = (att.get("type") or "").lower()
        fn = (att.get("filename") or "").lower()
        s = 0
        if t == "resume":
            s += 100
        elif "resume" in t:
            s += 50
        if fn.endswith(".pdf"):
            s += 20
        elif fn.endswith(".docx") or fn.endswith(".doc"):
            s += 10
        # Prefer the last one (most recent attachment)
        return s
    best = max(captured, key=score)
    aid = best.get("id")
    return str(aid) if aid else None


async def _handle_greenhouse_otp(page: Page, max_wait_seconds: int = 300) -> bool:
    """Wait for the user to complete Greenhouse's OTP challenge.

    Greenhouse puts up a 6-digit-code screen after Google SSO on a new
    device. The user types the code in their email. Once they land on
    the dashboard, we return True.

    Polls page.url every 2s; never touches the browser. Returns True if
    the dashboard is reached, False if max_wait_seconds elapsed.
    """
    print(
        "\n=== GREENHOUSE OTP ===\n"
        "  A 6-digit code was emailed to your AnyMind account.\n"
        "  Type it into the OTP screen in the Chromium window.\n"
        "  Tick 'Remember this device for 30 days' to skip this for 30 days.\n"
        "  Script will detect the dashboard and continue automatically.\n"
        f"  (will wait up to {max_wait_seconds}s)\n"
        "=======================\n",
        file=sys.stderr,
        flush=True,
    )
    import time
    start = time.time()
    while time.time() - start < max_wait_seconds:
        await asyncio.sleep(2)
        try:
            current_url = page.url
        except Exception:
            return False
        if "app.greenhouse.io" in current_url and "/otp_auth" not in current_url and "/login" not in current_url.lower():
            log.info("OTP complete — on dashboard")
            return True
    log.warning("OTP wait exceeded %ds", max_wait_seconds)
    return False


async def login_with_google(context: BrowserContext, email: str, password: str) -> Page:
    """Open Greenhouse login and walk through Google SSO.

    Strategy:
      1. Auto-fill email + Next.
      2. Auto-fill password + Next if a password field appears.
      3. For any further step (passkey / 2FA / consent / "is this you"),
         the script does NOT touch the browser. It prints a message and
         polls the URL until we land on app.greenhouse.io. You complete
         the step in the open Chromium window (or on your phone for
         passkey / 2FA). The script stays passive throughout — no
         Inspector, no input(), nothing that Google's anti-automation
         could detect.

    After login completes we **open a fresh page** for the candidate
    list. Reusing the OAuth tab caused `TargetClosedError` on the
    follow-up `page.goto()` because Greenhouse occasionally closes
    the original tab after the OAuth redirect.
    """
    page = await context.new_page()
    log.info("Opening Greenhouse login: %s", LOGIN_URL)
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")

    # Already authenticated?
    if _is_logged_in(page):
        log.info("Already authenticated — redirect target: %s", page.url)
        return page

    # Click "Sign in with Google" if visible.
    google_btn = page.locator(
        'a:has-text("Google"), button:has-text("Google"), '
        '[data-provider="google"], a[href*="google"]'
    ).first
    try:
        await google_btn.wait_for(state="visible", timeout=10_000)
        await google_btn.click()
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:
        log.warning("Google SSO button not found — assuming different login flow.")

    if _is_logged_in(page):
        log.info("Authenticated after Google click — %s", page.url)
        return page

    # Auto-fill email.
    try:
        email_input = page.locator('input[type="email"]').first
        await email_input.wait_for(state="visible", timeout=10_000)
        await email_input.fill(email)
        await page.locator('button:has-text("Next"), #identifierNext').first.click()
        log.info("Submitted email, watching for password or passkey step...")
    except Exception:
        log.info("No email field — opening manual pause")

    # Try to auto-fill the password. If the password field doesn't
    # appear within 8 seconds, Google is asking for a passkey / 2FA /
    # account choice. Pause for the user to handle it.
    password_handled = False
    try:
        pwd_input = page.locator('input[type="password"]').first
        await pwd_input.wait_for(state="visible", timeout=8_000)
        await pwd_input.fill(password)
        await page.locator('button:has-text("Next"), #passwordNext').first.click()
        log.info("Submitted password")
        password_handled = True
    except Exception:
        log.info("Password field not shown — Google wants passkey / 2FA / consent")

    # If password didn't work, OR if there's any further step
    # (2FA, "is this you", OAuth consent, etc.), wait for the user to
    # complete it in the browser. We poll the URL and bail as soon as
    # we land on the Greenhouse dashboard — no Inspector, no input().
    # Loop a few times because Google's flow can have multiple steps
    # (e.g. password → 2FA → consent → done).
    for attempt in range(5):
        if _is_logged_in(page):
            break
        log.info(
            "[attempt %d] Waiting for manual step. URL: %s",
            attempt + 1, page.url,
        )
        await _manual_pause(page, max_wait_seconds=180)
        # After the wait (or successful login), give the page time to settle.
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass

    if _is_logged_in(page):
        log.info("Login complete. Landed on %s", page.url)
        return page
    else:
        log.error("Login never completed — last URL: %s", page.url)
        return page  # return whatever we have; the caller will fail gracefully


def _is_logged_in(page: Page) -> bool:
    """True if the current page is on the Greenhouse dashboard."""
    return (
        "app.greenhouse.io" in page.url
        and "/login" not in page.url.lower()
        and "/signin" not in page.url.lower()
    )


async def _manual_pause(page: Page, max_wait_seconds: int = 300) -> None:
    """Wait while the user completes 2FA / passkey in the browser.

    Critically, this does NOT use `page.pause()`. The Inspector is for
    debugging test code — opening it during a real login flow interferes
    with the OAuth JavaScript and can cause Google to abort the flow.

    Instead, we just print a message and poll the page URL every 2s
    for up to `max_wait_seconds`. The script stays passive — it
    doesn't touch the browser, just observes. As soon as the URL
    changes to the Greenhouse dashboard, control returns.
    """
    print(
        "\n=== MANUAL LOGIN STEP ===\n"
        f"  URL: {page.url}\n"
        "  Complete the next step in the browser window:\n"
        "    - 2FA code: type it in\n"
        "    - Passkey: scan the QR / approve on your phone\n"
        "    - 'Is this you': click Continue\n"
        "    - OAuth consent: click Allow\n"
        "  The script will detect when you reach the Greenhouse\n"
        "  dashboard and continue automatically. No action needed here.\n"
        f"  (will wait up to {max_wait_seconds}s before timing out)\n"
        "=========================\n",
        file=sys.stderr,
        flush=True,
    )

    import time
    start = time.time()
    while time.time() - start < max_wait_seconds:
        await asyncio.sleep(2)
        # Check if the page is still alive
        try:
            current_url = page.url
        except Exception:
            log.warning("Page closed during manual login — the browser may have crashed")
            break
        if "app.greenhouse.io" in current_url and "/login" not in current_url.lower():
            log.info("Detected Greenhouse dashboard — login complete")
            return
        # If user navigated away from Google entirely but hasn't reached
        # Greenhouse, they're still in the flow. Keep waiting.
    log.warning("Manual login wait exceeded %ds — continuing anyway", max_wait_seconds)


async def collect_candidate_list(
    page: Page,
    list_url: str,
    max_pages: int = 100,
    max_candidates: Optional[int] = None,
) -> list[CandidateRef]:
    """Walk the paginated candidate list and return every candidate ref.

    Uses DIRECT URL NAVIGATION (`?page=N`) rather than DOM clicks.
    Greenhouse's list page is server-rendered, so each `?page=N` URL
    produces a fresh HTML with that page's candidates. DOM-based
    pagination (clicking "next") was unreliable: in the previous
    version it only surfaced ~1 new candidate per click because the
    "next page" control was opening filtered sub-views instead of
    advancing the list.

    We loop ?page=1..max_pages, parsing each page's HTML for
    /people/{pid}/applications/{aid} links. Stop when a page yields
    zero matches (we've hit the end).
    """
    from urllib.parse import urlparse, parse_qs, urlunparse, urlencode

    log.info("Opening candidate list: %s", list_url)

    seen: dict[str, CandidateRef] = {}
    parsed = urlparse(list_url)
    base_qs = parse_qs(parsed.query)

    for page_num in range(1, max_pages + 1):
        # Set ?page=N in the URL
        base_qs["page"] = [str(page_num)]
        new_qs = urlencode({k: v[0] if isinstance(v, list) else v for k, v in base_qs.items()})
        page_url = urlunparse(parsed._replace(query=new_qs))

        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            log.error("Could not open page %d: %s", page_num, e)
            break

        # Give the page time to render
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

        # Stop if this page had no matches — we've gone past the end.
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
        match = re.search(r"/sdash/(\d+)", link.get("href", ""))
        if not match:
            continue
        job_id = match.group(1)
        name = re.sub(r"\s+", " ", link.get("text", "")).strip()
        existing = jobs.get(job_id)
        # A page can link to the same dashboard more than once. Prefer the
        # most descriptive visible label.
        if existing is None or len(name) > len(existing.name):
            jobs[job_id] = JobRef(
                job_id=job_id,
                name=name or f"Job {job_id}",
                dashboard_url=f"https://app.greenhouse.io/sdash/{job_id}",
            )
    return sorted(jobs.values(), key=lambda job: (job.name.lower(), int(job.job_id)))


async def request_bulk_resume_exports(
    page: Page,
    candidates: list[CandidateRef],
    batch_size: int = 30,
) -> list[CrawlResult]:
    """Ask Greenhouse to email its native merged-resume exports.

    Greenhouse's UI declares a hard maximum of 30 applications per request.
    Each successful batch becomes a separate email attachment. Applications
    without resumes are skipped by Greenhouse itself.
    """
    if not 1 <= batch_size <= 30:
        raise ValueError("bulk batch size must be between 1 and 30")

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
            "https://app.greenhouse.io/people/bulk/print_resumes",
            data=body,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-CSRF-Token": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
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
            # Do not generate more external email jobs when the endpoint's
            # behavior differs from the captured, verified response.
            break
        await asyncio.sleep(random.uniform(1.0, 2.0))

    return results


async def fetch_one_candidate(
    context: BrowserContext,
    candidate: CandidateRef,
    raw_dir: Path,
    sem: asyncio.Semaphore,
    checkpoint: dict,
) -> CrawlResult:
    """Download the resume for a single candidate. Resumable.

    Strategy:
      1. Open the candidate detail page.
      2. Capture all API responses via page.on("response") — Greenhouse
         calls its internal API to load candidate data, including
         attachment URLs. We grab any response that contains attachment
         metadata.
      3. As a fallback, parse the DOM for /attachments/ links.
      4. Use the captured attachment ID to call /attachment_previews/
         and download the signed S3 PDF.
    """
    async with sem:
        out_path = raw_dir / f"{candidate.person_id}_pending.pdf"
        # Skip if already downloaded.
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
            """Capture API responses that mention attachments."""
            try:
                url = response.url
                ct = response.headers.get("content-type", "")
                # We want JSON responses from the API (not the S3 PDF)
                if "json" not in ct.lower():
                    return
                # Filter to API-like URLs
                if "app.greenhouse.io" not in url:
                    return
                # Common patterns: /people/{id}, /applications/{id}/...
                if not any(seg in url for seg in ["/people/", "/applications/", "/attachments/"]):
                    return
                body = await response.body()
                if not body:
                    return
                import json as _json
                try:
                    data = _json.loads(body)
                except Exception:
                    return
                _collect_attachments(data, captured_attachments, url)
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
            # Give Greenhouse's API a moment to fire
            await asyncio.sleep(random.uniform(2.5, 4.0))

            # Wait for the candidate name to render (helps confirm page loaded)
            try:
                await page.locator('h1').first.wait_for(state="visible", timeout=10_000)
            except Exception:
                pass

            # Extract the candidate's display name from the page heading.
            try:
                candidate.name = (await page.locator(
                    'h1, [data-test="candidate-name"]'
                ).first.inner_text()).strip()
            except Exception:
                pass

            # Prefer the request produced by Greenhouse's own View Resume
            # button instead of guessing an ID from unrelated page data.
            attachment_id, pdf_url = await _preview_from_resume_button(page)
            log.debug(
                "[%s] resume button attachment_id=%s source=%s",
                candidate.person_id, attachment_id, bool(pdf_url),
            )

            # Compatibility fallback for older candidate-page variants.
            if not attachment_id:
                attachment_id = await _find_resume_attachment_id(page)
                log.debug("[%s] DOM attachment_id: %s", candidate.person_id, attachment_id)

            # Fall back to captured API responses.
            if attachment_id is None and captured_attachments:
                attachment_id = _pick_resume_id_from_captures(captured_attachments)
                log.debug("[%s] captured attachment_id: %s", candidate.person_id, attachment_id)

            if attachment_id is None:
                return CrawlResult(
                    candidate_id=candidate.person_id,
                    name=candidate.name,
                    status="no_resume",
                    detail_url=candidate.detail_url,
                    error="no resume found via DOM or API capture",
                )

            # Call the attachment_previews endpoint to get the signed S3 URL.
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
                pdf_url = _unwrap_preview_source(preview.get("source", ""))
            if not pdf_url:
                return CrawlResult(
                    candidate_id=candidate.person_id,
                    name=candidate.name,
                    status="download_failed",
                    detail_url=candidate.detail_url,
                    error="attachment_previews returned no source URL",
                )

            # Download the S3 PDF using the browser's authenticated
            # session (the cookies travel with the request).
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

            # Rename to a stable, descriptive filename.
            final_name = f"{candidate.person_id}_{slugify(candidate.name or f'cid-{candidate.person_id}')}.pdf"
            final_path = raw_dir / final_name
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
            # The semaphore remains held during this delay, limiting the
            # aggregate request rate as well as the number of open tabs.
            await asyncio.sleep(random.uniform(1.0, 2.0))
            await page.close()


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
    use_system_chrome: bool = True,
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

        # IMPORTANT: We deliberately use Playwright's bundled Chromium,
        # not `channel="chrome"`. On macOS, `channel="chrome"` will try
        # to join any running Chrome instance — so if your personal
        # Chrome is open, actions get routed there instead of the
        # script's isolated profile. Bundled Chromium is a completely
        # separate process and never interferes with running Chrome.
        #
        # Downside: bundled Chromium can't access passkey bindings in
        # macOS Keychain. We handle this via `page.pause()` — the user
        # approves the passkey in their *real* Chrome and presses Resume
        # in the Playwright Inspector.
        if use_system_chrome:
            log.warning(
                "USE_SYSTEM_CHROME=true — but launching system Chrome "
                "would conflict with your already-running Chrome instance. "
                "Ignoring and using Playwright Chromium."
            )
        context = await pw.chromium.launch_persistent_context(**launch_kwargs)
        log.info("Launched isolated Playwright Chromium (user_data_dir=%s)", user_data_dir)

        # Apply playwright-stealth to mask automation fingerprint
        # (navigator.webdriver=true, missing plugins/languages, etc.)
        # Only works against the context's pages — needs to run after launch.
        try:
            stealth = Stealth(navigator_platform_override="MacIntel")
            await stealth.apply_stealth_async(context)
            log.info("Applied playwright-stealth to context")
        except Exception as e:
            log.warning("Could not apply stealth: %s", e)

        # Login (the returned page may have closed during OAuth — that's
        # fine, we'll open a fresh one below).
        try:
            await login_with_google(context, email, password)
        except Exception as e:
            log.warning("login_with_google raised %s — will try the list anyway", e)

        # Verify the session is valid by trying to open the dashboard.
        # If we get redirected back to login, the session is broken.
        list_page = await context.new_page()
        try:
            await list_page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            # Greenhouse sometimes puts up an OTP screen after Google SSO
            # (6-digit code emailed to you). Handle it before walking away.
            if "/otp_auth" in list_page.url:
                log.info("Greenhouse OTP screen detected — waiting for code")
                if not await _handle_greenhouse_otp(list_page):
                    log.error("OTP never completed — aborting")
                    await context.close()
                    return []
                # After OTP, re-navigate to dashboard.
                await list_page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            if not _is_logged_in(list_page):
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
                list_page,
                candidates,
                batch_size=bulk_batch_size,
            )
            await context.close()
            return results

        # Filter out already-done ones.
        completed = set(checkpoint.get("completed_ids", []))
        todo = [c for c in candidates if c.person_id not in completed]
        log.info("%d to download (skipping %d already done)", len(todo), len(completed) - 0)

        sem = asyncio.Semaphore(concurrency)
        results: list[CrawlResult] = []
        for done in asyncio.as_completed(
            [fetch_one_candidate(context, c, raw_dir, sem, checkpoint) for c in todo]
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
                "candidate_id": r.candidate_id,
                "name": r.name,
                "status": r.status,
                "detail_url": r.detail_url,
                "source_url": r.source_url,
                "pdf_file": r.pdf_path,
                "bytes": r.bytes,
                "error": r.error,
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            }
            for r in sorted(results, key=lambda r: int(r.candidate_id))
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
