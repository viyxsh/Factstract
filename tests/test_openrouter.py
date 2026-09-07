import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.openrouter import FACT_RESPONSE_SCHEMA, LlmFactCandidate, OpenRouterClient, OpenRouterError


class FakeTransport:
    def __init__(self, payload: dict | Exception):
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict, json: dict, timeout: float):
        self.calls.append((url, json))
        if isinstance(self.payload, Exception):
            raise self.payload
        return _FakeResponse(self.payload)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_extract_facts_uses_configured_endpoint():
    transport = FakeTransport({"choices": [{"message": {"content": json.dumps({"facts": [
        {
            "subject": "GDP",
            "metric": "nominal",
            "value_display": "100 INR Cr",
            "unit": "INR crore",
            "period": "FY25",
            "scope": "India",
            "quote": "GDP was 100 INR Cr.",
            "confidence": 0.8,
        }
    ]})}}]})
    with patch("httpx.post", side_effect=transport):
        client = OpenRouterClient(api_key="key", model="x", base_url="https://example.test/v1")
        candidates = client.extract_facts("source text", page_number=1, section_hint=None)
    assert len(candidates) == 1
    assert isinstance(candidates[0], LlmFactCandidate)
    assert candidates[0].subject == "GDP"
    assert transport.calls and "chat/completions" in transport.calls[0][0]


def test_extract_facts_requires_api_key():
    client = OpenRouterClient(api_key=None)
    client.api_key = ""
    with pytest.raises(OpenRouterError):
        client.extract_facts("text", page_number=1)


def test_extract_facts_raises_on_bad_payload():
    transport = FakeTransport({"choices": []})
    with patch("httpx.post", side_effect=transport):
        client = OpenRouterClient(api_key="k", model="x", base_url="https://example.test/v1")
        with pytest.raises(OpenRouterError):
            client.extract_facts("text", page_number=1)


def test_extract_facts_raises_on_invalid_json():
    transport = FakeTransport({"choices": [{"message": {"content": "not-json"}}]})
    with patch("httpx.post", side_effect=transport):
        client = OpenRouterClient(api_key="k", model="x", base_url="https://example.test/v1")
        with pytest.raises(OpenRouterError):
            client.extract_facts("text", page_number=1)


def test_response_schema_is_strict():
    assert FACT_RESPONSE_SCHEMA["strict"] is True
    assert "facts" in FACT_RESPONSE_SCHEMA["schema"]["required"]
