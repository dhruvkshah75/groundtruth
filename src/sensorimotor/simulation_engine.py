"""Explicit fixed-step physical simulation; no wall clock, sensors or scheduler."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from src.contracts.simulation import (
    LightingEvent,
    MoveEvent,
    Point,
    Pose,
    ScenarioDefinition,
    ScenarioEvent,
    SensorAvailabilityEvent,
    SimulationControl,
    SimulationSnapshot,
    SimulationStatus,
)

EVENT = TypeAdapter(ScenarioEvent)


def _swept(start: Point, end: Point, cx: float, cy: float, hx: float, hy: float) -> bool:
    """Open-interior segment/slab test: touching is permitted, crossing is not."""
    lo, hi = 0.0, 1.0
    for origin, delta, center, half in (
        (start.x_cm, end.x_cm - start.x_cm, cx, hx),
        (start.y_cm, end.y_cm - start.y_cm, cy, hy),
    ):
        if delta == 0:
            if abs(origin - center) >= half:
                return False
        else:
            a, b = sorted(((center - half - origin) / delta, (center + half - origin) / delta))
            lo, hi = max(lo, a), min(hi, b)
            if lo >= hi:
                return False
    return lo < hi and hi > 0 and lo < 1


class SimulationEngine:
    """Own a detached warehouse run. Call advance() from a future runtime loop.

    Circle/rectangle contacts use conservative radius-expanded rectangles. Solid
    motion is rejected for the whole tick on collision; this is not a sensor stop.
    Callers serialize access (this core starts no threads and owns no locks).
    """

    def __init__(
        self,
        scenario: ScenarioDefinition,
        *,
        epoch_utc: datetime,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._id_factory = id_factory
        self.load(scenario, epoch_utc=epoch_utc)

    def load(self, scenario: ScenarioDefinition, *, epoch_utc: datetime) -> None:
        """Validate before replacing a run; loading always starts paused at t=0."""
        scene = ScenarioDefinition.model_validate(scenario.model_dump(mode="json"))
        status = SimulationStatus(
            run_id=self._id_factory(),
            epoch_utc=epoch_utc,
            sim_time_ms=0,
            state="paused",
            playback_rate=scene.config.playback_rate,
        )
        snap = SimulationSnapshot(
            scenario_id=scene.scenario_id,
            config=scene.config,
            status=status,
            snapshot_sequence=0,
            world_version=0,
            robot_pose=scene.robot_pose,
            entities=scene.entities,
            color_cast=scene.initial_light.color_cast,
            light_intensity=scene.initial_light.intensity,
            sensor_availability=scene.sensor_availability,
        )
        self._check_initial(scene, snap)
        self._scene, self._snap = scene, snap.model_copy(deep=True)
        self._cursor = 0
        self._motions: dict[str, tuple[MoveEvent, Point]] = {}
        self._inputs: list[dict] = []
        self._events = deepcopy(scene.events)
        self._robot_command: Pose | None = None
        # Zero-time events are applied on the first advancing tick, never on reads.

    @staticmethod
    def _check_initial(scene: ScenarioDefinition, snap: SimulationSnapshot) -> None:
        solids = [e for e in snap.entities if e.solid]
        for i, entity in enumerate(solids):
            f = entity.footprint
            if _swept(
                snap.robot_pose,
                snap.robot_pose,
                f.x_cm,
                f.y_cm,
                f.width_cm / 2 + scene.robot_radius_cm,
                f.height_cm / 2 + scene.robot_radius_cm,
            ):
                raise ValueError("initial robot/solid overlap")
            for other in solids[i + 1 :]:
                g = other.footprint
                if (
                    abs(f.x_cm - g.x_cm) < (f.width_cm + g.width_cm) / 2
                    and abs(f.y_cm - g.y_cm) < (f.height_cm + g.height_cm) / 2
                ):
                    raise ValueError("initial solid footprints overlap")

    def snapshot(self) -> SimulationSnapshot:
        """Audience-only detached state; reading does not advance time."""
        return self._snap.model_copy(deep=True)

    def control(self, request: SimulationControl) -> None:
        """Validate controls; lifecycle changes never change world_version."""
        request = SimulationControl.model_validate(request.model_dump(mode="json"))
        if request.action == "reset":
            self.reset()
        elif request.action == "step":
            self.step()
        elif request.action == "set_rate":
            self._snap.config.playback_rate = request.playback_rate
            self._snap.status.playback_rate = request.playback_rate
        elif self._snap.status.state != "completed":
            self._snap.status.state = "running" if request.action == "play" else "paused"

    def play(self) -> None:
        self.control(SimulationControl(action="play"))

    def pause(self) -> None:
        self.control(SimulationControl(action="pause"))

    def set_rate(self, rate: float) -> None:
        self.control(SimulationControl(action="set_rate", playback_rate=rate))

    def reset(self) -> None:
        """Restore initial geometry with selected rate, epoch and a fresh run ID."""
        scene = self._scene.model_copy(deep=True)
        scene.config.playback_rate = self._snap.status.playback_rate
        self.load(scene, epoch_utc=self._snap.status.epoch_utc)

    def step(self) -> SimulationSnapshot:
        if self._snap.status.state != "paused":
            raise ValueError("manual step requires paused state")
        self._tick()
        return self.snapshot()

    def advance(self) -> SimulationSnapshot:
        """One runtime tick when running; a paused/completed call is a no-op."""
        if self._snap.status.state == "running":
            self._tick()
        return self.snapshot()

    def queue_event(self, event: ScenarioEvent) -> None:
        """Queue a future physical input. No belief writes or agent notification."""
        event = EVENT.validate_python(EVENT.dump_python(event, mode="json"))
        if self._snap.status.state == "completed" or event.at_ms <= self._snap.status.sim_time_ms:
            raise ValueError("input must target a future tick in an unfinished run")
        events = sorted([*self._events, event], key=lambda e: (e.at_ms, e.sequence))
        candidate = self._scene.model_dump(mode="json")
        candidate["events"] = [EVENT.dump_python(e, mode="json") for e in events]
        ScenarioDefinition.model_validate(candidate)
        self._events = events
        self._inputs.append(
            {
                "kind": "event",
                "at_ms": self._snap.status.sim_time_ms,
                "value": EVENT.dump_python(event, mode="json"),
            }
        )

    def schedule_presenter_event(self, event: ScenarioEvent) -> ScenarioEvent:
        """Assign an order and reserve an exclusive lighting or sensor slot."""
        for scheduled in self._events:
            if scheduled.at_ms != event.at_ms:
                continue
            if isinstance(event, LightingEvent) and isinstance(scheduled, LightingEvent):
                raise ValueError("A lighting change is already scheduled for this tick.")
            if (
                isinstance(event, SensorAvailabilityEvent)
                and isinstance(scheduled, SensorAvailabilityEvent)
                and event.capability == scheduled.capability
            ):
                raise ValueError("A change for this sensor is already scheduled for this tick.")
        sequence = max((scheduled.sequence for scheduled in self._events), default=-1) + 1
        assigned = event.model_copy(update={"sequence": sequence})
        self.queue_event(assigned)
        return assigned

    def command_robot(self, destination: Pose) -> None:
        """Request one next-tick, speed-bounded translation/cardinal turn.

        This internal primitive is not an advertised action or patrol planner.
        Physical collision can reject it; no safety claim is produced.
        """
        pose = Pose.model_validate(destination.model_dump(mode="json"))
        old = self._snap.robot_pose
        r, bounds = self._scene.robot_radius_cm, self._scene.bounds
        if self._snap.status.state == "completed":
            raise ValueError("run completed")
        if (pose.robot_id, pose.location, pose.frame_of_reference) != (
            old.robot_id,
            old.location,
            old.frame_of_reference,
        ):
            raise ValueError("robot identity/room/frame cannot change")
        dx, dy = pose.x_cm - old.x_cm, pose.y_cm - old.y_cm
        limit = self._snap.config.robot_speed_cm_s * self._snap.config.step_ms / 1000
        if (dx and dy) or abs(dx) + abs(dy) > limit + 1e-9:
            raise ValueError("robot command must be cardinal and within per-tick speed")
        if not (r <= pose.x_cm <= bounds.width_cm - r and r <= pose.y_cm <= bounds.height_cm - r):
            raise ValueError("robot footprint outside bounds")
        if self._robot_command is not None:
            raise ValueError("a robot command is already pending")
        self._robot_command = pose
        self._inputs.append(
            {
                "kind": "robot",
                "at_ms": self._snap.status.sim_time_ms,
                "value": pose.model_dump(mode="json"),
            }
        )

    def _move(self, snap: SimulationSnapshot, entity_id: str, target: Point, sweeps: list) -> None:
        entity = next(e for e in snap.entities if e.entity_id == entity_id)
        f = entity.footprint
        if entity.solid:
            for x0, y0, x1, y1 in sweeps:
                if _swept(
                    f,
                    target,
                    (x0 + x1) / 2,
                    (y0 + y1) / 2,
                    (x1 - x0 + f.width_cm) / 2,
                    (y1 - y0 + f.height_cm) / 2,
                ):
                    return
            for other in snap.entities:
                if other.entity_id == entity_id or not other.solid:
                    continue
                g = other.footprint
                if _swept(
                    f,
                    target,
                    g.x_cm,
                    g.y_cm,
                    (f.width_cm + g.width_cm) / 2,
                    (f.height_cm + g.height_cm) / 2,
                ):
                    return
            r = self._scene.robot_radius_cm
            if _swept(
                f,
                target,
                snap.robot_pose.x_cm,
                snap.robot_pose.y_cm,
                f.width_cm / 2 + r,
                f.height_cm / 2 + r,
            ):
                return
            sweeps.append(
                (
                    min(f.x_cm, target.x_cm) - f.width_cm / 2,
                    min(f.y_cm, target.y_cm) - f.height_cm / 2,
                    max(f.x_cm, target.x_cm) + f.width_cm / 2,
                    max(f.y_cm, target.y_cm) + f.height_cm / 2,
                )
            )
        f.x_cm, f.y_cm = target.x_cm, target.y_cm

    def _tick(self) -> None:
        # Build a candidate so failures never partially mutate an advancing tick.
        snap, motions = self.snapshot(), deepcopy(self._motions)
        t = snap.status.sim_time_ms + snap.config.step_ms
        before = snap.model_dump(
            include={
                "entities",
                "robot_pose",
                "color_cast",
                "light_intensity",
                "sensor_availability",
            }
        )
        sweeps: list[tuple[float, float, float, float]] = []
        # Finish existing segments at the boundary before capturing a new origin.
        for entity_id, (event, origin) in list(motions.items()):
            fraction = min(1.0, (t - event.at_ms) / event.duration_ms)
            self._move(
                snap,
                entity_id,
                Point(
                    x_cm=origin.x_cm + (event.to.x_cm - origin.x_cm) * fraction,
                    y_cm=origin.y_cm + (event.to.y_cm - origin.y_cm) * fraction,
                ),
                sweeps,
            )
            if t >= event.at_ms + event.duration_ms:
                del motions[entity_id]
        cursor = self._cursor
        while cursor < len(self._events) and self._events[cursor].at_ms <= t:
            event = self._events[cursor]
            if isinstance(event, MoveEvent):
                f = next(e.footprint for e in snap.entities if e.entity_id == event.entity_id)
                origin = Point(x_cm=f.x_cm, y_cm=f.y_cm)
                motions[event.entity_id] = (event, origin)
                # t=0 events first advance at t=step; all others start at ratio zero.
                fraction = min(1.0, (t - event.at_ms) / event.duration_ms)
                self._move(
                    snap,
                    event.entity_id,
                    Point(
                        x_cm=origin.x_cm + (event.to.x_cm - origin.x_cm) * fraction,
                        y_cm=origin.y_cm + (event.to.y_cm - origin.y_cm) * fraction,
                    ),
                    sweeps,
                )
            elif isinstance(event, LightingEvent):
                snap.color_cast, snap.light_intensity = event.color_cast, event.intensity
            else:
                setattr(snap.sensor_availability, event.capability, event.available)
            cursor += 1
        if self._robot_command is not None:
            old, target = snap.robot_pose, self._robot_command
            r = self._scene.robot_radius_cm
            blocked = any(
                _swept(
                    old,
                    target,
                    e.footprint.x_cm,
                    e.footprint.y_cm,
                    e.footprint.width_cm / 2 + r,
                    e.footprint.height_cm / 2 + r,
                )
                for e in snap.entities
                if e.solid
            )
            blocked = blocked or any(
                _swept(
                    old, target, (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 + r, (y1 - y0) / 2 + r
                )
                for x0, y0, x1, y1 in sweeps
            )
            if not blocked:
                snap.robot_pose = target.model_copy(deep=True)
        after = snap.model_dump(include=set(before))
        snap.world_version += before != after
        snap.snapshot_sequence += 1
        snap.status.sim_time_ms = t
        if t == snap.config.duration_ms:
            snap.status.state = "completed"
        SimulationSnapshot.model_validate(snap.model_dump(mode="json"))
        self._snap, self._motions, self._cursor = snap, motions, cursor
        self._robot_command = None

    @property
    def pending_robot_command(self) -> Pose | None:
        """Detached pending command for an atomic runtime/controller checkpoint handoff."""
        return self._robot_command.model_copy(deep=True) if self._robot_command else None

    def export_state(self) -> dict:
        """Versioned replay checkpoint; includes input order and pending commands."""
        return {
            "version": 1,
            "scenario": self._scene.model_dump(mode="json"),
            "snapshot": self._snap.model_dump(mode="json"),
            "inputs": deepcopy(self._inputs),
        }

    @classmethod
    def restore(cls, state: dict, *, id_factory: Callable[[], UUID] = uuid4) -> SimulationEngine:
        """Reconstruct and validate a checkpoint by deterministic replay, not trust."""
        state = deepcopy(state)
        if set(state) != {"version", "scenario", "snapshot", "inputs"} or state["version"] != 1:
            raise ValueError("unsupported checkpoint")
        target = SimulationSnapshot.model_validate(state["snapshot"])
        scene = ScenarioDefinition.model_validate(state["scenario"])
        # Playback may legitimately change during a run; all simulation semantics
        # must still agree with the scenario used to reconstruct its history.
        if scene.config.model_dump(exclude={"playback_rate"}) != target.config.model_dump(
            exclude={"playback_rate"}
        ):
            raise ValueError("checkpoint scenario and snapshot configuration disagree")
        if scene.scenario_id != target.scenario_id:
            raise ValueError("checkpoint scenario identity disagrees with snapshot")
        engine = cls(
            scene, epoch_utc=target.status.epoch_utc, id_factory=lambda: target.status.run_id
        )
        inputs = state["inputs"]
        cursor = 0
        while True:
            while (
                cursor < len(inputs) and inputs[cursor]["at_ms"] == engine._snap.status.sim_time_ms
            ):
                item = inputs[cursor]
                if set(item) != {"kind", "at_ms", "value"}:
                    raise ValueError("invalid input record")
                if item["kind"] == "event":
                    engine.queue_event(EVENT.validate_python(item["value"]))
                elif item["kind"] == "robot":
                    engine.command_robot(Pose.model_validate(item["value"]))
                else:
                    raise ValueError("unknown input kind")
                cursor += 1
            if engine._snap.status.sim_time_ms == target.status.sim_time_ms:
                break
            previous_time = engine._snap.status.sim_time_ms
            engine.play()
            engine.advance()
            replay_time = engine._snap.status.sim_time_ms
            if replay_time <= previous_time or replay_time > target.status.sim_time_ms:
                raise ValueError("checkpoint replay cannot advance to the saved time")
        if cursor != len(inputs):
            raise ValueError("input log is unordered or beyond checkpoint")
        engine.set_rate(target.status.playback_rate)
        engine._snap.status.state = target.status.state
        if engine.snapshot() != target:
            raise ValueError("checkpoint does not match deterministic replay")
        engine._id_factory = id_factory
        return engine
