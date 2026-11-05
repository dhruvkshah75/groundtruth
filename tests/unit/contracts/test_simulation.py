"""Executable contract/fixture gate for P2-01 (no live engine or sensor tests)."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.contracts import (
    CameraScan,
    ObservationSample,
    Point,
    ScenarioDefinition,
    SensorObservation,
    SensorSource,
    SimulationConfig,
    SimulationControl,
    SimulationSnapshot,
    SimulationStatus,
    TemporalObservationQuery,
    simulation,
)
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import RobotState, WorldState

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = json.loads((ROOT / "tests/fixtures/phase_two/contracts.json").read_text())
VALID = BUNDLE["valid"]


@pytest.mark.parametrize(
    "name,model",
    [
        ("configurations", SimulationConfig),
        ("statuses", SimulationStatus),
        ("snapshots", SimulationSnapshot),
        ("samples", ObservationSample),
        ("camera_scans", CameraScan),
        ("queries", TemporalObservationQuery),
        ("controls", SimulationControl),
    ],
)
def test_shared_valid_examples_round_trip(name, model):
    for payload in VALID[name]:
        parsed = model.model_validate(payload)
        assert model.model_validate_json(parsed.model_dump_json()) == parsed
    scene = ScenarioDefinition.model_validate(VALID["scenario"])
    assert ScenarioDefinition.model_validate_json(scene.model_dump_json()) == scene


@pytest.mark.parametrize("case", BUNDLE["invalid"])
def test_shared_invalid_examples_rejected(case):
    with pytest.raises(ValidationError):
        getattr(simulation, case["contract"]).model_validate(case["payload"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "0.5", 0, 2])
def test_rate_rejection_across_config_status_and_control(value):
    for model, payload in [
        (SimulationConfig, {}),
        (SimulationStatus, VALID["statuses"][0]),
        (SimulationControl, {"action": "set_rate"}),
    ]:
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "playback_rate": value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("step_ms", 0),
        ("step_ms", True),
        ("step_ms", 100.0),
        ("sensor_interval_ms", 550),
        ("duration_ms", 30001),
        ("camera_fov_deg", 0),
        ("camera_fov_deg", 361),
        ("camera_range_cm", float("inf")),
        ("robot_speed_cm_s", -1),
        ("freshness_ms", -1),
        ("stopping_distance_cm", 201),
    ],
)
def test_invalid_configuration_boundaries(field, value):
    with pytest.raises(ValidationError):
        SimulationConfig.model_validate({field: value})


def test_pacing_does_not_change_simulation_defaults():
    base = SimulationConfig().model_dump(exclude={"playback_rate"})
    for rate, seconds in [(0.25, 120), (0.5, 60), (1.0, 30)]:
        config = SimulationConfig(playback_rate=rate)
        assert config.model_dump(exclude={"playback_rate"}) == base
        assert config.duration_ms / rate / 1000 == seconds
        assert config.step_ms / rate == {0.25: 400, 0.5: 200, 1.0: 100}[rate]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(sim_time_ms=-1),
        lambda p: p.update(sim_time_ms=0.5),
        lambda p: p.update(sample_sequence=True),
        lambda p: p.update(captured_at="2026-01-01T00:00:01Z"),
        lambda p: p.update(epoch_utc="2026-01-01T00:00:00"),
        lambda p: p.update(world_version=99),
        lambda p: p["observer_pose"].update(direction="south", robot_id="other"),
        lambda p: p["result"]["context"].update(frame_of_reference="robot_base"),
        lambda p: p["result"]["context"]["extra_context"].update(script=[]),
        lambda p: p["result"]["measurements"]["objects"][0].update(intrinsic_color="red"),
        lambda p: p.update(world={"hidden_parcel": [300, 400]}),
        lambda p: p["result"].update(observed_at="2026-01-01T00:00:01Z"),
        lambda p: p["request"].update(capability="lidar_scan"),
        lambda p: p["result"]["measurements"]["objects"].reverse(),
        lambda p: p["result"]["measurements"]["objects"].append(
            deepcopy(p["result"]["measurements"]["objects"][0])
        ),
        lambda p: p["result"]["measurements"]["objects"][0]["position"].update(x_cm=float("nan")),
    ],
)
def test_inconsistent_or_leaking_sample_rejected(mutation):
    payload = deepcopy(VALID["samples"][0])
    mutation(payload)
    with pytest.raises(ValidationError):
        ObservationSample.model_validate(payload)


def test_failure_has_identity_without_hidden_properties():
    empty = ObservationSample.model_validate(VALID["samples"][8])
    missing = ObservationSample.model_validate(VALID["samples"][3])
    outage = ObservationSample.model_validate(VALID["samples"][4])
    assert isinstance(empty.result, SensorObservation)
    assert CameraScan.model_validate(empty.result.measurements).objects == []
    assert missing.result.reason == "target_not_visible"
    assert outage.result.reason == "sensor_unavailable"
    assert len({s.sample_id for s in (empty, missing, outage)}) == 3
    assert missing.sim_time_ms == VALID["samples"][2]["sim_time_ms"]
    bad = missing.model_dump(mode="json")
    bad["result"]["message"] = "Parcel moved behind shelf_01 to (360,300)."
    with pytest.raises(ValidationError):
        ObservationSample.model_validate(bad)


def test_run_identity_and_simulation_time_are_independent_of_versions():
    sample = ObservationSample.model_validate(VALID["samples"][0])
    other = sample.model_dump(mode="json")
    other["run_id"] = "00000000-0000-0000-0000-000000000002"
    assert ObservationSample.model_validate(other).sim_time_ms == sample.sim_time_ms
    assert sample.result.observed_at == sample.epoch_utc + timedelta(
        milliseconds=sample.sim_time_ms
    )
    assert ObservationSample.model_validate(VALID["samples"][1]).world_version == 1
    assert ObservationSample.model_validate(VALID["samples"][1]).sim_time_ms == 7000


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["entities"][0].update(entity_id=p["entities"][1]["entity_id"]),
        lambda p: p["entities"][0]["footprint"].update(x_cm=-10),
        lambda p: p["events"][0].update(entity_id="unknown"),
        lambda p: p["events"][0].update(duration_ms=30000),
        lambda p: p["events"][0].update(at_ms=5010),
        lambda p: p["events"].reverse(),
        lambda p: p["events"].insert(0, deepcopy(p["events"][0])),
        lambda p: p["events"][3].update(at_ms=6000),
        lambda p: p["patrol"][1].update(x_cm=200),
        lambda p: p["routes"][0]["end"].update(x_cm=200),
    ],
)
def test_invalid_scenario_links_geometry_and_ordering(mutation):
    payload = deepcopy(VALID["scenario"])
    mutation(payload)
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(payload)


def test_same_time_events_use_unique_stable_sequence():
    payload = deepcopy(VALID["scenario"])
    payload["events"][2]["at_ms"] = 10000
    payload["events"][2]["sequence"] = 1
    ScenarioDefinition.model_validate(payload)
    payload["events"][2]["sequence"] = 0
    with pytest.raises(ValidationError, match="unique"):
        ScenarioDefinition.model_validate(payload)


def test_fixture_has_meaningful_warehouse_and_safe_static_patrol():
    scene = ScenarioDefinition.model_validate(VALID["scenario"])
    by_kind = {
        kind: [e for e in scene.entities if e.kind == kind]
        for kind in ("parcel", "station", "shelf", "cart")
    }
    assert [len(by_kind[k]) for k in ("parcel", "station", "shelf", "cart")] == [3, 2, 2, 1]
    assert by_kind["parcel"][0].intrinsic_color == by_kind["parcel"][1].intrinsic_color
    assert all(e.opaque and e.solid for e in by_kind["shelf"])
    assert len(scene.routes) == 2
    # Static clearance proof for this fixture's horizontal/vertical patrol segments.
    # This is NOT the P2-02 collision engine or P2-03 sensing implementation.
    for start, end in zip(scene.patrol, scene.patrol[1:], strict=False):
        for shelf in by_kind["shelf"]:
            f = shelf.footprint
            r = scene.robot_radius_cm
            if start.x_cm == end.x_cm:
                assert not (
                    abs(start.x_cm - f.x_cm) <= f.width_cm / 2 + r
                    and max(start.y_cm, end.y_cm) >= f.y_cm - f.height_cm / 2 - r
                    and min(start.y_cm, end.y_cm) <= f.y_cm + f.height_cm / 2 + r
                )
            else:
                assert not (
                    abs(start.y_cm - f.y_cm) <= f.height_cm / 2 + r
                    and max(start.x_cm, end.x_cm) >= f.x_cm - f.width_cm / 2 - r
                    and min(start.x_cm, end.x_cm) <= f.x_cm + f.width_cm / 2 + r
                )
    # Initial north-facing visibility is analytically consistent (no raycasting engine).
    for parcel in by_kind["parcel"][:2]:
        dx = parcel.footprint.x_cm - scene.robot_pose.x_cm
        dy = parcel.footprint.y_cm - scene.robot_pose.y_cm
        assert dy > 0 and abs(dx) <= dy  # within +/-45 degrees
        assert math.hypot(dx, dy) < scene.config.camera_range_cm
    # The static-pose hidden endpoint lies beyond the first opaque shelf.
    hidden = next(e for e in scene.events if e.kind == "move" and e.at_ms == 20000)
    dx = hidden.to.x_cm - scene.robot_pose.x_cm
    dy = hidden.to.y_cm - scene.robot_pose.y_cm
    assert dy > 0 and abs(dx) <= dy
    assert math.hypot(dx, dy) < scene.config.camera_range_cm
    shelf = by_kind["shelf"][0].footprint
    fraction = (shelf.x_cm - scene.robot_pose.x_cm) / (hidden.to.x_cm - scene.robot_pose.x_cm)
    ray_y = scene.robot_pose.y_cm + fraction * (hidden.to.y_cm - scene.robot_pose.y_cm)
    assert 0 < fraction < 1 and abs(ray_y - shelf.y_cm) < shelf.height_cm / 2


def test_snapshot_and_control_cross_field_rules():
    payload = deepcopy(VALID["snapshots"][0])
    payload["status"]["state"] = "completed"
    with pytest.raises(ValidationError):
        SimulationSnapshot.model_validate(payload)
    payload["status"]["state"] = "paused"
    payload["status"]["playback_rate"] = 1
    with pytest.raises(ValidationError):
        SimulationSnapshot.model_validate(payload)
    with pytest.raises(ValidationError):
        SimulationControl(action="set_rate")


def test_minimal_protocol_preserves_existing_environment_and_legacy_readings():
    env = MockEnvironment(
        WorldState(
            robot=RobotState(
                robot_id="robot",
                x_cm=0,
                y_cm=0,
                direction="north",
                location="room",
                frame_of_reference="robot_base",
            )
        )
    )
    assert isinstance(env, SensorSource)
    assert {n for n in SensorSource.__dict__ if not n.startswith("_")} == {"observe"}
    legacy = env.observe(simulation.ObservationRequest(capability="robot_pose"))
    assert SensorObservation.model_validate_json(legacy.model_dump_json()) == legacy


def test_typescript_and_fixture_generation_is_current():
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_simulation_types.py"), "--check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_numeric_geometry_and_boolean_flags_are_not_coerced_from_text():
    with pytest.raises(ValidationError):
        Point(x_cm="10", y_cm=0)
    with pytest.raises(ValidationError):
        Point(x_cm=True, y_cm=0)
    entity = deepcopy(VALID["scenario"]["entities"][0])
    entity["visible"] = "false"
    with pytest.raises(ValidationError):
        simulation.SceneEntity.model_validate(entity)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["request"].update(parameters={"debug_world": True}),
        lambda p: p["request"].update(target="parcel_03"),
        lambda p: p["result"]["measurements"]["objects"][0].update(frame_of_reference="other"),
        lambda p: p.update(sim_time_ms=10**30),
    ],
)
def test_invalid_request_and_capture_metadata_rejected(mutation):
    payload = deepcopy(VALID["samples"][0])
    mutation(payload)
    with pytest.raises(ValidationError):
        ObservationSample.model_validate(payload)


def test_fixture_changed_position_matches_station_and_nominal_patrol_view():
    scene = ScenarioDefinition.model_validate(VALID["scenario"])
    move = scene.events[0]
    station = next(e for e in scene.entities if e.entity_id == "inspection_station")
    assert move.to == Point(x_cm=station.footprint.x_cm, y_cm=station.footprint.y_cm)
    # Before the first cart event, nominal unblocked patrol remains on route A.
    capture_ms = move.at_ms + move.duration_ms
    observer_y = scene.robot_pose.y_cm + scene.config.robot_speed_cm_s * capture_ms / 1000
    dx, dy = move.to.x_cm - scene.robot_pose.x_cm, move.to.y_cm - observer_y
    assert dy > 0 and abs(dx) <= dy
    assert math.hypot(dx, dy) < scene.config.camera_range_cm
    sample = ObservationSample.model_validate(VALID["samples"][1])
    assert sample.result.measurements["objects"][0]["position"] == move.to.model_dump()


def test_22_second_broad_scan_retains_the_unoccluded_second_parcel():
    """Fixture-specific geometry check, not a substitute for P2-03 sensing."""
    scene = ScenarioDefinition.model_validate(VALID["scenario"])
    sample = ObservationSample.model_validate(VALID["samples"][2])
    assert sample.sim_time_ms == 22000
    assert sample.request.target is None
    pose = sample.observer_pose
    parcel = next(e for e in scene.entities if e.entity_id == "parcel_02")
    assert not any(e.kind == "move" and e.entity_id == "parcel_02" for e in scene.events)
    dx = parcel.footprint.x_cm - pose.x_cm
    dy = parcel.footprint.y_cm - pose.y_cm
    assert pose.direction == "north" and dy > 0 and abs(dx) < dy
    assert math.hypot(dx, dy) < scene.config.camera_range_cm
    # Shelves lie east of the ray; the cleared cart lies beyond its destination.
    assert all(
        e.footprint.x_cm - e.footprint.width_cm / 2 > parcel.footprint.x_cm
        or e.footprint.y_cm - e.footprint.height_cm / 2 > parcel.footprint.y_cm
        for e in scene.entities
        if e.opaque
    )
    detections = CameraScan.model_validate(sample.result.measurements).objects
    assert [d.object_id for d in detections] == ["parcel_02"]
    assert detections[0].position == Point(x_cm=parcel.footprint.x_cm, y_cm=parcel.footprint.y_cm)
    assert sample.result.context.extra_context["lighting"] == "yellow"
    assert detections[0].apparent_color == "brown"


def test_23_second_lidar_cannot_report_a_hit_on_the_clear_reference_ray():
    scene = ScenarioDefinition.model_validate(VALID["scenario"])
    sample = ObservationSample.model_validate(VALID["samples"][5])
    assert sample.sim_time_ms == 23000 and sample.observer_pose.direction == "north"
    # At 22 s the cart has completed its clearance; until 23 s only light changes.
    snapshot = SimulationSnapshot.model_validate(VALID["snapshots"][2])
    assert snapshot.status.sim_time_ms == 22000
    assert all(e.kind == "lighting" for e in scene.events if 22000 < e.at_ms <= 23000)
    pose = sample.observer_pose
    for entity in snapshot.entities:
        if not entity.solid:
            continue
        f = entity.footprint
        spans_ray_x = abs(f.x_cm - pose.x_cm) <= f.width_cm / 2
        overlaps_sensor_y = (
            f.y_cm + f.height_cm / 2 >= pose.y_cm
            and f.y_cm - f.height_cm / 2 <= pose.y_cm + scene.config.lidar_range_cm
        )
        assert not (spans_ray_x and overlaps_sensor_y)
    assert sample.result.measurements == {
        "detected": False,
        "nearest_distance_cm": None,
        "direction": "front",
        "status": "clear",
    }


def test_empty_scan_example_faces_away_from_all_parcels():
    scene = ScenarioDefinition.model_validate(VALID["scenario"])
    sample = ObservationSample.model_validate(VALID["samples"][8])
    assert sample.observer_pose.direction == "south"
    assert sample.sim_time_ms == 23500
    snapshot = SimulationSnapshot.model_validate(VALID["snapshots"][2])
    assert not any(e.kind == "move" and 22000 < e.at_ms <= 23500 for e in scene.events)
    assert all(e.footprint.y_cm > sample.observer_pose.y_cm for e in snapshot.entities)
    assert CameraScan.model_validate(sample.result.measurements).objects == []
