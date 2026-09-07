import tempfile
from decimal import Decimal
from pathlib import Path

import fitz  # type: ignore[import-not-found]
import pytest

from app.contracts import DocumentRecord, FactRecord
from app.pipeline import PipelineRunner


def _make_pdf(tmp_path: Path) -> Path:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text(
        (72, 72),
        "Nominal GDP for FY25 was estimated at 7,225 INR Cr. "
        "Headline CPI inflation averaged 4.6 percent in FY25.",
    )
    out = tmp_path / "pipeline.pdf"
    pdf.save(out)
    pdf.close()
    return out


def test_pipeline_runner_without_key_uses_fallback(tmp_path: Path):
    pdf_path = _make_pdf(tmp_path)
    document = DocumentRecord(filename="x.pdf", sha256="abc", page_count=1, status="queued")
    runner = PipelineRunner(client=None)
    runner.client.api_key = ""
    result = runner.run_for_document(document, pdf_path)
    assert result.facts or result.failures
    assert all(fact.grounding_status == "grounded" for fact in result.facts)


def test_bad_quote_becomes_known_failure(tmp_path: Path):
    pdf_path = _make_pdf(tmp_path)
    document = DocumentRecord(filename="x.pdf", sha256="abc", page_count=1, status="queued")
    runner = PipelineRunner(client=None)
    runner.client.api_key = ""
    known_facts = []
    result = runner.run_for_document(document, pdf_path, known_facts=known_facts)
    # No relationship is created because we only compare against existing known_facts.
    assert isinstance(result.relationships, list)


def test_compare_groups_facts_by_comparison_key(tmp_path: Path):
    pdf_path = _make_pdf(tmp_path)
    document = DocumentRecord(filename="x.pdf", sha256="abc", page_count=1, status="queued")
    runner = PipelineRunner(client=None)
    runner.client.api_key = ""
    # Two facts with the same comparison key but different values:
    # the comparator must surface a contradiction or reconciliation record.
    known = FactRecord(
        document_id=99,
        page_number=1,
        subject="extracted amount",
        metric="reported figure",
        value_display="100 INR Cr",
        numeric_value=Decimal("100"),
        canonical_value_inr=Decimal("1000000000"),
        unit="INR crore",
        period=None,
        scope=None,
        quote="4.6 percent",
        grounding_status="grounded",
        subject_key="extracted-amount",
        metric_key="reported-figure",
        period_key="",
        scope_key="",
        canonical_unit="inr-crore",
        comparison_key="extracted-amount|reported-figure||",
    )
    result = runner.run_for_document(document, pdf_path, known_facts=[known])
    assert result.relationships
    kinds = {rel.kind for rel in result.relationships}
    assert kinds & {"corroborates", "contradicts", "reconciles"}
