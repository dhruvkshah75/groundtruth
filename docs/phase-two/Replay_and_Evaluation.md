# Run Export, Dual Replay, and Ten Epistemic Scenarios (P2-12 / WD-05)

[Issue #36](https://github.com/dhruvkshah75/groundtruth/issues/36)

This component provides reproducible evidence that GroundTruth Phase 2 is epistemic, grounded, and time-aware. It delivers versioned run export, dual-mode replay engine (`evidence_only` and `world_rerun`), ten automated epistemic evaluation scenarios, production integration for `GET /api/simulation/export`, and a deterministic CLI report generator.

---

## 1. Prerequisites and Merged Dependencies

This work builds on merged Phase 2 foundations:
- **P2-01 ([#21](https://github.com/dhruvkshah75/groundtruth/issues/21), PR [#26](https://github.com/dhruvkshah75/groundtruth/pull/26))**: Shared simulation contracts and warehouse fixtures (`ScenarioDefinition`, `ObservationSample`, `Pose`, `Point`).
- **P2-02 ([#22](https://github.com/dhruvkshah75/groundtruth/issues/22), PR [#28](https://github.com/dhruvkshah75/groundtruth/pull/28))**: Deterministic warehouse simulation engine, tick advancement, collision checking, and replay checkpoints.
- **P2-03 ([#23](https://github.com/dhruvkshah75/groundtruth/issues/23), PR [#30](https://github.com/dhruvkshah75/groundtruth/pull/30))**: Viewpoint-limited camera, occlusion, and timestamped warehouse sensors (`WarehouseSensors`).
- **P2-04 ([#24](https://github.com/dhruvkshah75/groundtruth/issues/24), PR [#39](https://github.com/dhruvkshah75/groundtruth/pull/39))**: SQLite observation ledger, normalized detections, and run-scoped temporal queries.
- **P2-05 ([#27](https://github.com/dhruvkshah75/groundtruth/issues/27), PR [#40](https://github.com/dhruvkshah75/groundtruth/pull/40))**: Temporal belief ingestion service (`TemporalBeliefIngestionService`), station zones, static route corridors, and auditable revisions.
- **P2-06 ([#29](https://github.com/dhruvkshah75/groundtruth/issues/29), PR [#41](https://github.com/dhruvkshah75/groundtruth/pull/41))**: Deterministic robot patrol controller with front-LiDAR reactive stopping and resumption.
- **P2-07 ([#31](https://github.com/dhruvkshah75/groundtruth/issues/31), PR [#43](https://github.com/dhruvkshah75/groundtruth/pull/43))**: Decoupled session runtime driving physical simulation and periodic sensor acquisition.
- **P2-08 ([#32](https://github.com/dhruvkshah75/groundtruth/issues/32), PR [#44](https://github.com/dhruvkshah75/groundtruth/pull/44))**: Temporal object questions (`object_location_query`) across 5 modes, strict anti-interpolation policy, and evidence citations.
- **P2-09 ([#33](https://github.com/dhruvkshah75/groundtruth/issues/33), PR [#45](https://github.com/dhruvkshah75/groundtruth/pull/45))**: Simulation HTTP API and export delegation boundary (`src/web/simulation_export.py`).

---

## 2. Architecture and Data Flow

```
                      +---------------------------------------+
                      |         ReplayBundle (v1)             |
                      |  - metadata (aliases, initial facts)  |
                      |  - static_map (zones, routes)         |
                      |  - recorded_samples (ObservationSample)|
                      |  - expected_evaluations (assertions)  |
                      +-------------------+-------------------+
                                          |
                   [Strict Audience Truth Segregation]
                                          |
                                          v
      +-----------------------------------+-----------------------------------+
      |                                                                       |
      v                                                                       v
[Mode: evidence_only]                                                [Mode: world_rerun]
- Fresh in-memory SQLite repo                                        - SimulationEngine initialized
- Ingest recorded envelopes via                                      - Sensor pipeline verified
  TemporalBeliefIngestionService                                     - Ingest recorded envelopes
- Zero simulator world access                                        - Query GroundedAgent
- Query GroundedAgent                                                - Compare expected outcomes
- Compare expected outcomes                                                   |
      |                                                                       |
      +-----------------------------------+-----------------------------------+
                                          |
                                          v
                        +------------------------------------+
                        |      ScenarioEvaluationLog         |
                        | - status, answer, revisions count  |
                        | - failure reason (if any)          |
                        | - pass/fail indicator (boolean)    |
                        +------------------------------------+
```

### Dual Execution Modes

1. **`evidence_only`**: Ingests recorded sensor observation envelopes into a fresh Tier 1 SQLite memory ledger without world or simulation engine access. This rigorously proves that beliefs, answers, and revisions are justified strictly by recorded evidence rather than hidden simulator state.
2. **`world_rerun`**: Advances the real runtime and patrol controller, captures fresh sensor samples, compares them with recorded samples, and evaluates the resulting evidence. The wrong-frame case includes a separately identified injected sample.

### Strict Audience Truth Segregation
The `AudienceTruthSection` in `ReplayBundle` contains ground-truth scenario definitions, true entity footprints, and presenter disruption events. By contract, audience truth is never supplied to `evidence_only` replay, intent planners, or answer synthesizers.

---

## 3. The Ten Epistemic Evaluation Scenarios

Each scenario defines an explicit conflict or uncertainty rationale and validates structured outcomes:

| # | Scenario Identifier | Core Epistemic Capability Tested | Expected Status |
| - | ------------------- | -------------------------------- | --------------- |
| 1 | `scenario_01_map_clear_vs_cart_blocked` | Static map clear vs live LiDAR obstacle; atomic belief revision to blocked with audit trail; subsequent clearance verification. | `grounded_conflict_resolved` / `audit_retrieved` |
| 2 | `scenario_02_visible_parcel_relocation` | Observed relocation between `packing_station` and `inspection_station`; revision successor link and mandatory transit caveat. | `verified` |
| 3 | `scenario_03_parcel_moves_outside_view` | Target omitted from subsequent broad camera scan; agent preserves last sighting at `packing_station` without guessing current coordinates. | `uncertain` |
| 4 | `scenario_04_shelf_occlusion_and_reacquisition` | Camera reports `target_not_visible` behind opaque shelf; subsequent viewpoint change restores verified belief. | `uncertain` -> `verified` |
| 5 | `scenario_05_two_similar_parcels` | Ambiguous reference ("the box") matching multiple distinct objects (`box_01`, `box_02`); agent refuses identity conflation and requests clarification. | `ambiguous` |
| 6 | `scenario_06_color_perspectives` | Isolates user expectation (red), live egocentric perception under yellow lighting (brown), and historical maintenance record (blue). | `perspectives_tracked` |
| 7 | `scenario_07_wrong_context_sample` | Foreign observation tagged with `room_999` / external frame is dropped by ingestion service; warehouse belief is preserved uncorrupted. | `verified` |
| 8 | `scenario_08_sensor_outage_and_recovery` | Camera reports `sensor_unavailable` outage at 2000 ms; agent reports safe uncertainty while citing last confirmed observation. | `uncertain` |
| 9 | `scenario_09_delayed_older_observation` | Delayed older sample (500 ms) arrives out-of-order after newer sample (2000 ms); retained in history without regressing active belief. | `verified` |
| 10 | `scenario_10_hidden_movement_between_scans` | Movement occurred wholly between samples; agent answers `at 500 ms` query using latest applicable sample (t=0 ms) with strict anti-interpolation disclaimer. | `verified` |

---

## 4. Production Exporter & Endpoint Integration

### Exporter Implementation (`src/evaluation/exporter.py`)
Implements the P2-09 `SimulationExporter` protocol defined in `src/web/simulation_export.py`:

```python
class DefaultSimulationExporter:
    """Production replay exporter implementing SimulationExporter protocol (P2-12)."""

    def __init__(self, session_lookup: Callable[[str], Any]) -> None:
        self._session_lookup = session_lookup

    def export(self, target: SimulationExportTarget) -> SimulationExportBundle:
        """Export run-scoped evidence into a validated SimulationExportBundle."""
        ...
```

### Redaction and Security
`DefaultSimulationExporter` guarantees that:
- Secrets, `.env` variables, and API keys are never included in exported bundles.
- Internal SQLite filesystem paths and unrelated conversation histories are excluded.
- All exported data is strictly run-scoped up to the requested simulation time boundary.

### Server Wiring (`src/web/server.py`)
In `main()`, `DefaultSimulationExporter` is wired directly into `AgentService`, providing live functionality for `GET /api/simulation/export`.

---

## 5. Command-Line Runner (`src/evaluation/cli.py`)

The evaluation runner supports both programmatic and CLI usage:

```bash
# Run all 10 scenarios in both replay modes and generate reports
uv run python -m src.evaluation.cli --mode both --output-dir artifacts/evaluation

# Run a specific scenario in evidence-only mode
uv run python -m src.evaluation.cli --scenario scenario_02_visible_parcel_relocation --mode evidence_only --output-dir /tmp/groundtruth-evaluation

# Validate and replay an external bundle file
uv run python -m src.evaluation.cli --bundle path/to/replay_bundle.json --mode both --output-dir /tmp/groundtruth-evaluation

# Replay an unchanged API export with a JSON array of ExpectedQueryEvaluation records
uv run python -m src.evaluation.cli --bundle export.json --expectations expectations.json --output-dir /tmp/groundtruth-evaluation
```

### Outputs Generated:
- `artifacts/evaluation/evaluation_summary.json`: Complete machine-readable logs and outcomes.
- `artifacts/evaluation/evaluation_report.md`: Readable Markdown summary table and per-scenario audits.

Exit code is `0` when all evaluations pass; non-zero (`1`) if any assertion or validation fails. The report includes a one-time map-only route comparison, using the initial stored map claim and the first recorded LiDAR sample; it is not an aggregate accuracy metric. A physical replay test compares outcomes at 0.25×, 0.5× and 1×. Expected locations must appear in the answer and agree with any structured location assessment. `expected_evidence_sample_ids` checks ledger persistence; `expected_response_evidence_sample_ids` separately requires the response trace to cite those samples.
An API export contains recorded observations and presenter events. Supply the expected query
evaluations with `--expectations`; the CLI validates the export and builds the replay bundle.
Bundles with delayed captures require an explicit `after_sample_sequence` for every query.

---

## 6. Verification and Test Results

### Targeted Evaluation Suite
```bash
uv run --locked pytest tests/unit/evaluation tests/integration/evaluation -v
```
The full suite below includes the evaluation tests.

### Complete Repository Verification
```bash
uv run --locked pytest -q
```
**Results:** `765 passed in 25.49s`. Both replay modes passed all 30 evaluations across the ten scenarios. CI regenerates the reports and compares them with the checked-in copies.

### Code Quality and Formatting
```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
```
**Results:** All checks passed cleanly.

---

## 7. Downstream Handoff

- **WD-06 ([#51](https://github.com/dhruvkshah75/groundtruth/issues/51))**: Use the generated 10 scenario evaluation logs (`artifacts/evaluation/evaluation_summary.json` and `evaluation_report.md`) for rehearsal readiness and demo preset verification.
- **WD-07 ([#38](https://github.com/dhruvkshah75/groundtruth/issues/38))**: Bundle the evaluation logs and replay artifacts with the 3-minute recording runbook for final submission packaging.
