"""Headless engine acceptance checks; no clocks, sleeps, sensors or providers."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from src.contracts.simulation import (
    LightingEvent,
    MoveEvent,
    Point,
    Pose,
    ScenarioDefinition,
    SensorAvailabilityEvent,
)
from src.sensorimotor.simulation_engine import SimulationEngine

DATA = json.loads((Path(__file__).parents[2] / "fixtures/phase_two/contracts.json").read_text())


def engine(scene=None):
    ids = iter(UUID(int=i) for i in range(1, 100))
    return SimulationEngine(
        ScenarioDefinition.model_validate(scene or DATA["valid"]["scenario"]),
        epoch_utc=datetime(2026, 1, 1, tzinfo=UTC),
        id_factory=lambda: next(ids),
    )


def until(e, t):
    e.play()
    while e.snapshot().status.sim_time_ms < t:
        e.advance()


def entity(e, name="parcel_01"):
    return next(x for x in e.snapshot().entities if x.entity_id == name)


def test_lifecycle_300_ticks_and_rate_independent_results():
    reference = None
    for rate in (0.25, 0.5, 1):
        e = engine()
        e.set_rate(rate)
        original = e.snapshot()
        assert e.advance() == original
        e.play()
        e.play()
        with pytest.raises(ValueError):
            e.step()
        until(e, 30000)
        assert e.snapshot().status.state == "completed"
        e.play()
        e.pause()
        assert e.advance().status.sim_time_ms == 30000
        snap = e.snapshot().model_dump(exclude={"config", "status"})
        if reference is None:
            reference = snap
        assert snap == reference
        old = e.snapshot().status.run_id
        e.reset()
        assert e.snapshot().status.run_id != old
        assert e.snapshot().status.playback_rate == rate
        assert e.snapshot().status.sim_time_ms == 0
        assert e.snapshot().status.state == "paused"
        assert e.export_state()["inputs"] == []


def test_motion_midpoint_endpoint_and_detached_snapshots():
    e = engine()
    until(e, 6000)
    assert entity(e).footprint.x_cm == 115
    assert entity(e).footprint.y_cm == 370
    until(e, 7000)
    assert entity(e).footprint.x_cm == 150
    assert entity(e).footprint.y_cm == 390
    s = e.snapshot()
    s.entities[0].footprint.x_cm = -999
    assert entity(e).footprint.x_cm == 150
    e.pause()
    before = e.snapshot()
    e.advance()
    assert e.snapshot() == before
    e.step()
    assert e.snapshot().status.sim_time_ms == 7100


def test_time_only_ticks_and_single_physical_version_increment():
    e = engine()
    e.step()
    assert e.snapshot().world_version == 0
    e.queue_event(
        LightingEvent(kind="lighting", at_ms=200, sequence=0, color_cast="yellow", intensity=0.8)
    )
    e.queue_event(
        SensorAvailabilityEvent(
            kind="sensor_availability",
            at_ms=200,
            sequence=1,
            capability="camera_detect",
            available=False,
        )
    )
    e.step()
    assert e.snapshot().world_version == 1
    assert not e.snapshot().sensor_availability.camera_detect
    e.pause()
    e.play()
    e.set_rate(1)
    assert e.snapshot().world_version == 1
    e.advance()
    assert e.snapshot().world_version == 1


def test_same_time_inputs_once_and_invalid_inputs_atomic():
    e = engine()
    for sequence, cast in [(0, "yellow"), (1, "neutral")]:
        e.queue_event(
            LightingEvent(
                kind="lighting", at_ms=100, sequence=sequence, color_cast=cast, intensity=1
            )
        )
    before = e.export_state()
    with pytest.raises(ValueError):
        e.queue_event(
            MoveEvent(
                kind="move",
                at_ms=100,
                sequence=2,
                entity_id="missing",
                to=Point(x_cm=20, y_cm=20),
                duration_ms=100,
            )
        )
    assert e.export_state() == before
    with pytest.raises(ValueError):
        e.set_rate(0.75)
    assert e.export_state() == before
    e.step()
    assert e.snapshot().color_cast == "neutral"
    e.step()
    assert e.snapshot().color_cast == "neutral"


def test_restore_mid_script_with_future_input_and_pending_robot():
    e = engine()
    until(e, 6000)
    e.pause()
    e.set_rate(0.25)
    e.queue_event(
        LightingEvent(kind="lighting", at_ms=8000, sequence=0, color_cast="blue", intensity=0.5)
    )
    pose = e.snapshot().robot_pose.model_dump()
    pose["y_cm"] += 3
    e.command_robot(Pose(**pose))
    saved = e.export_state()
    restored = SimulationEngine.restore(saved)
    assert restored.snapshot() == e.snapshot()
    assert restored.export_state() == saved
    until(e, 22000)
    until(restored, 22000)
    assert restored.snapshot() == e.snapshot()
    saved["snapshot"]["entities"][0]["footprint"]["x_cm"] = 999
    with pytest.raises(ValueError):
        SimulationEngine.restore(saved)


def test_initial_overlap_and_bad_load_leave_existing_run_intact():
    e = engine()
    before = e.export_state()
    scene = deepcopy(DATA["valid"]["scenario"])
    scene["entities"][-1]["footprint"].update(x_cm=100, y_cm=100)
    with pytest.raises(ValueError):
        e.load(ScenarioDefinition.model_validate(scene), epoch_utc=datetime(2026, 1, 1, tzinfo=UTC))
    assert e.export_state() == before


def test_swept_cart_cannot_tunnel_through_shelf():
    scene = deepcopy(DATA["valid"]["scenario"])
    scene["events"] = []
    scene["entities"][-1]["footprint"].update(x_cm=200, y_cm=300)
    e = engine(scene)
    e.queue_event(
        MoveEvent(
            kind="move",
            at_ms=100,
            sequence=0,
            entity_id="cart_01",
            to=Point(x_cm=400, y_cm=300),
            duration_ms=100,
        )
    )
    until(e, 200)
    assert entity(e, "cart_01").footprint.x_cm == 200


def test_robot_collision_bounds_and_command_validation():
    scene = deepcopy(DATA["valid"]["scenario"])
    scene["events"] = []
    scene["robot_pose"].update(x_cm=269, y_cm=300)
    scene["patrol"] = [{"x_cm": 269, "y_cm": 300}]
    e = engine(scene)
    pose = e.snapshot().robot_pose.model_dump()
    pose["x_cm"] = 272
    e.command_robot(Pose(**pose))
    e.step()
    assert e.snapshot().robot_pose.x_cm == 269  # shelf starts at x=285; radius=15
    for x in (900, 280, float("inf")):
        before = e.export_state()
        with pytest.raises(ValueError):
            e.command_robot(Pose(**{**pose, "x_cm": x}))
        assert e.export_state() == before


def test_restore_rejects_invalid_input_log_and_completed_reset():
    e = engine()
    until(e, 30000)
    saved = e.export_state()
    assert SimulationEngine.restore(saved).snapshot() == e.snapshot()
    saved["inputs"] = [{"kind": "event", "at_ms": -1, "value": {}}]
    with pytest.raises(ValueError):
        SimulationEngine.restore(saved)


def test_crossing_solid_trajectories_cannot_pass_through_each_other():
    scene = deepcopy(DATA["valid"]["scenario"])
    scene["events"] = []
    cart = scene["entities"][-1]
    cart["footprint"].update(x_cm=200, y_cm=100)
    other = deepcopy(cart)
    other["entity_id"] = "cart_02"
    other["footprint"].update(x_cm=300, y_cm=50)
    scene["entities"].append(other)
    e = engine(scene)
    e.queue_event(
        MoveEvent(
            kind="move",
            at_ms=100,
            sequence=0,
            entity_id="cart_01",
            to=Point(x_cm=400, y_cm=100),
            duration_ms=100,
        )
    )
    e.queue_event(
        MoveEvent(
            kind="move",
            at_ms=100,
            sequence=1,
            entity_id="cart_02",
            to=Point(x_cm=300, y_cm=150),
            duration_ms=100,
        )
    )
    until(e, 200)
    assert entity(e, "cart_02").footprint.y_cm == 50


def test_zero_time_events_and_chained_motion_resume_exactly():
    scene = deepcopy(DATA["valid"]["scenario"])
    scene["events"] = [
        {
            "kind": "move",
            "at_ms": 0,
            "sequence": 0,
            "entity_id": "parcel_01",
            "to": {"x_cm": 90, "y_cm": 350},
            "duration_ms": 100,
        },
        {
            "kind": "move",
            "at_ms": 100,
            "sequence": 0,
            "entity_id": "parcel_01",
            "to": {"x_cm": 110, "y_cm": 350},
            "duration_ms": 200,
        },
    ]
    e = engine(scene)
    assert entity(e).footprint.x_cm == 80
    e.step()
    assert entity(e).footprint.x_cm == 90
    restored = SimulationEngine.restore(e.export_state())
    e.step()
    restored.step()
    assert entity(e).footprint.x_cm == 100
    assert e.snapshot() == restored.snapshot()
    e.step()
    assert entity(e).footprint.x_cm == 110
