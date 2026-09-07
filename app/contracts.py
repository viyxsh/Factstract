from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from decimal import Decimal
from typing import Any, Literal

RelationshipKind = Literal["corroborates", "contradicts", "reconciles"]
JobStatus = Literal["queued", "running", "done", "failed"]


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


@dataclass(slots=True)
class DocumentRecord:
    id: int | None = None
    filename: str = ""
    sha256: str = ""
    page_count: int | None = None
    status: str = "queued"
    created_at: str | None = None
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(slots=True)
class JobRecord:
    id: int | None = None
    document_id: int = 0
    status: JobStatus = "queued"
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(slots=True)
class FactRecord:
    id: int | None = None
    document_id: int = 0
    page_number: int = 0
    subject: str = ""
    metric: str = ""
    value_display: str = ""
    numeric_value: Decimal | None = None
    canonical_value_inr: Decimal | None = None
    unit: str | None = None
    period: str | None = None
    scope: str | None = None
    quote: str = ""
    confidence: float = 0.0
    grounding_status: str = "pending"
    subject_key: str | None = None
    metric_key: str | None = None
    period_key: str | None = None
    scope_key: str | None = None
    canonical_unit: str | None = None
    comparison_key: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(slots=True)
class RelationshipRecord:
    id: int | None = None
    left_fact_id: int = 0
    right_fact_id: int = 0
    kind: RelationshipKind = "corroborates"
    title: str = ""
    rationale: str = ""
    created_at: str | None = None
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(slots=True)
class FailureRecord:
    id: int | None = None
    document_id: int = 0
    page_number: int = 0
    stage: str = ""
    reason: str = ""
    source_excerpt: str = ""
    suggested_improvement: str = ""
    created_at: str | None = None
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return _serialize(self)


__all__ = [
    "DocumentRecord",
    "FailureRecord",
    "FactRecord",
    "JobRecord",
    "JobStatus",
    "RelationshipKind",
    "RelationshipRecord",
]
