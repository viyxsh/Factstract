from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from app.contracts import (
    DocumentRecord,
    FailureRecord,
    FactRecord,
    JobRecord,
    RelationshipRecord,
)

_DOCUMENT_FIELDS = {field.name for field in fields(DocumentRecord)}
_JOB_FIELDS = {field.name for field in fields(JobRecord)}
_FACT_FIELDS = {field.name for field in fields(FactRecord)}
_RELATIONSHIP_FIELDS = {field.name for field in fields(RelationshipRecord)}
_FAILURE_FIELDS = {field.name for field in fields(FailureRecord)}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _decimal_to_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _decimal_from_text(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _document_from_row(row: sqlite3.Row) -> DocumentRecord:
    payload = _row_to_dict(row) or {}
    return DocumentRecord(
        id=payload.get("id"),
        filename=payload.get("filename", ""),
        sha256=payload.get("sha256", ""),
        page_count=payload.get("page_count"),
        status=payload.get("status", ""),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
    )


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    payload = _row_to_dict(row) or {}
    return JobRecord(
        id=payload.get("id"),
        document_id=payload.get("document_id", 0),
        status=payload.get("status", "queued"),
        progress=float(payload.get("progress", 0.0) or 0.0),
        message=payload.get("message", ""),
        error=payload.get("error"),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
    )


def _fact_from_row(row: sqlite3.Row) -> FactRecord:
    payload = _row_to_dict(row) or {}
    return FactRecord(
        id=payload.get("id"),
        document_id=payload.get("document_id", 0),
        page_number=payload.get("page_number", 0),
        subject=payload.get("subject", ""),
        metric=payload.get("metric", ""),
        value_display=payload.get("value_display", ""),
        numeric_value=_decimal_from_text(payload.get("numeric_value")),
        canonical_value_inr=_decimal_from_text(payload.get("canonical_value_inr")),
        unit=payload.get("unit"),
        period=payload.get("period"),
        scope=payload.get("scope"),
        quote=payload.get("quote", ""),
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        grounding_status=payload.get("grounding_status", "pending"),
        subject_key=payload.get("subject_key"),
        metric_key=payload.get("metric_key"),
        period_key=payload.get("period_key"),
        scope_key=payload.get("scope_key"),
        canonical_unit=payload.get("canonical_unit"),
        comparison_key=payload.get("comparison_key"),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
    )


def _relationship_from_row(row: sqlite3.Row) -> RelationshipRecord:
    payload = _row_to_dict(row) or {}
    return RelationshipRecord(
        id=payload.get("id"),
        left_fact_id=payload.get("left_fact_id", 0),
        right_fact_id=payload.get("right_fact_id", 0),
        kind=payload.get("kind", "corroborates"),
        title=payload.get("title", ""),
        rationale=payload.get("rationale", ""),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
    )


def _failure_from_row(row: sqlite3.Row) -> FailureRecord:
    payload = _row_to_dict(row) or {}
    return FailureRecord(
        id=payload.get("id"),
        document_id=payload.get("document_id", 0),
        page_number=payload.get("page_number", 0),
        stage=payload.get("stage", ""),
        reason=payload.get("reason", ""),
        source_excerpt=payload.get("source_excerpt", ""),
        suggested_improvement=payload.get("suggested_improvement", ""),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
    )


def _fetch_document(connection: sqlite3.Connection, document_id: int) -> DocumentRecord | None:
    row = connection.execute(
        "SELECT id, filename, sha256, page_count, status, created_at, updated_at FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    return _document_from_row(row) if row is not None else None


def _fetch_job(connection: sqlite3.Connection, job_id: int) -> JobRecord | None:
    row = connection.execute(
        "SELECT id, document_id, status, progress, message, error, created_at, updated_at FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    return _job_from_row(row) if row is not None else None


def _fetch_fact(connection: sqlite3.Connection, fact_id: int) -> FactRecord | None:
    row = connection.execute(
        """
        SELECT id, document_id, page_number, subject, metric, value_display, numeric_value,
               canonical_value_inr, unit, period, scope, quote, confidence, grounding_status,
               subject_key, metric_key, period_key, scope_key, canonical_unit, comparison_key,
               created_at, updated_at
        FROM facts
        WHERE id = ?
        """,
        (fact_id,),
    ).fetchone()
    return _fact_from_row(row) if row is not None else None


def _fetch_relationship(connection: sqlite3.Connection, relationship_id: int) -> RelationshipRecord | None:
    row = connection.execute(
        """
        SELECT id, left_fact_id, right_fact_id, kind, title, rationale, created_at, updated_at
        FROM relationships
        WHERE id = ?
        """,
        (relationship_id,),
    ).fetchone()
    return _relationship_from_row(row) if row is not None else None


def _fetch_failure(connection: sqlite3.Connection, failure_id: int) -> FailureRecord | None:
    row = connection.execute(
        """
        SELECT id, document_id, page_number, stage, reason, source_excerpt, suggested_improvement,
               created_at, updated_at
        FROM failures
        WHERE id = ?
        """,
        (failure_id,),
    ).fetchone()
    return _failure_from_row(row) if row is not None else None


class Repository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            page_count INTEGER,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
            progress REAL NOT NULL DEFAULT 0.0 CHECK (progress >= 0.0 AND progress <= 100.0),
            message TEXT NOT NULL DEFAULT '',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            subject TEXT NOT NULL,
            metric TEXT NOT NULL,
            value_display TEXT NOT NULL,
            numeric_value TEXT,
            canonical_value_inr TEXT,
            unit TEXT,
            period TEXT,
            scope TEXT,
            quote TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            grounding_status TEXT NOT NULL DEFAULT 'pending',
            subject_key TEXT,
            metric_key TEXT,
            period_key TEXT,
            scope_key TEXT,
            canonical_unit TEXT,
            comparison_key TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS relationships (
            id INTEGER PRIMARY KEY,
            left_fact_id INTEGER NOT NULL,
            right_fact_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('corroborates', 'contradicts', 'reconciles')),
            title TEXT NOT NULL,
            rationale TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (left_fact_id) REFERENCES facts(id) ON DELETE CASCADE,
            FOREIGN KEY (right_fact_id) REFERENCES facts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS failures (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            stage TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_excerpt TEXT NOT NULL,
            suggested_improvement TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);
        CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at, id);
        CREATE INDEX IF NOT EXISTS idx_jobs_document_id ON jobs(document_id, id);
        CREATE INDEX IF NOT EXISTS idx_facts_document_page ON facts(document_id, page_number, id);
        CREATE INDEX IF NOT EXISTS idx_facts_comparison_key ON facts(comparison_key, id);
        CREATE INDEX IF NOT EXISTS idx_relationships_facts ON relationships(left_fact_id, right_fact_id, id);
        CREATE INDEX IF NOT EXISTS idx_failures_document_page ON failures(document_id, page_number, id);
        """
        with self._connect() as connection:
            connection.executescript(schema)

    def create_document(self, document: DocumentRecord) -> DocumentRecord:
        existing = self.get_document_by_sha256(document.sha256)
        if existing is not None:
            return existing

        now = _utc_now()
        payload = document.as_dict()
        payload.update(
            {
                "created_at": document.created_at or now,
                "updated_at": document.updated_at or now,
            }
        )
        payload.pop("id", None)
        columns = [name for name in payload if name in _DOCUMENT_FIELDS and name != "id"]
        values = [payload[name] for name in columns]
        sql = f"INSERT INTO documents ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
        with self._connect() as connection:
            cursor = connection.execute(sql, values)
            document = _fetch_document(connection, int(cursor.lastrowid))
        if document is None:
            raise RuntimeError("inserted document was not readable")
        return document

    def get_document(self, document_id: int) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, filename, sha256, page_count, status, created_at, updated_at FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        return _document_from_row(row) if row is not None else None

    def get_document_by_sha256(self, sha256: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, filename, sha256, page_count, status, created_at, updated_at FROM documents WHERE sha256 = ?",
                (sha256,),
            ).fetchone()
        return _document_from_row(row) if row is not None else None

    def update_document(self, document_id: int, **fields: Any) -> DocumentRecord | None:
        allowed = {"filename", "sha256", "page_count", "status"}
        update_fields = {name: value for name, value in fields.items() if name in allowed}
        if not update_fields:
            update_fields = {}
        update_fields["updated_at"] = fields.get("updated_at") or _utc_now()
        columns = ", ".join(f"{name} = ?" for name in update_fields)
        values = list(update_fields.values()) + [document_id]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE documents SET {columns} WHERE id = ?",
                values,
            )
        return self.get_document(document_id)

    def list_documents(self) -> list[DocumentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, filename, sha256, page_count, status, created_at, updated_at FROM documents ORDER BY created_at, id"
            ).fetchall()
        return [_document_from_row(row) for row in rows]

    def create_job(self, job: JobRecord) -> JobRecord:
        now = _utc_now()
        payload = job.as_dict()
        payload.update(
            {
                "created_at": job.created_at or now,
                "updated_at": job.updated_at or now,
                "progress": float(job.progress),
            }
        )
        payload.pop("id", None)
        columns = [name for name in payload if name in _JOB_FIELDS and name != "id"]
        values = [payload[name] for name in columns]
        sql = f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
        with self._connect() as connection:
            cursor = connection.execute(sql, values)
            job = _fetch_job(connection, int(cursor.lastrowid))
        if job is None:
            raise RuntimeError("inserted job was not readable")
        return job

    def update_job(self, job_id: int, **fields: Any) -> JobRecord | None:
        allowed = {"status", "progress", "message", "error"}
        update_fields = {name: value for name, value in fields.items() if name in allowed}
        update_fields["updated_at"] = fields.get("updated_at") or _utc_now()
        if "progress" in update_fields:
            update_fields["progress"] = float(update_fields["progress"])
        columns = ", ".join(f"{name} = ?" for name in update_fields)
        values = list(update_fields.values()) + [job_id]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {columns} WHERE id = ?",
                values,
            )
        return self.get_job(job_id)

    def get_job(self, job_id: int) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, document_id, status, progress, message, error, created_at, updated_at FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def add_fact(self, fact: FactRecord) -> FactRecord:
        now = _utc_now()
        payload = fact.as_dict()
        payload.update(
            {
                "created_at": fact.created_at or now,
                "updated_at": fact.updated_at or now,
                "numeric_value": _decimal_to_text(fact.numeric_value),
                "canonical_value_inr": _decimal_to_text(fact.canonical_value_inr),
            }
        )
        payload.pop("id", None)
        columns = [name for name in payload if name in _FACT_FIELDS and name != "id"]
        values = [payload[name] for name in columns]
        sql = f"INSERT INTO facts ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
        with self._connect() as connection:
            cursor = connection.execute(sql, values)
            fact = _fetch_fact(connection, int(cursor.lastrowid))
        if fact is None:
            raise RuntimeError("inserted fact was not readable")
        return fact

    def get_fact(self, fact_id: int) -> FactRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, document_id, page_number, subject, metric, value_display, numeric_value,
                       canonical_value_inr, unit, period, scope, quote, confidence, grounding_status,
                       subject_key, metric_key, period_key, scope_key, canonical_unit, comparison_key,
                       created_at, updated_at
                FROM facts
                WHERE id = ?
                """,
                (fact_id,),
            ).fetchone()
        return _fact_from_row(row) if row is not None else None

    def list_facts(self, document_id: int | None = None) -> list[FactRecord]:
        with self._connect() as connection:
            if document_id is None:
                rows = connection.execute(
                    """
                    SELECT id, document_id, page_number, subject, metric, value_display, numeric_value,
                           canonical_value_inr, unit, period, scope, quote, confidence, grounding_status,
                           subject_key, metric_key, period_key, scope_key, canonical_unit, comparison_key,
                           created_at, updated_at
                    FROM facts
                    ORDER BY document_id, page_number, id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, document_id, page_number, subject, metric, value_display, numeric_value,
                           canonical_value_inr, unit, period, scope, quote, confidence, grounding_status,
                           subject_key, metric_key, period_key, scope_key, canonical_unit, comparison_key,
                           created_at, updated_at
                    FROM facts
                    WHERE document_id = ?
                    ORDER BY page_number, id
                    """,
                    (document_id,),
                ).fetchall()
        return [_fact_from_row(row) for row in rows]

    def add_relationship(self, relationship: RelationshipRecord) -> RelationshipRecord:
        now = _utc_now()
        payload = relationship.as_dict()
        payload.update(
            {
                "created_at": relationship.created_at or now,
                "updated_at": relationship.updated_at or now,
            }
        )
        payload.pop("id", None)
        columns = [name for name in payload if name in _RELATIONSHIP_FIELDS and name != "id"]
        values = [payload[name] for name in columns]
        sql = f"INSERT INTO relationships ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
        with self._connect() as connection:
            cursor = connection.execute(sql, values)
            relationship = _fetch_relationship(connection, int(cursor.lastrowid))
        if relationship is None:
            raise RuntimeError("inserted relationship was not readable")
        return relationship

    def get_relationship(self, relationship_id: int) -> RelationshipRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, left_fact_id, right_fact_id, kind, title, rationale, created_at, updated_at
                FROM relationships
                WHERE id = ?
                """,
                (relationship_id,),
            ).fetchone()
        return _relationship_from_row(row) if row is not None else None

    def list_relationships(self, document_id: int | None = None) -> list[RelationshipRecord]:
        with self._connect() as connection:
            if document_id is None:
                rows = connection.execute(
                    """
                    SELECT id, left_fact_id, right_fact_id, kind, title, rationale, created_at, updated_at
                    FROM relationships
                    ORDER BY created_at, id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT r.id, r.left_fact_id, r.right_fact_id, r.kind, r.title, r.rationale, r.created_at, r.updated_at
                    FROM relationships AS r
                    JOIN facts AS lf ON lf.id = r.left_fact_id
                    JOIN facts AS rf ON rf.id = r.right_fact_id
                    WHERE lf.document_id = ? OR rf.document_id = ?
                    ORDER BY r.created_at, r.id
                    """,
                    (document_id, document_id),
                ).fetchall()
        return [_relationship_from_row(row) for row in rows]

    def add_failure(self, failure: FailureRecord) -> FailureRecord:
        now = _utc_now()
        payload = failure.as_dict()
        payload.update(
            {
                "created_at": failure.created_at or now,
                "updated_at": failure.updated_at or now,
            }
        )
        payload.pop("id", None)
        columns = [name for name in payload if name in _FAILURE_FIELDS and name != "id"]
        values = [payload[name] for name in columns]
        sql = f"INSERT INTO failures ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
        with self._connect() as connection:
            cursor = connection.execute(sql, values)
            failure = _fetch_failure(connection, int(cursor.lastrowid))
        if failure is None:
            raise RuntimeError("inserted failure was not readable")
        return failure

    def get_failure(self, failure_id: int) -> FailureRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, document_id, page_number, stage, reason, source_excerpt, suggested_improvement,
                       created_at, updated_at
                FROM failures
                WHERE id = ?
                """,
                (failure_id,),
            ).fetchone()
        return _failure_from_row(row) if row is not None else None

    def list_failures(self, document_id: int | None = None) -> list[FailureRecord]:
        with self._connect() as connection:
            if document_id is None:
                rows = connection.execute(
                    """
                    SELECT id, document_id, page_number, stage, reason, source_excerpt, suggested_improvement,
                           created_at, updated_at
                    FROM failures
                    ORDER BY document_id, page_number, id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, document_id, page_number, stage, reason, source_excerpt, suggested_improvement,
                           created_at, updated_at
                    FROM failures
                    WHERE document_id = ?
                    ORDER BY page_number, id
                    """,
                    (document_id,),
                ).fetchall()
        return [_failure_from_row(row) for row in rows]

    def delete_all_demo_records(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM relationships")
            connection.execute("DELETE FROM facts")
            connection.execute("DELETE FROM failures")
            connection.execute("DELETE FROM jobs")
            connection.execute("DELETE FROM documents")


__all__ = ["Repository"]
