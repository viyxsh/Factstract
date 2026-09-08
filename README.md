# Factstract

**Factstract** — *facts* + *extract* — is a local-first prototype that
ingests PDFs, extracts grounded facts, compares related facts across
documents, and surfaces one explicit failure. The service exposes a JSON
API and a dependency-free dark web UI. SQLite is the only datastore.

## Requirements

- Python 3.14
- The dependencies listed in `requirements.txt`
- (Optional) `GEMINI_API_KEY` for live extraction with Gemini Flash.
  The demo path runs fully offline using a deterministic regex fallback.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run the service

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000` for the dark UI. The JSON API is rooted at
`/api`.

## Dashboard state

Opening the project seeds the demo showcase when the database is empty,
so the four required cases are visible immediately. Uploading your own
PDFs adds to the current view — new facts are compared against everything
already stored. The rail has a **Reset data** button (with a confirmation
dialog) that calls `POST /api/reset` to wipe documents, facts,
relationships, failures, jobs, cached file handles, and uploaded PDFs;
**Load demo** wipes and reseeds the showcase records. Reset is refused
with `409` while any job is still processing. Jobs left stuck by a server
restart are marked failed on the next startup, so they never block future
resets.

Comparison rule: facts group by normalized subject + metric. Equal values
corroborate (units canonicalize first, so `7,225 INR Cr` matches
`72.25 INR Bn`); differing values with an explicit period/scope difference
reconcile; differing values with no such explanation contradict.

## Run tests

```bash
source .venv/bin/activate
python -m pytest
```

The test suite runs without `GEMINI_API_KEY` and uses deterministic
fixtures, so it is safe to run in CI or on a fresh reviewer machine.

## Demo path (no API key)

1. Start the service.
2. Open the UI and click **Load demo**.
3. Inspect the **Facts**, **Relationships**, and **Known failures** tabs.
4. Open an evidence drawer from any fact card.

The demo loader inserts exactly the four showcase outcomes required by
`PROJECT_SPEC.md`: one corroborates, one contradicts, one reconciles, and
one explicit known failure.

## Live extraction (requires `GEMINI_API_KEY`)

1. Set `GEMINI_API_KEY` in `.env` (Gemini Flash is free for standard
   input/output; the default `GEMINI_MODEL` is `gemini-3.6-flash`).
   The service autoloads `.env` on startup, no manual `export` needed.
2. Restart the service.
3. Upload PDFs from the rail. Each upload returns `202` with a job ID; the
   UI polls `/api/jobs/{id}` until the pipeline finishes. Uploading several
   PDFs at once queues them FIFO: each document processes alone, in order,
   so it is compared against every previously stored fact.

Each PDF is uploaded once via the Gemini Files API and the cached handle
is reused across 25-page extraction windows, so large documents cost one
upload no matter how many windows they need. Handles expire after ~48
hours and are re-uploaded transparently.

`gemini-3.6-flash` is the primary model. On transient upstream failures
(overload, rate limits) each window automatically retries on the
`GEMINI_FALLBACK_MODELS` chain (default `gemini-3.5-flash`,
`gemini-3.5-flash-lite`, `gemini-flash-lite-latest`) before a failure is
recorded; if the whole chain is saturated it waits out the storm per
`FACT_KNOWLEDGE_RETRY_DELAYS` (default one 60s retry). Permanent errors
fail fast with no fallback.

Extraction windows run in parallel (`FACT_KNOWLEDGE_MAX_WORKERS`, default
4) with low thinking effort and a tight output cap to keep large PDFs
fast. Windows with no extractable text are reported as image-only
failures without spending an API call.

## Layout

| Path | Purpose |
| --- | --- |
| `app/contracts.py` | Domain dataclasses |
| `app/db.py` | SQLite repository |
| `app/pdf.py` | PyMuPDF text extraction + chunking |
| `app/normalize.py` | Unit/period/scope canonicalization |
| `app/gemini.py` | Gemini Flash typed client + Files API cache |
| `app/pipeline.py` | Extract → ground → store orchestration |
| `app/demo.py` | Deterministic demo loader |
| `app/main.py` | FastAPI service and static UI mount |
| `app/static/*` | Dark UI shell (HTML, CSS, vanilla JS) |
| `contracts/domain.json` | Domain model snapshot |
| `fixtures/__init__.py` | Re-exports the demo loader |
| `tests/*` | Regression coverage |
| `docs/demo-script.md` | Reviewer runbook |
| `PROJECT_SPEC.md` | Frozen specification |
| `TRACKER.md` | Pack-level tracker |
