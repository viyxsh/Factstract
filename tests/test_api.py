import io
import tempfile
from pathlib import Path

import fitz  # type: ignore[import-not-found]
import pytest
from fastapi.testclient import TestClient

from app.db import Repository


@pytest.fixture()
def client(monkeypatch, tmp_path: Path) -> TestClient:
    db_path = tmp_path / "api.sqlite3"
    repo = Repository(str(db_path))
    repo.init()
    monkeypatch.setenv("FACT_KNOWLEDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FACT_KNOWLEDGE_DB_PATH", str(db_path))
    import importlib

    from app import main as app_main

    importlib.reload(app_main)
    monkeypatch.setattr(app_main, "_REPOSITORY", repo)
    monkeypatch.setattr(app_main, "_RUNNER", app_main.PipelineRunner())
    # Upload tests assert request behavior only; never spawn real threads.
    monkeypatch.setattr(app_main, "_start_background_job", lambda *args: None)
    return TestClient(app_main.app)


def _make_pdf_bytes() -> bytes:
    buffer = io.BytesIO()
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Nominal GDP for FY25 was estimated at 7,225 INR Cr.")
    pdf.save(buffer)
    pdf.close()
    return buffer.getvalue()


def test_health_returns_service_state(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "llm_configured" in payload


def test_demo_load_returns_four_showcase_records(client: TestClient):
    response = client.post("/api/demo/load")
    assert response.status_code == 200
    counts = response.json()["counts"]
    assert counts["facts"] == 6
    assert counts["relationships"] == 3
    assert counts["failures"] == 1
    kinds = {rel["kind"] for rel in client.get("/api/relationships").json()}
    assert kinds == {"corroborates", "contradicts", "reconciles"}
    assert len(client.get("/api/failures").json()) == 1


def test_summary_endpoint_counts_records(client: TestClient):
    client.post("/api/demo/load")
    response = client.get("/api/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["facts"] == 6
    assert payload["documents"] == 3
    assert payload["relationships"] == 3


def test_evidence_endpoint_returns_verbatim_quote(client: TestClient):
    client.post("/api/demo/load")
    facts = client.get("/api/facts").json()
    fact_id = facts[0]["id"]
    response = client.get(f"/api/evidence/{fact_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["quote"]
    assert payload["page_number"]


def test_upload_rejects_oversize_pdf(client: TestClient, monkeypatch):
    import app.main as app_main

    monkeypatch.setattr(app_main, "MAX_UPLOAD_BYTES", 10)
    response = client.post(
        "/api/documents",
        files=[("files", ("big.pdf", b"%PDF-1.4 too big", "application/pdf"))],
    )
    assert response.status_code == 413


def test_upload_rejects_non_pdf(client: TestClient):
    response = client.post(
        "/api/documents",
        files=[("files", ("not.pdf", b"hello world", "text/plain"))],
    )
    assert response.status_code == 415


def test_upload_duplicate_does_not_create_second_document(client: TestClient):
    import app.main as app_main

    pdf_bytes = _make_pdf_bytes()
    files = [("files", ("dup.pdf", pdf_bytes, "application/pdf"))]
    first = client.post("/api/documents", files=files)
    assert first.status_code == 202
    assert len(first.json()) == 1
    document_id = first.json()[0]["document_id"]
    app_main._REPOSITORY.update_document(document_id, status="done")
    second = client.post("/api/documents", files=files)
    assert second.status_code == 202
    assert second.json() == []
    assert len(app_main._REPOSITORY.list_documents()) == 1


def _make_pdf_bytes_with(text: str) -> bytes:
    import io as _io

    import fitz as _fitz

    buffer = _io.BytesIO()
    pdf = _fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    pdf.save(buffer)
    pdf.close()
    return buffer.getvalue()


def test_simultaneous_uploads_queue_in_order(client: TestClient, monkeypatch):
    import app.main as app_main

    puts: list = []

    def record_start(job_id: int, document_id: int, pdf_path) -> None:
        puts.append((job_id, document_id, pdf_path))

    monkeypatch.setattr(app_main, "_start_background_job", record_start)
    response = client.post(
        "/api/documents",
        files=[
            ("files", ("a.pdf", _make_pdf_bytes_with("Alpha revenue was 100 INR Cr."), "application/pdf")),
            ("files", ("b.pdf", _make_pdf_bytes_with("Beta revenue was 200 INR Cr."), "application/pdf")),
        ],
    )
    assert response.status_code == 202
    jobs = response.json()
    assert len(jobs) == 2
    assert all(job["status"] == "queued" for job in jobs)
    assert [item[0] for item in puts] == [jobs[0]["id"], jobs[1]["id"]]
    assert puts[0][1] != puts[1][1]


def test_upload_retries_after_failed_run(client: TestClient):
    import app.main as app_main

    pdf_bytes = _make_pdf_bytes()
    files = [("files", ("retry.pdf", pdf_bytes, "application/pdf"))]
    first = client.post("/api/documents", files=files)
    assert first.status_code == 202
    document_id = first.json()[0]["document_id"]
    app_main._REPOSITORY.update_document(document_id, status="failed")
    second = client.post("/api/documents", files=files)
    assert second.status_code == 202
    assert len(second.json()) == 1


def test_reset_clears_all_state(client: TestClient):
    client.post("/api/demo/load")
    assert client.get("/api/summary").json()["facts"] == 6
    response = client.post("/api/reset")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert client.get("/api/documents").json() == []
    assert client.get("/api/facts").json() == []
    assert client.get("/api/relationships").json() == []
    assert client.get("/api/failures").json() == []
    assert client.get("/api/summary").json() == {"facts": 0, "documents": 0, "relationships": 0}


def test_reset_refuses_while_job_running(client: TestClient):
    from app import main as app_main
    from app.contracts import DocumentRecord, JobRecord

    client.post("/api/demo/load")
    doc = app_main._REPOSITORY.list_documents()[0]
    app_main._REPOSITORY.create_job(JobRecord(document_id=int(doc.id or 0), status="running"))
    response = client.post("/api/reset")
    assert response.status_code == 409
    assert client.get("/api/summary").json()["facts"] == 6


def test_index_html_is_served(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "Factstract" in response.text
