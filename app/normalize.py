from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_NEGATIVE_PARENS_RE = re.compile(r"^\((.*)\)$")

_INR_ALIASES = {
    "inr",
    "rs",
    "rupee",
    "rupees",
    "₹",
}

_INR_CR_ALIASES = {
    "inr-cr",
    "inr-crore",
    "inr crore",
    "inr cr",
}

_INR_BN_ALIASES = {
    "inr-bn",
    "inr-billion",
    "inr bn",
    "inr billion",
}

_PLAIN_CR_ALIASES = {
    "cr",
    "crore",
}

_PLAIN_BN_ALIASES = {
    "bn",
    "billion",
}


@dataclass(slots=True, frozen=True)
class NormalizedFact:
    subject_key: str
    metric_key: str
    period_key: str
    scope_key: str
    unit_key: str
    comparison_key: str
    numeric_value: Decimal | None
    canonical_value_inr: Decimal | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_key": self.subject_key,
            "metric_key": self.metric_key,
            "period_key": self.period_key,
            "scope_key": self.scope_key,
            "unit_key": self.unit_key,
            "comparison_key": self.comparison_key,
            "numeric_value": None if self.numeric_value is None else str(self.numeric_value),
            "canonical_value_inr": None if self.canonical_value_inr is None else str(self.canonical_value_inr),
        }


def normalize_text_key(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.strip().lower()
    lowered = lowered.replace("₹", "inr")
    lowered = lowered.replace("&", " and ")
    lowered = lowered.replace("/", " ")
    lowered = lowered.replace("\\", " ")
    lowered = lowered.replace("–", "-")
    lowered = lowered.replace("—", "-")
    lowered = _SPACE_RE.sub(" ", lowered)
    lowered = _NON_ALNUM_RE.sub("-", lowered)
    lowered = re.sub(r"-+", "-", lowered).strip("-")
    return lowered


def normalize_period_key(value: str | None) -> str:
    return normalize_text_key(value)


def normalize_scope_key(value: str | None) -> str:
    return normalize_text_key(value)


def normalize_unit_key(value: str | None) -> str:
    key = normalize_text_key(value)
    if key in _INR_CR_ALIASES:
        return "inr-crore"
    if key in _INR_BN_ALIASES:
        return "inr-billion"
    if key in _INR_ALIASES:
        return "inr"
    if key in _PLAIN_CR_ALIASES:
        return "crore"
    if key in _PLAIN_BN_ALIASES:
        return "billion"
    return key


def parse_decimal(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    text = text.replace("₹", "")
    match = _NEGATIVE_PARENS_RE.match(text)
    if match:
        text = f"-{match.group(1).strip()}"
    numeric_match = re.search(r"-?\d+(?:\.\d+)?", text)
    if numeric_match is not None:
        text = numeric_match.group(0)
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _value_to_inr_multiplier(unit_key: str) -> Decimal:
    if unit_key in _INR_CR_ALIASES or unit_key == "inr-crore" or unit_key == "crore":
        return Decimal("10000000")
    if unit_key in _INR_BN_ALIASES or unit_key == "inr-billion" or unit_key == "billion":
        return Decimal("1000000000")
    return Decimal("1")


def normalize_fact(
    subject: str | None,
    metric: str | None,
    value_display: str | int | float | Decimal | None,
    unit: str | None = None,
    period: str | None = None,
    scope: str | None = None,
) -> NormalizedFact:
    subject_key = normalize_text_key(subject)
    metric_key = normalize_text_key(metric)
    period_key = normalize_period_key(period)
    scope_key = normalize_scope_key(scope)
    unit_key = normalize_unit_key(unit)
    numeric_value = parse_decimal(value_display)

    canonical_value_inr: Decimal | None = None
    if numeric_value is not None:
        multiplier = _value_to_inr_multiplier(unit_key)
        canonical_value_inr = numeric_value * multiplier

    # Grouping key is subject+metric only. Period/scope stay on separate keys
    # so the comparator can reconcile (differing vintage/scope) instead of
    # silently splitting those facts into incomparable groups.
    comparison_key = "|".join((subject_key, metric_key))
    return NormalizedFact(
        subject_key=subject_key,
        metric_key=metric_key,
        period_key=period_key,
        scope_key=scope_key,
        unit_key=unit_key,
        comparison_key=comparison_key,
        numeric_value=numeric_value,
        canonical_value_inr=canonical_value_inr,
    )


__all__ = [
    "NormalizedFact",
    "normalize_fact",
    "normalize_period_key",
    "normalize_scope_key",
    "normalize_text_key",
    "normalize_unit_key",
    "parse_decimal",
]
