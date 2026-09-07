import tempfile
from pathlib import Path

import fitz  # type: ignore[import-not-found]
import pytest

from app.pdf import Chunk, chunk_pages, extract_pages, normalized_text, quote_is_grounded


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    pdf = fitz.open()
    page_one = pdf.new_page()
    page_one.insert_text(
        (72, 72),
        "Nominal GDP for FY25 was estimated at 7,225 INR Cr. "
        "Headline CPI inflation averaged 4.6 percent in FY25.",
    )
    page_two = pdf.new_page()
    page_two.insert_text(
        (72, 72),
        "Total revenue grew 12.4 percent year over year driven by parcel volumes.",
    )
    out = tmp_path / "fixture.pdf"
    pdf.save(out)
    pdf.close()
    return out


def test_extract_pages_returns_stable_numbers(sample_pdf: Path):
    pages = extract_pages(sample_pdf)
    assert [page.page_number for page in pages] == [1, 2]
    assert pages[0].text_hash == pages[0].text_hash


def test_chunk_pages_never_mix_page_numbers(sample_pdf: Path):
    pages = extract_pages(sample_pdf)
    chunks = chunk_pages(pages)
    assert chunks, "expected at least one chunk"
    for chunk in chunks:
        assert chunk.page_start == chunk.page_end
        assert chunk.id.startswith("p")
        # the page text appears entirely inside the chunk for one-page chunks
        if chunk.page_start == 1:
            assert "7,225 INR Cr" in chunk.text


def test_quote_is_grounded_normalizes_whitespace():
    page = "Nominal GDP for FY25 was estimated at 7,225 INR Cr."
    assert quote_is_grounded("7,225 INR Cr", page)
    assert quote_is_grounded("  7,225   INR  Cr  ", page)
    assert not quote_is_grounded("7,300 INR Cr", page)


def test_normalized_text_collapses_whitespace():
    assert normalized_text("hello\n\n world\t\t!") == "hello world !"


def test_chunk_pages_handles_long_paragraphs():
    long_paragraph = "Sentence one. " + "Sentence two. " * 200
    page = type("P", (), {"page_number": 1, "text": normalized_text(long_paragraph), "text_hash": "x"})()
    chunks = chunk_pages([page], max_chars=500)
    assert len(chunks) > 1
    assert all(chunk.id.startswith("p1-") for chunk in chunks)
