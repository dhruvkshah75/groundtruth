"""Explicit Phase 2 sensor adapter over one detached engine snapshot per call."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID, uuid4

from src.contracts import (
    CameraDetection,
    CameraScan,
    ObservationRequest,
    ObservationSample,
    ObservationUnavailable,
    SensorObservation,
    SimulationSnapshot,
    SpatialContext,
)
from src.contracts.simulation import Point, Rectangle


def rectangle_entry(start: Point, end: Point, shape: Rectangle) -> float | None:
    """First segment intersection fraction, including boundary/tangent contacts."""
    lo, hi = 0.0, 1.0
    for origin, delta, center, half in (
        (start.x_cm, end.x_cm - start.x_cm, shape.x_cm, shape.width_cm / 2),
        (start.y_cm, end.y_cm - start.y_cm, shape.y_cm, shape.height_cm / 2),
    ):
        if delta == 0:
            if abs(origin - center) > half:
                return None
        else:
            a, b = sorted(((center - half - origin) / delta, (center + half - origin) / delta))
            lo, hi = max(lo, a), min(hi, b)
            if lo > hi:
                return None
    return lo


def apparent_color(intrinsic: str, cast: str) -> str:
    """Shared deterministic mock appearance rule; not physical color science."""
    return "brown" if intrinsic == "red" and cast == "yellow" else intrinsic


class WarehouseSensors:
    """SensorSource-compatible inventory camera/LiDAR/light/pose adapter.

    The snapshot supplier is simulator-side only; do not pass this dependency to
    the LLM. Share one adapter/sequencer per run and serialize acquisition calls.
    No observation advances the engine or changes its world version.
    """

    def __init__(
        self,
        snapshot_source: Callable[[], SimulationSnapshot],
        *,
        id_factory: Callable[[], UUID] = uuid4,
        sequence_factory: Callable[[UUID], int] | None = None,
    ) -> None:
        self._source = snapshot_source
        self._ids = id_factory
        self._sequence_factory = sequence_factory
        self._run: UUID | None = None
        self._sequence = 0

    @staticmethod
    def _invalid(request: ObservationRequest) -> str | None:
        cap = request.capability
        if cap not in {"camera_detect", "lidar_scan", "robot_pose", "ambient_light"}:
            return "unsupported_capability"
        if request.target is not None and cap != "camera_detect":
            return "sensor_unavailable"
        if request.parameters != ({"direction": "front"} if cap == "lidar_scan" else {}):
            return "sensor_unavailable"
        return None

    def _capture(self, request: ObservationRequest):
        request = ObservationRequest.model_validate(request.model_dump(mode="json"))
        # Validate a detached copy, even if a supplier returns a live mutable model.
        snap = SimulationSnapshot.model_validate(self._source().model_dump(mode="json"))
        captured = snap.status.epoch_utc + timedelta(milliseconds=snap.status.sim_time_ms)
        reason = self._invalid(request)
        cap = request.capability
        if (
            reason is None
            and cap in ("camera_detect", "lidar_scan")
            and not getattr(snap.sensor_availability, cap)
        ):
            reason = "sensor_unavailable"
        if reason is None:
            measurements = self._measure(snap, request)
            if measurements is None:
                reason = (
                    "target_not_visible" if request.target is not None else "sensor_unavailable"
                )
        if reason is not None:
            result = ObservationUnavailable(
                capability=cap,
                reason=reason,
                message="Observation unavailable.",
                reported_at=captured,
            )
        else:
            result = SensorObservation(
                observation_id=self._ids(),
                sensor=cap,
                capability=cap,
                observed_at=captured,
                confidence_score=1.0,
                measurements=measurements,
                context=SpatialContext(
                    location=snap.robot_pose.location,
                    frame_of_reference=snap.robot_pose.frame_of_reference,
                    world_version=snap.world_version,
                    observer=snap.robot_pose.robot_id,
                    extra_context={"lighting": snap.color_cast},
                ),
            )
        return snap, request, result, captured

    def observe(self, request: ObservationRequest) -> SensorObservation | ObservationUnavailable:
        """Legacy-shaped result; invalid/unsupported requests return typed failures."""
        return self._capture(request)[2]

    def sample(self, request: ObservationRequest) -> ObservationSample:
        """Timestamped acquisition, including no-detection and sensor-failure attempts.

        Known malformed requests raise before capture: the P2-01 envelope forbids
        them. observe() remains available for their typed rejection response.
        """
        if self._invalid(request) == "sensor_unavailable":
            raise ValueError("malformed Phase 2 request; use observe for typed rejection")
        snap, request, result, captured = self._capture(request)
        run = snap.status.run_id
        if self._sequence_factory is None:
            if run != self._run:
                self._run, self._sequence = run, 0
            sequence = self._sequence
            self._sequence += 1
        else:
            sequence = self._sequence_factory(run)
        return ObservationSample(
            sample_id=self._ids(),
            run_id=run,
            sample_sequence=sequence,
            epoch_utc=snap.status.epoch_utc,
            sim_time_ms=snap.status.sim_time_ms,
            captured_at=captured,
            sensor_id=request.capability,
            observer_pose=snap.robot_pose,
            world_version=snap.world_version,
            request=request,
            result=result,
        )

    @staticmethod
    def _measure(snap: SimulationSnapshot, request: ObservationRequest) -> dict | None:
        pose, cap = snap.robot_pose, request.capability
        if cap == "robot_pose":
            return pose.model_dump(include={"robot_id", "x_cm", "y_cm", "direction"})
        if cap == "ambient_light":
            return {"intensity": snap.light_intensity, "color_cast": snap.color_cast}
        forward = {"north": (0, 1), "east": (1, 0), "south": (0, -1), "west": (-1, 0)}[
            pose.direction
        ]
        if cap == "lidar_scan":
            distance = snap.config.lidar_range_cm
            end = Point(
                x_cm=pose.x_cm + forward[0] * distance, y_cm=pose.y_cm + forward[1] * distance
            )
            hits = [
                fraction * distance
                for e in snap.entities
                if e.solid
                and e.location == pose.location
                and (fraction := rectangle_entry(pose, end, e.footprint)) is not None
            ]
            nearest = min(hits) if hits else None
            return {
                "detected": nearest is not None,
                "nearest_distance_cm": nearest,
                "direction": "front",
                "status": "blocked" if nearest is not None else "clear",
            }
        objects = []
        for entity in sorted(snap.entities, key=lambda e: e.entity_id):
            if (
                entity.kind == "station"
                or not entity.visible
                or entity.location != pose.location
                or request.target is not None
                and entity.entity_id != request.target
            ):
                continue
            point = Point(x_cm=entity.footprint.x_cm, y_cm=entity.footprint.y_cm)
            dx, dy = point.x_cm - pose.x_cm, point.y_cm - pose.y_cm
            distance = math.hypot(dx, dy)
            if distance == 0:
                # Invalid projection geometry: no arbitrary bearing/position disclosure.
                return None
            along = dx * forward[0] + dy * forward[1]
            side = dx * forward[1] - dy * forward[0]
            angle = math.degrees(math.atan2(abs(side), along))
            if (
                distance > snap.config.camera_range_cm + 1e-9
                or angle > snap.config.camera_fov_deg / 2 + 1e-9
            ):
                continue
            if any(
                e.entity_id != entity.entity_id
                and e.opaque
                and e.location == pose.location
                and rectangle_entry(pose, point, e.footprint) is not None
                for e in snap.entities
            ):
                continue
            objects.append(
                CameraDetection(
                    object_id=entity.entity_id,
                    apparent_color=apparent_color(entity.intrinsic_color, snap.color_cast),
                    position=point,
                    frame_of_reference=pose.frame_of_reference,
                    bounding_box={
                        "width_cm": entity.footprint.width_cm,
                        "height_cm": entity.footprint.height_cm,
                    },
                )
            )
        if request.target is not None and not objects:
            return None
        return CameraScan(objects=objects).model_dump(mode="json")
