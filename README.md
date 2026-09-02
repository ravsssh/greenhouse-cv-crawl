# Greenhouse CV Crawl

Discover jobs accessible to an HR user's Greenhouse account, then export
candidate resumes for one explicitly selected vacancy.

This is **not** the Harvest API path used by the sibling `recruitment-automation` project — it uses Playwright + your Google SSO session because the API key isn't available.

## What it does

1. Logs into `app.greenhouse.io` through Google SSO.
2. Walks the paginated candidate list for the job.
3. For every candidate, opens their detail page, finds the resume attachment, fetches the signed S3 PDF via `/attachment_previews/{id}`, saves it locally.
4. Writes:
   - `output/<job_id>/raw/<person_id>_<slug>.pdf` — one per candidate.
   - `output/<job_id>/metadata.json` — per-candidate record (name, status, source URL, etc.).
   - `output/<job_id>/all_cvs.pdf` — all CVs concatenated, each preceded by a one-page cover sheet.
   - `output/<job_id>/.checkpoint.json` — pagination state so a re-run resumes instead of restarting.
   - `output/<job_id>/.failures.log` — anything that errored.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cp .env.example .env
# edit .env — at minimum GOOGLE_EMAIL and GOOGLE_PASSWORD
```

`pypdf` is already on your system; the others install cleanly via the requirements file.

## First run (headed — required once)

Google will likely prompt for passkey, 2FA, or "is this you" the first time. When that happens, the script opens Playwright's **Inspector UI** over the browser — complete the step in the browser window, then press the **▶ Resume** button in the Inspector.

```bash
.venv/bin/python scripts/crawl.py --list-jobs
```

After login, the command prints the vacancies accessible to that account.
Choose one job ID explicitly:

```bash
.venv/bin/python scripts/crawl.py --job-id 3209839 --bulk-export --limit 3
```

This downloads 3 candidates as a smoke test. Verify:

- The browser window logged in successfully.
- `output/3209839/raw/` has 3 PDFs.
- `output/3209839/all_cvs.pdf` opens and contains 3 cover sheets + 3 CVs.
- `output/3209839/metadata.json` has 3 rows.

If the smoke test passes:

```bash
.venv/bin/python scripts/crawl.py --job-id 3209839
```

This runs the full pull — about 600 candidates at ~4 parallel, ~2–4 hours with the safety delays.

## How login actually works

The script launches Playwright's **bundled Chromium** into an isolated persistent user-data directory (`./chrome-profile/`). It then:

1. Auto-fills the email and clicks Next.
2. Auto-fills the password (if shown) and clicks Next.
3. If anything else appears — passkey prompt, 2FA code, "is this you", OAuth consent — **the script does NOT touch the browser**. It just prints a message in the terminal and waits up to 3 minutes, polling the URL every 2 seconds. You complete the step **in the Chromium window that's open** (or in your iPhone for passkey / 2FA), and the script automatically detects when you land on the Greenhouse dashboard and continues.

The script stays passive during 2FA — no Inspector, no input(), nothing that interferes with Google's OAuth JavaScript. This is critical: Playwright's Inspector (`page.pause()`) and Google's bot detection sometimes conflict and cause the OAuth tab to close mid-flow.

### Why not just use real Chrome?

We deliberately do **not** use `channel="chrome"`. On macOS, `channel="chrome"` will try to join any running Chrome instance — so if your personal Chrome is open, actions get routed through your personal profile instead of the script's isolated one. Bundled Chromium is a completely separate process and never interferes with your normal browsing.

### Trade-off

Bundled Chromium can't access passkey bindings in macOS Keychain. We work around this by making the script patient: it waits passively while you handle the passkey on your phone. The browser window stays open throughout.

### Why this approach over the Inspector

`page.pause()` is designed for **debugging your test code**, not for **completing a real login flow**. When you click/type in the actual browser while the Inspector is open, the Inspector treats it as a recorded test action — and on top of that, Google's anti-automation can detect the Inspector's hooks and abort the OAuth flow, closing the tab. Polling the URL is purely passive and doesn't trigger anything that could be detected.

## Headless mode

After the first interactive login, the persistent Chrome profile under `./chrome-profile/` holds the cookies. Subsequent runs can be headless:

```bash
HEADLESS=true .venv/bin/python scripts/crawl.py
# or
.venv/bin/python scripts/crawl.py --headless
```

## Resumable

The script checkpoints every successful download. Killing it and re-running resumes — anything already in `output/<job_id>/raw/` is skipped.

## Flags

| Flag | Default | What it does |
|---|---|---|
| `--list-jobs` | off | List vacancies accessible to the signed-in account and exit |
| `--job-id` | required | Greenhouse hiring plan / job ID selected from `--list-jobs` |
| `--url` | built from `--job-id` | Override the candidate list URL |
| `--out` | `./output` | Output root |
| `--limit N` | none | Stop after N candidates (smoke test) |
| `--concurrency N` | `1` | Parallel candidate tabs; higher values may trigger CloudFront blocking |
| `--headless` | off | Run headless (after first interactive login) |
| `--no-cover-pages` | off | Concatenate CVs with no separator pages |
| `--skip-merge` | off | Download only, don't build the merged PDF |
| `--bulk-export` | off | Use Greenhouse's native export; emails one merged PDF per batch |
| `--bulk-batch-size N` | `30` | Applications per bulk-export email (maximum 30) |
| `-v` | off | Debug logging |

## Native Greenhouse bulk export

Greenhouse can merge resumes server-side and email the result. This avoids
opening every candidate detail page and is substantially faster, but the UI
limits each export to 30 applications. A full 650-candidate run therefore
creates about 22 emails, and applications without resumes are skipped.

Smoke-test the flow with one small export first (this sends one real email):

```bash
.venv/bin/python scripts/crawl.py --job-id 3209839 --bulk-export --limit 3
```

Submit the complete job in 30-application batches:

```bash
.venv/bin/python scripts/crawl.py --job-id 3209839 --bulk-export
```

This mode does not create `all_cvs.pdf` locally; Greenhouse sends the merged
PDF attachments to the email address associated with the signed-in account.

## Troubleshooting

**Browser closes when I try to type 2FA code or scan passkey.**
This used to happen because the Playwright Inspector (`page.pause()`) interferes with Google's OAuth JavaScript. The current version doesn't use the Inspector at all — it just polls the URL while you complete 2FA in the browser. If you still see this, the script may have crashed; check `output/.../crawl.log` and rerun.

**Login hangs at "Sign in with Google".**
Your org may use a custom SSO page that wraps Google. Complete the login manually in the Chromium window — the script will detect when you reach the dashboard.

**`attachment_previews` returns 401.**
Session expired. Re-run with the headed browser, log in again.

**PDFs are 0 bytes or not starting with `%PDF`.**
The S3 signature expired before the download fired. This usually means too many parallel requests. Lower `--concurrency` to 2.

**`--limit 3` works but full pull fails halfway.**
Look at `.failures.log`. Most common cause is rate-limiting; rerun and the resumable checkpoint will pick up where it stopped.

## Data handling

Downloaded resumes are candidate PII. `output/`, `.env`, and `chrome-profile/` are gitignored. Clear `output/` once you've finished with a batch. Pulling candidate data into local tooling is worth clearing with whoever owns data protection at AnyMind (APPI / GDPR).
