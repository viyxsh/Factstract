"""Showcase fixtures live with the deterministic loader in :mod:`app.demo`.

This package exists so the project layout matches ``PROJECT_SPEC.md`` and
``docs/task-breakdown.md`` while keeping the loader, fixtures, and
repository bindings in one testable module.
"""

from app.demo import load_demo

__all__ = ["load_demo"]
