# Deterministic task breakdown

This file is the repo-local work queue. Each task is intentionally small enough that one subagent can own it without depending on implementation details from the others.

Rules for every task:

- Own exactly one file boundary or one tightly scoped file cluster.
- Read `PROJECT_SPEC.md` first.
- Produce deterministic output and a clear acceptance check.
- Prefer pure functions, exact API contracts, and stored demo fixtures over heuristic behavior.

## Work packs

| ID | Scope | Inputs | Output | Acceptance |
| --- | --- | --- | --- | --- |
| P01 | `app/pdf.py` | PDF bytes/text extraction rules | page text + chunking helpers | same PDF always yields same page/chunk IDs |
| P02 | `app/gemini.py` | Gemini API contract + Files API cache | typed client + schema gate | mock SDK test passes |
| P03 | `app/normalize.py` | unit/period/scoping rules | canonical comparison keys | `Cr` and `Bn` normalize to the same INR value |
| P04 | `app/contracts.py` | domain entities | dataclasses or Pydantic models | models match `PROJECT_SPEC.md` fields |
| P05 | `app/db.py` | SQLite persistence rules | schema + CRUD helpers | round-trip insert/read test passes |
| P06 | `app/pipeline.py` | extraction and grounding flow | deterministic orchestration | bad quote becomes `known_failure` |
| P07 | `app/demo.py` | showcase cases | offline demo loader | exact four showcase outcomes load |
| P08 | `app/main.py` | HTTP endpoints | FastAPI service | every API path in `PROJECT_SPEC.md` responds |
| P09 | `app/static/*` | UI spec + mockups | dependency-free dark shell | upload, tabs, evidence drawer render correctly |
| P10 | `tests/*` | all above modules | regression coverage | `pytest` passes without a network key |
| P11 | `README.md` + `docs/demo-script.md` | final runbook | short setup/demonstration guide | a fresh reviewer can run the demo in under 3 minutes |

## UI task split

If the UI work needs further subdivision, keep it in this order:

1. `app/static/index.html`
2. `app/static/styles.css`
3. `app/static/app.js`

That ordering avoids inter-file churn and lets each part be checked independently.
