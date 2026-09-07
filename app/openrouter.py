"""Small, isolated OpenRouter adapter for structured fact candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any


class OpenRouterError(RuntimeError):
    pass


@dataclass(frozen=True)
class LlmFactCandidate:
    subject: str
    metric: str
    value_display: str
    unit: str | None
    period: str | None
    scope: str | None
    quote: str
    confidence: float


FACT_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "grounded_fact_candidates",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["facts"],
        "properties": {
            "facts": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["subject", "metric", "value_display", "unit", "period", "scope", "quote", "confidence"],
                    "properties": {
                        "subject": {"type": "string"},
                        "metric": {"type": "string"},
                        "value_display": {"type": "string"},
                        "unit": {"type": ["string", "null"]},
                        "period": {"type": ["string", "null"]},
                        "scope": {"type": ["string", "null"]},
                        "quote": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            }
        },
    },
}


class OpenRouterClient:
    """One API boundary. Model output is still verified by the pipeline."""

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.model = model or os.getenv("OPENROUTER_MODEL", "openrouter/free")
        self.base_url = (base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def extract_facts(self, text: str, page_number: int, section_hint: str | None = None) -> list[LlmFactCandidate]:
        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not configured")
        try:
            import httpx
        except ImportError as error:  # pragma: no cover
            raise OpenRouterError("httpx is not installed. Run pip install -r requirements.txt.") from error

        prompt = (
            "Extract only useful numerical or semantic facts directly stated in this PDF page. "
            "Every fact must quote an exact contiguous excerpt from the supplied text. "
            "Do not infer values, reconcile sources, or invent missing period/scope. "
            f"Page: {page_number}. Section hint: {section_hint or 'none'}.\n\nSOURCE TEXT:\n{text}"
        )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "You are a precise financial and policy document fact extractor. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_schema", "json_schema": FACT_RESPONSE_SCHEMA},
            "provider": {"require_parameters": True},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            response = httpx.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=60.0)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            decoded = json.loads(content)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
            raise OpenRouterError(f"OpenRouter extraction failed: {error}") from error
        result: list[LlmFactCandidate] = []
        for item in decoded.get("facts", []):
            result.append(LlmFactCandidate(**item))
        return result

