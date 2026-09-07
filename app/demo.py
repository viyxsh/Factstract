"""Deterministic demo loader.

The demo loader inserts exactly the four showcase records required by the
spec — one corroborates, one contradicts, one reconciles, and one explicit
known failure — plus the underlying facts and documents. No LLM call is
made. Calling the loader twice is idempotent for the demo dataset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from app.contracts import (
    DocumentRecord,
    FailureRecord,
    FactRecord,
    RelationshipRecord,
)
from app.db import Repository


@dataclass(slots=True, frozen=True)
class DemoDocument:
    filename: str
    sha256: str
    page_count: int


@dataclass(slots=True, frozen=True)
class _FactTemplate:
    key: str
    document_sha: str
    page_number: int
    subject: str
    metric: str
    value_display: str
    unit: str
    period: str | None
    scope: str | None
    quote: str
    confidence: float
    canonical_unit: str
    canonical_value: Decimal


@dataclass(slots=True)
class _RelationshipTemplate:
    left_key: str
    right_key: str
    kind: str
    title: str
    rationale: str


@dataclass(slots=True)
class _FailureTemplate:
    document_sha: str
    page_number: int
    stage: str
    reason: str
    source_excerpt: str
    suggested_improvement: str


@dataclass(slots=True)
class _DemoFixtures:
    timestamp: str
    documents: list[DemoDocument]
    facts: list[_FactTemplate]
    relationships: list[_RelationshipTemplate]
    failures: list[_FailureTemplate]


_FIXTURES: _DemoFixtures = _DemoFixtures(
    timestamp="2026-01-01T00:00:00.000000+00:00",
    documents=[
        DemoDocument(
            filename="india-economic-survey-2024-25-excerpt.pdf",
            sha256="demo-sha-india-economic-survey",
            page_count=89,
        ),
        DemoDocument(
            filename="rbi-annual-report-2024-25-excerpt.pdf",
            sha256="demo-sha-rbi-annual-report",
            page_count=100,
        ),
        DemoDocument(
            filename="imf-india-2025-article-iv-excerpt.pdf",
            sha256="demo-sha-imf-india-2025",
            page_count=95,
        ),
    ],
    facts=[
        _FactTemplate(
            key="gdp-survey",
            document_sha="demo-sha-india-economic-survey",
            page_number=42,
            subject="India GDP FY25",
            metric="nominal GDP",
            value_display="7,225 INR Cr",
            unit="INR crore",
            period="FY25",
            scope="India",
            quote="Nominal GDP for FY25 was estimated at 7,225 INR Cr.",
            confidence=0.92,
            canonical_unit="inr",
            canonical_value=Decimal("72250000000"),
        ),
        _FactTemplate(
            key="gdp-rbi",
            document_sha="demo-sha-rbi-annual-report",
            page_number=18,
            subject="India GDP FY25",
            metric="nominal GDP",
            value_display="72.25 INR Bn",
            unit="INR billion",
            period="FY25",
            scope="India",
            quote="The Reserve Bank placed FY25 nominal GDP at 72.25 INR Bn.",
            confidence=0.9,
            canonical_unit="inr",
            canonical_value=Decimal("72250000000"),
        ),
        _FactTemplate(
            key="cpi-survey",
            document_sha="demo-sha-india-economic-survey",
            page_number=46,
            subject="India CPI inflation FY25",
            metric="CPI inflation",
            value_display="4.6 percent",
            unit="percent",
            period="FY25",
            scope="India",
            quote="Headline CPI inflation averaged 4.6 percent in FY25.",
            confidence=0.9,
            canonical_unit="percent",
            canonical_value=Decimal("4.6"),
        ),
        _FactTemplate(
            key="cpi-imf",
            document_sha="demo-sha-imf-india-2025",
            page_number=11,
            subject="India CPI inflation FY25",
            metric="CPI inflation",
            value_display="5.1 percent",
            unit="percent",
            period="FY25",
            scope="India",
            quote="The IMF staff report estimates FY25 CPI inflation at 5.1 percent.",
            confidence=0.88,
            canonical_unit="percent",
            canonical_value=Decimal("5.1"),
        ),
        _FactTemplate(
            key="cad-rbi",
            document_sha="demo-sha-rbi-annual-report",
            page_number=22,
            subject="India current account deficit FY25",
            metric="current account deficit",
            value_display="1.1 percent",
            unit="percent",
            period="FY25",
            scope="India",
            quote="The current account deficit narrowed to 1.1 percent of GDP in FY25.",
            confidence=0.85,
            canonical_unit="percent",
            canonical_value=Decimal("1.1"),
        ),
        _FactTemplate(
            key="cad-imf",
            document_sha="demo-sha-imf-india-2025",
            page_number=13,
            subject="India current account deficit FY24",
            metric="current account deficit",
            value_display="0.7 percent",
            unit="percent",
            period="FY24",
            scope="India",
            quote="The IMF estimates FY24 current account deficit at 0.7 percent of GDP.",
            confidence=0.87,
            canonical_unit="percent",
            canonical_value=Decimal("0.7"),
        ),
    ],
    relationships=[
        _RelationshipTemplate(
            left_key="gdp-survey",
            right_key="gdp-rbi",
            kind="corroborates",
            title="Nominal GDP FY25 across two reports",
            rationale=(
                "Both the Economic Survey and RBI report the FY25 nominal GDP at "
                "72,250,000,000 INR after unit canonicalization (7,225 INR Cr == 72.25 INR Bn)."
            ),
        ),
        _RelationshipTemplate(
            left_key="cpi-survey",
            right_key="cpi-imf",
            kind="contradicts",
            title="FY25 CPI inflation disagrees",
            rationale=(
                "The Economic Survey reports 4.6 percent average FY25 CPI inflation "
                "while the IMF staff report estimates 5.1 percent for the same period; "
                "the difference is not explained by differing periods or scopes."
            ),
        ),
        _RelationshipTemplate(
            left_key="cad-rbi",
            right_key="cad-imf",
            kind="reconciles",
            title="Current account deficit across vintages",
            rationale=(
                "Same metric and scope but distinct estimation vintages (FY25 vs FY24); "
                "the change in value is explained by the explicit period difference."
            ),
        ),
    ],
    failures=[
        _FailureTemplate(
            document_sha="demo-sha-imf-india-2025",
            page_number=27,
            stage="grounding",
            reason="Candidate quote did not appear verbatim in the cited page text.",
            source_excerpt="India GDP growth surged to 9.2 percent in FY25.",
            suggested_improvement=(
                "Use a contiguous excerpt from page 27 of the IMF report. "
                "The current quote is paraphrased."
            ),
        ),
    ],
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def load_demo(repository: Repository) -> dict[str, int]:
    """Insert (or re-insert) deterministic demo records into the repository."""
    repository.delete_all_demo_records()
    timestamp = _FIXTURES.timestamp
    document_records: dict[str, DocumentRecord] = {}
    for spec in _FIXTURES.documents:
        record = DocumentRecord(
            filename=spec.filename,
            sha256=spec.sha256,
            page_count=spec.page_count,
            status="done",
        )
        document_records[spec.sha256] = repository.create_document(record)

    fact_ids: dict[str, int] = {}
    for spec in _FIXTURES.facts:
        document = document_records[spec.document_sha]
        from app import normalize as _normalize

        normalized = _normalize.normalize_fact(
            spec.subject,
            spec.metric,
            spec.value_display,
            unit=spec.unit,
            period=spec.period,
            scope=spec.scope,
        )
        record = FactRecord(
            document_id=int(document.id or 0),
            page_number=spec.page_number,
            subject=spec.subject,
            metric=spec.metric,
            value_display=spec.value_display,
            numeric_value=normalized.numeric_value,
            canonical_value_inr=spec.canonical_value,
            unit=spec.unit,
            period=spec.period,
            scope=spec.scope,
            quote=spec.quote,
            confidence=spec.confidence,
            grounding_status="grounded",
            subject_key=normalized.subject_key,
            metric_key=normalized.metric_key,
            period_key=normalized.period_key,
            scope_key=normalized.scope_key,
            canonical_unit=normalized.unit_key,
            comparison_key=normalized.comparison_key,
        )
        stored = repository.add_fact(record)
        fact_ids[spec.key] = int(stored.id or 0)

    for spec in _FIXTURES.relationships:
        record = RelationshipRecord(
            left_fact_id=fact_ids[spec.left_key],
            right_fact_id=fact_ids[spec.right_key],
            kind=spec.kind,
            title=spec.title,
            rationale=spec.rationale,
        )
        repository.add_relationship(record)

    for spec in _FIXTURES.failures:
        document = document_records[spec.document_sha]
        record = FailureRecord(
            document_id=int(document.id or 0),
            page_number=spec.page_number,
            stage=spec.stage,
            reason=spec.reason,
            source_excerpt=spec.source_excerpt,
            suggested_improvement=spec.suggested_improvement,
        )
        repository.add_failure(record)

    return {
        "documents": len(_FIXTURES.documents),
        "facts": len(_FIXTURES.facts),
        "relationships": len(_FIXTURES.relationships),
        "failures": len(_FIXTURES.failures),
    }


__all__ = ["load_demo"]
