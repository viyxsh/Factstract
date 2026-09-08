"""Isolated Google Gemini adapter for structured fact candidates.

The client is the only module that talks to the Gemini API. It uses the
Files API so each PDF is uploaded once per content hash and the returned
file handle is reused across extraction requests (Google retains files for
~48 hours; expired handles are re-uploaded transparently).

Model output is still verified by the pipeline: every candidate quote must
be a normalized substring of its cited source page, otherwise it becomes a
`known_failure` instead of a fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable


LOGGER = logging.getLogger("app.gemini")


class GeminiError(RuntimeError):
    pass


class GeminiTransientError(GeminiError):
    """Retryable upstream failure (overload, rate limit, 5xx, timeouts)."""


def _is_transient(error: Exception) -> bool:
    """True for failures worth retrying on a fallback model. Auth errors,
    retired models (404), and malformed payloads are permanent."""
    for attr in ("code", "status"):
        value = getattr(error, attr, None)
        if value in (429, 500, 502, 503, 504):
            return True
    message = str(error).lower()
    markers = (
        "unavailable",
        "resource_exhausted",
        "deadline_exceeded",
        "overloaded",
        "high demand",
        "try again",
        "timed out",
        "timeout",
        "disconnected",
        "temporarily",
    )
    return any(marker in message for marker in markers)


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
    "type": "OBJECT",
    "required": ["facts"],
    "properties": {
        "facts": {
            "type": "ARRAY",
            "maxItems": 12,
            "items": {
                "type": "OBJECT",
                "required": ["subject", "metric", "value_display", "unit", "period", "scope", "quote", "confidence"],
                "properties": {
                    "subject": {"type": "STRING"},
                    "metric": {"type": "STRING"},
                    "value_display": {"type": "STRING"},
                    "unit": {"type": "STRING", "nullable": True},
                    "period": {"type": "STRING", "nullable": True},
                    "scope": {"type": "STRING", "nullable": True},
                    "quote": {"type": "STRING"},
                    "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}

DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_FALLBACKS = "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest"
_FILE_TTL = timedelta(hours=47)


def _retry_delays() -> list[float]:
    """Waits between full chain rounds; env-tunable, empty means one round."""
    delays: list[float] = []
    for part in os.getenv("FACT_KNOWLEDGE_RETRY_DELAYS", "60").split(","):
        try:
            value = float(part.strip())
        except ValueError:
            continue
        if value > 0:
            delays.append(value)
    return delays


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _is_not_found(error: Exception) -> bool:
    """True only for a genuinely expired/deleted file handle, never for
    transient transport blips (disconnects, 5xx, timeouts)."""
    code = getattr(error, "code", None)
    if code == 404 or code == "NOT_FOUND":
        return True
    status = getattr(error, "status", None)
    if status == 404 or status == "NOT_FOUND":
        return True
    message = str(error)
    return "NOT_FOUND" in message or "404" in message


class GeminiClient:
    """One API boundary. `sdk_factory` exists so tests can inject a fake."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        repository: Any | None = None,
        sdk_factory: Callable[[str], Any] | None = None,
        fallback_models: Iterable[str] | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self.repository = repository
        self._sdk_factory = sdk_factory
        self._sdk: Any | None = None
        if fallback_models is None:
            raw = os.getenv("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACKS)
            fallback_models = [part.strip() for part in raw.split(",")]
        self.fallback_models = [name for name in fallback_models if name and name != self.model]

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _client(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        if not self.api_key:
            raise GeminiError("GEMINI_API_KEY is not configured")
        factory = self._sdk_factory
        if factory is not None:
            self._sdk = factory(self.api_key)
            return self._sdk
        try:
            from google import genai
        except ImportError as error:  # pragma: no cover
            raise GeminiError("google-genai is not installed. Run pip install -r requirements.txt.") from error
        self._sdk = genai.Client(api_key=self.api_key)
        return self._sdk

    # -- Files API ------------------------------------------------------

    def get_or_upload_pdf(self, pdf_path: Path, sha: str | None = None, filename: str | None = None) -> str:
        """Upload once per content hash; return the cached file name on reuse."""
        digest = sha or sha256(pdf_path.read_bytes()).hexdigest()
        cached = self.repository.get_llm_file(digest) if self.repository is not None else None
        if cached is not None:
            try:
                live = self._client().files.get(name=cached["file_name"])
                state = str(getattr(live, "state", "") or "")
                if "ACTIVE" in state.upper() or not state:
                    return str(cached["file_name"])
            except Exception as error:
                if not _is_not_found(error):
                    # Transient lookup failure: the cached handle is probably
                    # still fine, so keep it instead of forcing a re-upload.
                    return str(cached["file_name"])
            if self.repository is not None:
                try:
                    self.repository.delete_llm_file(digest)
                except Exception:
                    pass
        sdk = self._client()
        try:
            uploaded = sdk.files.upload(
                file=str(pdf_path),
                config={"mime_type": "application/pdf", "display_name": filename or pdf_path.name},
            )
        except Exception as error:
            raise GeminiError(f"Gemini file upload failed: {error}") from error
        file_name = str(getattr(uploaded, "name", "") or "")
        uri = str(getattr(uploaded, "uri", "") or "")
        if not file_name:
            raise GeminiError("Gemini file upload returned no file handle")
        if self.repository is not None:
            try:
                self.repository.put_llm_file(
                    sha256=digest,
                    provider="gemini",
                    file_name=file_name,
                    uri=uri,
                    expires_at=(datetime.now(timezone.utc) + _FILE_TTL).isoformat(timespec="microseconds"),
                )
            except Exception:
                pass
        return file_name

    # -- Extraction -----------------------------------------------------

    def extract_facts(
        self, text: str, page_number: int, section_hint: str | None = None
    ) -> list[LlmFactCandidate]:
        prompt = (
            "Extract only useful numerical or semantic facts directly stated in this PDF page. "
            "Every fact must quote an exact contiguous excerpt from the supplied text. "
            "Do not infer values, reconcile sources, or invent missing period/scope. "
            f"Page: {page_number}. Section hint: {section_hint or 'none'}.\n\nSOURCE TEXT:\n{text}"
        )
        return self._generate_with_fallback([prompt], context="text window")

    def get_file_ref(self, file_name: str) -> Any:
        """Resolve a usable file reference (see `_file_part`). Public so the
        pipeline can resolve once per document instead of once per window."""
        return self._file_part(file_name)

    def _file_part(self, file_name: str) -> Any:
        """Resolve a usable file reference. A transient `files.get` failure
        falls back to the cached URI so one network blip doesn't fail the
        whole window; only a genuine 404 is reported as stale."""
        sdk = self._client()
        try:
            return sdk.files.get(name=file_name)
        except Exception as error:
            if _is_not_found(error):
                raise GeminiError(f"Gemini file handle is stale ({file_name})") from error
            cached_uri = ""
            if self.repository is not None:
                try:
                    row = self.repository.get_llm_file_by_name(file_name)
                    cached_uri = str((row or {}).get("uri", "") or "")
                except Exception:
                    pass
            if cached_uri:
                try:
                    from google.genai import types
                except ImportError as import_error:  # pragma: no cover
                    raise GeminiError(f"Gemini file lookup failed ({file_name}): {error}") from import_error
                return types.Part.from_uri(file_uri=cached_uri, mime_type="application/pdf")
            raise GeminiError(f"Gemini file lookup failed transiently ({file_name}): {error}") from error

    def extract_facts_for_file(
        self,
        file_name: str,
        page_start: int,
        page_end: int,
        section_hint: str | None = None,
        file_ref: Any | None = None,
    ) -> list[LlmFactCandidate]:
        """Extract from a large page window of an already-uploaded PDF file."""
        if file_ref is None:
            file_ref = self._file_part(file_name)
        prompt = (
            "The attached PDF was uploaded via the Files API. Extract only useful "
            "numerical or semantic facts directly stated on pages "
            f"{page_start} through {page_end} (inclusive, 1-based PDF page numbers). "
            "Every fact must quote an exact contiguous excerpt from those pages. "
            "Do not infer values, reconcile sources, or invent missing period/scope. "
            f"Section hint: {section_hint or 'none'}. Return JSON only."
        )
        return self._generate_with_fallback([file_ref, prompt], context=f"pages {page_start}-{page_end}")

    def _generate_with_fallback(self, contents: list[Any], context: str) -> list[LlmFactCandidate]:
        """Try the primary model, then each fallback in order — but only for
        transient upstream failures. Permanent errors fail fast. If the whole
        chain is saturated, wait out the storm and try again, bounded by
        `FACT_KNOWLEDGE_RETRY_DELAYS`."""
        last_error: GeminiError | None = None
        for wait in [0.0, *_retry_delays()]:
            if wait:
                LOGGER.info("Gemini saturated on %s; retrying chain in %.0fs", context, wait)
                time.sleep(wait)
            for model in [self.model, *self.fallback_models]:
                try:
                    return self._generate_for_model(model, contents, context)
                except GeminiTransientError as error:
                    last_error = error
                    LOGGER.warning("Gemini %s transient on %s; trying fallback", model, context)
        assert last_error is not None  # primary always attempted
        raise last_error

    def _generate_for_model(self, model: str, contents: list[Any], context: str) -> list[LlmFactCandidate]:
        sdk = self._client()
        try:
            response = sdk.models.generate_content(
                model=model,
                contents=contents,
                config={
                    "temperature": 0,
                    "response_mime_type": "application/json",
                    "response_schema": FACT_RESPONSE_SCHEMA,
                    # Extraction is mechanical, not reasoning: low thinking
                    # plus a tight output cap keeps windows fast.
                    "thinking_config": {"thinking_level": "low"},
                    "max_output_tokens": 4096,
                },
            )
            text = getattr(response, "text", "") or ""
            decoded = json.loads(text)
        except (GeminiError, json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as error:
            raise GeminiError(f"Gemini extraction failed ({context}): {error}") from error
        except Exception as error:  # SDK transport errors surface as varied types
            if _is_transient(error):
                raise GeminiTransientError(f"Gemini extraction failed ({context}): {error}") from error
            raise GeminiError(f"Gemini extraction failed ({context}): {error}") from error
        return _parse_candidates(decoded)


def _parse_candidates(decoded: Any) -> list[LlmFactCandidate]:
    if not isinstance(decoded, dict) or not isinstance(decoded.get("facts"), list):
        raise GeminiError("Gemini extraction returned an unexpected payload shape")
    result: list[LlmFactCandidate] = []
    for item in decoded["facts"]:
        if not isinstance(item, dict):
            raise GeminiError("Gemini extraction returned a malformed fact entry")
        try:
            result.append(
                LlmFactCandidate(
                    subject=str(item["subject"]),
                    metric=str(item["metric"]),
                    value_display=str(item["value_display"]),
                    unit=item.get("unit"),
                    period=item.get("period"),
                    scope=item.get("scope"),
                    quote=str(item["quote"]),
                    confidence=float(item["confidence"]),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GeminiError(f"Gemini extraction returned a malformed fact entry: {error}") from error
    return result


__all__ = [
    "DEFAULT_MODEL",
    "FACT_RESPONSE_SCHEMA",
    "GeminiClient",
    "GeminiError",
    "LlmFactCandidate",
]
