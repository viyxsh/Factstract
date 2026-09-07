import tempfile
from decimal import Decimal
from pathlib import Path

from app.db import Repository
from app.demo import load_demo


def test_load_demo_inserts_required_showcase_records():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Repository(str(Path(tmpdir) / "demo.sqlite3"))
        repo.init()
        counts = load_demo(repo)
        assert counts == {
            "documents": 3,
            "facts": 6,
            "relationships": 3,
            "failures": 1,
        }
        relationships = repo.list_relationships()
        kinds = {relationship.kind for relationship in relationships}
        assert kinds == {"corroborates", "contradicts", "reconciles"}
        failures = repo.list_failures()
        assert len(failures) == 1
        assert failures[0].stage == "grounding"


def test_load_demo_normalizes_canonical_value_for_units():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Repository(str(Path(tmpdir) / "demo.sqlite3"))
        repo.init()
        load_demo(repo)
        facts = repo.list_facts()
        canonical_values = {
            (fact.subject, fact.metric, fact.unit): fact.canonical_value_inr
            for fact in facts
        }
        assert canonical_values[("India GDP FY25", "nominal GDP", "INR crore")] == Decimal("72250000000")
        assert canonical_values[("India GDP FY25", "nominal GDP", "INR billion")] == Decimal("72250000000")


def test_load_demo_is_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Repository(str(Path(tmpdir) / "demo.sqlite3"))
        repo.init()
        first = load_demo(repo)
        second = load_demo(repo)
        assert first == second
        assert len(repo.list_facts()) == first["facts"]
