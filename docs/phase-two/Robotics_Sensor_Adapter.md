# P2-13 Recorded Robotics Sensor Adapter

`RecordedRoboticsSensorAdapter` in `src/sensorimotor/robotics_adapter.py` defines the official integration boundary between external robotics platforms (such as ROS 2 bag files, mobile robot telemetry logs, and edge camera/LiDAR loggers) and the GroundTruth epistemic verification system.

It translates external telemetry messages into typed, validated [P2-01 `ObservationSample`](Shared_Simulation_Contracts.md) envelopes without requiring live robotics hardware connectivity or access to the headless simulator engine.

---

## 1. Architectural Role and Principles

```
  +--------------------------------------------------------+
  |              External Robotics Platform               |
  |  (ROS 2 bag / JSON telemetry / mobile robot logs)     |
  +---------------------------+----------------------------+
                              |
                              v  ExternalRoboticsRecord (JSON / Model)
  +--------------------------------------------------------+
  |         RecordedRoboticsSensorAdapter (P2-13)          |
  |  - Step-Bound Playback (no search-ahead or dropped msgs)|
  |  - Coordinate & Heading Transforms (SE(2) Kinematics)  |
  |  - Deterministic Clock Calibration (UTC/ms -> sim_ms)  |
  |  - Strict Payload Validation (no fabricated clear/data)|
  |  - Upstream ID Preservation (uuid5 deterministic IDs)  |
  |  - Failure Normalization (typed ObservationUnavailable)|
  +---------------------------+----------------------------+
                              |
                              v  ObservationSample (P2-01 envelope)
  +---------------------------+----------------------------+
  |         Tier 1 SQLite Observation Ledger (P2-04)       |
  |  - Durable raw sample persistence & temporal indexing   |
  +---------------------------+----------------------------+
                              |
                              v  ObservationSample stream
  +--------------------------------------------------------+
  |      TemporalBeliefIngestionService (P2-05 / Tier 2)   |
  |  - Station zone resolution                             |
  |  - Deterministic belief revision & audit events        |
  |  - Active knowledge graph synchronization              |
  +--------------------------------------------------------+
```

### Core Principles:
- **No Simulator or Live Hardware Dependency:** The adapter runs entirely headless and offline. It translates recorded messages into P2-01 envelopes without requiring a running `SimulationEngine`.
- **Step-Bound Playback (No Search-Ahead):** Playback operates strictly at the current controlled step (`self._cursor`). Requesting an observation capability that was not recorded at the current step reports `ObservationUnavailable` at that step without advancing forward or dropping intervening sensor events (e.g., requesting a camera while the robot is recording LiDAR returns unavailable for the camera, preserving the LiDAR record).
- **Strict Separation from Belief Policy:** The adapter does not decide what the robot believes or write directly to databases. It produces standard `ObservationSample` envelopes, which are consumed by Tier 1 (`MemoryRepository`) and Tier 2 (`TemporalBeliefIngestionService`) through established public protocols.
- **Protocol Conformance:** Conforms to the `SensorSource` protocol:
  - `observe(request)`: Returns observation evidence without advancing simulation time or modifying the cursor.
  - `sample(request)`: Yields timestamped, validated `ObservationSample` envelopes, advancing the cursor per controlled step.
- **Deterministic and Idempotent:** Sample and observation IDs derive from the recording ID and message ID; targeted camera views additionally include the target. Replaying the same request produces identical IDs, while different views can be stored separately.

---

## 2. Versioned Record Schema (`ExternalRoboticsRecord`)

Defined in `src/sensorimotor/robotics_adapter_models.py`, `ExternalRoboticsRecord` is a versioned Pydantic contract (`schema_version: 1`).

### Schema Fields & Strict Validation:

| Field | Type | Description |
| :--- | :--- | :--- |
| `schema_version` | `Literal[1]` | Format version (currently `1`). |
| `recording_id` | `str` | Unique run/session identifier (e.g. `"rec_warehouse_001"`). |
| `message_id` | `str` | Message identifier unique within the recording (e.g. `"msg_0042"`). |
| `sequence` | `int` | Monotonically increasing message sequence index (`>= 0`). |
| `robot_id` | `str` | Identifier of the observing robot (e.g. `"robot_01"`). |
| `sensor_id` | `str` | Hardware sensor identifier (e.g. `"camera_front_rgb"`, `"lidar_front_2d"`). |
| `capability` | `str` | One of `"camera_detect"`, `"lidar_scan"`, `"robot_pose"`, `"ambient_light"`. |
| `captured_timestamp_utc` | `datetime \| None` | Timezone-aware ISO-8601 UTC timestamp of capture. |
| `captured_time_rel_ms` | `int \| None` | Relative milliseconds since recording epoch. |
| `source_frame` | `str \| None` | Coordinate frame of measurements. **Must be explicitly specified on available records; cannot default to warehouse_map.** |
| `robot_pose` | `Pose \| None` | Robot's location and heading at time of capture. **Required on available records; cannot be fabricated.** |
| `world_version` | `int` | Simulation or environment configuration version. |
| `detections` | `list[ExternalDetection] \| None` | Bounding boxes and positions for visual objects. Pass `[]` for an explicit empty scan; `None` represents missing data. |
| `lidar_measurement` | `ExternalLidarMeasurement \| None` | Front obstacle scan data (`lidar_scan`). Cannot be omitted on available scans (missing data is not treated as clear). |
| `light_measurement` | `ExternalLightMeasurement \| None` | Ambient light intensity and color cast (`ambient_light`). |
| `measurements` | `dict \| None` | Alternative LiDAR or light payload; all required measurement fields must be present. Camera records use `detections`. |
| `confidence` | `float \| None` | Detection confidence between `0.0` and `1.0`. **Must be explicitly provided on available records; cannot default to 1.0.** |
| `is_unavailable` | `bool` | Set to `true` when the sensor hardware failed or was blinded. |
| `unavailable_reason` | `str \| None` | `"sensor_unavailable"`, `"target_not_visible"`, or `"unsupported_capability"`. |
| `metadata` | `dict[str, Any]` | Optional platform-specific metadata (ROS topic, bag offset, etc.). |

---

## 3. Coordinate & Heading Frame Transforms (`TransformCalibration`)

Robotics hardware often captures data in egocentric sensor coordinates, and poses may be logged in local facility coordinates. GroundTruth requires observations and observer poses in canonical `warehouse_map` coordinates.

`TransformCalibration` provides exact transformations:
1. **`warehouse_map` (Canonical):** Identity transform; coordinates pass through unchanged.
2. **`robot_frame` / `robot_base` (Egocentric):**
   - Convention: `+y` is forward along the robot's heading; `+x` is lateral right.
   - Heading alignment:
     - `north`: `map_x = robot_x + dx`, `map_y = robot_y + dy`
     - `east`: `map_x = robot_x + dy`, `map_y = robot_y - dx`
     - `south`: `map_x = robot_x - dx`, `map_y = robot_y - dy`
     - `west`: `map_x = robot_x - dy`, `map_y = robot_y + dx`
3. **`base_link` (ROS REP 103 Standard):**
   - Convention: `+x` is forward along the robot's heading; `+y` is lateral left.
4. **Pose Kinematic Transforms (`transform_pose`):**
   - Transforms `(x_cm, y_cm)` translation and rotates cardinal `direction` heading by `rotation_deg`.
   - Rotated orientations must align with valid cardinal directions (`"north"`, `"east"`, `"south"`, `"west"`) or raise `UnknownFrameError`.
5. **Strict Frame Validation:**
   - If an unconfigured or unknown frame (e.g., `"uncalibrated_arm_link"`) is encountered, the adapter raises `UnknownFrameError` rather than guessing or silently relabelling coordinates.

---

## 4. Deterministic Clock Mapping (`ClockCalibration`)

GroundTruth tracks time in discrete simulation milliseconds (`sim_time_ms`) relative to an explicit timezone-aware `epoch_utc`.

`ClockCalibration` handles two capture modes:
- **Relative Milliseconds (`captured_time_rel_ms`):**
  - `sim_time_ms = captured_time_rel_ms`
  - `captured_at = epoch_utc + timedelta(milliseconds=sim_time_ms)`
- **Absolute UTC Timestamp (`captured_timestamp_utc`):**
  - `sim_time_ms = int(round((captured_timestamp_utc - epoch_utc).total_seconds() * 1000))`
  - `captured_at = captured_timestamp_utc`

### Rejection Guards:
- Negative relative milliseconds raise `UnmappableTimeError`.
- Capture timestamps occurring prior to `epoch_utc` raise `UnmappableTimeError`.
- Ambiguous timezone-naive timestamps raise `UnmappableTimeError`.

---

## 5. Traceability and Ledger Idempotence

To ensure external recordings can be reliably re-ingested or evaluated across multiple runs without state duplication:
- Deterministic IDs are computed via `uuid5`:
  ```python
  sample_id = uuid5(NAMESPACE_DNS, f"groundtruth:sample:{recording_id}:{message_id}")
  observation_id = uuid5(NAMESPACE_DNS, f"groundtruth:obs:{recording_id}:{message_id}")
  ```
- Storing or ingesting the same recording multiple times into `MemoryRepository` produces identical IDs and facts, ensuring complete idempotence.

---

## 6. End-to-End Ingestion Example

```python
from pathlib import Path
from src.declarative.memory_repository import MemoryRepository
from src.declarative.active_graph import GraphSyncedRepository
from src.procedural.temporal_belief_ingestion import TemporalBeliefIngestionService
from src.sensorimotor.robotics_adapter import RecordedRoboticsSensorAdapter
from src.contracts.models import FactQuery

# 1. Load recorded telemetry from JSON dataset
adapter = RecordedRoboticsSensorAdapter.from_file(
    "tests/fixtures/phase_two/robotics_recording.json"
)

# 2. Initialize Tier 1 storage and Tier 2 ingestion service
with MemoryRepository(":memory:") as repo:
    synced_repo = GraphSyncedRepository.create(repo)
    ingestion = TemporalBeliefIngestionService(synced_repo, repo)

    # 3. Stream recorded telemetry samples into GroundTruth step-by-step
    for sample in adapter:
        result = ingestion.ingest_sample(sample)
        print(f"Sample {sample.sample_sequence} ({sample.request.capability}): {result.status}")

    # 4. Verify resulting beliefs
    active_facts = repo.query_facts(FactQuery(active_only=True))
    for fact in active_facts:
        print(f"Active belief: {fact.subject} {fact.predicate} {fact.object}")
```

---

## Verification

After merging main into the adapter branch, `python -m pytest -q` passed 725 tests,
and `ruff check .` and `ruff format --check .` passed using the existing project
environment. The adapter unit and ingestion tests cover playback, malformed
payloads, frame validation and replay of targeted camera views.

## 7. External Robotics Team Handoff Checklist

Before handing off telemetry datasets to GroundTruth:
- [ ] **Recording ID:** Ensure every dataset export assigns a stable `recording_id`.
- [ ] **Message IDs:** Ensure `message_id` is unique and monotonically ordered by `sequence`.
- [ ] **Timestamp Validity:** Ensure timestamps are timezone-aware UTC or non-negative relative milliseconds aligned to the agreed `epoch_utc`.
- [ ] **Robot Pose Required:** Telemetry must include valid robot poses with cardinal headings; missing poses are not defaulted.
- [ ] **Coordinate Frames:** Tag telemetry with valid frames (`"warehouse_map"`, `"robot_frame"`, or `"base_link"`). If using custom sensor/facility extrinsics, configure corresponding `FrameTransform` entries in `TransformCalibration`. Uncalibrated frames are rejected.
- [ ] **Confidence Required:** Explicit confidence (`0.0` to `1.0`) must be provided for all available records; confidence is not fabricated as 1.0.
- [ ] **LiDAR Formatting:** Front LiDAR scans must provide `direction: "front"` and status (`"clear"` or `"blocked"` with `nearest_distance_cm`). Missing data is rejected and never assumed clear.
- [ ] **Visual Bounding Boxes:** Camera detections must supply `object_id`, `apparent_color`, position, and confidence. Explicitly empty scans must pass an empty list `[]`.
- [ ] **Failure States:** Hardware disconnects, blinding, or timeouts must be explicitly flagged with `is_unavailable: true` and an appropriate `unavailable_reason`.
