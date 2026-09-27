"""Tier 3 Mock Environment implementation."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from contracts.models import (
    CapabilityDescriptor,
    ObservationRequest,
    ObservationUnavailable,
    SensorObservation,
    SpatialContext,
)
from sensorimotor.world_models import WorldState


class MockEnvironment:
    """A deterministic mock physical world for Tier 3 sensors."""

    def __init__(
        self,
        world: WorldState,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], uuid.UUID] | None = None,
    ):
        """Initialize with a world state and optional deterministic factories."""
        self._world = world
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid.uuid4

        self._capabilities = [
            CapabilityDescriptor(
                name="lidar_scan",
                kind="sensor",
                description="Scan for obstacles in a specific direction.",
                parameters=["direction"],
            ),
            CapabilityDescriptor(
                name="camera_detect",
                kind="sensor",
                description="Detect and inspect objects using the camera.",
                parameters=[],
            ),
            CapabilityDescriptor(
                name="ambient_light",
                kind="sensor",
                description="Measure ambient light intensity and color cast.",
                parameters=[],
            ),
            CapabilityDescriptor(
                name="robot_pose",
                kind="sensor",
                description="Get current robot position and orientation.",
                parameters=[],
            ),
        ]

    def get_capabilities(self) -> list[CapabilityDescriptor]:
        """Return the list of supported capabilities."""
        # Return a fresh list (or deep copies) to prevent external mutation
        return [cap.model_copy(deep=True) for cap in self._capabilities]

    def observe(self, request: ObservationRequest) -> SensorObservation | ObservationUnavailable:
        """Process an observation request deterministically."""

        # 1. Validate capability against registry
        supported_names = {cap.name for cap in self._capabilities}
        if request.capability not in supported_names:
            return self._unavailable(
                request.capability,
                "unsupported_capability",
                f"Capability '{request.capability}' is not supported.",
            )

        # 2. Dispatch to specific sensor
        try:
            if request.capability == "lidar_scan":
                return self._lidar_scan(request)
            elif request.capability == "camera_detect":
                return self._camera_detect(request)
            elif request.capability == "ambient_light":
                return self._ambient_light(request)
            elif request.capability == "robot_pose":
                return self._robot_pose(request)
        except ValueError as e:
            # Domain validation error, e.g., missing parameter
            return self._unavailable(
                request.capability,
                "sensor_unavailable",
                str(e),
            )

        # Fallback if something slips through
        return self._unavailable(
            request.capability,
            "sensor_unavailable",
            "Internal error: unhandled capability.",
        )

    def _unavailable(self, capability: str, reason: str, message: str) -> ObservationUnavailable:
        """Helper to create ObservationUnavailable."""
        # Using type ignore or matching Literal since reason is typed strictly in contracts
        return ObservationUnavailable(
            capability=capability,
            reason=reason,  # type: ignore
            message=message,
            reported_at=self._clock(),
        )

    def _get_context(self) -> SpatialContext:
        """Helper to generate the current SpatialContext."""
        return SpatialContext(
            location=self._world.robot.location,
            frame_of_reference=self._world.robot.frame_of_reference,
            world_version=self._world.world_version,
            observer=self._world.robot.robot_id,
        )

    def _lidar_scan(
        self, request: ObservationRequest
    ) -> SensorObservation | ObservationUnavailable:
        """Execute lidar_scan."""
        direction = request.parameters.get("direction")
        if not direction:
            raise ValueError("lidar_scan requires a 'direction' parameter (e.g., 'front').")

        if direction != "front":
            raise ValueError("Only 'front' direction is currently supported.")

        nearest_dist = float("inf")
        nearest_obs = None

        # We consider a lateral tolerance of 5 cm to define a "ray" ahead of the robot.
        tolerance = 5.0
        robot = self._world.robot

        for obs in self._world.obstacles.values():
            if not obs.active or obs.location != robot.location:
                continue

            dx = obs.x_cm - robot.x_cm
            dy = obs.y_cm - robot.y_cm

            is_in_front = False
            dist = 0.0
            if robot.direction == "north" and dy > 0 and abs(dx) <= tolerance:
                is_in_front = True
                dist = dy
            elif robot.direction == "south" and dy < 0 and abs(dx) <= tolerance:
                is_in_front = True
                dist = -dy
            elif robot.direction == "east" and dx > 0 and abs(dy) <= tolerance:
                is_in_front = True
                dist = dx
            elif robot.direction == "west" and dx < 0 and abs(dy) <= tolerance:
                is_in_front = True
                dist = -dx

            if is_in_front and dist < nearest_dist:
                nearest_dist = dist
                nearest_obs = obs

        if nearest_obs:
            measurements = {
                "detected": True,
                "nearest_distance_cm": nearest_dist,
                "direction": direction,
                "status": "blocked",
                "obstacle_id": nearest_obs.obstacle_id,
            }
        else:
            measurements = {
                "detected": False,
                "nearest_distance_cm": None,
                "direction": direction,
                "status": "clear",
            }

        return SensorObservation(
            observation_id=self._id_factory(),
            sensor="lidar",
            capability="lidar_scan",
            observed_at=self._clock(),
            confidence_score=1.0,
            measurements=measurements,
            context=self._get_context(),
        )

    def _camera_detect(
        self, request: ObservationRequest
    ) -> SensorObservation | ObservationUnavailable:
        """Execute camera_detect."""
        target_id = request.target
        if not target_id:
            raise ValueError("camera_detect requires a target object ID.")

        obj = self._world.objects.get(target_id)
        if not obj or not obj.visible or obj.location != self._world.robot.location:
            return self._unavailable(
                "camera_detect", "target_not_visible", f"Target '{target_id}' is not visible."
            )

        # Appearance mapping rule: red object + yellow light -> apparent brown
        apparent_color = obj.intrinsic_color
        if obj.intrinsic_color == "red" and self._world.light.color_cast == "yellow":
            apparent_color = "brown"

        measurements = {
            "object_id": obj.object_id,
            "apparent_color": apparent_color,
            "bounding_box": obj.bounding_box,
        }

        context = self._get_context()
        context.extra_context["lighting"] = self._world.light.color_cast

        return SensorObservation(
            observation_id=self._id_factory(),
            sensor="camera",
            capability="camera_detect",
            observed_at=self._clock(),
            confidence_score=1.0,
            measurements=measurements,
            context=context,
        )

    def _ambient_light(
        self, request: ObservationRequest
    ) -> SensorObservation | ObservationUnavailable:
        """Execute ambient_light."""
        measurements = {
            "intensity": self._world.light.intensity,
            "color_cast": self._world.light.color_cast,
        }

        return SensorObservation(
            observation_id=self._id_factory(),
            sensor="light_sensor",
            capability="ambient_light",
            observed_at=self._clock(),
            confidence_score=1.0,
            measurements=measurements,
            context=self._get_context(),
        )

    def _robot_pose(
        self, request: ObservationRequest
    ) -> SensorObservation | ObservationUnavailable:
        """Execute robot_pose."""
        robot = self._world.robot
        measurements = {
            "robot_id": robot.robot_id,
            "x_cm": robot.x_cm,
            "y_cm": robot.y_cm,
            "direction": robot.direction,
        }

        return SensorObservation(
            observation_id=self._id_factory(),
            sensor="pose_sensor",
            capability="robot_pose",
            observed_at=self._clock(),
            confidence_score=1.0,
            measurements=measurements,
            context=self._get_context(),
        )
