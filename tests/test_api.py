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


def test_upload_rejects_non_pdf(client: TestClient):
    response = client.post(
        "/api/documents",
        files=[("files", ("not.pdf", b"hello world", "text/plain"))],
    )
    assert response.status_code == 415


def test_upload_duplicate_does_not_create_second_document(client: TestClient):
    pdf_bytes = _make_pdf_bytes()
    files = [("files", ("dup.pdf", pdf_bytes, "application/pdf"))]
    first = client.post("/api/documents", files=files)
    second = client.post("/api/documents", files=files)
    assert first.status_code == 202
    assert second.status_code == 202
    document_ids = {item["document_id"] for item in first.json()}
    assert document_ids == {item["document_id"] for item in second.json()}


def test_index_html_is_served(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "FactLens" in response.text
