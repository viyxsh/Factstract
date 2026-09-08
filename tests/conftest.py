"""Pytest discovery shim: ensure the project root is importable."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Retry backoff must never slow the suite; individual tests opt back in.
os.environ.setdefault("FACT_KNOWLEDGE_RETRY_DELAYS", "")
