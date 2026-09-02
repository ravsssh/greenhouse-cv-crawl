# Greenhouse Resume Exporter — Chrome Extension

Private Manifest V3 extension for HR users to select an accessible Greenhouse
vacancy and request the native emailed resume export in supported batches of 30.

Panduan sederhana untuk pengguna HR tersedia di
[`PANDUAN_INSTALASI_HR.md`](PANDUAN_INSTALASI_HR.md).

## Current MVP flow

1. Uses the HR user's existing `app.greenhouse.io` browser session.
2. Reads `/alljobs` and lists only jobs visible to that account.
3. Counts applications for the selected job across candidate-list pages.
4. Shows the candidate and email-batch counts.
5. Requires explicit confirmation.
6. Submits `/people/bulk/print_resumes` requests in batches of 30.

No Google password, Greenhouse password, session cookie, or resume is stored by
the extension. Greenhouse emails the generated PDFs to the signed-in user.

## Load locally for development

1. Open `chrome://extensions` in Chrome.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select this `chrome-extension` directory.
5. Sign in to `https://app.greenhouse.io` in the same Chrome profile.
6. Pin and open **Greenhouse Resume Exporter**.

Use a small test job before submitting a full export. Every successful batch
creates a real email.

## Security boundaries

- Host permission is restricted to `https://app.greenhouse.io/*`.
- The extension does not request the Chrome cookies permission. Requests run
  in the active Greenhouse tab's `MAIN` JavaScript world and use its existing
  same-origin session.
- The service worker accepts fixed operation names, not arbitrary URLs.
- The supported Greenhouse maximum of 30 is hard-coded.
- Candidate data is kept in memory only while the popup is open.

## Before organization deployment

- Add organization-owned icons and extension publisher metadata.
- Test with Site Admin and restricted Job Admin permission levels.
- Add a durable progress record so reopening the popup shows the last run.
- Add duplicate-run protection and an export audit record.
- Add automated tests against saved, redacted Greenhouse HTML fixtures.
- Review permissions and data handling with Security/Privacy.
- Publish as a domain-restricted private Chrome Web Store extension.

Greenhouse's bulk endpoint is an internal web endpoint rather than a documented
public API, so DOM and request compatibility need regression monitoring.
