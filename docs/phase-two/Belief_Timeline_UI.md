# Belief timeline and questions (P2-11)

[Issue #35](https://github.com/dhruvkshah75/groundtruth/issues/35) · Implementation in progress; implementation PR not yet opened.

## Dependencies

The workspace uses temporal ingestion and summaries from [P2-05](Temporal_Belief_Ingestion.md) (merged in `9add3df`), temporal questions from [P2-08](Temporal_Questions.md) (`13c7ffe`), the [P2-09 simulation API](Simulation_API.md) (`619e420`) and the [P2-10 workspace](Simulation_Workspace.md) (`38ff50e`).

## Current behavior

Load the warehouse in the Simulation tab, select an entity or route using the question panel or the audience map, and expand **Observation and revision timeline**. The belief panel requests `POST /api/simulation/evidence` with the current `run_id`, `generation`, and `entity_id`. The server checks the run identity, then reads the existing observation ledger, audit chains and P2-05 summary under the session lock. The response is limited to 40 observations and 100 facts and events. The panel presents capture times in simulation seconds, storage times separately, and the recorded policy reason and rule. Raw records remain expandable.

Stored station beliefs are not necessarily current positions. Verification, visibility, freshness and last-seen data come from the ingestion summary. A map selection sends only an entity name to the question flow; it never adds audience coordinates to the question. The existing belief graph and its scenario-map relationships remain intact. A graph selection shares focus with the simulation when both show the same run; selecting a timeline revision can focus its predecessor or successor in the graph.

Questions use `/api/ask`. The server includes the specific answer in the response as well as returning the session snapshot; the question panel checks the captured run and current incarnation before displaying it. The response keeps its capture time as the simulation continues. Previous responses from this run can be opened in the question-history dialog. A run or session change resets the focused panel.

If the provider rejects the grounded explanation for a temporal-history query because its request is too large (HTTP 413), the agent returns its already-evaluated evidence timeline and marks the response as a deterministic fallback. It does not silently substitute an answer for other provider errors. Provider diagnostics are kept out of the web response; size, rate and availability failures have short messages, and the question remains editable for retry.

## Known limitations and remaining validation

This is an intermediate handoff. The complete real-backend acceptance matrix—including hidden relocation, stale and unavailable observations, selection of both predecessor and successor, delayed receipt and slow answers across a reset—still needs browser-level verification. Observation retrieval is capped at 1,000 ledger samples before the display limit; pagination for longer runs is not implemented. A standalone relevant graph view inside the simulation tab has not been added; the existing graph can be opened alongside it.

## Checks

With local provider configuration excluded, `python -m pytest -q` passed 664 tests in an isolated copy using the project virtual environment. `ruff check .`, `ruff format --check .`, `npm run build`, `npm run test:graph` and `npm run test:display` passed. `npm run test:simulation` passed against the offline backend at 1440, 1024, 768 and 390 px, including evidence selection, question history, a provider error and timeline-to-graph focus. P2-14 should use actual run evidence rather than audience coordinates when validating the final demo.
