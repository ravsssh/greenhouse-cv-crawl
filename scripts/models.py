"""Shared data types for the Greenhouse CV crawl pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class CandidateRef:
    person_id: str
    application_id: str
    detail_url: str
    name: str = ""


@dataclass
class JobRef:
    job_id: str
    name: str
    dashboard_url: str


@dataclass
class CrawlResult:
    candidate_id: str
    name: str
    status: str  # "ok" | "no_resume" | "download_failed" | "error" | "bulk_requested" | "bulk_request_failed"
    detail_url: str = ""
    pdf_path: Optional[str] = None
    source_url: str = ""
    bytes: int = 0
    error: str = ""

    def to_json(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "status": self.status,
            "detail_url": self.detail_url,
            "source_url": self.source_url,
            "pdf_file": self.pdf_path,
            "bytes": self.bytes,
            "error": self.error,
        }


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "unnamed"
