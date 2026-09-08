from decimal import Decimal

from app import normalize


def test_text_key_lowercases_and_normalizes_punctuation():
    assert normalize.normalize_text_key("India GDP & Inflation!") == "india-gdp-and-inflation"


def test_period_scope_use_same_normalization():
    assert normalize.normalize_period_key("FY 2024-25") == normalize.normalize_scope_key("FY 2024-25")


def test_inr_unit_aliases_collapse_to_inr():
    for unit in ("INR", "₹", "Rs."):
        key = normalize.normalize_unit_key(unit)
        assert key == "inr"


def test_inr_crore_and_billion_distinct_canonical_keys():
    assert normalize.normalize_unit_key("INR crore") == "inr-crore"
    assert normalize.normalize_unit_key("INR billion") == "inr-billion"
    assert normalize.normalize_unit_key("INR Cr") == "inr-crore"
    assert normalize.normalize_unit_key("INR Bn") == "inr-billion"
    assert normalize.normalize_unit_key("USD") == "usd"


def test_crore_and_billion_canonicalize_to_same_inr_value():
    cr = normalize.normalize_fact(
        "India GDP FY25",
        "nominal GDP",
        "7,225 INR Cr",
        unit="INR crore",
        period="FY25",
        scope="India",
    )
    bn = normalize.normalize_fact(
        "India GDP FY25",
        "nominal GDP",
        "72.25 INR Bn",
        unit="INR billion",
        period="FY25",
        scope="India",
    )
    assert cr.canonical_value_inr == Decimal("72250000000")
    assert bn.canonical_value_inr == Decimal("72250000000")
    assert cr.comparison_key == bn.comparison_key


def test_period_difference_keeps_comparison_key_for_reconciliation():
    a = normalize.normalize_fact("X", "y", "1", unit="percent", period="FY25", scope="India")
    b = normalize.normalize_fact("X", "y", "2", unit="percent", period="FY24", scope="India")
    assert a.comparison_key == b.comparison_key
    assert a.period_key != b.period_key


def test_parse_decimal_handles_commas_and_parens():
    assert normalize.parse_decimal("(1,200.50)") == Decimal("-1200.50")
    assert normalize.parse_decimal("₹1,000") == Decimal("1000")
    assert normalize.parse_decimal("nope") is None
    assert normalize.parse_decimal(None) is None
