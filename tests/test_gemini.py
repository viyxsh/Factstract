import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gemini import FACT_RESPONSE_SCHEMA, GeminiClient, GeminiError, LlmFactCandidate


def _fact_payload(**overrides):
    entry = {
        "subject": "GDP",
        "metric": "nominal",
        "value_display": "100 INR Cr",
        "unit": "INR crore",
        "period": "FY25",
        "scope": "India",
        "quote": "GDP was 100 INR Cr.",
        "confidence": 0.8,
    }
    entry.update(overrides)
    return {"facts": [entry]}


class FakeFiles:
    def __init__(self, fail_get: bool = False):
        self.uploads: list[dict] = []
        self.fail_get = fail_get

    def upload(self, file: str, config: dict | None = None):
        self.uploads.append({"file": file, "config": config})
        return SimpleNamespace(name="files/abc123", uri="https://example.test/files/abc123", state="ACTIVE")

    def get(self, name: str):
        if self.fail_get:
            raise RuntimeError("gone")
        return SimpleNamespace(name=name, state="ACTIVE")


class FakeModels:
    def __init__(self, payload: dict | str | Exception):
        self.payload = payload
        self.calls: list[dict] = []

    def generate_content(self, model: str, contents: list, config: dict | None = None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if isinstance(self.payload, Exception):
            raise self.payload
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(text=text)


class FakeSdk:
    def __init__(self, payload: dict | str | Exception, fail_get: bool = False):
        self.files = FakeFiles(fail_get=fail_get)
        self.models = FakeModels(payload)


def _client(payload, **kwargs) -> tuple[GeminiClient, FakeSdk]:
    fake = FakeSdk(payload, fail_get=kwargs.pop("fail_get", False))
    client = GeminiClient(api_key="key", model="gemini-3.6-flash", sdk_factory=lambda _key: fake)
    return client, fake


def test_extract_facts_returns_candidates():
    client, fake = _client(_fact_payload())
    candidates = client.extract_facts("source text", page_number=1, section_hint=None)
    assert len(candidates) == 1
    assert isinstance(candidates[0], LlmFactCandidate)
    assert candidates[0].subject == "GDP"
    assert fake.models.calls and fake.models.calls[0]["model"] == "gemini-3.6-flash"


def test_default_model_is_current_flash():
    from app.gemini import DEFAULT_MODEL

    assert DEFAULT_MODEL == "gemini-3.6-flash"
    assert GeminiClient(api_key="key").model == "gemini-3.6-flash"


def test_extract_facts_requires_api_key():
    client = GeminiClient(api_key=None)
    client.api_key = ""
    with pytest.raises(GeminiError):
        client.extract_facts("text", page_number=1)


def test_extract_facts_raises_on_bad_payload():
    client, _ = _client({"unexpected": "shape"})
    with pytest.raises(GeminiError):
        client.extract_facts("text", page_number=1)


def test_extract_facts_raises_on_invalid_json():
    client, _ = _client("not-json")
    with pytest.raises(GeminiError):
        client.extract_facts("text", page_number=1)


def test_extract_facts_raises_on_transport_error():
    client, _ = _client(RuntimeError("boom"))
    with pytest.raises(GeminiError):
        client.extract_facts("text", page_number=1)


def test_get_or_upload_reuses_cached_handle(tmp_path: Path):
    from app.db import Repository

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    repo = Repository(str(tmp_path / "cache.sqlite3"))
    repo.init()
    client, fake = _client(_fact_payload())
    client.repository = repo
    first = client.get_or_upload_pdf(pdf, sha="abc", filename="doc.pdf")
    second = client.get_or_upload_pdf(pdf, sha="abc", filename="doc.pdf")
    assert first == second == "files/abc123"
    assert len(fake.files.uploads) == 1


def test_get_or_upload_reuploads_when_handle_stale(tmp_path: Path):
    from app.db import Repository

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    repo = Repository(str(tmp_path / "cache.sqlite3"))
    repo.init()
    repo.put_llm_file(sha256="abc", provider="gemini", file_name="files/stale", uri="", expires_at=None)
    client, fake = _client(_fact_payload(), fail_get=True)
    # First call finds the stale handle gone; re-upload path uses a fresh SDK.
    fresh = FakeSdk(_fact_payload())
    client._sdk_factory = lambda _key: fresh
    client._sdk = None
    handle = client.get_or_upload_pdf(pdf, sha="abc", filename="doc.pdf")
    assert handle == "files/abc123"
    assert len(fresh.files.uploads) == 1


def test_extract_facts_for_file_uses_file_handle():
    client, fake = _client(_fact_payload())
    candidates = client.extract_facts_for_file("files/abc123", 1, 25)
    assert len(candidates) == 1
    assert candidates[0].quote == "GDP was 100 INR Cr."


class FlakyModels:
    """Fail transiently on the primary model, succeed on any fallback."""

    def __init__(self):
        self.calls: list[dict] = []

    def generate_content(self, model: str, contents: list, config: dict | None = None):
        import json as _json

        self.calls.append({"model": model, "contents": contents, "config": config})
        if model == "gemini-3.6-flash":
            raise RuntimeError("503 UNAVAILABLE: high demand, try again later")
        return SimpleNamespace(text=_json.dumps(_fact_payload()))


def test_fallback_model_used_on_transient_failure():
    fake_sdk = SimpleNamespace(files=FakeFiles(), models=FlakyModels())
    client = GeminiClient(
        api_key="key",
        model="gemini-3.6-flash",
        fallback_models=["gemini-3.5-flash"],
        sdk_factory=lambda _key: fake_sdk,
    )
    candidates = client.extract_facts("source text", page_number=1)
    assert len(candidates) == 1
    tried = [call["model"] for call in fake_sdk.models.calls]
    assert tried == ["gemini-3.6-flash", "gemini-3.5-flash"]


def test_all_models_failing_raises():
    class AlwaysFail:
        def generate_content(self, model: str, contents: list, config: dict | None = None):
            raise RuntimeError("503 UNAVAILABLE: high demand, try again later")

    fake_sdk = SimpleNamespace(files=FakeFiles(), models=AlwaysFail())
    client = GeminiClient(
        api_key="key",
        model="gemini-3.6-flash",
        fallback_models=["gemini-3.5-flash"],
        sdk_factory=lambda _key: fake_sdk,
    )
    with pytest.raises(GeminiError):
        client.extract_facts("source text", page_number=1)


def test_permanent_error_does_not_fall_back():
    class AuthFail:
        def __init__(self):
            self.calls = 0

        def generate_content(self, model: str, contents: list, config: dict | None = None):
            self.calls += 1
            raise RuntimeError("401 UNAUTHORIZED: API key not valid")

    models = AuthFail()
    fake_sdk = SimpleNamespace(files=FakeFiles(), models=models)
    client = GeminiClient(
        api_key="key",
        model="gemini-3.6-flash",
        fallback_models=["gemini-3.5-flash"],
        sdk_factory=lambda _key: fake_sdk,
    )
    with pytest.raises(GeminiError):
        client.extract_facts("source text", page_number=1)
    assert models.calls == 1


def test_retry_delays_parse_from_env(monkeypatch):
    from app.gemini import _retry_delays

    monkeypatch.setenv("FACT_KNOWLEDGE_RETRY_DELAYS", "30, 90")
    assert _retry_delays() == [30.0, 90.0]
    monkeypatch.setenv("FACT_KNOWLEDGE_RETRY_DELAYS", "")
    assert _retry_delays() == []


def test_chain_retries_after_wait(monkeypatch):
    import time as _time

    from app.gemini import GeminiClient

    monkeypatch.setenv("FACT_KNOWLEDGE_RETRY_DELAYS", "45")
    slept: list[float] = []
    monkeypatch.setattr(_time, "sleep", lambda seconds: slept.append(seconds))

    attempts = {"n": 0}

    class StormThenClear:
        def generate_content(self, model: str, contents: list, config: dict | None = None):
            import json as _json

            attempts["n"] += 1
            # First round fails (primary + fallback), round two clears.
            if attempts["n"] <= 2:
                raise RuntimeError("503 UNAVAILABLE: high demand, try again later")
            return SimpleNamespace(text=_json.dumps(_fact_payload()))

    fake_sdk = SimpleNamespace(files=FakeFiles(), models=StormThenClear())
    client = GeminiClient(
        api_key="key",
        model="gemini-3.6-flash",
        fallback_models=["gemini-3.5-flash"],
        sdk_factory=lambda _key: fake_sdk,
    )
    candidates = client.extract_facts("source text", page_number=1)
    assert len(candidates) == 1
    assert slept == [45.0]


def test_response_schema_has_facts():
    assert "facts" in FACT_RESPONSE_SCHEMA["properties"]
    assert FACT_RESPONSE_SCHEMA["required"] == ["facts"]


def test_response_schema_uses_single_types():
    # The SDK rejects OpenAI-style ["string", "null"] unions client-side.
    def _walk(node):
        if isinstance(node, dict):
            if "type" in node:
                assert isinstance(node["type"], str), node
            for child in node.values():
                _walk(child)
        elif isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(FACT_RESPONSE_SCHEMA)


def test_transient_lookup_keeps_cache(tmp_path: Path):
    from app.db import Repository

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    repo = Repository(str(tmp_path / "cache.sqlite3"))
    repo.init()
    repo.put_llm_file(sha256="abc", provider="gemini", file_name="files/kept", uri="https://example.test/files/kept", expires_at=None)
    client, fake = _client(_fact_payload(), fail_get=True)
    client.repository = repo
    handle = client.get_or_upload_pdf(pdf, sha="abc", filename="doc.pdf")
    assert handle == "files/kept"
    assert len(fake.files.uploads) == 0
    assert repo.get_llm_file("abc") is not None


def test_not_found_lookup_triggers_reupload(tmp_path: Path):
    from app.db import Repository

    class NotFoundFiles(FakeFiles):
        def get(self, name: str):
            raise RuntimeError("404 NOT_FOUND: file is gone")

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    repo = Repository(str(Path(tmp_path) / "cache.sqlite3"))
    repo.init()
    repo.put_llm_file(sha256="abc", provider="gemini", file_name="files/stale", uri="", expires_at=None)
    fake_sdk = SimpleNamespace(files=NotFoundFiles(), models=FakeModels(_fact_payload()))
    client = GeminiClient(api_key="key", repository=repo, sdk_factory=lambda _key: fake_sdk)
    handle = client.get_or_upload_pdf(pdf, sha="abc", filename="doc.pdf")
    assert handle == "files/abc123"
    assert repo.get_llm_file("abc")["file_name"] == "files/abc123"
