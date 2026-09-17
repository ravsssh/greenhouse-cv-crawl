"""Resume attachment resolution for Greenhouse candidate pages.

Four strategies to find the resume attachment ID, plus helpers for
parsing Greenhouse's preview viewer URLs and scoring captured
attachment metadata from API responses.
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page

from greenhouse_api import ATTACHMENT_PREVIEW_RE

log = logging.getLogger(__name__)


def unwrap_preview_source(source: str) -> str:
    """Extract the signed document URL from Greenhouse's PDF viewer URL."""
    if not source:
        return ""
    parsed = urlparse(source)
    document_urls = parse_qs(parsed.query).get("document_url", [])
    return document_urls[0] if document_urls else source


async def preview_from_resume_button(page: Page) -> tuple[str, str]:
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
            unwrap_preview_source(payload.get("source", "")),
        )
    except Exception as exc:
        log.debug("Could not capture resume-button preview: %s", exc)
        return "", ""


async def find_resume_attachment_id(page: Page) -> Optional[str]:
    """Find the resume attachment's ID on a candidate detail page.

    Tries multiple strategies in order of specificity:
      1. <a> with text "Resume" whose href contains /attachments/
      2. Any element with data-attachment-id attribute
    """
    try:
        result = await page.evaluate(r"""() => {
            const anchors = Array.from(document.querySelectorAll('a[href*="/attachments/"]'));
            if (anchors.length === 0) return null;

            const scoreResume = (a) => {
                const t = (a.innerText || '').toLowerCase();
                if (t.includes('resume')) return 10;
                if (t.includes('cv ')) return 9;
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

            const href = best.getAttribute('href') || '';
            const m = href.match(/\/attachments\/(\d+)/);
            if (m) return m[1];

            return best.getAttribute('data-attachment-id');
        }""")
        if result:
            return str(result)
    except Exception as e:
        log.debug("Attachment link strategy failed: %s", e)

    try:
        result = await page.evaluate(r"""() => {
            const els = document.querySelectorAll('[data-attachment-id]');
            if (els.length === 0) return null;
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
        log.debug("data-attachment-id strategy failed: %s", e)

    return None


def collect_attachments(node, out: list, source_url: str) -> None:
    """Walk a JSON tree and collect any objects that look like attachments."""
    if isinstance(node, dict):
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
            collect_attachments(v, out, source_url)
    elif isinstance(node, list):
        for v in node:
            collect_attachments(v, out, source_url)


def pick_resume_id_from_captures(captured: list[dict]) -> Optional[str]:
    """Pick the attachment ID most likely to be the resume.

    Scoring: type == "resume" scores highest, then .pdf filename,
    then .docx/.doc, then any attachment.
    """
    if not captured:
        return None

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
        return s

    best = max(captured, key=score)
    aid = best.get("id")
    return str(aid) if aid else None
