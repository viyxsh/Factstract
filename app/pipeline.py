"""Deterministic extraction → grounding → normalization → comparison pipeline.

The pipeline is the only module that touches the OpenRouter client and the
repository together. It records `known_failure` records when a candidate
fact is not grounded against its source page, and it never silently
promotes model output into the fact table.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable

from app import normalize
from app.contracts import (
    DocumentRecord,
    FailureRecord,
    FactRecord,
    JobRecord,
    RelationshipRecord,
)
from app.openrouter import LlmFactCandidate, OpenRouterClient, OpenRouterError
from app.pdf import Chunk, PageText, chunk_pages, extract_pages, normalized_text, quote_is_grounded


ProgressCallback = Callable[[float, str], None]


@dataclass(slots=True, frozen=True)
class DocumentPages:
    document_id: int
    pages: list[PageText]
    chunks: list[Chunk]


def _deterministic_fallback_candidates(text: str, page_number: int) -> list[LlmFactCandidate]:
    """Offline fallback: regex-pull obvious INR/USD percent/amount candidates.

    Used when no `OPENROUTER_API_KEY` is configured so the demo runs offline.
    The pipeline still applies the grounding check, so unsupported numbers
    are recorded as failures rather than facts.
    """
    candidates: list[LlmFactCandidate] = []
    seen: set[tuple[str, str]] = set()
    pattern = re.compile(
        r"(?P<quote>(?P<value>\$?\d[\d,]*(?:\.\d+)?)\s*(?:USD|INR|Rs\.?|₹)?\s*(?:crore|cr|billion|bn|%|percent))",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        quote = match.group("quote").strip()
        value = match.group("value")
        unit_raw = quote[len(value):].strip().lower()
        if not unit_raw:
            continue
        if "usd" in unit_raw or "$" in quote:
            unit = "USD"
        elif any(token in unit_raw for token in ("billion", "bn")):
            unit = "INR billion"
        elif any(token in unit_raw for token in ("crore", "cr")):
            unit = "INR crore"
        elif "%" in unit_raw or "percent" in unit_raw:
            unit = "percent"
        else:
            unit = "INR"
        dedup_key = (quote.casefold(), unit)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        candidates.append(
            LlmFactCandidate(
                subject="extracted amount",
                metric="reported figure",
                value_display=value,
                unit=unit,
                period=None,
                scope=None,
                quote=quote,
                confidence=0.35,
            )
        )
    return candidates[:6]


def _page_lookup(pages: list[PageText]) -> dict[int, str]:
    return {page.page_number: page.text for page in pages}


def load_document_pages(document: DocumentRecord, pdf_path: Path) -> DocumentPages:
    pages = extract_pages(pdf_path)
    chunks = chunk_pages(pages)
    return DocumentPages(document_id=int(document.id or 0), pages=pages, chunks=chunks)


def _facts_from_candidates(
    candidates: Iterable[LlmFactCandidate],
    *,
    document_id: int,
    pages_by_number: dict[int, str],
) -> tuple[list[FactRecord], list[FailureRecord]]:
    facts: list[FactRecord] = []
    failures: list[FailureRecord] = []
    for candidate in candidates:
        if not candidate.quote:
            failures.append(
                FailureRecord(
                    document_id=document_id,
                    page_number=0,
                    stage="extraction",
                    reason="Model returned a candidate without a quote.",
                    source_excerpt=candidate.value_display or "",
                    suggested_improvement="Reject any candidate whose quote is missing.",
                )
            )
            continue
        page_text = pages_by_number.get(0, "")
        grounded_page = 0
        for number, text in pages_by_number.items():
            if quote_is_grounded(candidate.quote, text):
                page_text = text
                grounded_page = number
                break
        if not grounded_page:
            failures.append(
                FailureRecord(
                    document_id=document_id,
                    page_number=0,
                    stage="grounding",
                    reason="Quote was not found in the cited source page.",
                    source_excerpt=candidate.quote,
                    suggested_improvement="Only quote text that appears verbatim in the document.",
                )
            )
            continue
        normalized = normalize.normalize_fact(
            candidate.subject,
            candidate.metric,
            candidate.value_display,
            unit=candidate.unit,
            period=candidate.period,
            scope=candidate.scope,
        )
        facts.append(
            FactRecord(
                document_id=document_id,
                page_number=grounded_page,
                subject=candidate.subject,
                metric=candidate.metric,
                value_display=candidate.value_display,
                numeric_value=normalized.numeric_value,
                canonical_value_inr=normalized.canonical_value_inr,
                unit=candidate.unit,
                period=candidate.period,
                scope=candidate.scope,
                quote=candidate.quote,
                confidence=float(candidate.confidence),
                grounding_status="grounded",
                subject_key=normalized.subject_key,
                metric_key=normalized.metric_key,
                period_key=normalized.period_key,
                scope_key=normalized.scope_key,
                canonical_unit=normalized.unit_key,
                comparison_key=normalized.comparison_key,
            )
        )
    return facts, failures


def _candidates_for_chunk(
    chunk: Chunk,
    client: OpenRouterClient | None,
) -> list[LlmFactCandidate]:
    if client is not None and client.configured:
        try:
            return client.extract_facts(chunk.text, chunk.page_start, chunk.section_hint)
        except OpenRouterError:
            pass
    return _deterministic_fallback_candidates(chunk.text, chunk.page_start)


def _extract_for_document(
    document: DocumentRecord,
    pdf_path: Path,
    client: OpenRouterClient | None,
    progress: ProgressCallback | None = None,
) -> tuple[list[FactRecord], list[FailureRecord]]:
    document_pages = load_document_pages(document, pdf_path)
    if not document_pages.chunks:
        return [], [
            FailureRecord(
                document_id=int(document.id or 0),
                page_number=0,
                stage="extraction",
                reason="Document produced no text chunks (image-only or empty).",
                source_excerpt="",
                suggested_improvement="Run OCR before upload or provide a text-based PDF.",
            )
        ]
    pages_by_number = _page_lookup(document_pages.pages)
    all_facts: list[FactRecord] = []
    all_failures: list[FailureRecord] = []
    total = len(document_pages.chunks)
    for index, chunk in enumerate(document_pages.chunks, start=1):
        candidates = _candidates_for_chunk(chunk, client)
        facts, failures = _facts_from_candidates(
            candidates,
            document_id=int(document.id or 0),
            pages_by_number=pages_by_number,
        )
        all_facts.extend(facts)
        all_failures.extend(failures)
        if progress is not None:
            progress(10 + 70 * (index / total), f"Processed chunk {index}/{total}.")
    return all_facts, all_failures


def _compare_facts(facts: list[FactRecord]) -> list[RelationshipRecord]:
    by_key: dict[str, list[FactRecord]] = {}
    for fact in facts:
        key = fact.comparison_key
        if not key:
            continue
        by_key.setdefault(key, []).append(fact)

    relationships: list[RelationshipRecord] = []
    seen_pairs: set[tuple[int, int]] = set()
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (item.document_id, item.page_number, item.id or 0))
        anchor = group[0]
        anchor_canonical = anchor.canonical_value_inr
        for other in group[1:]:
            left_id = int(anchor.id or 0)
            right_id = int(other.id or 0)
            if left_id and right_id and left_id == right_id:
                continue
            pair = tuple(sorted((left_id, right_id)))
            if pair in seen_pairs:
                continue
            if (
                anchor_canonical is not None
                and other.canonical_value_inr is not None
                and anchor_canonical != other.canonical_value_inr
            ):
                if anchor.period and other.period and anchor.period != other.period:
                    relationships.append(
                        RelationshipRecord(
                            left_fact_id=left_id,
                            right_fact_id=right_id,
                            kind="reconciles",
                            title=f"Reconciliation: {anchor.subject} ({anchor.period} vs {other.period})",
                            rationale=(
                                f"Same subject '{anchor.subject}' and metric '{anchor.metric}' "
                                f"with different estimation vintage ({anchor.period} vs {other.period}); "
                                f"values differ as expected and reconcile by period."
                            ),
                        )
                    )
                else:
                    relationships.append(
                        RelationshipRecord(
                            left_fact_id=left_id,
                            right_fact_id=right_id,
                            kind="contradicts",
                            title=f"Contradiction: {anchor.subject} {anchor.metric}",
                            rationale=(
                                f"Two documents report different values for '{anchor.subject}' "
                                f"'{anchor.metric}' without an explanation in period or scope."
                            ),
                        )
                    )
            else:
                relationships.append(
                    RelationshipRecord(
                        left_fact_id=left_id,
                        right_fact_id=right_id,
                        kind="corroborates",
                        title=f"Corroboration: {anchor.subject} {anchor.metric}",
                        rationale=(
                            f"Same subject '{anchor.subject}', metric '{anchor.metric}', "
                            f"and canonical INR amount across documents."
                        ),
                    )
                )
            seen_pairs.add(pair)
    return relationships


@dataclass(slots=True)
class PipelineResult:
    document: DocumentRecord
    facts: list[FactRecord]
    failures: list[FailureRecord]
    relationships: list[RelationshipRecord]


class PipelineRunner:
    """Stateless orchestrator. The repository owns persistence, the runner
    produces ready-to-insert records.
    """

    def __init__(self, client: OpenRouterClient | None = None) -> None:
        self.client = client or OpenRouterClient()

    def run_for_document(
        self,
        document: DocumentRecord,
        pdf_path: Path,
        progress: ProgressCallback | None = None,
        known_facts: Iterable[FactRecord] | None = None,
    ) -> PipelineResult:
        if progress is not None:
            progress(0.05, "Extracting pages.")
        facts, failures = _extract_for_document(
            document,
            pdf_path,
            client=self.client if self.client.configured else None,
            progress=progress,
        )
        if progress is not None:
            progress(0.9, "Comparing facts across stored documents.")
        existing = list(known_facts or [])
        combined = existing + facts
        relationships = _compare_facts(combined)
        if progress is not None:
            progress(0.98, "Preparing result.")
        return PipelineResult(document=document, facts=facts, failures=failures, relationships=relationships)


__all__ = [
    "DocumentPages",
    "PipelineResult",
    "PipelineRunner",
    "load_document_pages",
]


def _self_test() -> None:  # pragma: no cover - helper for ad-hoc checks
    payload = json.loads(os.environ.get("DEMO_FIXTURE", "{}"))
    print(json.dumps(payload, default=str, indent=2))
