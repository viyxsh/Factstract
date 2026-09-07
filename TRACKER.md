# Build tracker

Status legend: `TODO` · `IN PROGRESS` · `DONE` · `BLOCKED`

| ID | Status | Owner boundary | Deliverable | Depends on | Deterministic done check |
| --- | --- | --- | --- | --- | --- |
| T00 | DONE | root | `PROJECT_SPEC.md`, env/dependency files | — | API/domain/state-machine rules are frozen |
| T01 | DONE | `app/contracts.py`, `app/db.py` | SQLite domain persistence | T00 | CRUD round-trip test passes |
| T02 | DONE | `app/pdf.py` | page text and deterministic chunking | T00 | same file → same page/chunk IDs |
| T03 | DONE | `app/normalize.py` | units/period comparison keys | T00 | Cr/Bn test passes |
| T04 | DONE | `app/openrouter.py` | typed OpenRouter adapter | T00 | fake transport unit test passes |
| T05 | DONE | `app/pipeline.py` | extract→ground→store orchestration | T01–T04 | bad quote is recorded as failure |
| T06 | DONE | `app/main.py` | HTTP API and job runner | T01–T05 | health/upload/jobs endpoints tested |
| T07 | DONE | `app/static/*` | dark UI shell, tabs, drawer | T00, T06 contract | demo records render in all tabs |
| T08 | DONE | `app/demo.py`, `fixtures/*` | deterministic showcase records | T01, T03 | exact four showcase outcomes load offline |
| T09 | DONE | `tests/*` | unit/API regression coverage | T01–T08 | `pytest` needs no API key |
| T10 | DONE | `README.md`, `docs/demo-script.md` | setup + ≤3 min demo runbook | T06–T09 | fresh reviewer can follow commands |

## Atomic work packs

The coarse tickets above are useful for overview, but the implementation should be split into smaller deterministic packs before handoff:

| Pack | Owned files | Goal | Deterministic check |
| --- | --- | --- | --- |
| P01 | `app/pdf.py` | stable PDF page text and chunking | same PDF always yields the same page/chunk IDs |
| P02 | `app/openrouter.py` | schema-validated OpenRouter boundary | mocked response parses or fails predictably |
| P03 | `app/normalize.py` | unit and amount canonicalization | `7,225 INR Cr` equals `72.25 INR Bn` |
| P04 | `app/contracts.py`, `contracts/domain.json` | domain model snapshots | fields match `PROJECT_SPEC.md` exactly |
| P05 | `app/db.py` | SQLite persistence | insert/read/update round-trip works |
| P06 | `app/pipeline.py` | extract-to-failure orchestration | bad chunks are recorded, not silently dropped |
| P07 | `app/demo.py`, `fixtures/*` | offline demo records | exactly four showcase outcomes load |
| P08 | `app/main.py` | HTTP API | all endpoints in `PROJECT_SPEC.md` respond |
| P09 | `app/static/index.html` | semantic shell and tab layout | no JS framework, every panel mounts |
| P10 | `app/static/styles.css` | dark responsive presentation | desktop rail and mobile stacking remain usable |
| P11 | `app/static/app.js` | fetch/poll/render flow | upload polls jobs and renders safe text nodes |
| P12 | `tests/*` | regression coverage | `pytest` passes with no network key |
| P13 | `README.md`, `docs/demo-script.md` | reviewer runbook | reviewer can reproduce the demo quickly |

## Execution rule

One pack owns the named file boundary. It may read any project file but must not modify another pack's owned files. Before marking a pack `DONE`, it adds or updates the stated deterministic check.
