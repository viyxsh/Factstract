"""Deterministic extraction → grounding → normalization → comparison pipeline.

The pipeline is the only module that touches the Gemini client and the
repository together. It records `known_failure` records when a candidate
fact is not grounded against its source page, and it never silently
promotes model output into the fact table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable

from app import normalize
from app.contracts import (
    DocumentRecord,
    FailureRecord,
    FactRecord,
    JobRecord,
    RelationshipRecord,
)
from app.gemini import GeminiClient, GeminiError, LlmFactCandidate
from app.pdf import Chunk, PageText, chunk_pages, extract_pages, normalized_text, quote_is_grounded

LOGGER = logging.getLogger("app.pipeline")

FILE_WINDOW_PAGES = 25
# A window whose pages hold less extractable text than this is reported as
# image-only without spending an API call (any candidate from it could never
# ground against empty page text anyway).
TEXT_WINDOW_MIN_CHARS = 200
MAX_WORKERS = max(1, int(os.environ.get("FACT_KNOWLEDGE_MAX_WORKERS", "4") or "4"))


ProgressCallback = Callable[[float, str], None]


@dataclass(slots=True, frozen=True)
class DocumentPages:
    document_id: int
    pages: list[PageText]
    chunks: list[Chunk]


def _deterministic_fallback_candidates(text: str, page_number: int) -> list[LlmFactCandidate]:
    """Offline fallback: regex-pull obvious INR/USD percent/amount candidates.

    Used when no `GEMINI_API_KEY` is configured so the demo runs offline.
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
    client: GeminiClient | None,
) -> list[LlmFactCandidate]:
    if client is not None and client.configured:
        try:
            return client.extract_facts(chunk.text, chunk.page_start, chunk.section_hint)
        except GeminiError:
            pass
    return _deterministic_fallback_candidates(chunk.text, chunk.page_start)


def _file_windows(page_count: int, window: int = FILE_WINDOW_PAGES) -> list[tuple[int, int]]:
    if page_count <= 0:
        return []
    return [(start, min(start + window - 1, page_count)) for start in range(1, page_count + 1, window)]


def _window_text_length(pages_by_number: dict[int, str], start: int, end: int) -> int:
    return sum(len(pages_by_number.get(number, "")) for number in range(start, end + 1))


def _extract_via_file(
    document: DocumentRecord,
    pdf_path: Path,
    pages_by_number: dict[int, str],
    client: GeminiClient,
    progress: ProgressCallback | None = None,
    window_pages: int = FILE_WINDOW_PAGES,
    max_workers: int = MAX_WORKERS,
) -> tuple[list[FactRecord], list[FailureRecord]] | None:
    """Large-window extraction over a Files API handle. Returns None when the
    file path is unavailable so the caller falls back to chunk extraction.

    Text-empty windows are recorded as image-only failures without an API
    call; the rest run in parallel (order-preserving) against one resolved
    file handle.
    """
    try:
        digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        file_name = client.get_or_upload_pdf(pdf_path, sha=digest, filename=document.filename or pdf_path.name)
        file_ref = client.get_file_ref(file_name)
    except (OSError, GeminiError):
        return None
    windows = _file_windows(len(pages_by_number), window_pages)
    if not windows:
        return None
    document_id = int(document.id or 0)
    all_facts: list[FactRecord] = []
    all_failures: list[FailureRecord] = []
    live_windows: list[tuple[int, int]] = []
    for start, end in windows:
        if _window_text_length(pages_by_number, start, end) < TEXT_WINDOW_MIN_CHARS:
            all_failures.append(
                FailureRecord(
                    document_id=document_id,
                    page_number=start,
                    stage="extraction",
                    reason=f"Pages {start}-{end} hold no extractable text (scanned or image-only).",
                    source_excerpt=f"pages {start}-{end}",
                    suggested_improvement="Run OCR before upload; OCR is out of scope for this prototype.",
                )
            )
        else:
            live_windows.append((start, end))
    total = len(windows)
    done = 0
    progress_lock = threading.Lock()

    def _run_window(indexed: tuple[int, tuple[int, int]]) -> tuple[list[FactRecord], list[FailureRecord]]:
        nonlocal done
        index, (start, end) = indexed
        try:
            candidates = client.extract_facts_for_file(file_name, start, end, file_ref=file_ref)
        except GeminiError as error:
            # Raw provider errors stay in the server log; the reviewer UI
            # only ever sees a friendly, actionable message.
            LOGGER.warning("Extraction failed for doc %s pages %s-%s: %s", document.id, start, end, error)
            outcome: tuple[list[FactRecord], list[FailureRecord]] = (
                [],
                [
                    FailureRecord(
                        document_id=document_id,
                        page_number=start,
                        stage="extraction",
                        reason=f"Fact extraction hit a temporary service issue on pages {start}-{end}.",
                        source_excerpt=f"pages {start}-{end}",
                        suggested_improvement="Retry processing this document; already-stored facts are unaffected.",
                    )
                ],
            )
        else:
            outcome = _facts_from_candidates(
                candidates,
                document_id=document_id,
                pages_by_number=pages_by_number,
            )
        if progress is not None:
            with progress_lock:
                done += 1
                progress(0.10 + 0.70 * (done / total), f"Processed pages {start}-{end} ({done}/{total}).")
        return outcome

    workers = max(1, min(max_workers, len(live_windows))) if live_windows else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for facts, failures in pool.map(_run_one_safe(_run_window), enumerate(live_windows, start=1)):
            all_facts.extend(facts)
            all_failures.extend(failures)
    return all_facts, all_failures


def _run_one_safe(
    func: Callable[[tuple[int, tuple[int, int]]], tuple[list[FactRecord], list[FailureRecord]]],
) -> Callable[[tuple[int, tuple[int, int]]], tuple[list[FactRecord], list[FailureRecord]]]:
    """Guard a window worker so one unexpected crash can't kill the pool."""
    from functools import wraps

    @wraps(func)
    def wrapper(indexed: tuple[int, tuple[int, int]]) -> tuple[list[FactRecord], list[FailureRecord]]:
        try:
            return func(indexed)
        except Exception as error:  # pragma: no cover - defensive
            LOGGER.exception("Window worker crashed: %s", error)
            return [], []

    return wrapper


def _extract_for_document(
    document: DocumentRecord,
    pdf_path: Path,
    client: GeminiClient | None,
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
    if client is not None and client.configured:
        via_file = _extract_via_file(document, pdf_path, pages_by_number, client, progress)
        if via_file is not None:
            return via_file
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
            progress(0.10 + 0.70 * (index / total), f"Processed chunk {index}/{total}.")
    return all_facts, all_failures


def _same_value(left: FactRecord, right: FactRecord) -> bool:
    """Equal canonical amounts corroborate. For non-numeric facts (no
    canonical value), equal display text corroborates."""
    if left.canonical_value_inr is not None and right.canonical_value_inr is not None:
        return left.canonical_value_inr == right.canonical_value_inr
    if left.canonical_value_inr is None and right.canonical_value_inr is None:
        return normalized_text(left.value_display or "").casefold() == normalized_text(
            right.value_display or ""
        ).casefold()
    return False


def _context_differences(left: FactRecord, right: FactRecord) -> list[str]:
    """Dimensions (period/scope) that are explicitly set on both facts but
    disagree — each one explains a value gap instead of contradicting it."""
    differences: list[str] = []
    if (
        left.period
        and right.period
        and (left.period_key or "") != (right.period_key or "")
    ):
        differences.append(f"period ({left.period} vs {right.period})")
    if left.scope and right.scope and (left.scope_key or "") != (right.scope_key or ""):
        differences.append(f"scope ({left.scope} vs {right.scope})")
    return differences


def compare_facts(facts: list[FactRecord]) -> list[RelationshipRecord]:
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
        for other in group[1:]:
            left_id = int(anchor.id or 0)
            right_id = int(other.id or 0)
            if left_id and right_id and left_id == right_id:
                continue
            pair = tuple(sorted((left_id, right_id)))
            if pair in seen_pairs:
                continue
            if _same_value(anchor, other):
                relationships.append(
                    RelationshipRecord(
                        left_fact_id=left_id,
                        right_fact_id=right_id,
                        kind="corroborates",
                        title=f"Corroboration: {anchor.subject} {anchor.metric}",
                        rationale=(
                            f"Same subject '{anchor.subject}', metric '{anchor.metric}', "
                            f"and equal value across documents."
                        ),
                    )
                )
            else:
                differences = _context_differences(anchor, other)
                if differences:
                    relationships.append(
                        RelationshipRecord(
                            left_fact_id=left_id,
                            right_fact_id=right_id,
                            kind="reconciles",
                            title=f"Reconciliation: {anchor.subject} {anchor.metric}",
                            rationale=(
                                f"Same subject '{anchor.subject}' and metric '{anchor.metric}' "
                                f"but different {', '.join(differences)}; "
                                f"the value gap is explained by context, not disagreement."
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

    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

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
        relationships = compare_facts(combined)
        if progress is not None:
            progress(0.98, "Preparing result.")
        return PipelineResult(document=document, facts=facts, failures=failures, relationships=relationships)


__all__ = [
    "DocumentPages",
    "PipelineResult",
    "PipelineRunner",
    "compare_facts",
    "load_document_pages",
]


def _self_test() -> None:  # pragma: no cover - helper for ad-hoc checks
    payload = json.loads(os.environ.get("DEMO_FIXTURE", "{}"))
    print(json.dumps(payload, default=str, indent=2))
