import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts import DocumentRecord, FactRecord
from app.db import Repository
from app.pipeline import PipelineResult


@pytest.fixture()
def main_module(tmp_path: Path, monkeypatch):
    import app.main as app_main

    repo = Repository(str(tmp_path / "main.sqlite3"))
    repo.init()
    monkeypatch.setattr(app_main, "_REPOSITORY", repo)
    return app_main, repo


def _fact(doc_id: int, **overrides) -> FactRecord:
    base: dict = {
        "document_id": doc_id,
        "page_number": 1,
        "subject": "India CPI",
        "metric": "inflation",
        "value_display": "4.6 percent",
        "numeric_value": Decimal("4.6"),
        "canonical_value_inr": Decimal("4.6"),
        "unit": "percent",
        "period": "FY25",
        "scope": "India",
        "quote": "CPI was 4.6 percent.",
        "grounding_status": "grounded",
        "subject_key": "india-cpi",
        "metric_key": "inflation",
        "period_key": "fy25",
        "scope_key": "india",
        "canonical_unit": "percent",
        "comparison_key": "india-cpi|inflation",
    }
    base.update(overrides)
    return FactRecord(**base)


def test_store_remaps_ids_and_replaces_on_rerun(main_module):
    app_main, repo = main_module
    other = repo.create_document(DocumentRecord(filename="o.pdf", sha256="o", page_count=1, status="done"))
    known = repo.add_fact(_fact(int(other.id or 0)))
    doc = repo.create_document(DocumentRecord(filename="n.pdf", sha256="n", page_count=1, status="queued"))
    new_fact = _fact(
        int(doc.id or 0),
        value_display="5.1 percent",
        numeric_value=Decimal("5.1"),
        canonical_value_inr=Decimal("5.1"),
        quote="CPI was 5.1 percent.",
    )
    assert new_fact.id is None
    result = PipelineResult(document=doc, facts=[new_fact], failures=[], relationships=[])
    counts = app_main._store_pipeline_result(int(doc.id or 0), [known], result)
    assert counts == {"facts": 1, "failures": 0, "relationships": 1}
    rels = repo.list_relationships()
    assert len(rels) == 1
    fact_ids = {fact.id for fact in repo.list_facts()}
    assert rels[0].left_fact_id in fact_ids
    assert rels[0].right_fact_id in fact_ids
    assert 0 not in (rels[0].left_fact_id, rels[0].right_fact_id)
    # Storing the same run again replaces instead of duplicating.
    counts_again = app_main._store_pipeline_result(int(doc.id or 0), [known], result)
    assert counts_again == counts
    assert len(repo.list_facts(document_id=int(doc.id or 0))) == 1
    assert len(repo.list_relationships()) == 1
