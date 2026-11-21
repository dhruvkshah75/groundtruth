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
  |  - Coordinate Frame Transforms (robot_frame -> map)   |
  |  - Deterministic Clock Calibration (UTC/ms -> sim_ms)  |
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
- **Strict Separation from Belief Policy:** The adapter does not decide what the robot believes or write directly to databases. It produces standard `ObservationSample` envelopes, which are consumed by Tier 1 (`MemoryRepository`) and Tier 2 (`TemporalBeliefIngestionService`) through established public protocols.
- **Protocol Conformance:** Conforms to the `SensorSource` protocol (`observe(request)`), while also providing `sample(request)` and dataset playback iterators (`play_next()`, `play_all()`).
- **Deterministic and Idempotent:** Sample and observation IDs are derived deterministically using `uuid5(NAMESPACE_DNS, ...)` from the recording ID and message ID. Re-ingesting the same recorded dataset produces identical IDs and facts, preventing duplicate entries.

---

## 2. Versioned Record Schema (`ExternalRoboticsRecord`)

Defined in `src/sensorimotor/robotics_adapter_models.py`, `ExternalRoboticsRecord` is a versioned Pydantic contract (`schema_version: 1`).

### Schema Fields:

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
| `source_frame` | `str` | Coordinate frame of measurements (`"warehouse_map"`, `"robot_frame"`, `"base_link"`). |
| `robot_pose` | `Pose \| None` | Robot's estimated location and heading at time of capture. |
| `world_version` | `int` | Simulation or environment configuration version. |
| `detections` | `list[ExternalDetection] \| None` | Bounding boxes and positions for visual objects (`camera_detect`). |
| `lidar_measurement` | `ExternalLidarMeasurement \| None` | Front obstacle scan data (`lidar_scan`). |
| `light_measurement` | `ExternalLightMeasurement \| None` | Ambient light intensity and color cast (`ambient_light`). |
| `confidence` | `float` | Detection confidence between `0.0` and `1.0` (default `1.0`). |
| `is_unavailable` | `bool` | Set to `true` when the sensor hardware failed or was blinded. |
| `unavailable_reason` | `str \| None` | `"sensor_unavailable"`, `"target_not_visible"`, or `"unsupported_capability"`. |
| `metadata` | `dict[str, Any]` | Optional platform-specific metadata (ROS topic, bag offset, etc.). |

---

## 3. Coordinate Frame Transforms (`TransformCalibration`)

Robotics hardware often captures data in egocentric sensor coordinates. GroundTruth requires observations in canonical `warehouse_map` coordinates.

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
4. **Custom Extrinsics (`FrameTransform`):**
   - Configurable rigid 2D planar rotation (`rotation_deg`) and translation offsets (`offset_x_cm`, `offset_y_cm`).
5. **Strict Frame Validation:**
   - If an unconfigured or unknown frame (e.g., `"uncalibrated_arm_link"`) is encountered, the adapter raises `UnknownFrameError` rather than guessing.

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

    # 3. Stream recorded telemetry samples into GroundTruth
    for sample in adapter:
        # Translates external camera/LiDAR/pose messages into ObservationSample
        result = ingestion.ingest_sample(sample)
        print(f"Sample {sample.sample_sequence} ({sample.request.capability}): {result.status}")

    # 4. Verify resulting beliefs
    active_facts = repo.query_facts(FactQuery(active_only=True))
    for fact in active_facts:
        print(f"Active belief: {fact.subject} {fact.predicate} {fact.object}")
```

---

## 7. External Robotics Team Handoff Checklist

Before handing off telemetry datasets to GroundTruth:
- [ ] **Recording ID:** Ensure every dataset export assigns a stable `recording_id`.
- [ ] **Message IDs:** Ensure `message_id` is unique and monotonically ordered by `sequence`.
- [ ] **Timestamp Validity:** Ensure timestamps are timezone-aware UTC or non-negative relative milliseconds aligned to the agreed `epoch_utc`.
- [ ] **Coordinate Frames:** Tag telemetry with standard frames (`"warehouse_map"`, `"robot_frame"`, or `"base_link"`). If using custom sensor extrinsics, configure corresponding `FrameTransform` entries in `TransformCalibration`.
- [ ] **LiDAR Formatting:** Front LiDAR scans must provide `direction: "front"` and status (`"clear"` or `"blocked"` with `nearest_distance_cm`).
- [ ] **Visual Bounding Boxes:** Camera detections must supply `object_id`, `apparent_color`, and position coordinates.
- [ ] **Failure States:** Hardware disconnects, blinding, or timeouts must be explicitly flagged with `is_unavailable: true` and an appropriate `unavailable_reason`.
