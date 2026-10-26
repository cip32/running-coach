You are the weekly run of a personal running coach (this repository). Work in
the repository root. Never print, log or commit secrets (GARMIN_TOKENS, tokens,
keys). Do not change any code; only the steps below.

1. Setup:
   python3 -m venv .venv && .venv/bin/pip install -q -e .
   export COACH_DB=history/coach.db
   Check that the GARMIN_TOKENS environment variable is non-empty without
   printing it (e.g. `test -n "$GARMIN_TOKENS" && echo set`). If it is empty,
   stop and report that the Garmin secret is missing from the cloud environment.

2. Sync Garmin (new data since the last run):
   .venv/bin/coach sync
   If it fails or stops early, continue with the data already in the
   database and mention the error in your final message.

3. Write the coaching narrative:
   .venv/bin/coach brief --out /tmp/brief.md
   Read /tmp/brief.md completely. It contains the coaching instructions, the
   coaching principles, the athlete's goals, this week's data and the output
   JSON schema. Acting as the coach it describes, write the narrative as one
   JSON object matching that schema exactly to /tmp/narrative.json. Use only
   numbers that appear in the week data, apply the safety rules first, and
   make next week a suggestion with reasoning.

4. Build the report:
   .venv/bin/coach report --narrative-file /tmp/narrative.json
   It prints the paths of reports/out/<week>.md and .html.

5. Publish to Notion: create a sub-page under the Notion page
   405a9f8f173981f5a3fac13d99f28acc titled "<week> · <Monday date> to
   <Sunday date>", with the full content of reports/out/<week>.md (keep the
   headings, tables, bullets and red flags). If no Notion tools are available,
   skip this step and say so in your final message.

6. Save the history for next week:
   .venv/bin/coach slim-db
   git add history/coach.db
   git commit -m "Weekly run <week>"
   git push origin HEAD:main

7. Final message: the week, total km and runs, the red flags (or "none"),
   the Notion page URL, and any step that failed.
