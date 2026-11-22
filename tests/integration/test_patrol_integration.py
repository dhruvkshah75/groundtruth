"""Headless integration tests for PatrolController with SimulationEngine (P2-06)."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from src.contracts.models import ObservationRequest
from src.contracts.simulation import ScenarioDefinition
from src.declarative.memory_repository import MemoryRepository
from src.procedural.temporal_belief_ingestion import TemporalBeliefIngestionService
from src.sensorimotor.patrol_controller import PatrolController
from src.sensorimotor.simulation_engine import SimulationEngine
from src.sensorimotor.warehouse_sensors import WarehouseSensors
from src.web.simulation_runtime import SimulationRuntime

FIXTURE_PATH = Path(__file__).parents[2] / "tests/fixtures/phase_two/contracts.json"
DATA = json.loads(FIXTURE_PATH.read_text())


def create_engine_and_sensors(scenario_dict=None):
    scene = ScenarioDefinition.model_validate(scenario_dict or DATA["valid"]["scenario"])
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    id_counter = iter(range(1, 10000))
    eng = SimulationEngine(
        scene,
        epoch_utc=epoch,
        id_factory=lambda: UUID(int=next(id_counter)),
    )
    sensors = WarehouseSensors(
        lambda: eng.snapshot(),
        id_factory=lambda: UUID(int=next(id_counter)),
    )
    return eng, sensors, scene


def get_front_lidar(sensors: WarehouseSensors):
    return sensors.sample(
        ObservationRequest(capability="lidar_scan", parameters={"direction": "front"})
    )


def get_pose(sensors: WarehouseSensors):
    return sensors.sample(ObservationRequest(capability="robot_pose"))


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


def test_patrol_progresses_along_route():
    """Verify robot follows waypoints, commands engine, and updates position."""
    eng, sensors, scene = create_engine_and_sensors()
    eng.play()

    controller = PatrolController(
        patrol=scene.patrol,
        config=scene.config,
        initial_pose=scene.robot_pose,
        run_id=eng.snapshot().status.run_id,
        scenario_id=scene.scenario_id,
    )

    initial_y = scene.robot_pose.y_cm
    assert scene.robot_pose.direction == "north"

    # Step through 10 ticks (1 second) with clear LiDAR
    for _ in range(10):
        t = eng.snapshot().status.sim_time_ms
        pose_sample = get_pose(sensors)
        lidar_sample = get_front_lidar(sensors)

        decision = controller.decide(t, pose_sample, lidar_sample)
        assert decision.status == "moving"
        assert decision.command is not None

        eng.command_robot(decision.command)
        eng.advance()

        reconciliation = controller.reconcile(get_pose(sensors))
        assert reconciliation.executed

    # Robot moved north by 30 cm (30 cm/s * 1s)
    current_y = eng.snapshot().robot_pose.y_cm
    assert current_y == pytest.approx(initial_y + 30.0, abs=1e-3)


def test_obstacle_approach_stop_clear_and_resume():
    """Place a solid cart on the route, observe sensor stop, clear it, and resume."""
    scene_data = deepcopy(DATA["valid"]["scenario"])

    # Place a solid cart directly in the robot's forward path at y=170
    cart_entity = {
        "entity_id": "cart_blocker",
        "kind": "cart",
        "location": "warehouse",
        "footprint": {
            "x_cm": 100.0,
            "y_cm": 170.0,
            "width_cm": 30.0,
            "height_cm": 30.0,
        },
        "intrinsic_color": "white",
        "solid": True,
        "opaque": True,
        "visible": True,
    }
    scene_data["entities"].append(cart_entity)

    eng, sensors, scene = create_engine_and_sensors(scene_data)
    eng.play()

    controller = PatrolController(
        patrol=scene.patrol,
        config=scene.config,
        initial_pose=scene.robot_pose,
        run_id=eng.snapshot().status.run_id,
        scenario_id=scene.scenario_id,
    )

    stopped = False
    stopped_at_y = None

    # Step forward until robot stops for the obstacle
    for _ in range(50):
        t = eng.snapshot().status.sim_time_ms
        pose_sample = get_pose(sensors)
        lidar_sample = get_front_lidar(sensors)

        decision = controller.decide(t, pose_sample, lidar_sample)
        if decision.status == "blocked":
            stopped = True
            stopped_at_y = eng.snapshot().robot_pose.y_cm
            # Clearance must be at or within stopping distance (50 cm)
            assert decision.record.computed_clearance_cm <= scene.config.stopping_distance_cm + 1e-3
            break

        assert decision.command is not None
        eng.command_robot(decision.command)
        eng.advance()
        controller.reconcile(get_pose(sensors))

    assert stopped, "Controller failed to stop before obstacle"
    assert stopped_at_y is not None
    # Robot must have stopped before colliding with the cart
    assert stopped_at_y <= 105.0

    # Next step without moving the cart: controller must stay blocked
    t = eng.snapshot().status.sim_time_ms
    dec_blocked = controller.decide(t, get_pose(sensors), get_front_lidar(sensors))
    assert dec_blocked.status == "blocked"
    assert dec_blocked.command is None

    # Now remove/clear the cart from the path (move it off to the side)
    cart = next(e for e in eng._snap.entities if e.entity_id == "cart_blocker")
    cart.footprint.x_cm = 500.0  # moved far away

    # Now take a fresh clear LiDAR scan and verify controller resumes!
    t_resume = eng.snapshot().status.sim_time_ms
    lidar_clear = get_front_lidar(sensors)
    dec_resumed = controller.decide(t_resume, get_pose(sensors), lidar_clear)

    assert dec_resumed.status == "moving"
    assert dec_resumed.command is not None
    eng.command_robot(dec_resumed.command)
    eng.advance()
    rec = controller.reconcile(get_pose(sensors))
    assert rec.executed
    assert eng.snapshot().robot_pose.y_cm > stopped_at_y


def test_sensor_outage_causes_waiting_and_recovers():
    """Verify that a LiDAR outage stops translation and resumes when restored."""
    eng, sensors, scene = create_engine_and_sensors()
    eng.play()

    controller = PatrolController(
        patrol=scene.patrol,
        config=scene.config,
        initial_pose=scene.robot_pose,
        run_id=eng.snapshot().status.run_id,
        scenario_id=scene.scenario_id,
    )

    # Move 2 ticks with healthy sensor
    for _ in range(2):
        t = eng.snapshot().status.sim_time_ms
        dec = controller.decide(t, get_pose(sensors), get_front_lidar(sensors))
        assert dec.status == "moving"
        eng.command_robot(dec.command)
        eng.advance()
        controller.reconcile(get_pose(sensors))

    # Disable LiDAR availability
    eng._snap.sensor_availability.lidar_scan = False

    t_outage = eng.snapshot().status.sim_time_ms
    outage_lidar = get_front_lidar(sensors)
    dec_outage = controller.decide(t_outage, get_pose(sensors), outage_lidar)

    assert dec_outage.status == "waiting_for_sensor"
    assert dec_outage.command is None

    # Restore LiDAR
    eng._snap.sensor_availability.lidar_scan = True
    t_restored = t_outage
    restored_lidar = get_front_lidar(sensors)
    dec_restored = controller.decide(t_restored, get_pose(sensors), restored_lidar)

    assert dec_restored.status == "moving"
    assert dec_restored.command is not None


def test_playback_rate_independence():
    """Verify controller decisions are identical at equal simulation times across rates."""
    results_by_rate = {}

    for rate in (0.25, 0.5, 1.0, 2.0):
        scene_data = deepcopy(DATA["valid"]["scenario"])
        scene_data["config"]["playback_rate"] = rate
        eng, sensors, scene = create_engine_and_sensors(scene_data)
        eng.set_rate(rate)
        eng.play()

        controller = PatrolController(
            patrol=scene.patrol,
            config=scene.config,
            initial_pose=scene.robot_pose,
            run_id=eng.snapshot().status.run_id,
            scenario_id=scene.scenario_id,
        )

        trace = []
        for _ in range(5):
            t = eng.snapshot().status.sim_time_ms
            p = get_pose(sensors)
            lidar = get_front_lidar(sensors)
            dec = controller.decide(t, p, lidar)
            cmd_x = dec.command.x_cm if dec.command else None
            cmd_y = dec.command.y_cm if dec.command else None
            trace.append((t, dec.status, cmd_x, cmd_y))
            if dec.command:
                eng.command_robot(dec.command)
                eng.advance()
                controller.reconcile(get_pose(sensors))

        results_by_rate[rate] = trace

    assert (
        results_by_rate[0.25]
        == results_by_rate[0.5]
        == results_by_rate[1.0]
        == results_by_rate[2.0]
    )


def test_paired_engine_controller_checkpoint_resumes_pending_tick_once():
    engine, sensors, scene = create_engine_and_sensors()
    engine.play()
    controller = PatrolController(
        scene.patrol,
        scene.config,
        initial_pose=scene.robot_pose,
        run_id=engine.snapshot().status.run_id,
        scenario_id=scene.scenario_id,
    )
    decision = controller.decide(0, get_pose(sensors), get_front_lidar(sensors))
    engine.command_robot(decision.command)
    controller_checkpoint = controller.export_checkpoint(0)
    engine_checkpoint = engine.export_state()
    restored_engine = SimulationEngine.restore(engine_checkpoint)
    restored_sensors = WarehouseSensors(restored_engine.snapshot)
    restored = PatrolController(
        scene.patrol,
        scene.config,
        initial_pose=scene.robot_pose,
        run_id=restored_engine.snapshot().status.run_id,
        scenario_id=scene.scenario_id,
    )
    pending = restored_engine.pending_robot_command
    pending.y_cm = 500
    assert restored_engine.pending_robot_command == decision.command
    restored.restore_checkpoint(
        controller_checkpoint,
        engine_pose_observation=get_pose(restored_sensors),
        engine_pending_command=restored_engine.pending_robot_command,
    )
    with pytest.raises(RuntimeError):
        restored.decide(0, get_pose(restored_sensors), get_front_lidar(restored_sensors))
    restored_engine.advance()
    assert restored.reconcile(get_pose(restored_sensors)).executed
    with pytest.raises(RuntimeError):
        restored.reconcile(get_pose(restored_sensors))
    assert restored_engine.snapshot().robot_pose == decision.command


def test_documented_patrol_example_executes():
    import re

    path = Path(__file__).parents[2] / "docs/phase-two/Patrol_Controller.md"
    blocks = re.findall(r"```python\n(.*?)```", path.read_text(), re.S)
    exec(compile(blocks[-1], str(path), "exec"), {})


def test_patrol_stop_decision_captured_and_preserved_across_later_scans_and_resumption():
    """Verify WD-03 stop decision capture, preservation across scans, and independent readout."""
    scene_data = deepcopy(DATA["valid"]["scenario"])
    # Put a cart at y=170 in the forward path of robot starting at (100, 100)
    cart_entity = {
        "entity_id": "cart_blocker",
        "kind": "cart",
        "location": "warehouse",
        "footprint": {
            "x_cm": 100.0,
            "y_cm": 170.0,
            "width_cm": 30.0,
            "height_cm": 30.0,
        },
        "intrinsic_color": "white",
        "solid": True,
        "opaque": True,
        "visible": True,
    }
    scene_data["entities"].append(cart_entity)
    scene = ScenarioDefinition.model_validate(scene_data)

    memory = MemoryRepository(":memory:")
    ingestion = TemporalBeliefIngestionService(memory)
    clock = [0.0]
    runtime = SimulationRuntime(
        scene,
        epoch_utc=datetime(2026, 1, 1, tzinfo=UTC),
        ingest=ingestion.ingest_sample,
        now=lambda: clock[0],
    )
    # Initially before any tick, decisions are None
    cur, stop = runtime.patrol_decision_summaries()
    assert cur is None
    assert stop is None

    # Step until robot stops for the obstacle
    for _ in range(50):
        runtime.step()
        cur, stop = runtime.patrol_decision_summaries()
        assert cur is not None
        if cur.state == "blocked":
            break

    cur_stop, initial_stop = runtime.patrol_decision_summaries()
    assert cur_stop is not None and cur_stop.state == "blocked"
    assert initial_stop is not None and initial_stop.state == "blocked"
    assert initial_stop.sample_id is not None
    assert initial_stop.measured_distance_cm is not None
    assert initial_stop.computed_clearance_cm is not None
    # Triggering LiDAR sample exists in the same run's ledger
    lidar_sample = memory.get_observation_sample(initial_stop.sample_id)
    assert lidar_sample is not None
    assert lidar_sample.run_id == runtime.run_id
    assert lidar_sample.request.capability == "lidar_scan"

    initial_sample_id = initial_stop.sample_id
    initial_decision_time = initial_stop.sim_time_ms
    initial_sensor_time = initial_stop.sensor_sim_time_ms

    # Step forward while remaining blocked to simulate later scans arriving
    for _ in range(15):
        runtime.step()
        cur_later, stop_later = runtime.patrol_decision_summaries()
        assert cur_later is not None and cur_later.state == "blocked"
        assert stop_later is not None and stop_later.state == "blocked"
        # Later scans do NOT overwrite the original triggering cause of the stop!
        assert stop_later.sample_id == initial_sample_id
        assert stop_later.sim_time_ms == initial_decision_time
        assert stop_later.sensor_sim_time_ms == initial_sensor_time

    # Now remove/clear the cart from the path
    cart = next(e for e in runtime.engine._snap.entities if e.entity_id == "cart_blocker")
    cart.footprint.x_cm = 500.0  # moved far away

    # Step forward: controller acquires clear reading and resumes motion!
    for _ in range(10):
        runtime.step()
        cur_resumed, stop_after_resumption = runtime.patrol_decision_summaries()
        assert cur_resumed is not None
        if cur_resumed.state == "moving":
            break

    cur_resumed, stop_after_resumption = runtime.patrol_decision_summaries()
    assert cur_resumed is not None and cur_resumed.state == "moving"
    # Historical stop decision remains readable independently after resumption!
    assert stop_after_resumption is not None and stop_after_resumption.state == "blocked"
    assert stop_after_resumption.sample_id == initial_sample_id
    assert stop_after_resumption.sim_time_ms == initial_decision_time
    assert stop_after_resumption.sensor_sim_time_ms == initial_sensor_time
    memory.close()


def test_patrol_turn_wait_cart_blocked_resume_preserves_concrete_block():
    """Verify turn -> wait -> cart blocked -> resume preserves concrete block decision [P1]."""
    scene_data = deepcopy(DATA["valid"]["scenario"])
    scene_data["robot_pose"]["direction"] = "east"
    scene_data["entities"].append(
        {
            "entity_id": "cart_blocker",
            "kind": "cart",
            "location": "warehouse",
            "footprint": {"x_cm": 100.0, "y_cm": 140.0, "width_cm": 30.0, "height_cm": 30.0},
            "intrinsic_color": "white",
            "solid": True,
            "opaque": True,
            "visible": True,
        }
    )
    scene = ScenarioDefinition.model_validate(scene_data)
    memory = MemoryRepository(":memory:")
    ingestion = TemporalBeliefIngestionService(memory)
    clock = [0.0]
    runtime = SimulationRuntime(
        scene,
        epoch_utc=datetime(2026, 1, 1, tzinfo=UTC),
        ingest=ingestion.ingest_sample,
        now=lambda: clock[0],
    )

    # Step 1: turning
    runtime.step()
    cur, stop = runtime.patrol_decision_summaries()
    assert cur is not None and cur.state == "turning"

    # Step 2: wait for aligned scan
    runtime.step()
    cur, stop = runtime.patrol_decision_summaries()
    assert cur is not None and cur.state == "waiting_for_sensor"
    assert stop is not None and stop.state == "waiting_for_sensor"

    # Step 6: concrete block upgrades saved decision from wait to blocked
    for _ in range(4):
        runtime.step()
    cur, stop = runtime.patrol_decision_summaries()
    assert cur is not None and cur.state == "blocked"
    assert stop is not None and stop.state == "blocked"
    initial_lidar_uuid = stop.sample_id
    assert initial_lidar_uuid is not None

    # Outage during stop episode does NOT overwrite the concrete block
    runtime.engine._snap.sensor_availability.lidar_scan = False
    for _ in range(5):
        runtime.step()
    cur, stop = runtime.patrol_decision_summaries()
    assert cur is not None and cur.state == "waiting_for_sensor"
    assert stop is not None and stop.state == "blocked"
    assert stop.sample_id == initial_lidar_uuid

    # Restore sensor and clear cart
    runtime.engine._snap.sensor_availability.lidar_scan = True
    cart = next(e for e in runtime.engine._snap.entities if e.entity_id == "cart_blocker")
    cart.footprint.x_cm = 500.0

    for _ in range(10):
        runtime.step()
        cur, stop = runtime.patrol_decision_summaries()
        if cur is not None and cur.state == "moving":
            break

    cur, stop = runtime.patrol_decision_summaries()
    assert cur is not None and cur.state == "moving"
    assert stop is not None and stop.state == "blocked"
    assert stop.sample_id == initial_lidar_uuid
    memory.close()


def test_patrol_cart_crossing_stall_reason_published_at_post_tick_boundary():
    """Verify actual stall reason and discrepancy are published at post-tick boundary [P2]."""
    scene_data = deepcopy(DATA["valid"]["scenario"])
    scene = ScenarioDefinition.model_validate(scene_data)
    memory = MemoryRepository(":memory:")
    ingestion = TemporalBeliefIngestionService(memory)
    clock = [0.0]
    runtime = SimulationRuntime(
        scene,
        epoch_utc=datetime(2026, 1, 1, tzinfo=UTC),
        ingest=ingestion.ingest_sample,
        now=lambda: clock[0],
    )

    # Step once to establish motion
    runtime.step()
    cur, stop = runtime.patrol_decision_summaries()
    assert cur is not None and cur.state == "moving"

    # Before next step, script a cart to cross directly in front of the robot at y=103
    cart = next(e for e in runtime.engine._snap.entities if e.entity_id == "cart_01")
    cart.footprint.x_cm = 100.0
    cart.footprint.y_cm = 103.0

    # Step: controller commands motion to y=106, but engine rejects because cart occupies it
    runtime.step()
    cur, stop = runtime.patrol_decision_summaries()
    assert cur is not None and cur.state == "stalled"
    assert "Movement rejected" in cur.policy_reason
    assert cur.sample_id is None
    assert cur.sim_time_ms == runtime._pose.sim_time_ms
    assert stop is not None and stop.state == "stalled"
    assert "Movement rejected" in stop.policy_reason
    assert stop.sample_id is None

    # Subsequent tick: decide runs and requests fresh scan (waiting_for_sensor)
    runtime.step()
    cur_next, stop_next = runtime.patrol_decision_summaries()
    assert cur_next is not None and cur_next.state == "waiting_for_sensor"
    # The stall discrepancy reason is NOT lost!
    assert stop_next is not None and stop_next.state == "stalled"
    assert "Movement rejected" in stop_next.policy_reason
    memory.close()
