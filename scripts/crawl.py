#!/usr/bin/env python
"""Bulk-export every candidate CV from a Greenhouse hiring plan.

Usage:
    python scripts/crawl.py --list-jobs
    python scripts/crawl.py --job-id 3209839 --bulk-export
    python scripts/crawl.py --job-id 3209839 --limit 5

The first run must be headed so you can complete Google's 2FA / "is this
you" prompts once. Subsequent runs reuse the persistent Chrome profile
and can run headless via --headless or HEADLESS=true in .env.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Local imports — running as `python scripts/crawl.py` means scripts/
# is on sys.path automatically. For `python -m scripts.crawl` add parent.
sys.path.insert(0, str(Path(__file__).parent))

from greenhouse_crawler import JobRef, run as run_crawler, write_metadata  # noqa: E402
from pdf_merger import merge as merge_pdfs  # noqa: E402


def candidate_list_url(job_id: str) -> str:
    return (
        f"https://app.greenhouse.io/plans/{job_id}/candidates"
        f"?hiring_plan_id={job_id}&job_status=open&sort=last_activity+desc"
        "&stage_status_id=2&type=all"
    )


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--job-id", default=None,
                   help="Greenhouse job ID; use --list-jobs to discover accessible IDs")
    p.add_argument("--list-jobs", action="store_true",
                   help="List jobs accessible to the signed-in account, then exit")
    p.add_argument("--url", default=None,
                   help="Override the candidate-list URL (default: standard pipeline URL)")
    p.add_argument("--out", default="output",
                   help="Output root directory (default: ./output)")
    p.add_argument("--limit", type=int, default=None,
                   help="Stop after N candidates (smoke test)")
    p.add_argument("--concurrency", type=int, default=None,
                   help="Parallel candidate downloads (default: $CONCURRENCY or 1)")
    p.add_argument("--headless", action="store_true",
                   help="Run headless (default: headed, so 2FA works on first run)")
    p.add_argument("--no-cover-pages", action="store_true",
                   help="Concatenate CVs without per-candidate cover sheets")
    p.add_argument("--skip-merge", action="store_true",
                   help="Download only; don't build the merged PDF")
    p.add_argument("--bulk-export", action="store_true",
                   help="Ask Greenhouse to email merged PDFs in batches (max 30 per email)")
    p.add_argument("--bulk-batch-size", type=int, default=30,
                   help="Applications per Greenhouse bulk-export email (default/max: 30)")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    load_dotenv()

    if not 1 <= args.bulk_batch_size <= 30:
        print(
            "ERROR: --bulk-batch-size must be between 1 and 30",
            file=sys.stderr,
        )
        return 2
    if not args.list_jobs and not args.job_id:
        print("ERROR: choose a job with --job-id, or run --list-jobs", file=sys.stderr)
        return 2

    import os
    email = os.environ.get("GOOGLE_EMAIL")
    password = os.environ.get("GOOGLE_PASSWORD")
    if not email or not password:
        print("ERROR: GOOGLE_EMAIL and GOOGLE_PASSWORD must be set in .env",
              file=sys.stderr)
        return 2

    user_data_dir = Path(os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profile")).resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    # Headless from env if --headless flag not passed.
    headless = args.headless or os.environ.get("HEADLESS", "false").lower() in ("1", "true", "yes")

    # Use real installed Chrome if requested (default). Needed for
    # passkey / Keychain access; Playwright Chromium doesn't have those.
    use_system_chrome = os.environ.get("USE_SYSTEM_CHROME", "true").lower() in ("1", "true", "yes")

    concurrency = args.concurrency or int(os.environ.get("CONCURRENCY", "1"))
    limit = args.limit or (int(os.environ["LIMIT"]) if os.environ.get("LIMIT") else None)

    list_url = args.url or (candidate_list_url(args.job_id) if args.job_id else "")
    out_root = Path(args.out) / (args.job_id or "_jobs")

    if not args.list_jobs:
        print(f"Job ID          : {args.job_id}")
        print(f"List URL        : {list_url}")
        print(f"Output root     : {out_root}")
    print(f"Headless        : {headless}")
    print(f"Concurrency     : {concurrency}")
    print(f"Limit           : {limit or '(full pull)'}")
    print(f"Cover pages     : {'no' if args.no_cover_pages else 'yes'}")
    print(f"Browser         : {'system Chrome' if use_system_chrome else 'Playwright Chromium'}")
    print()

    results = asyncio.run(
        run_crawler(
            job_id=args.job_id,
            list_url=list_url,
            out_root=out_root,
            headless=headless,
            user_data_dir=user_data_dir,
            email=email,
            password=password,
            concurrency=concurrency,
            limit=limit,
            use_system_chrome=use_system_chrome,
            bulk_export=args.bulk_export,
            bulk_batch_size=args.bulk_batch_size,
            list_jobs_only=args.list_jobs,
        )
    )

    if args.list_jobs:
        jobs = results
        if not jobs:
            print("No accessible jobs found.")
            return 1
        print(f"\nAccessible jobs ({len(jobs)}):")
        print(f"{'JOB ID':<14} JOB NAME")
        print(f"{'-' * 12:<14} {'-' * 40}")
        for job in jobs:
            assert isinstance(job, JobRef)
            print(f"{job.job_id:<14} {job.name}")
        print("\nSelect one with: scripts/crawl.py --job-id <JOB_ID> --bulk-export")
        return 0

    # Write metadata.json
    metadata_path = out_root / "metadata.json"
    write_metadata(results, metadata_path, args.job_id)
    print(f"\nWrote metadata to {metadata_path}")

    if args.bulk_export:
        failures = sum(r.status == "bulk_request_failed" for r in results)
        if failures:
            print("Bulk export failed; no email was queued for the failed batch.", file=sys.stderr)
            return 1
        print("Bulk export requests submitted; Greenhouse will email the merged PDFs.")
        return 0

    if args.skip_merge:
        print("Skipping merge (per --skip-merge)")
        return 0

    # Build the merged PDF
    raw_dir = out_root / "raw"
    out_pdf = out_root / "all_cvs.pdf"
    page_count = merge_pdfs(
        raw_dir=raw_dir,
        out_pdf=out_pdf,
        metadata_path=metadata_path,
        include_cover_pages=not args.no_cover_pages,
    )
    print(f"Wrote merged PDF to {out_pdf} ({page_count} pages)")

    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"\nFinal counts: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
