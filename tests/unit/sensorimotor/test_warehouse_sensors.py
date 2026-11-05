"""Phase 2 geometry, privacy, snapshot atomicity and engine integration gates."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from src.contracts import (
    ObservationRequest,
    ObservationUnavailable,
    ScenarioDefinition,
    SensorSource,
)
from src.sensorimotor.simulation_engine import SimulationEngine
from src.sensorimotor.warehouse_sensors import WarehouseSensors

DATA = json.loads((Path(__file__).parents[2] / "fixtures/phase_two/contracts.json").read_text())


def setup(snapshot=None):
    snap = deepcopy(snapshot or DATA["valid"]["snapshots"][0])
    from src.contracts import SimulationSnapshot

    snap = SimulationSnapshot.model_validate(snap)
    ids = iter(UUID(int=i) for i in range(100, 1000))
    return snap, WarehouseSensors(lambda: snap, id_factory=lambda: next(ids))


def camera(sensors, target=None):
    return sensors.sample(ObservationRequest(capability="camera_detect", target=target))


def lidar(sensors):
    return sensors.sample(
        ObservationRequest(capability="lidar_scan", parameters={"direction": "front"})
    )


@pytest.mark.parametrize(
    "direction,position",
    [("north", (100, 250)), ("east", (250, 100)), ("south", (100, 50)), ("west", (50, 100))],
)
def test_all_headings_and_frame_metadata(direction, position):
    snap, s = setup()
    snap.robot_pose.direction = direction
    parcel = snap.entities[0]
    parcel.footprint.x_cm, parcel.footprint.y_cm = position
    result = camera(s, "parcel_01")
    d = result.result.measurements["objects"][0]
    assert d["position"] == {"x_cm": position[0], "y_cm": position[1]}
    assert d["frame_of_reference"] == snap.robot_pose.frame_of_reference
    assert result.observer_pose.direction == direction
    assert result.result.observed_at == result.captured_at == snap.status.epoch_utc
    assert isinstance(s, SensorSource)


@pytest.mark.parametrize(
    "x,y,visible",
    [(200, 200, True), (100, 450, True), (201, 200, False), (100, 450.01, False), (100, 50, False)],
)
def test_camera_range_and_fov_edges(x, y, visible):
    snap, s = setup()
    snap.entities[0].footprint.x_cm = x
    snap.entities[0].footprint.y_cm = y
    result = camera(s, "parcel_01").result
    assert isinstance(result, ObservationUnavailable) is not visible


def test_occlusion_and_corner_touch_block_but_nonopaque_annotations_do_not():
    snap, s = setup()
    parcel = snap.entities[0]
    parcel.footprint.x_cm = 330
    parcel.footprint.y_cm = 340
    assert camera(s, "parcel_01").result.reason == "target_not_visible"
    snap.entities[5].opaque = False  # shelf_01
    assert camera(s, "parcel_01").result.measurements["objects"]
    snap.entities[5].opaque = True
    parcel.footprint.x_cm = 250
    parcel.footprint.y_cm = 250
    # Insert an opaque edge touching the center ray at its corner (150,150).
    shelf = snap.entities[5]
    shelf.footprint.x_cm = 160
    shelf.footprint.y_cm = 140
    shelf.footprint.width_cm = shelf.footprint.height_cm = 20
    assert camera(s, "parcel_01").result.reason == "target_not_visible"
    shelf.opaque = False
    assert camera(s, "parcel_01").result.measurements["objects"]


def test_target_privacy_other_room_hidden_unknown_and_empty_vs_outage():
    snap, s = setup()
    parcel = snap.entities[0]
    results = [camera(s, "unknown").result]
    parcel.visible = False
    results.append(camera(s, "parcel_01").result)
    parcel.visible = True
    parcel.location = "other"
    results.append(camera(s, "parcel_01").result)
    parcel.location = "warehouse"
    parcel.footprint.y_cm = 50
    results.append(camera(s, "parcel_01").result)
    assert all(
        r.reason == "target_not_visible" and r.message == "Observation unavailable."
        for r in results
    )
    snap.robot_pose.direction = "south"
    parcel.footprint.y_cm = 350
    assert camera(s).result.measurements == {"objects": []}
    snap.sensor_availability.camera_detect = False
    outage = camera(s)
    assert outage.result.reason == "sensor_unavailable"
    assert outage.sample_id and outage.run_id and outage.captured_at
    snap.sensor_availability.camera_detect = True
    assert camera(s).result.measurements == {"objects": []}


@pytest.mark.parametrize(
    "direction,center",
    [("north", (100, 200)), ("east", (200, 100)), ("south", (100, 0)), ("west", (0, 100))],
)
def test_lidar_surface_all_headings_and_nearest(direction, center):
    snap, s = setup()
    snap.robot_pose.direction = direction
    cart = snap.entities[-1]
    cart.footprint.x_cm, cart.footprint.y_cm = center
    assert lidar(s).result.measurements["nearest_distance_cm"] == 80
    cart.solid = False
    assert lidar(s).result.measurements["detected"] is False
    cart.solid = True
    cart.location = "other"
    assert lidar(s).result.measurements["detected"] is False


def test_lidar_range_edge_zero_hit_and_disabled():
    snap, s = setup()
    cart = snap.entities[-1]
    cart.footprint.x_cm = 100
    cart.footprint.y_cm = 320  # surface exactly at range=200
    assert lidar(s).result.measurements["nearest_distance_cm"] == 200
    cart.footprint.y_cm = 320.01
    assert lidar(s).result.measurements == {
        "detected": False,
        "nearest_distance_cm": None,
        "direction": "front",
        "status": "clear",
    }
    cart.footprint.y_cm = 100
    assert lidar(s).result.measurements["nearest_distance_cm"] == 0
    snap.sensor_availability.lidar_scan = False
    assert lidar(s).result.reason == "sensor_unavailable"


def test_single_snapshot_capture_ids_order_and_light_pose_consistency():
    snap, _ = setup()
    calls = 0

    def supplier():
        nonlocal calls
        calls += 1
        return snap

    s = WarehouseSensors(supplier)
    snap.color_cast = "yellow"
    sample = camera(s)
    assert calls == 1
    assert [d["object_id"] for d in sample.result.measurements["objects"]] == [
        "parcel_01",
        "parcel_02",
    ]
    assert all(
        d["apparent_color"] == "brown" and "intrinsic_color" not in d
        for d in sample.result.measurements["objects"]
    )
    snap.robot_pose.x_cm = 110  # captured result remains detached
    assert sample.observer_pose.x_cm == 100
    light = s.sample(ObservationRequest(capability="ambient_light"))
    pose = s.sample(ObservationRequest(capability="robot_pose"))
    assert light.result.measurements["color_cast"] == "yellow"
    assert pose.result.measurements["x_cm"] == 110
    assert [sample.sample_sequence, light.sample_sequence, pose.sample_sequence] == [0, 1, 2]
    assert len({sample.sample_id, light.sample_id, pose.sample_id}) == 3


def test_invalid_requests_and_zero_projection_geometry():
    snap, s = setup()
    for request in [
        ObservationRequest(capability="weather"),
        ObservationRequest(capability="robot_pose", target="parcel_01"),
        ObservationRequest(capability="camera_detect", parameters={"world": True}),
        ObservationRequest(capability="lidar_scan", parameters={"direction": "back"}),
    ]:
        assert isinstance(s.observe(request), ObservationUnavailable)
    unknown = s.sample(ObservationRequest(capability="weather"))
    assert unknown.result.reason == "unsupported_capability"
    with pytest.raises(ValueError):
        s.sample(ObservationRequest(capability="lidar_scan"))
    snap.entities[0].footprint.x_cm = snap.robot_pose.x_cm
    snap.entities[0].footprint.y_cm = snap.robot_pose.y_cm
    assert camera(s).result.reason == "sensor_unavailable"


def test_real_engine_relocation_hidden_target_and_corrected_fixture_examples():
    e = SimulationEngine(
        ScenarioDefinition.model_validate(DATA["valid"]["scenario"]),
        epoch_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )
    s = WarehouseSensors(e.snapshot)
    before = e.export_state()
    initial = camera(s, "parcel_01")
    assert e.export_state() == before
    assert initial.result.measurements["objects"][0]["position"] == {"x_cm": 80, "y_cm": 350}
    e.play()
    for _ in range(70):
        e.advance()
    changed = camera(s, "parcel_01")
    assert changed.sim_time_ms == 7000
    assert changed.result.measurements["objects"][0]["position"] == {"x_cm": 150, "y_cm": 390}
    for _ in range(150):
        e.advance()
    hidden = camera(s, "parcel_01")
    assert hidden.result.reason == "target_not_visible"
    broad = camera(s)
    assert [d["object_id"] for d in broad.result.measurements["objects"]] == ["parcel_02"]
    assert broad.result.measurements["objects"][0]["apparent_color"] == "brown"
    assert (
        next(x for x in e.snapshot().entities if x.entity_id == "parcel_01").footprint.x_cm == 330
    )
    for _ in range(10):
        e.advance()
    assert lidar(s).result.measurements["detected"] is False
    e.pause()
    before = e.export_state()
    camera(s)
    lidar(s)
    assert e.export_state() == before
    e.reset()
    assert camera(s).sample_sequence == 0


def test_playback_independent_measurements_and_global_sequencer():
    measurements = []
    for rate in (0.25, 0.5, 1):
        snap, s = setup()
        snap.config.playback_rate = snap.status.playback_rate = rate
        measurements.append(camera(s).result.measurements)
    assert measurements[0] == measurements[1] == measurements[2]
    snap, _ = setup()
    counter = iter(range(20))
    a = WarehouseSensors(lambda: snap, sequence_factory=lambda run: next(counter))
    b = WarehouseSensors(lambda: snap, sequence_factory=lambda run: next(counter))
    assert camera(a).sample_sequence == 0 and lidar(b).sample_sequence == 1


def test_nearest_hit_and_occluder_room_and_opaque_flags():
    snap, s = setup()
    cart = snap.entities[-1]
    cart.footprint.x_cm = 100
    cart.footprint.y_cm = 250
    shelf = snap.entities[5]
    shelf.footprint.x_cm = 100
    shelf.footprint.y_cm = 180
    shelf.footprint.height_cm = 20
    assert lidar(s).result.measurements["nearest_distance_cm"] == 70
    shelf.location = "other"
    assert lidar(s).result.measurements["nearest_distance_cm"] == 130
    parcel = snap.entities[0]
    parcel.footprint.x_cm = 100
    parcel.footprint.y_cm = 200
    assert camera(s, "parcel_01").result.measurements["objects"]
    shelf.location = "warehouse"
    assert camera(s, "parcel_01").result.reason == "target_not_visible"
    shelf.opaque = False
    assert camera(s, "parcel_01").result.measurements["objects"]


def test_actual_engine_sensor_outage_and_recovery():
    from src.contracts import SensorAvailabilityEvent

    e = SimulationEngine(
        ScenarioDefinition.model_validate(DATA["valid"]["scenario"]),
        epoch_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )
    s = WarehouseSensors(e.snapshot)
    for seq, cap in enumerate(("camera_detect", "lidar_scan")):
        e.queue_event(
            SensorAvailabilityEvent(
                kind="sensor_availability", at_ms=100, sequence=seq, capability=cap, available=False
            )
        )
        e.queue_event(
            SensorAvailabilityEvent(
                kind="sensor_availability", at_ms=200, sequence=seq, capability=cap, available=True
            )
        )
    e.step()
    assert camera(s).result.reason == lidar(s).result.reason == "sensor_unavailable"
    e.step()
    assert camera(s).result.measurements["objects"]
    assert lidar(s).result.measurements["status"] == "clear"
