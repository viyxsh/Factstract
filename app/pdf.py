"""Deterministic PDF text extraction and conservative chunking."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re


class PdfExtractionError(RuntimeError):
    """Raised when a PDF cannot be opened or text cannot be read."""


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str
    text_hash: str


@dataclass(frozen=True)
class Chunk:
    id: str
    page_start: int
    page_end: int
    text: str
    section_hint: str | None


def normalized_text(value: str) -> str:
    """Normalize only whitespace so quote matching remains evidence-preserving."""
    return re.sub(r"\s+", " ", value).strip()


def extract_pages(pdf_path: Path) -> list[PageText]:
    """Return stable, page-numbered text for a text-based PDF.

    PyMuPDF is imported here rather than at module import time so unit tests that
    do not process PDFs can run before optional runtime dependencies are installed.
    """
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as error:  # pragma: no cover - requires missing dependency
        raise PdfExtractionError("PyMuPDF is not installed. Run pip install -r requirements.txt.") from error

    try:
        document = fitz.open(pdf_path)
    except Exception as error:  # pragma: no cover - depends on malformed PDFs
        raise PdfExtractionError(f"Could not open PDF: {pdf_path.name}") from error

    pages: list[PageText] = []
    try:
        for position, page in enumerate(document, start=1):
            text = normalized_text(page.get_text("text"))
            pages.append(PageText(position, text, sha256(text.encode("utf-8")).hexdigest()))
    finally:
        document.close()
    return pages


def _section_hint(text: str) -> str | None:
    """Select the first short title-like line without semantic inference."""
    for line in text.split("\n"):
        candidate = line.strip()
        if 3 <= len(candidate) <= 90 and not candidate.endswith((".", ";", ":")):
            return candidate
    return None


def chunk_pages(pages: list[PageText], max_chars: int = 6_000) -> list[Chunk]:
    """Split each page on sentence-ish boundaries, never mixing page citations."""
    if max_chars < 500:
        raise ValueError("max_chars must be at least 500")
    chunks: list[Chunk] = []
    for page in pages:
        text = page.text
        if not text:
            continue
        parts = re.split(r"(?<=[.!?])\s+", text)
        buffer: list[str] = []
        size = 0
        part_number = 1
        for part in parts:
            if not part:
                continue
            if buffer and size + len(part) + 1 > max_chars:
                joined = " ".join(buffer)
                chunks.append(Chunk(f"p{page.page_number}-{part_number}", page.page_number, page.page_number, joined, _section_hint(joined)))
                part_number += 1
                buffer, size = [], 0
            # A single giant unbroken line remains a deterministic chunk.
            buffer.append(part)
            size += len(part) + 1
        if buffer:
            joined = " ".join(buffer)
            chunks.append(Chunk(f"p{page.page_number}-{part_number}", page.page_number, page.page_number, joined, _section_hint(joined)))
    return chunks


def quote_is_grounded(quote: str, page_text: str) -> bool:
    return bool(quote and normalized_text(quote).casefold() in normalized_text(page_text).casefold())

