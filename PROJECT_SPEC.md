# Fact Knowledge Layer — implementation specification

## Goal

Build a local-first prototype that accepts PDFs, extracts grounded facts, compares related facts across documents, and lets a reviewer inspect the evidence and reasoning. It must demonstrate corroboration, contradiction, contextual reconciliation, and one explicit failure.

## Deliberate scope

- A single FastAPI service serves both the JSON API and a dependency-free dark web UI.
- SQLite is the only datastore. Uploaded files and database state live under `.data/` and are never committed.
- PyMuPDF extracts text by page. Scanned/image-only pages are reported as failures; OCR is out of scope.
- Gemini is used only through `app.gemini.GeminiClient`; no route or UI code calls an LLM directly. PDFs are uploaded once via the Files API and the cached handle is reused across extraction windows.
- The first version extracts a conservative, small number of facts per chunk. It does not claim exhaustive document coverage.
- There is no graph database, vector database, authentication, background queue service, or hard-coded production facts.

## Data and evidence rules

1. A fact is visible only when its `quote` is a normalized substring of its cited source page.
2. A numerical fact keeps the original display value and, when supported, a deterministic canonical value.
3. Relationships are generated only between comparison-compatible facts: same normalized subject and metric, with an explicit period/scope decision.
4. A different unit is never itself a contradiction. INR crore and INR billion normalize to INR.
5. A relationship has two fact IDs, a label, a plain-language rationale, and source-page evidence through those facts.
6. Unsupported model output becomes a `known_failure`; it is never silently promoted to a fact.

## API contract

| Method | Path | Response / purpose |
| --- | --- | --- |
| GET | `/api/health` | service status and whether a live LLM is configured |
| GET | `/api/summary` | facts, documents, and relationships counts |
| GET | `/api/documents` | documents and their latest processing state |
| POST | `/api/documents` | upload one or more PDFs; returns `202` jobs |
| GET | `/api/jobs/{job_id}` | queued/running/done/failed job state and progress |
| GET | `/api/facts` | all grounded facts; optional `document_id` |
| GET | `/api/relationships` | cross-document result cards |
| GET | `/api/failures` | explicit extraction/reasoning failures |
| GET | `/api/evidence/{fact_id}` | the stored, verbatim evidence for a fact |
| POST | `/api/demo/load` | load deterministic demo records only; no LLM call |

API errors use `{ "detail": "human-readable reason" }`. List endpoints return arrays, ordered predictably by source and ID.

## Domain contract

### Document

`id`, `filename`, `sha256`, `page_count`, `created_at`, `status`

### Fact

`id`, `document_id`, `page_number`, `subject`, `metric`, `value_display`, `numeric_value`, `canonical_value_inr`, `unit`, `period`, `scope`, `quote`, `confidence`, `grounding_status`

### Relationship

`id`, `left_fact_id`, `right_fact_id`, `kind` (`corroborates|contradicts|reconciles`), `title`, `rationale`, `created_at`

### Failure

`id`, `document_id`, `page_number`, `stage`, `reason`, `source_excerpt`, `suggested_improvement`, `created_at`

### Job

`id`, `document_id`, `status` (`queued|running|done|failed`), `progress`, `message`, `error`, `created_at`, `updated_at`

## Processing state machine

```text
upload → queued → running → page extraction → chunking → LLM candidates
       → grounding → normalization → relationship comparison → done
                                                   └→ failed (only on unrecoverable job error)
```

Individual bad chunks/page extracts are recorded as failures and do not fail the whole document.

## Deterministic completion checks

- `python -m pytest` passes without a network key.
- `POST /api/demo/load` results in exactly four relationship/failure showcase records: one of each required assignment case.
- Any evidence endpoint returns an excerpt that appears on its stored page text.
- `7,225 INR Cr` and `72.25 INR Bn` normalize to the same INR amount.
- A source with different value but explicit differing estimation vintage is `reconciles`, not `contradicts`.
- Uploading a non-PDF returns HTTP 415; duplicate document bytes do not make a duplicate document.

## UI specification

- Dark desktop-first layout based on `Context/UI_insp1.jpg` and `Context/UI_insp2.jpg`.
- Fixed document rail, upload action, top counters, and tabs: Facts, Relationships, Evidence, Known failures.
- Relationship cards use green/red/amber labels respectively and show the two facts plus the rationale.
- Selecting evidence opens a readable drawer/panel with the exact quote and page.
- Keyboard and mobile refinement are not a launch blocker, but controls must remain usable below 800px.

## Non-negotiable handoff material

The repository must contain this specification, `TRACKER.md`, `.env.example`, `requirements.txt`, tests, a runnable README, and `docs/demo-script.md`.

