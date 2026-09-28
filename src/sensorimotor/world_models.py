"""Deterministic world models for Tier 3 mock environment."""

from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["north", "east", "south", "west"]


class RobotState(BaseModel):
    """The pose and location of the robot in the mock world."""

    robot_id: str = Field(min_length=1)
    x_cm: float
    y_cm: float
    direction: Direction
    location: str = Field(min_length=1)
    frame_of_reference: str = Field(min_length=1)


class Obstacle(BaseModel):
    """A physical obstacle that can block paths or be detected by LiDAR."""

    obstacle_id: str = Field(min_length=1)
    x_cm: float
    y_cm: float
    location: str = Field(min_length=1)
    active: bool = True
    radius_cm: float | None = None


class SimulatedObject(BaseModel):
    """An object that can be perceived by the camera."""

    object_id: str = Field(min_length=1)
    x_cm: float
    y_cm: float
    location: str = Field(min_length=1)
    intrinsic_color: str = Field(min_length=1)
    visible: bool = True
    bounding_box: dict[str, float] = Field(
        default_factory=lambda: {"width_cm": 10.0, "height_cm": 10.0}
    )


class AmbientLight(BaseModel):
    """Lighting conditions affecting camera perception."""

    intensity: float = Field(ge=0.0, default=1.0)
    color_cast: str = Field(min_length=1, default="neutral")


class WorldState(BaseModel):
    """The complete, deterministic state of the mock environment."""

    world_version: int = Field(ge=0, default=0)
    robot: RobotState
    obstacles: dict[str, Obstacle] = Field(default_factory=dict)
    objects: dict[str, SimulatedObject] = Field(default_factory=dict)
    light: AmbientLight = Field(default_factory=AmbientLight)
    scenario_name: str | None = None

    def increment_version(self) -> None:
        """Increment the world version when state mutates."""
        self.world_version += 1
