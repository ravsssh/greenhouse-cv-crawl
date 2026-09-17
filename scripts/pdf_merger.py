"""Merge every candidate's downloaded CV into a single PDF.

Each candidate's CV is preceded by a one-page cover sheet so the merged
file is skim-friendly. metadata.json stays the authoritative index.

Pure pypdf — no reportlab required. The cover page is a hand-built
PDF content stream (one text line per visual line), embedded into a
single-page PDF, then appended ahead of the candidate's real CV.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

log = logging.getLogger(__name__)

PAGE_W = 595.27
PAGE_H = 841.89
FONT_SIZE = 14
LINE_HEIGHT = 17
MARGIN_LEFT = 56
MARGIN_TOP = 70


def wrap(text: str, width: int = 70) -> list[str]:
    """Wrap a long string into lines of ~width characters."""
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for w in words:
        if cur and cur_len + 1 + len(w) > width:
            lines.append(" ".join(cur))
            cur, cur_len = [w], len(w)
        else:
            cur.append(w)
            cur_len += (1 if cur_len else 0) + len(w)
    if cur:
        lines.append(" ".join(cur))
    return lines


@dataclass
class CoverFields:
    name: str
    candidate_id: str
    page_index: int
    source_url: str
    downloaded_at: str
    status: str

    def render_lines(self) -> list[tuple[str, str]]:
        title = self.name or "(unnamed candidate)"
        return [
            ("h1", title),
            ("body", ""),
            ("body", f"Candidate ID : {self.candidate_id}"),
            ("body", f"Page index   : {self.page_index}"),
            ("body", f"Status       : {self.status}"),
            ("body", f"Downloaded   : {self.downloaded_at}"),
            ("body", ""),
            ("body", "Source URL:"),
            ("body", self.source_url),
        ]


def _build_cover_page(fields: CoverFields) -> PdfReader:
    """Build a single-page PDF containing a cover sheet for one candidate."""
    lines = fields.render_lines()
    rendered: list[tuple[str, str]] = []
    for style, text in lines:
        if style == "h1" or len(text) <= 80:
            rendered.append((style, text))
        else:
            for chunk in wrap(text, 80):
                rendered.append((style, chunk))

    content_parts: list[str] = []
    y = PAGE_H - MARGIN_TOP
    for style, text in rendered:
        if not text:
            y -= LINE_HEIGHT // 2
            continue
        if style == "h1":
            content_parts.append(
                f"BT /F1 22 Tf {MARGIN_LEFT} {y:.2f} Td ({escape(text)}) Tj ET"
            )
            y -= 28
        else:
            content_parts.append(
                f"BT /F1 {FONT_SIZE} Tf {MARGIN_LEFT} {y:.2f} Td ({escape(text)}) Tj ET"
            )
            y -= LINE_HEIGHT

    stream_data = "\n".join(content_parts).encode("latin-1", errors="replace")

    writer = PdfWriter()
    page = writer.add_blank_page(width=PAGE_W, height=PAGE_H)

    content_stream = DecodedStreamObject()
    content_stream.set_data(stream_data)
    page[NameObject("/Contents")] = ContentStream(content_stream, page)

    font_dict = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Courier"),
    })
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_dict})
    })
    page[NameObject("/Resources")] = resources

    buf = io.BytesIO()
    writer.write_stream(buf)
    buf.seek(0)
    return PdfReader(buf)


def escape(s: str) -> str:
    """Escape a string for inclusion in a PDF literal string (..)."""
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def discover_candidates(raw_dir: Path) -> list[dict]:
    """Walk raw/ for downloaded PDFs and return per-candidate dicts.

    Filenames are <candidate_id>_<slug>.pdf — the candidate_id is the
    leading numeric prefix.
    """
    records: list[dict] = []
    for pdf in sorted(raw_dir.glob("*.pdf")):
        m = re.match(r"^(\d+)_", pdf.name)
        if not m:
            log.warning("Skipping file with no numeric prefix: %s", pdf.name)
            continue
        cid = m.group(1)
        slug = pdf.stem[len(cid) + 1:]
        name = slug.replace("-", " ").strip() or f"candidate {cid}"
        records.append({
            "candidate_id": cid,
            "name": name,
            "pdf_path": pdf,
            "source_url": "",
            "status": "ok",
            "downloaded_at": "",
        })
    return records


def load_metadata(json_path: Path) -> dict[str, dict]:
    """Load metadata.json and key it by candidate_id."""
    if not json_path.exists():
        return {}
    data = json.loads(json_path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for row in data.get("candidates", []):
        out[str(row.get("candidate_id"))] = row
    return out


def merge(
    raw_dir: Path,
    out_pdf: Path,
    metadata_path: Path,
    include_cover_pages: bool = True,
) -> int:
    """Build out_pdf from every PDF in raw_dir. Returns the page count.

    Always rebuilds from scratch (cheap at 600 candidates).
    """
    metadata_by_id = load_metadata(metadata_path)
    records = discover_candidates(raw_dir)
    if not records:
        log.warning("No PDFs in %s — nothing to merge", raw_dir)
        return 0

    writer = PdfWriter()
    page_count = 0

    for rec in records:
        meta = metadata_by_id.get(rec["candidate_id"], {})
        rec["name"] = meta.get("name") or rec["name"]
        rec["source_url"] = meta.get("source_url") or rec["source_url"]
        rec["status"] = meta.get("status") or rec["status"]
        rec["downloaded_at"] = meta.get("downloaded_at") or rec["downloaded_at"]

        if include_cover_pages:
            cover_fields = CoverFields(
                name=rec["name"],
                candidate_id=rec["candidate_id"],
                page_index=page_count + 1,
                source_url=rec["source_url"],
                downloaded_at=rec["downloaded_at"],
                status=rec["status"],
            )
            cover_pdf = _build_cover_page(cover_fields)
            for p in cover_pdf.pages:
                writer.add_page(p)
                page_count += 1

        reader = PdfReader(str(rec["pdf_path"]))
        for p in reader.pages:
            writer.add_page(p)
            page_count += 1

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with out_pdf.open("wb") as f:
        writer.write_stream(f)

    log.info("Wrote %s — %d pages from %d candidates", out_pdf, page_count, len(records))
    return page_count
