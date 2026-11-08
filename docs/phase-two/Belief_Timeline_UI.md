# Belief timeline and questions (P2-11)

[Issue #35](https://github.com/dhruvkshah75/groundtruth/issues/35) ·
[Pull request #47](https://github.com/dhruvkshah75/groundtruth/pull/47) ·
[Implementation branch](https://github.com/dhruvkshah75/groundtruth/tree/codex/p2-11-belief-timeline).

## Dependencies

All four prerequisites are closed and their integrated interfaces are present on the implementation branch:

| Issue | Interface | Merged code |
| --- | --- | --- |
| [P2-05 / #27](https://github.com/dhruvkshah75/groundtruth/issues/27) | [Temporal ingestion and belief summaries](Temporal_Belief_Ingestion.md) | `9add3df` |
| [P2-08 / #32](https://github.com/dhruvkshah75/groundtruth/issues/32) | [Temporal questions and grounded answers](Temporal_Questions.md) | `13c7ffe` |
| [P2-09 / #33](https://github.com/dhruvkshah75/groundtruth/issues/33) | [Run-scoped simulation HTTP API](Simulation_API.md) | `619e420` |
| [P2-10 / #34](https://github.com/dhruvkshah75/groundtruth/issues/34) | [Warehouse simulation workspace](Simulation_Workspace.md) | `38ff50e` |

## Try it locally

```bash
cd frontend
npm ci
npm run build
cd ..
GROUNDTRUTH_PROVIDER_MODE=offline uv run --locked python -m src.web.server
```

Open `http://127.0.0.1:8765/simulation` and load the warehouse preset. Drag **Belief graph** beside Simulation. Select **Parcel 01** and play. Open **Observation and revision timeline** and inspect the move from packing to inspection. **Focus earlier belief** selects the predecessor in the stored graph; **Focus updated belief** selects its successor. The selection is retained if the graph is opened afterward. Select **Route A** to inspect the cart's blockage and later clearance. The graph's **Fact list** also supports keyboard selection and sends focus back to the evidence panel and map.

In **Ask about the run**, use one of the suggested questions or type e.g. “When was parcel 01 last seen?” or “Show the history of parcel 01.” The answer keeps its original capture time while playback continues. **Show all questions** opens the captured answers for the loaded run.

## Data flow and semantics

- The audience map is physical truth. Selecting a map object supplies an entity reference to the question form. Its audience coordinates never enter the question. Camera position, coordinate frame and apparent color come from actual detections; LiDAR distance comes from the matching route observation. Existing user expectations and historical maintenance claims remain separately attributed in the shared answer renderer.
- `POST /api/simulation/evidence` takes `{ "expected": { "run_id": "...", "generation": 1 }, "entity_id": "parcel_01" }`. Use the identity from `GET /api/simulation/state`. The service validates it under the session lock, then reads P2-05 summaries, the observation ledger and audit chains. The panel separates stored claims from current verification, visibility, freshness and sensor availability. The timeline orders observations and revisions by simulation capture time. Ledger receipt is a separate wall-clock timestamp; its difference from the simulation epoch is not a latency measurement. Audit reasons and rules come from stored events, and raw records can be expanded.
- The existing graph reads `GET /api/state` and keeps surviving nodes in place when real simulation relationships change. The graph highlights changed facts using persisted revisions and audit events. Audience motion alone does not change any graph relationship. Scenario map nodes and room relationships remain visible.
- Simulation questions use `POST /api/ask` with `question` and the optional `expected` identity above. Normal chat can omit `expected`. The backend checks the requested identity before executing, captures evidence without holding playback during answer generation, and discards an answer if the session or run changes during execution. The response includes the request-specific `answer` and its `simulation_evidence` capture metadata. The workspace also checks that identity before showing the answer. A new run or session clears its focused evidence and inline question view. No schema migration is required.
- A provider rejection for an oversized history request triggers one retry with the evaluated sightings and without redundant sensor payloads or sample identifiers. The answer still goes through grounding review. If the retry or review fails, the question stays editable and the UI shows a short error. Markdown tables render in the shared chat renderer with horizontal scrolling when needed and sample ID columns omitted.

The timeline displays at most 40 observations and 100 facts and audit events from a query capped at 1,000 candidate samples. Raw receipt times are available for samples; full-ledger pagination is outside this workspace. Failed or stale requests are surfaced without inventing readings, and a previous run's answers do not appear in the current run's inline history.

## Verification and handoff

- `uv run --locked pytest -q`: 666 passed in a clean source copy without local provider configuration. The [HTTP tests](../../tests/integration/test_simulation_api.py) include late receipt ordered by capture time and stale ask identity rejection. Existing tests cover separately attributed color perspectives and ingestion policy.
- `uv run --locked ruff check .`, `uv run --locked ruff format --check .`, and `git diff --check` passed.
- `npm ci`, `npm run build`, `npm run test:graph`, and `npm run test:display` passed in `frontend`.
- `npm run test:simulation` passed against the offline backend at 1440, 1024, 768 and 390 px. The [browser test](../../frontend/scripts/check-simulation-browser.mjs) covers visible and unseen relocation, outage and stale sightings, both route transitions, raw records, keyboard graph selection, Markdown history tables, and delayed answers during playback and across a reset. Start the backend as above and set `CHROME_PATH` to an installed Chrome executable if Playwright's Chromium is unavailable.

For P2-14, use the same run's sensor evidence when validating the live demo: compare a visible move with an unseen one, inspect the earlier and updated claims, and ask a temporal question during playback. Use the capture label on the answer when comparing it with the live clock.
