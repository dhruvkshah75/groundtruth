# Temporal Object Questions and Evidence-Grounded Explanations (P2-08)

- **Issue:** [#32: [P2-08] Add temporal object questions and evidence-grounded explanations](https://github.com/dhruvkshah75/groundtruth/issues/32)
- **Pull Request:** `feat/p208`
- **Lane:** Procedural Reasoning / Tier 2 Epistemic Integration & Natural Language QA
- **Status:** Complete
- **Prerequisites:**
  - Issue [#21](https://github.com/dhruvkshah75/groundtruth/issues/21) Shared Simulation Contracts (`src/contracts/simulation.py`, `src/contracts/models.py`)
  - Issue [#24](https://github.com/dhruvkshah75/groundtruth/issues/24) Observation Ledger & Temporal Queries (`src/declarative/memory_repository.py`)
  - Issue [#27](https://github.com/dhruvkshah75/groundtruth/issues/27) Temporal Belief Ingestion (`src/procedural/temporal_belief_ingestion.py`)
  - Issue [#31](https://github.com/dhruvkshah75/groundtruth/issues/31) Deterministic Tool-Calling & ReAct Grounding (`src/providers/`)

---

## 1. Overview and Architecture

The **Temporal Object Questions and Explanations** milestone introduces an additive, non-breaking intent and execution pipeline that enables GroundTruth to answer time-aware questions about physical entities in the simulated warehouse environment.

Unlike standard LLM agents that hallucinate intermediate coordinates, invent unrecorded movements, or smooth over sensor dropouts, GroundTruth's procedural epistemic architecture enforces **strict physical grounding**:
1. All location statements cite explicit, tamper-evident observation ledger evidence (`sample_id`, `sim_time_ms`, coordinate points, observer robot, camera state).
2. The agent refuses to interpolate between discrete sightings.
3. Unobserved intervals, sensor outages (`ObservationUnavailable`), and future-dated queries are explicitly acknowledged as uncertain.
4. When physical movement between stations is detected, transitions are audited alongside a mandatory physical transit caveat.

```mermaid
flowchart TD
    subgraph User Interaction
        UQ["User Query: 'Where was parcel_01 at 5 seconds?'"] --> AG["GroundedAgent.ask()"]
    end

    subgraph Tier 2: Intent Classification & Tool Calling
        AG --> PROV["RuleBasedIntentProvider / GroqReActProvider"]
        PROV -->|IntentRequest| IR["Intent: 'object_location_query'\nmode: 'at_time'\nat_sim_time_ms: 5000\nentity_id: 'parcel_01'"]
    end

    subgraph Tier 2: Procedural Planning
        IR --> PB["PlanBuilder._build_object_location_query()"]
        PB --> EP["ExecutionPlan\ntemporal_operations: [TemporalObservationOperation]"]
    end

    subgraph Tier 1: Declarative Ledger Queries
        EP --> PE["PlanExecutor.execute()"]
        PE -->|query_observations| MR["MemoryRepository / GraphSyncedRepository"]
        MR -->|SQLite: observation_samples & detections| PE
        PE --> RES["PlanExecutionResult\ntemporal_results: [TemporalObservationExecutionResult]"]
    end

    subgraph Tier 2: Epistemic Evaluation & Grounding
        RES --> EE["EpistemicEvaluator._evaluate_temporal_object_query()"]
        EE -->|Status & Grounded Explanation| EV["EpistemicEvaluation\nstatus: 'verified' | 'uncertain' | 'no_data'\ntelemetry: [SensoryTelemetry]"]
    end

    subgraph Tier 2: Answer Synthesis
        EV --> AS["AnswerGenerator._synthesize_answer()"]
        AS --> RESP["AgentResponse\nanswer: Grounded Explanation\noperations_trace, execution_summary"]
    end
```

### Strict Three-Tier Separation
- **Tier 1 (Declarative):** Persistence of immutable observation envelopes and normalized detections in SQLite; indexed by `run_id`, `sim_time_ms`, `sensor_type`, and `entity_id`.
- **Tier 2 (Procedural):** Intent parsing, plan construction, deterministic epistemic evaluation, and synthesis guards. Procedural components consume memory protocols without concrete SQLite imports.
- **Tier 3 (Sensorimotor):** Simulation clock and warehouse sensors generating validated observation envelopes (`StoredObservationSample`, `CameraScan`).

---

## 2. The Five Temporal Query Modes

The `object_location_query` intent supports five distinct temporal analysis modes:

| Mode | Natural Language Prompt Examples | Primary Grounding Ledger Query | Output & Truth Value |
| :--- | :--- | :--- | :--- |
| `current` | *"Where is parcel_01 right now?"*, *"What is the current position of blue box?"* | Latest camera scan and latest positive sighting of entity | Returns verified coordinates if currently detected; reports uncertainty if camera has outage or scan lacks entity. Falls back to stored memory fact if no camera scans exist. |
| `last_seen` | *"When and where was parcel_01 last seen?"*, *"Where was the red parcel last spotted?"* | Most recent positive sighting sample in the run | Cites last confirmed `sim_time_ms`, coordinates $(x, y)$, observer robot, sample age in ms, and subsequent scan verification context. |
| `at_time` | *"Where was parcel_01 at 5 seconds?"*, *"Check location of parcel_02 at 12000 ms"* | Latest sighting with `sim_time_ms <= at_sim_time_ms` + run horizon | Strictly validates `at_sim_time_ms >= 0`. Rejects future queries beyond run horizon. Reports exact observation at or prior to target time. Prohibits position interpolation. |
| `history` | *"Show me the history of parcel_01"*, *"List all sightings of box_02"* | Chronological list of positive detections across entire run | Formats chronological timeline of every sighting, timestamp, coordinates, observer robot, and sample ID. |
| `changes` | *"Has parcel_01 moved?"*, *"Where was parcel_01 previously?"*, *"Show movements of parcel_02"* | Sequential station-zone transitions and coordinate shifts | Detects station relocations and coordinate changes. Formats transition intervals, reasons from belief revision audit trail, and appends the mandatory transit caveat. |

---

## 3. Epistemic Grounding & Anti-Interpolation Semantics

### A. Strict Anti-Interpolation Principle
When evaluating an `at_time` query for a timestamp $T$ between two observations ($t_1 < T < t_2$), or after an observation ($t_1 \le T$ with no later observation):
- The agent reports the last verified sighting at or prior to $T$ ($t_1$).
- The agent explicitly states: *"No camera observation recorded at exactly T ms. Position at T ms cannot be interpolated."*
- Intermediate coordinates between sample points are **never mathematically interpolated or hallucinated**.

### B. Time Semantics & Future-Dated Queries
- Target timestamps must be non-negative (`at_sim_time_ms >= 0`). Requests with negative timestamps fail schema validation (`ValueError`).
- If $T > t_{\text{latest\_run}}$, the evaluation returns `status="uncertain"`:
  *"The requested simulation time T ms is in the future relative to available observation ledger evidence (latest recorded: ... ms). Location cannot be verified."*
- Simulation time is strictly independent of wall-clock time and playback rate.

### C. Sensor Outage Resilience
When a sensor outage occurs (`ObservationUnavailable`), the failure envelope is preserved in the observation ledger:
- In `current` mode: The agent does not fabricate absence or claim the parcel disappeared; it reports: *"Current location cannot be freshly verified because the camera reported an outage at simulation time T ms (outage_reason). Last confirmed sighting was at ... ms."*
- In `last_seen` mode: The last confirmed sighting is returned with subsequent outage context noted in the verification summary.

### D. Mandatory Physical Transit Caveat
Whenever location transitions between distinct stations or coordinates are detected in `changes` mode, the explanation concludes with GroundTruth's mandatory physical transit disclaimer:
> *"Note: Two sightings at distinct locations demonstrate that a transition occurred, but do not prove continuous path, exact transit duration, or physical relocation cause."*

### E. Entity Resolution and Ambiguity Clarification
- Natural language aliases (e.g., *"red parcel"*, *"blue box"*) are resolved using known graph facts.
- If an alias is ambiguous (e.g., multiple parcels share color or descriptor) or cannot be resolved, the agent does not guess an arbitrary entity. It returns a clarification request (`status="requires_clarification"`).
- Out-of-scope requests (e.g., controlling robots or predicting future routes) are gracefully rejected.

---

## 4. End-to-End Implementation Contracts

### Contract Additions (`src/contracts/models.py`)
```python
IntentType = Literal[
    ...,
    "object_location_query",
]

TemporalQueryMode = Literal[
    "current",
    "last_seen",
    "at_time",
    "history",
    "changes",
]


class IntentRequest(BaseModel):
    intent: IntentType
    entity_id: str | None = None
    mode: TemporalQueryMode = "current"
    at_sim_time_ms: int | None = None
    run_id: UUID | None = None
    # ...
```

### Tool Definition (`src/providers/tools.py`)
The tool `object_location_query` is exposed to LLM function calling with detailed descriptions of all 5 modes, required entity arguments, and non-negative integer parameter validation.

### Procedural Pipeline
1. **`RuleBasedIntentProvider`** (`src/procedural/rule_based_provider.py`):
   - Regex-free and regex-powered classification for temporal modes, time units (`seconds`, `s`, `ms`), and entity aliases.
   - Extracts `mode` and `at_sim_time_ms`.
2. **`PlanBuilder`** (`src/procedural/plan_builder.py`):
   - Emits `TemporalObservationOperation` instances configured for the specific mode.
   - For `at_time` mode, fetches both historical sightings up to $T$ and the run horizon to evaluate future boundaries.
3. **`PlanExecutor`** (`src/procedural/plan_executor.py`):
   - Queries `self._memory.query_observations(...)` to retrieve stored observation envelopes and detections.
   - Returns typed `TemporalObservationExecutionResult`.
4. **`EpistemicEvaluator`** (`src/procedural/epistemic_evaluator.py`):
   - Reconstructs physical timelines, checks camera outages, verifies coordinates, audits revision trails for station changes, and formats tamper-evident explanations.
5. **`GroundedAgent`** (`src/agent.py`):
   - Coordinates intent proposal, plan building, execution, evaluation, and answer delivery with complete operation traces.

### F. P2-07 Session Runtime & Request-Owned Evidence Integration
Following the completion of Issue [#43 / P2-07](https://github.com/dhruvkshah75/groundtruth/issues/43):
- **Request-Owned Run and Capture Time:** `GroundedAgent.capture(...)` accepts `capture_time_ms` directly from the request's session snapshot (`SimulationRuntime.read().status.sim_time_ms`), ensuring evidence evaluation uses the exact simulation clock tick when the user asked the question.
- **Freshness Threshold (1,000 ms):** In `current` mode, sightings are validated against `capture_time_ms`. Sightings older than 1,000 ms are marked `uncertain` rather than verified.
- **Sequence and Timestamp Tie-Breaking:** When comparing sightings against camera scans or outages, the evaluator applies `_is_strictly_later(a, b)` using both `sim_time_ms` and `sample_sequence`. An equal-time non-detection or outage correctly supersedes an earlier sighting at the same timestamp.
- **Cross-Run Reset Isolation:** `MemoryRepository.get_latest_run_id()` orders by insertion order (`rowid DESC LIMIT 1`) rather than simulation time, and implicit queries prioritize the request's active `sensors.run_id`, preventing resets from exposing previous run data.
- **Canonical Alias Resolution:** Canonical entity IDs resolved from the execution result are used across all 5 temporal modes to match detections. Missing detections yield uncertainty/error instead of fabricating `(0, 0)` coordinates. Pronouns such as `it` are matched strictly with word boundaries so legacy robot pose questions (e.g. `What is your position?`) are not misclassified as object queries.
- **Physical Position Changes vs. Observer Movement:** In `changes` mode, object positions are compared across compatible reference frames (`pos1 != pos2`). Movement of the observer robot between stations alone does not invent object relocation. Observer location is preserved solely as provenance.
- **Stored Belief Run Provenance:** Stored location beliefs without matching run provenance cannot establish current position for a run lacking sightings, preventing cross-run belief leakage.
- **Robust Temporal Parsing:** Natural language word numbers (e.g., "seven seconds") are parsed into exact milliseconds. Unrecognized time phrases are rejected at planning rather than falling through to current mode, and relative terms (e.g., "previously", "earlier") correctly route to `changes`/`history`. Negative values (including fractional values like `-0.1 ms`) are rejected before integer truncation.

---

## 5. Verification and Test Results

The implementation is verified by 44 targeted unit and integration tests in [`tests/unit/procedural/test_temporal_questions.py`](file:///c:/Users/rajat/groundtruth/tests/unit/procedural/test_temporal_questions.py), alongside GroundTruth's complete regression suite (628 tests).

### Test Categories
1. **Mode-Specific Grounding Tests:**
   - `test_current_mode_fresh_positive_sighting`: Confirms verified status with exact coordinates.
   - `test_current_mode_resilient_to_subsequent_camera_outage`: Verifies uncertainty on camera outage.
   - `test_current_mode_resilient_to_subsequent_empty_scan`: Verifies uncertainty when later scan omits entity.
   - `test_current_mode_no_sightings_with_stored_belief`: Tests fallback when no camera scans exist.
   - `test_last_seen_mode_with_subsequent_status`: Validates timestamp, coordinates, age calculation against capture time, and subsequent scan status.
   - `test_at_time_mode_preceding_sighting_no_interpolation`: Proves anti-interpolation guarantee.
   - `test_at_time_mode_future_time_uncertain`: Rejects queries beyond recorded horizon.
   - `test_history_mode_chronological_timeline`: Validates full chronological detection sequence.
   - `test_changes_mode_visible_station_relocation`: Verifies station transition detection with revision audit reasons and mandatory transit caveat.
   - `test_changes_mode_no_transition`: Verifies stationary object explanation.
2. **Robustness & Edge Cases:**
   - `test_at_time_mode_negative_time_rejected`: Validates Pydantic schema validation.
   - `test_alias_ambiguity_returns_clarification`: Validates entity resolution safety.
   - `test_missing_alias_returns_unrecognized`: Validates missing alias error handling.
   - `test_adversarial_synthesis_falls_back_to_deterministic_grounding`: Confirms deterministic defense against LLM hallucination.
   - `test_out_of_order_sample_delivery_chronological_ordering`: Tests deterministic chronological ordering on out-of-order sample arrivals.
   - `test_prompt_dataset_coverage`: Executes natural language prompts across all 5 modes end-to-end.
3. **P2-07 Integration & PR Review Tests:**
   - `test_aliases_across_all_temporal_modes`: Tests canonical entity resolution across `current`, `last_seen`, `at_time`, `history`, and `changes`.
   - `test_missing_detection_yields_uncertainty_or_no_data_never_invented_coords`: Verifies missing detections produce uncertainty/no-data without invented coordinates.
   - `test_unspecified_object_reference_does_not_default_to_parcel_01`: Verifies ungrounded questions like "Where is it?" do not default to `parcel_01`.
   - `test_equal_time_sequence_tie_outage_or_non_detection_marks_uncertain`: Verifies tie-breaking on same timestamp using `sample_sequence`.
   - `test_capture_time_stale_evidence_marks_current_uncertain_and_advances_last_seen_age`: Verifies 1,000 ms freshness threshold and capture time reference.
   - `test_cross_run_reset_isolation`: Verifies that resetting at 20s followed by a new run at 0s does not leak evidence across runs.
   - `test_legacy_robot_pose_questions_not_classified_as_object_queries`: Verifies word-boundary matching for `it` so robot pose questions like "What is your position?" preserve `current_robot_pose`.
   - `test_observer_location_change_does_not_invent_object_movement`: Verifies observer movement between stations does not invent object movement when coordinates are identical.
   - `test_stored_belief_scoped_to_run_earlier_run_cannot_establish_location`: Verifies stored beliefs from older runs cannot establish current location for a new run.
   - `test_time_words_and_historical_intent_preserved`: Verifies parsing of word numbers (e.g., "seven seconds"), mapping "previously" to changes, and rejecting unparseable times.
   - `test_negative_fractional_time_rejected`: Verifies negative fractional times (e.g., `-0.1 ms`) are rejected before integer truncation.

### Verification Run
```text
uv run --locked pytest tests/unit/procedural/test_temporal_questions.py -v
44 passed in 1.94s

uv run --locked pytest -q
628 passed in 17.87s

uv run --locked ruff check .
All checks passed!

uv run --locked ruff format --check .
138 files already formatted
```
