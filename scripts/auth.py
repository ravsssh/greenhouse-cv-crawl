"""Google SSO login for Greenhouse via Playwright."""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from playwright.async_api import BrowserContext, Page

from greenhouse_api import LOGIN_URL

log = logging.getLogger(__name__)


def is_logged_in(page: Page) -> bool:
    return (
        "app.greenhouse.io" in page.url
        and "/login" not in page.url.lower()
        and "/signin" not in page.url.lower()
    )


async def handle_greenhouse_otp(page: Page, max_wait_seconds: int = 300) -> bool:
    """Wait for the user to complete Greenhouse's OTP challenge."""
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


async def _manual_pause(page: Page, max_wait_seconds: int = 300) -> None:
    """Wait while the user completes 2FA / passkey in the browser.

    Polls the page URL every 2s — does NOT use page.pause().
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
    start = time.time()
    while time.time() - start < max_wait_seconds:
        await asyncio.sleep(2)
        try:
            current_url = page.url
        except Exception:
            log.warning("Page closed during manual login — the browser may have crashed")
            break
        if "app.greenhouse.io" in current_url and "/login" not in current_url.lower():
            log.info("Detected Greenhouse dashboard — login complete")
            return
    log.warning("Manual login wait exceeded %ds — continuing anyway", max_wait_seconds)


async def login_with_google(context: BrowserContext, email: str, password: str) -> Page:
    """Open Greenhouse login and walk through Google SSO.

    Auto-fills email and password when possible. For further steps
    (passkey / 2FA / consent), polls the URL passively until the user
    completes them in the browser.
    """
    page = await context.new_page()
    log.info("Opening Greenhouse login: %s", LOGIN_URL)
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")

    if is_logged_in(page):
        log.info("Already authenticated — redirect target: %s", page.url)
        return page

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

    if is_logged_in(page):
        log.info("Authenticated after Google click — %s", page.url)
        return page

    try:
        email_input = page.locator('input[type="email"]').first
        await email_input.wait_for(state="visible", timeout=10_000)
        await email_input.fill(email)
        await page.locator('button:has-text("Next"), #identifierNext').first.click()
        log.info("Submitted email, watching for password or passkey step...")
    except Exception:
        log.info("No email field — opening manual pause")

    try:
        pwd_input = page.locator('input[type="password"]').first
        await pwd_input.wait_for(state="visible", timeout=8_000)
        await pwd_input.fill(password)
        await page.locator('button:has-text("Next"), #passwordNext').first.click()
        log.info("Submitted password")
    except Exception:
        log.info("Password field not shown — Google wants passkey / 2FA / consent")

    for attempt in range(5):
        if is_logged_in(page):
            break
        log.info("[attempt %d] Waiting for manual step. URL: %s", attempt + 1, page.url)
        await _manual_pause(page, max_wait_seconds=180)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass

    if is_logged_in(page):
        log.info("Login complete. Landed on %s", page.url)
    else:
        log.error("Login never completed — last URL: %s", page.url)
    return page
