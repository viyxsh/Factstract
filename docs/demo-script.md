# Demo script

Use this as the reviewer runbook once the API and UI are wired up.

1. Start the app from the repository root.

   ```bash
   source .venv/bin/activate
   uvicorn app.main:app --reload
   ```

2. Open the dark web UI served at `http://127.0.0.1:8000`.
3. Click **Load demo** in the left rail to insert the deterministic
   showcase records without invoking the LLM.
4. Check the four top counters (Facts, Documents, Relationships,
   Failures) reflect the loader output: 6 / 3 / 3 / 1.
5. Switch to the **Relationships** tab and confirm the three coloured
   cards are visible: green corroborates, red contradicts, amber
   reconciles.
6. Switch to the **Evidence** tab and click any fact to load the
   evidence drawer. The drawer must show the verbatim quote and source
   page.
7. Switch to the **Known failures** tab and confirm the explicit
   failure record from the demo loader is present.
8. (Optional) Upload a PDF from the rail to watch a real job run. The
   banner shows progress until the job is `done` or `failed`.

## Notes

- Demo mode does not require `GEMINI_API_KEY`.
- Uploads surface a job state while processing.
- Every visible quote comes from stored evidence, not synthesized UI
  text.
- `python -m pytest` runs the suite without a network key.
