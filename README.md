# Factstract

**Factstract** (*facts* + *extract*) is a local-first fact knowledge layer.
Upload PDFs and it extracts grounded facts, then links them across documents
as **corroborations**, **contradictions**, or **reconciliations**, with
source evidence shown inline and extraction failures surfaced explicitly
instead of hidden.

🎬 **Video demo (≤ 3 min):** https://www.loom.com/share/1ba6d9116e594e90b1768b742d7c7fd7

## Contents

- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [Setup and run instructions](#setup-and-run-instructions)
- [API or UI](#api-or-ui)
  - [JSON API](#json-api)
  - [Web UI](#web-ui)
- [What counts as a fact and how it is represented](#what-counts-as-a-fact-and-how-it-is-represented)
- [Approach, architecture and key decisions](#approach-architecture-and-key-decisions)
- [Limitations and next steps](#limitations-and-next-steps)
- [Additional notes](#additional-notes)

## Tech stack

![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Uvicorn](https://img.shields.io/badge/Uvicorn-2C2C2C?logo=uvicorn&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini_Flash-4285F4?logo=googlegemini&logoColor=white)
![PyMuPDF](https://img.shields.io/badge/PyMuPDF-FF6F00?logo=adobeacrobatreader&logoColor=white)
![HTML5](https://img.shields.io/badge/HTML5-E34F26?logo=html5&logoColor=white)
![CSS3](https://img.shields.io/badge/CSS3-1572B6?logo=css&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?logo=javascript&logoColor=black)
![Pytest](https://img.shields.io/badge/pytest-0A9EDC?logo=pytest&logoColor=white)

| Layer | Technology |
| --- | --- |
| Language | Python 3.14 |
| API + UI server | FastAPI + Uvicorn (one service serves both) |
| Datastore | SQLite (only datastore; state lives under `.data/`) |
| LLM | Gemini Flash (`gemini-3.6-flash` primary, lite fallbacks) via the `google-genai` SDK |
| PDF parsing | PyMuPDF (page text extraction, no OCR) |
| Frontend | Dependency-free HTML + CSS + vanilla JS dark UI |
| Tests | pytest (runs fully offline, no API key) |

## Architecture

![Factstract architecture](Context/Architecture%20Diagram.png)

## Setup and run instructions

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure the key (optional; demo mode works without it)
cp .env.example .env
# then put your key in .env as GEMINI_API_KEY=...

# 4. Start the service (autoloads .env, no manual export needed)
uvicorn app.main:app --reload

# 5. Open the UI
open http://127.0.0.1:8000

# 6. Run the test suite (needs no API key, no network)
python -m pytest
```

Opening the project with an empty database auto-loads the deterministic
demo showcase, so the four required cases are visible immediately.

## API or UI

Both. One FastAPI service exposes a JSON API and serves the dark review UI
from `/`.

### JSON API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | service status + whether a live LLM is configured |
| GET | `/api/summary` | counts of facts, documents, relationships |
| GET | `/api/documents` | documents and their processing state |
| POST | `/api/documents` | upload one or more PDFs (PDF only, 50 MB/file); returns `202` jobs |
| GET | `/api/jobs/{job_id}` | `queued` → `running` → `done`/`failed` with progress |
| GET | `/api/facts` | grounded facts; optional `?document_id=` |
| GET | `/api/relationships` | comparison cards with rationale |
| GET | `/api/failures` | explicit extraction/reasoning failures |
| GET | `/api/evidence/{fact_id}` | stored verbatim evidence for one fact |
| POST | `/api/demo/load` | reseed the deterministic demo (wipes first) |
| POST | `/api/reset` | wipe everything; refused with `409` while a job runs |

Errors look like `{ "detail": "human-readable reason" }`. Uploading a
non-PDF returns `415`; oversize files return `413`; re-uploading finished
bytes is a no-op (SHA-256 dedupe) instead of duplicating facts.

### Web UI

Fixed document rail (brand, tip card, **Upload PDFs** / **Load demo** /
**Reset data** with confirm dialog), live health indicator, four stat
cards, job banner with clamped progress bar, and four tabs:

- **Facts:** grounded fact cards with an eye icon that jumps to evidence.
- **Relationships:** green/red/amber cards; each side expands its exact
  quote inline so two evidences sit side by side for comparison.
- **Evidence:** every fact with an inline expandable quote, page, value,
  filename, and confidence.
- **Known failures:** rejected quotes with reason, source excerpt, and a
  suggested fix.

A searchable document dropdown scopes every tab; the layout stays usable
below 800 px.

## What counts as a fact and how it is represented

A fact is a single checkable claim from exactly one document page: a
*subject* (who/what), a *metric* (what was measured), a *display value*
with *unit*, and an optional *period*/*scope*, plus the **exact contiguous
quote** it came from. A fact is stored only if its quote appears
word-for-word on the cited page; anything else becomes a `known_failure`.

```json
{
  "id": 1,
  "document_id": 1,
  "page_number": 42,
  "subject": "India GDP FY25",
  "metric": "nominal GDP",
  "value_display": "7,225 INR Cr",
  "numeric_value": "7225",
  "canonical_value_inr": "72250000000",
  "unit": "INR crore",
  "period": "FY25",
  "scope": "India",
  "quote": "Nominal GDP for FY25 was estimated at 7,225 INR Cr.",
  "confidence": 0.92,
  "grounding_status": "grounded"
}
```

A relationship links two comparison-compatible facts (same normalized
subject and metric) with a plain-language rationale:

```json
{
  "id": 1,
  "left_fact_id": 1,
  "right_fact_id": 2,
  "kind": "corroborates",
  "title": "Nominal GDP FY25 across two reports",
  "rationale": "Both reports state 72,250,000,000 INR after unit canonicalization (7,225 INR Cr == 72.25 INR Bn)."
}
```

`kind` is one of `corroborates` (equal canonical value), `contradicts`
(different value, same period/scope), or `reconciles` (different value
explained by an explicit period/scope difference).

## Approach, architecture and key decisions

**Pipeline:** upload → queued → one FIFO worker → page extraction →
25-page windows in parallel over a cached Files API handle → Gemini
candidates → grounding check → unit normalization → pairwise comparison →
stored (replacing that document's previous rows, never duplicating).

**Important decisions and trade-offs:**

- **Gemini Flash over paid APIs:** free tier, 1M-token context, and a
  Files API (upload once per SHA-256, reuse handles, 50 MB/PDF) tailor-made
  for the large/many-PDF brownie points. `3.6-flash` primary with lite
  fallbacks plus a timed retry round, because free-tier 503 storms are real.
- **SQLite only, no graph/vector DB:** relationships are computed joins
  over normalized keys, fully auditable in one file. Trade-off: no
  semantic similarity; only same-subject/same-metric facts compare.
- **Grounding as a hard gate:** the model proposes, the pipeline
  disposes. Ungrounded output is a recorded failure, which directly
  produces the assignment's fourth case instead of hallucinations.
- **Conservative extraction:** at most 12 facts per window with low
  thinking effort, trading exhaustive coverage for speed, cost, and
  precision.
- **FIFO documents, parallel windows:** one document at a time (so each
  sees all prior facts for cross-doc links) with parallel windows inside
  it; text-empty windows are logged as image-only without an API call.
- **Friendly errors:** raw provider output stays in server logs; the UI
  only ever shows actionable messages.
- **Deterministic demo + offline tests:** reviewers can evaluate without
  any key; `pytest` needs no network.

**AI tools used:** built with the help of AI coding assistants, with
human review of every change, live API verification, and a 67-test
regression suite guarding behavior.

## Limitations and next steps

- Free-tier rate limits can still 503 under load; the fallback chain
  absorbs brief storms, not sustained outages.
- Only same-subject/same-metric facts compare; differently worded
  subjects never meet, and entity aliasing would fix this.
- No OCR: scanned/image-only pages are reported as failures by design.
- Single local worker, no auth or multi-user support.
- Next: incremental wiki-style synthesis pages per entity, richer
  cross-vintage reasoning, and OCR as an explicit preprocessing stage.

## Additional notes

- The starter PDFs and reference mockups are gitignored, so the repo is
  code-only; use **Load demo** or upload any PDFs to populate it.
- Credentials never enter the repo (`.env` is ignored); the demo video
  above shows a full PDF run plus all four required cases.
