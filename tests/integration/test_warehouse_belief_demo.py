"""Verify the recording checkpoints with the real preset and manual API steps."""

from uuid import uuid4

from src.contracts.simulation_api import (
    SimulationControlRequest,
    SimulationEvidenceRequest,
    SimulationLoadRequest,
    SimulationRunIdentity,
)
from src.providers import ProviderConfig
from src.web.service import AgentService

PRESET = "warehouse_belief_demo_v1"


def test_scripted_warehouse_recording_checkpoints():
    service = AgentService(ProviderConfig(mode="rule-based", api_key=None), background=False)
    session_id = str(uuid4())
    state = service.read_live_simulation(session_id)

    def identity():
        return SimulationRunIdentity(run_id=state.run_id, generation=state.generation)

    def step_to(checkpoint):
        nonlocal state
        while state.sim_time_ms < checkpoint:
            state = service.apply_simulation_control(
                session_id, SimulationControlRequest(action="step", expected=identity())
            )
        assert state.sim_time_ms == checkpoint
        assert state.state == "paused"

    def evidence(**target):
        return service.read_simulation_evidence(
            session_id, SimulationEvidenceRequest(expected=identity(), **target)
        )

    try:
        state = service.load_warehouse(
            session_id,
            SimulationLoadRequest(preset=PRESET, expected=identity(), request_id=uuid4()),
        )
        assert state.audience.snapshot.scenario_id == PRESET
        session = service.get_session(session_id)
        sightings = []
        checkpoints = (
            (0, (240, 240), "verified", "station_A", 0, "verified"),
            (6500, (365, 355), "last_seen", "station_A", 6000, "last_known"),
            (12500, (390, 240), "verified", None, 12500, "earlier_station"),
            (19500, (500, 240), "verified", "station_B", 19500, "verified"),
        )
        for checkpoint, world_position, status, station, captured_at, edge_status in checkpoints:
            step_to(checkpoint)
            parcel = next(
                entity
                for entity in state.audience.snapshot.entities
                if entity.entity_id == "parcel_01"
            )
            assert (parcel.footprint.x_cm, parcel.footprint.y_cm) == world_position
            record = evidence(entity_id="parcel_01")
            assessment = record["location_assessment"]
            sighting = assessment["last_sighting"]
            sightings.append(sighting)
            assert assessment["verification_status"] == status
            assert (sighting["station_id"], sighting["sim_time_ms"]) == (station, captured_at)
            assert assessment["current_location"] == (station if status == "verified" else None)
            if status == "last_seen":
                assert assessment["current_sighting"] is None
                assert assessment["lost_verification_sample"]["sim_time_ms"] == checkpoint
                assert sighting["position"] == {"x_cm": 240, "y_cm": 240}
            else:
                assert assessment["current_sighting"] == sighting
                x, y = world_position
                assert sighting["position"] == {"x_cm": x, "y_cm": y}
            edge = next(
                edge
                for edge in session.snapshot()["graph"]["edges"]
                if edge["source"] == "parcel_01" and edge["predicate"] == "located_at"
            )
            assert edge["target"] == (station or "station_A")
            assert edge["location_evidence"] == {"status": edge_status, "assessment": assessment}
            assert len(record["events"]) == (1 if station == "station_B" else 0)

        step_to(24000)
        route = evidence(entity_id="route_A")["summary"]
        assert route["status"] == "clear"
        assert route["latest_lidar_attempt"]["result"]["measurements"]["status"] == "clear"
        assert state.patrol.state == "moving"
        assert state.patrol.latest_stop_decision is None

        step_to(28500)
        assert state.patrol.state == "blocked"
        assert evidence(entity_id="route_A")["summary"]["status"] == "blocked"
        stop = state.patrol.latest_stop_decision
        stop_sample = evidence(sample_id=stop.sensor_sample_id)["sample"]
        assert stop.sim_time_ms == stop_sample["sim_time_ms"] == 28000
        assert stop_sample["request"]["capability"] == "lidar_scan"
        assert stop_sample["result"]["measurements"]["nearest_distance_cm"] == 50
        stopped_x = state.audience.snapshot.robot_pose.x_cm

        step_to(33000)
        assert state.patrol.state == "moving"
        assert state.audience.snapshot.robot_pose.x_cm > stopped_x
        assert state.patrol.latest_stop_decision == stop
        clearance = evidence(sample_id=state.patrol.current_decision.sensor_sample_id)["sample"]
        assert clearance["sim_time_ms"] == 32500
        assert clearance["result"]["measurements"]["status"] == "clear"
        assert evidence(entity_id="route_A")["summary"]["status"] == "clear"
        current = evidence(entity_id="parcel_01")
        assessment = current["location_assessment"]
        sighting = assessment["current_sighting"]
        assert (sighting["station_id"], sighting["sim_time_ms"]) == ("station_B", 33000)
        assert all(sample["run_id"] == str(state.run_id) for sample in current["samples"])

        questions = (
            ("Where is parcel_01 now?", sighting["sample_id"]),
            ("Where was parcel_01 at 6.5 seconds?", sightings[1]["sample_id"]),
            (
                "What evidence caused parcel_01's location belief to change?",
                sightings[3]["sample_id"],
            ),
            ("Why did the robot stop?", stop_sample["sample_id"]),
        )
        for prompt, sample_id in questions:
            answer = session.ask(prompt, expected=identity())
            assert answer["status"] == "verified"
            assert answer["simulation_evidence"]["sim_time_ms"] == 33000
            assert [sample["sample_id"] for sample in answer["sensor_telemetry"]] == [sample_id]
            assert sample_id in answer["answer"]
        assert evidence(entity_id="parcel_01") == current
    finally:
        service.shutdown()
