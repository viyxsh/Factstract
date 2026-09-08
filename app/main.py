"""FastAPI service: HTTP API + dark web UI + background pipeline runner."""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app import demo as demo_module
from app.contracts import (
    DocumentRecord,
    FactRecord,
    FailureRecord,
    JobRecord,
    JobStatus,
    RelationshipKind,
    RelationshipRecord,
)
from app.db import Repository
from app.gemini import GeminiClient
from app.pipeline import PipelineResult, PipelineRunner, compare_facts


LOGGER = logging.getLogger("app.main")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FACT_KNOWLEDGE_DATA_DIR", PROJECT_ROOT / ".data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.environ.get("FACT_KNOWLEDGE_DB_PATH", DATA_DIR / "app.sqlite3"))
STATIC_DIR = PROJECT_ROOT / "app" / "static"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Gemini Files API accepts at most 50 MB per PDF; larger files are rejected
# before they cost an upload. Keep in sync with the hint in index.html.
MAX_UPLOAD_BYTES = int(os.environ.get("FACT_KNOWLEDGE_MAX_UPLOAD_BYTES", 50 * 1024 * 1024))

_REPOSITORY = Repository(str(DB_PATH))
_REPOSITORY.init()
_GEMINI = GeminiClient(repository=_REPOSITORY)
_RUNNER = PipelineRunner(client=_GEMINI)

if _GEMINI.configured:
    # Live mode starts clean: drop any demo-seeded documents/results from a
    # previous session so the user sees only their own uploads unless they
    # explicitly click "Load demo". User-uploaded documents are kept.
    try:
        _removed_demo_docs = _REPOSITORY.delete_demo_seed()
        if _removed_demo_docs:
            LOGGER.info("Cleared %d demo-seeded document(s) on startup (live mode).", _removed_demo_docs)
    except Exception:
        LOGGER.exception("Demo-seed cleanup on startup failed")

try:
    _stuck = _REPOSITORY.mark_stuck_jobs_failed(
        "Service restarted during processing; please re-upload the document."
    )
    if _stuck:
        LOGGER.info("Marked %d stuck job(s) as failed on startup.", _stuck)
except Exception:
    LOGGER.exception("Stuck-job cleanup on startup failed")

_JOB_LOCK = threading.Lock()
# Single FIFO worker: documents process one at a time, in upload order, so
# each job sees every previously stored fact (cross-document comparison),
# API pressure stays at one document's windows, and SQLite writers never
# contend with each other.
_JOB_QUEUE: queue.Queue[tuple[int, int, Path]] = queue.Queue()
_WORKER_STARTED = False


app = FastAPI(title="Factstract", version="0.1.0")
app.mount("/app/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _to_response(record: Any) -> dict[str, Any]:
    if hasattr(record, "as_dict"):
        return record.as_dict()
    return dict(record)


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    index = STATIC_DIR / "index.html"
    return FileResponse(index)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "llm_configured": _GEMINI.configured,
        "provider": "gemini",
        "model": _GEMINI.model,
    }


@app.get("/api/summary")
def summary() -> dict[str, int]:
    facts = _REPOSITORY.list_facts()
    documents = _REPOSITORY.list_documents()
    relationships = _REPOSITORY.list_relationships()
    return {
        "facts": len(facts),
        "documents": len(documents),
        "relationships": len(relationships),
    }


@app.get("/api/documents")
def list_documents() -> list[dict[str, Any]]:
    return [_to_response(doc) for doc in _REPOSITORY.list_documents()]


@app.post("/api/documents", status_code=202)
async def upload_documents(files: list[UploadFile] = File(..., alias="files")) -> list[dict[str, Any]]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF is required.")
    jobs: list[dict[str, Any]] = []
    for upload in files:
        if upload.content_type not in {"application/pdf", "application/x-pdf"} and not (
            upload.filename and upload.filename.lower().endswith(".pdf")
        ):
            raise HTTPException(status_code=415, detail=f"{upload.filename}: only PDFs are accepted.")
        contents = await upload.read()
        if not contents.startswith(b"%PDF"):
            raise HTTPException(status_code=415, detail=f"{upload.filename}: not a valid PDF payload.")
        if len(contents) > MAX_UPLOAD_BYTES:
            size_mb = len(contents) / (1024 * 1024)
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename}: {size_mb:.1f} MB exceeds the 50 MB per-file limit.",
            )
        sha = hashlib.sha256(contents).hexdigest()
        existing = _REPOSITORY.get_document_by_sha256(sha)
        if existing is not None and existing.status == "done":
            # Idempotent re-upload: same bytes, already processed. No new
            # job, so facts and relationships are never duplicated. Docs
            # with a failed/pending run still reprocess as a retry.
            continue
        if existing is not None:
            document = existing
        else:
            page_count = _safe_page_count(contents)
            document = _REPOSITORY.create_document(
                DocumentRecord(
                    filename=upload.filename or "uploaded.pdf",
                    sha256=sha,
                    page_count=page_count,
                    status="queued",
                )
            )
        job = _REPOSITORY.create_job(JobRecord(document_id=int(document.id or 0), status="queued"))
        stored_path = UPLOAD_DIR / f"{document.id}-{sha[:12]}.pdf"
        stored_path.write_bytes(contents)
        jobs.append(_to_response(job))
        _start_background_job(int(job.id or 0), document.id, stored_path)
    return jobs


def _safe_page_count(contents: bytes) -> int | None:
    try:
        from app.pdf import extract_pages

        with (UPLOAD_DIR / "_probe.pdf").open("wb") as probe:
            probe.write(contents)
        pages = extract_pages(UPLOAD_DIR / "_probe.pdf")
        (UPLOAD_DIR / "_probe.pdf").unlink(missing_ok=True)
        return len(pages)
    except Exception:  # pragma: no cover - depends on malformed PDFs
        (UPLOAD_DIR / "_probe.pdf").unlink(missing_ok=True)
        return None


def _store_pipeline_result(
    document_id: int, known_facts: list[FactRecord], result: PipelineResult
) -> dict[str, int]:
    """Persist one run, replacing the document's previous derived rows.

    Relationships are recomputed from persisted rows so every fact
    reference is a real database ID — never 0 for an unstored fact.
    """
    _REPOSITORY.delete_document_data(document_id)
    stored_facts = [_REPOSITORY.add_fact(fact) for fact in result.facts]
    for failure in result.failures:
        _REPOSITORY.add_failure(failure)
    added_relationships = 0
    for relationship in compare_facts(list(known_facts) + stored_facts):
        if not _relationship_already_recorded(relationship):
            _REPOSITORY.add_relationship(relationship)
            added_relationships += 1
    return {
        "facts": len(stored_facts),
        "failures": len(result.failures),
        "relationships": added_relationships,
    }


def _run_job(job_id: int, document_id: int, pdf_path: Path) -> None:
    try:
        _REPOSITORY.update_job(job_id, status="running", progress=0.01, message="Starting pipeline.")
        document = _REPOSITORY.get_document(document_id)
        if document is None:
            _REPOSITORY.update_job(job_id, status="failed", error="Document disappeared.")
            return
        _REPOSITORY.update_document(int(document.id or 0), status="running")

        def progress(value: float, message: str) -> None:
            _REPOSITORY.update_job(job_id, status="running", progress=value, message=message)

        known_facts = [
            f for f in _REPOSITORY.list_facts() if int(f.document_id or 0) != int(document.id or 0)
        ]
        result = _RUNNER.run_for_document(
            document,
            pdf_path,
            progress=progress,
            known_facts=known_facts,
        )
        counts = _store_pipeline_result(int(document.id or 0), known_facts, result)
        _REPOSITORY.update_document(int(document.id or 0), status="done")
        _REPOSITORY.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=f"Stored {counts['facts']} facts and {counts['failures']} failures.",
        )
    except Exception as error:  # pragma: no cover - defensive
        LOGGER.exception("Pipeline job failed")
        try:
            _REPOSITORY.update_document(document_id, status="failed")
        except Exception:
            LOGGER.exception("Could not mark document as failed")
        _REPOSITORY.update_job(
            job_id, status="failed", error="Processing failed unexpectedly; please retry the upload."
        )


def _job_worker() -> None:
    while True:
        job_id, document_id, pdf_path = _JOB_QUEUE.get()
        try:
            _run_job(job_id, document_id, pdf_path)
        except Exception:  # pragma: no cover - one bad job must not kill the queue
            LOGGER.exception("Job worker crashed on job %s", job_id)
        finally:
            _JOB_QUEUE.task_done()


def _ensure_worker() -> None:
    global _WORKER_STARTED
    with _JOB_LOCK:
        if not _WORKER_STARTED:
            thread = threading.Thread(target=_job_worker, name="job-worker", daemon=True)
            thread.start()
            _WORKER_STARTED = True


def _start_background_job(job_id: int, document_id: int, pdf_path: Path) -> None:
    _ensure_worker()
    _JOB_QUEUE.put((job_id, document_id, pdf_path))


def _relationship_already_recorded(relationship: RelationshipRecord) -> bool:
    existing = _REPOSITORY.list_relationships()
    for record in existing:
        if (
            {record.left_fact_id, record.right_fact_id}
            == {relationship.left_fact_id, relationship.right_fact_id}
            and record.kind == relationship.kind
        ):
            return True
    return False


@app.get("/api/jobs/{job_id}")
def job_state(job_id: int) -> dict[str, Any]:
    record = _REPOSITORY.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _to_response(record)


@app.get("/api/facts")
def list_facts(document_id: int | None = None) -> list[dict[str, Any]]:
    return [_to_response(fact) for fact in _REPOSITORY.list_facts(document_id=document_id)]


@app.get("/api/relationships")
def list_relationships(document_id: int | None = None) -> list[dict[str, Any]]:
    return [_to_response(rel) for rel in _REPOSITORY.list_relationships(document_id=document_id)]


@app.get("/api/failures")
def list_failures(document_id: int | None = None) -> list[dict[str, Any]]:
    return [_to_response(failure) for failure in _REPOSITORY.list_failures(document_id=document_id)]


@app.get("/api/evidence/{fact_id}")
def evidence(fact_id: int) -> dict[str, Any]:
    fact = _REPOSITORY.get_fact(fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found.")
    document = _REPOSITORY.get_document(int(fact.document_id or 0))
    return {
        "fact": _to_response(fact),
        "document_filename": document.filename if document else None,
        "page_number": fact.page_number,
        "quote": fact.quote,
        "value_display": fact.value_display,
        "unit": fact.unit,
        "grounding_status": fact.grounding_status,
    }


@app.post("/api/reset")
def reset_all() -> dict[str, Any]:
    """Wipe all stored state (documents, facts, relationships, failures,
    jobs, cached file handles, uploaded PDFs) for a fresh dashboard.
    Refused with 409 while any job is queued/running so a reload never
    discards in-progress work."""
    if _REPOSITORY.count_active_jobs() > 0:
        raise HTTPException(
            status_code=409,
            detail="A document is still processing; reset is available once jobs finish.",
        )
    _REPOSITORY.clear_all()
    removed_files = 0
    for stored in UPLOAD_DIR.glob("*.pdf"):
        try:
            stored.unlink()
            removed_files += 1
        except OSError:
            LOGGER.warning("Could not remove stale upload %s", stored)
    return {"ok": True, "removed_files": removed_files}


@app.post("/api/demo/load")
def load_demo() -> dict[str, Any]:
    counts = demo_module.load_demo(_REPOSITORY)
    documents = _REPOSITORY.list_documents()
    jobs = []
    for document in documents:
        job = _REPOSITORY.create_job(
            JobRecord(document_id=int(document.id or 0), status="done", progress=1.0, message="Demo loader")
        )
        jobs.append(_to_response(job))
    return {"counts": counts, "jobs": jobs}


@app.exception_handler(HTTPException)
async def http_error_handler(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


__all__ = ["app"]
