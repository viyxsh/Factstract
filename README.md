# Fact Knowledge Layer

A local-first prototype that ingests PDFs, extracts grounded facts, compares
related facts across documents, and surfaces one explicit failure. The
service exposes a JSON API and a dependency-free dark web UI. SQLite is the
only datastore.

## Requirements

- Python 3.14
- The dependencies listed in `requirements.txt`
- (Optional) `OPENROUTER_API_KEY` for live extraction. The demo path runs
  fully offline using a deterministic regex fallback.

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

## Run tests

```bash
source .venv/bin/activate
python -m pytest
```

The test suite runs without `OPENROUTER_API_KEY` and uses deterministic
fixtures, so it is safe to run in CI or on a fresh reviewer machine.

## Demo path (no API key)

1. Start the service.
2. Open the UI and click **Load demo**.
3. Inspect the **Facts**, **Relationships**, and **Known failures** tabs.
4. Open an evidence drawer from any fact card.

The demo loader inserts exactly the four showcase outcomes required by
`PROJECT_SPEC.md`: one corroborates, one contradicts, one reconciles, and
one explicit known failure.

## Live extraction (requires `OPENROUTER_API_KEY`)

1. Set `OPENROUTER_API_KEY` in `.env`.
2. Restart the service.
3. Upload PDFs from the rail. Each upload returns `202` with a job ID; the
   UI polls `/api/jobs/{id}` until the pipeline finishes.

## Layout

| Path | Purpose |
| --- | --- |
| `app/contracts.py` | Domain dataclasses |
| `app/db.py` | SQLite repository |
| `app/pdf.py` | PyMuPDF text extraction + chunking |
| `app/normalize.py` | Unit/period/scope canonicalization |
| `app/openrouter.py` | OpenRouter typed client |
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
