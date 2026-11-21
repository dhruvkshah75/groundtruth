"""Data models and calibration schemas for external recorded robotics sensor data.

Provides versioned schemas to represent robotics telemetry recorded from
external hardware (ROS bags, mobile robot loggers, camera/LiDAR feeds),
along with 2D coordinate frame transform calibrations and clock calibrations.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_DNS, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.contracts.simulation import Direction, Point, Pose


class RoboticsAdapterError(Exception):
    """Base exception for robotics adapter errors."""


class UnknownFrameError(RoboticsAdapterError):
    """Raised when an unconfigured or unsupported source frame is encountered."""


class UnmappableTimeError(RoboticsAdapterError):
    """Raised when an external timestamp cannot be mapped to simulation time."""


class CorruptedRecordError(RoboticsAdapterError):
    """Raised when a record has invalid or internally inconsistent fields."""


class ExternalDetection(BaseModel):
    """Visual object detection in external camera coordinates or robot frame."""

    model_config = ConfigDict(extra="forbid")

    object_id: str = Field(min_length=1)
    apparent_color: str = Field(min_length=1)
    position_x_cm: float
    position_y_cm: float
    bounding_box_width_cm: float = Field(default=40.0, gt=0)
    bounding_box_height_cm: float = Field(default=40.0, gt=0)
    confidence: float = Field(ge=0.0, le=1.0)


class ExternalLidarMeasurement(BaseModel):
    """LiDAR obstacle scan in external robot or sensor frame."""

    model_config = ConfigDict(extra="forbid")

    detected: bool
    nearest_distance_cm: float | None = Field(default=None, ge=0)
    direction: Literal["front"] = "front"
    status: Literal["clear", "blocked"] | None = None

    @model_validator(mode="after")
    def validate_consistency(self) -> ExternalLidarMeasurement:
        if self.detected != (self.nearest_distance_cm is not None):
            raise ValueError("detected and nearest_distance_cm must agree")
        expected_status = "blocked" if self.detected else "clear"
        if self.status is not None and self.status != expected_status:
            raise ValueError(f"status '{self.status}' contradicts detected={self.detected}")
        return self


class ExternalLightMeasurement(BaseModel):
    """Ambient light sensor reading."""

    model_config = ConfigDict(extra="forbid")

    intensity: float = Field(ge=0.0)
    color_cast: str = Field(min_length=1)


class FrameTransform(BaseModel):
    """Rigid 2D coordinate transform from a source sensor frame to target frame."""

    model_config = ConfigDict(extra="forbid")

    source_frame: str = Field(min_length=1)
    target_frame: str = Field(default="warehouse_map", min_length=1)
    offset_x_cm: float = 0.0
    offset_y_cm: float = 0.0
    rotation_deg: float = 0.0  # Counter-clockwise rotation in degrees


class TransformCalibration(BaseModel):
    """Calibration mapping for coordinate systems."""

    model_config = ConfigDict(extra="forbid")

    canonical_frame: str = "warehouse_map"
    transforms: dict[str, FrameTransform] = Field(default_factory=dict)

    def transform_point(
        self,
        point: Point,
        source_frame: str,
        robot_pose: Pose | None = None,
    ) -> Point:
        """Transform a 2D point from source_frame into canonical_frame.

        Supported frames:
        - canonical_frame ('warehouse_map'): identity transform.
        - 'robot_frame' / 'robot_base': egocentric frame where +y is forward along
          robot heading, and +x is lateral right.
        - 'base_link': ROS standard REP 103 frame where +x is forward along
          robot heading, and +y is lateral left.
        - Any custom frame configured in self.transforms.
        """
        if source_frame == self.canonical_frame:
            return Point(x_cm=point.x_cm, y_cm=point.y_cm)

        # Standard robot egocentric frames requiring robot pose
        if source_frame in {"robot_frame", "robot_base"}:
            if robot_pose is None:
                raise UnknownFrameError(
                    f"Transform from '{source_frame}' requires robot_pose but none was provided"
                )
            dx, dy = point.x_cm, point.y_cm
            fwd_x, fwd_y, lat_x, lat_y = self._direction_vectors(robot_pose.direction)
            map_x = robot_pose.x_cm + (dx * lat_x) + (dy * fwd_x)
            map_y = robot_pose.y_cm + (dx * lat_y) + (dy * fwd_y)
            return Point(x_cm=round(map_x, 3), y_cm=round(map_y, 3))

        if source_frame == "base_link":
            if robot_pose is None:
                raise UnknownFrameError(
                    f"Transform from '{source_frame}' requires robot_pose but none was provided"
                )
            fwd_comp, left_comp = point.x_cm, point.y_cm
            fwd_x, fwd_y, lat_x, lat_y = self._direction_vectors(robot_pose.direction)
            map_x = robot_pose.x_cm + (fwd_comp * fwd_x) - (left_comp * lat_x)
            map_y = robot_pose.y_cm + (fwd_comp * fwd_y) - (left_comp * lat_y)
            return Point(x_cm=round(map_x, 3), y_cm=round(map_y, 3))

        if source_frame in self.transforms:
            tf = self.transforms[source_frame]
            rad = math.radians(tf.rotation_deg)
            cos_r = math.cos(rad)
            sin_r = math.sin(rad)
            rx = point.x_cm * cos_r - point.y_cm * sin_r
            ry = point.x_cm * sin_r + point.y_cm * cos_r
            tx = rx + tf.offset_x_cm
            ty = ry + tf.offset_y_cm
            if tf.target_frame == self.canonical_frame:
                return Point(x_cm=round(tx, 3), y_cm=round(ty, 3))
            return self.transform_point(
                Point(x_cm=tx, y_cm=ty), tf.target_frame, robot_pose=robot_pose
            )

        supported = list(self.transforms.keys()) + [
            self.canonical_frame,
            "robot_frame",
            "robot_base",
            "base_link",
        ]
        msg = (
            f"Unknown or unconfigured source frame '{source_frame}'. Configured frames: {supported}"
        )
        raise UnknownFrameError(msg)

    def transform_pose(self, pose: Pose) -> Pose:
        """Transform a robot pose into canonical_frame, including position and heading."""
        if pose.frame_of_reference == self.canonical_frame:
            return pose

        if pose.frame_of_reference in self.transforms:
            tf = self.transforms[pose.frame_of_reference]
            rad = math.radians(tf.rotation_deg)
            cos_r = math.cos(rad)
            sin_r = math.sin(rad)

            # Transform translation
            rx = pose.x_cm * cos_r - pose.y_cm * sin_r
            ry = pose.x_cm * sin_r + pose.y_cm * cos_r
            tx = round(rx + tf.offset_x_cm, 3)
            ty = round(ry + tf.offset_y_cm, 3)

            # Transform orientation (cardinal heading)
            heading_angles: dict[Direction, float] = {
                "east": 0.0,
                "north": 90.0,
                "west": 180.0,
                "south": 270.0,
            }
            current_deg = heading_angles[pose.direction]
            new_deg = (current_deg + tf.rotation_deg) % 360.0

            new_direction: Direction | None = None
            for card, deg in heading_angles.items():
                if abs(new_deg - deg) < 1e-3 or abs(new_deg - (deg + 360.0)) < 1e-3:
                    new_direction = card
                    break

            if new_direction is None:
                raise UnknownFrameError(
                    f"Transformed pose heading {new_deg}° is not a valid cardinal direction"
                )

            if tf.target_frame == self.canonical_frame:
                return Pose(
                    x_cm=tx,
                    y_cm=ty,
                    robot_id=pose.robot_id,
                    direction=new_direction,
                    location=pose.location,
                    frame_of_reference=self.canonical_frame,
                )

            intermediate = Pose(
                x_cm=tx,
                y_cm=ty,
                robot_id=pose.robot_id,
                direction=new_direction,
                location=pose.location,
                frame_of_reference=tf.target_frame,
            )
            return self.transform_pose(intermediate)

        supported = list(self.transforms.keys()) + [self.canonical_frame]
        raise UnknownFrameError(
            f"Cannot transform pose: unconfigured frame '{pose.frame_of_reference}'. "
            f"Configured frames: {supported}"
        )

    @staticmethod
    def _direction_vectors(direction: Direction) -> tuple[float, float, float, float]:
        """Return (fwd_x, fwd_y, right_x, right_y) for a cardinal direction."""
        vectors: dict[str, tuple[float, float, float, float]] = {
            "north": (0.0, 1.0, 1.0, 0.0),
            "east": (1.0, 0.0, 0.0, -1.0),
            "south": (0.0, -1.0, -1.0, 0.0),
            "west": (-1.0, 0.0, 0.0, 1.0),
        }
        return vectors[direction]


class ClockCalibration(BaseModel):
    """Deterministic clock mapping between external timestamps and simulation ticks."""

    model_config = ConfigDict(extra="forbid")

    epoch_utc: datetime

    @field_validator("epoch_utc")
    @classmethod
    def require_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("epoch_utc must be timezone-aware")
        return v

    def calculate_sim_time(
        self,
        captured_utc: datetime | None,
        rel_ms: int | None,
    ) -> tuple[int, datetime]:
        """Map external timestamp into (sim_time_ms, timezone_aware_captured_at)."""
        if rel_ms is not None:
            if rel_ms < 0:
                raise UnmappableTimeError(f"Relative capture time cannot be negative: {rel_ms}")
            sim_time_ms = rel_ms
            captured_at = self.epoch_utc + timedelta(milliseconds=sim_time_ms)
            if captured_utc is not None:
                if captured_utc.tzinfo is None or captured_utc.utcoffset() is None:
                    raise UnmappableTimeError("captured_timestamp_utc must be timezone-aware")
                diff = abs((captured_utc - captured_at).total_seconds())
                if diff > 0.005:
                    raise UnmappableTimeError(
                        f"Inconsistent timestamps: rel_ms={rel_ms} implies "
                        f"{captured_at.isoformat()} but captured_timestamp_utc "
                        f"is {captured_utc.isoformat()}"
                    )
            return sim_time_ms, captured_at

        if captured_utc is not None:
            if captured_utc.tzinfo is None or captured_utc.utcoffset() is None:
                raise UnmappableTimeError("captured_timestamp_utc must be timezone-aware")
            delta_seconds = (captured_utc - self.epoch_utc).total_seconds()
            if delta_seconds < 0:
                raise UnmappableTimeError(
                    f"Capture timestamp {captured_utc.isoformat()} is before "
                    f"epoch {self.epoch_utc.isoformat()}"
                )
            sim_time_ms = int(round(delta_seconds * 1000))
            return sim_time_ms, captured_utc

        raise UnmappableTimeError(
            "Record must provide at least one of 'captured_time_rel_ms' or 'captured_timestamp_utc'"
        )


class ExternalRoboticsRecord(BaseModel):
    """Versioned external robotics telemetry message.

    Designed for ingestion from ROS 2 bag exports, protobuf/JSON stream dumps,
    or custom vehicle logging frameworks.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    recording_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    robot_id: str = Field(min_length=1)
    sensor_id: str = Field(min_length=1)
    capability: Literal["camera_detect", "lidar_scan", "robot_pose", "ambient_light"]

    # Timestamp options: relative ms, absolute UTC, or both
    captured_timestamp_utc: datetime | None = None
    captured_time_rel_ms: int | None = None

    source_frame: str | None = None
    robot_pose: Pose | None = None
    world_version: int = Field(default=1, ge=0)

    # Capability-specific payloads
    detections: list[ExternalDetection] | None = None
    lidar_measurement: ExternalLidarMeasurement | None = None
    light_measurement: ExternalLightMeasurement | None = None
    measurements: dict[str, Any] | None = None

    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    is_unavailable: bool = False
    unavailable_reason: (
        Literal[
            "unsupported_capability",
            "sensor_unavailable",
            "target_not_visible",
        ]
        | None
    ) = None
    unavailable_message: str = "Observation unavailable."
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("captured_timestamp_utc")
    @classmethod
    def validate_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.utcoffset() is None):
            raise ValueError("captured_timestamp_utc must be timezone-aware")
        return v

    @model_validator(mode="after")
    def validate_payload_consistency(self) -> ExternalRoboticsRecord:
        if self.is_unavailable:
            if self.unavailable_reason is None:
                self.unavailable_reason = "sensor_unavailable"
            return self

        # Available records must explicitly specify frame and confidence
        if self.source_frame is None or not self.source_frame.strip():
            raise ValueError(
                "source_frame must be explicitly specified "
                "(e.g. 'warehouse_map', 'robot_frame', 'base_link')"
            )
        if self.confidence is None:
            raise ValueError(
                "confidence must be explicitly provided between 0.0 and 1.0; cannot be defaulted"
            )
        if self.robot_pose is None:
            raise ValueError("robot_pose must be explicitly provided for available observations")

        # Validate capability payloads are not missing
        if self.capability == "camera_detect":
            if self.detections is None and self.measurements is None:
                raise ValueError(
                    "camera_detect record requires 'detections' list (pass [] for empty scan)"
                )
        elif self.capability == "lidar_scan":
            if self.lidar_measurement is None and self.measurements is None:
                raise ValueError("lidar_scan record requires 'lidar_measurement' or 'measurements'")
        elif self.capability == "ambient_light":
            if self.light_measurement is None and self.measurements is None:
                raise ValueError(
                    "ambient_light record requires 'light_measurement' or 'measurements'"
                )
        elif self.capability == "robot_pose":
            if self.robot_pose is None and self.measurements is None:
                raise ValueError("robot_pose record requires 'robot_pose'")

        return self


def derive_deterministic_ids(recording_id: str, message_id: str) -> tuple[UUID, UUID]:
    """Derive deterministic sample_id and observation_id from upstream message identifiers."""
    sample_id = uuid5(NAMESPACE_DNS, f"groundtruth:sample:{recording_id}:{message_id}")
    observation_id = uuid5(NAMESPACE_DNS, f"groundtruth:obs:{recording_id}:{message_id}")
    return sample_id, observation_id
