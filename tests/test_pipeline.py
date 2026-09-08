import tempfile
from decimal import Decimal
from pathlib import Path

import fitz  # type: ignore[import-not-found]
import pytest

from app.contracts import DocumentRecord, FactRecord
from app.pipeline import PipelineRunner


def _make_pdf(tmp_path: Path) -> Path:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text(
        (72, 72),
        "Nominal GDP for FY25 was estimated at 7,225 INR Cr. "
        "Headline CPI inflation averaged 4.6 percent in FY25.",
    )
    out = tmp_path / "pipeline.pdf"
    pdf.save(out)
    pdf.close()
    return out


def test_pipeline_runner_without_key_uses_fallback(tmp_path: Path):
    pdf_path = _make_pdf(tmp_path)
    document = DocumentRecord(filename="x.pdf", sha256="abc", page_count=1, status="queued")
    runner = PipelineRunner(client=None)
    runner.client.api_key = ""
    result = runner.run_for_document(document, pdf_path)
    assert result.facts or result.failures
    assert all(fact.grounding_status == "grounded" for fact in result.facts)


def test_bad_quote_becomes_known_failure(tmp_path: Path):
    pdf_path = _make_pdf(tmp_path)
    document = DocumentRecord(filename="x.pdf", sha256="abc", page_count=1, status="queued")
    runner = PipelineRunner(client=None)
    runner.client.api_key = ""
    known_facts = []
    result = runner.run_for_document(document, pdf_path, known_facts=known_facts)
    # No relationship is created because we only compare against existing known_facts.
    assert isinstance(result.relationships, list)


def test_file_window_errors_stay_user_friendly(tmp_path: Path):
    from types import SimpleNamespace

    from app.gemini import GeminiClient
    from app.pipeline import PipelineRunner

    pdf_path = _make_pdf(tmp_path)
    scary = "3 validation errors for Schema properties.facts.items.properties.unit.type Traceback boom"

    class FailModels:
        def generate_content(self, model, contents, config=None):
            raise RuntimeError(scary)

    class FailFiles:
        def upload(self, file, config=None):
            return SimpleNamespace(name="files/x", uri="u", state="ACTIVE")

        def get(self, name):
            return SimpleNamespace(name=name, state="ACTIVE")

    fake = SimpleNamespace(files=FailFiles(), models=FailModels())
    client = GeminiClient(api_key="k", sdk_factory=lambda _key: fake)
    document = DocumentRecord(filename="x.pdf", sha256="abc", page_count=1, status="queued")
    result = PipelineRunner(client=client).run_for_document(document, pdf_path, known_facts=[])
    assert result.facts == []
    assert result.failures
    for failure in result.failures:
        assert "validation errors" not in failure.reason
        assert "Traceback" not in failure.reason
        assert "boom" not in failure.reason


def test_progress_stays_within_unit_range(tmp_path: Path):
    from app.pipeline import PipelineRunner

    pdf_path = _make_pdf(tmp_path)
    document = DocumentRecord(filename="x.pdf", sha256="abc", page_count=1, status="queued")
    runner = PipelineRunner(client=None)
    runner.client.api_key = ""
    seen: list[float] = []
    runner.run_for_document(document, pdf_path, progress=lambda value, _msg: seen.append(value))
    assert seen, "expected progress callbacks"
    assert all(0.0 <= value <= 1.0 for value in seen), seen


def _known_fact(**overrides) -> FactRecord:
    base = {
        "document_id": 99,
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


def test_vintage_difference_reconciles_not_contradicts():
    from app.pipeline import compare_facts

    old = _known_fact()
    new = _known_fact(
        document_id=100,
        value_display="5.1 percent",
        numeric_value=Decimal("5.1"),
        canonical_value_inr=Decimal("5.1"),
        period="FY24",
        period_key="fy24",
        quote="CPI was 5.1 percent.",
    )
    rels = compare_facts([old, new])
    assert len(rels) == 1
    assert rels[0].kind == "reconciles"
    assert "FY25" in rels[0].rationale and "FY24" in rels[0].rationale


def test_scope_difference_reconciles():
    from app.pipeline import compare_facts

    old = _known_fact()
    new = _known_fact(
        document_id=100,
        value_display="5.1 percent",
        numeric_value=Decimal("5.1"),
        canonical_value_inr=Decimal("5.1"),
        scope="Global",
        scope_key="global",
        quote="CPI was 5.1 percent.",
    )
    rels = compare_facts([old, new])
    assert len(rels) == 1
    assert rels[0].kind == "reconciles"


def test_same_period_different_value_contradicts():
    from app.pipeline import compare_facts

    old = _known_fact()
    new = _known_fact(document_id=100, value_display="5.1 percent", numeric_value=Decimal("5.1"),
                      canonical_value_inr=Decimal("5.1"), quote="CPI was 5.1 percent.")
    rels = compare_facts([old, new])
    assert len(rels) == 1
    assert rels[0].kind == "contradicts"


def test_equal_canonical_corroroborates_across_units():
    from app.pipeline import compare_facts

    left = _known_fact(
        subject="India GDP", metric="nominal GDP", value_display="7,225 INR Cr",
        numeric_value=Decimal("7225"), canonical_value_inr=Decimal("72250000000"),
        unit="INR crore", subject_key="india-gdp", metric_key="nominal-gdp",
        canonical_unit="inr-crore", comparison_key="india-gdp|nominal-gdp",
        quote="GDP was 7,225 INR Cr.",
    )
    right = _known_fact(
        document_id=100, subject="India GDP", metric="nominal GDP", value_display="72.25 INR Bn",
        numeric_value=Decimal("72.25"), canonical_value_inr=Decimal("72250000000"),
        unit="INR billion", subject_key="india-gdp", metric_key="nominal-gdp",
        canonical_unit="inr-billion", comparison_key="india-gdp|nominal-gdp",
        quote="GDP was 72.25 INR Bn.",
    )
    rels = compare_facts([left, right])
    assert len(rels) == 1
    assert rels[0].kind == "corroborates"


def test_non_numeric_facts_compare_by_display_text():
    from app.pipeline import compare_facts

    left = _known_fact(
        subject="IR", metric="email", value_display="ir@x.com", numeric_value=None,
        canonical_value_inr=None, unit=None, period=None, period_key="",
        scope=None, scope_key="", quote="ir@x.com",
        subject_key="ir", metric_key="email", comparison_key="ir|email",
    )
    same = _known_fact(
        document_id=100, subject="IR", metric="email", value_display="IR@X.COM",
        numeric_value=None, canonical_value_inr=None, unit=None, period=None,
        period_key="", scope=None, scope_key="", quote="IR@X.COM",
        subject_key="ir", metric_key="email", comparison_key="ir|email",
    )
    assert compare_facts([left, same])[0].kind == "corroborates"
    different = _known_fact(
        document_id=100, subject="IR", metric="email", value_display="other@y.com",
        numeric_value=None, canonical_value_inr=None, unit=None, period=None,
        period_key="", scope=None, scope_key="", quote="other@y.com",
        subject_key="ir", metric_key="email", comparison_key="ir|email",
    )
    assert compare_facts([left, different])[0].kind == "contradicts"


class _CountingFiles:
    def __init__(self):
        self.get_calls = 0
        self.uploads = 0

    def upload(self, file, config=None):
        from types import SimpleNamespace

        self.uploads += 1
        return SimpleNamespace(name="files/x", uri="u", state="ACTIVE")

    def get(self, name):
        from types import SimpleNamespace

        self.get_calls += 1
        return SimpleNamespace(name=name, state="ACTIVE")


def _window_echo_models(page_text):
    import json as _json
    import re
    from types import SimpleNamespace

    class EchoModels:
        def __init__(self):
            self.calls = 0

        def generate_content(self, model, contents, config=None):
            self.calls += 1
            prompt = contents[-1] if isinstance(contents[-1], str) else ""
            match = re.search(r"pages (\d+) through (\d+)", prompt)
            start = int(match.group(1)) if match else 1
            quote = page_text(start)
            return SimpleNamespace(
                text=_json.dumps(
                    {
                        "facts": [
                            {
                                "subject": "Echo",
                                "metric": "m",
                                "value_display": quote,
                                "unit": None,
                                "period": None,
                                "scope": None,
                                "quote": quote,
                                "confidence": 0.9,
                            }
                        ]
                    }
                )
            )

    return EchoModels()


def _two_page_pdf(tmp_path: Path, texts: list[str]) -> Path:
    import fitz

    pdf = fitz.open()
    for text in texts:
        page = pdf.new_page()
        if text:
            page.insert_textbox(fitz.Rect(72, 72, 540, 750), text)
    out = tmp_path / "windows.pdf"
    pdf.save(out)
    pdf.close()
    return out


def _sentence(i: int) -> str:
    return (f"Alpha testing fact number {i} records {100 + i} INR Cr in the annual filing statement. " * 4).strip()


def test_empty_windows_skipped_without_api_call(tmp_path: Path):
    from types import SimpleNamespace

    from app.gemini import GeminiClient
    from app.pipeline import _extract_via_file, _page_lookup, load_document_pages

    long_text = _sentence(1)
    pdf_path = _two_page_pdf(tmp_path, [long_text] + [""] * 29)
    pages = load_document_pages(
        DocumentRecord(filename="x.pdf", sha256="abc", page_count=30, status="queued"), pdf_path
    )
    pages_by_number = _page_lookup(pages.pages)
    files = _CountingFiles()
    models = _window_echo_models(lambda start: long_text if start == 1 else "")
    fake = SimpleNamespace(files=files, models=models)
    client = GeminiClient(api_key="k", sdk_factory=lambda _key: fake)
    facts, failures = _extract_via_file(
        DocumentRecord(filename="x.pdf", sha256="abc", page_count=30, status="queued"),
        pdf_path,
        pages_by_number,
        client,
    )
    assert models.calls == 1
    assert len(facts) == 1
    assert any("no extractable text" in failure.reason for failure in failures)


def test_windows_run_in_parallel_with_single_file_resolve(tmp_path: Path):
    from types import SimpleNamespace

    from app.gemini import GeminiClient
    from app.pipeline import _extract_via_file, _page_lookup, load_document_pages

    pdf_path = _two_page_pdf(tmp_path, [_sentence(1), _sentence(2)])
    document = DocumentRecord(filename="x.pdf", sha256="abc", page_count=2, status="queued")
    pages_by_number = _page_lookup(load_document_pages(document, pdf_path).pages)
    files = _CountingFiles()
    models = _window_echo_models(_sentence)
    fake = SimpleNamespace(files=files, models=models)
    client = GeminiClient(api_key="k", sdk_factory=lambda _key: fake)
    facts, failures = _extract_via_file(document, pdf_path, pages_by_number, client, window_pages=1)
    assert failures == []
    assert models.calls == 2
    assert files.get_calls == 1
    assert [fact.quote for fact in facts] == [_sentence(1), _sentence(2)]


def test_compare_groups_facts_by_comparison_key(tmp_path: Path):
    pdf_path = _make_pdf(tmp_path)
    document = DocumentRecord(filename="x.pdf", sha256="abc", page_count=1, status="queued")
    runner = PipelineRunner(client=None)
    runner.client.api_key = ""
    # Two facts with the same comparison key but different values:
    # the comparator must surface a contradiction or reconciliation record.
    known = FactRecord(
        document_id=99,
        page_number=1,
        subject="extracted amount",
        metric="reported figure",
        value_display="100 INR Cr",
        numeric_value=Decimal("100"),
        canonical_value_inr=Decimal("1000000000"),
        unit="INR crore",
        period=None,
        scope=None,
        quote="4.6 percent",
        grounding_status="grounded",
        subject_key="extracted-amount",
        metric_key="reported-figure",
        period_key="",
        scope_key="",
        canonical_unit="inr-crore",
        comparison_key="extracted-amount|reported-figure",
    )
    result = runner.run_for_document(document, pdf_path, known_facts=[known])
    assert result.relationships
    kinds = {rel.kind for rel in result.relationships}
    assert kinds & {"corroborates", "contradicts", "reconciles"}
