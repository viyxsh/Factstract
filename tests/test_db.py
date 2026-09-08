import json
import tempfile
from pathlib import Path

import pytest

from app.contracts import DocumentRecord, FactRecord, FailureRecord, JobRecord, RelationshipRecord
from app.db import Repository


@pytest.fixture()
def repository() -> Repository:
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Repository(str(Path(tmpdir) / "state.sqlite3"))
        repo.init()
        yield repo


def test_document_round_trip(repository: Repository):
    record = DocumentRecord(filename="demo.pdf", sha256="abc123", page_count=42, status="done")
    stored = repository.create_document(record)
    assert stored.id is not None
    fetched = repository.get_document(int(stored.id))
    assert fetched is not None
    assert fetched.filename == "demo.pdf"
    assert fetched.page_count == 42


def test_duplicate_document_returns_existing(repository: Repository):
    record = DocumentRecord(filename="dup.pdf", sha256="same-hash", page_count=10, status="queued")
    first = repository.create_document(record)
    second = repository.create_document(record)
    assert first.id == second.id


def test_job_progress_update(repository: Repository):
    document = repository.create_document(DocumentRecord(filename="x.pdf", sha256="h1", page_count=2, status="queued"))
    job = repository.create_job(JobRecord(document_id=int(document.id or 0), status="queued"))
    updated = repository.update_job(int(job.id or 0), status="running", progress=0.5, message="halfway")
    assert updated is not None
    assert updated.status == "running"
    assert updated.progress == 0.5


def test_fact_decimal_round_trip(repository: Repository):
    document = repository.create_document(DocumentRecord(filename="x.pdf", sha256="h2", page_count=1, status="done"))
    fact = FactRecord(
        document_id=int(document.id or 0),
        page_number=1,
        subject="X",
        metric="y",
        value_display="7,225 INR Cr",
        quote="quoted text",
        grounding_status="grounded",
    )
    stored = repository.add_fact(fact)
    fetched = repository.get_fact(int(stored.id or 0))
    assert fetched is not None
    assert fetched.subject == "X"


def test_relationship_and_failure_crud(repository: Repository):
    document = repository.create_document(DocumentRecord(filename="x.pdf", sha256="h3", page_count=1, status="done"))
    left = repository.add_fact(
        FactRecord(document_id=int(document.id or 0), page_number=1, subject="A", metric="m", value_display="1", quote="q1")
    )
    right = repository.add_fact(
        FactRecord(document_id=int(document.id or 0), page_number=2, subject="A", metric="m", value_display="1", quote="q2")
    )
    rel = repository.add_relationship(
        RelationshipRecord(left_fact_id=int(left.id or 0), right_fact_id=int(right.id or 0), kind="corroborates", title="match", rationale="same")
    )
    failure = repository.add_failure(
        FailureRecord(document_id=int(document.id or 0), page_number=3, stage="grounding", reason="missing quote", source_excerpt="x", suggested_improvement="y")
    )
    assert repository.list_relationships()[0].id == rel.id
    assert repository.list_failures()[0].id == failure.id


def test_delete_all_demo_records_clears_tables(repository: Repository):
    document = repository.create_document(DocumentRecord(filename="y.pdf", sha256="h4", page_count=1, status="done"))
    repository.add_fact(
        FactRecord(document_id=int(document.id or 0), page_number=1, subject="X", metric="m", value_display="1", quote="q")
    )
    repository.add_failure(
        FailureRecord(document_id=int(document.id or 0), page_number=1, stage="x", reason="y", source_excerpt="z", suggested_improvement="w")
    )
    repository.delete_all_demo_records()
    assert repository.list_documents() == []
    assert repository.list_facts() == []
    assert repository.list_failures() == []


def test_repository_init_is_idempotent(tmp_path: Path):
    repo = Repository(str(tmp_path / "double.sqlite3"))
    repo.init()
    repo.init()


def test_active_job_count_and_stuck_cleanup(repository: Repository):
    document = repository.create_document(DocumentRecord(filename="x.pdf", sha256="h5", page_count=1, status="queued"))
    assert repository.count_active_jobs() == 0
    running = repository.create_job(JobRecord(document_id=int(document.id or 0), status="running"))
    repository.create_job(JobRecord(document_id=int(document.id or 0), status="done", progress=1.0))
    assert repository.count_active_jobs() == 1
    marked = repository.mark_stuck_jobs_failed("restarted")
    assert marked == 1
    assert repository.count_active_jobs() == 0
    assert repository.get_job(int(running.id or 0)).status == "failed"
