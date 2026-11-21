"""Recorded robotics sensor adapter.

Converts recorded external robotics telemetry into typed P2-01 ObservationSample
envelopes, conforming to the SensorSource protocol without requiring access to
the simulation engine or live hardware.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from src.contracts.models import (
    ObservationRequest,
    ObservationUnavailable,
    SensorObservation,
    SpatialContext,
)
from src.contracts.simulation import (
    BoundingBox,
    CameraDetection,
    CameraScan,
    LidarMeasurement,
    LightMeasurement,
    ObservationSample,
    Point,
    Pose,
    PoseMeasurement,
    SensorSource,
)
from src.sensorimotor.robotics_adapter_models import (
    ClockCalibration,
    CorruptedRecordError,
    ExternalRoboticsRecord,
    TransformCalibration,
    UnknownFrameError,
    derive_deterministic_ids,
)


class RecordedRoboticsSensorAdapter(SensorSource):
    """Translates recorded robotics telemetry into P2-01 observation samples.

    Conforms to the ``SensorSource`` protocol (``observe()``) and provides
    timestamped, validated ``ObservationSample`` envelopes via ``sample()``.
    Playback operates strictly on the current recorded step and does not skip
    intervening messages or search ahead for requested capabilities.
    """

    def __init__(
        self,
        records: list[ExternalRoboticsRecord],
        clock_calibration: ClockCalibration,
        transform_calibration: TransformCalibration | None = None,
        run_id: UUID | None = None,
        default_robot_pose: Pose | None = None,
        auto_advance: bool = True,
    ) -> None:
        self._records = list(records)
        self._clock_cal = clock_calibration
        self._tf_cal = transform_calibration or TransformCalibration()
        self._cursor = 0
        self._auto_advance = auto_advance

        if run_id is not None:
            self._run_id = run_id
        elif self._records:
            self._run_id = uuid5(NAMESPACE_DNS, f"groundtruth:run:{self._records[0].recording_id}")
        else:
            self._run_id = uuid5(NAMESPACE_DNS, "groundtruth:run:default")

        self._default_pose = default_robot_pose

    # -------------------------------------------------------------------------
    # Factories
    # -------------------------------------------------------------------------

    @classmethod
    def from_records_data(
        cls,
        records_data: list[dict[str, Any]],
        clock_calibration: ClockCalibration,
        transform_calibration: TransformCalibration | None = None,
        run_id: UUID | None = None,
        default_robot_pose: Pose | None = None,
        auto_advance: bool = True,
    ) -> RecordedRoboticsSensorAdapter:
        """Create adapter from raw dictionary records."""
        parsed_records: list[ExternalRoboticsRecord] = []
        for i, item in enumerate(records_data):
            try:
                parsed_records.append(ExternalRoboticsRecord.model_validate(item))
            except Exception as exc:
                raise CorruptedRecordError(
                    f"Failed to validate external robotics record at index {i}: {exc}"
                ) from exc
        return cls(
            records=parsed_records,
            clock_calibration=clock_calibration,
            transform_calibration=transform_calibration,
            run_id=run_id,
            default_robot_pose=default_robot_pose,
            auto_advance=auto_advance,
        )

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        clock_calibration: ClockCalibration | None = None,
        transform_calibration: TransformCalibration | None = None,
        run_id: UUID | None = None,
        default_robot_pose: Pose | None = None,
        auto_advance: bool = True,
    ) -> RecordedRoboticsSensorAdapter:
        """Load adapter from a JSON dataset file."""
        file_path = Path(path)
        with file_path.open(encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            records_raw = data.get("records", [])
            epoch_str = data.get("epoch_utc")
            if clock_calibration is None and epoch_str:
                epoch_dt = datetime.fromisoformat(epoch_str)
                if epoch_dt.tzinfo is None:
                    epoch_dt = epoch_dt.replace(tzinfo=UTC)
                clock_calibration = ClockCalibration(epoch_utc=epoch_dt)
        elif isinstance(data, list):
            records_raw = data
        else:
            raise CorruptedRecordError("File root must be a JSON object or list")

        if clock_calibration is None:
            raise ValueError(
                "clock_calibration must be provided if not present as 'epoch_utc' in file"
            )

        return cls.from_records_data(
            records_data=records_raw,
            clock_calibration=clock_calibration,
            transform_calibration=transform_calibration,
            run_id=run_id,
            default_robot_pose=default_robot_pose,
            auto_advance=auto_advance,
        )

    # -------------------------------------------------------------------------
    # Properties & Navigation
    # -------------------------------------------------------------------------

    @property
    def run_id(self) -> UUID:
        return self._run_id

    @property
    def current_index(self) -> int:
        return self._cursor

    @property
    def total_records(self) -> int:
        return len(self._records)

    @property
    def remaining_records(self) -> int:
        return max(0, len(self._records) - self._cursor)

    def has_next(self) -> bool:
        return self._cursor < len(self._records)

    def seek(self, index: int) -> None:
        if not (0 <= index <= len(self._records)):
            raise IndexError(f"Index {index} out of bounds (total: {len(self._records)})")
        self._cursor = index

    def reset(self) -> None:
        self._cursor = 0

    # -------------------------------------------------------------------------
    # Core Translation
    # -------------------------------------------------------------------------

    def translate_record(
        self,
        record: ExternalRoboticsRecord,
        request: ObservationRequest | None = None,
        run_id: UUID | None = None,
    ) -> ObservationSample:
        """Translate a single external robotics record into a P2-01 ObservationSample.

        Performs coordinate transformations to canonical 'warehouse_map' frame,
        deterministic clock mapping, and strict schema validation.
        """
        active_run_id = run_id or self._run_id

        # 1. Resolve simulation time and capture timestamp
        sim_time_ms, captured_at = self._clock_cal.calculate_sim_time(
            record.captured_timestamp_utc, record.captured_time_rel_ms
        )

        # 2. Derive deterministic IDs for idempotence
        sample_id, obs_id = derive_deterministic_ids(record.recording_id, record.message_id)

        # 3. Determine and validate observer pose: require a real pose and transform it
        raw_pose = record.robot_pose or self._default_pose
        if raw_pose is None:
            raise CorruptedRecordError(
                f"External robotics record '{record.message_id}' is missing required 'robot_pose'"
            )

        # Transform pose (both coordinates and heading orientation) into canonical frame
        observer_pose = self._tf_cal.transform_pose(raw_pose)

        # 4. Canonicalize or validate ObservationRequest
        cap = record.capability
        if request is None:
            expected_parameters = {"direction": "front"} if cap == "lidar_scan" else {}
            request = ObservationRequest(capability=cap, parameters=expected_parameters)
        else:
            if request.capability != cap:
                raise ValueError(
                    f"Request capability '{request.capability}' does not match "
                    f"record capability '{cap}'"
                )

        # 5. Handle Unavailable / Sensor Failure State
        if record.is_unavailable:
            reason = record.unavailable_reason or "sensor_unavailable"
            unavailable_result = ObservationUnavailable(
                capability=cap,
                reason=reason,
                message="Observation unavailable.",
                reported_at=captured_at,
            )
            return ObservationSample(
                sample_id=sample_id,
                run_id=active_run_id,
                sample_sequence=record.sequence,
                epoch_utc=self._clock_cal.epoch_utc,
                sim_time_ms=sim_time_ms,
                captured_at=captured_at,
                sensor_id=record.sensor_id,
                observer_pose=observer_pose,
                world_version=record.world_version,
                request=request,
                result=unavailable_result,
            )

        # 6. Validate explicit source_frame and confidence for available observations
        if record.source_frame is None or not record.source_frame.strip():
            raise UnknownFrameError(
                f"Record '{record.message_id}' has unspecified source_frame; "
                "cannot assume map frame"
            )
        if record.confidence is None:
            raise CorruptedRecordError(
                f"Record '{record.message_id}' has missing confidence; cannot fabricate certainty"
            )

        # 7. Extract capability-specific measurements
        lighting_cast = "neutral"
        measurements: dict[str, Any]

        if cap == "camera_detect":
            if record.detections is None and record.measurements is None:
                raise CorruptedRecordError(
                    f"Camera record '{record.message_id}' is missing detection data; "
                    "distinguish missing payload from explicit empty scan []"
                )

            raw_detections = record.detections or []
            transformed_detections: list[CameraDetection] = []

            for det in raw_detections:
                source_pt = Point(x_cm=det.position_x_cm, y_cm=det.position_y_cm)
                map_pt = self._tf_cal.transform_point(
                    source_pt, record.source_frame, robot_pose=observer_pose
                )
                transformed_detections.append(
                    CameraDetection(
                        object_id=det.object_id,
                        apparent_color=det.apparent_color,
                        position=map_pt,
                        frame_of_reference=self._tf_cal.canonical_frame,
                        bounding_box=BoundingBox(
                            width_cm=det.bounding_box_width_cm,
                            height_cm=det.bounding_box_height_cm,
                        ),
                    )
                )

            # Sort detections deterministically by object_id
            sorted_detections = sorted(transformed_detections, key=lambda d: d.object_id)

            # Handle targeted camera requests
            if request.target is not None:
                targeted = [d for d in sorted_detections if d.object_id == request.target]
                if not targeted:
                    unavailable_result = ObservationUnavailable(
                        capability=cap,
                        reason="target_not_visible",
                        message="Observation unavailable.",
                        reported_at=captured_at,
                    )
                    return ObservationSample(
                        sample_id=sample_id,
                        run_id=active_run_id,
                        sample_sequence=record.sequence,
                        epoch_utc=self._clock_cal.epoch_utc,
                        sim_time_ms=sim_time_ms,
                        captured_at=captured_at,
                        sensor_id=record.sensor_id,
                        observer_pose=observer_pose,
                        world_version=record.world_version,
                        request=request,
                        result=unavailable_result,
                    )
                sorted_detections = [targeted[0]]

            scan = CameraScan(objects=sorted_detections)
            measurements = scan.model_dump(mode="json")

        elif cap == "lidar_scan":
            if record.lidar_measurement is not None:
                lm = record.lidar_measurement
                detected = lm.detected
                dist = lm.nearest_distance_cm
                status = "blocked" if detected else "clear"
                lidar_obj = LidarMeasurement(
                    detected=detected,
                    nearest_distance_cm=dist,
                    direction="front",
                    status=status,
                )
                measurements = lidar_obj.model_dump(mode="json")
            elif record.measurements is not None:
                lidar_obj = LidarMeasurement.model_validate(record.measurements)
                measurements = lidar_obj.model_dump(mode="json")
            else:
                raise CorruptedRecordError(
                    f"LiDAR record '{record.message_id}' missing measurement payload; "
                    "cannot assume clear scan on missing data"
                )

        elif cap == "robot_pose":
            pose_obj = PoseMeasurement(
                robot_id=observer_pose.robot_id,
                x_cm=observer_pose.x_cm,
                y_cm=observer_pose.y_cm,
                direction=observer_pose.direction,
            )
            measurements = pose_obj.model_dump(mode="json")

        elif cap == "ambient_light":
            if record.light_measurement is not None:
                intensity = record.light_measurement.intensity
                lighting_cast = record.light_measurement.color_cast
            elif record.measurements is not None:
                intensity = float(record.measurements.get("intensity", 1.0))
                lighting_cast = str(record.measurements.get("color_cast", "neutral"))
            else:
                raise CorruptedRecordError(
                    f"Ambient light record '{record.message_id}' missing light_measurement; "
                    "cannot fabricate default light readings"
                )
            light_obj = LightMeasurement(intensity=intensity, color_cast=lighting_cast)
            measurements = light_obj.model_dump(mode="json")

        else:
            raise ValueError(f"Unsupported capability: {cap}")

        # 8. Construct SpatialContext (strictly adheres to P2-01 constraints)
        context = SpatialContext(
            location=observer_pose.location,
            frame_of_reference=observer_pose.frame_of_reference,
            world_version=record.world_version,
            observer=observer_pose.robot_id,
            extra_context={"lighting": lighting_cast},
        )

        sensor_obs = SensorObservation(
            observation_id=obs_id,
            sensor=record.sensor_id,
            capability=cap,
            observed_at=captured_at,
            confidence_score=record.confidence,
            measurements=measurements,
            context=context,
        )

        # 9. Assemble full ObservationSample
        return ObservationSample(
            sample_id=sample_id,
            run_id=active_run_id,
            sample_sequence=record.sequence,
            epoch_utc=self._clock_cal.epoch_utc,
            sim_time_ms=sim_time_ms,
            captured_at=captured_at,
            sensor_id=record.sensor_id,
            observer_pose=observer_pose,
            world_version=record.world_version,
            request=request,
            result=sensor_obs,
        )

    # -------------------------------------------------------------------------
    # SensorSource Protocol Implementation
    # -------------------------------------------------------------------------

    def observe(self, request: ObservationRequest) -> SensorObservation | ObservationUnavailable:
        """Conform to SensorSource without advancing simulation time or skipping records."""
        if not self.has_next():
            raise IndexError("Recorded robotics stream exhausted; no records available")

        record = self._records[self._cursor]
        if record.capability != request.capability:
            sim_time_ms, captured_at = self._clock_cal.calculate_sim_time(
                record.captured_timestamp_utc, record.captured_time_rel_ms
            )
            return ObservationUnavailable(
                capability=request.capability,
                reason="sensor_unavailable",
                message="Observation unavailable.",
                reported_at=captured_at,
            )

        sample = self.translate_record(record, request=request)
        return sample.result

    def sample(self, request: ObservationRequest | None = None) -> ObservationSample:
        """Acquire the observation sample at the current controlled step.

        Does NOT search forward for requested capabilities; reports unavailable
        on capability mismatch at the current step. Advances cursor if auto_advance is True.
        """
        if not self.has_next():
            raise IndexError("Recorded robotics stream exhausted; no further records available")

        record = self._records[self._cursor]
        if self._auto_advance:
            self._cursor += 1

        if request is not None and record.capability != request.capability:
            sim_time_ms, captured_at = self._clock_cal.calculate_sim_time(
                record.captured_timestamp_utc, record.captured_time_rel_ms
            )
            sample_id, _ = derive_deterministic_ids(
                record.recording_id, f"mismatch_{record.sequence}_{request.capability}"
            )
            raw_pose = record.robot_pose or self._default_pose
            if raw_pose is None:
                raise CorruptedRecordError(
                    f"Record at step {record.sequence} is missing required 'robot_pose'"
                )
            observer_pose = self._tf_cal.transform_pose(raw_pose)
            return ObservationSample(
                sample_id=sample_id,
                run_id=self._run_id,
                sample_sequence=record.sequence,
                epoch_utc=self._clock_cal.epoch_utc,
                sim_time_ms=sim_time_ms,
                captured_at=captured_at,
                sensor_id=request.capability,
                observer_pose=observer_pose,
                world_version=record.world_version,
                request=request,
                result=ObservationUnavailable(
                    capability=request.capability,
                    reason="sensor_unavailable",
                    message="Observation unavailable.",
                    reported_at=captured_at,
                ),
            )

        return self.translate_record(record, request=request)

    # -------------------------------------------------------------------------
    # Stream / Batch Playback
    # -------------------------------------------------------------------------

    def play_next(self) -> ObservationSample:
        """Advance cursor and return the translated sample."""
        if not self.has_next():
            raise IndexError("Recorded robotics stream exhausted")
        record = self._records[self._cursor]
        self._cursor += 1
        return self.translate_record(record)

    def play_all(self) -> list[ObservationSample]:
        """Translate and return all records in order."""
        return [self.translate_record(record) for record in self._records]

    def __iter__(self) -> Iterator[ObservationSample]:
        while self.has_next():
            yield self.play_next()
