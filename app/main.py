"""FastAPI service: HTTP API + dark web UI + background pipeline runner."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import demo as demo_module
from app.contracts import (
    DocumentRecord,
    FailureRecord,
    JobRecord,
    JobStatus,
    RelationshipKind,
    RelationshipRecord,
)
from app.db import Repository
from app.openrouter import OpenRouterClient
from app.pipeline import PipelineRunner


LOGGER = logging.getLogger("app.main")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FACT_KNOWLEDGE_DATA_DIR", PROJECT_ROOT / ".data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.environ.get("FACT_KNOWLEDGE_DB_PATH", DATA_DIR / "app.sqlite3"))
STATIC_DIR = PROJECT_ROOT / "app" / "static"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

_REPOSITORY = Repository(str(DB_PATH))
_REPOSITORY.init()
_OPENROUTER = OpenRouterClient()
_RUNNER = PipelineRunner(client=_OPENROUTER)

_JOB_LOCK = threading.Lock()
_BACKGROUND_JOBS: dict[int, threading.Thread] = {}


app = FastAPI(title="Fact knowledge layer", version="0.1.0")
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
        "llm_configured": _OPENROUTER.configured,
        "model": _OPENROUTER.model,
        "base_url": _OPENROUTER.base_url,
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
        sha = hashlib.sha256(contents).hexdigest()
        existing = _REPOSITORY.get_document_by_sha256(sha)
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


def _start_background_job(job_id: int, document_id: int, pdf_path: Path) -> None:
    def run() -> None:
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
            for fact in result.facts:
                _REPOSITORY.add_fact(fact)
            for failure in result.failures:
                _REPOSITORY.add_failure(failure)
            for relationship in result.relationships:
                if not _relationship_already_recorded(relationship):
                    _REPOSITORY.add_relationship(relationship)
            _REPOSITORY.update_document(int(document.id or 0), status="done")
            _REPOSITORY.update_job(
                job_id,
                status="done",
                progress=1.0,
                message=f"Stored {len(result.facts)} facts and {len(result.failures)} failures.",
            )
        except Exception as error:  # pragma: no cover - defensive
            LOGGER.exception("Pipeline job failed")
            _REPOSITORY.update_job(job_id, status="failed", error=str(error))

    with _JOB_LOCK:
        thread = threading.Thread(target=run, name=f"job-{job_id}", daemon=True)
        _BACKGROUND_JOBS[job_id] = thread
        thread.start()


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
